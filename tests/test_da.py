"""日前出清正确性检验：功率平衡、LMP 分解一致性（对偶与有限差分）、阻塞出现、约束满足。"""
import numpy as np
import pyomo.environ as pyo


def test_power_balance(da_result):
    u = da_result.tables["units"]
    sysdf = da_result.tables["system"]
    gen = u.groupby("period").P_mw.sum().to_numpy()
    load = sysdf.load_mw.to_numpy()
    shed = sysdf.shed_mw.to_numpy()
    surplus = sysdf.surplus_mw.to_numpy()
    assert np.allclose(gen + shed - surplus, load, atol=1e-4)
    assert shed.max() < 1e-6 and surplus.max() < 1e-6


def test_injection_sum_zero_and_flows(da_result):
    m = da_result.model
    for t in m.T:
        assert abs(sum(pyo.value(m.inj[n, t]) for n in m.N)) < 1e-5
    lines = da_result.tables["lines"]
    assert (lines.slack_mw < 1e-6).all()
    assert (lines.loading <= 1 + 1e-6).all()


def test_lmp_decomposition(da_result):
    p = da_result.prices
    net = da_result.case.net
    dec = p["energy"][None, :] - net.ptdf.T @ (p["mu_plus"] - p["mu_minus"])
    assert np.abs(p["lmp"] - dec).max() < 1e-6
    ref = net.bus_index(net.ref_bus)
    assert np.abs(p["lmp"][ref] - p["energy"]).max() < 1e-6     # 参考母线 LMP = 能量价
    assert (p["mu_plus"] >= -1e-6).all() and (p["mu_minus"] >= -1e-6).all()  # 阻塞对偶非负
    # 互补松弛：μ>0 的线路必须满载
    lines = da_result.tables["lines"]
    act = lines[(lines.mu_plus + lines.mu_minus) > 1e-4]
    assert (act.loading > 1 - 1e-5).all()


def test_congestion_occurs(da_result):
    p = da_result.prices
    spread = p["lmp"].max(axis=0) - p["lmp"].min(axis=0)
    assert spread.max() > 1.0, "应出现节点价差"
    assert (np.abs(p["mu_plus"]) + np.abs(p["mu_minus"]) > 1e-3).any(), "应有线路阻塞对偶 > 0"


def _rhs_shift(m, n, t, delta):
    c = m.c_inj[n, t]
    rhs = pyo.value(c.upper)
    c.set_value(c.body == rhs + delta)
    return rhs


def test_lmp_finite_difference(da_result):
    """固定整数的 SCED 上，节点负荷 ±0.5 MW 的目标函数差分应与 LMP 一致（退化时位于左右导数之间）。"""
    from smm import scuc
    m = da_result.model
    dt = m._md.dt
    p = da_result.prices
    net = da_result.case.net
    spread = p["lmp"].max(axis=0) - p["lmp"].min(axis=0)
    t = int(np.argmax(spread))
    i_hi, i_lo = int(np.argmax(p["lmp"][:, t])), int(np.argmin(p["lmp"][:, t]))
    base = pyo.value(m.obj)
    checked = 0
    for i in (i_hi, i_lo):
        n = net.buses[i]
        if da_result.case.load_bus.get(n) is None:
            continue
        h = 0.5
        rhs = _rhs_shift(m, n, t, +h)
        scuc.solve_sced(m)
        up = (pyo.value(m.obj) - base) / h / dt
        m.c_inj[n, t].set_value(m.c_inj[n, t].body == rhs - h)
        scuc.solve_sced(m)
        dn = (base - pyo.value(m.obj)) / h / dt
        m.c_inj[n, t].set_value(m.c_inj[n, t].body == rhs)
        lmp = p["lmp"][i, t]
        lo, hi = min(up, dn) - 1e-3, max(up, dn) + 1e-3
        assert lo - 1e-2 * abs(lmp) <= lmp <= hi + 1e-2 * abs(lmp), (n, t, lmp, up, dn)
        checked += 1
    scuc.solve_sced(m)
    assert checked >= 1


def test_storage_binary_exclusive(da_result):
    """储能充放状态为 0-1 变量，u_ch+u_dis≤1，SCED 中与启停一起固定。"""
    m = da_result.model
    assert da_result.case.cfg.base["solver"]["storage_binary"] is True
    for s in m.S:
        for t in m.T:
            uc, ud = m.u_ch[s, t], m.u_dis[s, t]
            assert uc.fixed and ud.fixed                      # SCED 固定
            assert uc.value in (0, 1) and ud.value in (0, 1)
            assert uc.value + ud.value <= 1
            pmax = m._md.storage[s]["pmax"]
            assert pyo.value(m.Pc[s, t]) <= pmax * uc.value + 1e-6
            assert pyo.value(m.Pd[s, t]) <= pmax * ud.value + 1e-6


def test_storage_and_hydro(da_result):
    st = da_result.tables["storage"]
    assert ((st.P_dis > 1e-3) & (st.P_ch > 1e-3)).sum() == 0
    assert (st.u_ch + st.u_dis <= 1).all()
    cfg = da_result.case.cfg
    for s in cfg.units["storage"]:
        d = st[st.unit == s["id"]]
        assert abs(d.soc.iloc[-1] - s["soc_end"]) < 1e-6
        assert d.soc.min() >= s["soc_min"] - 1e-6 and d.soc.max() <= s["soc_max"] + 1e-6
    hy = da_result.tables["hydro"]
    h = cfg.units["hydro"][0]["seasons"][cfg.base["season"]]
    assert hy.V_1e4m3.iloc[-1] >= h["v_end"] - 1e-6
    assert hy.P_mw.sum() * cfg.dt <= h["e_day"] + 1e-6
    zones = cfg.units["hydro"][0]["zones"]
    # 每台机组不在振动区：总出力必须可分解进各子区
    for P, n in zip(hy.P_mw, hy.units_on):
        assert P >= n * min(z[0] for z in zones) - 1e-6 and P <= n * max(z[1] for z in zones) + 1e-6


def test_reserve_and_thermal_limits(da_result):
    s = da_result.tables["system"]
    assert (s.R_up_short < 1e-6).all()
    u = da_result.tables["units"]
    cfg = da_result.case.cfg
    for g in cfg.units["thermal"]:
        d = u[u.unit == g["id"]]
        on = d.on.to_numpy()
        P = d.P_mw.to_numpy()
        assert np.all(P <= g["pmax"] * on + 1e-6) and np.all(P >= g["pmin"] * on - 1e-6)
        ramp = g["ramp"] * 60 * cfg.dt
        dP = np.diff(np.r_[g["init"]["p0"], P])
        prev_on = np.r_[1 if g["init"]["online"] else 0, on[:-1]]
        assert np.all(dP <= ramp * prev_on + g["pmin"] * (1 - prev_on) + 1e-5)
