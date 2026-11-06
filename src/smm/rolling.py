"""15分钟实时滚动：默认2小时前瞻，只执行首时段。

日前锁定类始终继承日前启停。可选日内市场优化剩余全天，开启交接时
快速火电继承最新日内启停，储能/抽蓄/水电逐主体采用有效日内参考。
无日内或显式关闭启停交接时保留历史实时自由GT模式。
状态价值是带符号的同日延续边际值；覆盖日末时额外价值为0，日末目标独立保留。
仅明确硬约束不可行才在同一求解预算内重试软带，不能以辅助算价或程序故障触发软化。
旧sced_anchor/fixed_da及水电window_end_da/fixed_da为显式可选策略。
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from . import scuc, handoff, hydro_commitment
from .case import DACase, reserve_requirement
from .forecast import forecast_view
from .natural_inflow import model_natural, natural_values, case_input_evidence
from .time_grid import period_label
from .storage_anchors import anchor_periods, exact_target, validate_anchor_config, policy_metadata, validate_actual_state

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
    hydro_p: dict = field(default_factory=dict)      # j -> 上个实际执行时段站级聚合出力MW
    hydro_online: dict = field(default_factory=dict) # j -> 上个实际执行时段在线台数
    pumped_used: dict = field(default_factory=dict)  # p -> executed positive gen/pump count increments
    pumped_hist: dict = field(default_factory=dict)  # p -> xg/xp absolute executed history; unknown=NaN
    hydro_commitment: dict = field(default_factory=dict)  # identified states, exact boundary and carried obligations
    hydro_pre_history: dict = field(default_factory=dict)  # absolute pre-day Qout intervals
    pumped_pre_history: dict = field(default_factory=dict)  # absolute pre-day xg/xp intervals
    storage_dispatch: dict = field(default_factory=dict)    # last actual mode/power, not a new ramp constraint
    pumped_dispatch: dict = field(default_factory=dict)     # last actual power; counts/modes remain in pumped
    continuation: dict = field(default_factory=dict)        # day boundary/hash provenance and effective day targets
    day_initial_hydro: dict = field(default_factory=dict)    # fixed effective day-start stock, not each RT window


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
    hp = {h["id"]: float(h["init_units_on"]) * float(h["init_p_unit"]) for h in cfg.units["hydro"]}
    hn = {h["id"]: int(h["init_units_on"]) for h in cfg.units["hydro"]}
    return SysState(0, th, st, hy, {h: 0.0 for h in hy}, _pumped_init(cfg),
                    {h: np.full(cfg.T, np.nan) for h in hy}, hp, hn,
                    {p["id"]: {"gen":0., "pump":0.} for p in cfg.units.get("pumped",[])},
                    {p["id"]: {k:np.full(cfg.T,np.nan) for k in ("xg","xp")} for p in cfg.units.get("pumped",[])},
                    hydro_commitment=hydro_commitment.initial_state(cfg), day_initial_hydro=dict(hy))


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
    hp = {j: float(plan["Ph"][j][t - 1]) for j in plan["Ph"]}
    hn = {j: sum(int(a[t - 1]) for (unit, z), a in plan["delta"].items() if unit == j) for j in hp}
    start = _pumped_init(cfg)
    pu, phist = {}, {}
    for p in ps:
        pu[p] = {kind: float(np.maximum(np.diff(np.r_[start[p][initial], plan[key][p][:t]]),0).sum())
                 for kind,key,initial in (("gen","ngen","ng"),("pump","npump","np"))}
        phist[p] = {key:np.r_[np.asarray(plan[key][p][:t],float), np.full(cfg.T-t,np.nan)] for key in ("xg","xp")}
    return SysState(t, th, sto, hy, used, ps, hist, hp, hn, pu, phist,
                    hydro_commitment=hydro_commitment.state_from_plan(cfg, plan, t))


def _sub_cfg(cfg, **solver):
    base = copy.deepcopy(cfg.base)
    base["solver"].update(solver)
    return replace(cfg, base=base)


def select_case_inputs(da_case,start_period,issue_period,first_actual,state,stage):
    from .natural_inflow import is_causal
    if not is_causal(da_case.cfg): return None
    if da_case.input_provider is None or da_case.day_start_utc is None:
        raise ValueError('v3 rolling requires the frozen causal provider and explicit absolute day clock')
    return da_case.input_provider.select_day(da_case.day_start_utc,issue_period,start_period,stage=stage,
        end_period=da_case.cfg.T,current_interval=first_actual,executed_through_period=state.t)


def make_window_case(da_case: DACase, cfg, t0: int, t1: int, issue_t: int, first_actual: bool, state: SysState,
                     fixed: dict, terminal: dict, stage: str, *, selected_inputs=None) -> DACase:
    """构造 [t0, t1) 的滚动窗口算例：预测取 issue_t 时刻的滚动预测，初值取实际状态。"""
    net, prof = da_case.net, da_case.profiles
    from .natural_inflow import is_causal
    from .fixed_load import nodal_demand, tie_mode
    per = range(t0,t1)
    if is_causal(cfg):
        if selected_inputs is None:
            selected_inputs=select_case_inputs(da_case,t0,issue_t,first_actual,state,stage)
        from .causal_types import utc_seconds,utc_iso
        day=utc_seconds(da_case.day_start_utc)
        if (selected_inputs.issue_time_utc!=utc_iso(day+issue_t*900)
                or selected_inputs.start_utc!=utc_iso(day+t0*900)
                or selected_inputs.end_utc!=utc_iso(day+cfg.T*900)):
            raise ValueError('rolling selected inputs must match this issue and complete remaining-day coverage')
        if da_case.input_provider.selection(selected_inputs.content_hash)!=selected_inputs:
            raise ValueError('rolling inputs differ from frozen source registry')
        observed=selected_inputs.sources[0]=='current_interval_perfect_measurement'
        if observed!=bool(first_actual): raise ValueError('rolling current-observation policy/source mismatch')
        start,end=utc_iso(day+t0*900),utc_iso(day+t1*900)
        load=selected_inputs.values('load',start,end,kind='load')
        ren_fc={r['id']:selected_inputs.values(r['id'],start,end,kind=r['type']) for r in cfg.units['renewable']}
        if any((ren_fc[r['id']]>float(r['cap'])+1e-8).any() for r in cfg.units['renewable']):
            raise ValueError('selected renewable availability exceeds capacity')
    else:
        if selected_inputs is not None: raise ValueError('legacy model cannot silently consume v3 inputs')
        from .forecast import validate_profiles
        validate_profiles(cfg,prof)
        load=forecast_view(prof,'load',issue_t,per,first_actual)
        ren_fc={r['id']:np.minimum(forecast_view(prof,r['id'],issue_t,per,first_actual),r['cap']) for r in cfg.units['renewable']}
    load_bus,fixed_load=nodal_demand(cfg,net,prof,load,periods=per,issue_t=issue_t,
                                    first_actual=first_actual,selected_inputs=selected_inputs)
    tie = {k: (b, np.asarray(s)[t0:t1]) for k, (b, s) in da_case.tie.items()}
    up, dn, info = reserve_requirement(cfg, sum(load_bus.values()) if tie_mode(cfg) == "fixed_load" else load, sum(ren_fc.values()))
    e_day = {h["id"]: float(h["seasons"][cfg.base["season"]]["e_day"]) - state.hydro_used[h["id"]]
             for h in cfg.units["hydro"] if h["seasons"][cfg.base["season"]].get("e_day") is not None}
    expected_hydro = {h["id"] for h in cfg.units["hydro"]}
    if set(state.hydro) != expected_hydro or any(not np.isfinite(float(v)) for v in state.hydro.values()):
        raise ValueError("滚动水电状态hydro必须完整包含所有启用水电的有限实际库容，不能退回季节初值")
    if set(state.hydro_p) != expected_hydro or set(state.hydro_online) != expected_hydro:
        raise ValueError("滚动水电状态缺少实际hydro_p/hydro_online，不能退回日前出力")
    hydro_state = hydro_commitment.normalize_state(cfg, state.hydro_commitment, t0, online_counts=state.hydro_online)
    init = dict(hydro_commitment=hydro_state, thermal=copy.deepcopy(state.thermal), storage=dict(state.storage), hydro=dict(state.hydro),
                hydro_used=dict(state.hydro_used),
                hydro_p=dict(state.hydro_p), hydro_online=dict(state.hydro_online),
                pumped=copy.deepcopy(state.pumped), pumped_used=copy.deepcopy(state.pumped_used),
                pumped_hist=copy.deepcopy(state.pumped_hist), hydro_hist={h: a for h, a in state.hydro_hist.items()})
    for key in ('hydro_pre_history', 'pumped_pre_history', 'storage_dispatch', 'pumped_dispatch', 'continuation', 'day_initial_hydro'):
        init[key] = copy.deepcopy(getattr(state, key))
    return DACase(cfg, net, prof, da_case.bids, load_bus, ren_fc, tie, up, dn, info,
                  T=t1 - t0, t0=t0, init=init, fixed=fixed, hydro_e_budget=e_day, terminal=terminal, stage=stage, fixed_load=fixed_load,
                  actual_state_required=bool(getattr(da_case,'actual_state_required',False)),
                  input_provider=da_case.input_provider,selected_inputs=selected_inputs,day_start_utc=da_case.day_start_utc)


# ----------------------------------------------------------------------------- 求解与快照
def hydro_diagnostics(m) -> dict:
    """Station-aggregate physical inputs for independent window water/ramp checks."""
    md, case = m._md, m._case
    soft_targets = (case.terminal or {}).get("hydro_tracking", {})
    targets = soft_targets or (case.terminal or {}).get("hydro_window_end", {})
    out = {}
    for h, d in md.hydro.items():
        units = d["units"]
        count = lambda j, t: sum(V(m.delta[j, z, t]) for z in range(len(md.hydro_units[j]["zones"])))
        on = [sum(count(j, t) for j in units) for t in m.T]
        up = [sum(md.hydro_units[j]["ramp"] * 60 * md.dt * count(j, t) for j in units) for t in m.T]
        down = [sum(md.hydro_units[j]["ramp"] * 60 * md.dt *
                    (md.hydro_units[j]["units0"] if t == 0 else count(j, t - 1)) for j in units) for t in m.T]
        out[h] = dict(p_start_mw=sum(md.hydro_units[j]["p0"] for j in units), v_start_1e4m3=d["v0"],
                      units_start=sum(md.hydro_units[j]["units0"] for j in units),
                      ramp_mw_per_min_per_unit={j: md.hydro_units[j]["ramp"] for j in units},
                      dt_h=md.dt, qin_m3s=[model_natural(md,h,t) for t in m.T],
                      upstream_in_m3s=[float(V(m._upstream_out(h, t))) for t in m.T],
                      units_on=on, ramp_up_limit_mw=up, ramp_down_limit_mw=down,
                      v_end_target_1e4m3=float(targets[h]) if h in targets else None,
                      terminal_kind=("soft_da_window_end" if h in soft_targets else "hard_da_window_end") if h in targets else None,
                      window_start=case.t0, window_end_exclusive=case.t0 + md.T,
                      remaining_energy_budget_mwh=d.get("e_day"),
                      spill_m3s=[float(V(m.Qspill[h, t])) for t in m.T], water_rate=d["water_rate"])
        qout = [float(d["water_rate"] * sum(V(m.Ph[j, t]) for j in units) + V(m.Qspill[h, t])) for t in m.T]
        limit = d.get("qout_max")
        out[h].update(qout_m3s=qout, qout_max_m3s=limit, qout_max_source=d.get("qout_max_source"),
                      qout_limit_active=limit is not None,
                      qout_limit_margin_m3s=[None if limit is None else limit-q for q in qout])
        anchor_indices = [i for i, a in enumerate(m._anchors) if a[0] == h and a[4] == "V"]
        out[h].update(tracking_slack_up_1e4m3=sum(V(m.sA_up[i]) for i in anchor_indices),
                      tracking_slack_down_1e4m3=sum(V(m.sA_dn[i]) for i in anchor_indices),
                      tracking_penalty_yuan_per_1e4m3=m._anchor_cost_coefficients[anchor_indices[0]] if anchor_indices else None)
    return out


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
        line_slack_by_line={l: a(lambda t, l=l: m.sl[l, t]) for l in md.lines},
        load_bus={n: np.asarray(case.load_bus.get(n, np.zeros(T)), float) for n in md.buses},
        fixed_load_bus={n: sum((np.asarray(v, float) for b, v in case.fixed_load.values() if b == n), np.zeros(T)) for n in md.buses},
        ren_fc={w: np.asarray(v, float) for w, v in case.ren_fc.items()},
        # Diagnostics only: retain each hard/soft anchor and its corresponding slacks.
        # E is MWh; Vu is 1e4 m3. These records never change optimization or dispatch.
        anchors=[dict(unit=m._anchors[i][0], period=m._anchors[i][1],
                      kind=m._anchors[i][4], hard=i in getattr(m, "_hard_anchor_indices", ()),
                      state_end_hour=(case.t0 + m._anchors[i][1] + 1) * md.dt,
                      lower=V(m.A_lo[i]), upper=V(m.A_hi[i]),
                      value=V(getattr(m, m._anchors[i][4])[m._anchors[i][0], m._anchors[i][1]]),
                      slack_up=V(m.sA_up[i]), slack_dn=V(m.sA_dn[i])) for i in m.A],
    )
    from .regulation import snapshot as regulation_snapshot
    sn["regulation"] = regulation_snapshot(m)
    sn["hydro_constraints"] = hydro_diagnostics(m)
    evidence=case_input_evidence(case)
    if evidence is not None:
        sn['input_provenance']=evidence
    from .results import reserve_snapshot, tracking_results
    sn["reserve"] = reserve_snapshot(m)
    sn["tracking"] = tracking_results(m).to_dict("records")
    from .asset_reachability import diagnostics as reachability_diagnostics
    sn["asset_reachability"] = reachability_diagnostics(m)
    from .hydro_daily_energy import diagnostics as daily_energy_diagnostics
    sn["hydro_daily_energy"] = daily_energy_diagnostics(m)
    sn.update(hydro_commitment.snapshot(m))
    return sn


def clear_window(case: DACase, log=lambda *a: None, *, solver_budget=None) -> dict:
    """Energy MIP → fixed integers → optional regulation allocation → final energy SCED.

    The allocation LP is not a repricing solve and its power/duals never execute.
    With regulation, reservation/slack-budget bounds intentionally differ from the preplan.
    Enabled auxiliary pricing uses a separate clone after the formal SCED; its
    quantities/states never execute. Physical slack penalties remain in the
    formal objective. Auxiliary raw/settlement-clipped prices are labelled separately.
    """
    cfg = case.cfg
    if cfg.mode.get("auxiliary_pricing", {}).get("enabled", False) and cfg.mode.get("regulation", {}).get("enabled", False):
        raise ValueError("auxiliary pricing currently requires regulation disabled")
    lim = cfg.mode["price_limits"]
    t0 = time.perf_counter()
    m = scuc.build_model(case)
    build_elapsed = time.perf_counter()-t0
    if case.stage == "RT":
        raw_total = cfg.base["solver"]["time_limit_s"]
        total = float(raw_total)
        if isinstance(raw_total, bool) or not np.isfinite(total) or total <= 0.:
            raise ValueError("RT total solver budget must be finite positive seconds")
        m._solver_budget = solver_budget if solver_budget is not None else scuc.SolverBudget(total_s=total, reserve_lp_s=min(10.,total*.1))
    from . import regulation
    from .stage_diagnostics import capture
    stages = {}
    regulation.prepare_energy(m)
    info = scuc.solve_scuc(m)
    stages["scuc_preplan"] = capture(m, "scuc_preplan", info)
    scuc.fix_integers(m)
    allocation = regulation.clear_reservations(m)
    info["regulation_allocation"] = allocation
    if allocation is not None:
        stages["agc_auxiliary"] = capture(m, "agc_auxiliary", allocation)
    try:
        sced = scuc.solve_sced(m)
        from .hydro_spill_tiebreak import select_hydro_water
        sced["hydro_water_selection"] = select_hydro_water(m, sced)
    except scuc.InfeasibleModelError as exc:
        # Retain only snapshots that really completed before the failed LP.
        exc.completed_stage_diagnostics = stages
        raise
    m._hydro_quantity_source = 'energy_sced'
    stages["energy_sced"] = capture(m, "energy_sced", sced)
    sn = snapshot(m)
    slack = scuc.slack_summary(m)
    pr = scuc.extract_prices(m)
    pr["lmp_clamped"] = np.clip(pr["lmp"], lim["clear_min"], lim["clear_max"])
    dispatch_prices = pr
    pricing_model, pricing_info, source = None, None, "standard_sced"
    if cfg.mode.get("auxiliary_pricing", {}).get("enabled", False):
        from .auxiliary_pricing import run_auxiliary_pricing, numeric_diagnostics
        aux = run_auxiliary_pricing(m)
        aux["auxiliary_prices"].update(selected_for_settlement=case.stage != "ID", settlement_applied=case.stage != "ID",
                                       settlement_applied_meaning="selected_clipped_price_rule_not_payment_execution")
        pricing_model, pricing_info = aux["model"], numeric_diagnostics(aux)
        pr = aux["auxiliary_prices"]["raw"]
        pr["lmp_clamped"] = np.clip(pr["lmp"], lim["clear_min"], lim["clear_max"])
        source = "bounded_auxiliary_pricing"
        stages["energy_sced"]["summary"]["prices_are_market_prices"] = False
        stages["auxiliary_pricing"] = capture(pricing_model, "auxiliary_pricing", aux["solve_info"])
        stages["auxiliary_pricing"]["summary"].update(status=("辅助算价（日内指示价，不执行、不结算）" if case.stage == "ID" else "辅助算价（数量不执行；价格用于结算）"), prices_are_market_prices=case.stage != "ID",
                                                    objective_kind="auxiliary_pricing_not_dispatch_cost", quantities_execute=False)
    value_window = []
    for i, (kind, unit, t_rel, lo, hi, label) in enumerate(getattr(m, "_state_bands", [])):
        x = V(getattr(m, kind)[unit, t_rel])
        v = next((val for k2, u2, t2, val in m._state_values if (k2, u2, t2) == (kind, unit, t_rel)), None)
        value_window.append(dict(kind=kind, unit=unit, period=case.t0 + t_rel, relative_period=t_rel, label=label,
                                 value_native=v, state=x, lower=lo, upper=hi,
                                 lower_binding=bool(x <= lo + 1e-6), upper_binding=bool(x >= hi - 1e-6),
                                 slack_up=V(m.sB_up[i]), slack_dn=V(m.sB_dn[i]),
                                 soft=i in getattr(m, "_state_band_soft_indices", set(m.B) if m._state_band_soft else set())))
        policy = getattr(m, "_state_band_terminal_policy", {"mode": "hard"})
        planned = bool(case.stage == "RT" and label == "day_end" and policy.get("mode") == "soft")
        factor = getattr(m, "_state_band_energy_factors", {}).get(i)
        deviation = V(m.sB_up[i]) + V(m.sB_dn[i])
        value_window[-1].update(terminal_policy=policy.get("mode", "hard"), planned_terminal_soft=planned,
            terminal_penalty_yuan_per_discharge_mwh=policy.get("penalty_yuan_per_discharge_mwh") if planned else None,
            discharge_energy_factor=factor if planned else None,
            deviation_discharge_mwh=deviation * factor if planned and factor is not None else None,
            terminal_penalty_cost=deviation * getattr(m, "_state_band_cost_coefficients", {}).get(i, 0.) if planned else None)
    sn["value_window"] = value_window
    sn["hydro_terminal_lookahead"] = scuc.hydro_terminal_lookahead_diagnostics(m)
    return dict(model=m, snap=sn, prices=pr, dispatch_prices=dispatch_prices,
                pricing_model=pricing_model, pricing_info=pricing_info, slack=slack, stage_diagnostics=stages,
                repriced=pricing_model is not None, repriced_meaning="separate_bounded_auxiliary_clone" if pricing_model is not None else "not_repriced",
                anchor_relaxed=0, anchor_slack_present=bool(slack["anchor_slack_max_mwh"] > 1e-6),
                pricing_source=source, settlement_price_rule="clip_raw_lmp_to_clear_limits",
                mip=info, sced=sced, build_time_s=build_elapsed,
                solver_budget=m._solver_budget.report() if hasattr(m,"_solver_budget") else None,
                non_solver_elapsed_s=max(0.,time.perf_counter()-t0-m._solver_budget.used_s) if hasattr(m,"_solver_budget") else None,
                time_s=time.perf_counter() - t0)


# ----------------------------------------------------------------------------- 日内滚动
def _da_plan_arrays(da_res) -> dict:
    m = da_res.model
    plan = snapshot(m)
    plan["state_values"] = scuc.state_values(m) if hasattr(m, "dual") else {"E": {}, "Vu": {}, "V": {}}
    return plan


def handoff_config(cfg) -> dict:
    """日内→实时交接开关（realtime.handoff_source / handoff / intraday_commit_rt）。"""
    return handoff.configuration(cfg)


def intraday_reference(da_plan: dict, ref: dict, id_round: dict, T: int, *, cfg=None) -> dict:
    """第三轮：用一轮日内结果更新实时参考（[τ, T) 覆盖，之前沿用原参考）。

    状态轨迹 E/Vu/Vh、稳定区台数 delta、出库 Qout 与日内SCED价值成包验证，
    相对时段映射到绝对时段。资源缺失或无效时尾部回退日前，不挪用旧日内轮的价值。
    未设跨日残值，因此末时段延续价值为0；终态目标对偶单列诊断，不是对偶本身为0。
    日末目标独立取日前值，水电保持最低日末库容，不用末端奖励替代目标约束。
    """
    if 'state_value_evidence' in id_round or id_round.get('models_retained') is False:
        from .result_retention import handoff_values
        vals, error, errors = handoff_values(id_round)
        return handoff.merge_intraday(da_plan, ref, id_round, T, vals, cfg=cfg, value_error=error, resource_errors=errors)
    m = id_round.get("model")
    vals, error, errors = {}, None, {}
    if m is None or not hasattr(m, "dual"):
        error = "intraday_SCED_model_or_duals_missing"
    else:
        try:
            vals = scuc.state_values(m, errors=errors)
        except KeyError as exc:
            error = f"intraday_SCED_dual_missing: {exc}"
    return handoff.merge_intraday(da_plan, ref, id_round, T, vals, cfg=cfg, value_error=error, resource_errors=errors)


def _hydro_bounds(cfg, h, state=None):
    season = h["seasons"][cfg.base["season"]]
    vmin, vmax, vend = float(season["vmin"]), float(season["vmax"]), float(season["v_end"])
    if h.get("regulation") == "run_of_river":
        vmax = float(season["v0"]); vmin = vmax - float(h.get("pondage", 0) or 0); vend = vmax
    if h.get('regulation') == 'run_of_river' and state is not None:
        targets = getattr(state, 'day_initial_hydro', {}) or getattr(state, 'continuation', {}).get('day_initial_hydro', {})
        if targets: vend = float(targets[h['id']])
    return vmin, vmax, vend, season


def value_method_terms(cfg, da_plan, t, t1, *, storage=True, pumped=True, hydro=True, day_end_plan=None, realtime=False,
                       actual_state=None, selected_inputs=None, day_start_utc=None) -> dict:
    """第二轮C/D项：窗口末状态价值 + 偏差带 + 日末约束（本函数仅生成常数）。

    实时value模式连接整数资产续程；soft只施加有限末态目标成本，hard保留严格可达域。

    窗口末 τ=t1−1：目标加 −v_τ·X_τ（v 取日前最终SCED对偶，scuc.state_values）；
    偏差带 |X_τ − X^DA_τ| ≤ δ_τ，δ_τ = min(δ0, R_τ)：δ0 = band_fraction×容量（假设值2%），
    R_τ 为按额定充/放（抽/发）功率或最小出库流量从 X_τ 仍可在日末回到日前日末值的可达距离。
    窗口覆盖日末（t1=T）时：E/Vu双侧目标、水电下限目标；RT soft从首次求解有限计罚，ID仍硬。
    """
    from .config import rt_terminal_policy
    policy = rt_terminal_policy(cfg.mode) if realtime else {"mode": "hard", "penalty_yuan_per_discharge_mwh": None}
    soft_terminal = policy["mode"] == "soft"
    vm = cfg.mode["realtime"].get("value_method", {})
    frac = float(vm.get("band_fraction", 0.02))
    hfrac = float(vm.get("hydro_band_fraction", frac))
    T, dt = cfg.T, cfg.dt
    tau = t1 - 1
    rel = tau - t
    n = T - 1 - tau
    day_end = t1 == T
    target_plan = da_plan if day_end_plan is None else day_end_plan
    values = da_plan.get("state_values", {})
    sv, bands = [], []

    def value(kind, unit):
        if day_end:
            return 0.  # 日末目标约束不是跨日残值；软化后也不能再奖励终态偏移。
        arr = values.get(kind, {}).get(unit, {}).get("value_native")
        if arr is None or len(arr) != T or not np.isfinite(float(arr[tau])):
            raise ValueError(f"{kind}.{unit} 缺少有效SCED状态价值，不能用零代替")
        return float(arr[tau])

    def add(kind, unit, x_da, target, lo_phys, hi_phys, d0, reach_lo, reach_hi, day_end_lo=None, day_end_hi=None):
        if day_end:
            lo = target if day_end_lo is None else day_end_lo
            hi = target if day_end_hi is None else day_end_hi
            lo, hi = min(max(lo, lo_phys), hi_phys), min(max(hi, lo_phys), hi_phys)
            label = "day_end"
        else:
            lo = max(x_da - d0, lo_phys, lo_phys if soft_terminal else reach_lo)
            hi = min(x_da + d0, hi_phys, hi_phys if soft_terminal else reach_hi)
            label = "band"
            if lo > hi:   # 数值保护：日前轨迹本身可达，取日前值
                lo = hi = min(max(x_da, lo_phys), hi_phys)
        sv.append((kind, unit, rel, value(kind, unit)))
        bands.append((kind, unit, rel, lo, hi, label))

    if storage:
        for d in cfg.units["storage"]:
            s = d["id"]
            if s not in da_plan.get("E", {}):
                continue
            emin, emax = d["soc_min"] * d["ecap"], d["soc_max"] * d["ecap"]
            x_da, target = float(da_plan["E"][s][tau]), float(target_plan["E"][s][T - 1])
            pmax = float(d["pmax"])
            add("E", s, x_da, target, emin, emax, frac * d["ecap"],
                target - n * dt * d["eta_c"] * pmax, target + n * dt * pmax / d["eta_d"])
    if pumped:
        for d in cfg.units.get("pumped", []):
            p = d["id"]
            if p not in da_plan.get("Vu", {}):
                continue
            vlo = max(float(d["vup_min"]), float(d["v_total"]) - float(d["vdn_max"]))
            vhi = min(float(d["vup_max"]), float(d["v_total"]) - float(d["vdn_min"]))
            N = int(d["n_units"])
            x_da, target = float(da_plan["Vu"][p][tau]), float(target_plan["Vu"][p][T - 1])
            add("Vu", p, x_da, target, vlo, vhi, frac * (vhi - vlo),
                target - n * dt * N * float(d["unit_pump"]) / float(d["e_pump"]),
                target + n * dt * N * float(d["unit_gen_max"]) / float(d["e_gen"]))
    if hydro:
        specs = {h["id"]: h for h in cfg.units["hydro"]}
        for h, d in specs.items():
            if h not in da_plan.get("Vh", {}):
                continue
            vmin, vmax, vend, season = _hydro_bounds(cfg, d, actual_state)
            x_da = float(da_plan["Vh"][h][tau])
            # R⁻：τ 之后按最小出库（max(生态流量, 日前锁定台数×稳定区下限×耗水率)）蓄水，日末仍能达到 v_end
            inflow = 0.
            for k in range(tau + 1, T):
                pmin = sum(int(arr[k]) * float(d["zones"][z][0]) for (j, z), arr in da_plan["delta"].items() if j == h)
                qmin = max(float(season["qeco"]), float(d["water_rate"]) * pmin)
                up = 0.
                if d.get("upstream") and d["upstream"] in da_plan.get("Qout", {}):
                    k_up = k - int(d.get("lag_periods", 0))
                    up = (float(da_plan["Qout"][d["upstream"]][k_up]) if k_up >= 0
                          else _pre_day_hydro_outflow(cfg, d["upstream"], actual_state, k_up))
                inflow += (natural_values(cfg,h,[k],selected_inputs=selected_inputs,day_start_utc=day_start_utc)[0] + up - qmin) * dt * .36
            add("V", h, x_da, vend, vmin, vmax, hfrac * (vmax - vmin), vend - inflow, vmax,
                day_end_lo=vend, day_end_hi=vmax)
    out = dict(state_value=sv, state_band=bands)
    if realtime:
        out["state_band_terminal_policy"] = policy
    return out


def storage_anchors(cfg, E_da: dict, t: int, t1: int) -> dict:
    """实时窗口 [t, t1) 的储能 SOC 锚点：{s: [(窗口内相对时段, lo, hi)]}（MWh）。"""
    sc = cfg.mode["realtime"]["storage"]
    kind = sc.get("anchor", "hourly_eq")
    x = float(sc.get("x", 0.05)) if kind == "window_end_pct" else 0.
    ks = anchor_periods(cfg, t, t1)
    out = {}
    for d in cfg.units["storage"]:
        s = d["id"]
        emin, emax = d["soc_min"] * d["ecap"], d["soc_max"] * d["ecap"]
        if kind == "window_end_pct":
            # 窗口覆盖日末时区间收为日前日末值（与日前 B 项、价值法日末约束一致；K 验证发现此前日末可漂移 ±x）
            ks, xx = [t1 - 1], (0.0 if t1 >= cfg.T else x)
        elif kind in ("hourly_eq", "two_hour_eq"):
            xx = 0.0
        else:
            raise ValueError(f"未知锚点方式 {kind}")
        lst = []
        for k in ks:
            if kind == "two_hour_eq":
                e = exact_target(E_da, s, k, emin, emax)
                lst.append((k - t, e, e))
                continue
            e = float(E_da[s][k])
            lo, hi = max(emin, (1 - xx) * e), min(emax, (1 + xx) * e)   # 在满足 SOC 上下限的前提下浮动
            lst.append((k - t, lo, max(lo, hi)))
        out[s] = lst
    return out


def pumped_anchors(cfg, Vu_da: dict, t: int, t1: int) -> dict:
    """实时窗口 [t, t1) 的抽蓄上库库容锚点（万m³），规则与储能 SOC 锚点相同（realtime.storage.anchor / x）。"""
    sc = cfg.mode["realtime"]["storage"]
    kind = sc.get("anchor", "hourly_eq")
    x = float(sc.get("x", 0.05)) if kind == "window_end_pct" else 0.
    ks = anchor_periods(cfg, t, t1)
    out = {}
    for d in cfg.units.get("pumped", []):
        p = d["id"]
        if p not in Vu_da and kind != "two_hour_eq":
            continue
        vlo = max(float(d["vup_min"]), float(d["v_total"]) - float(d["vdn_max"]))
        vhi = min(float(d["vup_max"]), float(d["v_total"]) - float(d["vdn_min"]))
        if kind == "window_end_pct":
            # 窗口覆盖日末时区间收为日前日末值（与日前 B 项、价值法日末约束一致；K 验证发现此前日末可漂移 ±x）
            ks, xx = [t1 - 1], (0.0 if t1 >= cfg.T else x)
        elif kind in ("hourly_eq", "two_hour_eq"):
            xx = 0.0
        else:
            raise ValueError(f"未知锚点方式 {kind}")
        lst = []
        for k in ks:
            if kind == "two_hour_eq":
                v = exact_target(Vu_da, p, k, vlo, vhi)
                lst.append((k - t, v, v))
                continue
            v = float(Vu_da[p][k])
            lo, hi = max(vlo, (1 - xx) * v), min(vhi, (1 + xx) * v)
            lst.append((k - t, lo, max(lo, hi)))
        out[p] = lst
    return out


def run_intraday_round(da_case, da_plan, cfg, state: SysState, tau: int, log=print) -> dict:
    """（可选，默认关闭）τ 时刻的一轮日内滚动（窗口至日末）：锁定煤电/CCGT；水电期末库容硬约束；
    储能日末 SOC 锚定日前出清日末值（同实时 window_end_pct 规则）。"""
    from .input_validation import validate_time_grid, intraday_lead_periods
    validate_time_grid(cfg)
    lead = intraday_lead_periods(cfg)
    ic = cfg.mode["intraday"]
    T = cfg.T
    lock = set(ic["lock_types"])
    fixed_u = {}
    for u in cfg.units["thermal"]:
        if u["type"] in lock:
            for t in range(tau, T):
                fixed_u[(u["id"], t - tau)] = int(da_plan["u"][u["id"]][t])
    sc = cfg.mode["realtime"]["storage"]
    if sc.get("mode", "value") == "value":
        # 价值法：日内窗口至日末，储能/抽蓄日末状态 = 日前日末值（同日前B项），水电 V_T ≥ v_end（hydro_vend）
        term = dict(hydro_vend=True, **value_method_terms(cfg, da_plan, tau, T, hydro=False, actual_state=state))
    else:
        term = dict(hydro_vend=True, storage_anchor=storage_anchors(cfg, da_plan["E"], tau, T),
                    anchor_penalty=float(sc.get("anchor_penalty", 1e5)))
        if sc.get("anchor") == "two_hour_eq":
            term["storage_anchor_hard"] = True
        if da_plan.get("Vu"):
            term["pumped_anchor"] = pumped_anchors(cfg, da_plan["Vu"], tau, T)
    c2 = _sub_cfg(cfg, mip_rel_gap=ic.get("mip_rel_gap", 0.005), time_limit_s=ic.get("time_limit_s", 60),
                  warm_start=False, tighten=False)
    issue = max(0, tau - lead)
    case = make_window_case(da_case, c2, tau, T, issue, False, state, {"u": fixed_u}, term, "ID")
    r = clear_window(case)
    r.update(tau=tau, issue_t=issue)
    target_gap = float(ic.get("mip_rel_gap", 0.005))
    actual_gap = float(r["mip"]["gap"])
    r["solution_quality"] = dict(termination=r["mip"].get("termination"), target_gap=target_gap,
        actual_gap=actual_gap, gap_target_met=bool(np.isfinite(actual_gap) and actual_gap <= target_gap + 1e-9),
        acceptance="existing_feasible_incumbent_policy_not_physical_failure")
    log(f"[ID] 第 {period_label(tau, cfg.dt)} 轮 窗口 {tau}–{T-1}  MIP {r['mip']['time_s']:.1f}s gap {r['mip']['gap']:.3%}"
        f"  {'(存在松弛；标准SCED原始对偶定价)' if any(v > 1e-6 for v in r['slack'].values()) else ''}")
    return r


def _pre_day_hydro_outflow(cfg, upstream, state=None, index=-1):
    """Match SCUC's declared pre-day generation outflow, with no invented spill."""
    if state is not None:
        from .state_bridge import hydro_before
        return hydro_before(cfg, state, upstream, index)
    spec = next((h for h in cfg.units.get("hydro", []) if h["id"] == upstream), None)
    if spec is None:
        raise ValueError(f"缺少上游水电{upstream}的日前前史设备申报")
    value = float(spec["water_rate"]) * float(spec["init_units_on"]) * float(spec["init_p_unit"])
    if not np.isfinite(value) or value < 0:
        raise ValueError(f"上游水电{upstream}日前前史出库必须有限非负")
    return value


