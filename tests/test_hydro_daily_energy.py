"""Hard daily lower bounds follow execution, never overlapping forecasts."""
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import rolling, scuc
from smm.da import run_da
from smm.hydro_daily_energy import audit as raw_audit, audit_exported as raw_audit_exported, diagnostics, reference_terms, id_state_values
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_hydro_rolling import hydro_case
from test_asset_reachability import raw_status, solve


def audit(cfg, da, rr):
    return raw_audit(cfg, da, rr, bids=getattr(cfg, '_audit_test_bids', None))


def audit_exported(cfg, evidence, states):
    return raw_audit_exported(cfg, evidence, states, bids=getattr(cfg, '_audit_test_bids', None))


def case(T=4, minimum=40.):
    c=hydro_case(T=T)
    c.cfg.units['hydro'][0].update(e_day_min=minimum,ramp=100.)
    c.bids['units']['H1']['segments'][0]['price']=700.
    c.bids['units']['LOCAL']['segments'][0]['price']=100.
    c.cfg._audit_test_bids=c.bids
    return c


@pytest.mark.parametrize('stage',['DA','ID','RT'])
def test_all_day_horizons_enforce_same_hard_minimum(stage):
    c=case();c.stage=stage
    c.terminal=None if stage=='DA' else {'hydro_vend':True}
    m=solve(scuc.build_model(c))
    assert list(m.c_edaymin)==['H1']
    assert sum(pyo.value(m.Ph['H1',t])*.25 for t in m.T)==pytest.approx(40.,abs=2e-6)
    assert diagnostics(m)['rows'][0]['remaining_minimum_mwh']==40.


@pytest.mark.parametrize('used,remaining',[(15.,25.),(50.,0.)])
def test_id_and_day_end_rt_subtract_only_executed_energy(used,remaining):
    c=case();state=rolling.initial_state(c.cfg);state.t=1;state.hydro_used['H1']=used
    for stage in ('ID','RT'):
        w=rolling.make_window_case(c,c.cfg,1,4,1,False,state,{}, {'hydro_vend':True},stage)
        m=solve(scuc.build_model(w));r=diagnostics(m)['rows'][0]
        assert r['executed_before_mwh']==used and r['remaining_minimum_mwh']==remaining
        assert r['window_generation_mwh']>=remaining-2e-6
        assert r['remaining_upper_mwh']==500.-used


def suffix_case(minimum=40.):
    c=case(minimum=minimum);c.T=1;c.stage='RT'
    c.cfg.units['hydro'][0].update(init_units_on=0,init_p_unit=0,qout_max=1000.)
    c.cfg.units['hydro'][0]['seasons']['wet'].update(qin=0.,v0=24.4,qeco=0.)
    c.load_bus={k:a[:1] for k,a in c.load_bus.items()};c.R_up=c.R_up[:1];c.R_dn=c.R_dn[:1]
    c.terminal=dict(fix_power={'Ph':{('H1',0):0.}},hydro_daily_energy_reference=dict(
        start_period=1,end_period_exclusive=4,hydro_mode='sced',delta={},power={},reference_metadata={}))
    return c


def test_physical_suffix_prevents_water_dumping_dead_end_without_future_actual_data():
    c=suffix_case();m=solve(scuc.build_model(c));d=diagnostics(m)
    assert pyo.value(m.Ph['H1',0])==0.
    assert pyo.value(m.V['H1',0])>=24.4-2e-6
    assert d['rows'][0]['witness_generation_mwh']==pytest.approx(40.,abs=2e-6)
    assert d['suffix']['integers_fixed'] and not d['suffix']['quantities_execute']
    from pyomo.core.expr.visitor import identify_variables
    assert not any(v.name.startswith('hydro_daily_energy_suffix.') for v in identify_variables(m.obj.expr))
    impossible=scuc.build_model(c);impossible.V['H1',0].fix(10.)
    assert raw_status(impossible)==('infeasible',False)
    c.cfg.units['hydro'][0]['e_day_min']=None
    old=scuc.build_model(c);old.V['H1',0].fix(10.)
    assert raw_status(old)==('optimal',True)


