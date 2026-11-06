"""第二轮（价值法实时）回归：对偶符号有限差分、价值项只在窗口末、回退不中断、
备用用时段末库存、辅助算价保留价值项、水电日末约束、负电价下限 -50。"""
import copy
from dataclasses import replace

import numpy as np
import pyomo.environ as pyo
import pytest
from pyomo.repn import generate_standard_repn

from smm import rolling, scuc


def _value_da(da_result, aux=False, **realtime):
    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    mode["realtime"]["storage"]["mode"] = "value"
    mode["realtime"]["hydro"] = "value"
    mode["realtime"].update(realtime)
    mode["auxiliary_pricing"]["enabled"] = aux
    cfg2 = replace(cfg, mode=mode)
    return replace(da_result, case=replace(da_result.case, cfg=cfg2))


def _objective_vars(m):
    repn = generate_standard_repn(m.obj.expr, quadratic=False)
    return {id(v): c for v, c in zip(repn.linear_vars, repn.linear_coefs)}


def test_state_value_dual_sign_matches_finite_difference(da_result):
    """v[t] = −dual(c_soc[t+1]) = dObj/d(期末多 1 MWh)取负；有限差分：在 t 段末免费注入 ε。"""
    m = da_result.model.clone()
    md, dt = m._md, m._md.dt
    scuc.solve_sced(m)
    obj0 = pyo.value(m.obj)
    s = list(m.S)[0]
    t = 40
    vals = scuc.state_values(m)["E"][s]["value_native"]
    d = md.storage[s]
    base = m.c_soc[s, t + 1].expr

    def shifted(eps):
        m.c_soc[s, t + 1].set_value(m.E[s, t + 1] == m.E[s, t] + eps + (d["eta_c"] * m.Pc[s, t + 1] - m.Pd[s, t + 1] / d["eta_d"]) * dt)
        scuc.solve_sced(m)
        return pyo.value(m.obj)

    eps = 0.05
    right = -(shifted(eps) - obj0) / eps     # 价值 = −dObj/dε（库存每多 1 MWh 节省的目标）
    left = -(obj0 - shifted(-eps)) / eps
    m.c_soc[s, t + 1].set_value(base)
    # LP 对偶在退化点取左右导数之间的任一次梯度；符号与区间一致即可
    lo, hi = min(left, right), max(left, right)
    tol = max(1.0, 0.02 * abs(vals[t]))
    assert lo - tol <= vals[t] <= hi + tol, (left, right, vals[t])
    assert np.sign(vals[t]) == np.sign(left) or abs(vals[t]) < tol


def test_value_term_only_at_window_end(da_result):
    da2 = _value_da(da_result)
    cfg = da2.case.cfg
    plan = rolling._da_plan_arrays(da2)
    t, t1 = 10, 18
    terms = rolling.value_method_terms(cfg, plan, t, t1)
    assert terms["state_value"] and {x[2] for x in terms["state_value"]} == {t1 - 1 - t}
    # Model-only diagnostic assumes the identified DA prefix was followed;
    # replay its per-unit history instead of relabelling the midnight state.
    state = rolling.state_from_plan(cfg, plan, t)
    assert state.t == t
    case = rolling.make_window_case(da2.case, cfg, t, t1, t, True, state, {}, dict(terms), "RT")
    m = scuc.build_model(case)
    coefs = _objective_vars(m)
    for s in m.S:
        for k in m.T:
            c = coefs.get(id(m.E[s, k]), 0.)
            if k == t1 - 1 - t:
                v = next(x[3] for x in terms["state_value"] if x[0] == "E" and x[1] == s)
                assert c == pytest.approx(-v)
            else:
                assert c == 0.


def test_fallback_softens_and_does_not_stop(da_result, monkeypatch):
    da2 = _value_da(da_result)
    original = rolling.clear_window
    calls = []

    def flaky(case, **kwargs):
        calls.append(bool((case.terminal or {}).get("state_band_soft")))
        if case.stage == "RT" and not (case.terminal or {}).get("state_band_soft"):
            raise scuc.InfeasibleModelError("forced infeasible hard band", phase="scuc", solver_status="infeasible")
        return original(case, **kwargs)

    monkeypatch.setattr(rolling, "clear_window", flaky)
    rr = rolling.run_rolling(da2, periods=2, log=lambda *a: None)
    assert len(rr.rt) == 2
    assert all(r["fallback"] and "forced infeasible" in r["fallback"]["reason"] for r in rr.rt)
    assert calls.count(True) == 2