def hydro_terminal_lookahead_terms(cfg, plan, state, t, t1, metadata, *, selected_inputs=None, day_start_utc=None):
    """Reference-continuation shortage preference, not a feasible future dispatch."""
    from .config import rt_terminal_policy
    selector = cfg.mode.get("realtime", {}).get("value_method", {}).get("hydro_lookahead", "off")
    if selector not in ("off", "reference_continuation"):
        raise ValueError("value_method.hydro_lookahead须为off/reference_continuation")
    if selector == "off" or rt_terminal_policy(cfg.mode)["mode"] != "soft" or t1 >= cfg.T:
        return None
    rows = {}
    for d in cfg.units.get("hydro", []):
        h = d["id"]
        _, _, vend, season = _hydro_bounds(cfg, d, state)
        own = handoff._array(plan.get("Qout", {}).get(h), cfg.T, f"lookahead.Qout.{h}", bounds=(0, np.inf))
        upstream = d.get("upstream")
        upref = (handoff._array(plan.get("Qout", {}).get(upstream), cfg.T,
                    f"lookahead.Qout.{upstream}", bounds=(0, np.inf)) if upstream else None)
        periods, outflows, arrivals, sources = [], [], [], []
        for k in range(t1, cfg.T):
            periods.append(k); outflows.append(float(own[k]))
            shifted = k - int(d.get("lag_periods", 0))
            if not upstream:
                arrival, source = 0., "none_or_before_day"
            elif shifted < 0:
                arrival = _pre_day_hydro_outflow(cfg, upstream, state, shifted)
                source = "pre_day_explicit_history" if getattr(state, 'continuation', {}) else "pre_day_initial_outflow"
            elif shifted < t:
                hist = state.hydro_hist.get(upstream)
                if hist is None or shifted >= len(hist) or not np.isfinite(float(hist[shifted])) or hist[shifted] < 0:
                    raise ValueError(f"水电前瞻缺少已执行上游出库 {upstream}[{shifted}]")
                arrival, source = float(hist[shifted]), "executed_history"
            else:
                arrival, source = float(upref[shifted]), "selected_reference"
            arrivals.append(arrival); sources.append(source)
        natural=natural_values(cfg,h,periods,selected_inputs=selected_inputs,day_start_utc=day_start_utc)
        recovery = sum(qn + up - q for qn, up, q in zip(natural, arrivals, outflows)) * cfg.dt * .36
        meta = metadata.get("V", {}).get(h, {})
        label = meta.get("trajectory_source", "day_ahead")
        rows[h] = dict(relative_period=t1-t-1, minimum_window_end_volume=vend-recovery,
            terminal_target=vend, reference_recovery_volume=recovery,
            reference_source="intraday" if str(label).startswith("intraday") else "day_ahead",
            reference_source_label=label, reference_round=meta.get("round"),
            reference_fallback_reason=meta.get("fallback_reason"),
            future_periods=periods, reference_outflows_m3s=outflows,
            upstream_arrivals_m3s=arrivals, upstream_arrival_sources=sources,
            upstream_reference_metadata=copy.deepcopy(metadata.get("V", {}).get(upstream)) if upstream else None,
            natural_inflow_m3s=(natural.tolist() if selected_inputs is not None else float(season['qin'])),
            natural_inflow_source=(selected_inputs.forecast_id if selected_inputs is not None else 'legacy_season_constant'),dt_h=cfg.dt)
    return rows


