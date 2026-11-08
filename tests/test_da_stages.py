"""Small stage-contract regressions; no full-day MILP needed."""
import copy

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from smm import scuc, settlement
from types import SimpleNamespace
from smm.case import DACase
from smm.config import load_config
from smm.da import run_da, summarize
from smm.network import Network
from smm.rolling import snapshot


def tiny_case(reserve=500.0, local_price=700.0):
    cfg = load_config()
    cfg.mode["startup_cost_mode"] = "state_dependent"
    # This fixture tests the historical single-SCED contract, independently of current defaults.
    cfg.mode["auxiliary_pricing"]["enabled"] = False
    cfg.mode["auxiliary_pricing"]["rt_tracking_policy"] = "preserve"
    cfg.mode["auxiliary_pricing"].pop("anchor_penalty_yuan_per_discharge_mwh", None)
    cfg.mode["realtime"].pop("tracking_policy", None)
    cfg.mode["reserve"].update(up_requirement_mode='formula', storage_reserve=False, down_enabled=True)
    # Preserve the original energy-only regression contract when new markets default on.
    if "regulation" in cfg.mode:
        cfg.mode["regulation"]["enabled"] = False
    cfg.base['periods'] = 1
    # This synthetic zero-Pmin, one-segment test market declares its own valid
    # generic bid policy; the production validator is never bypassed.
    cfg.mode['bid_rules'] = {'profile': 'generic', 'default': {'segments_min': 1, 'price_step': None, 'min_segment_fraction': 0}}
    cfg.mode['coal_first_segment'] = None
    cfg.base['solver'].update(time_limit_s=5, mip_rel_gap=0.0)
    a = copy.deepcopy(cfg.units['thermal'][0])
    a.update(id='CHEAP', bus=1, pmin=0, pmax=200, ramp=10000, noload=0,
             init=dict(online=True, p0=0, hours=48))
    b = copy.deepcopy(a)
    b.update(id='LOCAL', bus=2)
    cfg.units = dict(thermal=[a, b], renewable=[], hydro=[], storage=[], pumped=[], tie=[])
    bids = {'rule_set': cfg.rule_set, 'date': cfg.base['date'], 'units': {g: {'segments': [dict(p_start=0, p_end=200, price=c)],
                         'startup_cost': dict(hot=0, warm=0, cold=0), 'noload_cost': 0}
                      for g, c in [('CHEAP', 10), ('LOCAL', local_price)]}}
    net = Network([1, 2], pd.DataFrame([dict(branch_id='L', rate_mw=50)]),
                  np.array([[0., -1.]]), pd.Series([0., 1.], index=[1, 2]), 100, ref_bus=1)
    return DACase(cfg, net, pd.DataFrame(), bids, {2: np.array([100.])}, {}, {},
                  np.array([reserve]), np.array([0.]), {})


