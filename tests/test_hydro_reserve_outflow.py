"""Necessary instantaneous hydro reserve flow screening, not full activation."""
from copy import deepcopy
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc
from smm.da import run_da
from smm.results import reserve_snapshot
from smm.reserve_checks import audit
from test_hydro_reserve_water import water_case, hydro_row
from test_storage_reserve import solve


def flow_case(qmax=80., *, power=20., eco=0., rate=1., duration=.25, dt=.25, headroom=100., providers=None):
    c=water_case(power=power,headroom=headroom,dt=dt,duration=duration,water_rate=rate,providers=providers)
    h=c.cfg.units['hydro'][0];h['qout_max']=qmax
    for season in h['seasons'].values():season['qeco']=0.
    h['seasons']['wet']['qeco']=eco
    return c


@pytest.mark.parametrize('stage',['DA','ID','RT'])
def test_full_baseline_outflow_leaves_no_up_reserve(stage):
    c=flow_case(qmax=20.);c.stage=stage;m=solve(c)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(0.)
    e=reserve_snapshot(m);r=hydro_row(e)
    assert r['hydro_reserve_outflow_checked'] is True
    assert r['reserve_up_outflow_margin_m3s']==pytest.approx(0.)
    assert audit({'da':e,'rt_windows':[]})['hydro_outflow_check_passed'] is True


def test_reducible_spill_preserves_ecological_total_instead_of_locking_spill():
    m=solve(flow_case(qmax=80.,eco=80.));e=reserve_snapshot(m);r=hydro_row(e)
    assert pyo.value(m.Qspill['H1',0])==pytest.approx(60.)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(60.)
    assert r['Qout_base_m3s']==pytest.approx(80.)
    assert r['reserve_up_activation_spill_m3s']==pytest.approx(0.)
    assert r['reserve_up_activation_outflow_m3s']==pytest.approx(80.)
    assert r['reserve_up_ecological_margin_m3s']==pytest.approx(0.)
    assert audit({'da':e,'rt_windows':[]})['passed']


@pytest.mark.parametrize('rate',[1.,1.2,2.])
@pytest.mark.parametrize('dt,duration',[(.25,.25),(.5,.25),(.25,1.)])
def test_flow_units_do_not_multiply_time_or_water_volume_factor(rate,dt,duration):
    m=solve(flow_case(qmax=80.,rate=rate,dt=dt,duration=duration))
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(80./rate-20.)


def test_multimachine_uses_station_total_not_per_unit_flow_limit():
    c=flow_case(qmax=150.,rate=2.);h=c.cfg.units['hydro'][0]
    h.update(n_units=2,init_units_on=2,init_p_unit=10.)
    c.bids['units']['H1']['segments'][0]['p_end']=200.
    c.fixed['delta'][('H1',0,0)]=2
    m=solve(c)
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(55.)
    assert pyo.value(m.Ph['H1',0])+pyo.value(m.rh_up['H1',0])==pytest.approx(75.)


def test_water_limit_remains_separate_and_does_not_credit_spill_recovery():
    m=solve(flow_case(qmax=80.,headroom=.9))
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(10.)
    r=hydro_row(reserve_snapshot(m))
    assert r['reserve_up_outflow_margin_m3s']==pytest.approx(50.)
    assert r['reserve_duration_up_margin_1e4m3']==pytest.approx(0.)


def test_no_upper_flow_setting_does_not_invent_limit_or_certify_infinite_capacity():
    m=solve(flow_case(qmax=None));e=reserve_snapshot(m);r=hydro_row(e)
    assert len(m.c_rhup_qout)==0
    assert pyo.value(m.rh_up['H1',0])==pytest.approx(80.)
    assert r['qout_max_m3s'] is None and r['reserve_up_outflow_margin_m3s'] is None
    assert r['hydro_reserve_outflow_status']=='not_configured'
    result=audit({'da':e,'rt_windows':[]})
    assert result['passed'] and result['hydro_outflow_check_passed'] is None
    assert result['hydro_outflow_check_scope']=='not_configured_no_hydro_outflow_limit'


def test_no_hydro_participation_does_not_add_reserve_flow_gate():
    m=solve(flow_case(qmax=20.,providers=[]));e=reserve_snapshot(m)
    assert len(m.c_rhup_qout)==0
    assert hydro_row(e)['hydro_reserve_outflow_status']=='not_participating'
    assert audit({'da':e,'rt_windows':[]})['hydro_outflow_check_passed'] is None


def test_baseline_flow_violation_is_not_repaired_by_activation_spill_witness():
    m=scuc.build_model(flow_case(qmax=19.))
    # Baseline 20MW with rate=1 is already above the physical bound.
    result=pyo.SolverFactory('appsi_highs').solve(m,load_solutions=False)
    assert 'infeasible' in str(result.solver.termination_condition).lower()


