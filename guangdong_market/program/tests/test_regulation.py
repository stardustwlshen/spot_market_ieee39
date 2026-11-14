"""Small AGC capability/unit/dual tests; no full-day solves."""
import copy

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc, regulation
from smm.config import load_config
from smm.bids import default_bids
from test_da_stages import tiny_case


def resource(*, cap=10., ramp=100., k=1., bid=8.):
    return dict(eligible=True, capacity_mw=cap, ramp_mw_per_min=ramp,
                performance=k, bid_yuan_per_mw=bid)


def case_with_agc(ratio=.05, resources=None):
    c = tiny_case(reserve=0)
    c.cfg.mode['regulation'] = dict(enabled=True, demand_ratio=ratio, mileage_ratio=4.,
        response_minutes=1., sustain_h=.25, shortage_penalty=100000.,
        resources=resources if resources is not None else {
            'CHEAP': resource(k=2, bid=6), 'LOCAL': resource(k=1, bid=8)})
    return c


def solve(c, modify=None):
    m = scuc.build_model(c)
    if modify:
        modify(m)
    regulation.prepare_energy(m)
    scuc.solve_scuc(m)
    scuc.fix_integers(m)
    regulation.clear_reservations(m)
    scuc.solve_sced(m)
    return m


def test_physical_requirement_not_performance_weighted_and_mileage_units():
    m = solve(case_with_agc())
    snap = regulation.snapshot(m)
    assert snap['q_mw']['CHEAP'][0] == pytest.approx(5.)  # not 5/K = 2.5
    assert snap['q_mw']['LOCAL'][0] == pytest.approx(0.)
    assert pyo.value(regulation.bid_objective_term(m)) == pytest.approx((6 / 2) * 4 * 5)  # no dt
    assert snap['auction_info']['objective'] == pytest.approx(60)
    assert regulation.objective_term(m) == 0  # final SCED has energy objective only
    assert snap['clearing_mode'] == 'sequential_reservation'
    assert snap['auction_info']['energy_continuous_free'] is True
    assert not any(v.fixed for v in m.P.values())
    assert all(v.fixed for v in m.q_agc.values())
    assert all(v.fixed for v in m.s_agc.values())
    price = regulation.prices(m)
    assert price['available'] is False
    assert price['requirement_marginal_yuan_per_mw_period'] == [None]
    assert scuc.extract_prices(m)['regulation']['normalized_marginal_yuan_per_mw_mileage'] == [None]


def test_shortage_is_explicit_not_fake_award_or_settlement_price():
    m = solve(case_with_agc(.30))
    snap = regulation.snapshot(m)
    assert sum(x[0] for x in snap['q_mw'].values()) == pytest.approx(20)
    assert snap['shortage_mw'] == pytest.approx([10])
    assert regulation.prices(m)['available'] is False
    assert snap['auction_info']['objective'] == pytest.approx(100000*10 + 4*(3*10 + 8*10))


def test_response_ramp_limit_and_shared_existing_reserve():
    c = case_with_agc(.1, {'CHEAP': resource(ramp=3)})
    m = solve(c)
    assert pyo.value(m.q_agc['CHEAP', 0]) == pytest.approx(3)
    c = case_with_agc(.1, {'CHEAP': resource()})
    # Energy export limit fixes cheap dispatch to 50; 145 MW reserve leaves 5 MW AGC.
    def fixed_operating_point(m):
        m.r_up['CHEAP', 0].fix(145)
        m.P['CHEAP', 0].fix(50)
    m = solve(c, fixed_operating_point)
    assert pyo.value(m.q_agc['CHEAP', 0]) == pytest.approx(5)
    assert pyo.value(m.P['CHEAP', 0]) + pyo.value(m.r_up['CHEAP', 0]) + pyo.value(m.q_agc['CHEAP', 0]) <= 200 + 1e-6


def test_starting_thermal_cannot_provide_agc():
    c = case_with_agc(.05, {'CHEAP': resource()})
    c.cfg.units['thermal'][0]['init']['online'] = False
    c.cfg.units['thermal'][0]['init']['p0'] = 0
    m = solve(c)
    assert pyo.value(m.q_agc['CHEAP', 0]) == pytest.approx(0)