def test_standard_sced_keeps_slack_dispatch_and_its_own_raw_prices(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Extra repricing/anchor relaxation is forbidden")

    monkeypatch.setattr(scuc, 'set_penalties', forbidden)
    monkeypatch.setattr(scuc, 'relax_anchors', forbidden)
    solve = scuc.solve_sced
    calls = []

    def counted(m):
        calls.append(m)
        return solve(m)

    monkeypatch.setattr(scuc, 'solve_sced', counted)
    r = run_da(tiny_case(), verbose=False)
    assert calls == [r.model]
    assert r.pricing_model is None and r.pricing_info is None
    assert r.tables['units'].set_index('unit').P_mw.to_dict() == {'CHEAP': 50., 'LOCAL': 50.}
    assert pyo.value(r.model.flow['L', 0]) == pytest.approx(50)
    assert r.slack['line_slack_max'] == pytest.approx(0)
    assert r.slack['reserve_up_short_max'] > 0
    assert r.tables['lines'].flow_mw.iloc[0] == pytest.approx(50)
    assert pyo.value(r.model.obj) == pytest.approx(r.sced_info['objective'])
    assert r.costs == __import__('smm.results', fromlist=['cost_breakdown']).cost_breakdown(r.model)
    sn = snapshot(r.model)
    settled = settlement.settle(r, SimpleNamespace(rt=[dict(snap=sn, prices=r.prices)]))
    cheap = settled['detail'].query("entity == 'CHEAP'").iloc[0]
    assert cheap.q_da == pytest.approx(50)
    assert cheap.q_rt == pytest.approx(50)
    assert cheap.da_amount == pytest.approx(50 * r.prices['lmp_clamped'][0, 0] * .25)
    assert settled['reconciliation'].residual.abs().max() < 1e-6
    assert not any(v.fixed for v in r.model.P.values())
    assert r.prices['lmp'].max() > 800  # physical shortage penalties stay in raw duals
    assert r.prices['lmp_clamped'].max() <= 800  # explicit settlement clipping only
    np.testing.assert_allclose(r.prices['lmp'], scuc.extract_prices(r.model)['lmp'])
    perturbed = r.model.clone()
    con = perturbed.c_inj[2, 0]
    con.set_value(con.body == pyo.value(con.upper) + .01)
    price_up = solve(perturbed)['objective']
    assert (price_up - r.sced_info['objective']) / .01 / .25 == pytest.approx(r.prices['lmp'][1, 0])
    assert set(r.tables['lines'].dual_source) == {'standard_sced'}


def test_normal_sced_continuous_dispatch_not_locked_and_price_is_marginal():
    r = run_da(tiny_case(reserve=0), verbose=False)
    assert r.pricing_model is None
    assert r.pricing_info is None
    assert r.stage_comparison['dispatch_source'] == 'fixed_integer_sced'
    assert r.stage_comparison['sced_minus_scuc_objective'] <= 1e-6
    assert not any(v.fixed for v in r.model.P.values())
    np.testing.assert_allclose(r.prices['lmp'][:, 0], [10, 700], atol=1e-6)
    # Finite difference at the demand bus with the same fixed integers.
    m = r.model.clone()
    con = m.c_inj[2, 0]
    con.set_value(con.body == pyo.value(con.upper) + .01)
    changed = scuc.solve_sced(m)
    assert (changed['objective'] - r.sced_info['objective']) / .01 / .25 == pytest.approx(700)


def test_nearoptimal_incumbent_metadata_does_not_require_equal_continuous_dispatch(monkeypatch):
    original = scuc.solve_scuc

    def feasible_incumbent(m, log=lambda *a: None):
        info = original(m, log)
        # Same fixed commitment, but a feasible non-optimal continuous incumbent:
        # send all load from LOCAL, instead of the optimal 50/50 split.
        for g, value in [('CHEAP', 0), ('LOCAL', 100)]:
            m.P[g, 0].set_value(value)
            m.p[g, 0, 0].set_value(value)
        m.inj[1, 0].set_value(0)
        m.inj[2, 0].set_value(0)
        # flow is an Expression, not an independently mutable variable.
        info.update(objective=pyo.value(m.obj), termination='maxTimeLimit', gap=.5)
        return info

    monkeypatch.setattr(scuc, 'solve_scuc', feasible_incumbent)
    r = run_da(tiny_case(reserve=0), verbose=False)
    assert r.scuc_snapshot['P']['CHEAP', 0] == 0
    assert r.scuc_info['termination'] == 'maxTimeLimit'
    assert r.scuc_info['gap'] == .5
    assert pyo.value(r.model.P['CHEAP', 0]) == pytest.approx(50)
    assert r.stage_comparison['physical_max_delta_mw'] == pytest.approx(50)
    assert r.stage_comparison['sced_minus_scuc_objective'] == pytest.approx(-8625)
    assert 'degeneracy' in r.stage_comparison['interpretation']


def test_equal_cost_degeneracy_keeps_continuous_variables_free():
    r = run_da(tiny_case(reserve=0, local_price=10), verbose=False)
    assert r.stage_comparison['integer_mismatch'] == 0
    assert r.stage_comparison['objective_nonincrease']
    assert r.stage_comparison['sced_minus_scuc_objective'] == pytest.approx(0)
    assert not any(v.fixed for v in r.model.P.values())
    assert sum(r.tables['units'].P_mw) == pytest.approx(100)
    np.testing.assert_allclose(r.prices['lmp'][:, 0], [10, 10], atol=1e-6)
    # There can be multiple optimal continuous allocations; compare feasibility,
    # objective and marginal values, not equality to one arbitrary MIP allocation.


@pytest.mark.parametrize('anchor', [False, True])
def test_rolling_uses_one_unchanged_sced_even_with_slack(monkeypatch, anchor):
    from smm.rolling import clear_window
    from smm.bids import default_bids
    c = tiny_case()
    if anchor:
        original = load_config()
        storage = copy.deepcopy(original.units['storage'][0])
        storage['bus'] = 2  # The synthetic network has buses 1/2, not IEEE39 bus19.
        c.cfg.units['storage'] = [storage]
        c.bids['units'][storage['id']] = default_bids(original)['units'][storage['id']]
        c.terminal = {'storage_anchor': {storage['id']: [(0, 10000., 10000.)]},
                      'anchor_penalty': 1e5}

    def forbidden(*args, **kwargs):
        raise AssertionError("Extra repricing/anchor relaxation is forbidden")

    monkeypatch.setattr(scuc, 'set_penalties', forbidden)
    monkeypatch.setattr(scuc, 'relax_anchors', forbidden)
    solve = scuc.solve_sced
    calls = []

    def counted(m):
        calls.append(m)
        return solve(m)

    monkeypatch.setattr(scuc, 'solve_sced', counted)
    r = clear_window(c)
    assert calls == [r['model']]
    assert r['repriced'] is False and r['anchor_relaxed'] == 0
    assert r['pricing_source'] == 'standard_sced'
    assert r['anchor_slack_present'] == anchor
    if anchor:
        assert r['slack']['anchor_slack_max_mwh'] > 0
    np.testing.assert_allclose(r['prices']['lmp'], scuc.extract_prices(r['model'])['lmp'])
    assert r['snap']['P']['CHEAP'][0] == pytest.approx(pyo.value(r['model'].P['CHEAP', 0]))
    assert not any(v.fixed for v in r['model'].P.values())


@pytest.mark.parametrize("force_slack", [False, True])
def test_typed_soft_anchors_have_exact_reported_deviations(force_slack):
    """Deterministic feasible and forced-soft cases, E and Vu units checked separately."""
    from smm.rolling import clear_window
    from smm.bids import default_bids
    original = load_config()
    c = tiny_case(reserve=0)
    storage = copy.deepcopy(original.units['storage'][0]); storage['bus'] = 2
    pumped = copy.deepcopy(original.units['pumped'][0]); pumped['bus'] = 2
    c.cfg.units['storage'] = [storage]; c.cfg.units['pumped'] = [pumped]
    generated = default_bids(original)
    for unit in (storage, pumped):
        c.bids['units'][unit['id']] = copy.deepcopy(generated['units'][unit['id']])
    e = 10000. if force_slack else storage['soc0'] * storage['ecap']
    v = 10000. if force_slack else pumped['vup0']
    c.terminal = {'storage_anchor': {storage['id']: [(0, e, e)]},
                  'pumped_anchor': {pumped['id']: [(0, v, v)]}, 'anchor_penalty': 1e5}
    r = clear_window(c)
    assert r['repriced'] is False and r['anchor_relaxed'] == 0
    for kind, key in [('E', 'storage_anchor_slack_max_mwh'), ('Vu', 'pumped_anchor_slack_max_1e4m3')]:
        anchors = [a for a in r['snap']['anchors'] if a['kind'] == kind]
        assert len(anchors) == 1
        a = anchors[0]
        up = max(a['value']-a['upper'], 0.)
        dn = max(a['lower']-a['value'], 0.)
        assert a['slack_up'] == pytest.approx(up, rel=0, abs=1e-5)
        assert a['slack_dn'] == pytest.approx(dn, rel=0, abs=1e-5)
        assert r['slack'][key] == pytest.approx(up+dn, rel=0, abs=1e-5)
        if force_slack:
            assert up+dn > 1
        else:
            assert up+dn <= 1e-5
            assert a['value'] == pytest.approx(a['lower'], rel=0, abs=1e-5)
    assert r['slack']['anchor_slack_max_mwh'] == pytest.approx(
        max(r['slack']['storage_anchor_slack_max_mwh'], r['slack']['pumped_anchor_slack_max_1e4m3']), rel=0, abs=1e-5)