@pytest.mark.parametrize('mode',['hard','soft'])
def test_physically_impossible_lower_bound_cannot_become_soft(mode):
    c=suffix_case(80.)
    c.terminal['state_band_terminal_policy']={'mode':mode,'penalty_yuan_per_discharge_mwh':1000.}
    c.terminal['state_band_soft']=True
    assert raw_status(scuc.build_model(c))==('infeasible',False)


def test_missing_actual_cumulative_energy_and_suffix_reference_fail_closed():
    c=case();s=rolling.initial_state(c.cfg);s.t=1
    w=rolling.make_window_case(c,c.cfg,1,4,1,False,s,{}, {},'ID')
    del w.init['hydro_used']
    with pytest.raises(ValueError,match='实际已执行累计'):scuc.build_model(w)
    c=suffix_case();del c.terminal['hydro_daily_energy_reference']
    with pytest.raises(ValueError,match='资产续程参考'):scuc.build_model(c)


@pytest.mark.parametrize('policy',['preserve','remove'])
def test_auxiliary_tracking_policy_never_removes_daily_physics(policy):
    c=suffix_case();c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']=policy
    m=solve(scuc.build_model(c));aux=run_auxiliary_pricing(m)['model']
    assert aux.hydro_daily_energy_suffix.active and aux.c_edaymin['H1'].active
    assert all(v.fixed for v in aux.hydro_daily_energy_suffix.delta.values())
    assert diagnostics(aux)['rows'][0]['minimum_margin_mwh']>=-2e-6
    assert pyo.value(m.Ph['H1',0])==0.


def test_future_fixed_reference_and_power_are_not_freed():
    c=suffix_case()
    spec=c.terminal['hydro_daily_energy_reference'];spec.update(hydro_mode='value',delta={('H1',0):np.zeros(4)})
    assert raw_status(scuc.build_model(c))==('infeasible',False)
    spec['delta'][('H1',0)][:]=1
    m=solve(scuc.build_model(c))
    assert all(pyo.value(m.hydro_daily_energy_suffix.delta['H1',0,t])==1 for t in m.hydro_daily_energy_suffix.F)
    spec.update(hydro_mode='fixed_da',power={'H1':np.zeros(4)})
    assert raw_status(scuc.build_model(c))==('infeasible',False)


def test_suffix_ramp_and_outflow_and_daily_upper_remain_hard():
    for change in ('ramp','qout','upper'):
        c=suffix_case()
        if change=='ramp':c.cfg.units['hydro'][0]['ramp']=.1
        elif change=='qout':
            c.cfg.units['hydro'][0]['qout_max']=10.
            c.cfg.units['hydro'][0]['seasons']['dry']['qeco']=0.
        else:c.hydro_e_budget={'H1':30.}
        assert raw_status(scuc.build_model(c))==('infeasible',False)


def test_cascade_suffix_couples_current_release_and_future_lag():
    c=suffix_case(20.)
    h=deepcopy(c.cfg.units['hydro'][0]);h.update(id='H2',upstream='H1',lag_periods=1,e_day_min=None,
        water_rate=1.,init_units_on=0,init_p_unit=0)
    h['seasons']['wet'].update(v0=10.,vmin=10.,vmax=100.,v_end=10.)
    c.cfg.units['hydro'].append(h);c.bids['units']['H2']=deepcopy(c.bids['units']['H1'])
    m=solve(scuc.build_model(c));b=m.hydro_daily_energy_suffix
    for t in b.F:
        prior=pyo.value(m.V['H2',0] if t==1 else b.V['H2',t-1])
        source=m if t-1==0 else b
        arriving=pyo.value(source.Ph['H1',t-1]+source.Qspill['H1',t-1])
        own=pyo.value(b.Ph['H2',t]+b.Qspill['H2',t])
        assert pyo.value(b.V['H2',t])==pytest.approx(prior+(arriving-own)*.09)


