"""RT-only reference-continuation preference; no fictitious hard terminal target."""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo
import pytest
from pyomo.repn import generate_standard_repn

from smm import scuc
from test_asset_reachability import solve, raw_status
from test_hydro_rolling import hydro_case, assert_water_and_bounds


def lookahead_case(*, target=110., recovery=0., value=0., price=300., T=1):
    c=hydro_case(T=T)
    c.T=T;c.cfg.base['periods']=T+4;c.stage='RT'
    c.cfg.units['hydro'][0]['ramp']=10000.
    # Synthetic economic tradeoff fixture declares its own bid cap; production
    # limits remain untouched and production bid validation still executes.
    c.cfg.mode['price_limits']['bid_max']=max(c.cfg.mode['price_limits']['bid_max'],price)
    for unit in ('CHEAP','LOCAL'):
        c.bids['units'][unit]['segments'][0]['price']=price
    c.terminal=dict(
        state_band_terminal_policy=dict(mode='soft',penalty_yuan_per_discharge_mwh=1000.),
        state_value=[('V','H1',T-1,value)],
        hydro_terminal_lookahead={'H1':dict(relative_period=T-1,
            minimum_window_end_volume=target-recovery,terminal_target=target,
            reference_recovery_volume=recovery,reference_source='day_ahead',
            reference_round=None,reference_fallback_reason=None)})
    return c


def fixed_case(**kwargs):
    c=lookahead_case(**kwargs)
    c.terminal['fix_power']={'Ph':{('H1',t):50. for t in range(c.T)}}
    return c


def row(c):
    return c.terminal['hydro_terminal_lookahead']['H1']


def coefficient(m,variable):
    repn=generate_standard_repn(m.obj.expr)
    return {id(v):c for v,c in zip(repn.linear_vars,repn.linear_coefs)}.get(id(variable),0.)


def test_small_model_reports_finite_shortage_native_energy_cost_and_reference():
    c=fixed_case(target=100.,recovery=-10.)
    m=solve(scuc.build_model(c));b=m.hydro_terminal_lookahead
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert d['state']==pytest.approx(100.)
    assert d['projected_terminal_volume']==pytest.approx(90.)
    assert d['projected_terminal_shortfall']==pytest.approx(10.)
    assert d['actual_projected_shortfall']==pytest.approx(10.)
    assert d['factor']==pytest.approx(1/.36)
    assert d['projected_terminal_shortfall_mwh']==pytest.approx(10/.36)
    assert d['penalty_cost']==pytest.approx(10000/.36)
    assert coefficient(m,b.slack['H1'])==pytest.approx(1000/.36)  # no extra dt
    assert d['forecast_only'] and d['active'] and d['slack_zero_means_target_met']
    assert d['reference_source']=='day_ahead' and d['reference_round'] is None
    assert d['scope']=='reference_continuation_terminal_preference_not_feasibility_proof'
    assert not hasattr(m,'c_vend')  # did not restore hard day-end target
    assert len(m.T)==1 and list(b.component_map(pyo.Var))==['slack']
    assert_water_and_bounds(m)


def test_inputs_and_diagnostic_copies_do_not_mutate_model_or_original_spec():
    c=fixed_case();row(c).update(reference_source='intraday',reference_round=12,
                               reference_fallback_reason='new_reference')
    m=solve(scuc.build_model(c));row(c)['terminal_target']=777.
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert d['terminal_target']==110. and d['reference_round']==12
    d['terminal_target']=999.
    assert m._hydro_terminal_lookahead['H1']['terminal_target']==110.


@pytest.mark.parametrize('price,retains',[ (300.,True),(1500.,False) ])
def test_two_hour_window_finite_price_tradeoff_not_hidden_hard_target(price,retains):
    c=lookahead_case(target=100.,price=price,T=8)
    m=solve(scuc.build_model(c));d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert m._md.dt==.25 and len(m.T)==8 and c.cfg.T==12
    if retains:
        assert d['projected_terminal_shortfall']==pytest.approx(0.,abs=1e-6)
        assert d['state']>=100.-1e-6
    else:
        assert d['projected_terminal_shortfall']==pytest.approx(36.)
        assert d['penalty_cost']==pytest.approx(100000.)
        assert d['state']==pytest.approx(64.)
    assert_water_and_bounds(m)