def value_asset_reachability(cfg, da_plan, window_end):
    """Integer physical continuation with explicit hard/soft DA day-end policy."""
    if window_end >= cfg.T or not (cfg.units["storage"] or cfg.units.get("pumped")):
        return None
    relative = cfg.T - 1 - window_end
    targets = {}
    for family, key, name in (("storage", "E", "storage_targets"), ("pumped", "Vu", "pumped_targets")):
        targets[name] = {}
        for unit in cfg.units.get(family, []):
            uid = unit["id"]
            array = handoff._array(da_plan.get(key, {}).get(uid), cfg.T, f"day_ahead.{key}.{uid}")
            target = float(array[-1])
            targets[name][uid] = [(relative, target, target)]
    from .config import rt_terminal_policy
    policy = rt_terminal_policy(cfg.mode)
    return dict(target_policy="day_end_soft" if policy["mode"] == "soft" else "day_end",
                penalty_yuan_per_discharge_mwh=policy["penalty_yuan_per_discharge_mwh"],
                start_period=window_end, end_period_exclusive=cfg.T, **targets)


def run_rt_period(da_case, da_plan: dict, plan: dict, cfg, state: SysState, t: int, *, ref: dict | None = None,
                  id_active: bool = False) -> dict:
    """时段 t 的实时 SCED：窗口 [t, t+H)（日末截断），首时段实际值。
    da_plan = 日前出清（全天），plan = 参考计划（无日内时即日前；开日内时为最近一轮日内计划）。
    ref = 价值法参考（状态轨迹 + 价值 v，intraday_reference 生成；None = 日前）。id_active = 本时段前已有日内结果。"""
    rc = cfg.mode["realtime"]
    validate_anchor_config(cfg)
    from .input_validation import validate_time_grid
    validate_time_grid(cfg)
    state_deviations = []
    if rc["storage"].get("mode", "value") == "sced_anchor":
        # 第二轮C项：硬→软回退后起点可能已偏离锚点，只记录偏差，不抛异常、不重置实际库存
        state_deviations = validate_actual_state(cfg, da_plan, state, t, tolerate=True)
    if state.t != t or not 0 <= t < cfg.T:
        raise ValueError("实时窗口起点必须等于实际状态时标，且位于当前运行日内")
    T = cfg.T
    H = int(round(rc["lookahead_h"] * 60 / rc["interval_min"]))
    t1 = min(T, t + H)
    win = range(t, t1)
    from .natural_inflow import is_causal
    if is_causal(cfg) and not bool(rc.get('first_period_actual',True)):
        raise ValueError('v3 real execution requires explicit current_interval_perfect_measurement until a deviation correction model exists')
    current_inputs=select_case_inputs(da_case,t,t,bool(rc.get('first_period_actual',True)),state,'RT')
    lock = set(rc["lock_types"])
    vplan, reference_metadata, ho, use_ref = handoff.selected_reference(cfg, da_plan, ref, id_active)
    inherited = set()
    if id_active and ho["source"] == "intraday" and ho["commitment"] and ho["commit_rt"] == "inherit":
        # 第三轮：快速机组（intraday.free_types 中的火电类型）由日内按最新预测决定启停，实时继承（不再在 2h 窗口内自行开停）
        inherited = set(cfg.mode["intraday"].get("free_types", [])) & {u["type"] for u in cfg.units["thermal"]}
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    fixed = {"u": {}}
    thermal_reference_u = {}
    for g, typ in types.items():
        if typ not in lock and typ not in inherited:
            continue
        chosen, meta = da_plan, dict(source="day_ahead", round=None, fallback_reason=None)
        if typ not in lock:
            chosen = ref or da_plan
            meta = copy.deepcopy((ref or {}).get("reference_metadata", {}).get("u", {}).get(g,
                dict(source=(ref or {}).get("reference_source", "intraday" if ref else "day_ahead"), round=None,
                     fallback_reason=None if ref else "intraday_reference_missing")))
        try:
            values = handoff._array(chosen.get("u", {}).get(g), T, f"reference.u.{g}", integer=True, binary=True)
        except ValueError as exc:
            if chosen is da_plan:
                raise
            values = handoff._array(da_plan.get("u", {}).get(g), T, f"day_ahead.u.{g}", integer=True, binary=True)
            meta = dict(source="day_ahead", round=None, fallback_reason=str(exc))
        thermal_reference_u[g] = np.rint(values[t:t1]).astype(int)
        fixed["u"].update({(g, k - t): int(values[k]) for k in win})
        reference_metadata["u"][g] = meta
    fix_power = {}
    hydro_mode = rc.get("hydro", "value")
    if hydro_mode not in ("value", "fixed_da", "window_end_da", "sced"):
        raise ValueError(f"未知水电实时模式 {hydro_mode}")
    hydro_reference_delta = {}
    if hydro_mode in ("value", "fixed_da", "window_end_da"):
        # 旧 fixed_da/window_end_da 仍使用DA，不能混入ID台数与固定DA出力冲突。
        dsrc = vplan if hydro_mode == "value" else da_plan
        normalized_delta = {jz: handoff._array(a, T, f"reference.delta.{jz}", integer=True)
                            for jz, a in dsrc["delta"].items()}
        fixed["delta"] = {(j, z, k - t): int(normalized_delta[(j, z)][k]) for (j, z) in normalized_delta for k in win}
        hydro_reference_delta = {jz: np.asarray(a[t:t1], dtype=int).copy() for jz, a in normalized_delta.items()}
        if hydro_mode != "value":
            reference_metadata["V"] = {h: dict(trajectory_source="day_ahead", value_source=None, round=None, fallback_reason=None)
                                       for h in da_plan.get("Vh", {})}
    if hydro_mode == "fixed_da":
        fix_power["Ph"] = {(h, k - t): float(da_plan["Ph"][h][k]) for h in da_plan["Ph"] for k in win}
    sc = rc["storage"]
    mode = sc.get("mode", "value")
    term = dict(anchor_penalty=float(sc.get("anchor_penalty", 1e5)))
    if mode == "sced_anchor" and sc.get("anchor") == "two_hour_eq":
        term["storage_anchor_hard"] = True
        term["storage_anchor_policy"] = policy_metadata(cfg)
        if t1 < T:
            term["asset_reachability"] = dict(start_period=t1,end_period_exclusive=T,
                storage_targets=storage_anchors(cfg,da_plan["E"],t1,T),
                pumped_targets=pumped_anchors(cfg,da_plan.get("Vu",{}),t1,T))
    tracking = rc.get("tracking_policy") if hydro_mode == "window_end_da" else None
    if tracking is not None and tracking.get("mode") != "legacy":
        if tracking.get("mode") != "soft_deliverable_energy" or hydro_mode != "window_end_da" or mode != "sced_anchor":
            raise ValueError("soft tracking requires window_end_da hydro and sced_anchor storage")
        term["tracking_policy"] = dict(tracking)
    if hydro_mode == "window_end_da":
        term["hydro_tracking" if "tracking_policy" in term else "hydro_window_end"] = {h: float(da_plan["Vh"][h][t1 - 1]) for h in da_plan["Vh"]}
    if mode == "fixed_da":                                 # 执行日前出清充放电曲线
        fixed["u_ch"] = {(s, k - t): int(da_plan["u_ch"][s][k]) for s in da_plan["u_ch"] for k in win}
        fixed["u_dis"] = {(s, k - t): int(da_plan["u_dis"][s][k]) for s in da_plan["u_dis"] for k in win}
        fix_power["Pd"] = {(s, k - t): float(da_plan["Pd"][s][k]) for s in da_plan["Pd"] for k in win}
        fix_power["Pc"] = {(s, k - t): float(da_plan["Pc"][s][k]) for s in da_plan["Pc"] for k in win}
    elif mode == "sced_anchor":                            # 进入 SCED，锚定日前 SOC
        term["storage_anchor"] = storage_anchors(cfg, da_plan["E"], t, t1)
    elif mode != "value":
        raise ValueError(f"未知储能实时模式 {mode}")
    if mode == "value" or hydro_mode == "value":
        vt = value_method_terms(cfg, vplan, t, t1, actual_state=state, storage=mode == "value", pumped=mode == "value",
                                hydro=hydro_mode == "value", day_end_plan=da_plan, realtime=True,
                                selected_inputs=current_inputs,day_start_utc=da_case.day_start_utc)
        term.update(vt)
        if hydro_mode == "value":
            hydro_lookahead = hydro_terminal_lookahead_terms(cfg, vplan, state, t, t1, reference_metadata,
                selected_inputs=current_inputs,day_start_utc=da_case.day_start_utc)
            if hydro_lookahead:
                term["hydro_terminal_lookahead"] = hydro_lookahead
        term["state_band_penalty"] = float(rc.get("value_method", {}).get("fallback_penalty", 1.0e5))
        if mode == "value":
            certificate = value_asset_reachability(cfg, da_plan, t1)
            if certificate is not None:
                term["asset_reachability"] = certificate
    if mode != "value" and (da_plan.get("Pg") or (sc.get("anchor") == "two_hour_eq" and cfg.units.get("pumped"))):
        # 抽蓄实时规则同储能（realtime.storage）：fixed_da = 执行日前抽发曲线（工况、台数、功率）；
        # sced_anchor = 抽/发 0-1 工况与台数在窗口 MIP 内决策，上库库容按储能同一锚点规则（B2 hourly_eq / B1 window_end_pct）。
        if mode == "fixed_da":
            for key in ("xg", "xp", "ngen", "npump"):
                fixed[key] = {(p, k - t): int(da_plan[key][p][k]) for p in da_plan[key] for k in win}
            fix_power["Pg"] = {(p, k - t): float(da_plan["Pg"][p][k]) for p in da_plan["Pg"] for k in win}
            fix_power["Pp"] = {(p, k - t): float(da_plan["Pp"][p][k]) for p in da_plan["Pp"] for k in win}
        else:
            term["pumped_anchor"] = pumped_anchors(cfg, da_plan["Vu"], t, t1)
    term["fix_power"] = fix_power
    from .hydro_daily_energy import reference_terms as daily_energy_reference_terms
    daily_reference = daily_energy_reference_terms(cfg, da_plan, vplan, hydro_mode, t1, reference_metadata)
    if daily_reference is not None:
        term["hydro_daily_energy_reference"] = daily_reference
    c2 = _sub_cfg(cfg, mip_rel_gap=float(rc.get("mip_rel_gap", 0.01)), time_limit_s=float(rc.get("time_limit_s", 30)),
                  warm_start=False, tighten=False)
    total = c2.base["solver"]["time_limit_s"]
    solver_budget = scuc.SolverBudget(total_s=total, reserve_lp_s=min(10., total * .1))
    case = make_window_case(da_case, c2, t, t1, t, bool(rc.get("first_period_actual", True)), state, fixed, term, "RT", selected_inputs=current_inputs)
    fallback = None
    try:
        try:
            r = clear_window(case, solver_budget=solver_budget)
        except scuc.InfeasibleModelError as hard_exc:
            # 第二轮C项回退：先硬后软。偏差带/日末约束/两小时硬锚点在本窗口改为高罚软约束（假设值罚因子），
            # 记录原因并继续滚动；软化后仍失败才视为真实错误。
            # Planned day-end targets are already soft: retrying them cannot
            # repair a physical infeasibility and must not be called fallback.
            planned_soft = term.get("state_band_terminal_policy", {}).get("mode") == "soft"
            hard_bands = any(not (planned_soft and len(row) > 5 and row[5] == "day_end")
                             for row in term.get("state_band", []))
            softenable = ((hard_bands or bool(term.get("storage_anchor_hard")))
                          and hard_exc.phase in ("scuc", "formal_sced"))
            if not softenable:
                raise
            soft = copy.deepcopy(term)
            penalty = float(rc.get("value_method", {}).get("fallback_penalty", 1.0e5))
            soft["state_band_soft"] = True
            # Keep the selected hard/soft asset suffix when current bands soften.
            # Planned terminal softness never becomes an emergency penalty.
            # Legacy hard-clock fallback is unchanged.
            if soft.pop("storage_anchor_hard", False):
                soft.pop("asset_reachability", None)
                soft["anchor_penalty"] = penalty
                soft["pumped_anchor_penalty"] = penalty
            fallback = dict(t=t, window_end_exclusive=t1, reason=f"{type(hard_exc).__name__}: {str(hard_exc)[:300]}",
                            penalty=penalty, softened=("state_band" if term.get("state_band") else "") +
                            ("+two_hour_eq" if term.get("storage_anchor_hard") else ""))
            case = make_window_case(da_case, c2, t, t1, t, bool(rc.get("first_period_actual", True)), state, fixed, soft, "RT", selected_inputs=current_inputs)
            solver_budget.allowance()
            r = clear_window(case, solver_budget=solver_budget)
            fallback["band_slack_max"] = r["slack"].get("state_band_slack_max", 0.)
            fallback["anchor_slack_max"] = r["slack"].get("anchor_slack_max_mwh", 0.)
    except Exception as exc:
        from .live_runner import RunCancelled
        if isinstance(exc, (RunCancelled, scuc.SolverBudgetExceededError)):
            raise
        details = {h: dict(initial_V_1e4m3=state.hydro[h], initial_P_mw=state.hydro_p[h],
                           initial_online=state.hydro_online[h],
                           hard_target_V_1e4m3=term.get("hydro_window_end", {}).get(h)) for h in state.hydro}
        anchor_details = dict(policy=sc.get("anchor"), hard=bool(term.get("storage_anchor_hard")),
                              actual_storage_MWh=state.storage, actual_pumped=state.pumped,
                              actual_pumped_starts_used=state.pumped_used,
                              remaining_day_asset_certificate="asset_reachability" in term,
                              storage_targets=term.get("storage_anchor"), pumped_targets=term.get("pumped_anchor"))
        from .hydro_daily_energy import limits as daily_energy_limits
        daily_limits = daily_energy_limits(cfg)
        daily_context = {h: dict(daily_minimum_mwh=v, executed_before_mwh=state.hydro_used[h],
                                remaining_minimum_mwh=max(0., v-state.hydro_used[h])) for h,v in daily_limits.items()}
        raise RuntimeError(f"RT窗口[{t},{t1})出清失败，储能/抽蓄锚点={anchor_details}；仅明确硬约束不可行时允许受预算限制的软化；水电模式={hydro_mode}，实际初态/硬目标={details}；水电硬日电量下限={daily_context}（不得软化）；"
                           f"原始异常 {type(exc).__name__}: {exc}") from exc
    r["t"] = t
    targets = []
    for h, diag in r["snap"]["hydro_constraints"].items():
        power = np.asarray(r["snap"]["Ph"][h])
        delta_p = np.diff(np.r_[diag["p_start_mw"], power])
        violation = max(0., float(np.max(delta_p - diag["ramp_up_limit_mw"])),
                        float(np.max(-delta_p - diag["ramp_down_limit_mw"])))
        target = diag["v_end_target_1e4m3"]
        actual_end = float(r["snap"]["Vh"][h][-1])
        targets.append(dict(unit=h, window_start=t, window_end_exclusive=t1,
                            target_V_1e4m3=target, actual_terminal_V_1e4m3=actual_end,
                            terminal_error_1e4m3=None if target is None else actual_end-target,
                            terminal_kind=diag["terminal_kind"],
                            tracking_slack_up_1e4m3=diag["tracking_slack_up_1e4m3"],
                            tracking_slack_down_1e4m3=diag["tracking_slack_down_1e4m3"],
                            current_V_1e4m3=diag["v_start_1e4m3"], current_P_mw=diag["p_start_mw"],
                            ramp_violation_max_mw=violation))
    r["hydro_window_targets"] = targets  # forecast horizon terminal state, not executed state
    r["fallback"] = fallback
    r["state_deviations"] = state_deviations
    r["reference_metadata"] = reference_metadata
    r["thermal_reference_u"] = thermal_reference_u
    r["hydro_reference_delta"] = hydro_reference_delta
    r["handoff_config"] = {k: v for k, v in ho.items() if k != "levels"}
    r["handoff_config"]["intraday_attempted"] = bool(id_active)
    r["handoff_config"]["levels"] = {stage: {key: sorted(value) for key, value in groups.items()} for stage, groups in ho["levels"].items()}
    for w in r["snap"].get("value_window", []):
        kind, unit = w["kind"], w["unit"]
        meta = reference_metadata[kind][unit]
        w.update(reference_source=meta["trajectory_source"], value_source=meta["value_source"],
                 reference_round=meta["round"], reference_fallback_reason=meta["fallback_reason"],
                 reference_state=float(vplan[handoff.STATE_KEYS[kind]][unit][w["period"]]))
    sources = {w["reference_source"] for w in r["snap"].get("value_window", [])}
    r["reference_source"] = next(iter(sources)) if len(sources) == 1 else ("mixed" if sources else "day_ahead")
    return r


