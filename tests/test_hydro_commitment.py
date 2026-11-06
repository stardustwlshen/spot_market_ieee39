from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import hydro_commitment as hc, rolling, scuc
from test_hydro_rolling import hydro_case


def case(T=6, *, count=1, online=0, age=1., ut=.6, dt=.6):
    c=hydro_case(T=T)
    h=c.cfg.units['hydro'][0]
    h.update(n_units=count,init_units_on=online,init_p_unit=0.,ut=ut,dt=dt)
    h['commitment']=hc.research_initial(h,elapsed_h=age)
    c.bids['units']['H1']['segments'][0]['p_end']=100.*count
    c.cfg.base['solver'].update(time_limit_s=10,mip_rel_gap=1e-8)
    return c


def solved(c, sequence=None):
    m=scuc.build_model(c)
    if sequence is not None:
        for k,u in enumerate(sequence):m.hydro_commitment.u['H1','H1_U01',k].fix(u)
    scuc.solve_scuc(m)
    scuc.fix_integers(m)
    scuc.solve_sced(m)
    m._hydro_quantity_source='energy_sced'
    return m


def test_default_legacy_remains_explicit():
    c=hydro_case(T=2)
    assert hc.initial_state(c.cfg)=={}
    assert not hasattr(scuc.build_model(c),'hydro_commitment')


@pytest.mark.parametrize('bad',[None,True,-1,float('inf'),float('nan'),'1'])
def test_invalid_duration_all_entrypoints(bad):
    c=case();c.cfg.units['hydro'][0]['ut']=bad
    with pytest.raises(ValueError):scuc.build_model(c)


@pytest.mark.parametrize('mutation',['missing','duplicate','count','unknown_age','boolean_age'])
def test_schema_fail_closed(mutation):
    c=case(count=2)
    rows=c.cfg.units['hydro'][0]['commitment']['initial_units']
    if mutation=='missing':rows.pop()
    elif mutation=='duplicate':rows[1]['unit_id']=rows[0]['unit_id']
    elif mutation=='count':rows[0]['online']=True
    elif mutation=='unknown_age':rows[0]['elapsed_kind']='unknown'
    else:rows[0]['elapsed_h']=True
    with pytest.raises(ValueError):scuc.build_model(c)


def test_new_start_nonmultiple_requires_three_periods():
    solved(case(T=4),[1,1,1,0])
    with pytest.raises(scuc.InfeasibleModelError):solved(case(T=4),[1,1,0,0])


def test_initial_partial_nonmultiple_requires_two_more_periods():
    solved(case(T=3,online=1,age=.2),[1,1,0])
    with pytest.raises(scuc.InfeasibleModelError):solved(case(T=3,online=1,age=.2),[1,0,0])


def test_exact_decimal_boundary_not_epsilon_shortened():
    c=case(T=5,ut=1.000000001)
    with pytest.raises(scuc.InfeasibleModelError):solved(c,[1,1,1,1,0])
    solved(case(T=5,ut=1.),[1,1,1,1,0])


def test_zero_durations_still_forbid_same_unit_start_stop():
    m=scuc.build_model(case(T=1,ut=0,dt=0))
    b=m.hydro_commitment
    b.start['H1','H1_U01',0].fix(1);b.stop['H1','H1_U01',0].fix(1)
    with pytest.raises(scuc.InfeasibleModelError):scuc.solve_scuc(m)


def test_same_count_does_not_erase_identity_or_age():
    c=case(T=1,count=2,online=1,age=.1,ut=1,dt=1)
    rows=c.cfg.units['hydro'][0]['commitment']['initial_units'];rows[1]['elapsed_h']=2.
    m=scuc.build_model(c)
    m.hydro_commitment.u['H1','H1_U01',0].fix(0)
    m.hydro_commitment.u['H1','H1_U02',0].fix(1)
    with pytest.raises(scuc.InfeasibleModelError):scuc.solve_scuc(m)
    rows[0]['elapsed_h']=2.
    m=scuc.build_model(c)
    m.hydro_commitment.u['H1','H1_U01',0].fix(0)
    m.hydro_commitment.u['H1','H1_U02',0].fix(1)
    scuc.solve_scuc(m)


