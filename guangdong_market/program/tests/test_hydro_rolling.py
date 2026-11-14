"""Tiny hydro window invariants; no full-day IEEE39 solves."""
from copy import deepcopy
import os

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from smm import rolling, scuc
from smm.config import load_config
from smm.da import run_da
from test_da_stages import tiny_case


def hydro_case(T=10):
    c=tiny_case(reserve=0)
    cfg=c.cfg;cfg.base['periods']=T
    if os.environ.get('HYDRO_TEST_THREADS'):
        cfg.base['solver']['threads']=int(os.environ['HYDRO_TEST_THREADS'])
    cfg.mode['regulation']['enabled']=False
    cfg.mode['realtime']['hydro']='window_end_da'
    cfg.mode['realtime']['lookahead_h']=2.
    cfg.mode['reserve'].update(include_ctg=False,rho_L=0.,rho_RE=0.,rho_L_dn=0.,rho_RE_dn=0.)
    h=deepcopy(load_config().units['hydro'][0])
    h.update(id='H1',bus=2,n_units=1,unit_pmax=100.,zones=[[0.,100.]],ramp=1.,
             water_rate=1.,init_units_on=1,init_p_unit=50.,qout_max=100.,spill_penalty=50.)
    h['seasons']['wet']=dict(qin=50.,v0=100.,vmin=10.,vmax=200.,v_end=100.,qeco=5.,e_day=500.)
    cfg.units['hydro']=[h]
    c.bids['units']['H1']=dict(mode='price_quantity',segments=[dict(p_start=0.,p_end=100.,price=5.)])
    load=np.full(T,120.)
    c.load_bus={2:load.copy()};c.R_up=np.zeros(T);c.R_dn=np.zeros(T)
    c.profiles=pd.DataFrame({f'load_{stage}':load.copy() for stage in ('actual','da','id','rt')})
    return c


@pytest.fixture(scope='module')
def hydro_da():
    return run_da(hydro_case(),verbose=False)


def assert_water_and_bounds(m):
    for h in m.HS:
        d=m._md.hydro[h]
        for t in m.T:
            assert abs(pyo.value(m.c_water[h,t].body))<1e-6
            assert pyo.value(m.V[h,t])>=d['vmin']-1e-6
            assert pyo.value(m.V[h,t])<=d['vmax']+1e-6
            q=d['water_rate']*pyo.value(m.Ph[h,t])+pyo.value(m.Qspill[h,t])
            assert d['qeco']-1e-6<=q<=d['qout_max']+1e-6


def test_da_ramp_from_declared_initial_power_and_water(hydro_da):
    m=hydro_da.model;h=hydro_da.case.cfg.units['hydro'][0]
    power=hydro_da.tables['hydro'].P_mw.to_numpy()
    np.testing.assert_array_less(np.abs(np.diff(np.r_[50.,power])),h['ramp']*15+1e-6)
    assert_water_and_bounds(m)


def test_window_end_target_da_delta_and_free_hydro_power(hydro_da):
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,0)
    m=r['model'];sn=r['snap']
    assert len(sn['Ph']['H1'])==8
    assert sn['Vh']['H1'][-1]==pytest.approx(plan['Vh']['H1'][7],abs=1e-6,rel=0)
    assert all(not v.fixed for v in m.Ph.values())
    for (uid,z),arr in plan['delta'].items():
        np.testing.assert_array_equal(sn['delta'][(uid,z)],arr[:8])
    assert_water_and_bounds(m)
    np.testing.assert_array_less(np.abs(np.diff(np.r_[50.,sn['Ph']['H1']])),15.+1e-6)


def test_advance_only_first_period_budget_state_and_actual_power(hydro_da):
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,0);sn=r['snap']
    nxt=rolling.advance(state,sn,cfg)
    assert nxt.hydro['H1']==pytest.approx(sn['Vh']['H1'][0])
    assert nxt.hydro_used['H1']==pytest.approx(sn['Ph']['H1'][0]*.25)
    assert nxt.hydro_used['H1']!=pytest.approx(sn['Ph']['H1'].sum()*.25)
    assert nxt.hydro_hist['H1'][0]==pytest.approx(sn['Qout']['H1'][0])
    assert np.isnan(nxt.hydro_hist['H1'][1:]).all()
    r2=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,nxt,1)
    assert abs(r2['snap']['Ph']['H1'][0]-sn['Ph']['H1'][0])<=15.+1e-6
    assert r2['model']._md.hydro['H1']['v0']==pytest.approx(nxt.hydro['H1'])
    assert r2['model']._md.hydro['H1']['e_day']==pytest.approx(500.-nxt.hydro_used['H1'])


def test_last_short_window_matches_actual_day_end_target(hydro_da):
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model)
    state=rolling.state_from_plan(cfg,plan,9)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,9)
    assert len(r['snap']['Ph']['H1'])==1
    assert r['snap']['Vh']['H1'][0]==pytest.approx(plan['Vh']['H1'][9],abs=1e-6,rel=0)
    assert abs(r['snap']['Ph']['H1'][0]-plan['Ph']['H1'][8])<=15.+1e-6


