"""日前 SCUC（MILP）与 SCED（固定整数后的 LP）+ LMP（方案第 6、7 节）。

目标：最小化能量报价成本（含储能充电报价抵扣）、启动费、空载费，
以及已建模的供需平衡、线路、备用和储能锚点软约束松弛罚。
新能源弃电与水电弃水不附加惩罚；报价成本不等同于真实社会成本，不能据此直接声称社会福利最大化。
目标以元计；按元/MWh报价的功率项乘 Δt，启动费按次计。能量平衡对偶除以 Δt 得元/MWh。
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
    pumped: dict = field(default_factory=dict)


def _seg_lists(segs):
    L = [s["p_end"] - s["p_start"] for s in segs]
    c = [s["price"] for s in segs]
    return L, c


def prepare(case: DACase) -> ModelData:
    cfg, bids = case.cfg, case.bids["units"]
    from .input_validation import validate_physical_inputs
    validate_physical_inputs(cfg, init=case.init)
    md = ModelData(T=case.T or cfg.T, dt=cfg.dt, buses=case.net.buses, lines=list(case.net.branches.branch_id))
    states_cfg = cfg.mode["startup_states"]  # 名称 -> 阈值 h（升序）
    for u in cfg.units["thermal"]:
        b = bids[u["id"]]
        L, c = _seg_lists(b["segments"])
        from .bids import resolve_startup_bid
        startup = resolve_startup_bid(cfg, b)
        uniform = startup["mode"] == "uniform_per_unit"
        su = b.get("startup_cost", {})
        states = sorted(states_cfg.items(), key=lambda kv: kv[1])
        uu = dict(u)
        if case.init and u["id"] in case.init.get("thermal", {}):
            uu["init"] = dict(case.init["thermal"][u["id"]])
        md.thermal[u["id"]] = dict(uu, L=L, c=c, noload_bid=b.get("noload_cost", u["noload"]),
                                   states=[] if uniform else [(k, int(np.ceil(float(h) / cfg.dt - 1e-12)), su[k]) for k, h in states],
                                   startup_threshold_hours={k: float(h) for k, h in states},
                                   cold=0. if uniform else su["cold"], startup_cost_mode=startup["mode"],
                                   startup_cost_yuan=startup["cost_yuan"], startup_cost_source=startup["source"])
    for r in cfg.units["renewable"]:
        b = bids[r["id"]]
        L, c = _seg_lists(b["segments"])
        md.ren[r["id"]] = dict(r, L=L, c=c, fc=case.ren_fc[r["id"]])
    season = cfg.base["season"]
    if case.init is not None:
        hydro_v = case.init.get("hydro", {})
        expected_hydro = {h["id"] for h in cfg.units["hydro"]}
        if (not isinstance(hydro_v, dict) or set(hydro_v) != expected_hydro
                or any(not np.isfinite(float(v)) for v in hydro_v.values())):
            raise ValueError("滚动初值hydro必须完整包含所有启用水电的有限实际库容，不能退回季节初值")
    for h in cfg.units["hydro"]:
        b = bids[h["id"]]
        L, c = _seg_lists(b["segments"])
        n = h["n_units"]
        if b.get("mode") == "planned":
            c = [0.0] * len(c)  # 计划出力/价格接受：不报价，按水量约束优化
        # 同型机组聚合：按“各子区运行台数”建模（整数 0..n），与逐台二进制等价但无对称性
        hd = dict(h, **h["seasons"][season], units=[h["id"]])
        hd.setdefault("regulation", "reservoir")
        if hd["regulation"] == "run_of_river":
            # 径流式：库容仅为日调节池（pondage，万m3；0 = 纯径流，来水 = 发电流量 + 弃水），日末回到初值，不受日电量约束
            v0s = float(hd["v0"])
            hd["vmin"], hd["vmax"], hd["v_end"] = v0s - float(h.get("pondage", 0) or 0), v0s, v0s
            hd["e_day"], hd["e_day_min"] = None, None
        md.hydro[h["id"]] = hd
        if case.init and h["id"] in case.init.get("hydro", {}):
            md.hydro[h["id"]]["v0"] = float(case.init["hydro"][h["id"]])
        if case.hydro_e_budget and h["id"] in case.hydro_e_budget and hd.get("e_day") is not None:
            remaining = float(case.hydro_e_budget[h["id"]])
            if not np.isfinite(remaining) or remaining < -1e-6:
                raise ValueError(f"水电 {h['id']} 实际执行已超日电量预算: 剩余 {remaining} MWh")
            md.hydro[h["id"]]["e_day"] = remaining  # never clip away an actual budget violation
        # Each j currently represents a station's identical units aggregated by
        # stable-zone counts, not an individually identified physical turbine.
        for required in ("ramp", "init_units_on", "init_p_unit"):
            if required not in h:
                raise ValueError(f"水电 {h['id']} 缺少显式 {required}，不能猜测初始出力/爬坡")
        ramp = float(h["ramp"])  # MW/min per physical unit
        n0 = float(h["init_units_on"])
        p0 = n0 * float(h["init_p_unit"])
        if case.init is not None:
            if h["id"] not in case.init.get("hydro_p", {}) or h["id"] not in case.init.get("hydro_online", {}):
                raise ValueError(f"水电 {h['id']} 滚动初值必须带实际hydro_p和hydro_online")
            p0 = float(case.init["hydro_p"][h["id"]])
            n0 = float(case.init["hydro_online"][h["id"]])
        if (not np.isfinite(ramp) or ramp <= 0 or not np.isfinite(n0) or n0 != round(n0)
                or not 0 <= n0 <= n or not np.isfinite(p0) or p0 < -1e-6
                or p0 > n0 * float(h["unit_pmax"]) + 1e-6):
            raise ValueError(f"水电 {h['id']} 初始出力/在线台数/爬坡参数非法")
        md.hydro_units[h["id"]] = dict(station=h["id"], bus=h["bus"], zones=h["zones"], n_units=n, L=L, c=c,
                                       pmax=h["unit_pmax"] * n, ramp=ramp, p0=p0, units0=int(n0))
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
                                   soc_end=b.get("soc_end"), sched=b.get("self_schedule"))   # 申报期末 SOC；None = 未申报，由出清决定
        if case.init and s["id"] in case.init.get("storage", {}):
            md.storage[s["id"]]["soc0"] = float(case.init["storage"][s["id"]]) / s["ecap"]
    # 抽蓄市场规则同储能（MKT.pumped.rules = storage）：报价方式取报价文件 mode（price_quantity / quantity_only），
    # 日末上库库容按申报值硬约束（同储能申报期末 SOC），未申报由出清决定。
    for p in cfg.units.get("pumped", []):
        b = bids.get(p["id"]) or {}
        mode = b.get("mode", "price_quantity")
        if mode == "dispatch":          # 旧名（已弃用）→ quantity_only
            mode = "quantity_only"
        N = int(p["n_units"])
        if mode == "price_quantity":
            Lg, cg = _seg_lists(b["gen_segments"])
            Lp, cp = _seg_lists(b["pump_segments"])
        else:   # quantity_only：同储能报量不报价（发电计极小成本 EPS 避免退化，抽水 0）
            Lg, cg, Lp, cp = [p["unit_gen_max"] * N], [EPS_STORAGE], [p["unit_pump"] * N], [0.0]
        init_mode = p.get("init_mode", "idle")
        st0 = dict(V=float(p["vup0"]), ng=N if init_mode == "gen" else 0, np=N if init_mode == "pump" else 0,
                   xg=int(init_mode == "gen"), xp=int(init_mode == "pump"))
        if case.init and p["id"] in case.init.get("pumped", {}):
            st0.update(case.init["pumped"][p["id"]])
        if "vup_end" in b:                       # 报价申报的日末上库库容（None = 不申报）
            vend = b["vup_end"]
        else:                                    # 报价未含该字段（旧报价）：用 units.yaml 的 vup_end_bid 作为缺省申报
            vend = p.get("vup_end", p.get("vup_end_bid", p["vup0"]))
        md.pumped[p["id"]] = dict(p, mode=mode, N=N, Lg=Lg, cg=cg, Lp=Lp, cp=cp, init=st0,
                                  v_lo=max(float(p["vup_min"]), float(p["v_total"]) - float(p["vdn_max"])),
                                  v_hi=min(float(p["vup_max"]), float(p["v_total"]) - float(p["vdn_min"])),
                                  v_end=None if vend is None else float(vend))
    return md


def _sto_M(d, kind, dt, initial=False):
    """储能 big-M 收紧：取 min(额定功率, 报价总量, 单时段可用能量折算功率)。"""
    E_rng = (d["soc_max"] - d["soc_min"]) * d["ecap"]
    if initial:
        # Actual initial stock can be outside a tighter declared SOC band.
        # The first transition must use its actual headroom, not band width.
        E_rng = max(0., ((d["soc0"] - d["soc_min"]) if kind == "dis"
                        else (d["soc_max"] - d["soc0"])) * d["ecap"])
    if kind == "dis":
        return min(d["pmax"], sum(d["Ld"]), E_rng * d["eta_d"] / dt)
    return min(d["pmax"], sum(d["Lc"]), E_rng / d["eta_c"] / dt)


def _attach_startup_state_constraints(m, md, dt):
    """Exact disjoint outage intervals, independent of startup-bid ordering.

    Shutdown at k and startup at t imply (t-k)*dt hours offline. State
    thresholds are strict upper bounds: hot <8h, warm <72h, cold >=72h.
    Excluding every more recent shutdown prevents an older cycle from making
    a warm/cold state eligible when the latest outage is actually hot.
    """
    thresholds = {g: {name: K for name, K, _ in d["states"]} for g, d in md.thermal.items()}
    def upper(m, g, state, t):
        d = md.thermal[g]
        K = thresholds[g][state]
        hours = d.get("startup_threshold_hours", {}).get(state, K*dt)
        initial = int(not d["init"]["online"] and d["init"]["hours"] + t*dt < hours)
        recent = sum(m.w[g, tau] for tau in range(max(0, t-K+1), t))
        return m.y[g, state, t] <= recent + initial
    m.c_ystate = pyo.Constraint(m.GST, m.T, rule=upper)
    m.c_ystate_lower = pyo.ConstraintList()
    for g, d in md.thermal.items():
        previous = 0
        previous_hours = 0.
        for state, upper_K, _ in d["states"]:
            for t in m.T:
                y = m.y[g, state, t]
                if not d["init"]["online"] and d["init"]["hours"] + t*dt < previous_hours:
                    m.c_ystate_lower.add(y == 0)
                else:
                    for tau in range(max(0, t-previous+1), t):
                        m.c_ystate_lower.add(y <= 1-m.w[g, tau])
            previous = upper_K
            previous_hours = d.get("startup_threshold_hours", {}).get(state, upper_K*dt)
        for t in m.T:
            if not d["init"]["online"] and d["init"]["hours"] + t*dt < previous_hours:
                m.c_ystate_lower.add(m.ycold[g, t] == 0)
            else:
                for tau in range(max(0, t-previous+1), t):
                    m.c_ystate_lower.add(m.ycold[g, t] <= 1-m.w[g, tau])
    m.c_ysum = pyo.Constraint(m.G, m.T, rule=lambda m, g, t:
        sum(m.y[g, state, t] for state, _, _ in md.thermal[g]["states"]) + m.ycold[g, t] == m.v[g, t])


def build_model(case: DACase, penalties: dict | None = None) -> pyo.ConcreteModel:
    # DACase is mutable and can be constructed directly (including RT/ID windows).
    # Revalidate every build; never cache approval of a mutable bid dictionary.
    from .input_validation import validate_case_inputs
    validate_case_inputs(case)
    from .bids import validate_bids, raise_on_errors
    raise_on_errors(validate_bids(case.cfg, case.bids, require_complete=True))
    cfg, net = case.cfg, case.net
    from .config import validate_tracking_policy
    validate_tracking_policy(cfg.mode)
    md = prepare(case)
    pen = dict(cfg.mode["penalties"]) if penalties is None else dict(penalties)
    from .input_validation import number
    for key in ('balance', 'line', 'reserve'):
        if key not in pen or number(pen[key], f'penalties.{key}') < 0:
            raise ValueError(f'penalties.{key}须为有限非负数')
    rcfg = cfg.mode["reserve"]
    down_enabled = rcfg.get("down_enabled", True)
    if not isinstance(down_enabled, bool):
        raise ValueError("reserve.down_enabled必须为布尔值")
    T, dt = md.T, md.dt
    m = pyo.ConcreteModel(f"{case.stage}_SCUC")
    t0 = int(case.t0)
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
    m.PS = pyo.Set(initialize=list(md.pumped))
    m.PGK = pyo.Set(dimen=2, initialize=[(p, k) for p, d in md.pumped.items() for k in range(len(d["Lg"]))])
    m.PPK = pyo.Set(dimen=2, initialize=[(p, k) for p, d in md.pumped.items() for k in range(len(d["Lp"]))])
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
    # 抽水蓄能（全厂级）：工况 0-1（发电 xg / 抽水 xp）+ 各工况运行台数（整数）
    m.xg = pyo.Var(m.PS, m.T, domain=pyo.Binary)
    m.xp = pyo.Var(m.PS, m.T, domain=pyo.Binary)
    m.ngen = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeIntegers, bounds=lambda m, p, t: (0, md.pumped[p]["N"]))
    m.npump = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeIntegers, bounds=lambda m, p, t: (0, md.pumped[p]["N"]))
    m.pg = pyo.Var(m.PGK, m.T, domain=pyo.NonNegativeReals)
    m.pp = pyo.Var(m.PPK, m.T, domain=pyo.NonNegativeReals)
    m.Pg = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)
    m.Pp = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)
    m.Vu = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)
    m.sg = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)     # 发电启动台次（随整数自然取整）
    m.sp = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)     # 抽水启动台次
    m.rp_up = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)
    m.rp_dn = pyo.Var(m.PS, m.T, domain=pyo.NonNegativeReals)
    if not down_enabled:
        for name in ("r_dn", "rh_dn", "rs_dn", "rp_dn", "sR_dn"):
            for v in getattr(m, name).values():
                v.fix(0.)

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
        UT = int(np.ceil(md.thermal[g]["ut"] / dt - 1e-10))
        return sum(m.v[g, tau] for tau in range(max(0, t - UT + 1), t + 1)) <= m.u[g, t]

    def min_dn(m, g, t):
        DT = int(np.ceil(md.thermal[g]["dt"] / dt - 1e-10))
        return sum(m.w[g, tau] for tau in range(max(0, t - DT + 1), t + 1)) <= 1 - m.u[g, t]
    step = int(cfg.base["solver"].get("commit_step_periods", 1))
    if step > 1:  # 可选工程简化：启停只在整 step 个时段边界变化（如 4 = 按小时决策）
        m.c_cstep = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: m.v[g, t] + m.w[g, t] == 0 if (case.t0 + t) % step else pyo.Constraint.Skip)
    m.c_minup = pyo.Constraint(m.G, m.T, rule=min_up)
    m.c_mindn = pyo.Constraint(m.G, m.T, rule=min_dn)
    # Physical residual minimum times also remain constraints: later case.fixed
    # may not overwrite the requirement by replacing a variable fix.
    initial_minimum = {}
    for g, d in md.thermal.items():
        init = d["init"]
        if init["online"]:
            rem = max(0, int(np.ceil((d["ut"] - init["hours"]) / dt - 1e-10)))
            for t in range(min(rem, T)):
                m.u[g, t].fix(1)
                initial_minimum[g, t] = 1
        else:
            rem = max(0, int(np.ceil((d["dt"] - init["hours"]) / dt - 1e-10)))
            for t in range(min(rem, T)):
                m.u[g, t].fix(0)
                initial_minimum[g, t] = 0

    m.c_initial_min_duration = pyo.Constraint(list(initial_minimum), rule=lambda m, g, t: m.u[g, t] == initial_minimum[g, t])

    if cfg.mode.get("startup_cost_mode", "state_dependent") == "uniform_per_unit":
        for variable in m.ycold.values():
            variable.fix(0.)
    else:
        _attach_startup_state_constraints(m, md, dt)

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

    def hydro_on(j, t):
        return sum(m.delta[j, z, t] for z in range(len(md.hydro_units[j]["zones"])))

    def hydro_prev_p(j, t):
        return md.hydro_units[j]["p0"] if t == 0 else m.Ph[j, t - 1]

    def hydro_prev_on(j, t):
        return md.hydro_units[j]["units0"] if t == 0 else hydro_on(j, t - 1)

    # Station aggregate endpoint envelope. Upward ramp uses current online count;
    # downward ramp uses previous count, including the executed cross-window state.
    # A unit's rating is MW/min; multiply by 60*dt exactly once. No new commitment.
    m.c_hramp_up = pyo.Constraint(m.J, m.T, rule=lambda m, j, t:
        m.Ph[j, t] - hydro_prev_p(j, t) <= md.hydro_units[j]["ramp"] * 60 * dt * hydro_on(j, t))
    m.c_hramp_dn = pyo.Constraint(m.J, m.T, rule=lambda m, j, t:
        hydro_prev_p(j, t) - m.Ph[j, t] <= md.hydro_units[j]["ramp"] * 60 * dt * hydro_prev_on(j, t))

    def qgen(h, t):
        d = md.hydro[h]
        return d["water_rate"] * sum(m.Ph[j, t] for j in d["units"])

    hist = (case.init or {}).get("hydro_hist", {})

    def upstream_out(h, t):
        """梯级：上游电站出库（发电流量 + 弃水）经 lag 个时段到达（川V4.0 附件8 约束(17) 上游项、附件2(三) 上下游时滞）。"""
        d = md.hydro[h]
        up = d.get("upstream")
        if not up:
            return 0.0
        tau = t - int(d.get("lag_periods", 0))
        if tau >= 0 and up in md.hydro:
            return qgen(up, tau) + m.Qspill[up, tau]
        a = t0 + tau                                   # 窗口外：取已执行（或日前计划）出库流量；日前之前取初值
        hh = hist.get(up)
        if a >= 0:
            if hh is None or a >= len(hh) or not np.isfinite(float(hh[a])):
                raise ValueError(f"梯级水电 {h} 缺少上游 {up} 时段 {a} 已执行/已计划出库历史，不能退回设备初值")
            return float(hh[a])
        # Only pre-day water travel is represented by the explicit equipment
        # initial-outflow assumption; within-day missing history must fail closed.
        u = next(x for x in cfg.units["hydro"] if x["id"] == up)
        return float(u["water_rate"] * u["init_units_on"] * u["init_p_unit"])

    def water(m, h, t):
        d = md.hydro[h]
        Vp = d["v0"] if t == 0 else m.V[h, t - 1]
        return m.V[h, t] == Vp + (d["qin"] + upstream_out(h, t) - qgen(h, t) - m.Qspill[h, t]) * dt * 0.36
    m._upstream_out = upstream_out
    m.c_water = pyo.Constraint(m.HS, m.T, rule=water)          # 水量平衡（川V4.0 附件8 约束(17)）
    m.c_vlim = pyo.Constraint(m.HS, m.T, rule=lambda m, h, t: pyo.inequality(md.hydro[h]["vmin"], m.V[h, t], md.hydro[h]["vmax"]))
    m.c_eco = pyo.Constraint(m.HS, m.T, rule=lambda m, h, t: qgen(h, t) + m.Qspill[h, t] >= md.hydro[h]["qeco"])   # 生态流量
    m.c_qout = pyo.Constraint(m.HS, m.T, rule=lambda m, h, t: qgen(h, t) + m.Qspill[h, t] <= float(md.hydro[h]["qout_max"])
                              if md.hydro[h].get("qout_max") is not None else pyo.Constraint.Skip)
    term = case.terminal
    if term is None or term.get("hydro_vend", False):   # 日前 / 至日末的日内窗口：期末库容（水位）控制（川V4.0 附件8 约束(17)、7.4.11）
        m.c_vend = pyo.Constraint(m.HS, rule=lambda m, h: m.V[h, T - 1] >= md.hydro[h]["v_end"])
    if term is not None and "hydro_window_end" in term:
        targets = term["hydro_window_end"]
        if not isinstance(targets, dict) or set(targets) != set(md.hydro):
            raise ValueError("hydro_window_end须为所有启用水电的同钟点日前库容目标")
        for h, target in targets.items():
            if not np.isfinite(float(target)):
                raise ValueError(f"水电 {h} 窗口末库容目标必须有限；不可截断/软化目标")
        m.c_hydro_window_end = pyo.Constraint(m.HS, rule=lambda m, h:
            m.V[h, T - 1] == float(targets[h]))  # hard equality, no slack or price rerun
    # 实时fixed_da为本模型保守研究设置，并非甘肃2026规则要求永久沿用日前曲线。
    # 日电量约束（川V4.0 附件8 约束(16)、7.4.10）：仅库容型；径流式按来水发电
    m.c_eday = pyo.Constraint(m.HS, rule=lambda m, h: sum(m.Ph[j, t] for j in md.hydro[h]["units"] for t in range(T)) * dt <= md.hydro[h]["e_day"] + 1e-6
                              if md.hydro[h].get("e_day") is not None else pyo.Constraint.Skip)
    m.c_edaymin = pyo.Constraint(m.HS, rule=lambda m, h: sum(m.Ph[j, t] for j in md.hydro[h]["units"] for t in range(T)) * dt >= float(md.hydro[h]["e_day_min"]) - 1e-6
                                 if (term is None and md.hydro[h].get("e_day_min")) else pyo.Constraint.Skip)

    # ---------------- 抽水蓄能（全厂级，闭式上下库）
    m.c_pgseg = pyo.Constraint(m.PGK, m.T, rule=lambda m, p, k, t: m.pg[p, k, t] <= md.pumped[p]["Lg"][k])
    m.c_ppseg = pyo.Constraint(m.PPK, m.T, rule=lambda m, p, k, t: m.pp[p, k, t] <= md.pumped[p]["Lp"][k])
    m.c_pgsum = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.Pg[p, t] == sum(m.pg[p, k, t] for k in range(len(md.pumped[p]["Lg"]))))
    m.c_ppsum = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.Pp[p, t] == sum(m.pp[p, k, t] for k in range(len(md.pumped[p]["Lp"]))))
    m.c_pexcl = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.xg[p, t] + m.xp[p, t] <= 1)        # 全厂不同时抽水与发电
    m.c_png_hi = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.ngen[p, t] <= md.pumped[p]["N"] * m.xg[p, t])
    m.c_pnp_hi = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.npump[p, t] <= md.pumped[p]["N"] * m.xp[p, t])
    m.c_png_lo = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.ngen[p, t] >= m.xg[p, t])
    m.c_pnp_lo = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.npump[p, t] >= m.xp[p, t])
    m.c_pgmin = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.Pg[p, t] >= md.pumped[p]["unit_gen_min"] * m.ngen[p, t])
    m.c_pgmax = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.Pg[p, t] <= md.pumped[p]["unit_gen_max"] * m.ngen[p, t])

    def pump_lo(m, p, t):
        d = md.pumped[p]
        lo = d["unit_pump_min"] if d.get("variable_speed") else d["unit_pump"]   # 定速：抽水功率 = 台数 × 额定
        return m.Pp[p, t] >= lo * m.npump[p, t]
    m.c_pplo = pyo.Constraint(m.PS, m.T, rule=pump_lo)
    m.c_pphi = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.Pp[p, t] <= md.pumped[p]["unit_pump"] * m.npump[p, t])

    def xprev(p, key, t):
        if t >= 0:
            return getattr(m, key)[p, t]
        absolute = case.t0 + t
        if t == -1:
            return md.pumped[p]["init"][key]
        if absolute < 0:
            # Explicit research pre-day assumption: declared init mode held before day start.
            return int(md.pumped[p].get("init_mode", "idle") == ("gen" if key == "xg" else "pump"))
        history = (case.init or {}).get("pumped_hist", {}).get(p, {}).get(key, [])
        if absolute >= len(history) or not np.isfinite(float(history[absolute])) or float(history[absolute]) not in (0., 1.):
            raise ValueError(f"抽蓄 {p} 缺少已执行工况历史 {key}[{absolute}]")
        return int(history[absolute])

    def gap_gp(m, p, t, k):
        return xprev(p, "xg", t - k) + m.xp[p, t] <= 1
    def gap_pg(m, p, t, k):
        return xprev(p, "xp", t - k) + m.xg[p, t] <= 1
    gaps = sorted({int(d.get("switch_gap", 1)) for d in md.pumped.values()} or {1})
    m.PGAP = pyo.Set(initialize=list(range(1, max(gaps) + 1)))
    # 工况转换间隔：发电→抽水、抽水→发电之间至少停 switch_gap 个时段（转换需数分钟，假设做法）
    m.c_pgap1 = pyo.Constraint(m.PS, m.T, m.PGAP, rule=lambda m, p, t, k: gap_gp(m, p, t, k) if k <= int(md.pumped[p].get("switch_gap", 1)) else pyo.Constraint.Skip)
    m.c_pgap2 = pyo.Constraint(m.PS, m.T, m.PGAP, rule=lambda m, p, t, k: gap_pg(m, p, t, k) if k <= int(md.pumped[p].get("switch_gap", 1)) else pyo.Constraint.Skip)

    def nprev(p, key, t):
        return md.pumped[p]["init"][{"ngen": "ng", "npump": "np"}[key]] if t < 0 else getattr(m, key)[p, t]
    m.c_psg = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.sg[p, t] >= m.ngen[p, t] - nprev(p, "ngen", t - 1))
    m.c_psp = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.sp[p, t] >= m.npump[p, t] - nprev(p, "npump", t - 1))
    pstep = int((cfg.mode.get("pumped") or {}).get("mode_step_periods", 1))
    if term is None and pstep > 1:   # 工程简化（假设）：抽发台数只在整 pstep 时段边界变化（4 = 小时内工况一致，类比广东 2026 细则储能“小时内充放电状态一致”）
        m.c_pstep_g = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.ngen[p, t] == m.ngen[p, t - 1] if t % pstep else pyo.Constraint.Skip)
        m.c_pstep_p = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: m.npump[p, t] == m.npump[p, t - 1] if t % pstep else pyo.Constraint.Skip)
    # Aggregate N*per-unit daily limits; executed positive count changes, not
    # potentially degenerate sg/sp, consume the remaining budget across windows.
    used_starts = (case.init or {}).get("pumped_used", {})
    remaining_starts = {}
    for p, d in md.pumped.items():
        if case.t0 > 0 and p not in used_starts:
            raise ValueError(f"抽蓄 {p} 缺少当日已执行启动累计量")
        used = used_starts.get(p, {"gen": 0., "pump": 0.})
        remaining_starts[p] = {}
        for kind, capkey in (("gen", "max_gen_starts"), ("pump", "max_pump_starts")):
            value = used.get(kind)
            cap = d["N"] * d[capkey]
            if value is None or not np.isfinite(float(value)) or value < 0 or value > cap:
                raise ValueError(f"抽蓄 {p} {kind} 已执行启动累计量非法或超过日上限")
            remaining_starts[p][kind] = cap - float(value)
    m._pumped_remaining_starts = remaining_starts
    m.c_psg_max = pyo.Constraint(m.PS, rule=lambda m, p: sum(m.sg[p, t] for t in m.T) <= remaining_starts[p]["gen"])
    m.c_psp_max = pyo.Constraint(m.PS, rule=lambda m, p: sum(m.sp[p, t] for t in m.T) <= remaining_starts[p]["pump"])

    def pres(m, p, t):
        d = md.pumped[p]
        Vp = d["init"]["V"] if t == 0 else m.Vu[p, t - 1]
        return m.Vu[p, t] == Vp + (m.Pp[p, t] / d["e_pump"] - m.Pg[p, t] / d["e_gen"]) * dt
    m.c_pres = pyo.Constraint(m.PS, m.T, rule=pres)            # 上库库容平衡（万m3；电量/库容转换系数）
    m.c_pvlim = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: pyo.inequality(md.pumped[p]["v_lo"], m.Vu[p, t], md.pumped[p]["v_hi"]))
    if term is None:   # 日前：同储能申报期末 SOC —— 申报了日末上库库容即硬约束，未申报由出清决定
        P_end = [p for p in m.PS if md.pumped[p]["v_end"] is not None]
        m.c_pvend = pyo.Constraint(P_end, rule=lambda m, p: m.Vu[p, T - 1] == md.pumped[p]["v_end"])

    # ---------------- 储能
    # 分段上限直接乘状态变量（逐段 big-M = 段长，比只约束总功率更紧）
    m.c_sdseg = pyo.Constraint(m.SD, m.T, rule=lambda m, s, k, t: m.pd[s, k, t] <= md.storage[s]["Ld"][k] * m.u_dis[s, t])
    m.c_scseg = pyo.Constraint(m.SC, m.T, rule=lambda m, s, k, t: m.pc[s, k, t] <= md.storage[s]["Lc"][k] * m.u_ch[s, t])
    m.c_sdsum = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pd[s, t] == sum(m.pd[s, k, t] for k in range(len(md.storage[s]["Ld"]))))
    m.c_scsum = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pc[s, t] == sum(m.pc[s, k, t] for k in range(len(md.storage[s]["Lc"]))))
    m.c_sexcl = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.u_ch[s, t] + m.u_dis[s, t] <= 1)   # 充放互斥
    m.c_sdmax = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pd[s, t] <= _sto_M(md.storage[s], "dis", dt, initial=t == 0) * m.u_dis[s, t])
    m.c_scmax = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: m.Pc[s, t] <= _sto_M(md.storage[s], "ch", dt, initial=t == 0) * m.u_ch[s, t])

    def soc(m, s, t):
        d = md.storage[s]
        Ep = d["soc0"] * d["ecap"] if t == 0 else m.E[s, t - 1]
        return m.E[s, t] == Ep + (d["eta_c"] * m.Pc[s, t] - m.Pd[s, t] / d["eta_d"]) * dt
    m.c_soc = pyo.Constraint(m.S, m.T, rule=soc)
    m.c_elim = pyo.Constraint(m.S, m.T, rule=lambda m, s, t: pyo.inequality(md.storage[s]["soc_min"] * md.storage[s]["ecap"], m.E[s, t], md.storage[s]["soc_max"] * md.storage[s]["ecap"]))
    # 期末 SOC：
    #   日前：储能申报了运行日末期望 SOC → 硬约束 E[T−1] = soc_end·ecap（川V4.0 7.7.4、附件8 式(20)）；未申报 → 不约束，由出清决定。
    #   two_hour_eq：电池/抽蓄以c_anc_eq硬等式执行，sA固定0；水电软跟踪不受影响。
    #   历史实时（甘V3.2 运营细则附录5 第9项）：窗口内锚点时段 lo ≤ E ≤ hi（带显式松弛与罚项的软锚点），lo/hi 由 rolling 按日前 SOC 计算：
    #     window_end_pct：窗口末时段 (1−x)E_DA ≤ E_RT ≤ (1+x)E_DA；hourly_eq：窗口内各整点 E_RT = E_DA（广东）。
    #   sA_up/sA_dn 为软锚点松弛（罚 anchor_penalty）；标准流程不再放宽边界或重定价，直接使用同次SCED对偶。
    if term is None:
        S_end = [s for s in m.S if md.storage[s]["soc_end"] is not None]
        m.c_eend = pyo.Constraint(S_end, rule=lambda m, s: m.E[s, T - 1] == float(md.storage[s]["soc_end"]) * md.storage[s]["ecap"])
    # 锚点：储能 E（MWh）与抽蓄上库库容 Vu（万m3），规则相同（realtime.storage.anchor）
    tracking = (term or {}).get("tracking_policy")
    if tracking is not None:
        if not isinstance(tracking, dict) or case.stage != "RT" or tracking.get("mode") != "soft_deliverable_energy":
            raise ValueError("soft tracking policy requires RT soft_deliverable_energy")
        tracking_c = tracking.get("penalty_yuan_per_discharge_mwh")
        if isinstance(tracking_c, bool) or not isinstance(tracking_c, (int, float)) or not np.isfinite(tracking_c) or tracking_c <= 0:
            raise ValueError("tracking penalty must be finite positive yuan/discharge MWh")
    hydro_tracking = (term or {}).get("hydro_tracking", {})
    if hydro_tracking:
        if tracking is None or set(hydro_tracking) != set(md.hydro) or any(not np.isfinite(float(v)) for v in hydro_tracking.values()):
            raise ValueError("hydro_tracking requires complete finite RT soft targets")
        if "hydro_window_end" in term:
            raise ValueError("cannot apply hard and soft hydro window tracking together")
    anchors = [] if term is None else (
        [(s, int(t), float(lo), float(hi), "E") for s, lst in term.get("storage_anchor", {}).items() for (t, lo, hi) in lst]
        + [(p, int(t), float(lo), float(hi), "Vu") for p, lst in term.get("pumped_anchor", {}).items() for (t, lo, hi) in lst]
        + [(h, T - 1, float(v), float(v), "V") for h, v in hydro_tracking.items()])
    m.A = pyo.Set(initialize=list(range(len(anchors))))
    m._anchors = anchors
    hard = {i for i, a in enumerate(anchors) if (term or {}).get("storage_anchor_hard", False) and a[4] in ("E", "Vu")}
    if any(anchors[i][2] != anchors[i][3] for i in hard):
        raise ValueError("储能/抽蓄硬锚点必须给定相同上下界")
    m._hard_anchor_indices = hard
    m._anchor_cost_coefficients = {}
    for i, (unit, _, _, _, kind) in enumerate(anchors):
        if i in hard:
            coefficient = 0.
        elif tracking is not None:
            conversion = (float(md.storage[unit]["eta_d"]) if kind == "E" else
                          float(md.pumped[unit]["e_gen"]) if kind == "Vu" else
                          1. / (.36 * float(md.hydro[unit]["water_rate"])))
            if not np.isfinite(conversion) or conversion <= 0:
                raise ValueError("invalid tracking discharge-energy conversion")
            coefficient = tracking_c * conversion
        else:
            Pa = float(term.get("anchor_penalty", 1e5))
            coefficient = float(term.get("pumped_anchor_penalty", Pa)) if kind == "Vu" else Pa
        m._anchor_cost_coefficients[i] = coefficient
    m.A_lo = pyo.Param(m.A, mutable=True, initialize={i: a[2] for i, a in enumerate(anchors)})
    m.A_hi = pyo.Param(m.A, mutable=True, initialize={i: a[3] for i, a in enumerate(anchors)})
    m.sA_up = pyo.Var(m.A, domain=pyo.NonNegativeReals)
    m.sA_dn = pyo.Var(m.A, domain=pyo.NonNegativeReals)
    for i in hard:
        m.sA_up[i].fix(0.)
        m.sA_dn[i].fix(0.)
    m.c_anc_eq = pyo.Constraint(sorted(hard), rule=lambda m, i: getattr(m, anchors[i][4])[anchors[i][0], anchors[i][1]] == m.A_lo[i])
    m.c_anc_hi = pyo.Constraint(m.A, rule=lambda m, i: getattr(m, anchors[i][4])[anchors[i][0], anchors[i][1]] <= m.A_hi[i] + m.sA_up[i])
    m.c_anc_lo = pyo.Constraint(m.A, rule=lambda m, i: getattr(m, anchors[i][4])[anchors[i][0], anchors[i][1]] >= m.A_lo[i] - m.sA_dn[i])

    def sched(m, s, t):
        d = md.storage[s]
        if d["mode"] != "self_schedule":
            return pyo.Constraint.Skip
        return m.Pd[s, t] - m.Pc[s, t] == float(d["sched"][t0 + t])
    m.c_ssched = pyo.Constraint(m.S, m.T, rule=sched)

    # ---------------- 节点注入、功率平衡、PTDF 线路约束
    gens_at = {n: [g for g, d in md.thermal.items() if d["bus"] == n] for n in md.buses}
    ren_at = {n: [w for w, d in md.ren.items() if d["bus"] == n] for n in md.buses}
    hyd_at = {n: [j for j, d in md.hydro_units.items() if d["bus"] == n] for n in md.buses}
    sto_at = {n: [s for s, d in md.storage.items() if d["bus"] == n] for n in md.buses}
    ps_at = {n: [p for p, d in md.pumped.items() if d["bus"] == n] for n in md.buses}
    tie_at = {n: sum((sch for (b, sch) in case.tie.values() if b == n), np.zeros(T)) for n in md.buses}
    ref = case.net.ref_bus

    def inj_def(m, n, t):
        gen = (sum(m.P[g, t] for g in gens_at[n]) + sum(m.Pw[w, t] for w in ren_at[n])
               + sum(m.Ph[j, t] for j in hyd_at[n]) + sum(m.Pd[s, t] - m.Pc[s, t] for s in sto_at[n])
               + sum(m.Pg[p, t] - m.Pp[p, t] for p in ps_at[n])
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
    if use_sto:
        # Simplified incremental reserve energy only, using current period-start
        # stock (actual initial state at t=0, otherwise E[t-1]). No extra qualification binaries or big-M. Baseline modes
        # and stock dynamics are unchanged; this is NOT a full H-hour delivery
        # guarantee for the baseline plus activation trajectory.
        raw_duration = rcfg.get("T_dur_h")
        if (isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float))
                or not np.isfinite(raw_duration) or raw_duration <= 0):
            raise ValueError("启用储能/抽蓄备用须显式设置有限正数reserve.T_dur_h（小时）")
        Tdur = float(raw_duration)
        def storage_available(s, t, upper=False):
            d = md.storage[s]
            bound = d["soc_max" if upper else "soc_min"] * d["ecap"]
            stock = d["soc0"] * d["ecap"] if t == 0 else m.E[s, t-1]
            available = bound - stock if upper else stock - bound
            # Initial SOC can lie outside the declared operating band and recover
            # within the first period. Do not make zero reserve infeasible there.
            return max(0., available) if t == 0 else available
        m.c_rsup_soc = pyo.Constraint(m.S, m.T, rule=lambda m, s, t:
            Tdur * m.rs_up[s, t] / md.storage[s]["eta_d"] <= storage_available(s, t))
        m.c_rsdn_soc = pyo.Constraint(m.S, m.T, rule=lambda m, s, t:
            Tdur * m.rs_dn[s, t] * md.storage[s]["eta_c"] <= storage_available(s, t, upper=True))
    use_ps = use_sto   # Common participation switch, but PS reserve is generation-only.
    m.c_rpup = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: (m.rp_up[p, t] <= md.pumped[p]["unit_gen_max"] * m.ngen[p, t] - m.Pg[p, t])
                              if use_ps else m.rp_up[p, t] == 0)
    m.c_rpdn = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t: (m.rp_dn[p, t] <= m.Pg[p, t] - md.pumped[p]["unit_gen_min"] * m.ngen[p, t])
                              if use_ps else m.rp_dn[p, t] == 0)
    if use_ps:
        # Online generation headroom above forces zero reserve in pump/idle.
        # Effective reservoir bounds already account for both reservoirs.
        def pumped_available(p, t, upper=False):
            d = md.pumped[p]
            stock = d["init"]["V"] if t == 0 else m.Vu[p, t-1]
            available = d["v_hi"] - stock if upper else stock - d["v_lo"]
            return max(0., available) if t == 0 else available
        m.c_rpup_volume = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t:
            Tdur * m.rp_up[p, t] / md.pumped[p]["e_gen"] <= pumped_available(p, t))
        m.c_rpdn_volume = pyo.Constraint(m.PS, m.T, rule=lambda m, p, t:
            Tdur * m.rp_dn[p, t] / md.pumped[p]["e_gen"] <= pumped_available(p, t, upper=True))
    mu = float(rcfg.get("mu_RE", 0.0))
    hyd_ok = "hydro" in prov
    m.c_Rup = pyo.Constraint(m.T, rule=lambda m, t: sum(m.r_up[g, t] for g in G_res) + (sum(m.rh_up[j, t] for j in md.hydro_units) if hyd_ok else 0)
                             + sum(m.rs_up[s, t] for s in md.storage) + sum(m.rp_up[p, t] for p in md.pumped) + mu * sum(float(md.ren[w]["fc"][t]) - m.Pw[w, t] for w in md.ren)
                             + m.sR_up[t] >= float(case.R_up[t]))
    m.c_Rdn = pyo.Constraint(m.T, rule=lambda m, t: (sum(m.r_dn[g, t] for g in G_res) + (sum(m.rh_dn[j, t] for j in md.hydro_units) if hyd_ok else 0)
                             + sum(m.rs_dn[s, t] for s in md.storage) + sum(m.rp_dn[p, t] for p in md.pumped) + m.sR_dn[t] >= float(case.R_dn[t]))
                             if down_enabled else pyo.Constraint.Skip)

    # ---------------- 调频：独立AGC资格，与能量/备用共享物理能力
    from . import regulation
    regulation.attach(m)

    # ---------------- 滚动窗口：固定指定整数变量（日内锁定的启停、实时沿用的水电台数/储能状态）
    for name, vals in (case.fixed or {}).items():
        if name not in INT_VARS or not isinstance(vals, dict):
            raise ValueError(f'fixed.{name}须为受支持整数变量的索引映射')
        var = getattr(m, name)
        for idx, val in vals.items():
            value = number(val, f'fixed.{name}[{idx}]')
            if idx not in var or abs(value - round(value)) > 1e-6:
                raise ValueError(f'fixed.{name}[{idx}]须为有效索引的整数值，禁止静默取整')
            value = int(round(value))  # Solver integrality tolerance only.
            if value not in var[idx].domain or (var[idx].lb is not None and value < var[idx].lb) or (var[idx].ub is not None and value > var[idx].ub):
                raise ValueError(f'fixed.{name}[{idx}]超出整数变量技术边界')
            var[idx].fix(value)
    for name, vals in ((term or {}).get("fix_power") or {}).items():   # 实时固定功率（水电/储能执行日前曲线）
        if name not in ('P', 'Pw', 'Ph', 'Pd', 'Pc', 'Pg', 'Pp') or not isinstance(vals, dict):
            raise ValueError(f'fix_power.{name}须为受支持功率变量的索引映射')
        var = getattr(m, name)
        for idx, val in vals.items():
            value = number(val, f'fix_power.{name}[{idx}]')
            if idx not in var or value < -1e-6:
                raise ValueError(f'fix_power.{name}[{idx}]须为有效索引的非负功率，禁止静默裁剪')
            var[idx].fix(value)

    # Asset-only suffix keeps remaining hard anchors reachable without extending market clearing.
    from .asset_reachability import attach as attach_reachability
    attach_reachability(m)

    # ---------------- 目标
    m.obj = pyo.Objective(expr=_objective_expr(m), sense=pyo.minimize)
    return m


INT_VARS = ("u", "v", "w", "delta", "u_ch", "u_dis", "xg", "xp", "ngen", "npump")  # w 为连续变量，固定时一并取整；储能充放状态与启停一起固定


@dataclass
class SolverBudget:
    """Shared solver-call elapsed budget, not an OS wall-clock deadline."""
    total_s: float
    reserve_lp_s: float = 10.
    used_s: float = 0.
    calls: list = field(default_factory=list)

    def allowance(self, reserve_s=0.):
        available = self.total_s - self.used_s - reserve_s
        if not np.isfinite(available) or available <= 0:
            raise RuntimeError("RT shared solver budget exhausted; no dispatch/price fallback permitted")
        return available

    def report(self):
        return dict(total_solver_budget_s=self.total_s, solver_elapsed_s=self.used_s,
                    remaining_solver_s=max(0., self.total_s-self.used_s), reserve_lp_s=self.reserve_lp_s,
                    scope="solver_calls_only_not_build_export_or_OS_wall_clock", calls=list(self.calls))


def _solve_with_budget(m, opt, *, tee=False, warmstart=False, phase="sced", reserve_lp=False):
    budget = getattr(m, "_solver_budget", None)
    if budget is None:
        return opt.solve(m, tee=tee, warmstart=warmstart)
    limit = budget.allowance(budget.reserve_lp_s if reserve_lp else 0.)
    limit = min(limit, float(opt.highs_options.get("time_limit", limit)))
    opt.highs_options["time_limit"] = limit
    start = time.perf_counter()
    success = False
    try:
        result = opt.solve(m, tee=tee, warmstart=warmstart)
        success = True
    finally:
        elapsed = max(0., time.perf_counter()-start)
        budget.used_s += elapsed
        budget.calls.append(dict(phase=phase, time_limit_s=limit, elapsed_s=elapsed, returned=success))
    if budget.used_s > budget.total_s:
        raise RuntimeError("RT shared solver budget exhausted during solver call; results not executable")
    return result


def _solver(cfg, mip=True):
    opt = pyo.SolverFactory("appsi_highs")
    sc = cfg.base["solver"]
    opt.highs_options = {"threads": int(sc.get("threads", 4)), "output_flag": bool(sc.get("log", False)),
                         "time_limit": float(sc.get("time_limit_s", 300))}
    opt.config.stream_solver = bool(sc.get("log", False))
    if mip:
        opt.highs_options["mip_rel_gap"] = float(sc.get("mip_rel_gap", 0.01))
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
    _solve_with_budget(m, opt, tee=bool(sc.get("log", False)), warmstart=False, phase="warm_relaxation", reserve_lp=True)
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
        _solve_with_budget(m, opt, tee=bool(sc.get("log", False)), warmstart=False, phase="warm_commitment", reserve_lp=True)
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
    if getattr(m, "_agc", {}).get("enabled", False) and getattr(m, "_agc_stage", None) != "energy_scuc":
        raise ValueError("启用调频时必须先prepare_energy，再执行唯一能量SCUC")
    cfg = m._case.cfg
    ws = None
    t0 = time.perf_counter()
    if cfg.base["solver"].get("warm_start", False):
        ws = warm_start(m, log)
    opt = _solver(cfg)
    try:
        res = _solve_with_budget(m, opt, tee=bool(cfg.base["solver"].get("log", False)),
                                 warmstart=bool(ws and ws["ok"]), phase="scuc", reserve_lp=True)
    except RuntimeError as exc:
        native=getattr(opt,"_solver_model",None)
        status=str(native.getModelStatus()) if native is not None else "unavailable"
        primal=bool(native.getSolution().value_valid) if native is not None else None
        raise RuntimeError(f"SCUC失败：highs_model_status={status}, primal_solution_available={primal}；{exc}") from exc
    el = time.perf_counter() - t0
    tc = str(res.solver.termination_condition)
    if "optimal" not in tc.lower() and not ("maxtime" in tc.lower() or "time" in tc.lower()):
        raise RuntimeError(f"SCUC 未得到可行解：{tc}")
    info = dict(time_s=el, termination=tc, objective=pyo.value(m.obj), warm_start=ws,
                best_bound=res.problem.lower_bound,
                gap=(pyo.value(m.obj) - res.problem.lower_bound) / max(1.0, abs(pyo.value(m.obj))))
    return info


def integer_components(m):
    for name in INT_VARS:
        yield name, getattr(m, name)
    block=getattr(m,'asset_reachability',None)
    if block is not None:
        for name in block._integer_names:
            yield 'asset_reachability.'+name, getattr(block,name)


def fix_integers(m):
    for name, var in integer_components(m):
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


def thermal_startup_cost(m):
    """Yuan per startup event, never multiplied by timestep duration."""
    md = m._md
    return sum((md.thermal[g]["startup_cost_yuan"] * m.v[g, t]
                if md.thermal[g]["startup_cost_mode"] == "uniform_per_unit" else
                sum(cost * m.y[g, state, t] for state, _, cost in md.thermal[g]["states"])
                + md.thermal[g]["cold"] * m.ycold[g, t]) for g in m.G for t in m.T)


def _objective_expr(m):
    md, dt = m._md, m._md.dt
    th = sum(md.thermal[g]["c"][k] * m.p[g, k, t] * dt for (g, k) in m.GK for t in m.T)
    nl = sum(md.thermal[g]["noload_bid"] * m.u[g, t] * dt for g in m.G for t in m.T)
    su = thermal_startup_cost(m)
    rw = sum(md.ren[w]["c"][k] * m.pw[w, k, t] * dt for (w, k) in m.WK for t in m.T)
    hy = sum(md.hydro_units[j]["c"][k] * m.ph[j, k, t] * dt for (j, k) in m.JK for t in m.T)
    # Renewable/hydro energy bids determine dispatch; no additional curtailment/spill penalty.
    st = sum(md.storage[s]["cd"][k] * m.pd[s, k, t] * dt for (s, k) in m.SD for t in m.T) \
        - sum(md.storage[s]["cc"][k] * m.pc[s, k, t] * dt for (s, k) in m.SC for t in m.T)
    ps = sum(md.pumped[p]["cg"][k] * m.pg[p, k, t] * dt for (p, k) in m.PGK for t in m.T) \
        - sum(md.pumped[p]["cp"][k] * m.pp[p, k, t] * dt for (p, k) in m.PPK for t in m.T) \
        + sum(md.pumped[p]["start_cost_gen"] * m.sg[p, t] + md.pumped[p]["start_cost_pump"] * m.sp[p, t] for p in m.PS for t in m.T)
    pen = {k: float(v) for k, v in m._pen.items()}
    pn = sum(pen["balance"] * (sum(m.shed[n, t] for n in m.N) + m.surplus[t]) * dt for t in m.T) \
        + sum(pen["line"] * m.sl[l, t] * dt for l in m.L for t in m.T) \
        + sum(pen["reserve"] * (m.sR_up[t] + m.sR_dn[t]) * dt for t in m.T)
    term = m._case.terminal
    tp = 0
    if term is not None and len(m.A):
        tp = sum(m._anchor_cost_coefficients[i] * (m.sA_up[i] + m.sA_dn[i]) for i in m.A)
    from . import regulation
    return th + nl + su + rw + hy + st + ps + pn + tp + regulation.objective_term(m)


def solve_sced(m):
    """固定整数后的 LP，读取对偶。"""
    if getattr(m, "_agc", {}).get("enabled", False) and getattr(m, "_agc_stage", None) not in (
            "agc_reservation_lp", "reserved_for_energy_sced"):
        raise ValueError("启用调频时SCED必须通过clear_reservations完成独立预留，不能绕过阶段顺序")
    cfg = m._case.cfg
    if not hasattr(m, "dual"):
        m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    opt = _solver(cfg, mip=False)
    t0 = time.perf_counter()
    res = _solve_with_budget(m, opt, tee=bool(cfg.base["solver"].get("log", False)), warmstart=False,
                             phase=getattr(m, "_solver_budget_phase", "formal_sced"))
    el = time.perf_counter() - t0
    tc = str(res.solver.termination_condition)
    if "optimal" not in tc.lower():
        raise RuntimeError(f"SCED 未得到最优解：{tc}")
    return dict(time_s=el, termination=tc, objective=pyo.value(m.obj))


def storage_simultaneous(m, tol=1e-3) -> int:
    return sum(1 for s in m.S for t in m.T if pyo.value(m.Pd[s, t]) > tol and pyo.value(m.Pc[s, t]) > tol) \
        + sum(1 for p in m.PS for t in m.T if pyo.value(m.Pg[p, t]) > tol and pyo.value(m.Pp[p, t]) > tol)


def slack_summary(m) -> dict:
    """Numeric reporting only; anchor slack units follow E (MWh) / Vu (10^4 m3).

    anchor_slack_max_mwh is a deprecated, mixed-unit legacy key. Its numeric
    value is preserved for old consumers; use the two typed keys for magnitudes.
    """
    T = list(m.T)
    anchor_values = {i: pyo.value(m.sA_up[i]) + pyo.value(m.sA_dn[i]) for i in m.A}
    return dict(
        shed_mwh=sum(pyo.value(m.shed[n, t]) for n in m.N for t in T) * m._md.dt,
        surplus_mwh=sum(pyo.value(m.surplus[t]) for t in T) * m._md.dt,
        line_slack_max=max(pyo.value(m.sl[l, t]) for l in m.L for t in T),
        reserve_up_short_max=max(pyo.value(m.sR_up[t]) for t in T),
        reserve_dn_short_max=max(pyo.value(m.sR_dn[t]) for t in T),
        anchor_slack_max_mwh=max(anchor_values.values(), default=0.0),
        storage_anchor_slack_max_mwh=max((v for i, v in anchor_values.items() if m._anchors[i][4] == "E"), default=0.0),
        hydro_anchor_slack_max_1e4m3=max((v for i, v in anchor_values.items() if m._anchors[i][4] == "V"), default=0.0),
        pumped_anchor_slack_max_1e4m3=max((v for i, v in anchor_values.items() if m._anchors[i][4] == "Vu"), default=0.0),
    )


def has_slack(m, tol=1e-6) -> bool:
    """调度松弛（切负荷/弃电/线路/备用）是否被触发；锚点松弛单独处理（relax_anchors）。"""
    s = slack_summary(m)
    return any(s[k] > tol for k in ("shed_mwh", "surplus_mwh", "line_slack_max", "reserve_up_short_max", "reserve_dn_short_max"))


def relax_anchors(m, tol=1e-6) -> int:
    """锚点松弛被触发时：把锚点边界放宽到实际解的 E，并固定 sA=0，使防无解罚因子不进入对偶电价。返回放宽个数。"""
    n = 0
    for i in m.A:
        if i in getattr(m, "_hard_anchor_indices", ()):
            continue  # Never turn an authorized hard consistency constraint into a soft target.
        s, t = m._anchors[i][0], m._anchors[i][1]
        e = pyo.value(getattr(m, m._anchors[i][4])[s, t])
        if pyo.value(m.sA_up[i]) > tol:
            m.A_hi[i] = e; n += 1
        if pyo.value(m.sA_dn[i]) > tol:
            m.A_lo[i] = e; n += 1
        m.sA_up[i].fix(0); m.sA_dn[i].fix(0)
    return n


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
    from . import regulation
    r_up = np.array([m.dual[m.c_Rup[t]] for t in range(T)]) / dt
    down_enabled = bool(case.cfg.mode["reserve"].get("down_enabled", True))
    r_dn = (np.array([m.dual[m.c_Rdn[t]] for t in range(T)]) / dt
            if down_enabled else [None] * T)
    return dict(lmp=pi, lmp_decomp=lmp_dec, energy=energy, congestion=cong, mu_plus=mu_p, mu_minus=mu_m,
                reserve_up_price=r_up, reserve_dn_price=r_dn, reserve_down_enabled=down_enabled, regulation=regulation.prices(m))
