"""Reserve evidence tests: raw units and first executed index, no large solve."""
from types import SimpleNamespace
import numpy as np
import pytest

from smm import rolling
from smm.results import reserve_snapshot
from smm.da import run_da
from test_da_stages import tiny_case


def test_reserve_snapshot_off_does_not_modify_dispatch_or_objective():
    result=run_da(tiny_case(reserve=0),verbose=False)
    m=result.model
    import pyomo.environ as pyo
    before=(pyo.value(m.obj),[(v.name,v.value,v.fixed) for v in m.component_data_objects(pyo.Var)])
    evidence=reserve_snapshot(m)
    assert evidence['storage_reserve_enabled'] is False
    assert set(x['unit'] for x in evidence['resources'])=={'CHEAP','LOCAL'}
    assert evidence['system'][0]['R_up_req']==0.
    assert evidence['system'][0]['R_up_supply']==pytest.approx(sum(x['r_up'] for x in evidence['resources'] if x['reserve_eligible']))
    assert before==(pyo.value(m.obj),[(v.name,v.value,v.fixed) for v in m.component_data_objects(pyo.Var)])
    assert 'reserve_eligible' in result.tables['units']
    assert 'R_up_supply' in result.tables['system']


def test_rt_reserve_fields_are_first_executed_not_window_max():
    result=run_da(tiny_case(reserve=0),verbose=False)
    sn=rolling.snapshot(result.model)
    # Supply artificial second relative index only in detached reporting evidence.
    for r in list(sn['reserve']['resources']):
        later=dict(r,relative_period=1,period=1,r_up=999.)
        sn['reserve']['resources'].append(later)
    sn['reserve']['system'][0].update(R_up_req=12.,R_up_supply=9.,R_up_short=3.)
    sn['reserve']['system'].append(dict(sn['reserve']['system'][0],relative_period=1,period=1,R_up_short=777.))
    r=dict(t=0,snap=sn,prices=result.prices,repriced=False,anchor_relaxed=0,time_s=0.,slack={'anchor_slack_max_mwh':0.,'reserve_up_short_max':777.})
    rr=rolling.RollingResult(rt=[r],states=[rolling.initial_state(result.case.cfg)])
    table=rolling.rt_tables(result,rr)
    assert table['system'].R_up_req.tolist()==[12.]
    assert table['system'].R_up_supply.tolist()==[9.]
    assert table['system'].R_up_short.tolist()==[3.]
    assert table['system'].reserve_up_short_max_win.tolist()==[777.]
    assert table['reserve'].relative_period.tolist()==[0,0]
    assert len(table['units'])==2
    assert (table['units'].r_up<999).all()


def test_missing_reserve_evidence_is_not_silently_replaced_with_zero():
    result=run_da(tiny_case(reserve=0),verbose=False);sn=rolling.snapshot(result.model);del sn['reserve']
    r=dict(t=0,snap=sn,prices=result.prices,repriced=False,anchor_relaxed=0,time_s=0.,slack={'anchor_slack_max_mwh':0.})
    with pytest.raises(KeyError,match='reserve'):
        rolling.rt_tables(result,rolling.RollingResult(rt=[r],states=[rolling.initial_state(result.case.cfg)]))


def independent_auditor():
    from smm.reserve_checks import audit
    return audit


def test_incremental_low_soc_does_not_claim_full_delivery():
    from test_storage_reserve import battery_case,solve
    c=battery_case('dis',80.,initial_energy=45.)
    c.cfg.mode['reserve']['down_enabled']=False;c.R_dn=np.zeros(1)
    m=solve(c);e=reserve_snapshot(m);r=next(x for x in e['resources'] if x['type']=='storage')
    assert e['formulation']=='incremental_current_stock'
    assert 'not_full_baseline_plus_activation_delivery_guarantee' in e['scope']
    assert r['reserve_eligible'] is True and r['reserve_qualified']==1
    assert r['r_up']==pytest.approx(20.,abs=1e-6)
    assert r['reserve_duration_lower_margin_mwh']==pytest.approx(25.-20./.9,abs=1e-6)
    assert r['reserve_duration_upper_margin_mwh'] is None
    assert r['full_activation_up_margin_mwh']<0
    assert independent_auditor()({'da':e,'rt_windows':[]})['passed']