@pytest.fixture(scope='module')
def completed():
    c=case(T=12,minimum=80.)
    c.cfg.mode['intraday']['enabled']=True
    c.cfg.mode['realtime']['hydro']='value';c.cfg.mode['realtime']['storage']['mode']='value'
    da=run_da(c,verbose=False);rr=rolling.run_rolling(da,log=lambda *a:None)
    return c.cfg,rolling.snapshot(da.model),rr


def test_complete_execution_and_id_energy_accounting(completed):
    cfg,da,rr=completed;result=audit(cfg,da,rr)
    assert result['hydro_daily_minimum_evidence_valid'] and result['hydro_daily_minimum_final_satisfied']
    actual=sum(r['snap']['Ph']['H1'][0]*cfg.dt for r in rr.rt)
    assert actual==pytest.approx(rr.states[-1].hydro_used['H1'])
    assert actual>=80.-2e-6
    assert rr.id_rounds
    for r in rr.id_rounds:
        row=r['snap']['hydro_daily_energy']['rows'][0]
        assert row['executed_before_mwh']==pytest.approx(rr.states[r['tau']].hydro_used['H1'])


@pytest.mark.parametrize('damage',['missing','duplicate_period','missing_subject','nan','used','final_used','witness_power',
 'witness_volume','witness_counts','witness_rows','witness_scope','reported_minimum','actual_shortfall'])
def test_audit_does_not_accept_missing_or_tampered_evidence(completed,damage):
    cfg,da,rr=deepcopy(completed)
    sn=rr.rt[0]['snap'];d=sn['hydro_daily_energy']
    if damage=='missing':sn.pop('hydro_daily_energy')
    elif damage=='duplicate_period':rr.rt[1]['t']=0
    elif damage=='missing_subject':sn['Ph'].pop('H1')
    elif damage=='nan':sn['Ph']['H1'][0]=np.nan
    elif damage=='used':rr.states[1].hydro_used['H1']+=1
    elif damage=='final_used':rr.states[-1].hydro_used['H1']+=1
    elif damage=='witness_power':d['suffix']['rows'][0]['power_mw']+=1
    elif damage=='witness_volume':d['suffix']['rows'][0]['volume_1e4m3']+=1
    elif damage=='witness_counts':d['suffix']['rows'][0]['zone_counts'][0]=.5
    elif damage=='witness_rows':d['suffix']['rows'].pop()
    elif damage=='witness_scope':d['suffix']['quantities_execute']=True
    elif damage=='reported_minimum':d['rows'][0]['remaining_minimum_mwh']=0
    else:
        for r in rr.rt:r['snap']['Ph']['H1'][0]=0.
    r=audit(cfg,da,rr)
    assert not r['hydro_daily_minimum_evidence_valid'] and r['hydro_daily_minimum_final_satisfied'] is not True


def test_prefix_is_not_a_completed_day_or_a_false_actual_failure(completed):
    cfg,da,rr=deepcopy(completed);rr.rt=rr.rt[:2];rr.states=rr.states[:3]
    rr.id_rounds=[r for r in rr.id_rounds if r['tau']<2]
    r=audit(cfg,da,rr)
    assert r['hydro_daily_minimum_evidence_valid'] and r['hydro_daily_minimum_final_satisfied'] is None
    assert not r['hydro_daily_minimum_full_day']


def test_null_lower_bound_keeps_original_model_and_does_not_require_new_evidence():
    c=case(minimum=None);m=solve(scuc.build_model(c))
    assert not list(m.c_edaymin) and not hasattr(m,'hydro_daily_energy_suffix')
    assert not diagnostics(m)['active']
    assert not audit(c.cfg,{},NS(rt=[],states=[]))['hydro_daily_minimum_active']


