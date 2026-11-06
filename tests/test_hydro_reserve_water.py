"""Independent analytic water budgets for incremental period-end hydro reserve."""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc, rolling
from smm.da import run_da
from smm.results import reserve_snapshot
from smm.reserve_checks import audit
from test_hydro_rolling import hydro_case
from test_storage_reserve import solve


def water_case(*, power=20., headroom=.9, dt=.25, duration=.25, water_rate=1., stage='DA', providers=None):
    c = hydro_case(T=1)
    c.stage = stage
    c.cfg.base['dt_h'] = dt
    c.cfg.mode['realtime']['interval_min'] = dt*60
    c.cfg.mode['reserve'].update(providers=['hydro'] if providers is None else providers,
        storage_reserve=False, down_enabled=False, T_dur_h=duration)
    h = c.cfg.units['hydro'][0]
    h.update(ramp=100., zones=[[0., 100.]], init_p_unit=power,
             water_rate=water_rate, e_day_min=None, qout_max=None)
    h['seasons']['wet'].update(qin=0., qeco=0., vmin=10., vmax=200., v_end=10.,
        v0=10.+headroom+.36*water_rate*power*dt)
    c.terminal={'fix_power': {'Ph': {('H1',0): power}}}
    c.fixed={'delta': {('H1',0,0):1}}
    c.R_up=np.array([1000.]);c.R_dn=np.zeros(1)
    return c


def hydro_row(e, unit='H1', t=0):
    return next(r for r in e['resources'] if r['unit']==unit and r['relative_period']==t)


@pytest.mark.parametrize('stage',['DA','ID','RT'])
def test_exhausted_end_stock_cannot_offer_waterless_up_reserve(stage):
    m=solve(water_case(power=45.,headroom=0.,stage=stage))
    assert pyo.value(m.V['H1',0])==pytest.approx(10.)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(0.)
    assert pyo.value(m.sR_up[0])==pytest.approx(1000.)


@pytest.mark.parametrize('dt',[.25,.5,1.])
@pytest.mark.parametrize('water_rate',[1.,1.2,3.])
def test_duration_water_units_and_end_stock_not_baseline_twice(dt,water_rate):
    m=solve(water_case(dt=dt,water_rate=water_rate))
    assert pyo.value(m.V['H1',0])==pytest.approx(10.9)
    expected=.9/(.36*water_rate*.25)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(expected)
    e=reserve_snapshot(m);r=hydro_row(e)
    assert r['reserve_duration_up_margin_1e4m3']==pytest.approx(0.,abs=1e-7)
    assert audit({'da':e,'rt_windows':[]})['hydro_water_check_passed'] is True


@pytest.mark.parametrize('duration',[.25,.5,1.])
def test_duration_scaling_independent_of_dispatch_dt(duration):
    m=solve(water_case(duration=duration))
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(.9/(.36*duration))


def test_online_power_headroom_still_limits_water_rich_reserve():
    m=solve(water_case(power=20.,headroom=100.))
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(80.)


def test_zero_reserve_does_not_impose_extra_baseline_water_budget():
    c=water_case(power=45.,headroom=0.);c.R_up[:]=0
    m=solve(c,lambda m:m.rh_up['H1',0].fix(0.))
    assert pyo.value(m.Ph['H1',0])==pytest.approx(45.)
    assert pyo.value(m.V['H1',0])==pytest.approx(10.)


@pytest.mark.parametrize('duration',[None,0.,-1.,True,'0.25',float('nan'),float('inf')])
def test_hydro_participation_requires_duration_even_when_storage_disabled(duration):
    c=water_case();c.cfg.mode['reserve']['T_dur_h']=duration
    with pytest.raises(ValueError,match='T_dur_h'):scuc.build_model(c)


def test_no_hydro_participation_does_not_enable_water_constraint():
    c=water_case(providers=[]);c.cfg.mode['reserve']['T_dur_h']=float('nan')
    m=solve(c)
    assert not hasattr(m,'c_rhup_water') or not len(m.c_rhup_water)
    assert reserve_snapshot(m)['hydro_reserve_water_enabled'] is False