@pytest.mark.parametrize('auxiliary',[False,True])
def test_flow_scarcity_dispatch_prices_and_dual_finite_differences(auxiliary):
    c=flow_case(qmax=60.,power=0.);c.terminal={};c.load_bus={2:np.array([90.])};c.R_up[:]=50.
    c.cfg.mode['auxiliary_pricing']['enabled']=auxiliary
    result=run_da(c,verbose=False)
    assert pyo.value(result.model.Ph['H1',0])==pytest.approx(10.)
    assert pyo.value(result.model.rh_up['H1',0])==pytest.approx(50.)
    assert pyo.value(result.model.P['LOCAL',0])==pytest.approx(30.)
    np.testing.assert_allclose(result.prices['lmp'][:,0],[10.,700.],atol=1e-6)
    model=result.pricing_model if auxiliary else result.model
    assert model.c_rhup_qout['H1',0].active
    for bus in (1,2):
        shifted=model.clone();con=shifted.c_inj[bus,0];con.set_value(con.body==pyo.value(con.upper)+.001)
        scuc.solve_sced(shifted)
        fd=(pyo.value(shifted.obj)-pyo.value(model.obj))/.001/.25
        assert fd==pytest.approx(result.prices['lmp'][bus-1,0],abs=1e-4)
    if auxiliary:
        for which in ('dispatch','auxiliary'):
            e=result.pricing_info['reserve_evidence'][which]
            assert audit({'da':e,'rt_windows':[]})['hydro_outflow_check_passed'] is True


@pytest.mark.parametrize('field',['qout_max_m3s','qeco_m3s','Qgen_base_m3s','Qspill_base_m3s','Qout_base_m3s',
    'reserve_up_activation_generation_m3s','reserve_up_activation_spill_m3s','reserve_up_activation_outflow_m3s',
    'reserve_up_outflow_margin_m3s','reserve_up_ecological_margin_m3s'])
@pytest.mark.parametrize('damage',['missing','nan','change'])
def test_outflow_evidence_fails_closed(field,damage):
    e=reserve_snapshot(solve(flow_case(eco=80.)));row=hydro_row(e)
    if damage=='missing':row.pop(field)
    elif damage=='nan':row[field]=float('nan')
    else:row[field]+=1.
    try:result=audit({'da':e,'rt_windows':[]})
    except (ValueError,KeyError):return
    assert not result['passed']


def new_policy_fixture(qmax=80.):
    c=flow_case(qmax=qmax);e=reserve_snapshot(solve(c));ev={'da':e,'rt_windows':[],'id_windows':[]};checked=audit(ev)
    keys=('evidence_version','hydro_reserve_water_formulation','hydro_reserve_water_enabled',
          'hydro_reserve_outflow_formulation','hydro_reserve_outflow_enabled')
    summary={'reserve':dict(checked,**{k:e[k] for k in keys}),'intraday':{'rounds':0}}
    return c,ev,summary,checked


@pytest.mark.parametrize('damage',['summary_rule','summary_flag','summary_status','window_rule','window_flag',
    'downgrade_window','downgrade_all','frozen_qmax','frozen_qeco'])
def test_new_outflow_summary_cannot_downgrade_or_forge_frozen_limits(damage):
    from test_validate_runs_evidence import validator
    c,ev,summary,checked=new_policy_fixture();e=ev['da']
    if damage=='summary_rule':summary['reserve'].pop('hydro_reserve_outflow_formulation')
    elif damage=='summary_flag':summary['reserve'].pop('hydro_reserve_outflow_enabled')
    elif damage=='summary_status':summary['reserve'].pop('hydro_outflow_check_passed')
    elif damage=='window_rule':e.pop('hydro_reserve_outflow_formulation')
    elif damage=='window_flag':e.pop('hydro_reserve_outflow_enabled')
    elif damage=='downgrade_window':e['evidence_version']=2
    elif damage=='downgrade_all':e['evidence_version']=summary['reserve']['evidence_version']=2
    elif damage=='frozen_qmax':hydro_row(e)['qout_max_m3s']=100.
    else:hydro_row(e)['qeco_m3s']=1.
    with pytest.raises(ValueError):
        validator._hydro_reserve_water_policy_audit(ev,summary,c.cfg.mode,c.cfg.units['hydro'],'wet',checked)


def test_old_water_only_version_remains_water_checked_not_outflow_certified():
    from test_validate_runs_evidence import validator
    c,ev,summary,_=new_policy_fixture();e=ev['da'];e['evidence_version']=2
    for k in ('hydro_reserve_outflow_formulation','hydro_reserve_outflow_enabled'):e.pop(k)
    for row in e['resources']:row.pop('hydro_reserve_outflow_checked',None)
    checked=audit(ev)
    assert checked['passed'] and checked['hydro_water_check_passed'] is True
    assert checked['hydro_outflow_check_passed'] is None
    summary['reserve']=dict(checked,**{k:e[k] for k in ('evidence_version','hydro_reserve_water_formulation','hydro_reserve_water_enabled')})
    result=validator._hydro_reserve_water_policy_audit(ev,summary,c.cfg.mode,c.cfg.units['hydro'],'wet',checked)
    assert result['水电备用水量已验证'] is True
    assert result['水电备用出库已验证'] is None


def test_unconfigured_limit_is_explicit_in_final_validation():
    from test_validate_runs_evidence import validator
    c,ev,summary,checked=new_policy_fixture(None)
    result=validator._hydro_reserve_water_policy_audit(ev,summary,c.cfg.mode,c.cfg.units['hydro'],'wet',checked)
    assert result['水电备用出库已验证'] is None
    assert '未配置' in result['水电备用出库规则']
