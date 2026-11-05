"""Fast LP regressions for signed continuation values and terminal-target prices.

All models use 15-minute periods; the ordinary horizon is 8 periods (2 hours).
These are exact LP sensitivity tests, not a substitute for full IEEE39 replay.
"""
from types import SimpleNamespace

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import rolling, scuc


ASSETS = {"E": ("ES1", "S", "c_soc", "c_eend", 1.),
          "Vu": ("PS1", "PS", "c_pres", "c_pvend", 9.6),
          "V": ("H1", "HS", "c_water", "c_vend", 1. / (.36 * 1.2))}


def stock_lp(kind, *, T=8, generation_bid=0., thermal_cost=100., target=2.,
             terminal="direct", salvage=0.):
    unit, group, balance_name, end_name, energy_per_native = ASSETS[kind]
    m = pyo.ConcreteModel()
    for name in ("S", "PS", "HS"):
        setattr(m, name, pyo.Set(initialize=[unit] if name == group else []))
    m.T = pyo.RangeSet(0, T - 1)
    dt = .25
    m.g = pyo.Var(m.T, bounds=(0., 40. * energy_per_native))
    m.h = pyo.Var(m.T, bounds=(0., 40. * energy_per_native))
    for k, (_, grp, con, _, _) in ASSETS.items():
        setattr(m, k, pyo.Var(getattr(m, grp), m.T, bounds=(0., 20.)))
        if k == kind:
            X = getattr(m, k)
            setattr(m, con, pyo.Constraint(getattr(m, grp), m.T, rule=lambda m, u, t:
                X[u, t] == (10. if t == 0 else X[u, t - 1]) - m.h[t] * dt / energy_per_native))
        else:
            setattr(m, con, pyo.Constraint(getattr(m, grp), m.T))
    X = getattr(m, kind)
    m.c_balance = pyo.Constraint(m.T, rule=lambda m, t: m.g[t] + m.h[t] == 40. * energy_per_native)
    if terminal == "direct":
        setattr(m, end_name, pyo.Constraint(getattr(m, group), rule=lambda m, u:
            X[u, T - 1] >= target if kind == "V" else X[u, T - 1] == target))
    elif terminal == "band":
        hi = 20. if kind == "V" else target
        m._state_bands = [(kind, unit, T - 1, target, hi, "day_end")]
        m.B = pyo.Set(initialize=[0])
        m.c_band_lo = pyo.Constraint(m.B, rule=lambda m, i: X[unit, T - 1] >= target)
        m.c_band_hi = pyo.Constraint(m.B, rule=lambda m, i: X[unit, T - 1] <= hi)
    m._md = SimpleNamespace(T=T, dt=dt, hydro={"H1": {"water_rate": 1.2}}, pumped={"PS1": {"e_gen": 9.6}})
    m._case = SimpleNamespace(cfg=SimpleNamespace(base={"solver": {"threads": 4, "time_limit_s": 10., "log": False}}))
    m.obj = pyo.Objective(expr=sum((thermal_cost*m.g[t] + generation_bid*m.h[t]) * dt for t in m.T)
                          - salvage*X[unit, T - 1])
    scuc.solve_sced(m)
    return m


@pytest.mark.parametrize("kind,generation_bid", [("E", 0.), ("Vu", 0.), ("V", 0.), ("E", 420.), ("Vu", 420.)])
def test_every_nonterminal_value_matches_two_sided_finite_difference(kind, generation_bid):
    m = stock_lp(kind, generation_bid=generation_bid)
    unit, _, balance_name, _, energy = ASSETS[kind]
    rec = scuc.state_values(m)[kind][unit]
    base_obj = pyo.value(m.obj)
    expected = (100. - generation_bid) * energy
    np.testing.assert_allclose(rec["value_native"][:-1], expected, atol=1e-6)
    np.testing.assert_allclose(rec["value_yuan_per_mwh"][:-1], 100. - generation_bid, atol=1e-6)
    assert rec["value_native"][-1] == rec["value_yuan_per_mwh"][-1] == 0.
    for t in range(m._md.T - 1):
        c = getattr(m, balance_name)[unit, t + 1]
        expr = c.expr
        lhs, rhs = expr.args
        for eps in (-.01, .01):
            c.set_value(lhs == rhs + eps)
            scuc.solve_sced(m)
            assert -(pyo.value(m.obj) - base_obj) / eps == pytest.approx(rec["value_native"][t], abs=1e-5)
        c.set_value(expr)
    np.testing.assert_allclose(rec["value_native"][:-1], -rec["dual_raw"][1:])