def test_final_validation_failure_prevents_successful_settlement(completed,monkeypatch):
    from smm import full
    cfg,sn,rr=deepcopy(completed)
    rr.rt[-1]['snap']['hydro_daily_energy']['rows'][0]['remaining_minimum_mwh']=999.
    da=NS(model=object(),case=NS(bids=cfg._audit_test_bids))
    monkeypatch.setattr(full,'run_da',lambda *a,**k:da)
    monkeypatch.setattr(rolling,'run_rolling',lambda *a,**k:rr)
    monkeypatch.setattr(rolling,'snapshot',lambda model:sn)
    def forbidden(*args,**kwargs):
        raise AssertionError('failure must be raised before settlement')
    monkeypatch.setattr(full.settlement,'settle',forbidden)
    with pytest.raises(RuntimeError,match='不生成成功结算') as caught:
        full.run_full(cfg,object(),log=lambda *a:None)
    assert not caught.value.daily_energy_evidence['hydro_daily_minimum_evidence_valid']


def test_model_infeasibility_is_typed_not_a_fabricated_daily_success():
    m=scuc.build_model(suffix_case(80.))
    with pytest.raises(scuc.InfeasibleModelError) as caught:scuc.solve_scuc(m)
    assert caught.value.phase=='scuc'


def test_zero_minimum_has_no_suffix_or_hidden_future_restrictions():
    c=suffix_case(0.);c.terminal.pop('hydro_daily_energy_reference')
    m=scuc.build_model(c);m.V['H1',0].fix(10.);solve(m)
    assert not hasattr(m,'hydro_daily_energy_suffix')
    assert diagnostics(m)['rows'][0]['daily_minimum_mwh']==0.
    original=pyo.value(m.obj)
    c.cfg.units['hydro'][0]['e_day_min']=None
    old=scuc.build_model(c);old.V['H1',0].fix(10.);solve(old)
    assert pyo.value(old.obj)==pytest.approx(original)


def test_fulfilled_minimum_does_not_require_unneeded_future_certificate():
    c=case();s=rolling.initial_state(c.cfg);s.t=1;s.hydro_used['H1']=50.
    w=rolling.make_window_case(c,c.cfg,1,2,1,False,s,{}, {},'RT')
    m=solve(scuc.build_model(w))
    assert not hasattr(m,'hydro_daily_energy_suffix')
    assert diagnostics(m)['rows'][0]['remaining_minimum_mwh']==0.


def exported(completed):
    from smm.full import actual_state_evidence
    cfg,da,rr=deepcopy(completed)
    def convert(sn):
        sn=deepcopy(sn)
        sn['delta']=[dict(station=h,zone=z,values=list(a)) for (h,z),a in sn['delta'].items()]
        return sn
    hydro=dict(dt_h=cfg.dt,season=cfg.base['season'],da=convert(da),
        rt_windows=[dict(t=r['t'],snapshot=convert(r['snap']),reference_metadata=r['reference_metadata'],
                        handoff_config=r['handoff_config']) for r in rr.rt],
        id_rounds=[dict(tau=r['tau'],snapshot=convert(r['snap']),state_values=id_state_values(r)) for r in rr.id_rounds],
        id_failures=rr.id_failures)
    states=dict(windows=[actual_state_evidence(s) for s in rr.states[:-1]],
                final_executed_state=actual_state_evidence(rr.states[-1]))
    return cfg,hydro,states


def test_offline_adapter_recomputes_without_summary(completed):
    cfg,hydro,states=exported(completed)
    result=audit_exported(cfg,hydro,states)
    assert result['hydro_daily_minimum_final_satisfied']
    states['final_executed_state']['hydro_used']['H1']+=1
    assert not audit_exported(cfg,hydro,states)['hydro_daily_minimum_evidence_valid']


@pytest.mark.parametrize('damage',['snapshot','state','delta','period','time','suffix'])
def test_offline_adapter_rejects_incomplete_or_tampered_evidence(completed,damage):
    cfg,h,s=exported(completed)
    if damage=='snapshot':h['rt_windows'][0].pop('snapshot')
    elif damage=='state':s.pop('final_executed_state')
    elif damage=='delta':h['da']['delta'].append(deepcopy(h['da']['delta'][0]))
    elif damage=='period':h['rt_windows'][1]['t']=0
    elif damage=='time':h['dt_h']=1.
    else:h['rt_windows'][0]['snapshot']['hydro_daily_energy']['suffix']['rows'][0]['volume_1e4m3']+=1
    assert not audit_exported(cfg,h,s)['hydro_daily_minimum_evidence_valid']