def test_reserve_energy_uses_period_end_stock(da_result):
    m = da_result.model
    if not hasattr(m, "c_rsup_soc"):
        pytest.skip("storage reserve disabled in fixture")
    s, t = list(m.S)[0], 5
    ids = {id(v) for v in generate_standard_repn(m.c_rsup_soc[s, t].body).linear_vars}
    assert id(m.E[s, t]) in ids and id(m.E[s, t - 1]) not in ids


def test_reserve_energy_end_stock_in_model_build(da_result):
    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    mode["reserve"]["storage_reserve"] = True
    case = replace(da_result.case, cfg=replace(cfg, mode=mode))
    m = scuc.build_model(case)
    s, t = list(m.S)[0], 5
    ids = {id(v) for v in generate_standard_repn(m.c_rsup_soc[s, t].body).linear_vars}
    assert id(m.E[s, t]) in ids and id(m.E[s, t - 1]) not in ids
    p = list(m.PS)[0]
    ids = {id(v) for v in generate_standard_repn(m.c_rpup_volume[p, t].body).linear_vars}
    assert id(m.Vu[p, t]) in ids and id(m.Vu[p, t - 1]) not in ids


def test_auxiliary_model_keeps_value_terms_and_bands(da_result):
    da2 = _value_da(da_result, aux=True)
    cfg = da2.case.cfg
    plan = rolling._da_plan_arrays(da2)
    t, t1 = 20, 28
    terms = rolling.value_method_terms(cfg, plan, t, t1)
    state = rolling.initial_state(cfg)
    state = replace(state, t=0) if hasattr(state, "t") else state
    case = rolling.make_window_case(da2.case, cfg, 0, 8, 0, True, state, {}, dict(rolling.value_method_terms(cfg, plan, 0, 8)), "RT")
    r = rolling.clear_window(case)
    aux = r["pricing_model"]
    assert aux is not None and aux._state_values and aux._state_bands
    assert aux.c_band_hi.active and aux.c_band_lo.active
    coefs = _objective_vars(aux)
    s = list(aux.S)[0]
    assert coefs.get(id(aux.E[s, 7]), 0.) != 0. or next(x[3] for x in aux._state_values if x[1] == s) == 0.
    assert r["snap"]["value_window"]
    assert terms["state_band"]


def test_hydro_and_storage_day_end_constraints(da_result):
    da2 = _value_da(da_result)
    cfg = da2.case.cfg
    plan = rolling._da_plan_arrays(da2)
    T = cfg.T
    terms = rolling.value_method_terms(cfg, plan, T - 8, T)
    bands = {(k, u): (lo, hi, label) for k, u, _, lo, hi, label in terms["state_band"]}
    for h in cfg.units["hydro"]:
        if ("V", h["id"]) in bands:
            vmin, vmax, vend, _ = rolling._hydro_bounds(cfg, h)
            lo, hi, label = bands[("V", h["id"])]
            assert label == "day_end" and lo == pytest.approx(min(max(vend, vmin), vmax)) and hi == pytest.approx(vmax)
    for d in cfg.units["storage"]:
        lo, hi, label = bands[("E", d["id"])]
        assert label == "day_end" and lo == pytest.approx(hi) == pytest.approx(plan["E"][d["id"]][T - 1])
    # 非日末窗口：偏差带不超过 δ0
    terms = rolling.value_method_terms(cfg, plan, 30, 38)
    for k, u, rel, lo, hi, label in terms["state_band"]:
        assert label == "band" and lo <= hi + 1e-9
        if k == "E":
            d = next(x for x in cfg.units["storage"] if x["id"] == u)
            assert hi - lo <= 2 * 0.02 * d["ecap"] + 1e-6


def test_negative_price_floor_is_minus_50_everywhere():
    from smm.config import load_config
    from smm.results import clamp_prices
    cfg = load_config()
    lim = cfg.mode["price_limits"]
    assert lim["bid_min"] == lim["clear_min"] == -50
    pr = clamp_prices(dict(lmp=np.array([[-80., -30., 900.]])), lim["clear_min"], lim["clear_max"])
    assert pr["lmp_clamped"].tolist() == [[-50., -30., 800.]]
    from smm.bids import default_bids, validate_bids
    uid = cfg.units["renewable"][0]["id"]
    for price, ok in ((-50.0, True), (-50.01, False)):
        b = copy.deepcopy(default_bids(cfg))
        b["units"][uid]["segments"][0]["price"] = price
        bad = [i for i in validate_bids(cfg, b) if i.level == "error" and i.unit_id == uid]
        assert (not bad) == ok, bad