@pytest.mark.parametrize("kind", list(ASSETS))
@pytest.mark.parametrize("terminal", ["direct", "band"])
def test_terminal_target_shadow_price_is_separate_and_matches_fd(kind, terminal):
    m = stock_lp(kind, terminal=terminal)
    unit, _, _, _, energy = ASSETS[kind]
    rec = scuc.state_values(m)[kind][unit]
    diag = rec["terminal_target_dual"]
    assert diag["available"] and diag["constraint"]
    assert diag["value_native"] == pytest.approx(100. * energy)
    assert diag["value_yuan_per_mwh"] == pytest.approx(100.)
    assert rec["value_native"][-1] == 0.
    for eps in (-.001, .001):
        changed = stock_lp(kind, terminal=terminal, target=2. + eps)
        assert (pyo.value(changed.obj) - pyo.value(m.obj)) / eps == pytest.approx(diag["value_native"], abs=1e-5)


@pytest.mark.parametrize("kind", ["E", "Vu"])
@pytest.mark.parametrize("terminal", ["direct", "band"])
def test_negative_terminal_target_shadow_price_is_not_clipped(kind, terminal):
    m = stock_lp(kind, generation_bid=420., terminal=terminal)
    rec = scuc.state_values(m)[kind][ASSETS[kind][0]]
    assert rec["terminal_target_dual"]["value_yuan_per_mwh"] == pytest.approx(-320.)
    assert rec["value_native"][-1] == 0.


@pytest.mark.parametrize("kind", list(ASSETS))
def test_single_period_has_no_continuation_value(kind):
    rec = scuc.state_values(stock_lp(kind, T=1))[kind][ASSETS[kind][0]]
    assert rec["value_native"].tolist() == rec["value_yuan_per_mwh"].tolist() == [0.]
    assert rec["terminal_target_dual"]["available"]


def test_missing_terminal_target_is_unavailable_not_zero():
    rec = scuc.state_values(stock_lp("E", terminal="none"))["E"]["ES1"]
    diag = rec["terminal_target_dual"]
    assert diag["available"] is False
    assert diag["constraint"] is None and diag["value_native"] is None and diag["value_yuan_per_mwh"] is None
    assert rec["value_native"][-1] == 0.


def test_missing_terminal_dual_is_unavailable_not_zero():
    m = stock_lp("V")
    del m.dual[m.c_vend["H1"]]
    diag = scuc.state_values(m)["V"]["H1"]["terminal_target_dual"]
    assert not diag["available"] and diag["constraint"] == "c_vend[H1]" and diag["value_native"] is None


def test_zero_target_shadow_price_is_available():
    m = stock_lp("V", target=0., thermal_cost=0.)
    diag = scuc.state_values(m)["V"]["H1"]["terminal_target_dual"]
    assert diag["available"] and diag["value_native"] == 0.


def test_terminal_water_price_is_not_an_unrequested_cross_day_salvage_value():
    # Actual production extraction and window-term functions are used. A DA
    # target shadow price must not reward unused water after the day is over.
    da = stock_lp("V", T=1)
    values = scuc.state_values(da)
    energy = ASSETS["V"][-1]
    cfg = SimpleNamespace(T=1, dt=.25, base={"season": "wet"},
        mode={"realtime": {"value_method": {}}},
        units={"hydro": [{"id": "H1", "water_rate": 1.2, "zones": [[0., 100.]],
                          "seasons": {"wet": {"vmin": 0., "vmax": 20., "v_end": 2., "qin": 0., "qeco": 0.}}}]})
    plan = {"state_values": values, "Vh": {"H1": [2.]}, "delta": {}, "Qout": {}}
    terms = rolling.value_method_terms(cfg, plan, 0, 1, storage=False, pumped=False, hydro=True)
    v = terms["state_value"][0][3] if terms["state_value"] else 0.
    assert v == 0.
    assert terms["state_band"][0][3:5] == (2., 20.)
    rt = stock_lp("V", T=1, thermal_cost=50., salvage=v)
    legacy = stock_lp("V", T=1, thermal_cost=50., salvage=values["V"]["H1"]["terminal_target_dual"]["value_native"])
    assert pyo.value(rt.V["H1", 0]) == pytest.approx(2.)
    assert pyo.value(legacy.V["H1", 0]) == pytest.approx(10.)
    assert 50.*pyo.value(rt.g[0])*.25 == pytest.approx(100.*energy)
    assert 50.*pyo.value(legacy.g[0])*.25 == pytest.approx(500.*energy)