def test_actual_start_power_not_da_predecessor_controls_ramp(hydro_da):
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model)
    state=rolling.state_from_plan(cfg,plan,1)
    state.hydro_p['H1']=max(0.,float(plan['Ph']['H1'][0])-10.)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,1)
    assert r['model']._md.hydro_units['H1']['p0']==state.hydro_p['H1']
    assert abs(r['snap']['Ph']['H1'][0]-state.hydro_p['H1'])<=15.+1e-6
    assert r['snap']['Vh']['H1'][-1]==pytest.approx(plan['Vh']['H1'][8],abs=1e-6,rel=0)


def test_constructed_inflow_deviation_reoptimizes_power_keeps_terminal(hydro_da):
    cfg=deepcopy(hydro_da.case.cfg);cfg.units['hydro'][0]['seasons']['wet']['qin']+=5.
    plan=rolling.snapshot(hydro_da.model);state=rolling.state_from_plan(cfg,plan,9)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,9)
    assert r['snap']['Vh']['H1'][0]==pytest.approx(plan['Vh']['H1'][9],abs=1e-6,rel=0)
    # A binding power ramp may route additional inflow through spill, not force extra MW.
    assert r['snap']['Qout']['H1'][0]==pytest.approx(plan['Qout']['H1'][9]+5.,abs=1e-6,rel=0)
    assert r['snap']['hydro_constraints']['H1']['qin_m3s']==[55.]
    assert_water_and_bounds(r['model'])


def test_unreachable_hard_target_fails_without_returning_official_result(hydro_da):
    cfg=deepcopy(hydro_da.case.cfg);cfg.units['hydro'][0]['seasons']['wet']['qin']=0.
    plan=rolling.snapshot(hydro_da.model);state=rolling.state_from_plan(cfg,plan,9)
    state.hydro['H1']=10.
    with pytest.raises(RuntimeError,match='水电|window|窗口'):
        rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,9)


def test_exhausted_daily_budget_rejected_not_silently_clipped(hydro_da):
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model);state=rolling.state_from_plan(cfg,plan,1)
    state.hydro_used['H1']=501.
    with pytest.raises((RuntimeError,ValueError),match='预算|budget|水电'):
        rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,1)


def test_lagged_inflow_uses_executed_history_then_current_window():
    case=hydro_case(T=6);cfg=case.cfg
    h2=deepcopy(cfg.units['hydro'][0]);h2.update(id='H2',upstream='H1',lag_periods=2,
          regulation='run_of_river',pondage=0.,ramp=100.,init_p_unit=55.)
    h2['seasons']['wet'].update(qin=5.,vmin=100.,vmax=100.,qeco=0.)
    cfg.units['hydro'].append(h2)
    case.bids['units']['H2']=deepcopy(case.bids['units']['H1'])
    state=rolling.initial_state(cfg);state.t=2
    state.hydro_hist['H1'][:2]=[37.,42.]
    window=rolling.make_window_case(case,cfg,2,6,2,True,state,{}, {},'RT')
    m=scuc.build_model(window)
    assert pyo.value(m._upstream_out('H2',0))==37.
    assert pyo.value(m._upstream_out('H2',1))==42.
    m.Ph['H1',0].value=60.;m.Qspill['H1',0].value=4.
    assert pyo.value(m._upstream_out('H2',2))==64.


def test_fixed_integer_sced_has_same_hydro_constraints_not_fixed_continuous_output():
    c=hydro_case(T=2);m=scuc.build_model(c)
    scuc.solve_scuc(m)
    constraints={x.name:str(x.expr) for x in m.component_data_objects(pyo.Constraint,active=True)}
    scuc.fix_integers(m);scuc.solve_sced(m)
    assert constraints=={x.name:str(x.expr) for x in m.component_data_objects(pyo.Constraint,active=True)}
    assert all(x.fixed for x in m.delta.values())
    assert all(not x.fixed for x in m.Ph.values())
    assert_water_and_bounds(m)


def test_full_summary_hydro_checks_detect_bad_window_terminal(hydro_da):
    from smm.full import hydro_window_checks
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,0)
    rr=rolling.RollingResult(rt=[r],states=[state])
    result=hydro_window_checks(hydro_da,rr)
    assert result['hydro_window_checks_available']
    assert result['hydro_window_constraints_satisfied']
    assert result['hydro_window_records'][0]['terminal_target_1e4m3']==pytest.approx(plan['Vh']['H1'][7])
    bad=deepcopy(r);bad['snap']['Vh']['H1'][-1]+=1.
    result=hydro_window_checks(hydro_da,rolling.RollingResult(rt=[bad],states=[state]))
    assert result['hydro_window_terminal_max_err_1e4m3']==pytest.approx(1.)
    assert not result['hydro_window_constraints_satisfied']


