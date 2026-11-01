"""日内滚动优化 + 实时 15 min SCED（滚动窗口，首时段执行）。

时序（参数见 config/modes.yaml 的 MKT.intraday / MKT.realtime）：
  日前（DA）出清 → 对每个日内触发时刻 τ（默认每 1 h）：
      日内滚动：窗口 [τ, 96)，边界=τ 时刻的实际系统状态（上一轮实时执行结果），预测=τ−1 发布的滚动预测；
                煤电、燃气 CCGT 开停沿用日前（锁定），燃气 GT、水电台数、储能充放 0-1 状态重新优化；输出参考价（不结算）。
      实时：对 τ..τ+interval−1 的每个 15 min 时段 t，窗口 [t, t+8)（2 h 前瞻），首时段用实际负荷/风光；
                火电开停、水电台数取最近一轮日内计划；储能 0-1 状态按 storage_state 重新决定或沿用；
                固定整数 → LP 求 LMP；只执行首时段，SOC/库容/机组状态滚入下一时段。
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


def initial_state(cfg) -> SysState:
    th = {u["id"]: dict(u["init"]) for u in cfg.units["thermal"]}
    season = cfg.base["season"]
    hy = {h["id"]: float(h["seasons"][season]["v0"]) for h in cfg.units["hydro"]}
    st = {}
    for s in cfg.units["storage"]:
        st[s["id"]] = float(s["soc0"]) * float(s["ecap"])
    return SysState(0, th, st, hy, {h: 0.0 for h in hy})


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
    init = dict(thermal=copy.deepcopy(state.thermal), storage=dict(state.storage), hydro=dict(state.hydro))
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
    if scuc.has_slack(m):
        scuc.set_penalties(m, {k: float(v) for k, v in cfg.mode["pricing_penalties"].items()})
        scuc.solve_sced(m)
        repriced = True
    pr = scuc.extract_prices(m)
    pr["lmp_clamped"] = np.clip(pr["lmp"], lim["clear_min"], lim["clear_max"])
    return dict(model=m, snap=sn, prices=pr, slack=slack, repriced=repriced, mip=info, sced=sced,
                time_s=time.perf_counter() - t0)


# ----------------------------------------------------------------------------- 日内滚动
def _da_plan_arrays(da_res) -> dict:
    m = da_res.model
    return snapshot(m)


def run_intraday_round(da_case, da_plan, cfg, state: SysState, tau: int, log=print) -> dict:
    """τ 时刻触发的一轮日内滚动优化（窗口至日末）。"""
    ic = cfg.mode["intraday"]
    T = cfg.T
    lock = set(ic["lock_types"])
    fixed_u = {}
    for u in cfg.units["thermal"]:
        if u["type"] in lock:
            for t in range(tau, T):
                fixed_u[(u["id"], t - tau)] = int(da_plan["u"][u["id"]][t])
    term = dict(storage={s["id"]: s["soc_end"] * s["ecap"] for s in cfg.units["storage"]},
                hydro={h["id"]: h["seasons"][cfg.base["season"]]["v_end"] for h in cfg.units["hydro"]},
                penalty=cfg.mode.get("terminal_penalty", 1000))
    c2 = _sub_cfg(cfg, mip_rel_gap=ic.get("mip_rel_gap", 0.005), time_limit_s=ic.get("time_limit_s", 60),
                  warm_start=False, tighten=False)
    issue = max(0, tau - int(ic.get("lead_issue_periods", 1)))
    case = make_window_case(da_case, c2, tau, T, issue, False, state, {"u": fixed_u}, term, "ID")
    r = clear_window(case)
    r.update(tau=tau, issue_t=issue)
    log(f"[ID] 第 {tau//4:02d}:{(tau%4)*15:02d} 轮 窗口 {tau}–{T-1}  MIP {r['mip']['time_s']:.1f}s gap {r['mip']['gap']:.3%}"
        f"  {'(松弛→定价重算)' if r['repriced'] else ''}")
    return r


def run_rt_period(da_case, plan: dict, cfg, state: SysState, t: int) -> dict:
    """时段 t 的实时 SCED：窗口 [t, t+H)，首时段实际值；plan = 最近一轮日内计划（全天绝对时段数组）。"""
    rc = cfg.mode["realtime"]
    T = cfg.T
    H = int(round(rc["lookahead_h"] * 60 / rc["interval_min"]))
    t1 = min(T, t + H)
    fixed = {"u": {}, "delta": {}}
    for g in plan["u"]:
        for k in range(t, t1):
            fixed["u"][(g, k - t)] = int(plan["u"][g][k])
    for (j, z), arr in plan["delta"].items():
        for k in range(t, t1):
            fixed["delta"][(j, z, k - t)] = int(arr[k])
    if rc.get("storage_state", "redecide") == "fixed":
        fixed["u_ch"] = {(s, k - t): int(plan["u_ch"][s][k]) for s in plan["u_ch"] for k in range(t, t1)}
        fixed["u_dis"] = {(s, k - t): int(plan["u_dis"][s][k]) for s in plan["u_dis"] for k in range(t, t1)}
    term = dict(storage={s: float(plan["E"][s][t1 - 1]) for s in plan["E"]},
                hydro={h: float(plan["Vh"][h][t1 - 1]) for h in plan["Vh"]},
                penalty=cfg.mode.get("terminal_penalty", 1000))
    c2 = _sub_cfg(cfg, mip_rel_gap=1e-4, time_limit_s=30, warm_start=False, tighten=False)
    case = make_window_case(da_case, c2, t, t1, t, bool(rc.get("first_period_actual", True)), state, fixed, term, "RT")
    r = clear_window(case)
    r["t"] = t
    return r


def _abs_plan(snap: dict, t0: int, T: int, base: dict | None = None) -> dict:
    """把窗口快照写入全天数组（[t0, T) 覆盖，之前沿用 base）。"""
    out = copy.deepcopy(base) if base else {}
    for key in ("P", "u", "Pw", "Ph", "Vh", "Pd", "Pc", "u_ch", "u_dis", "E", "delta"):
        d = out.setdefault(key, {})
        for k, arr in snap[key].items():
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
    return SysState(state.t + 1, th, sto, hy, used)


@dataclass
class RollingResult:
    id_rounds: list = field(default_factory=list)      # 每轮日内 dict(tau, snap, prices, slack, ...)
    rt: list = field(default_factory=list)             # 每个实时时段 dict(t, snap(首时段), prices(首时段), ...)
    states: list = field(default_factory=list)         # 每个时段开始时的实际状态
    plans: list = field(default_factory=list)          # 每轮日内后的全天计划（绝对时段）
    time_s: dict = field(default_factory=dict)


def run_rolling(da_res, periods: int | None = None, log=print) -> RollingResult:
    """交替执行：日内第 k 轮（τ）→ 实时 τ..τ+interval−1 → 下一轮日内。"""
    case = da_res.case
    cfg = case.cfg
    T = cfg.T if periods is None else min(cfg.T, periods)
    ic = cfg.mode["intraday"]
    step = max(1, int(round(ic["interval_h"] / cfg.dt)))
    da_plan = _da_plan_arrays(da_res)
    plan = _abs_plan(da_plan, 0, cfg.T)
    state = initial_state(cfg)
    out = RollingResult()
    t_id = t_rt = 0.0
    for t in range(T):
        if t % step == 0:
            a = time.perf_counter()
            r = run_intraday_round(case, da_plan, cfg, state, t, log)
            t_id += time.perf_counter() - a
            plan = _abs_plan(r["snap"], t, cfg.T, plan)
            r["plan_abs"] = plan
            out.id_rounds.append(r)
            out.plans.append(plan)
        a = time.perf_counter()
        r = run_rt_period(case, plan, cfg, state, t)
        t_rt += time.perf_counter() - a
        out.states.append(state)
        out.rt.append(dict(t=t, snap=r["snap"], prices=r["prices"], slack=r["slack"], repriced=r["repriced"],
                           time_s=r["time_s"], mip_time=r["mip"]["time_s"]))
        state = advance(state, r["snap"], cfg)
        if t % 4 == 3:
            log(f"[RT] 已执行至 {(t+1)//4:02d}:00（时段 {t}），首时段 LMP {r['prices']['lmp_clamped'][:, 0].min():.1f}"
                f"~{r['prices']['lmp_clamped'][:, 0].max():.1f}{'，含松弛' if r['repriced'] else ''}")
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
    urows, prow, lrow, srow, sysrow = [], [], [], [], []
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
        for i, b in enumerate(net.buses):
            prow.append(dict(period=t, bus=b, lmp=pr["lmp_clamped"][i, 0], lmp_raw=pr["lmp"][i, 0],
                             energy=pr["energy"][0], congestion=pr["congestion"][i, 0],
                             load_mw=sn["load_bus"][b][0], shed_mw=sn["shed"][b][0], inj_mw=sn["inj"][b][0]))
        for li, l in enumerate(net.branches.branch_id):
            lrow.append(dict(period=t, line=l, flow_mw=sn["flow"][l][0], mu_plus=pr["mu_plus"][li, 0], mu_minus=pr["mu_minus"][li, 0]))
        sysrow.append(dict(period=t, load_mw=sum(v[0] for v in sn["load_bus"].values()),
                           shed_mw=sum(v[0] for v in sn["shed"].values()), surplus_mw=sn["surplus"][0],
                           curtail_mw=sum(sn["ren_fc"][w][0] - sn["Pw"][w][0] for w in sn["Pw"]),
                           energy_price=pr["energy"][0], repriced=r["repriced"], solve_s=r["time_s"],
                           **{f"{k}_win": v for k, v in r["slack"].items()}))
    units = pd.DataFrame(urows)
    units["curtail_mw"] = np.where(units.type.isin(["wind", "pv"]), units.get("avail_mw", 0) - units.P_mw, 0.0)
    return dict(units=units, prices=pd.DataFrame(prow), lines=pd.DataFrame(lrow), storage=pd.DataFrame(srow),
                system=pd.DataFrame(sysrow))