def failed_intraday_reference(da_plan, tau, reason):
    """An explicit failed attempt replaces every resource bundle with DA."""
    ref = copy.deepcopy(da_plan)
    ref["reference_source"] = "day_ahead"
    ref["reference_metadata"] = {}
    why = f"intraday_physical_infeasible@{tau}: {reason}"
    for kind, key in handoff.STATE_KEYS.items():
        ref["reference_metadata"][kind] = {unit: dict(trajectory_source="day_ahead", value_source="day_ahead",
            round=None, attempted_round=tau, fallback_reason=why) for unit in da_plan.get(key, {})}
    ref["reference_metadata"]["u"] = {unit: dict(source="day_ahead", round=None, attempted_round=tau,
        fallback_reason=why) for unit in da_plan.get("u", {})}
    return ref


def _abs_plan(snap: dict, t0: int, T: int, base: dict | None = None) -> dict:
    """把窗口快照写入全天数组（[t0, T) 覆盖，之前沿用 base）。"""
    out = copy.deepcopy(base) if base else {}
    for key in ("P", "u", "Pw", "Ph", "Vh", "Pd", "Pc", "u_ch", "u_dis", "E", "delta",
                "Qout", "Pg", "Pp", "xg", "xp", "ngen", "npump", "Vu", "hydro_u", "hydro_start", "hydro_stop"):
        d = out.setdefault(key, {})
        for k, arr in snap.get(key, {}).items():
            full = d.get(k)
            if full is None:
                full = np.zeros(T, dtype=arr.dtype)
            full = full.astype(np.result_type(full, arr))
            full[t0:t0 + len(arr)] = arr
            d[k] = full
    if 'hydro_commitment' in snap and 'hydro_commitment' not in out:
        out['hydro_commitment'] = copy.deepcopy(snap['hydro_commitment'])
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
    hp = {j: float(a[0]) for j, a in snap["Ph"].items()}
    hn = {j: sum(int(a[0]) for (unit, z), a in snap["delta"].items() if unit == j) for j in hp}
    pu, phist = copy.deepcopy(state.pumped_used), copy.deepcopy(state.pumped_hist)
    for d in cfg.units.get("pumped", []):
        p = d["id"]
        if p not in pu or p not in phist:
            raise ValueError(f"抽蓄 {p} 缺少实际启动累计量/工况历史")
        for kind, key, capkey in (("gen","ng","max_gen_starts"),("pump","np","max_pump_starts")):
            pu[p][kind] += max(ps[p][key]-state.pumped[p][key],0)
            if pu[p][kind] > d["n_units"]*d[capkey]:
                raise ValueError(f"抽蓄 {p} 实际 {kind} 启动次数超过日上限")
        for key in ("xg","xp"):
            phist[p][key][state.t] = ps[p][key]
    commitment = hydro_commitment.advance(cfg, state.hydro_commitment, snap, state.t)
    return SysState(state.t + 1, th, sto, hy, used, ps, hist, hp, hn, pu, phist,
                    hydro_commitment=commitment,
                    hydro_pre_history=copy.deepcopy(state.hydro_pre_history),
                    pumped_pre_history=copy.deepcopy(state.pumped_pre_history),
                    storage_dispatch={s: dict(P_ch=float(snap['Pc'][s][0]), P_dis=float(snap['Pd'][s][0]),
                        u_ch=int(snap['u_ch'][s][0]), u_dis=int(snap['u_dis'][s][0]), source='executed')
                        for s in state.storage} if state.continuation else copy.deepcopy(state.storage_dispatch),
                    pumped_dispatch={p: dict(P_gen=float(snap['Pg'][p][0]), P_pump=float(snap['Pp'][p][0]),
                        xg=ps[p]['xg'], xp=ps[p]['xp'], source='executed')
                        for p in state.pumped} if state.continuation else copy.deepcopy(state.pumped_dispatch),
                    continuation=copy.deepcopy(state.continuation), day_initial_hydro=copy.deepcopy(state.day_initial_hydro))