def test_higher_than_reference_is_not_penalized_or_rewarded_under_negative_price():
    c=lookahead_case(target=100.,price=-50.,T=8)
    m=scuc.build_model(c);m.V['H1',7].fix(120.);solve(m)
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert d['state']>100.
    assert d['penalty_cost']==pytest.approx(0.)
    objective=pyo.value(m.obj)
    m.V['H1',7].fix(130.);solve(m)
    assert pyo.value(m.obj)==pytest.approx(objective)  # flat above target, no salvage reward
    assert coefficient(m,m.V['H1',7])==0.
    assert all(pyo.value(m.Ph['H1',t])==pytest.approx(0.) for t in m.T)


def test_same_dispatch_less_spill_strictly_improves_new_objective_from_first_window():
    c=fixed_case(target=100.,T=8)
    old=scuc.build_model(c);old.V['H1',7].fix(90.);solve(old)
    retained=scuc.build_model(c);retained.V['H1',7].fix(100.);solve(retained)
    for t in old.T:
        assert pyo.value(old.Ph['H1',t])==pytest.approx(pyo.value(retained.Ph['H1',t]))
        for g in old.G:assert pyo.value(old.P[g,t])==pytest.approx(pyo.value(retained.P[g,t]))
    assert pyo.value(old.obj)-pyo.value(retained.obj)==pytest.approx(10000/.36)
    assert sum(pyo.value(old.Qspill['H1',t]-retained.Qspill['H1',t])*.09 for t in old.T)==pytest.approx(10.)
    assert_water_and_bounds(old);assert_water_and_bounds(retained)


def test_shortage_cost_finite_difference_for_extra_initial_water_and_target():
    base=fixed_case(target=110.);m=solve(scuc.build_model(base));obj=pyo.value(m.obj)
    eps=.001
    water=deepcopy(base);water.cfg.units['hydro'][0]['seasons']['wet']['v0']+=eps
    more=solve(scuc.build_model(water))
    assert (pyo.value(more.obj)-obj)/eps==pytest.approx(-1000/.36,abs=1e-4)
    target=deepcopy(base);row(target)['terminal_target']+=eps;row(target)['minimum_window_end_volume']+=eps
    raised=solve(scuc.build_model(target))
    assert (pyo.value(raised.obj)-obj)/eps==pytest.approx(1000/.36,abs=1e-4)


def test_signed_market_value_and_emergency_band_policy_are_not_replaced():
    c=fixed_case(value=-7.)
    c.terminal.update(state_band=[('V','H1',0,101.,150.,'band')],state_band_soft=True,state_band_penalty=123.)
    m=solve(scuc.build_model(c))
    assert m._state_values==[('V','H1',0,-7.)]
    assert coefficient(m,m.V['H1',0])==7.
    assert coefficient(m,m.sB_dn[0])==123.
    assert coefficient(m,m.hydro_terminal_lookahead.slack['H1'])==pytest.approx(1000/.36)


def test_threshold_above_physical_capacity_stays_soft_and_is_not_clipped():
    m=solve(scuc.build_model(fixed_case(target=250.)))
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert d['minimum_window_end_volume']==250.
    assert d['state']==pytest.approx(100.)
    assert d['projected_terminal_shortfall']==pytest.approx(150.)
    m.V['H1',0].fix(201.)
    assert raw_status(m)==('infeasible',False)  # physical bound remains hard


def test_threshold_below_physical_lower_bound_has_no_penalty():
    m=solve(scuc.build_model(fixed_case(target=-20.)))
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert d['minimum_window_end_volume']==-20.
    assert d['penalty_cost']==0.


def test_objective_rebuild_preserves_active_cost_and_does_not_restore_removed_block():
    m=solve(scuc.build_model(fixed_case()));before=pyo.value(m.obj)
    scuc.set_penalties(m,dict(m._pen));assert pyo.value(m.obj)==pytest.approx(before)
    b=m.hydro_terminal_lookahead;cost=pyo.value(b.cost)
    m.obj.set_value(m.obj.expr-b.cost);b.deactivate();b.slack['H1'].fix(0.)
    scuc.set_penalties(m,dict(m._pen))
    assert coefficient(m,b.slack['H1'])==0.
    assert pyo.value(m.obj)==pytest.approx(before-cost)
    d=scuc.hydro_terminal_lookahead_diagnostics(m)[0]
    assert not d['active'] and not d['slack_zero_means_target_met']
    assert d['projected_terminal_shortfall']==0. and d['actual_projected_shortfall']>0.
    assert d['penalty_cost']==0.


def test_absent_lookahead_preserves_legacy_model_and_diagnostics():
    c=fixed_case();c.terminal.pop('hydro_terminal_lookahead')
    m=solve(scuc.build_model(c))
    assert not hasattr(m,'hydro_terminal_lookahead')
    assert m._hydro_terminal_lookahead=={}
    assert scuc.hydro_terminal_lookahead_diagnostics(m)==[]


