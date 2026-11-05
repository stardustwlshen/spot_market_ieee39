"""状态价值符号回归（价值曲线全负问题的核对）：

1. 有限差分：在日前最终 SCED 的动态约束 X[t+1] == X[t] + 流量 右端加 ε（= 时段 t 末多 ε 存量），
   v[t] 必须落在 −ΔObj/ε 的左右导数区间内（ES/PS/H 各一）。正 v = 多存 1 单位使报价目标下降。
2. 方向：RT 窗口末目标项为 −v·X_τ，v 取很大正值时 X_τ 不低于日前值，取很大负值时不高于日前值。
"""
import copy
from dataclasses import replace

import numpy as np
import pyomo.environ as pyo
import pytest

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
    """报价目标 = 系统其余成本 + 储能/抽蓄自身报价项；v 是前者合计的 −导数。
    多 1 MWh SOC 时系统其余成本不增加（可少买电或多放电），v 为负只能来自自身充电买价盈余被挤掉。"""
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
    assert v_sys >= -1e-6, v_sys
    if v < -1.:
        assert v_own < 0.


@pytest.mark.parametrize("sign", [1., -1.])
def test_rt_window_end_direction_with_extreme_value(da_result, sign):
    """v 很大为正 → RT 窗口末 X_τ ≥ 日前值（多存）；v 很大为负 → X_τ ≤ 日前值。"""
    da2 = _value_da(da_result)
    cfg = da2.case.cfg
    plan = rolling._da_plan_arrays(da2)
    t, t1 = 0, 8
    terms = rolling.value_method_terms(cfg, plan, t, t1)
    terms["state_value"] = [(k, u, rel, sign * 2000. if k in ("E", "Vu") else v) for k, u, rel, v in terms["state_value"]]
    state = rolling.initial_state(cfg)
    case = rolling.make_window_case(da2.case, cfg, t, t1, t, True, state, {}, dict(terms), "RT")
    r = rolling.clear_window(case)
    m = r["model"]
    rel = t1 - 1 - t
    checked, at_bound = 0, 0
    for k, u, rel_b, lo, hi, label in terms["state_band"]:
        if k not in ("E", "Vu"):
            continue
        x = pyo.value(getattr(m, k)[u, rel])
        x_da = float(plan[k][u][t1 - 1])
        if sign > 0:
            assert x >= x_da - 1e-6, (k, u, x, x_da, lo, hi)
            at_bound += abs(x - hi) < 1e-4
        else:
            assert x <= x_da + 1e-6, (k, u, x, x_da, lo, hi)
            at_bound += abs(x - lo) < 1e-4
        checked += 1
    # 抽蓄定速抽水只能按台阶调整库容，不要求贴边；至少储能应贴到偏差带边界
    assert checked and at_bound