@dataclass
class RollingResult:
    id_rounds: list = field(default_factory=list)      # 每轮日内 dict(tau, snap, prices, slack, ...)
    id_failures: list = field(default_factory=list)  # confirmed physical failures, never successful ID rounds
    rt: list = field(default_factory=list)             # 每个实时时段 dict(t, snap(首时段), prices(首时段), ...)
    states: list = field(default_factory=list)         # 每个时段开始时的实际状态
    plans: list = field(default_factory=list)          # 每轮日内后的全天计划（绝对时段）
    time_s: dict = field(default_factory=dict)
    id_failed_stages: list = field(default_factory=list)  # completed snapshots before a failed ID stage
    actual_state_required: bool = False
    id_models_retained: bool = True  # False requires hash-verified numeric ID evidence.


def run_rolling(da_res, periods: int | None = None, log=print, *, progress_callback=None, should_cancel=None,
                initial_actual_state: SysState | None = None, retain_id_models: bool = True) -> RollingResult:
    """逐时段实时滚动；MKT.intraday.enabled 时在每个日内触发时刻先做一轮日内，实时开机沿用日内计划。"""
    if type(retain_id_models) is not bool:
        raise ValueError("retain_id_models must be boolean")
    from .input_validation import validate_requested_periods
    validate_requested_periods(periods)
    if not bool(getattr(da_res.case.cfg, 'base', {}).get('solver', {}).get('storage_binary', True)):
        raise ValueError('storage_binary=false仅用于日前/独立LP研究对照，不支持滚动实际执行')
    case = da_res.case
    if getattr(case, 'actual_state_required', False) and getattr(case, 'init', None) is None:
        raise ValueError('Continuous-day rolling requires its admitted actual initial state')
    cfg = case.cfg
    from .input_validation import validate_time_grid
    validate_time_grid(cfg)
    T = cfg.T if periods is None else min(cfg.T, periods)
    ic = cfg.mode["intraday"]
    use_id = bool(ic.get("enabled", False))
    step = int(round(ic["interval_h"] / cfg.dt)) if use_id else 1
    da_plan = _da_plan_arrays(da_res)
    plan = _abs_plan(da_plan, 0, cfg.T)
    ref = None
    ho = handoff_config(cfg)
    from .state_bridge import state_from_case_init, validate_system_state, pack_state, digest
    if initial_actual_state is not None:
        state = copy.deepcopy(initial_actual_state)
        validate_system_state(cfg, state, expected_t=0)
        if getattr(case, 'init', None) is None or digest(pack_state(cfg, state)) != digest(pack_state(cfg, state_from_case_init(cfg, case.init))):
            raise ValueError('DA and rolling must consume the same explicit actual initial state')
    elif getattr(case, 'init', None) is not None:
        state = state_from_case_init(cfg, case.init)
    else:
        state = initial_state(cfg)  # Legacy single-day only; series always supplies case.init.
    out = RollingResult(actual_state_required=getattr(case, 'actual_state_required', False), id_models_retained=retain_id_models)
    out.plans.append(plan)
    t_id = t_rt = 0.0
    for t in range(T):
        if should_cancel and should_cancel():
            from .live_runner import RunCancelled
            raise RunCancelled(f"Cancelled after {len(out.rt)} executed periods")
        if use_id and t % step == 0:
            a = time.perf_counter()
            try:
                r = run_intraday_round(case, da_plan, cfg, state, t, log)
            except scuc.InfeasibleModelError as exc:
                if exc.phase not in ("scuc", "formal_sced"):
                    raise
                failure = dict(tau=t, phase=exc.phase, solver_status=exc.solver_status,
                               reason=str(exc), fallback="day_ahead", status="failed_physical_infeasible")
                out.id_failures.append(failure)
                out.id_failed_stages.append(dict(tau=t, stages=getattr(exc, 'completed_stage_diagnostics', {})))
                # Discard stale ID references without resetting actual executed states.
                plan = _abs_plan(da_plan, 0, cfg.T)
                ref = failed_intraday_reference(da_plan, t, str(exc))
                log(f"[ID失败] 时段 {t}，{exc.phase}/{exc.solver_status}；该轮不计成功，实时回退日前参考：{exc}")
            else:
                plan = _abs_plan(r["snap"], t, cfg.T, plan)
                r["plan_abs"] = plan
                if not retain_id_models:
                    from .result_retention import capture_state_evidence, detach_id_models
                    capture_state_evidence(r)
                if ho["source"] == "intraday":
                    ref = intraday_reference(da_plan, ref or da_plan, r, cfg.T, cfg=cfg)
                if not retain_id_models:
                    r = detach_id_models(r)
                out.id_rounds.append(r)
            t_id += time.perf_counter() - a
            out.plans.append(plan)
            if progress_callback:
                progress_callback({"phase": "id_round", "rr": out, "tau": t, "completed": len(out.rt), "total": T})
        a = time.perf_counter()
        r = run_rt_period(case, da_plan, plan, cfg, state, t, ref=ref, id_active=bool(out.id_rounds or out.id_failures))
        t_rt += time.perf_counter() - a
        out.states.append(state)
        out.rt.append(dict(t=t, snap=r["snap"], prices=r["prices"], dispatch_prices=r["dispatch_prices"],
                           pricing_info=r["pricing_info"], solver_budget=r.get("solver_budget"), build_time_s=r.get("build_time_s"),
                           non_solver_elapsed_s=r.get("non_solver_elapsed_s"), repriced_meaning=r["repriced_meaning"], slack=r["slack"], repriced=r["repriced"],
                           hydro_window_targets=r["hydro_window_targets"],
                           fallback=r.get("fallback"), state_deviations=r.get("state_deviations", []),
                           anchor_relaxed=r["anchor_relaxed"], anchor_slack_present=r["anchor_slack_present"],
                           pricing_source=r["pricing_source"], time_s=r["time_s"], mip_time=r["mip"]["time_s"],
                           mip=dict(r["mip"]), sced=dict(r["sced"]),
                           regulation_allocation=r["mip"].get("regulation_allocation", {}),
                           stage_diagnostics=r.get("stage_diagnostics", {}),
                           plan_idx=len(out.plans) - 1,
                           reference_source=r.get("reference_source", "day_ahead"),
                           reference_metadata=r.get("reference_metadata", {}),
                           hydro_reference_delta=r.get("hydro_reference_delta", {}),
                           thermal_reference_u=r.get("thermal_reference_u", {}),
                           handoff_config=r.get("handoff_config", {})))
        state = advance(state, r["snap"], cfg)
        out.time_s = dict(intraday=t_id, realtime=t_rt)
        if progress_callback:
            progress_callback({"phase": "rt_window", "rr": out, "round": out.rt[-1], "completed": len(out.rt), "total": T})
        if int((t + 1) * cfg.dt) > int(t * cfg.dt):
            log(f"[RT] 已执行至 {period_label(t + 1, cfg.dt)}（时段 {t}），首时段 LMP {r['prices']['lmp_clamped'][:, 0].min():.1f}"
                f"~{r['prices']['lmp_clamped'][:, 0].max():.1f}{'，含物理松弛' if any(r['slack'][k] > 1e-6 for k in ('shed_mwh', 'surplus_mwh', 'line_slack_max', 'reserve_up_short_max', 'reserve_dn_short_max')) else ''}"
                f"{'，锚点松弛' if r['slack']['anchor_slack_max_mwh'] > 1e-6 else ''}")
        if not retain_id_models:
            # Numeric histories are already complete; release the last RT model
            # and periodically collect Pyomo expression/rule cycles.
            del r
            if (t + 1) % 8 == 0 or t + 1 == T:
                import gc
                gc.collect()
    out.states.append(state)
    out.time_s = dict(intraday=t_id, realtime=t_rt)
    return out


