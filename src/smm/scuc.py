"""日前 SCUC（MILP）与 SCED（固定整数后的 LP）+ LMP（方案第 6、7 节）。

目标：最小化 报价成本 + 启动费 + 空载费 + 弃水罚 + 松弛罚（负荷固定时等价于社会福利最大化）。
所有成本项按 元 计，功率项乘 Δt。对偶除以 Δt 得 元/MWh。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from .bids import THERMAL_TYPES
from .case import DACase

EPS_STORAGE = 0.1  # 报量不报价储能的极小损耗成本（元/MWh，假设值）


@dataclass
class ModelData:
    T: int
    dt: float
    buses: list
    lines: list
    thermal: dict = field(default_factory=dict)
    ren: dict = field(default_factory=dict)
    hydro_units: dict = field(default_factory=dict)
    hydro: dict = field(default_factory=dict)
    storage: dict = field(default_factory=dict)


def _seg_lists(segs):
    L = [s["p_end"] - s["p_start"] for s in segs]
    c = [s["price"] for s in segs]
    return L, c


def prepare(case: DACase) -> ModelData:
    cfg, bids = case.cfg, case.bids["units"]
    md = ModelData(T=cfg.T, dt=cfg.dt, buses=case.net.buses, lines=list(case.net.branches.branch_id))
    states_cfg = cfg.mode["startup_states"]  # 名称 -> 阈值 h（升序）
    for u in cfg.units["thermal"]:
        b = bids[u["id"]]
        L, c = _seg_lists(b["segments"])
        su = b["startup_cost"]
        states = sorted(states_cfg.items(), key=lambda kv: kv[1])
        md.thermal[u["id"]] = dict(u, L=L, c=c, noload_bid=b.get("noload_cost", u["noload"]),
                                   states=[(k, int(round(h / cfg.dt)), su[k]) for k, h in states],
                                   cold=su["cold"])
    for r in cfg.units["renewable"]:
        b = bids[r["id"]]
        L, c = _seg_lists(b["segments"])
        md.ren[r["id"]] = dict(r, L=L, c=c, fc=case.ren_fc[r["id"]])
    season = cfg.base["season"]
    for h in cfg.units["hydro"]:
        b = bids[h["id"]]
        L, c = _seg_lists(b["segments"])
        n = h["n_units"]
        if b.get("mode") == "planned":
            c = [0.0] * len(c)  # 计划出力/价格接受：不报价，按水量约束优化
        # 同型机组聚合：按“各子区运行台数”建模（整数 0..n），与逐台二进制等价但无对称性
        md.hydro[h["id"]] = dict(h, **h["seasons"][season], units=[h["id"]])
        md.hydro_units[h["id"]] = dict(station=h["id"], bus=h["bus"], zones=h["zones"], n_units=n, L=L, c=c,
                                       pmax=h["unit_pmax"] * n)
    for s in cfg.units["storage"]:
        b = bids[s["id"]]
        mode = b.get("mode", "price_quantity")
        if mode == "price_quantity":
            Ld, cd = _seg_lists(b["dis_segments"])
            Lc, cc = _seg_lists(b["ch_segments"])
        else:
            Ld, cd, Lc, cc = [s["pmax"]], [EPS_STORAGE], [s["pmax"]], [0.0]
        md.storage[s["id"]] = dict(s, mode=mode, Ld=Ld, cd=cd, Lc=Lc, cc=cc,
                                   soc_min=b.get("soc_min", s["soc_min"]), soc_max=b.get("soc_max", s["soc_max"]),
                                   soc_end=b.get("soc_end", s["soc_end"]), sched=b.get("self_schedule"))
    return md


def _sto_M(d, kind, dt):
    """储能 big-M 收紧：取 min(额定功率, 报价总量, 单时段可用能量折算功率)。"""
    E_rng = (d["soc_max"] - d["soc_min"]) * d["ecap"]
    if kind == "dis":
        return min(d["pmax"], sum(d["Ld"]), E_rng * d["eta_d"] / dt)
    return min(d["pmax"], sum(d["Lc"]), E_rng / d["eta_c"] / dt)


def build_model(case: DACase, penalties: dict | None = None) -> pyo.ConcreteModel:
    cfg, net = case.cfg, case.net
    md = prepare(case)
    pen = dict(cfg.mode["penalties"]) if penalties is None else penalties
    rcfg = cfg.mode["reserve"]
    T, dt = md.T, md.dt
    m = pyo.ConcreteModel("DA_SCUC")
    m.T = pyo.RangeSet(0, T - 1)
    m.N = pyo.Set(initialize=md.buses)
    m.L = pyo.Set(initialize=md.lines)
    m.G = pyo.Set(initialize=list(md.thermal))
    m.W = pyo.Set(initialize=list(md.ren))
    m.J = pyo.Set(initialize=list(md.hydro_units))
    m.HS = pyo.Set(initialize=list(md.hydro))
    m.S = pyo.Set(initialize=list(md.storage))
    m.GK = pyo.Set(dimen=2, initialize=[(g, k) for g, d in md.thermal.items() for k in range(len(d["L"]))])
    m.WK = pyo.Set(dimen=2, initialize=[(w, k) for w, d in md.ren.items() for k in range(len(d["L"]))])
    m.JK = pyo.Set(dimen=2, initialize=[(j, k) for j, d in md.hydro_units.items() for k in range(len(d["L"]))])
    m.JZ = pyo.Set(dimen=2, initialize=[(j, z) for j, d in md.hydro_units.items() for z in range(len(d["zones"]))])
    m.SD = pyo.Set(dimen=2, initialize=[(s, k) for s, d in md.storage.items() for k in range(len(d["Ld"]))])
    m.SC = pyo.Set(dimen=2, initialize=[(s, k) for s, d in md.storage.items() for k in range(len(d["Lc"]))])
    m.GST = pyo.Set(dimen=2, initialize=[(g, st[0]) for g, d in md.thermal.items() for st in d["states"]])
    m._md, m._case, m._pen = md, case, pen

    # ---------------- 变量
    m.u = pyo.Var(m.G, m.T, domain=pyo.Binary)
    m.v = pyo.Var(m.G, m.T, domain=pyo.Binary)
    m.w = pyo.Var(m.G, m.T, bounds=(0, 1))            # 由 u、v 决定，自然取整
    m.y = pyo.Var(m.GST, m.T, bounds=(0, 1))          # 分状态启动（随 v 取整）
    m.ycold = pyo.Var(m.G, m.T, bounds=(0, 1))
    m.p = pyo.Var(m.GK, m.T, domain=pyo.NonNegativeReals)
    m.P = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)
    m.pw = pyo.Var(m.WK, m.T, domain=pyo.NonNegativeReals)
    m.Pw = pyo.Var(m.W, m.T, domain=pyo.NonNegativeReals)
    m.delta = pyo.Var(m.JZ, m.T, domain=pyo.NonNegativeIntegers,
                      bounds=lambda m, j, z, t: (0, md.hydro_units[j]["n_units"]))  # 水电各稳定子区运行台数
    m.pz = pyo.Var(m.JZ, m.T, domain=pyo.NonNegativeReals)
    m.ph = pyo.Var(m.JK, m.T, domain=pyo.NonNegativeReals)
    m.Ph = pyo.Var(m.J, m.T, domain=pyo.NonNegativeReals)
    m.V = pyo.Var(m.HS, m.T, domain=pyo.NonNegativeReals)
    m.Qspill = pyo.Var(m.HS, m.T, domain=pyo.NonNegativeReals)
    sto_bin = bool(cfg.base["solver"].get("storage_binary", True))
    sdom = pyo.Binary if sto_bin else pyo.UnitInterval   # 默认 0-1；false 仅用于对比试验（LP 松弛）
    m.u_ch = pyo.Var(m.S, m.T, domain=sdom)              # 1=充电状态
    m.u_dis = pyo.Var(m.S, m.T, domain=sdom)             # 1=放电状态
    m.pd = pyo.Var(m.SD, m.T, domain=pyo.NonNegativeReals)
    m.pc = pyo.Var(m.SC, m.T, domain=pyo.NonNegativeReals)
    m.Pd = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals)
    m.Pc = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals)
    m.E = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals)
    m.inj = pyo.Var(m.N, m.T, domain=pyo.Reals)
    m.shed = pyo.Var(m.N, m.T, domain=pyo.NonNegativeReals)
    m.surplus = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.sl = pyo.Var(m.L, m.T, domain=pyo.NonNegativeReals)
    m.r_up = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)
    m.r_dn = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)
    m.rh_up = pyo.Var(m.J, m.T, domain=pyo.NonNegativeReals)
    m.rh_dn = pyo.Var(m.J, m.T, domain=pyo.NonNegativeReals)
    m.rs_up = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals)
    m.rs_dn = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals)
    m.sR_up = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.sR_dn = pyo.Var(m.T, domain=pyo.NonNegativeReals)

    load_bus = {n: case.load_bus.get(n, np.zeros(T)) for n in md.buses}
    for n in md.buses:
        for t in range(T):
            m.shed[n, t].setub(float(load_bus[n][t]))

    # ---------------- 火电
    def seg_ub(m, g, k, t):
        return m.p[g, k, t] <= md.thermal[g]["L"][k] * m.u[g, t]
    m.c_seg = pyo.Constraint(m.GK, m.T, rule=seg_ub)
    m.c_Psum = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.P[g, t] == sum(m.p[g, k, t] for k in range(len(md.thermal[g]["L"]))))
    m.c_Pmin = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.P[g, t] >= md.thermal[g]["pmin"] * m.u[g, t])
    m.c_Pmax = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.P[g, t] <= md.thermal[g]["pmax"] * m.u[g, t])

    def u_prev(g, t):
        return (1 if md.thermal[g]["init"]["online"] else 0) if t == 0 else m.u[g, t - 1]

    def p_prev(g, t):
        return md.thermal[g]["init"]["p0"] if t == 0 else m.P[g, t - 1]

    m.c_logic = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.u[g, t] - u_prev(g, t) == m.v[g, t] - m.w[g, t])

    def min_up(m, g, t):
        UT = int(round(md.thermal[g]["ut"] / dt))
        return sum(m.v[g, tau] for tau in range(max(0, t - UT + 1), t + 1)) <= m.u[g, t]

    def min_dn(m, g, t):
        DT = int(round(md.thermal[g]["dt"] / dt))
        return sum(m.w[g, tau] for tau in range(max(0, t - DT + 1), t + 1)) <= 1 - m.u[g, t]
    step = int(cfg.base["solver"].get("commit_step_periods", 1))
    if step > 1:  # 可选工程简化：启停只在整 step 个时段边界变化（如 4 = 按小时决策）
        m.c_cstep = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.v[g, t] + m.w[g, t] == 0 if t % step else pyo.Constraint.Skip)
    m.c_minup = pyo.Constraint(m.G, m.T, rule=min_up)
    m.c_mindn = pyo.Constraint(m.G, m.T, rule=min_dn)
    # 初始状态未满最小开/停机时间的锁定
    for g, d in md.thermal.items():
        init = d["init"]
        if init["online"]:
            rem = max(0, int(round((d["ut"] - init["hours"]) / dt)))
            for t in range(min(rem, T)):
                m.u[g, t].fix(1)
        else:
            rem = max(0, int(round((d["dt"] - init["hours"]) / dt)))
            for t in range(min(rem, T)):
                m.u[g, t].fix(0)

    # 分状态启动费：y_s ≤ Σ_{τ=1..K_s} w[t−τ] (+ 初始停机时间满足阈值时为 1)
    def y_rule(m, g, s, t):
        d = md.thermal[g]
        K = dict((k, K) for k, K, _ in d["states"])[s]
        init = d["init"]
        const = 0
        if not init["online"] and (init["hours"] / dt + t) < K:
            const = 1
        expr = sum(m.w[g, t - tau] for tau in range(1, K + 1) if t - tau >= 0)
        if isinstance(expr, int) and expr == 0 and const == 0:
            return m.y[g, s, t] == 0
        return m.y[g, s, t] <= expr + const
    m.c_ystate = pyo.Constraint(m.GST, m.T, rule=y_rule)
    m.c_ysum = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: sum(m.y[g, s, t] for (gg, s) in m.GST if gg == g) + m.ycold[g, t] == m.v[g, t])

    def ramp_up(m, g, t):
        d = md.thermal[g]
        RU = d["ramp"] * 60 * dt
        SR = d["pmin"]
        return m.P[g, t] - p_prev(g, t) <= RU * u_prev(g, t) + SR * m.v[g, t]

    def ramp_dn(m, g, t):
        d = md.thermal[g]
        RD = d["ramp"] * 60 * dt
        SR = d["pmin"]
        return p_prev(g, t) - m.P[g, t] <= RD * m.u[g, t] + SR * m.w[g, t]
    m.c_rup = pyo.Constraint(m.G, m.T, rule=ramp_up)
    m.c_rdn = pyo.Constraint(m.G, m.T, rule=ramp_dn)

    # ---------------- 新能源
    m.c_wseg = pyo.Constraint(m.WK, m.T, rule=lambda m, w, k, t: m.pw[w, k, t] <= md.ren[w]["L"][k])
    m.c_wsum = pyo.Constraint(m.W, m.T, rule=lambda m, w, t: m.Pw[w, t] == sum(m.pw[w, k, t] for k in range(len(md.ren[w]["L"]))))
    m.c_wfc = pyo.Constraint(m.W, m.T, rule=lambda m, w, t: m.Pw[w, t] <= float(md.ren[w]["fc"][t]))

    # ---------------- 水电（单库，4 台机，振动区）
    m.c_hseg = pyo.Constraint(m.JK, m.T, rule=lambda m, j, k, t: m.ph[j, k, t] <= md.hydro_units[j]["L"][k])
    m.c_hsum = pyo.Constraint(m.J, m.T, rule=lambda m, j, t: m.Ph[j, t] == sum(m.ph[j, k, t] for k in range(len(md.hydro_units[j]["L"]))))
    m.c_hzsum = pyo.Constraint(m.J, m.T, rule=lambda m, j, t: m.Ph[j, t] == sum(m.pz[j, z, t] for z in range(len(md.hydro_units[j]["zones"]))))
    m.c_hzlo = pyo.Constraint(m.JZ, m.T, rule=lambda m, j, z, t: m.pz[j, z, t] >= md.hydro_units[j]["zones"][z][0] * m.delta[j, z, t])
    m.c_hzhi = pyo.Constraint(m.JZ, m.T, rule=lambda m, j, z, t: m.pz[j, z, t] <= md.hydro_units[j]["zones"][z][1] * m.delta[j, z, t])
    m.c_hzone = pyo.Constraint(m.J, m.T, rule=lambda m, j, t: sum(m.delta[j, z, t] for z in range(len(md.hydro_units[j]["zones"]))) <= md.hydro_units[j]["n_units"])

    def qgen(h, t):
        d = md.hydro[h]
        return d["water_rate"] * sum(m.Ph[j, t] for j in d["units"])

    def water(m, h, t):
        d = md.hydro[h]
        Vp = d["v0"] if t == 0 else m.V[h, t - 1]
        return m.V[h, t] == Vp + (d["qin"] - qgen(h, t) - m.Qspill[h, t]) * dt * 0.36
    m.c_water = pyo.Constraint(m.HS, m.T, rule=water)
    m.c_vlim = pyo.Constraint(m.HS, m.T, rule=lambda m, h, t: pyo.inequality(md.hydro[h]["vmin"], m.V[h, t], md.hydro[h]["vmax"]))
    m.c_eco = pyo.Constraint(m.HS, m.T, rule=lambda m, h, t: qgen(h, t) + m.Qspill[h, t] >= md.hydro[h]["qeco"])
    m.c_vend = pyo.Constraint(m.HS, rule=lambda m, h: m.V[h, T - 1] >= md.hydro[h]["v_end"])
    m.c_eday = pyo.Constraint(m.HS, rule=lambda m, h: sum(m.Ph[j, t] for j in md.hydro[h]["units"] for t in range(T)) * dt <= md.hydro[h]["e_day"])

    # ---------------- 储能
    # 分段上限直接乘状态变量（逐段 big-M = 段长，比只约束总功率更紧）
    m.c_sdseg = pyo.Constraint(m.SD, m.T, rule=lambda m, s, k, t: m.pd[s, k, t] <= md.storage[s]["Ld"][k] * m.u_dis[s, t])
    m.c_scseg = pyo.Constraint(m.SC, m.T, rule=lambda m, s, k, t: m.pc[s, k, t] <= md.storage[s]["Lc"][k] * m.u_ch[s, t])
    m.c_sdsum = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pd[s, t] == sum(m.pd[s, k, t] for k in range(len(md.storage[s]["Ld"]))))
    m.c_scsum = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pc[s, t] == sum(m.pc[s, k, t] for k in range(len(md.storage[s]["Lc"]))))
    m.c_sexcl = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.u_ch[s, t] + m.u_dis[s, t] <= 1)   # 充放互斥
    m.c_sdmax = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pd[s, t] <= _sto_M(md.storage[s], "dis", dt) * m.u_dis[s, t])
    m.c_scmax = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pc[s, t] <= _sto_M(md.storage[s], "ch", dt) * m.u_ch[s, t])

    def soc(m, s, t):
        d = md.storage[s]
        Ep = d["soc0"] * d["ecap"] if t == 0 else m.E[s, t - 1]
        return m.E[s, t] == Ep + (d["eta_c"] * m.Pc[s, t] - m.Pd[s, t] / d["eta_d"]) * dt
    m.c_soc = pyo.Constraint(m.S, m.T, rule=soc)
    m.c_elim = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: pyo.inequality(md.storage[s]["soc_min"] * md.storage[s]["ecap"], m.E[s, t], md.storage[s]["soc_max"] * md.storage[s]["ecap"]))
    m.c_eend = pyo.Constraint(m.S, rule=lambda m, s: m.E[s, T - 1] == md.storage[s]["soc_end"] * md.storage[s]["ecap"])

    def sched(m, s, t):
        d = md.storage[s]
        if d["mode"] != "self_schedule":
            return pyo.Constraint.Skip
        return m.Pd[s, t] - m.Pc[s, t] == float(d["sched"][t])
    m.c_ssched = pyo.Constraint(m.S, m.T, rule=sched)

    # ---------------- 节点注入、功率平衡、PTDF 线路约束
    gens_at = {n: [g for g, d in md.thermal.items() if d["bus"] == n] for n in md.buses}
    ren_at = {n: [w for w, d in md.ren.items() if d["bus"] == n] for n in md.buses}
    hyd_at = {n: [j for j, d in md.hydro_units.items() if d["bus"] == n] for n in md.buses}
    sto_at = {n: [s for s, d in md.storage.items() if d["bus"] == n] for n in md.buses}
    tie_at = {n: sum((sch for (b, sch) in case.tie.values() if b == n), np.zeros(T)) for n in md.buses}
    ref = case.net.ref_bus

    def inj_def(m, n, t):
        gen = (sum(m.P[g, t] for g in gens_at[n]) + sum(m.Pw[w, t] for w in ren_at[n])
               + sum(m.Ph[j, t] for j in hyd_at[n]) + sum(m.Pd[s, t] - m.Pc[s, t] for s in sto_at[n])
               + float(tie_at[n][t]) + m.shed[n, t] - (m.surplus[t] if n == ref else 0))
        return gen - m.inj[n, t] == float(load_bus[n][t])
    m.c_inj = pyo.Constraint(m.N, m.T, rule=inj_def)            # 对偶 = 节点 LMP × Δt
    m.c_bal = pyo.Constraint(m.T, rule=lambda m, t: sum(m.inj[n, t] for n in md.buses) == 0)  # 对偶 = λ × Δt

    ptdf = case.net.ptdf
    rate = dict(zip(case.net.branches.branch_id, case.net.branches.rate_mw))
    lidx = {l: i for i, l in enumerate(md.lines)}
    nidx = {n: i for i, n in enumerate(md.buses)}

    def flow(m, l, t):
        row = ptdf[lidx[l]]
        return sum(row[nidx[n]] * m.inj[n, t] for n in md.buses if abs(row[nidx[n]]) > 1e-8)
    m.flow = pyo.Expression(m.L, m.T, rule=flow)
    m.c_fup = pyo.Constraint(m.L, m.T, rule=lambda m, l, t: m.flow[l, t] <= rate[l] + m.sl[l, t])
    m.c_fdn = pyo.Constraint(m.L, m.T, rule=lambda m, l, t: m.flow[l, t] >= -rate[l] - m.sl[l, t])

    # ---------------- 备用 (12)–(13)
    prov = set(rcfg["providers"])
    G_res = [g for g, d in md.thermal.items() if d["type"] in prov]
    tight = bool(cfg.base["solver"].get("tighten", False))

    def rup_cap(m, g, t):
        d = md.thermal[g]
        rhs = d["pmax"] * m.u[g, t]
        if tight and int(round(d["ut"] / dt)) > 1:
            # 收紧（Morales-España 启停能力）：启动/停机当段出力+备用不超过 SU/SD（此处取 Pmin）
            rhs = rhs - (d["pmax"] - d["pmin"]) * m.v[g, t]
            if t + 1 < T:
                rhs = rhs - (d["pmax"] - d["pmin"]) * m.w[g, t + 1]
        return m.P[g, t] + m.r_up[g, t] <= rhs
    m.c_rup_cap = pyo.Constraint(m.G, m.T, rule=rup_cap)
    m.c_rdn_cap = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.r_dn[g, t] <= m.P[g, t] - md.thermal[g]["pmin"] * m.u[g, t])
    zhi = lambda j, t: sum(md.hydro_units[j]["zones"][z][1] * m.delta[j, z, t] for z in range(len(md.hydro_units[j]["zones"])))
    zlo = lambda j, t: sum(md.hydro_units[j]["zones"][z][0] * m.delta[j, z, t] for z in range(len(md.hydro_units[j]["zones"])))
    m.c_rhup_cap = pyo.Constraint(m.J, m.T, rule=lambda m, j, t: m.rh_up[j, t] <= zhi(j, t) - m.Ph[j, t])
    m.c_rhdn_cap = pyo.Constraint(m.J, m.T, rule=lambda m, j, t: m.rh_dn[j, t] <= m.Ph[j, t] - zlo(j, t))
    use_sto = bool(rcfg.get("storage_reserve"))

    def sto_res_up(m, s, t):
        d = md.storage[s]
        if not use_sto:
            return m.rs_up[s, t] == 0
        return m.rs_up[s, t] <= d["pmax"] - m.Pd[s, t] + m.Pc[s, t]

    def sto_res_dn(m, s, t):
        d = md.storage[s]
        if not use_sto:
            return m.rs_dn[s, t] == 0
        return m.rs_dn[s, t] <= d["pmax"] - m.Pc[s, t] + m.Pd[s, t]
    m.c_rsup = pyo.Constraint(m.S, m.T, rule=sto_res_up)
    m.c_rsdn = pyo.Constraint(m.S, m.T, rule=sto_res_dn)
    if use_sto:  # SOC 可持续 T_dur（默认 1 h，假设值）
        Tdur = float(rcfg.get("T_dur_h", 1.0))
        m.c_rsup_soc = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.E[s, t] - md.storage[s]["soc_min"] * md.storage[s]["ecap"] >= (m.Pd[s, t] + m.rs_up[s, t] - m.Pc[s, t]) * Tdur / md.storage[s]["eta_d"])
        m.c_rsdn_soc = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: md.storage[s]["soc_max"] * md.storage[s]["ecap"] - m.E[s, t] >= md.storage[s]["eta_c"] * (m.Pc[s, t] + m.rs_dn[s, t] - m.Pd[s, t]) * Tdur)
    mu = float(rcfg.get("mu_RE", 0.0))
    hyd_ok = "hydro" in prov
    m.c_Rup = pyo.Constraint(m.T, rule=lambda m, t: sum(m.r_up[g, t] for g in G_res) + (sum(m.rh_up[j, t] for j in md.hydro_units) if hyd_ok else 0)
                             + sum(m.rs_up[s, t] for s in md.storage) + mu * sum(float(md.ren[w]["fc"][t]) - m.Pw[w, t] for w in md.ren)
                             + m.sR_up[t] >= float(case.R_up[t]))
    m.c_Rdn = pyo.Constraint(m.T, rule=lambda m, t: sum(m.r_dn[g, t] for g in G_res) + (sum(m.rh_dn[j, t] for j in md.hydro_units) if hyd_ok else 0)
                             + sum(m.rs_dn[s, t] for s in md.storage) + m.sR_dn[t] >= float(case.R_dn[t]))

    # ---------------- 目标
    m.obj = pyo.Objective(expr=_objective_expr(m), sense=pyo.minimize)
    return m


INT_VARS = ("u", "v", "w", "delta", "u_ch", "u_dis")  # w 为连续变量，固定时一并取整；储能充放状态与启停一起固定


def _solver(cfg, mip=True):
    opt = pyo.SolverFactory("appsi_highs")
    sc = cfg.base["solver"]
    opt.highs_options = {"threads": int(sc.get("threads", 4)), "output_flag": bool(sc.get("log", False)),
                         "time_limit": float(sc.get("time_limit_s", 300)),
                         "mip_rel_gap": float(sc.get("mip_rel_gap", 0.002))}
    opt.config.stream_solver = bool(sc.get("log", False))
    if mip:
        opt.highs_options.update(sc.get("highs_extra") or {})   # presolve / mip_heuristic_effort 等
    return opt


def _heuristic_commitment(m, ulp, theta):
    """由 LP 松弛的开机变量构造一个满足最小开停机的单段开机计划（启发式，仅用于热启动）。"""
    md, dt, T = m._md, m._md.dt, len(m.T)
    plan = {}
    for g, d in md.thermal.items():
        on = [t for t in range(T) if ulp[g, t] > theta or (m.u[g, t].fixed and m.u[g, t].value > 0.5)]
        u = [0] * T
        if on:
            first, last = on[0], on[-1]
            if d["init"]["online"]:
                first = 0                                   # 初始在线：不安排“停机再启动”
            last = max(last, min(T - 1, first + int(round(d["ut"] / dt)) - 1))
            for t in range(first, last + 1):
                u[t] = 1
        for t in range(T):
            if m.u[g, t].fixed:
                u[t] = int(round(m.u[g, t].value))
        plan[g] = u
    return plan


def warm_start(m, log=lambda *a: None) -> dict:
    """热启动：① LP 松弛 → ② 启发式开机计划 → ③ 固定火电启停求小 MIP（水电台数、储能状态） → 作为 HiGHS 初始可行解。"""
    cfg = m._case.cfg
    sc = cfg.base["solver"]
    t0 = time.perf_counter()
    opt = _solver(cfg)
    opt.highs_options["solve_relaxation"] = True       # 只解 LP 松弛
    opt.solve(m)
    ulp = {(g, t): pyo.value(m.u[g, t]) for g in m.G for t in m.T}
    lp_bound = pyo.value(m.obj)
    plan = _heuristic_commitment(m, ulp, float(sc.get("warm_start_theta", 0.2)))
    prefixed = {(g, t) for g in m.G for t in m.T if m.u[g, t].fixed}
    for g in m.G:
        for t in m.T:
            m.u[g, t].fix(plan[g][t])
    opt = _solver(cfg)
    opt.highs_options.update(time_limit=float(sc.get("warm_start_time_s", 30)), mip_rel_gap=0.01)
    try:
        opt.solve(m)          # 罚松弛保证该子问题总可行
        ok = True
    except RuntimeError:
        ok = False
    inc = pyo.value(m.obj) if ok else None
    for g in m.G:
        for t in m.T:
            if (g, t) not in prefixed:
                m.u[g, t].unfix()
    el = time.perf_counter() - t0
    log(f"[DA] 热启动：LP 下界 {lp_bound:.0f}，启发式可行解 {inc if inc is None else round(inc)}，用时 {el:.1f}s")
    return dict(time_s=el, lp_bound=lp_bound, incumbent=inc, ok=ok)


def solve_scuc(m: pyo.ConcreteModel, log=lambda *a: None):
    cfg = m._case.cfg
    ws = None
    t0 = time.perf_counter()
    if cfg.base["solver"].get("warm_start", False):
        ws = warm_start(m, log)
    opt = _solver(cfg)
    if ws and ws["ok"]:
        opt.config.warmstart = True
    res = opt.solve(m)
    el = time.perf_counter() - t0
    tc = str(res.solver.termination_condition)
    if "optimal" not in tc.lower() and not ("maxtime" in tc.lower() or "time" in tc.lower()):
        raise RuntimeError(f"SCUC 未得到可行解：{tc}")
    info = dict(time_s=el, termination=tc, objective=pyo.value(m.obj), warm_start=ws,
                best_bound=res.problem.lower_bound,
                gap=(pyo.value(m.obj) - res.problem.lower_bound) / max(1.0, abs(pyo.value(m.obj))))
    return info


def fix_integers(m):
    for name in INT_VARS:
        var = getattr(m, name)
        for idx in var:
            val = round(pyo.value(var[idx]))
            if name in ("u_ch", "u_dis") and not var[idx].is_binary():   # 仅对比试验的松弛模式：按实际功率确定状态
                val = 1 if pyo.value((m.Pc if name == "u_ch" else m.Pd)[idx]) > 1e-6 else 0
            var[idx].domain = pyo.Reals  # 放松整数域，使 HiGHS 按 LP 求解并给出对偶
            var[idx].fix(val)


def set_penalties(m, pen):
    """替换罚因子并重建目标（用于定价罚因子重算）。"""
    m._pen = pen
    m.del_component(m.obj)
    m.obj = pyo.Objective(expr=_objective_expr(m), sense=pyo.minimize)


def _objective_expr(m):
    md, dt = m._md, m._md.dt
    th = sum(md.thermal[g]["c"][k] * m.p[g, k, t] * dt for (g, k) in m.GK for t in m.T)
    nl = sum(md.thermal[g]["noload_bid"] * m.u[g, t] * dt for g in m.G for t in m.T)
    su = sum(dict((s, c) for s, _, c in md.thermal[g]["states"])[s] * m.y[g, s, t] for (g, s) in m.GST for t in m.T) \
        + sum(md.thermal[g]["cold"] * m.ycold[g, t] for g in m.G for t in m.T)
    rw = sum(md.ren[w]["c"][k] * m.pw[w, k, t] * dt for (w, k) in m.WK for t in m.T)
    hy = sum(md.hydro_units[j]["c"][k] * m.ph[j, k, t] * dt for (j, k) in m.JK for t in m.T)
    sp = sum(md.hydro[h]["spill_penalty"] * m.Qspill[h, t] * dt for h in m.HS for t in m.T)
    st = sum(md.storage[s]["cd"][k] * m.pd[s, k, t] * dt for (s, k) in m.SD for t in m.T) \
        - sum(md.storage[s]["cc"][k] * m.pc[s, k, t] * dt for (s, k) in m.SC for t in m.T)
    pen = {k: float(v) for k, v in m._pen.items()}
    pn = sum(pen["balance"] * (sum(m.shed[n, t] for n in m.N) + m.surplus[t]) * dt for t in m.T) \
        + sum(pen["line"] * m.sl[l, t] * dt for l in m.L for t in m.T) \
        + sum(pen["reserve"] * (m.sR_up[t] + m.sR_dn[t]) * dt for t in m.T)
    return th + nl + su + rw + hy + sp + st + pn


def solve_sced(m):
    """固定整数后的 LP，读取对偶。"""
    cfg = m._case.cfg
    if not hasattr(m, "dual"):
        m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    opt = _solver(cfg, mip=False)
    t0 = time.perf_counter()
    res = opt.solve(m)
    el = time.perf_counter() - t0
    tc = str(res.solver.termination_condition)
    if "optimal" not in tc.lower():
        raise RuntimeError(f"SCED 未得到最优解：{tc}")
    return dict(time_s=el, termination=tc, objective=pyo.value(m.obj))


def storage_simultaneous(m, tol=1e-3) -> int:
    return sum(1 for s in m.S for t in m.T if pyo.value(m.Pd[s, t]) > tol and pyo.value(m.Pc[s, t]) > tol)


def slack_summary(m) -> dict:
    T = list(m.T)
    return dict(
        shed_mwh=sum(pyo.value(m.shed[n, t]) for n in m.N for t in T) * m._md.dt,
        surplus_mwh=sum(pyo.value(m.surplus[t]) for t in T) * m._md.dt,
        line_slack_max=max(pyo.value(m.sl[l, t]) for l in m.L for t in T),
        reserve_up_short_max=max(pyo.value(m.sR_up[t]) for t in T),
        reserve_dn_short_max=max(pyo.value(m.sR_dn[t]) for t in T),
    )


def has_slack(m, tol=1e-6) -> bool:
    s = slack_summary(m)
    return any(v > tol for v in s.values())


def extract_prices(m) -> dict:
    """λ、μ⁺、μ⁻、节点 LMP（直接对偶）与分解 LMP = λ − Σ PTDF·(μ⁺ − μ⁻)。单位 元/MWh。"""
    md, case = m._md, m._case
    dt = md.dt
    T = md.T
    N, L = md.buses, md.lines
    lam = np.array([m.dual[m.c_bal[t]] for t in range(T)]) / dt
    pi = np.array([[m.dual[m.c_inj[n, t]] for t in range(T)] for n in N]) / dt
    # 符号已用 π_n（注入定义约束对偶）逐点核对：π = λ − PTDF^T(μ⁺ − μ⁻)，误差 ~1e-12
    mu_p = -np.array([[m.dual[m.c_fup[l, t]] for t in range(T)] for l in L]) / dt
    mu_m = -np.array([[m.dual[m.c_fdn[l, t]] for t in range(T)] for l in L]) / dt  # 经验核对：appsi_highs 中 ≥ 约束对偶亦 ≤ 0
    ptdf = case.net.ptdf  # (L, N)
    # 平衡约束写成 Σinj = 0、注入定义 gen − inj = load：π_n = −λ_bal(符号由 KKT 定) ...
    # KKT 对 inj[n]：−π_n + λ_bal + Σ_l PTDF[l,n]·(dual_up + dual_dn) = 0
    energy = lam
    cong = -(ptdf.T @ (mu_p - mu_m))  # (N, T)：−Σ PTDF·(μ⁺−μ⁻)
    lmp_dec = energy[None, :] + cong
    r_up = np.array([m.dual[m.c_Rup[t]] for t in range(T)]) / dt
    r_dn = np.array([m.dual[m.c_Rdn[t]] for t in range(T)]) / dt
    return dict(lmp=pi, lmp_decomp=lmp_dec, energy=energy, congestion=cong, mu_plus=mu_p, mu_minus=mu_m,
                reserve_up_price=r_up, reserve_dn_price=r_dn)