def storage_case(power=5., charging=False, soc0=.5, sustain=.25):
    c = case_with_agc(.10, {'ES1': resource(ramp=100, k=1.5)})
    source = load_config()
    d = copy.deepcopy(source.units['storage'][0])
    d['soc0'] = soc0
    c.cfg.units['storage'] = [d]
    c.bids['units']['ES1'] = default_bids(source)['units']['ES1']
    c.bids['units']['ES1']['soc_end'] = None
    c.cfg.mode['regulation']['sustain_h'] = sustain
    c.fixed = {'u_dis': {('ES1', 0): int(not charging)}, 'u_ch': {('ES1', 0): int(charging)}}
    c.terminal = {'fix_power': {'Pd': {('ES1', 0): 0 if charging else power},
                              'Pc': {('ES1', 0): power if charging else 0}}}
    return c


@pytest.mark.parametrize('charging', [False, True])
def test_storage_symmetric_award_preserves_mode(charging):
    m = solve(storage_case(charging=charging))
    # 5 MW base (dis)charge can only move 5 MW towards zero, never reverse mode.
    assert pyo.value(m.q_agc['ES1', 0]) == pytest.approx(5)
    assert pyo.value(m.s_agc[0]) == pytest.approx(5)


def test_idle_storage_cannot_offer_symmetric_agc():
    c = storage_case(power=0)
    c.fixed = {'u_dis': {('ES1', 0): 0}, 'u_ch': {('ES1', 0): 0}}
    m = solve(c)
    assert pyo.value(m.q_agc['ES1', 0]) == pytest.approx(0)


def test_storage_start_and_end_energy_budgets_are_enforced():
    # Charging moves away from minimum SOC, but START energy bounds still matter.
    m = solve(storage_case(power=10, charging=True, soc0=.11, sustain=.25))
    assert pyo.value(m.q_agc['ES1', 0]) == pytest.approx((.11-.10)*200*.95/.25)
    # Discharge moves toward minimum SOC: END energy is the tighter limit.
    m = solve(storage_case(power=10, soc0=.12, sustain=.25))
    expected = ((.12-.10)*200 - 10*.25/.95)*.95/.25
    assert pyo.value(m.q_agc['ES1', 0]) == pytest.approx(expected)


def test_disabled_adds_no_agc_model_or_objective():
    c = case_with_agc()
    c.cfg.mode['regulation']['enabled'] = False
    m = solve(c)
    assert not hasattr(m, 'q_agc')
    assert regulation.objective_term(m) == 0
    assert regulation.snapshot(m)['enabled'] is False


@pytest.mark.parametrize('change', [
    lambda r: r.update(unknown=1),
    lambda r: r.update(demand_ratio=float('nan')),
    lambda r: r.update(mileage_ratio=0),
    lambda r: r.update(sustain_h=.5),
    lambda r: r.update(slack_budget_tolerance=.01),
    lambda r: r['resources'].update(UNKNOWN=resource()),
    lambda r: r['resources']['CHEAP'].update(performance=.5),
    lambda r: r['resources']['CHEAP'].update(bid_yuan_per_mw=8.05),
    lambda r: r['resources']['CHEAP'].update(capacity_mw=13),
    lambda r: r['resources']['CHEAP'].update(ramp_mw_per_min=10001),
    lambda r: r['resources']['CHEAP'].update(eligible='yes'),
])
def test_invalid_agc_configuration_fails_closed(change):
    c = case_with_agc()
    change(c.cfg.mode['regulation'])
    with pytest.raises(ValueError):
        scuc.build_model(c)


def test_unsupported_renewable_cannot_claim_agc():
    c = case_with_agc()
    c.cfg.units['renewable'] = [dict(id='WIND', type='wind', cap=100, bus=1)]
    c.cfg.mode['regulation']['resources'] = {'WIND': resource()}
    with pytest.raises(ValueError, match='不支持'):
        regulation.validate_config(c.cfg)


def test_thermal_scheduled_ramp_consumes_response_headroom():
    c = case_with_agc(.1, {'CHEAP': resource(ramp=1)})
    d = c.cfg.units['thermal'][0]
    d['ramp'] = 1.
    d['init']['p0'] = 0.
    # At the 15 MW interval ramp limit there is no same-direction AGC headroom.
    m = solve(c, lambda m: m.P['CHEAP', 0].fix(15))
    assert pyo.value(m.q_agc['CHEAP', 0]) == pytest.approx(0)