# ----------------------------------------------------------------------------- 结果表
def _first_hour_mean(prices, dt):
    """Time-weighted first-hour mean, including a fractional final period."""
    values = np.asarray(prices, dtype=float)
    weights = np.clip(1.0 - np.arange(values.shape[1]) * dt, 0., dt)
    return float(np.average(values.mean(axis=0), weights=weights))


def intraday_tables(da_res, rr: RollingResult) -> dict:
    cfg = da_res.case.cfg
    net = da_res.case.net
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    types.update({r["id"]: r["type"] for r in cfg.units["renewable"]})
    types.update({h["id"]: "hydro" for h in cfg.units["hydro"]})
    rows, prow, srow = [], [], []
    for r in rr.id_rounds:
        tau, sn = r["tau"], r["snap"]
        lab = f"{period_label(tau, cfg.dt)}"
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
                prow.append(dict(round=lab, tau=tau, period=tau + t, bus=b, lmp=lmp[i],
                                 load_mw=sn["load_bus"][b][t], fixed_load_mw=sn.get("fixed_load_bus", {}).get(b, np.zeros(T))[t]))
    plans = pd.DataFrame(rows)
    prices = pd.DataFrame(prow)
    sto = pd.DataFrame(srow)
    if not rr.id_rounds:
        cols = ["round", "tau", "period", "unit", "type", "P_mw", "on"]
        return dict(plans=pd.DataFrame(columns=cols), prices=pd.DataFrame(columns=["round", "tau", "period", "bus", "lmp", "load_mw", "fixed_load_mw"]),
                    storage=pd.DataFrame(columns=["round", "tau", "period", "unit", "P_dis", "P_ch", "u_dis", "u_ch", "E_mwh"]),
                    summary=pd.DataFrame(columns=["round", "tau", "window", "mip_s", "gap", "obj", "repriced", "lmp_avg_first_hour", "termination", "gap_target", "gap_target_met"]))
    summ = pd.DataFrame([dict(round=period_label(r["tau"], cfg.dt), tau=r["tau"], window=f"{r['tau']}-{cfg.T-1}",
                              mip_s=r["mip"]["time_s"], gap=r["mip"]["gap"], obj=r["mip"]["objective"],
                              repriced=r["repriced"], **{k: v for k, v in r["slack"].items()},
                              termination=r.get("solution_quality", {}).get("termination", r["mip"].get("termination")),
                              gap_target=r.get("solution_quality", {}).get("target_gap"),
                              gap_target_met=r.get("solution_quality", {}).get("gap_target_met"),
                              lmp_avg_first_hour=_first_hour_mean(r["prices"]["lmp_clamped"], cfg.dt))
                         for r in rr.id_rounds])
    return dict(plans=plans, prices=prices, storage=sto, summary=summ)


