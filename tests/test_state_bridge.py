from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from smm import rolling,scuc
from smm import state_bridge as b
from smm.series import validate_day,validate_day_bids,AwaitingBids,bid_hash
from multiday_fixture import micro_day


def test_full_precision_roundtrip_and_explicit_case_entry():
    day,s=micro_day(); packed=b.pack_state(day.cfg,s);r=b.unpack_state(day.cfg,packed)
    assert b.digest(packed)==b.digest(b.pack_state(day.cfg,r))
    assert r.storage['ES1']==12.3456789012345
    from smm.case import build_da_case
    c=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=b.state_to_case_init(s),require_complete_bids=True)
    m=scuc.build_model(c)
    assert m._md.storage['ES1']['soc0']*20==s.storage['ES1']
    assert m._md.pumped['PS1']['init']['V']==s.pumped['PS1']['V']
    assert m._md.pumped['PS1']['v_end'] is None
    assert m._md.hydro['H1']['v0']==s.hydro['H1']


@pytest.mark.parametrize('mutation',['missing_thermal','missing_hydro_p','wrong_t','missing_history','future_actual','inventory','clock'])
def test_invalid_state_fails_closed(mutation):
    day,s=micro_day()
    if mutation=='missing_thermal':s.thermal.pop('C1')
    elif mutation=='missing_hydro_p':s.hydro_p.pop('H1')
    elif mutation=='wrong_t':s.t=1
    elif mutation=='missing_history':s.hydro_pre_history={}
    elif mutation=='future_actual':s.hydro_hist['H1'][0]=0.
    elif mutation=='inventory':s.storage['ES1']=99.
    elif mutation=='clock':s.continuation['day_start']='2026-07-16T00:00:00+00:00'
    with pytest.raises(ValueError):b.validate_system_state(day.cfg,s,expected_t=0,continuation_required=True)


def test_bid_guards():
    day,s=micro_day();validate_day(day)
    day.bid_receipt['confirmed']=False
    with pytest.raises(AwaitingBids):validate_day_bids(day)
    day.bid_receipt['confirmed']=True;day.bid_data['units']['C1']['segments'][0]['price']+=1
    with pytest.raises(ValueError,match='hash'):validate_day_bids(day)
    day.bid_receipt['bid_hash']=bid_hash(day.bid_data);day.bid_data['date']='2026-07-14'
    with pytest.raises(ValueError,match='date'):validate_day_bids(day)
    day,s=micro_day();day.bid_data['units'].pop('PS1');day.bid_receipt['bid_hash']=bid_hash(day.bid_data)
    with pytest.raises(ValueError):validate_day_bids(day)


def test_actual_precise_initial_duration_audit_not_rounded():
    day,s=micro_day();s.thermal['C1']['hours']=3.999999999
    rt=[dict(t=0,snap={'u':{'C1':np.array([0]),'C2':np.array([0])}})]
    assert len(b.thermal_duration_audit(day.cfg,s,rt))==0  # explicit numerical tolerance 1e-9
    s.thermal['C1']['hours']=3.99
    assert len(b.thermal_duration_audit(day.cfg,s,rt))==1


def test_pre_history_model_reads_executed_water_and_pumped_mode():
    day,s=micro_day()
    s.hydro_pre_history['H1']['values']=[7.,8.,9.]
    s.hydro_pre_history['H1']['sources']=['executed']*3
    s.pumped_pre_history['PS1']['values']={'xg':[1,0,0],'xp':[0,0,0]}
    s.pumped_pre_history['PS1']['sources']=['executed']*3
    from smm.case import build_da_case
    c=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=b.state_to_case_init(s),require_complete_bids=True)
    m=scuc.build_model(c)
    assert m._upstream_out('H2',0)==7
    assert m._upstream_out('H2',1)==8
    m.xp['PS1',0].value=1
    import pyomo.environ as pyo
    assert pyo.value(m.c_pgap1['PS1',0,3].body)>1


def test_research_prehistory_requires_acknowledgment():
    day,s=micro_day()
    with pytest.raises(ValueError,match='acknowledgment'):b.research_initial_state(day.cfg)


@pytest.mark.parametrize('gap',[3,30])
def test_asset_suffix_consumes_cross_day_mode_history(gap):
    day,s=micro_day(with_hydro=False);day.cfg.units['pumped'][0]['switch_gap']=gap
    s=b.research_initial_state(day.cfg,acknowledge_constant_prehistory=True)
    s.pumped_pre_history['PS1']['values']['xg'][1]=1  # old actual gen before day start
    s.pumped_pre_history['PS1']['sources']=['executed']*gap
    from smm.case import build_da_case
    c=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=b.state_to_case_init(s),require_complete_bids=True)
    c.T=1;c.stage='RT';c.terminal={'asset_reachability':dict(target_policy='day_end',start_period=1,end_period_exclusive=24,storage_targets={'ES1':[(22,s.storage['ES1'],s.storage['ES1'])]},pumped_targets={'PS1':[(22,s.pumped['PS1']['V'],s.pumped['PS1']['V'])]})}
    c.load_bus={k:v[:1] for k,v in c.load_bus.items()};c.R_up=c.R_up[:1];c.R_dn=c.R_dn[:1]
    m=scuc.build_model(c);block=m.asset_reachability
    import pyomo.environ as pyo
    for var in m.component_data_objects(pyo.Var):var.set_value(0,skip_validation=True)
    block.xp['PS1',1].set_value(1)
    # A prior executed gen at 1-gap is still inside the switch gap of suffix period 1.
    assert any(con.upper is not None and abs(pyo.value(con.upper)-1)<1e-10 and pyo.value(con.body)>1.5 for con in block.constraints.values())
    c.init['pumped_pre_history']['PS1']['values']['xg']=[]
    with pytest.raises(ValueError,match='history'):scuc.build_model(c)