@pytest.mark.parametrize('stage',['DA','ID'])
def test_lookahead_cannot_leak_into_day_ahead_or_intraday(stage):
    c=fixed_case();c.stage=stage
    with pytest.raises(ValueError,match='仅可用于RT'):scuc.build_model(c)


@pytest.mark.parametrize('policy',[None,{'mode':'hard','penalty_yuan_per_discharge_mwh':None}])
def test_lookahead_rejected_without_explicit_rt_soft_policy(policy):
    c=fixed_case()
    if policy is None:c.terminal.pop('state_band_terminal_policy')
    else:c.terminal['state_band_terminal_policy']=policy
    with pytest.raises(ValueError,match='RT soft'):scuc.build_model(c)


def test_day_end_rejects_duplicate_future_penalty_even_if_target_is_soft():
    c=fixed_case();c.cfg.base['periods']=1
    with pytest.raises(ValueError,match='日末窗口'):scuc.build_model(c)


@pytest.mark.parametrize('bad',[{},[],{'UNKNOWN':{}},{'H1':[]}])
def test_malformed_or_incomplete_station_specs_rejected(bad):
    c=fixed_case();c.terminal['hydro_terminal_lookahead']=bad
    with pytest.raises(ValueError,match='水电参考续程'):scuc.build_model(c)


@pytest.mark.parametrize('field',['relative_period','minimum_window_end_volume','terminal_target','reference_recovery_volume'])
@pytest.mark.parametrize('bad',[None,True,float('nan'),float('inf'),'100'])
def test_nonfinite_missing_or_nonnumeric_inputs_rejected(field,bad):
    c=fixed_case();row(c)[field]=bad
    with pytest.raises(ValueError,match='有限数值'):scuc.build_model(c)


@pytest.mark.parametrize('period',[-1,1,.5])
def test_lookahead_only_on_current_window_final_state(period):
    c=fixed_case();row(c)['relative_period']=period
    with pytest.raises(ValueError,match='最后时段'):scuc.build_model(c)


def test_inconsistent_reference_accounting_is_rejected():
    c=fixed_case();row(c)['reference_recovery_volume']=10.
    with pytest.raises(ValueError,match='阈值'):scuc.build_model(c)


@pytest.mark.parametrize('source',[None,'old_intraday','actual_future'])
def test_reference_source_must_be_current_day_ahead_or_intraday(source):
    c=fixed_case();row(c)['reference_source']=source
    with pytest.raises(ValueError,match='来源'):scuc.build_model(c)


@pytest.mark.parametrize('policy',['preserve','remove'])
@pytest.mark.parametrize('anchor_override',[None,1234.])
def test_real_auxiliary_preserve_remove_and_anchor_override_are_independent(policy,anchor_override):
    from smm.auxiliary_pricing import run_auxiliary_pricing
    c=fixed_case(value=-7.)
    c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']=policy
    if anchor_override is not None:
        c.cfg.mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh']=anchor_override
    m=solve(scuc.build_model(c));before=pyo.value(m.obj)
    original=scuc.hydro_terminal_lookahead_diagnostics(m)
    auxiliary=run_auxiliary_pricing(m)['model'];b=auxiliary.hydro_terminal_lookahead
    assert m.hydro_terminal_lookahead.active and pyo.value(m.obj)==pytest.approx(before)
    assert scuc.hydro_terminal_lookahead_diagnostics(m)==original
    assert coefficient(auxiliary,auxiliary.V['H1',0])==7.  # signed market value survives
    if policy=='preserve':
        assert b.active and not b.slack['H1'].fixed
        assert pyo.value(b.slack['H1'])==pytest.approx(10.)
        assert coefficient(auxiliary,b.slack['H1'])==pytest.approx(1000/.36)
    else:
        assert not b.active and b.slack['H1'].fixed and pyo.value(b.slack['H1'])==0.
        assert coefficient(auxiliary,b.slack['H1'])==0.
    prior=pyo.value(auxiliary.obj)
    scuc.set_penalties(auxiliary,dict(auxiliary._pen))
    assert pyo.value(auxiliary.obj)==pytest.approx(prior)
    assert coefficient(auxiliary,b.slack['H1'])==pytest.approx(1000/.36 if policy=='preserve' else 0.)
    scuc.solve_sced(auxiliary)
    assert np.isfinite(pyo.value(auxiliary.obj))
    assert_water_and_bounds(auxiliary)