@pytest.mark.parametrize('pondage,expected',[(0.,0.),(.9,10.)])
def test_run_of_river_uses_effective_pondage_not_unused_seasonal_vmin(pondage,expected):
    c=water_case(power=20.,headroom=0.)
    h=c.cfg.units['hydro'][0];h.update(regulation='run_of_river',pondage=pondage)
    h['seasons']['wet'].update(v0=100.,vmin=10.,vmax=200.,v_end=100.,qin=20.)
    m=solve(c)
    assert pyo.value(m.V['H1',0])==pytest.approx(100.)
    assert m._md.hydro['H1']['vmin']==pytest.approx(100.-pondage)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(expected)


def test_spilled_water_is_not_added_back_to_end_inventory():
    c=water_case(power=20.,headroom=0.)
    h=c.cfg.units['hydro'][0];h.update(regulation='run_of_river',pondage=0.)
    h['seasons']['wet'].update(v0=100.,vmin=100.,vmax=100.,v_end=100.,qin=70.)
    m=solve(c)
    assert pyo.value(m.Qspill['H1',0])==pytest.approx(50.)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(0.)


def test_cascade_credits_only_baseline_water_after_declared_arrival():
    c=hydro_case(T=2)
    c.cfg.mode['reserve'].update(providers=['hydro'],storage_reserve=False,down_enabled=False,T_dur_h=.25)
    h=c.cfg.units['hydro'][0]
    h.update(zones=[[0.,100.]],ramp=100.,init_units_on=0,init_p_unit=0.,e_day_min=None)
    h['seasons']['wet'].update(v0=100.,vmin=10.,v_end=10.,qin=0.,qeco=0.)
    d=deepcopy(h);d.update(id='H2',upstream='H1',lag_periods=1,init_units_on=1)
    d['seasons']['wet'].update(v0=10.9)
    c.cfg.units['hydro'].append(d);c.bids['units']['H2']=deepcopy(c.bids['units']['H1'])
    c.fixed={'delta':{(uid,0,t):1 for uid in ['H1','H2'] for t in range(2)}}
    c.terminal={'fix_power':{'Ph':{(uid,t):p for uid,p in [('H1',50.),('H2',0.)] for t in range(2)}}}
    c.R_up[:]=1000.
    m=solve(c,lambda m:[v.fix(0.) for v in m.Qspill.values()])
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(50.)
    assert pyo.value(m.rh_up['H2',0])==pytest.approx(10.)  # No pre-day release and current release still in transit.
    assert pyo.value(m.rh_up['H2',1])==pytest.approx(60.)  # Only baseline 50MW release arrives, not upstream reserve.


def pricing_case(auxiliary=False):
    c=water_case(power=0.,headroom=5.4)
    c.cfg.mode['auxiliary_pricing']['enabled']=auxiliary
    c.terminal={};c.load_bus={2:np.array([90.])};c.R_up[:]=50.
    return c


@pytest.mark.parametrize('auxiliary',[False,True])
def test_water_scarcity_changes_dispatch_and_price_with_verified_duals(auxiliary):
    r=run_da(pricing_case(auxiliary),verbose=False)
    assert pyo.value(r.model.Ph['H1',0])==pytest.approx(10.)
    assert pyo.value(r.model.rh_up['H1',0])==pytest.approx(50.)
    assert pyo.value(r.model.P['LOCAL',0])==pytest.approx(30.)
    np.testing.assert_allclose(r.prices['lmp'][:,0],[10.,700.],atol=1e-6)
    m=r.pricing_model if auxiliary else r.model
    assert m.c_rhup_water['H1',0].active
    for bus in (1,2):
        n=m.clone();con=n.c_inj[bus,0];con.set_value(con.body==pyo.value(con.upper)+.001)
        scuc.solve_sced(n)
        fd=(pyo.value(n.obj)-pyo.value(m.obj))/.001/.25
        assert fd==pytest.approx(r.prices['lmp'][bus-1,0],abs=1e-4)
    assert pyo.value(r.model.Ph['H1',0])==pytest.approx(10.)
    if auxiliary:
        assert pyo.value(m.V['H1',0])-10.+1e-6>=.36*.25*pyo.value(m.rh_up['H1',0])


@pytest.mark.parametrize('field',['V_end_1e4m3','V_min_1e4m3','water_rate_m3s_per_mw',
    'reserve_incremental_water_up_1e4m3','reserve_available_water_1e4m3','reserve_duration_up_margin_1e4m3'])
