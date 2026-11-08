"""状态价值符号回归（价值曲线全负问题的核对）：

1. 有限差分：在日前最终 SCED 的动态约束 X[t+1] == X[t] + 流量 右端加 ε（= 时段 t 末多 ε 存量），
   v[t] 必须落在 −ΔObj/ε 的左右导数区间内（ES/PS/H 各一）。正 v = 多存 1 单位使报价目标下降。
2. 方向：同一 RT 可行集仅改变一个资产的窗口末价值，比较负、零、正价值的库存响应。
   原生价值按能量单位换算；不把不同预测/整数约束下的日前库存当作零价值反事实。
"""
import copy
from dataclasses import replace

import numpy as np
import pyomo.environ as pyo
import pytest
from pyomo.repn import generate_standard_repn

from smm import rolling, scuc


def _value_da(da_result):
    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    mode["realtime"]["storage"]["mode"] = "value"
    mode["realtime"]["hydro"] = "value"
    mode["auxiliary_pricing"]["enabled"] = False
    return replace(da_result, case=replace(da_result.case, cfg=replace(cfg, mode=mode)))


def _own_bid_terms(m):
    """储能/抽蓄自身报价项（充电/抽水买价记负成本），用于把报价目标拆成“系统其余成本 + 自身报价”。"""
    md, dt, V = m._md, m._md.dt, pyo.value
    st = sum(md.storage[s]["cd"][k] * V(m.pd[s, k, t]) * dt for (s, k) in m.SD for t in m.T) \
        - sum(md.storage[s]["cc"][k] * V(m.pc[s, k, t]) * dt for (s, k) in m.SC for t in m.T)
    ps = sum(md.pumped[p]["cg"][k] * V(m.pg[p, k, t]) * dt for (p, k) in m.PGK for t in m.T) \
        - sum(md.pumped[p]["cp"][k] * V(m.pp[p, k, t]) * dt for (p, k) in m.PPK for t in m.T) \
        + sum(md.pumped[p]["start_cost_gen"] * V(m.sg[p, t]) + md.pumped[p]["start_cost_pump"] * V(m.sp[p, t]) for p in m.PS for t in m.T)
    return st + ps


@pytest.mark.parametrize("kind,con_name,units_attr,eps", [("E", "c_soc", "S", .5), ("Vu", "c_pres", "PS", .05), ("V", "c_water", "HS", .05)])
def test_state_value_sign_fd_all_kinds(da_result, kind, con_name, units_attr, eps):
    m = da_result.model.clone()
    scuc.solve_sced(m)
    obj0 = pyo.value(m.obj)
    units = list(getattr(m, units_attr))
    if not units:
        pytest.skip(f"no {kind} units")
    u, t = units[0], 40
    v = float(scuc.state_values(m)[kind][u]["value_native"][t])
    con = getattr(m, con_name)[u, t + 1]
    base = con.expr
    lhs, rhs = base.args          # 约束写法：X[t+1] == X[t] + 流量×dt（左边是 X[t+1]）
    assert lhs is getattr(m, kind)[u, t + 1]

    def shifted(e):
        con.set_value(lhs == rhs + e)
        try:
            scuc.solve_sced(m)
            return pyo.value(m.obj)
        finally:
            con.set_value(base)

    try:
        right = -(shifted(eps) - obj0) / eps
        left = -(obj0 - shifted(-eps)) / eps
    except RuntimeError:
        pytest.skip("固定整数后该存量无法调整（机组停机），有限差分不可行")
    lo, hi = min(left, right), max(left, right)
    tol = max(1.0, .02 * abs(v))
    assert lo - tol <= v <= hi + tol, (kind, u, left, right, v)


def test_value_sign_decomposition_bid_vs_system(da_result):
    """总报价目标分解成立；各分量沿联合最优解变化，不各自满足单调性。
    硬日末库存、固定工况和系统余电成本也可产生负值，不能断言只来自自身报价。
    主对偶的双侧有限差分由 test_state_value_sign_fd_all_kinds 单独核验。
    """
    m = da_result.model.clone()
    scuc.solve_sced(m)
    s, t, eps = list(m.S)[0], 40, .5
    obj0, own0 = pyo.value(m.obj), _own_bid_terms(m)
    v = float(scuc.state_values(m)["E"][s]["value_native"][t])
    con = m.c_soc[s, t + 1]
    base = con.expr
    lhs, rhs = base.args
    con.set_value(lhs == rhs + eps)
    scuc.solve_sced(m)
    obj1, own1 = pyo.value(m.obj), _own_bid_terms(m)
    con.set_value(base)
    scuc.solve_sced(m)
    v_total = -(obj1 - obj0) / eps
    v_sys = -((obj1 - own1) - (obj0 - own0)) / eps
    v_own = -(own1 - own0) / eps
    assert v_total == pytest.approx(v_sys + v_own, abs=1e-6)
    assert np.isfinite([v, v_total, v_sys, v_own]).all()


def _unsolved_window_signature(m):
    """Capture the feasible set before SCUC fixes any selected integer decisions."""
    V = pyo.value
    variables = tuple((v.name, str(v.domain), V(v.lb) if v.has_lb() else None,
                       V(v.ub) if v.has_ub() else None, V(v) if v.fixed else None)
                      for v in m.component_data_objects(pyo.Var, active=True))
    constraints = tuple((c.name, str(c.expr))
                        for c in m.component_data_objects(pyo.Constraint, active=True))
    repn = generate_standard_repn(m.obj.expr)
    assert repn.is_linear()
    coefficients = {v.name: float(V(c)) for v, c in zip(repn.linear_vars, repn.linear_coefs)}
    return (variables, constraints), coefficients, float(V(repn.constant))


