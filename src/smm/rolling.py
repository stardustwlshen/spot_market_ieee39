"""实时 15 min 滚动 SCED（甘肃方式，默认不设独立日内市场）。

时序（参数见 config/modes.yaml 的 MKT.realtime；窗口与时序见该文件注明的甘V3.2 条款）：
  日前（DA）出清 → 对每个 15 min 时段 t：窗口 [t, t+8)（2 h，日末截断），首时段用实际负荷/风光（假设做法）；
    - 煤电、燃气 CCGT 启停锁定日前结果；燃气 GT 启停在窗口 MIP 内按需决策；
    - 水电执行日前出清曲线（甘肃运营实施细则 第六十条，摘要来源）；
    - 储能按 realtime.storage.mode：
        sced_anchor（默认）：沿用日前封存报价进入 SCED，初值 = 实际 SOC，锚点约束跟随日前 SOC：
            anchor=hourly_eq（默认，B2 广东式）：窗口内各整点 E_RT = E_DA；
            anchor=window_end_pct（可选，B1 甘肃附录5 第9项）：窗口末 (1±x)E_DA；
        fixed_da：执行日前出清充放电曲线，价格接受者（山西第九十五条等）；
    - 固定整数 → LP 求 LMP；只执行首时段，SOC/库容/机组状态滚入下一时段。
  MKT.intraday.enabled=true 时另做事件式日内滚动（川V4.0 8.1，仅对比用）。
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from . import scuc
from .case import DACase, reserve_requirement
from .forecast import forecast_view

V = pyo.value


# ----------------------------------------------------------------------------- 系统状态
@dataclass
class SysState:
    t: int                                   # 下一个待执行的时段
    thermal: dict                            # g -> {online, p0, hours}
    storage: dict                            # s -> E (MWh)
    hydro: dict                              # h -> V (万 m³)
    hydro_used: dict                         # h -> 当日已发电量 MWh
    pumped: dict = field(default_factory=dict)       # p -> {V, ng, np, xg, xp}（上库库容 万m³、发电/抽水台数与工况）
    hydro_hist: dict = field(default_factory=dict)   # h -> 全天出库流量数组（已执行时段，m³/s；梯级下游时滞用）


def _pumped_init(cfg) -> dict:
    out = {}
    for p in cfg.units.get("pumped", []):
        im, N = p.get("init_mode", "idle"), int(p["n_units"])
        out[p["id"]] = dict(V=float(p["vup0"]), ng=N if im == "gen" else 0, np=N if im == "pump" else 0,
                            xg=int(im == "gen"), xp=int(im == "pump"))
    return out


def initial_state(cfg) -> SysState:
    th = {u["id"]: dict(u["init"]) for u in cfg.units["thermal"]}
    season = cfg.base["season"]
    hy = {h["id"]: float(h["seasons"][season]["v0"]) for h in cfg.units["hydro"]}
    st = {}
    for s in cfg.units["storage"]:
        st[s["id"]] = float(s["soc0"]) * float(s["ecap"])
    return SysState(0, th, st, hy, {h: 0.0 for h in hy}, _pumped_init(cfg), {h: np.full(cfg.T, np.nan) for h in hy})


def state_from_plan(cfg, plan: dict, t: int) -> SysState:
    """假设 [0, t) 完全按 plan（全天数组，如日前快照）执行时，时段 t 的系统状态（测试/诊断用）。"""
    if t == 0:
        return initial_state(cfg)
    dt = cfg.dt
    th = {}
    for u in cfg.units["thermal"]:
        g = u["id"]
        seq = [int(x) for x in plan["u"][g][:t]]
        on = seq[-1]
        n = 0
        for x in reversed(seq):
            if x != on:
                break
            n += 1
        hrs = n * dt + (u["init"]["hours"] if n == t and bool(u["init"]["online"]) == bool(on) else 0.0)
        th[g] = dict(online=bool(on), p0=float(plan["P"][g][t - 1]), hours=hrs)
    sto = {s["id"]: float(plan["E"][s["id"]][t - 1]) for s in cfg.units["storage"]}
    hy = {h: float(plan["Vh"][h][t - 1]) for h in plan["Vh"]}
    used = {h: float(np.sum(plan["Ph"][h][:t]) * dt) for h in plan["Ph"]}
    ps = {p: dict(V=float(plan["Vu"][p][t - 1]), ng=int(plan["ngen"][p][t - 1]), np=int(plan["npump"][p][t - 1]),
                  xg=int(plan["xg"][p][t - 1]), xp=int(plan["xp"][p][t - 1])) for p in plan.get("Vu", {})}
    hist = {}
    for h in plan["Ph"]:
        a = np.full(cfg.T, np.nan)
        a[:t] = np.asarray(plan["Qout"][h][:t]) if "Qout" in plan else np.nan
        hist[h] = a
    return SysState(t, th, sto, hy, used, ps, hist)


def _sub_cfg(cfg, **solver):
    base = copy.deepcopy(cfg.base)
    base["solver"].update(solver)
    return replace(cfg, base=base)


def make_window_case(da_case: DACase, cfg, t0: int, t1: int, issue_t: int, first_actual: bool, state: SysState,
                     fixed: dict, terminal: dict, stage: str) -> DACase:
    """构造 [t0, t1) 的滚动窗口算例：预测取 issue_t 时刻的滚动预测，初值取实际状态。"""
    net, prof = da_case.net, da_case.profiles
    per = range(t0, t1)
    load = forecast_view(prof, "load", issue_t, per, first_actual)
    load_bus = {b: net.load_share[b] * load for b in net.buses if net.load_share[b] > 0}
    ren_fc = {r["id"]: np.minimum(forecast_view(prof, r["id"], issue_t, per, first_actual), r["cap"])
              for r in cfg.units["renewable"]}
    tie = {k: (b, np.asarray(s)[t0:t1]) for k, (b, s) in da_case.tie.items()}
    up, dn, info = reserve_requirement(cfg, load, sum(ren_fc.values()))
    e_day = {h["id"]: float(h["seasons"][cfg.base["season"]]["e_day"]) - state.hydro_used[h["id"]]
             for h in cfg.units["hydro"]}
    init = dict(thermal=copy.deepcopy(state.thermal), storage=dict(state.storage), hydro=dict(state.hydro),
                pumped=copy.deepcopy(state.pumped), hydro_hist={h: a for h, a in state.hydro_hist.items()})
    return DACase(cfg, net, prof, da_case.bids, load_bus, ren_fc, tie, up, dn, info,
                  T=t1 - t0, t0=t0, init=init, fixed=fixed, hydro_e_budget=e_day, terminal=terminal, stage=stage)


# ----------------------------------------------------------------------------- 求解与快照
def snapshot(m) -> dict:
    """窗口模型的物理量（按窗口内相对时段 0..T−1）。"""
    md, case = m._md, m._case
    T = md.T
    rT = range(T)
    a = lambda f: np.array([V(f(t)) for t in rT])
    sn = dict(
        P={g: a(lambda t, g=g: m.P[g, t]) for g in md.thermal},
        u={g: np.rint(a(lambda t, g=g: m.u[g, t])).astype(int) for g in md.thermal},
        Pw={w: a(lambda t, w=w: m.Pw[w, t]) for w in md.ren},
        Ph={h: a(lambda t, d=d: sum(m.Ph[j, t] for j in d["units"])) for h, d in md.hydro.items()},
        delta={(j, z): np.rint(a(lambda t, j=j, z=z: m.delta[j, z, t])).astype(int) for (j, z) in m.JZ},
        Vh={h: a(lambda t, h=h: m.V[h, t]) for h in md.hydro},
        Qout={h: a(lambda t, h=h, d=d: d["water_rate"] * sum(m.Ph[j, t] for j in d["units"]) + m.Qspill[h, t]) for h, d in md.hydro.items()},
        Pg={p: a(lambda t, p=p: m.Pg[p, t]) for p in md.pumped},
        Pp={p: a(lambda t, p=p: m.Pp[p, t]) for p in md.pumped},
        xg={p: np.rint(a(lambda t, p=p: m.xg[p, t])).astype(int) for p in md.pumped},
        xp={p: np.rint(a(lambda t, p=p: m.xp[p, t])).astype(int) for p in md.pumped},
        ngen={p: np.rint(a(lambda t, p=p: m.ngen[p, t])).astype(int) for p in md.pumped},
        npump={p: np.rint(a(lambda t, p=p: m.npump[p, t])).astype(int) for p in md.pumped},
        Vu={p: a(lambda t, p=p: m.Vu[p, t]) for p in md.pumped},
        Pd={s: a(lambda t, s=s: m.Pd[s, t]) for s in md.storage},
        Pc={s: a(lambda t, s=s: m.Pc[s, t]) for s in md.storage},
        u_ch={s: np.rint(a(lambda t, s=s: m.u_ch[s, t])).astype(int) for s in md.storage},
        u_dis={s: np.rint(a(lambda t, s=s: m.u_dis[s, t])).astype(int) for s in md.storage},
        E={s: a(lambda t, s=s: m.E[s, t]) for s in md.storage},
        shed={n: a(lambda t, n=n: m.shed[n, t]) for n in md.buses},
        surplus=a(lambda t: m.surplus[t]),
        inj={n: a(lambda t, n=n: m.inj[n, t]) for n in md.buses},
        flow={l: a(lambda t, l=l: m.flow[l, t]) for l in md.lines},
        line_slack=float(max(V(m.sl[l, t]) for l in m.L for t in rT)),
        load_bus={n: np.asarray(case.load_bus.get(n, np.zeros(T)), float) for n in md.buses},
        ren_fc={w: np.asarray(v, float) for w, v in case.ren_fc.items()},
    )
    return sn


def clear_window(case: DACase, log=lambda *a: None) -> dict:
    """SCUC/MIP（仅剩余自由整数变量）→ 固定整数 → SCED(LP) → 对偶求价；有松弛时按定价罚因子重算价格。

    物理执行量取调度罚因子下的 LP 解（snapshot），价格取（必要时）定价罚因子重算后的对偶。
    """
    cfg = case.cfg
    lim = cfg.mode["price_limits"]
    t0 = time.perf_counter()
    m = scuc.build_model(case)
    info = scuc.solve_scuc(m)
    scuc.fix_integers(m)
    sced = scuc.solve_sced(m)
    sn = snapshot(m)
    slack = scuc.slack_summary(m)
    repriced = False
    n_relax = scuc.relax_anchors(m) if slack["anchor_slack_max_mwh"] > 1e-6 else 0   # 锚点防无解松弛不参与定价
    if scuc.has_slack(m):
        scuc.set_penalties(m, {k: float(v) for k, v in cfg.mode["pricing_penalties"].items()})
        repriced = True
    pcfg = cfg.mode.get("pumped", {})
    n_pf = scuc.fix_pumped_for_pricing(m, pcfg.get("pricing", "fixed"), float(pcfg.get("pricing_eps", 0.1)))
    if repriced or n_relax or n_pf:
        scuc.solve_sced(m)
    pr = scuc.extract_prices(m)
    pr["lmp_clamped"] = np.clip(pr["lmp"], lim["clear_min"], lim["clear_max"])
    return dict(model=m, snap=sn, prices=pr, slack=slack, repriced=repriced, anchor_relaxed=n_relax, mip=info, sced=sced,
                time_s=time.perf_counter() - t0)


# ----------------------------------------------------------------------------- 日内滚动
def _da_plan_arrays(da_res) -> dict:
    m = da_res.model
    return snapshot(m)


def storage_anchors(cfg, E_da: dict, t: int, t1: int) -> dict:
    """实时窗口 [t, t1) 的储能 SOC 锚点：{s: [(窗口内相对时段, lo, hi)]}（MWh）。"""
    sc = cfg.mode["realtime"]["storage"]
    kind = sc.get("anchor", "hourly_eq")
    x = float(sc.get("x", 0.05))
    out = {}
    for d in cfg.units["storage"]:
        s = d["id"]
        emin, emax = d["soc_min"] * d["ecap"], d["soc_max"] * d["ecap"]
        if kind == "window_end_pct":
            ks, xx = [t1 - 1], x
        elif kind == "hourly_eq":
            ks, xx = [k for k in range(t, t1) if (k + 1) % 4 == 0], 0.0
        else:
            raise ValueError(f"未知锚点方式 {kind}")
        lst = []
        for k in ks:
            e = float(E_da[s][k])
            lo, hi = max(emin, (1 - xx) * e), min(emax, (1 + xx) * e)   # 在满足 SOC 上下限的前提下浮动
            lst.append((k - t, lo, max(lo, hi)))
        out[s] = lst
    return out


def run_intraday_round(da_case, da_plan, cfg, state: SysState, tau: int, log=print) -> dict:
    """（可选，默认关闭）τ 时刻的一轮日内滚动（窗口至日末）：锁定煤电/CCGT；水电期末库容硬约束；
    储能日末 SOC 锚定日前出清日末值（同实时 window_end_pct 规则）。"""
    ic = cfg.mode["intraday"]
    T = cfg.T
    lock = set(ic["lock_types"])
    fixed_u = {}
    for u in cfg.units["thermal"]:
        if u["type"] in lock:
            for t in range(tau, T):
                fixed_u[(u["id"], t - tau)] = int(da_plan["u"][u["id"]][t])
    sc = cfg.mode["realtime"]["storage"]
    term = dict(hydro_vend=True, storage_anchor=storage_anchors(cfg, da_plan["E"], tau, T),
                anchor_penalty=float(sc.get("anchor_penalty", 1e5)))
    c2 = _sub_cfg(cfg, mip_rel_gap=ic.get("mip_rel_gap", 0.005), time_limit_s=ic.get("time_limit_s", 60),
                  warm_start=False, tighten=False)
    issue = max(0, tau - int(ic.get("lead_issue_periods", 1)))
    case = make_window_case(da_case, c2, tau, T, issue, False, state, {"u": fixed_u}, term, "ID")
    r = clear_window(case)
    r.update(tau=tau, issue_t=issue)
    log(f"[ID] 第 {tau//4:02d}:{(tau%4)*15:02d} 轮 窗口 {tau}–{T-1}  MIP {r['mip']['time_s']:.1f}s gap {r['mip']['gap']:.3%}"
        f"  {'(松弛→定价重算)' if r['repriced'] else ''}")
    return r


def run_rt_period(da_case, da_plan: dict, plan: dict, cfg, state: SysState, t: int) -> dict:
    """时段 t 的实时 SCED：窗口 [t, t+H)（日末截断），首时段实际值。
    da_plan = 日前出清（全天），plan = 参考计划（无日内时即日前；开日内时为最近一轮日内计划）。"""
    rc = cfg.mode["realtime"]
    T = cfg.T
    H = int(round(rc["lookahead_h"] * 60 / rc["interval_min"]))
    t1 = min(T, t + H)
    win = range(t, t1)
    lock = set(rc["lock_types"])
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    fixed = {"u": {(g, k - t): int(plan["u"][g][k]) for g in plan["u"] if types[g] in lock for k in win}}
    fix_power = {}
    if rc.get("hydro", "fixed_da") == "fixed_da":          # 水电执行日前曲线（台数、出力）
        fixed["delta"] = {(j, z, k - t): int(da_plan["delta"][(j, z)][k]) for (j, z) in da_plan["delta"] for k in win}
        fix_power["Ph"] = {(h, k - t): float(da_plan["Ph"][h][k]) for h in da_plan["Ph"] for k in win}
    sc = rc["storage"]
    mode = sc.get("mode", "sced_anchor")
    term = dict(anchor_penalty=float(sc.get("anchor_penalty", 1e5)))
    if mode == "fixed_da":                                 # 执行日前出清充放电曲线
        fixed["u_ch"] = {(s, k - t): int(da_plan["u_ch"][s][k]) for s in da_plan["u_ch"] for k in win}
        fixed["u_dis"] = {(s, k - t): int(da_plan["u_dis"][s][k]) for s in da_plan["u_dis"] for k in win}
        fix_power["Pd"] = {(s, k - t): float(da_plan["Pd"][s][k]) for s in da_plan["Pd"] for k in win}
        fix_power["Pc"] = {(s, k - t): float(da_plan["Pc"][s][k]) for s in da_plan["Pc"] for k in win}
    elif mode == "sced_anchor":                            # 进入 SCED，锚定日前 SOC
        term["storage_anchor"] = storage_anchors(cfg, da_plan["E"], t, t1)
    else:
        raise ValueError(f"未知储能实时模式 {mode}")
    pc = cfg.mode.get("pumped") or {}
    pmode = pc.get("rt_mode", "fixed_da")
    if da_plan.get("Pg"):
        # 抽蓄：工况与台数沿用日前（两种方式一致，转换需数分钟、不在 15 min 实时内改变工况）
        for key in ("xg", "xp", "ngen", "npump"):
            fixed[key] = {(p, k - t): int(da_plan[key][p][k]) for p in da_plan[key] for k in win}
        if pmode == "fixed_da":                            # 执行日前发电/抽水曲线（与水电 fixed_da 一致）
            fix_power["Pg"] = {(p, k - t): float(da_plan["Pg"][p][k]) for p in da_plan["Pg"] for k in win}
            fix_power["Pp"] = {(p, k - t): float(da_plan["Pp"][p][k]) for p in da_plan["Pp"] for k in win}
        elif pmode == "hourly_eq":                         # 出力实时可调，上库库容在整点等于日前（与储能 B2 一致）
            term["pumped_anchor"] = {p: [(k - t, float(da_plan["Vu"][p][k]), float(da_plan["Vu"][p][k]))
                                         for k in win if (k + 1) % 4 == 0] for p in da_plan["Vu"]}
            term["pumped_anchor_penalty"] = float(pc.get("anchor_penalty", 1e5))
        elif pmode == "band":                              # 广东 2026 细则 6.6.2.1：实时上库库容在日前结果 ±带宽 内优化
            bw = float(pc.get("rt_band_1e4m3", 20.0))
            term["pumped_anchor"] = {p: [(k - t, float(da_plan["Vu"][p][k]) - bw, float(da_plan["Vu"][p][k]) + bw)
                                         for k in win] for p in da_plan["Vu"]}
            term["pumped_anchor_penalty"] = float(pc.get("anchor_penalty", 1e5))
        else:
            raise ValueError(f"未知抽蓄实时模式 {pmode}")
    term["fix_power"] = fix_power
    c2 = _sub_cfg(cfg, mip_rel_gap=float(rc.get("mip_rel_gap", 1e-4)), time_limit_s=float(rc.get("time_limit_s", 30)),
                  warm_start=False, tighten=False)
    case = make_window_case(da_case, c2, t, t1, t, bool(rc.get("first_period_actual", True)), state, fixed, term, "RT")
    r = clear_window(case)
    r["t"] = t
    return r


def _abs_plan(snap: dict, t0: int, T: int, base: dict | None = None) -> dict:
    """把窗口快照写入全天数组（[t0, T) 覆盖，之前沿用 base）。"""
    out = copy.deepcopy(base) if base else {}
    for key in ("P", "u", "Pw", "Ph", "Vh", "Pd", "Pc", "u_ch", "u_dis", "E", "delta",
                "Qout", "Pg", "Pp", "xg", "xp", "ngen", "npump", "Vu"):
        d = out.setdefault(key, {})
        for k, arr in snap.get(key, {}).items():
            full = d.get(k)
            if full is None:
                full = np.zeros(T, dtype=arr.dtype)
            full = full.astype(np.result_type(full, arr))
            full[t0:t0 + len(arr)] = arr
            d[k] = full
    return out


def advance(state: SysState, snap: dict, cfg) -> SysState:
    """执行窗口首时段，把实际结果滚入下一时段的初始状态（SOC、库容、机组在线时长、出力）。"""
    dt = cfg.dt
    th = {}
    for g, s in state.thermal.items():
        on = bool(snap["u"][g][0])
        hrs = s["hours"] + dt if on == bool(s["online"]) else dt
        th[g] = dict(online=on, p0=float(snap["P"][g][0]), hours=hrs)
    sto = {s: float(snap["E"][s][0]) for s in state.storage}
    hy = {h: float(snap["Vh"][h][0]) for h in state.hydro}
    used = {h: state.hydro_used[h] + float(snap["Ph"][h][0]) * dt for h in state.hydro}
    ps = {p: dict(V=float(snap["Vu"][p][0]), ng=int(snap["ngen"][p][0]), np=int(snap["npump"][p][0]),
                  xg=int(snap["xg"][p][0]), xp=int(snap["xp"][p][0])) for p in state.pumped}
    hist = {}
    for h, a in state.hydro_hist.items():
        a = a.copy()
        if state.t < len(a):
            a[state.t] = float(snap["Qout"][h][0])
        hist[h] = a
    return SysState(state.t + 1, th, sto, hy, used, ps, hist)


@dataclass
class RollingResult:
    id_rounds: list = field(default_factory=list)      # 每轮日内 dict(tau, snap, prices, slack, ...)
    rt: list = field(default_factory=list)             # 每个实时时段 dict(t, snap(首时段), prices(首时段), ...)
    states: list = field(default_factory=list)         # 每个时段开始时的实际状态
    plans: list = field(default_factory=list)          # 每轮日内后的全天计划（绝对时段）
    time_s: dict = field(default_factory=dict)


def run_rolling(da_res, periods: int | None = None, log=print) -> RollingResult:
    """逐时段实时滚动；MKT.intraday.enabled 时在每个日内触发时刻先做一轮日内，实时开机沿用日内计划。"""
    case = da_res.case
    cfg = case.cfg
    T = cfg.T if periods is None else min(cfg.T, periods)
    ic = cfg.mode["intraday"]
    use_id = bool(ic.get("enabled", False))
    step = max(1, int(round(ic["interval_h"] / cfg.dt)))
    da_plan = _da_plan_arrays(da_res)
    plan = _abs_plan(da_plan, 0, cfg.T)
    state = initial_state(cfg)
    out = RollingResult()
    out.plans.append(plan)
    t_id = t_rt = 0.0
    for t in range(T):
        if use_id and t % step == 0:
            a = time.perf_counter()
            r = run_intraday_round(case, da_plan, cfg, state, t, log)
            t_id += time.perf_counter() - a
            plan = _abs_plan(r["snap"], t, cfg.T, plan)
            r["plan_abs"] = plan
            out.id_rounds.append(r)
            out.plans.append(plan)
        a = time.perf_counter()
        r = run_rt_period(case, da_plan, plan, cfg, state, t)
        t_rt += time.perf_counter() - a
        out.states.append(state)
        out.rt.append(dict(t=t, snap=r["snap"], prices=r["prices"], slack=r["slack"], repriced=r["repriced"],
                           anchor_relaxed=r["anchor_relaxed"], time_s=r["time_s"], mip_time=r["mip"]["time_s"],
                           plan_idx=len(out.plans) - 1))
        state = advance(state, r["snap"], cfg)
        if t % 4 == 3:
            log(f"[RT] 已执行至 {(t+1)//4:02d}:00（时段 {t}），首时段 LMP {r['prices']['lmp_clamped'][:, 0].min():.1f}"
                f"~{r['prices']['lmp_clamped'][:, 0].max():.1f}{'，含松弛' if r['repriced'] else ''}"
                f"{'，锚点松弛' if r['anchor_relaxed'] else ''}")
    out.states.append(state)
    out.time_s = dict(intraday=t_id, realtime=t_rt)
    return out


# ----------------------------------------------------------------------------- 结果表
def intraday_tables(da_res, rr: RollingResult) -> dict:
    cfg = da_res.case.cfg
    net = da_res.case.net
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    types.update({r["id"]: r["type"] for r in cfg.units["renewable"]})
    types.update({h["id"]: "hydro" for h in cfg.units["hydro"]})
    rows, prow, srow = [], [], []
    for r in rr.id_rounds:
        tau, sn = r["tau"], r["snap"]
        lab = f"{tau//4:02d}:{(tau%4)*15:02d}"
        T = len(sn["surplus"])
        for t in range(T):
            for g in sn["P"]:
                rows.append(dict(round=lab, tau=tau, period=tau + t, unit=g, type=types[g], P_mw=sn["P"][g][t], on=int(sn["u"][g][t])))
            for w in sn["Pw"]:
                rows.append(dict(round=lab, tau=tau, period=tau + t, unit=w, type=types[w], P_mw=sn["Pw"][w][t], on=1))
            for h in sn["Ph"]:
                non = sum(int(a[t]) for (j, z), a in sn["delta"].items() if j == h)
                rows.append(dict(round=lab, tau=tau, period=tau + t, unit=h, type="hydro", P_mw=sn["Ph"][h][t], on=non))
            for p in sn.get("Pg", {}):
                rows.append(dict(round=lab, tau=tau, period=tau + t, unit=p, type="pumped", P_mw=sn["Pg"][p][t] - sn["Pp"][p][t],
                                 on=int(sn["ngen"][p][t] + sn["npump"][p][t])))
            for s in sn["Pd"]:
                rows.append(dict(round=lab, tau=tau, period=tau + t, unit=s, type="storage", P_mw=sn["Pd"][s][t] - sn["Pc"][s][t], on=1))
                srow.append(dict(round=lab, tau=tau, period=tau + t, unit=s, P_dis=sn["Pd"][s][t], P_ch=sn["Pc"][s][t],
                                 u_dis=int(sn["u_dis"][s][t]), u_ch=int(sn["u_ch"][s][t]), E_mwh=sn["E"][s][t]))
            lmp = r["prices"]["lmp_clamped"][:, t]
            for i, b in enumerate(net.buses):
                prow.append(dict(round=lab, tau=tau, period=tau + t, bus=b, lmp=lmp[i]))
    plans = pd.DataFrame(rows)
    prices = pd.DataFrame(prow)
    sto = pd.DataFrame(srow)
    if not rr.id_rounds:
        cols = ["round", "tau", "period", "unit", "type", "P_mw", "on"]
        return dict(plans=pd.DataFrame(columns=cols), prices=pd.DataFrame(columns=["round", "tau", "period", "bus", "lmp"]),
                    storage=pd.DataFrame(columns=["round", "tau", "period", "unit", "P_dis", "P_ch", "u_dis", "u_ch", "E_mwh"]),
                    summary=pd.DataFrame(columns=["round", "tau", "window"]))
    summ = pd.DataFrame([dict(round=f"{r['tau']//4:02d}:{(r['tau']%4)*15:02d}", tau=r["tau"], window=f"{r['tau']}-{cfg.T-1}",
                              mip_s=r["mip"]["time_s"], gap=r["mip"]["gap"], obj=r["mip"]["objective"],
                              repriced=r["repriced"], **{k: v for k, v in r["slack"].items()},
                              lmp_avg_first_hour=float(r["prices"]["lmp_clamped"][:, :4].mean()))
                         for r in rr.id_rounds])
    return dict(plans=plans, prices=prices, storage=sto, summary=summ)


def rt_tables(da_res, rr: RollingResult) -> dict:
    """实时执行结果（每时段首时段）。"""
    cfg = da_res.case.cfg
    net = da_res.case.net
    dt = cfg.dt
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    types.update({r["id"]: r["type"] for r in cfg.units["renewable"]})
    buses_of = {u["id"]: u["bus"] for u in cfg.units["thermal"]}
    buses_of.update({r["id"]: r["bus"] for r in cfg.units["renewable"]})
    buses_of.update({h["id"]: h["bus"] for h in cfg.units["hydro"]})
    buses_of.update({s["id"]: s["bus"] for s in cfg.units["storage"]})
    buses_of.update({p["id"]: p["bus"] for p in cfg.units.get("pumped", [])})
    vtot = {p["id"]: float(p["v_total"]) for p in cfg.units.get("pumped", [])}
    urows, prow, lrow, srow, sysrow, psrow, hrow = [], [], [], [], [], [], []
    ptdf = net.ptdf
    for r, st in zip(rr.rt, rr.states):
        t, sn, pr = r["t"], r["snap"], r["prices"]
        for g in sn["P"]:
            urows.append(dict(period=t, unit=g, type=types[g], bus=buses_of[g], P_mw=sn["P"][g][0], on=int(sn["u"][g][0])))
        for w in sn["Pw"]:
            urows.append(dict(period=t, unit=w, type=types[w], bus=buses_of[w], P_mw=sn["Pw"][w][0], on=1,
                              avail_mw=sn["ren_fc"][w][0]))
        for h in sn["Ph"]:
            urows.append(dict(period=t, unit=h, type="hydro", bus=buses_of[h], P_mw=sn["Ph"][h][0],
                              on=sum(int(a[0]) for (j, z), a in sn["delta"].items() if j == h)))
        for s in sn["Pd"]:
            urows.append(dict(period=t, unit=s, type="storage", bus=buses_of[s], P_mw=sn["Pd"][s][0] - sn["Pc"][s][0], on=1))
            srow.append(dict(period=t, unit=s, P_dis=sn["Pd"][s][0], P_ch=sn["Pc"][s][0], u_dis=int(sn["u_dis"][s][0]),
                             u_ch=int(sn["u_ch"][s][0]), E_start=st.storage[s], E_mwh=sn["E"][s][0]))
        for p in sn.get("Pg", {}):
            xg, xp = int(sn["xg"][p][0]), int(sn["xp"][p][0])
            urows.append(dict(period=t, unit=p, type="pumped", bus=buses_of[p], P_mw=sn["Pg"][p][0] - sn["Pp"][p][0],
                              on=int(sn["ngen"][p][0] + sn["npump"][p][0])))
            psrow.append(dict(period=t, unit=p, bus=buses_of[p], P_gen=sn["Pg"][p][0], P_pump=sn["Pp"][p][0],
                              n_gen=int(sn["ngen"][p][0]), n_pump=int(sn["npump"][p][0]),
                              mode="发电" if xg else ("抽水" if xp else "停机"), V_start=st.pumped[p]["V"],
                              V_up=sn["Vu"][p][0], V_dn=vtot[p] - sn["Vu"][p][0]))
        for h in sn["Ph"]:
            hrow.append(dict(period=t, station=h, P_mw=sn["Ph"][h][0], V_start=st.hydro[h], V_1e4m3=sn["Vh"][h][0],
                             Q_out=sn["Qout"][h][0] if "Qout" in sn else np.nan))
        for i, b in enumerate(net.buses):
            prow.append(dict(period=t, bus=b, lmp=pr["lmp_clamped"][i, 0], lmp_raw=pr["lmp"][i, 0],
                             energy=pr["energy"][0], congestion=pr["congestion"][i, 0],
                             load_mw=sn["load_bus"][b][0], shed_mw=sn["shed"][b][0], inj_mw=sn["inj"][b][0]))
        for li, l in enumerate(net.branches.branch_id):
            lrow.append(dict(period=t, line=l, flow_mw=sn["flow"][l][0], mu_plus=pr["mu_plus"][li, 0], mu_minus=pr["mu_minus"][li, 0]))
        sysrow.append(dict(period=t, load_mw=sum(v[0] for v in sn["load_bus"].values()),
                           shed_mw=sum(v[0] for v in sn["shed"].values()), surplus_mw=sn["surplus"][0],
                           curtail_mw=sum(sn["ren_fc"][w][0] - sn["Pw"][w][0] for w in sn["Pw"]),
                           energy_price=pr["energy"][0], repriced=r["repriced"], anchor_relaxed=r["anchor_relaxed"],
                           solve_s=r["time_s"],
                           **{f"{k}_win": v for k, v in r["slack"].items()}))
    units = pd.DataFrame(urows)
    units["curtail_mw"] = np.where(units.type.isin(["wind", "pv"]), units.get("avail_mw", 0) - units.P_mw, 0.0)
    pcols = ["period", "unit", "bus", "P_gen", "P_pump", "n_gen", "n_pump", "mode", "V_start", "V_up", "V_dn"]
    return dict(units=units, prices=pd.DataFrame(prow), lines=pd.DataFrame(lrow), storage=pd.DataFrame(srow),
                system=pd.DataFrame(sysrow), pumped=pd.DataFrame(psrow, columns=pcols), hydro=pd.DataFrame(hrow))