@pytest.mark.parametrize('damage',['missing','nan','change'])
def test_hydro_water_evidence_tampering_fails_closed(field,damage):
    e=reserve_snapshot(solve(water_case()));bad=deepcopy(e);r=hydro_row(bad)
    if damage=='missing':r.pop(field)
    elif damage=='nan':r[field]=float('nan')
    else:r[field]+=1.
    try:
        result=audit({'da':bad,'rt_windows':[]})
    except (ValueError,KeyError):
        return
    assert not result['passed'] and not result['hydro_water_check_passed']


def policy_fixture():
    c=water_case();e=reserve_snapshot(solve(c));ev={'da':e,'rt_windows':[],'id_windows':[]}
    checked=audit(ev)
    summary={'reserve':dict(checked, **{k:e[k] for k in ('evidence_version','hydro_reserve_water_formulation','hydro_reserve_water_enabled','hydro_reserve_outflow_formulation','hydro_reserve_outflow_enabled')}),
             'intraday':{'rounds':0}}
    return c,ev,summary,checked


def test_new_water_policy_is_bound_to_summary_and_frozen_inputs():
    from test_validate_runs_evidence import validator
    c,ev,summary,checked=policy_fixture()
    result=validator._hydro_reserve_water_policy_audit(ev,summary,c.cfg.mode,c.cfg.units['hydro'],'wet',checked)
    assert result['水电备用水量已验证'] is True
    assert result['水电备用水量规则']=='period_end_stock_incremental_up_water_v1'


@pytest.mark.parametrize('damage',['summary_all','summary_version','summary_rule','summary_result',
    'window_version','window_rule','window_enabled','duration','frozen_rate','frozen_min','missing_id'])
def test_new_water_evidence_cannot_downgrade_or_change_frozen_physics(damage):
    from test_validate_runs_evidence import validator
    c,ev,summary,checked=policy_fixture();e=ev['da']
    if damage=='summary_all':summary.pop('reserve')
    elif damage=='summary_version':summary['reserve'].pop('evidence_version')
    elif damage=='summary_rule':summary['reserve'].pop('hydro_reserve_water_formulation')
    elif damage=='summary_result':summary['reserve'].pop('hydro_water_check_passed')
    elif damage=='window_version':e.pop('evidence_version')
    elif damage=='window_rule':e.pop('hydro_reserve_water_formulation')
    elif damage=='window_enabled':e['hydro_reserve_water_enabled']=False
    elif damage=='duration':e['T_dur_h']=.5
    elif damage=='frozen_rate':hydro_row(e)['water_rate_m3s_per_mw']=2.
    elif damage=='frozen_min':hydro_row(e)['V_min_1e4m3']=0.
    else:ev.pop('id_windows')
    with pytest.raises(ValueError):
        validator._hydro_reserve_water_policy_audit(ev,summary,c.cfg.mode,c.cfg.units['hydro'],'wet',checked)


@pytest.mark.parametrize('key',['evidence_version','hydro_reserve_water_formulation','hydro_reserve_water_enabled'])
def test_independent_audit_requires_complete_new_schema(key):
    c,ev,summary,checked=policy_fixture();ev['da'].pop(key)
    with pytest.raises(ValueError):audit(ev)


def test_legacy_capacity_evidence_does_not_claim_water_verification():
    from test_validate_runs_evidence import validator
    c,ev,summary,checked=policy_fixture();e=ev['da']
    for key in ('evidence_version','hydro_reserve_water_formulation','hydro_reserve_water_enabled'):
        e.pop(key)
    for key in ('hydro_reserve_outflow_formulation','hydro_reserve_outflow_enabled'):
        e.pop(key)
    row=hydro_row(e);row.pop('hydro_reserve_water_checked');row.pop('hydro_reserve_outflow_checked')
    for field in ('reserve_duration_up_margin_1e4m3','reserve_duration_down_margin_1e4m3'):
        row[field]=None
    checked=audit(ev)
    assert checked['passed']
    assert checked['hydro_water_check_passed'] is None
    assert checked['hydro_water_check_scope']=='legacy_capacity_only_not_water_verified'
    result=validator._hydro_reserve_water_policy_audit(ev,{},c.cfg.mode,c.cfg.units['hydro'],'wet',checked)
    assert result['水电备用水量已验证'] is None
    assert '历史' in result['水电备用水量规则']