def _assert_window_feasible(m, tolerance=1e-6):
    """Check every active row and variable bound, not just the target stock."""
    V = pyo.value
    for v in m.component_data_objects(pyo.Var, active=True):
        x = float(V(v))
        assert np.isfinite(x), v.name
        assert not v.has_lb() or x >= V(v.lb) - tolerance, (v.name, x, v.lb)
        assert not v.has_ub() or x <= V(v.ub) + tolerance, (v.name, x, v.ub)
    for c in m.component_data_objects(pyo.Constraint, active=True):
        x = float(V(c.body))
        assert np.isfinite(x), c.name
        assert not c.has_lb() or x >= V(c.lower) - tolerance, (c.name, x, c.lower)
        assert not c.has_ub() or x <= V(c.upper) + tolerance, (c.name, x, c.upper)
    assert not m._state_band_soft_indices
    for kind, unit, rel, lo, hi, _ in m._state_bands:
        x = V(getattr(m, kind)[unit, rel])
        assert lo - tolerance <= x <= hi + tolerance, (kind, unit, x, lo, hi)
    assert all(abs(value) <= tolerance for value in scuc.slack_summary(m).values())


@pytest.mark.parametrize("kind,unit", [("E", "ES1"), ("E", "ES2"), ("E", "ES3"), ("Vu", "PS1")])
def test_rt_window_end_direction_with_extreme_value(da_result, monkeypatch, kind, unit):
    """Single-asset comparative statics on an identical, strictly solved RT case.

    A negative native value of 2000 yuan/10^4 m3 is only 208.33 yuan/MWh
    for PS1, so it need not outweigh its pumping bid. DA is also a different
    feasible set. Neither a finite negative value nor jointly changing all
    resources implies that each RT stock must be below its DA reference.
    """
    da2 = _value_da(da_result)
    base = copy.deepcopy(da2.case.cfg.base)
    base["solver"].update(mip_rel_gap=1e-8, warm_start=False)
    cfg = replace(da2.case.cfg, base=base)
    da2 = replace(da2, case=replace(da2.case, cfg=cfg))
    plan = rolling._da_plan_arrays(da2)
    t, t1 = 0, 8
    rel = t1 - 1 - t
    terms = rolling.value_method_terms(cfg, plan, t, t1)
    target = (kind, unit, rel)
    assert sum((k, u, tau) == target for k, u, tau, _ in terms["state_value"]) == 1
    band = next(row for row in terms["state_band"] if tuple(row[:3]) == target)
    assert band[4] > band[3]
    # E is already MWh; one native Vu unit stores e_gen MWh of generation.
    energy_per_native = (1. if kind == "E" else
                         float(next(d for d in cfg.units["pumped"] if d["id"] == unit)["e_gen"]))
    magnitude_native = 2000. * energy_per_native
    case = rolling.make_window_case(da2.case, cfg, t, t1, t, True,
                                    rolling.initial_state(cfg), {}, copy.deepcopy(terms), "RT")
    signatures, coefficients, constants, stocks = {}, {}, {}, {}
    original_build = scuc.build_model
    target_name = f"{kind}[{unit},{rel}]"
    for sign in (-1, 0, 1):
        terminal = copy.deepcopy(terms)
        terminal["state_value"] = [(k, u, tau, sign * magnitude_native if (k, u, tau) == target else v)
                                   for k, u, tau, v in terms["state_value"]]
        observed = []

        def inspect_build(window_case):
            model = original_build(window_case)
            observed.append(_unsolved_window_signature(model))
            return model

        with monkeypatch.context() as patch:
            patch.setattr(scuc, "build_model", inspect_build)
            result = rolling.clear_window(replace(case, terminal=terminal))
        assert len(observed) == 1
        signatures[sign], coefficients[sign], constants[sign] = observed[0]
        mip, lp = result["mip"], result["sced"]
        assert mip["termination"].lower() == "optimal", mip
        assert np.isfinite([mip["objective"], mip["best_bound"], mip["gap"]]).all(), mip
        assert abs(mip["gap"]) <= 1e-7, mip
        assert lp["termination"].lower() == "optimal", lp
        _assert_window_feasible(result["model"])
        stocks[sign] = pyo.value(getattr(result["model"], kind)[unit, rel])

    assert signatures[-1] == signatures[0] == signatures[1]
    assert constants[-1] == constants[0] == constants[1]
    names = set().union(*(row.keys() for row in coefficients.values()))
    names.add(target_name)  # Missing terms must fail even when absent from every map.
    for sign in (-1, 0, 1):
        for name in names:
            expected = -sign * magnitude_native if name == target_name else coefficients[0].get(name, 0.)
            assert coefficients[sign].get(name, 0.) == pytest.approx(expected, abs=1e-9, rel=0.), (sign, name)
    assert stocks[-1] <= stocks[0] + 1e-6, stocks
    assert stocks[0] <= stocks[1] + 1e-6, stocks
    # A reversed or missing value term must not pass through flat/degenerate
    # dispatch: require at least 1 MWh of actual response, far above residuals.
    assert (stocks[1] - stocks[-1]) * energy_per_native > 1., stocks