def test_pure_suffix_is_eliminated_and_late_start_is_pending():
    c=case(T=96,ut=1,dt=1);c.T=1;c.t0=95;c.stage='RT';c.terminal={'hydro_vend':False}
    c.load_bus={b:a[:1] for b,a in c.load_bus.items()};c.R_up=c.R_up[:1];c.R_dn=c.R_dn[:1]
    state=rolling.initial_state(c.cfg);state.t=95
    state.hydro_commitment['state_at']=hc.stamp(hc.window_time(c.cfg,95))
    c.init={k:deepcopy(v) for k,v in vars(state).items() if k!='t'}
    m=solved(c,[1])
    assert len(m.hydro_commitment.K)==1
    sn=rolling.snapshot(m)
    nxt=rolling.advance(state,sn,c.cfg)
    assert hc.pending(nxt.hydro_commitment)[0]['remaining_h']==.75
    assert nxt.hydro_commitment['state_at'].endswith('T00:00:00Z')
    c2=deepcopy(c);c2.cfg.base['date']='2026-07-16';c2.t0=0;c2.T=1
    c2.init={k:deepcopy(v) for k,v in vars(nxt).items() if k!='t'}
    c2.init['hydro_used']={'H1':0.}
    with pytest.raises(scuc.InfeasibleModelError):solved(c2,[0])
    solved(c2,[1])


def test_first_period_only_updates_age_and_forbids_aux_execution():
    c=case(T=4,online=1,age=2);c.stage='RT';c.terminal={'hydro_vend':False}
    state=rolling.initial_state(c.cfg);m=solved(c,[1,1,0,0]);sn=rolling.snapshot(m)
    nxt=rolling.advance(state,sn,c.cfg)
    assert nxt.hydro_commitment['stations']['H1']['units']['H1_U01']['elapsed_h']==2.25
    sn['hydro_commitment']['quantity_source']='auxiliary_pricing'
    with pytest.raises(ValueError,match='正式energy_sced'):rolling.advance(state,sn,c.cfg)


def test_forecast_initial_allowed_for_model_not_execution():
    c=case(T=1);state=rolling.initial_state(c.cfg)
    block=state.hydro_commitment;block['source']='forecast';block['issued_at']='2026-07-14T12:00:00Z'
    c.init={k:deepcopy(v) for k,v in vars(state).items() if k!='t'};c.stage='RT';c.terminal={'hydro_vend':False}
    sn=rolling.snapshot(solved(c,[0]))
    with pytest.raises(ValueError,match='预测'):rolling.advance(state,sn,c.cfg)


def test_existing_obligation_survives_parameter_reduction():
    c=case(online=1,age=.5,ut=1)
    state=hc.initial_state(c.cfg);old=state['stations']['H1']['units']['H1_U01']['required_until']
    c.cfg.units['hydro'][0]['ut']=.25
    assert hc.normalize_state(c.cfg,state)['stations']['H1']['units']['H1_U01']['required_until']==old
    c.cfg.units['hydro'][0]['ut']=2
    assert hc.seconds(hc.normalize_state(c.cfg,state)['stations']['H1']['units']['H1_U01']['required_until'])-hc.seconds(state['state_at'])==Decimal('5400')


def test_wrong_state_time_and_missing_state_rejected():
    c=case();state=rolling.initial_state(c.cfg)
    c.init={k:deepcopy(v) for k,v in vars(state).items() if k!='t'}
    c.init['hydro_commitment']['state_at']='2026-07-16T00:00:00Z'
    with pytest.raises(ValueError,match='时刻'):scuc.build_model(c)
    c.init.pop('hydro_commitment')
    with pytest.raises(ValueError,match='缺少完整'):scuc.build_model(c)


def test_fixed_integer_registry_covers_new_block_and_rejects_fraction():
    m=solved(case(T=2))
    names={name for name,_ in scuc.integer_components(m)}
    assert {'hydro_commitment.u','hydro_commitment.start','hydro_commitment.stop'}<=names
    assert all(v.fixed and not v.is_integer() for name,c in scuc.integer_components(m) for v in c.values())
    v=m.hydro_commitment.start['H1','H1_U01',0];v.set_value(.2)
    with pytest.raises(ValueError,match='non-integral'):scuc.fix_integers(m)


def test_mixed_zone_initial_validation_is_preserved():
    c=case(T=1,count=2,online=2,age=2)
    h=c.cfg.units['hydro'][0];h.update(zones=[[10,20],[80,100]],init_p_unit=10.)
    state=rolling.initial_state(c.cfg);state.hydro_p['H1']=100.
    c.init={k:deepcopy(v) for k,v in vars(state).items() if k!='t'}
    scuc.build_model(c)
    c.init['hydro_p']['H1']=60.
    with pytest.raises(ValueError,match='稳定区'):scuc.build_model(c)