def rt_tables(da_res, rr: RollingResult) -> dict:
    """实时执行结果（每时段首时段）。"""
    cfg = da_res.case.cfg
    net = da_res.case.net
    dt = cfg.dt
    # Each stored window contributes only its executed first period. Fail closed
    # before zip truncation or an overlapping/reordered window can look complete.
    if [r["t"] for r in rr.rt] != list(range(len(rr.rt))):
        raise ValueError("RT output requires unique contiguous executed periods starting at zero")
    if len(rr.states) < len(rr.rt) or any(st.t != r["t"] for r, st in zip(rr.rt, rr.states)):
        raise ValueError("RT output requires the matching actual initial state for every executed period")
    specs = {u["id"]: u for family in ("thermal", "renewable", "hydro", "storage", "pumped") for u in cfg.units.get(family, [])}
    rates = dict(zip(net.branches.branch_id, net.branches.rate_mw))
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
            urows.append(dict(period=t, unit=g, type=types[g], bus=buses_of[g], P_mw=sn["P"][g][0], on=int(sn["u"][g][0]),
                              startup=int(sn["u"][g][0] > 0 and not st.thermal[g]["online"]),
                              avail_mw=specs[g]["pmax"] * int(sn["u"][g][0])))
        for w in sn["Pw"]:
            urows.append(dict(period=t, unit=w, type=types[w], bus=buses_of[w], P_mw=sn["Pw"][w][0], on=1,
                              avail_mw=sn["ren_fc"][w][0], startup=0))
        for h in sn["Ph"]:
            urows.append(dict(period=t, unit=h, type="hydro", bus=buses_of[h], P_mw=sn["Ph"][h][0],
                              on=sum(int(a[0]) for (j, z), a in sn["delta"].items() if j == h), startup=0,
                              avail_mw=specs[h]["unit_pmax"] * sn["hydro_constraints"][h]["units_on"][0]))
        for s in sn["Pd"]:
            urows.append(dict(period=t, unit=s, type="storage", bus=buses_of[s], P_mw=sn["Pd"][s][0] - sn["Pc"][s][0], on=1, startup=0, avail_mw=specs[s]["pmax"]))
            srow.append(dict(period=t, unit=s, bus=buses_of[s], soc=sn["E"][s][0]/specs[s]["ecap"], P_dis=sn["Pd"][s][0], P_ch=sn["Pc"][s][0], u_dis=int(sn["u_dis"][s][0]),
                             u_ch=int(sn["u_ch"][s][0]), E_start=st.storage[s], E_mwh=sn["E"][s][0]))
        for p in sn.get("Pg", {}):
            xg, xp = int(sn["xg"][p][0]), int(sn["xp"][p][0])
            urows.append(dict(period=t, unit=p, type="pumped", bus=buses_of[p], P_mw=sn["Pg"][p][0] - sn["Pp"][p][0],
                              on=int(sn["ngen"][p][0] + sn["npump"][p][0]),
                              startup=max(int(sn["ngen"][p][0])-st.pumped[p]["ng"],0)+max(int(sn["npump"][p][0])-st.pumped[p]["np"],0),
                              avail_mw=specs[p]["unit_gen_max"] * specs[p]["n_units"]))
            psrow.append(dict(period=t, unit=p, bus=buses_of[p], P_gen=sn["Pg"][p][0], P_pump=sn["Pp"][p][0],
                              n_gen=int(sn["ngen"][p][0]), n_pump=int(sn["npump"][p][0]),
                              mode="发电" if xg else ("抽水" if xp else "停机"), V_start=st.pumped[p]["V"],
                              V_up=sn["Vu"][p][0], V_dn=vtot[p] - sn["Vu"][p][0],
                              E_equiv_mwh=(sn["Vu"][p][0]-max(specs[p]["vup_min"],vtot[p]-specs[p]["vdn_max"])) * specs[p]["e_gen"]))
        for h in sn["Ph"]:
            hd = sn["hydro_constraints"][h]
            hrow.append(dict(period=t, station=h, bus=buses_of[h], units_on=hd["units_on"][0],
                             Q_gen=hd["water_rate"] * sn["Ph"][h][0], Q_spill=hd["spill_m3s"][0],
                             Q_in=hd["qin_m3s"][0]+hd["upstream_in_m3s"][0],
                             Q_natural=hd["qin_m3s"][0], Q_upstream=hd["upstream_in_m3s"][0],
                             natural_input_source=(sn['input_provenance']['sources'][0] if sn.get('input_provenance') else 'legacy_season_constant'),
                             natural_forecast_id=(sn['input_provenance']['forecast_id'] if sn.get('input_provenance') else None),
                             natural_issue_time_utc=(sn['input_provenance']['issue_time_utc'] if sn.get('input_provenance') else None), regulation=specs[h].get("regulation", "reservoir"), P_start_mw=st.hydro_p[h], units_start=st.hydro_online[h],
                             P_mw=sn["Ph"][h][0], V_start=st.hydro[h], V_1e4m3=sn["Vh"][h][0],
                             Q_out=sn["Qout"][h][0] if "Qout" in sn else np.nan,
                             qout_m3s=hd["qout_m3s"][0], qout_max_m3s=hd["qout_max_m3s"],
                             qout_max_source=hd["qout_max_source"], qout_limit_active=hd["qout_limit_active"],
                             qout_limit_margin_m3s=hd["qout_limit_margin_m3s"][0]))
        for tid, (bus, schedule) in da_res.case.tie.items():
            urows.append(dict(period=t, unit=tid, type="tie", bus=bus, P_mw=float(schedule[t]),
                              on=1, startup=0, avail_mw=float(schedule[t])))
        dp = r.get("dispatch_prices", pr)
        source = r.get("pricing_source", "standard_sced")
        for i, b in enumerate(net.buses):
            prow.append(dict(period=t, bus=b, pricing_source=source, raw_price_source=source, settlement_price_source=source+"_clipped",
                             physical_quantity_source="standard_sced", flow_source="standard_sced", dual_source=source,
                             dispatch_raw_lmp=dp["lmp"][i,0], dispatch_energy=dp["energy"][0], dispatch_congestion=dp["congestion"][i,0], lmp=pr["lmp_clamped"][i, 0], lmp_raw=pr["lmp"][i, 0],
                             energy=pr["energy"][0], congestion=pr["congestion"][i, 0],
                             load_mw=sn["load_bus"][b][0], fixed_load_mw=sn.get("fixed_load_bus", {}).get(b, [0.])[0], shed_mw=sn["shed"][b][0], served_load_mw=sn["load_bus"][b][0]-sn["shed"][b][0], inj_mw=sn["inj"][b][0]))
        for li, l in enumerate(net.branches.branch_id):
            lrow.append(dict(period=t, line=l, flow_source="standard_sced", dual_source=source, flow_mw=sn["flow"][l][0], rate_mw=rates[l], loading=abs(sn["flow"][l][0])/rates[l],
                              slack_mw=sn.get("line_slack_by_line", {}).get(l, [np.nan])[0], mu_plus=pr["mu_plus"][li, 0], mu_minus=pr["mu_minus"][li, 0]))
        sysrow.append(dict(period=t, fixed_load_mw=sum(v[0] for v in sn.get("fixed_load_bus", {}).values()), load_mw=sum(v[0] for v in sn["load_bus"].values()),
                           shed_mw=sum(v[0] for v in sn["shed"].values()), surplus_mw=sn["surplus"][0],
                           curtail_mw=sum(sn["ren_fc"][w][0] - sn["Pw"][w][0] for w in sn["Pw"]),
                           energy_price=pr["energy"][0], reserve_up_price=pr["reserve_up_price"][0], reserve_dn_price=pr["reserve_dn_price"][0], repriced=r["repriced"], anchor_relaxed=r["anchor_relaxed"],
                           anchor_slack_present=bool(r["slack"]["anchor_slack_max_mwh"] > 1e-6),
                           pricing_source=r.get("pricing_source", "standard_sced"), solve_s=r["time_s"],
                           fallback=bool(r.get("fallback")),
                           **{f"{k}_win": v for k, v in r["slack"].items()}))
    units = pd.DataFrame(urows)
    units["curtail_mw"] = np.where(units.type.isin(["wind", "pv"]), units.get("avail_mw", 0) - units.P_mw, 0.0)
    pcols = ["period", "unit", "bus", "P_gen", "P_pump", "n_gen", "n_pump", "mode", "V_start", "V_up", "V_dn", "E_equiv_mwh"]
    # Join only executed index 0, never overlapping future windows.
    reserve_rows=[];reserve_system=[]
    for r in rr.rt:
        evidence=r["snap"]["reserve"]
        reserve_rows.extend(x for x in evidence["resources"] if x["relative_period"]==0)
        reserve_system.extend(x for x in evidence["system"] if x["relative_period"]==0)
    reserve_detail=pd.DataFrame(reserve_rows); reserve_sys=pd.DataFrame(reserve_system)
    if len(reserve_detail):
        units=units.merge(reserve_detail.reindex(columns=["period","unit","r_up","r_dn","reserve_eligible","reserve_qualified","reserve_formulation"]),on=["period","unit"],how="left",validate="one_to_one")
        # Renewables have no offered reserve variables; their confidence credit is system-only.
        no_offer=units.type.isin(["wind","pv","tie"])
        units.loc[no_offer,["r_up","r_dn"]]=0.;units.loc[no_offer,"reserve_eligible"]=False
    system=pd.DataFrame(sysrow)
    if len(reserve_sys): system=system.merge(reserve_sys.drop(columns=["relative_period","load_mw"]),on="period",how="left",validate="one_to_one")
    storage=pd.DataFrame(srow, columns=["period", "unit", "bus", "soc", "P_dis", "P_ch", "u_dis", "u_ch", "E_start", "E_mwh"]);pumped=pd.DataFrame(psrow,columns=pcols)
    diagnostics=["period","unit","r_up","r_dn","reserve_eligible","reserve_qualified","reserve_formulation","T_dur_h","reserve_power_up_margin_mw","reserve_power_down_margin_mw",
                 "reserve_duration_up_margin_mwh","reserve_duration_down_margin_mwh","reserve_duration_up_margin_1e4m3","reserve_duration_down_margin_1e4m3", "reserve_duration_lower_margin_mwh","reserve_duration_upper_margin_mwh", "reserve_duration_lower_margin_1e4m3","reserve_duration_upper_margin_1e4m3", "reserve_down_enabled"]
    if len(reserve_detail):
        if len(storage):storage=storage.merge(reserve_detail.reindex(columns=diagnostics),on=["period","unit"],how="left",validate="one_to_one")
        if len(pumped):pumped=pumped.merge(reserve_detail.reindex(columns=diagnostics),on=["period","unit"],how="left",validate="one_to_one")
    prices = pd.DataFrame(prow)
    units = units.merge(prices[["period", "bus", "lmp"]], on=["period", "bus"], how="left", validate="many_to_one")
    units["energy_mwh"] = units.P_mw * dt
    # This is an at-RT-price valuation, not DA-plus-deviation cash settlement.
    units["rt_price_valuation_yuan"] = units.energy_mwh * units.lmp
    from .results import _times
    times = dict(enumerate(_times(cfg.T, dt)))
    for frame in (units, prices, storage, pumped, system):
        frame["time"] = frame.period.map(times)
    lines = pd.DataFrame(lrow)
    lines["dispatch_at_capacity"] = lines.loading >= 1 - 1e-6
    lines["pricing_dual_active"] = lines.mu_plus.abs() + lines.mu_minus.abs() > 1e-6
    lines["congested"] = lines.dispatch_at_capacity | lines.pricing_dual_active
    hydro = pd.DataFrame(hrow, columns=["period", "station", "bus", "units_on", "Q_gen", "Q_spill", "Q_in", "Q_natural", "Q_upstream", "natural_input_source", "natural_forecast_id", "natural_issue_time_utc", "regulation", "P_start_mw", "units_start", "P_mw", "V_start", "V_1e4m3", "Q_out", "qout_m3s", "qout_max_m3s", "qout_max_source", "qout_limit_active", "qout_limit_margin_m3s"])
    from .regulation_results import realtime_tables
    regulation_detail, regulation_system = realtime_tables(rr)
    return dict(units=units, prices=prices, lines=lines, storage=storage,
                system=system, pumped=pumped, hydro=hydro, reserve=reserve_detail, reserve_system=reserve_sys,
                regulation=regulation_detail, regulation_system=regulation_system)