def test_positive_minimum_audit_requires_independent_frozen_bids(completed):
    cfg,da,rr=completed
    assert not raw_audit(cfg,da,rr)['hydro_daily_minimum_evidence_valid']
    cfg,h,s=exported(completed)
    assert not raw_audit_exported(cfg,h,s)['hydro_daily_minimum_evidence_valid']


@pytest.fixture(scope='module')
def narrowed_offer():
    c=case(T=12,minimum=80.);c.cfg.mode['intraday']['enabled']=True
    c.cfg.mode['realtime']['hydro']='value';c.cfg.mode['realtime']['storage']['mode']='value'
    c.bids['units']['H1']['segments'][0]['p_end']=50.
    c.cfg.units['hydro'][0]['n_units']=2
    da=run_da(c,verbose=False);rr=rolling.run_rolling(da,log=lambda *a:None)
    return c.cfg,rolling.snapshot(da.model),rr


@pytest.mark.parametrize('damage',['nonexistent_round','free_mode','counts','capacity','forged_window_source','stale_after_failure'])
def test_witness_cross_checks_real_references_and_frozen_capacity(narrowed_offer,damage):
    cfg,da,rr=deepcopy(narrowed_offer)
    assert audit(cfg,da,rr)['hydro_daily_minimum_final_satisfied']
    r=rr.rt[0];d=r['snap']['hydro_daily_energy'];s=d['suffix']
    if damage=='nonexistent_round':s['reference_metadata']['H1'].update(trajectory_source='intraday@999',round=999)
    elif damage=='free_mode':s['hydro_mode']='sced'
    elif damage=='counts':
        row=s['rows'][0];row['zone_counts']=[1. if row['zone_counts'][0]==2. else 2.]
    elif damage=='capacity':
        row=next(x for x in s['rows'] if x['power_mw']==50.)
        row['power_mw']=51.
        for future in s['rows']:
            if future['unit']==row['unit'] and future['period']>=row['period']:
                future['volume_1e4m3']-=.36*cfg.dt  # Keep water balance without relying on arbitrary spill.
        d['rows'][0]['witness_generation_mwh']+=cfg.dt;d['rows'][0]['minimum_margin_mwh']+=cfg.dt
    elif damage=='forged_window_source':
        s['reference_metadata']['H1'].update(trajectory_source='day_ahead',round=None)
        r['reference_metadata']['V']['H1']=deepcopy(s['reference_metadata']['H1'])
    else:rr.id_failures.append(dict(tau=0,phase='scuc',solver_status='infeasible'))
    result=audit(cfg,da,rr)
    assert not result['hydro_daily_minimum_evidence_valid'],result
    assert result['hydro_daily_minimum_final_satisfied'] is False
    if damage=='capacity':assert any('frozen offered power capacity' in e for e in result['hydro_daily_minimum_errors'])


def test_offline_witness_reference_and_capacity_cross_checks(narrowed_offer):
    cfg,h,st=exported(narrowed_offer)
    assert audit_exported(cfg,h,st)['hydro_daily_minimum_final_satisfied']
    d=h['rt_windows'][0]['snapshot']['hydro_daily_energy'];s=d['suffix']
    row=next(x for x in s['rows'] if x['power_mw']==50.)
    row['power_mw']=51.
    for future in s['rows']:
        if future['unit']==row['unit'] and future['period']>=row['period']:
            future['volume_1e4m3']-=.36*cfg.dt  # Preserve water with unchanged, possibly zero spill.
    d['rows'][0]['witness_generation_mwh']+=cfg.dt;d['rows'][0]['minimum_margin_mwh']+=cfg.dt
    result=audit_exported(cfg,h,st)
    assert not result['hydro_daily_minimum_evidence_valid']
    assert any('frozen offered power capacity' in e for e in result['hydro_daily_minimum_errors'])