def test_existing_physical_suffix_shares_identified_commitment():
    c=case(T=6,ut=1);c.T=1;c.stage='RT';c.load_bus={b:a[:1] for b,a in c.load_bus.items()};c.R_up=c.R_up[:1];c.R_dn=c.R_dn[:1]
    c.cfg.units['hydro'][0]['e_day_min']=1.
    c.terminal=dict(hydro_vend=False,hydro_daily_energy_reference=dict(start_period=1,end_period_exclusive=6,
        delta={('H1',0):np.array([1,0,0,0,0,0])},power={},hydro_mode='value',reference_metadata={}))
    m=scuc.build_model(c)
    assert len(m.hydro_commitment.K)==6
    assert m.hydro_commitment._representation=='physical_suffix_shared_commitment'
    m.delta['H1',0,0].fix(1)
    with pytest.raises(scuc.InfeasibleModelError):scuc.solve_scuc(m)
    c.terminal['hydro_daily_energy_reference']['delta'][('H1',0)]=np.array([1,1,1,1,0,0])
    solved(c,[1])


def test_aux_clone_keeps_all_commitments_and_never_mutates_dispatch():
    from smm.auxiliary_pricing import run_auxiliary_pricing
    c=case(T=2,online=1,age=0.,ut=1);c.stage='RT';c.terminal={'hydro_vend':False}
    m=solved(c,[1,1]);before=deepcopy(rolling.snapshot(m))
    for policy in ('remove','preserve'):
        c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']=policy
        aux=run_auxiliary_pricing(m)['model']
        assert aux.hydro_commitment.initial_residual.active
        assert all(v.fixed for name,c in scuc.integer_components(aux) for v in c.values())
        assert rolling.snapshot(aux)['hydro_commitment']['quantity_source']=='auxiliary_pricing'
        assert rolling.snapshot(m)['hydro_commitment']==before['hydro_commitment']


def test_enabled_editor_help_and_new_unit_template_are_effective():
    from smm.unit_editor import unit_template
    from unit_parameter_help import field_help, field_readonly
    h=unit_template('hydro','NEW',2,100.,n_units=2)
    assert hc.enabled(h) and len(h['commitment']['initial_units'])==2
    assert not field_readonly('hydro',('ut',),h)
    assert '跨日' in field_help('hydro',('dt',),h)
    old=deepcopy(h);old.pop('commitment')
    assert field_readonly('hydro',('ut',),old)


@pytest.mark.parametrize('index', [-1, .5, True, 2])
def test_execution_rejects_outside_day_or_noninteger_index(index):
    c=case(T=2)
    with pytest.raises(ValueError,match='首段索引'):
        hc.advance(c.cfg,hc.initial_state(c.cfg),{},index)


def test_cannot_shorten_inherited_deadline_by_corrupting_checkpoint():
    c=case(T=2,online=1,age=.2,ut=1)
    state=hc.initial_state(c.cfg)
    c.cfg.units['hydro'][0]['ut']=0.
    state['stations']['H1']['units']['H1_U01']['required_until']=state['state_at']
    with pytest.raises(ValueError,match='禁止静默清空'):
        hc.normalize_state(c.cfg,state)


def test_execution_audit_accepts_only_official_contiguous_prefix():
    c=case(T=2,online=1,age=2.,ut=1);c.stage='RT';c.terminal={'hydro_vend':False}
    initial=hc.initial_state(c.cfg);sn=rolling.snapshot(solved(c,[1,1]))
    result=hc.audit_execution(c.cfg,initial,[{'t':0,'snap':sn}])
    assert result['executed_periods']==1
    assert result['terminal_state']['stations']['H1']['units']['H1_U01']['elapsed_h']==2.25
    assert result['future_fulfillment_verified'] is False
    with pytest.raises(ValueError,match='连续前缀'):hc.audit_execution(c.cfg,initial,[{'t':1,'snap':sn}])
    with pytest.raises(ValueError,match='不超过'):hc.audit_execution(c.cfg,initial,[{}]*3)
    with pytest.raises(ValueError,match='须为对象'):hc.audit_execution(c.cfg,initial,[None])