def test_eligible_zero_reserve_is_not_reported_as_positive_supply():
    from test_storage_reserve import battery_case,solve
    c=battery_case();c.R_up=np.zeros(1);c.R_dn=np.zeros(1)
    c.cfg.mode['reserve']['down_enabled']=False
    def force(m):m.rs_up['ES1',0].fix(0)
    e=reserve_snapshot(solve(c,force));r=next(x for x in e['resources'] if x['type']=='storage')
    assert r['reserve_qualified']==1 and r['r_up']==0
    assert r['reserve_duration_lower_margin_mwh'] is not None
    check=independent_auditor()({'da':e,'rt_windows':[]})
    assert check['passed'] and check['qualified_resource_periods']==1
    assert check['positive_reserve_resource_periods']==0


def test_only_up_incremental_margin_and_tamper_detection():
    from copy import deepcopy
    from test_storage_reserve import battery_case,solve
    c=battery_case('ch',20.,initial_energy=175.)
    c.cfg.mode['reserve']['down_enabled']=False;c.R_dn=np.zeros(1)
    e=reserve_snapshot(solve(c));r=next(x for x in e['resources'] if x['type']=='storage')
    assert r['reserve_qualified']==1
    assert r['r_dn']==0 and r['reserve_down_enabled'] is False
    expected=r['E_start_mwh']-r['E_min_mwh']-r['T_dur_h']*r['r_up']/r['eta_d']
    assert r['reserve_duration_lower_margin_mwh']==pytest.approx(expected)
    assert r['reserve_duration_upper_margin_mwh'] is None
    assert independent_auditor()({'da':e,'rt_windows':[]})['passed']
    for field in ('r_up','reserve_duration_lower_margin_mwh','reserve_duration_up_margin_mwh'):
        bad=deepcopy(e);br=next(x for x in bad['resources'] if x['type']=='storage');br[field]+=1.
        assert not independent_auditor()({'da':bad,'rt_windows':[]})['passed']


@pytest.mark.parametrize('mode', ['gen','idle','pump'])
def test_incremental_pumped_evidence_audits(mode):
    from test_storage_reserve import pumped_case,solve
    e=reserve_snapshot(solve(pumped_case(mode)))
    assert independent_auditor()({'da':e,'rt_windows':[]})['passed']


def test_pumped_pumping_never_qualified_and_down_price_disabled():
    from test_storage_reserve import pumped_case,solve
    from smm import scuc
    c=pumped_case('pump');c.cfg.mode['reserve']['down_enabled']=False;c.R_dn=np.zeros(1)
    m=solve(c);e=reserve_snapshot(m);r=next(x for x in e['resources'] if x['type']=='pumped')
    assert r['reserve_eligible'] is False and r['reserve_qualified']==0
    assert r['r_up']==r['r_dn']==0.
    assert r['reserve_duration_lower_margin_1e4m3'] is None
    assert independent_auditor()({'da':e,'rt_windows':[]})['passed']
    prices=scuc.extract_prices(m)
    assert prices['reserve_down_enabled'] is False
    assert prices['reserve_dn_price']==[None]


@pytest.mark.parametrize('mode,power,initial,zero_direction', [('ch',80.,20.,'up'),('dis',80.,180.,'down')])
def test_initial_stock_recovery_does_not_fake_incremental_capacity(mode,power,initial,zero_direction):
    from test_storage_reserve import battery_case,solve
    c=battery_case(mode,power,initial_energy=initial)
    c.bids['units']['ES1'].update(soc_min=.15,soc_max=.85)
    e=reserve_snapshot(solve(c))
    r=next(x for x in e['resources'] if x['type']=='storage')
    assert r['r_up' if zero_direction=='up' else 'r_dn']==pytest.approx(0.)
    assert r['reserve_duration_'+zero_direction+'_margin_mwh']==pytest.approx(0.)
    assert independent_auditor()({'da':e,'rt_windows':[]})['passed']


def test_legacy_rt_evidence_missing_formulation_is_not_relabelled():
    result=run_da(tiny_case(reserve=0),verbose=False);sn=rolling.snapshot(result.model)
    sn['reserve'].pop('formulation',None)
    for row in sn['reserve']['resources']:row.pop('reserve_formulation',None)
    r=dict(t=0,snap=sn,prices=result.prices,repriced=False,anchor_relaxed=0,time_s=0.,slack={'anchor_slack_max_mwh':0.})
    tables=rolling.rt_tables(result,rolling.RollingResult(rt=[r],states=[rolling.initial_state(result.case.cfg)]))
    assert tables['units'].reserve_formulation.isna().all()