def test_archived_profit_replay_uses_actual_counts_not_cfg_defaults(tmp_path):
    import json
    from smm.profit_recalculation import archived_actual_initial
    specs={'C1':'coal','PS1':'pumped'}
    assert archived_actual_initial(tmp_path,specs) is None
    (tmp_path/'admission.json').write_text('{}')
    with pytest.raises(ValueError,match='missing'):archived_actual_initial(tmp_path,specs)
    initial={'t':0,'thermal':{'C1':{'online':True}},'pumped':{'PS1':{'ng':1,'np':0}}}
    (tmp_path/'rolling_actual_states.json').write_text(json.dumps({'windows':[initial]}))
    assert archived_actual_initial(tmp_path,specs)==initial
    initial['pumped']['PS1'].pop('np')
    (tmp_path/'rolling_actual_states.json').write_text(json.dumps({'windows':[initial]}))
    with pytest.raises(ValueError,match='count'):archived_actual_initial(tmp_path,specs)


def test_run_of_river_effective_day_target_not_config_or_each_window():
    day,s=micro_day();h=day.cfg.units['hydro'][0];h.update(regulation='run_of_river',pondage=10.)
    h['seasons']['wet']['v0']=110.;s.hydro['H1']=107.;s.continuation['day_initial_hydro']['H1']=107.
    b.validate_system_state(day.cfg,s,expected_t=0)
    from smm.case import build_da_case
    c=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=b.state_to_case_init(s),require_complete_bids=True)
    assert scuc.build_model(c)._md.hydro['H1']['v_end']==107.
    # A later window's current stock differs, but its day-end goal remains 107.
    c.init['hydro']['H1']=105.;c.stage='RT';c.t0=1
    from smm import hydro_commitment as hc
    c.init['hydro_commitment']['state_at']=hc.stamp(hc.window_time(day.cfg,1))
    for st in c.init['hydro_commitment']['stations'].values():
        for u in st['units'].values():u['elapsed_h']+=1
    c.init['hydro_hist']={h:np.zeros(24) for h in s.hydro}
    assert scuc.build_model(c)._md.hydro['H1']['v_end']==107.


def test_physical_identity_keeps_unit_ids_and_solver_rules_but_not_daily_budgets():
    day,s=micro_day();old=b.physical_identity(day.cfg,day.net)
    changed=deepcopy(day.cfg);changed.units['pumped'][0]['max_gen_starts']+=1
    changed.units['hydro'][0]['seasons']['wet']['e_day']+=1
    assert b.physical_identity(changed,day.net)==old
    for field in ('commit_step_periods','storage_binary'):
        changed=deepcopy(day.cfg);changed.base['solver'][field]=99
        assert b.physical_identity(changed,day.net)!=old
    changed=deepcopy(day.cfg);changed.units['hydro'][0]['commitment']['initial_units'][0]['unit_id']='OTHER'
    assert b.physical_identity(changed,day.net)!=old


def test_completed_output_hash_excludes_only_derived_cost_supplement(tmp_path):
    from smm.series import _files
    (tmp_path/'original.json').write_text('{}');before=_files(tmp_path)
    (tmp_path/'cost_recalculations').mkdir();(tmp_path/'cost_recalculations'/'supplement.json').write_text('{}')
    assert _files(tmp_path)==before
    (tmp_path/'unexpected.json').write_text('{}');assert _files(tmp_path)!=before


def test_frozen_network_csv_preserves_effective_ratings_and_load(tmp_path):
    from smm.series import _freeze,day_fingerprint
    from dataclasses import replace
    from smm.network import build_network
    day,s=micro_day();day.cfg.base.update(line_scale={'L':.7},line_capacity_multiplier=1.2)
    frozen=_freeze(day,tmp_path)
    rebuilt=build_network(frozen)
    np.testing.assert_allclose(rebuilt.branches.rate_mw,day.net.branches.rate_mw,rtol=1e-14,atol=1e-12)
    np.testing.assert_allclose(rebuilt.load_share,day.net.load_share)
    assert day_fingerprint(replace(day,cfg=frozen))==day_fingerprint(day)


def test_marked_continuous_case_cannot_fall_back_to_cfg_initial_state():
    from smm.case import build_da_case
    from types import SimpleNamespace
    from smm.full import hydro_terminal_lookahead_checks
    day,s=micro_day()
    with pytest.raises(ValueError,match='actual initial'):
        build_da_case(day.cfg,day.net,day.profiles,day.bid_data,require_complete_bids=True,require_actual_state=True)
    case=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=b.state_to_case_init(s),require_complete_bids=True,require_actual_state=True)
    case.init=None
    with pytest.raises(ValueError,match='actual initial'):
        rolling.run_rolling(SimpleNamespace(case=case))
    with pytest.raises(ValueError,match='actual initial'):
        hydro_terminal_lookahead_checks(day.cfg,SimpleNamespace(actual_state_required=True),{})


def test_frozen_effective_bids_match_day_model_and_new_cost_file(tmp_path):
    from smm.series import _freeze,_cost_files
    from smm.bids import effective_bids
    import json
    day,s=micro_day();frozen=_freeze(day,tmp_path)
    assert json.loads((frozen.root/'resolved_bids.json').read_text())==effective_bids(day.cfg,day.bid_data)
    assert 'spot_market_costs.yaml' in _cost_files(day.cfg)