def test_snapshot_carries_independent_physical_inputs_and_violation_diagnostics():
    m = solve(case_with_agc())
    s = regulation.snapshot(m)
    assert s['diagnostics']['constraint_violation_max_mw'] < 1e-6
    assert s['diagnostics']['energy_constraint_violation_max_mwh'] < 1e-6
    assert s['physical']['CHEAP']['reserve_up_mw'][0] >= 0
    assert s['period_indices'] == [0]
    # Diagnose a manually corrupted result without another optimization.
    m.q_agc['CHEAP', 0].set_value(15, skip_validation=True)
    bad = regulation.snapshot(m)
    assert bad['diagnostics']['family_max_violations']['certified_capacity'] == pytest.approx(5)


@pytest.mark.parametrize('offer', [0., 3.])
def test_declared_capacity_offer_limits_physical_award(offer):
    r = resource()
    r['offered_capacity_mw'] = offer
    m = solve(case_with_agc(.05, {'CHEAP': r}))
    assert pyo.value(m.q_agc['CHEAP', 0]) == pytest.approx(offer)
    assert regulation.snapshot(m)['resources']['CHEAP']['offered_capacity_mw'] == offer


@pytest.mark.parametrize('offer', [-1., 11., float('inf'), True])
def test_declared_capacity_offer_cannot_exceed_technical_cap(offer):
    c = case_with_agc()
    c.cfg.mode['regulation']['resources']['CHEAP']['offered_capacity_mw'] = offer
    with pytest.raises(ValueError):
        scuc.build_model(c)


def test_sequential_auction_never_buys_free_safety_slack():
    c = case_with_agc(.04, {'LOCAL': resource()})
    c.load_bus[2] = np.array([250.])  # CHEAP 50 MW export + LOCAL 200 MW: no AGC upward room
    m = solve(c)
    s = regulation.snapshot(m)
    assert s['q_mw']['LOCAL'][0] < 1e-3
    assert s['shortage_mw'][0] > 9.999
    budget = s['slack_budget']
    assert budget['auction_max_violation'] < 1e-6
    assert budget['final_max_violation'] < 1e-6
    assert all(r['actual'] <= r['baseline'] + budget['tolerance'] + 1e-6 for r in budget['rows'])
    assert any(r['variable'] == 'sl' and r['baseline'] < 1e-6 for r in budget['rows'])
    # Removing the budgets is a counterexample: the AGC-only LP can create its
    # headroom through unpriced load shedding/line overload. Not production code.
    for row in m._agc_slack_budgets:
        getattr(m, row['variable'])[row['index']].setub(None)
    for v in m.q_agc.values():
        v.unfix()
    for v in m.s_agc.values():
        v.unfix()
    m.del_component('obj')
    m.obj = pyo.Objective(expr=regulation.bid_objective_term(m))
    scuc.solve_sced(m)
    assert pyo.value(m.q_agc['LOCAL', 0]) == pytest.approx(10)
    assert sum(pyo.value(v) for v in m.shed.values()) + sum(pyo.value(v) for v in m.sl.values()) > 1


def test_positive_pre_uc_slack_budget_is_preserved_not_silently_zeroed():
    c = case_with_agc(.01, {'LOCAL': resource()})
    c.load_bus[2] = np.array([260.])  # baseline must use network/balance slack
    m = solve(c)
    b = regulation.snapshot(m)['slack_budget']
    assert any(r['baseline'] > 1 for r in b['rows'])
    assert b['final_max_violation'] < 1e-6
    assert all(r['upper'] <= r['baseline'] + b['tolerance'] + 1e-9 for r in b['rows'])


def test_public_solver_entrypoints_cannot_skip_sequential_stages():
    m = scuc.build_model(case_with_agc())
    with pytest.raises(ValueError, match='prepare_energy'):
        scuc.solve_scuc(m)
    regulation.prepare_energy(m)
    scuc.solve_scuc(m)
    scuc.fix_integers(m)
    with pytest.raises(ValueError, match='clear_reservations'):
        scuc.solve_sced(m)
    regulation.clear_reservations(m)
    scuc.solve_sced(m)
    with pytest.raises(ValueError, match='唯一能量SCUC'):
        scuc.solve_scuc(m)