def test_available_ramp_headroom_allows_real_power_response_to_inflow():
    # One period removes the previous 10-period fixture's binding end ramp.
    result=run_da(hydro_case(T=1),verbose=False)
    cfg=deepcopy(result.case.cfg);cfg.units['hydro'][0]['seasons']['wet']['qin']+=5.
    plan=rolling.snapshot(result.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(result.case,plan,plan,cfg,state,0)
    assert r['snap']['Ph']['H1'][0]==pytest.approx(plan['Ph']['H1'][0]+5.,abs=1e-6,rel=0)
    assert r['snap']['Vh']['H1'][0]==pytest.approx(plan['Vh']['H1'][0],abs=1e-6,rel=0)
    assert all(not x.fixed for x in r['model'].Ph.values())


@pytest.mark.parametrize('corruption',['missing_target','wrong_target','nan_volume','missing_delta','missing_state'])
def test_hydro_summary_evidence_fail_closed(hydro_da,corruption):
    from smm.full import hydro_window_checks
    cfg=hydro_da.case.cfg;plan=rolling.snapshot(hydro_da.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,0)
    rr=rolling.RollingResult(rt=[r],states=[state])
    if corruption=='missing_target':r['snap']['hydro_constraints']['H1']['v_end_target_1e4m3']=None
    elif corruption=='wrong_target':
        r['snap']['hydro_constraints']['H1']['v_end_target_1e4m3']+=1.
        r['snap']['Vh']['H1'][-1]+=1.
    elif corruption=='nan_volume':r['snap']['Vh']['H1'][0]=np.nan
    elif corruption=='missing_delta':r['snap']['delta']={}
    else:rr.states=[]
    check=hydro_window_checks(hydro_da,rr)
    assert not check['hydro_window_constraints_satisfied']
    if corruption=='wrong_target':assert check['hydro_window_terminal_max_err_1e4m3']==pytest.approx(1.,abs=1e-6,rel=0)


@pytest.mark.parametrize('bad_value',['missing','nan'])
def test_rolling_window_rejects_missing_or_nonfinite_actual_hydro_volume(bad_value):
    case=hydro_case(T=2);cfg=case.cfg;state=rolling.initial_state(cfg)
    if bad_value=='missing':del state.hydro['H1']
    else:state.hydro['H1']=np.nan
    with pytest.raises(ValueError,match='库容|hydro|水电'):
        rolling.make_window_case(case,cfg,0,2,0,True,state,{}, {},'RT')


@pytest.mark.parametrize('bad_value',['missing','nan'])
def test_direct_model_entry_rejects_missing_or_nonfinite_actual_hydro_volume(bad_value):
    case=hydro_case(T=2);cfg=case.cfg;state=rolling.initial_state(cfg)
    window=rolling.make_window_case(case,cfg,0,2,0,True,state,{}, {},'RT')
    if bad_value=='missing':del window.init['hydro']['H1']
    else:window.init['hydro']['H1']=np.nan
    with pytest.raises(ValueError,match='库容|hydro|水电'):
        scuc.build_model(window)


def lagged_case():
    case=hydro_case(T=4);cfg=case.cfg
    h2=deepcopy(cfg.units['hydro'][0]);h2.update(id='H2',upstream='H1',lag_periods=2,
          regulation='run_of_river',pondage=0.,ramp=100.,init_p_unit=55.)
    h2['seasons']['wet'].update(qin=5.,vmin=100.,vmax=100.,qeco=0.)
    cfg.units['hydro'].append(h2);case.bids['units']['H2']=deepcopy(case.bids['units']['H1'])
    return case


@pytest.mark.parametrize('bad_history',['missing','nan','short'])
def test_lagged_already_executed_history_cannot_fall_back_to_initial_flow(bad_history):
    case=lagged_case();cfg=case.cfg;state=rolling.initial_state(cfg);state.t=2
    state.hydro_hist['H1'][:2]=[37.,42.]
    if bad_history=='missing':del state.hydro_hist['H1']
    elif bad_history=='nan':state.hydro_hist['H1'][0]=np.nan
    else:state.hydro_hist['H1']=np.array([])
    window=rolling.make_window_case(case,cfg,2,4,2,True,state,{}, {},'RT')
    with pytest.raises(ValueError,match='历史|history|上游'):
        scuc.build_model(window)


def test_lag_before_day_start_still_uses_explicit_initial_flow():
    case=lagged_case();cfg=case.cfg;state=rolling.initial_state(cfg)
    window=rolling.make_window_case(case,cfg,0,2,0,True,state,{}, {},'RT')
    m=scuc.build_model(window)
    assert pyo.value(m._upstream_out('H2',0))==pytest.approx(50.,abs=1e-12,rel=0)
    assert pyo.value(m._upstream_out('H2',1))==pytest.approx(50.,abs=1e-12,rel=0)


def test_explicit_fixed_da_hydro_mode_still_follows_full_da_power(hydro_da):
    cfg=deepcopy(hydro_da.case.cfg);cfg.mode['realtime']['hydro']='fixed_da'
    plan=rolling.snapshot(hydro_da.model);state=rolling.initial_state(cfg)
    r=rolling.run_rt_period(hydro_da.case,plan,plan,cfg,state,0)
    np.testing.assert_allclose(r['snap']['Ph']['H1'],plan['Ph']['H1'][:8],atol=1e-6,rtol=0)
    assert all(v.fixed for v in r['model'].Ph.values())
    assert not hasattr(r['model'],'c_hydro_window_end')