def test_nonzero_window_uses_actual_start_inventory():
    c=hydro_case(T=2);cfg=c.cfg
    cfg.mode['reserve'].update(providers=['hydro'],storage_reserve=False,down_enabled=False,T_dur_h=.25)
    h=cfg.units['hydro'][0];h.update(zones=[[0.,100.]],ramp=100.,init_p_unit=20.)
    h['seasons']['wet'].update(qin=0.,qeco=0.,v_end=10.)
    state=rolling.initial_state(cfg);state.t=1;state.hydro['H1']=12.7
    state.hydro_p['H1']=20.;state.hydro_used['H1']=5.;state.hydro_hist['H1'][0]=20.
    term={'fix_power':{'Ph':{('H1',0):20.}}}
    window=rolling.make_window_case(c,cfg,1,2,1,False,state,{'delta':{('H1',0,0):1}},term,'RT')
    window.R_up[:]=1000.
    m=solve(window)
    assert m._md.hydro['H1']['v0']==pytest.approx(12.7)
    assert pyo.value(m.V['H1',0])==pytest.approx(10.9)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(10.)


def test_actual_export_includes_id_and_auxiliary_water_evidence(tmp_path):
    import json
    from smm import full
    from smm.live_runner import capture_provenance
    from test_validate_runs_evidence import validator
    c=hydro_case(T=8);cfg=c.cfg
    cfg.mode['intraday']['enabled']=True
    cfg.mode['auxiliary_pricing']['enabled']=True
    cfg.mode['reserve'].update(providers=['hydro'],storage_reserve=False,down_enabled=False,T_dur_h=.25)
    capture_provenance(tmp_path,cfg.root)
    result=full.run_full(cfg,c,periods=2,log=lambda *a:None)
    summary=full.write_full_outputs(result,tmp_path,make_plots=False)
    evidence=json.loads((tmp_path/'reserve_window_evidence.json').read_text())
    saved=json.loads((tmp_path/'summary.json').read_text())
    assert len(evidence['id_windows'])==len(result['rr'].id_rounds)==1
    assert saved['reserve']['evidence_version']==3
    checked=audit(evidence)
    assert checked['hydro_water_check_passed'] is True
    assert checked['hydro_outflow_check_passed'] is True
    assert saved['reserve']['hydro_reserve_outflow_formulation']=='generation_flow_with_reducible_spill_v1'
    assert validator._hydro_reserve_water_policy_audit(evidence,summary,cfg.mode,cfg.units['hydro'],'wet',checked)['水电备用水量已验证'] is True
    saved_aux=json.loads((tmp_path/'auxiliary_pricing_evidence.json').read_text())
    assert len(saved_aux['id_windows'])==1
    assert saved_aux['id_windows'][0]['window_start']==result['rr'].id_rounds[0]['tau']
    for model_result in [saved_aux['da'],*[r['diagnostics'] for r in saved_aux['rt_windows']],*[r['diagnostics'] for r in saved_aux['id_windows']]]:
        for which in ('dispatch','auxiliary'):
            e=model_result['reserve_evidence'][which]
            checked_aux=audit({'da':e,'rt_windows':[]})
            assert checked_aux['hydro_water_check_passed'] is True
            assert checked_aux['hydro_outflow_check_passed'] is True
    actual_prices=result['rt']['prices'];assert not actual_prices.empty
    np.testing.assert_allclose(result['settle']['detail'].total,
        (result['settle']['detail'].q_da*result['settle']['detail'].lmp_da
         +(result['settle']['detail'].q_rt-result['settle']['detail'].q_da)*result['settle']['detail'].lmp_rt)*cfg.dt,atol=1e-7)


def test_required_ecological_spill_is_deducted_before_reserve_screen():
    c=water_case(power=20.,headroom=.9)
    c.cfg.units['hydro'][0]['seasons']['wet']['qeco']=30.
    m=solve(c)
    assert pyo.value(m.Qspill['H1',0])==pytest.approx(10.)
    assert pyo.value(m.V['H1',0])==pytest.approx(10.)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(0.)
