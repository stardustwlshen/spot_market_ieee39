"""Research sequential AGC reservation: fixed commitment, mileage-only auction.

No capacity-market payment is introduced. Physical demand counts MW, not K*MW.
One energy SCUC fixes commitment, then an AGC-only LP reserves capability with
continuous dispatch free, then final energy SCED fixes those awards. There is no
joint demand marginal price: final AGC demand duals are not published. Mileage
ratio is per modeled period (no extra dt).
"""
from __future__ import annotations

import math
from numbers import Real

import pyomo.environ as pyo

_TOP = {"enabled", "demand_ratio", "mileage_ratio", "response_minutes", "sustain_h",
        "shortage_penalty", "resources", "research_note", "slack_budget_tolerance"}
_RESOURCE = {"eligible", "capacity_mw", "ramp_mw_per_min", "performance", "bid_yuan_per_mw", "offered_capacity_mw"}
_FIELDS = ("demand_ratio", "mileage_ratio", "response_minutes", "sustain_h", "shortage_penalty")
_SUPPORTED = {"coal", "gas_ccgt", "gas_gt", "storage"}


def _number(value, label, *, lo=0.0, hi=None, strict=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"regulation.{label} 必须为有限数值")
    value = float(value)
    if value < lo or (strict and value <= lo) or (hi is not None and value > hi):
        raise ValueError(f"regulation.{label} 超出允许范围")
    return value


def validate_config(cfg) -> dict:
    """Validate explicit research capabilities; no inferred AGC eligibility/rates."""
    raw = cfg.mode.get("regulation")
    if raw is None:
        return {"enabled": False}
    if not isinstance(raw, dict):
        raise ValueError("regulation 必须为映射")
    unknown = set(raw) - _TOP
    if unknown:
        raise ValueError(f"regulation 未知字段: {sorted(unknown)}")
    if not isinstance(raw.get("enabled", False), bool):
        raise ValueError("regulation.enabled 必须为布尔值")
    if not raw.get("enabled", False):
        return {"enabled": False}
    missing = set(_FIELDS + ("resources",)) - set(raw)
    if missing:
        raise ValueError(f"regulation 缺少字段: {sorted(missing)}")
    out = {"enabled": True}
    for key in _FIELDS:
        out[key] = _number(raw[key], key, strict=key != "demand_ratio",
                           hi=1.0 if key == "demand_ratio" else None)
    if out["sustain_h"] > cfg.dt + 1e-9:
        raise ValueError("regulation.sustain_h 不得超过单时段长度；端点预算不代表跨多时段AGC轨迹")
    out["slack_budget_tolerance"] = _number(raw.get("slack_budget_tolerance", 1e-5), "slack_budget_tolerance", hi=1e-4)
    resources = raw["resources"]
    if not isinstance(resources, dict):
        raise ValueError("regulation.resources 必须按机组ID映射")
    catalog = {}
    for kind in ("thermal", "storage", "hydro", "pumped", "renewable", "tie"):
        for d in cfg.units.get(kind, []):
            catalog[d["id"]] = (d.get("type", kind), d)
    clean = {}
    for uid, spec in resources.items():
        if uid not in catalog:
            raise ValueError(f"regulation.resources 未知机组ID: {uid}")
        if not isinstance(spec, dict) or set(spec) - _RESOURCE:
            raise ValueError(f"regulation.resources.{uid} 字段非法")
        if not isinstance(spec.get("eligible"), bool):
            raise ValueError(f"regulation.resources.{uid}.eligible 必须显式为布尔值")
        if not spec["eligible"]:
            continue
        kind, physical = catalog[uid]
        if kind not in _SUPPORTED:
            raise ValueError(f"{uid}: 首版AGC仅支持合格火电/储能，不支持 {kind}")
        if not (_RESOURCE - {"offered_capacity_mw"}).issubset(spec):
            raise ValueError(f"regulation.resources.{uid} 必须显式提供容量、爬坡、性能和里程报价")
        cap = _number(spec["capacity_mw"], f"{uid}.capacity_mw", strict=True)
        offered = _number(spec.get("offered_capacity_mw", cap), f"{uid}.offered_capacity_mw", hi=cap)
        ramp = _number(spec["ramp_mw_per_min"], f"{uid}.ramp_mw_per_min", strict=True)
        k = _number(spec["performance"], f"{uid}.performance", lo=.6, hi=2.)
        bid = _number(spec["bid_yuan_per_mw"], f"{uid}.bid_yuan_per_mw", lo=5., hi=15.)
        if abs(bid * 10 - round(bid * 10)) > 1e-7:
            raise ValueError(f"{uid}: 调频里程报价步长须为0.1元/MW里程")
        certified = float(physical["pmax"]) * (.06 if kind == "coal" else .2)
        if cap > certified + 1e-8:
            raise ValueError(f"{uid}: AGC容量超过认证上限 {certified:g} MW")
        if kind != "storage" and ramp > float(physical["ramp"]) + 1e-8:
            raise ValueError(f"{uid}: AGC响应爬坡超过物理爬坡能力")
        clean[uid] = dict(eligible=True, type=kind, capacity_mw=cap, offered_capacity_mw=offered,
                          ramp_mw_per_min=ramp, performance=k, bid_yuan_per_mw=bid,
                          certified_max_mw=certified)
    out["resources"] = clean
    return out


def attach(m):
    """Attach linear AGC constraints after energy/reserve variables and constraints."""
    cfg, md, case = m._case.cfg, m._md, m._case
    spec = validate_config(cfg)
    m._agc = spec
    if not spec["enabled"]:
        return
    resources = spec["resources"]
    demand = [spec["demand_ratio"] * sum(float(v[t]) for v in case.load_bus.values()) for t in m.T]
    spec["requirement_mw"] = demand
    m.AGC = pyo.Set(initialize=list(resources))
    m.q_agc = pyo.Var(m.AGC, m.T, domain=pyo.NonNegativeReals,
                      bounds=lambda m, uid, t: (0., resources[uid]["offered_capacity_mw"]))
    m.s_agc = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.agc_requirement_mw = pyo.Param(m.T, mutable=True, initialize={t: demand[t] for t in m.T})
    m.c_agc_requirement = pyo.Constraint(m.T, rule=lambda m, t:
        sum(m.q_agc[uid, t] for uid in m.AGC) + m.s_agc[t] >= m.agc_requirement_mw[t])
    m._agc_stage = "configured"
    m.c_agc_response = pyo.Constraint(m.AGC, m.T, rule=lambda m, uid, t:
        m.q_agc[uid, t] <= resources[uid]["ramp_mw_per_min"] * spec["response_minutes"])
    m.c_agc_physical = pyo.ConstraintList()
    m._agc_constraint_groups = {}

    def add(group, expr):
        row = m.c_agc_physical.add(expr)
        m._agc_constraint_groups.setdefault(group, []).append(row.index())
    agc_h = spec["sustain_h"]
    reserve_h = float(cfg.mode["reserve"].get("T_dur_h", 1.0))
    if not math.isfinite(reserve_h) or reserve_h <= 0:
        raise ValueError("reserve.T_dur_h 必须是正有限数值（AGC与备用联合能量预算）")
    for uid, technical in resources.items():
        for t in m.T:
            q = m.q_agc[uid, t]
            if technical["type"] != "storage":
                d = md.thermal[uid]
                add("shared_up", m.P[uid, t] + m.r_up[uid, t] + q <= d["pmax"] * m.u[uid, t])
                add("shared_down", m.P[uid, t] - m.r_dn[uid, t] - q >= d["pmin"] * m.u[uid, t])
                # Conservative: no AGC during the startup period, no new integers.
                add("online_no_start", q <= technical["capacity_mw"] * (m.u[uid, t] - m.v[uid, t]))
                # Share the scheduled interval ramp envelope with AGC and reserve.
                # Startup/shutdown receive their existing transition allowance plus
                # a conservative big-M exemption; q itself is already zero on start.
                previous_on = int(bool(d["init"]["online"])) if t == 0 else m.u[uid, t - 1]
                previous_p = d["init"]["p0"] if t == 0 else m.P[uid, t - 1]
                interval_ramp = d["ramp"] * 60 * md.dt
                add("interval_ramp_up", m.P[uid, t] - previous_p + q + m.r_up[uid, t] <=
                                    interval_ramp * previous_on + (d["pmin"] + d["pmax"]) * m.v[uid, t])
                add("interval_ramp_down", previous_p - m.P[uid, t] + q + m.r_dn[uid, t] <=
                                    interval_ramp * m.u[uid, t] + (d["pmin"] + d["pmax"]) * m.w[uid, t])
            else:
                d = md.storage[uid]
                # Preserve the selected charging/discharging mode for joint
                # reserve+AGC activation: no crossing zero or exploiting both modes.
                add("shared_up", q + m.rs_up[uid, t] <= d["pmax"] * m.u_dis[uid, t] - m.Pd[uid, t] + m.Pc[uid, t])
                add("shared_down", q + m.rs_dn[uid, t] <= d["pmax"] * m.u_ch[uid, t] - m.Pc[uid, t] + m.Pd[uid, t])
                # Incremental worst-direction energy budget, both interval endpoints.
                # This is a conservative capability reservation, not an AGC signal
                # or an extra net-energy injection in the SOC balance equation.
                start = d["soc0"] * d["ecap"] if t == 0 else m.E[uid, t - 1]
                for energy in (start, m.E[uid, t]):
                    add("energy_up_mwh", energy - d["soc_min"] * d["ecap"] >=
                                        (q * agc_h + m.rs_up[uid, t] * reserve_h) / d["eta_d"])
                    add("energy_down_mwh", d["soc_max"] * d["ecap"] - energy >=
                                        (q * agc_h + m.rs_dn[uid, t] * reserve_h) / d["eta_d"])


def objective_term(m):
    """The pre-UC and final SCED optimize energy only; AGC has a separate auction."""
    return 0.


def bid_objective_term(m):
    """AGC-only auction: (bid/K)*mileage_ratio*MW + shortage; no dt/capacity fee."""
    spec = getattr(m, "_agc", {"enabled": False})
    if not spec["enabled"]:
        return 0.
    return (sum((d["bid_yuan_per_mw"] / d["performance"]) * spec["mileage_ratio"] * m.q_agc[uid, t]
                for uid, d in spec["resources"].items() for t in m.T)
            + spec["shortage_penalty"] * sum(m.s_agc[t] for t in m.T))


def prepare_energy(m):
    """Prepare the ONLY SCUC: disable AGC awards/demand, retain shared guards."""
    if not getattr(m, "_agc", {}).get("enabled", False):
        return
    for v in m.q_agc.values():
        v.fix(0.)
    for t in m.T:
        m.s_agc[t].fix(0.)
        m.agc_requirement_mw[t] = 0.
    m._agc_stage = "energy_scuc"
    m._agc_auction_info = None


def clear_reservations(m):
    """Allocate AGC after UC fixing; restore pure energy objective for final SCED.

    No energy continuous variable is fixed here. Existing case fixed powers stay
    fixed. The AGC LP's temporary dispatch and duals are never settlement outputs.
    """
    if not getattr(m, "_agc", {}).get("enabled", False):
        return None
    from .scuc import integer_components, solve_sced
    if any(not v.fixed for name, component in integer_components(m) for v in component.values()):
        raise ValueError("调频预留必须在全部SCUC整数决策固定之后进行")
    if getattr(m, "_agc_stage", None) != "energy_scuc":
        raise ValueError("调频预留要求先调用prepare_energy并完成能量SCUC")
    # Non-AGC safety slack cannot be used as a free source of AGC headroom.
    # Keep this same domain for the reservation LP AND final energy SCED.
    tolerance = m._agc["slack_budget_tolerance"]
    budgets = []
    for name in ("shed", "surplus", "sl", "sR_up", "sR_dn", "sA_up", "sA_dn"):
        for index, v in getattr(m, name).items():
            baseline = float(pyo.value(v))
            upper = baseline + tolerance
            if v.has_ub():
                upper = min(upper, float(pyo.value(v.ub)))
            original_upper = v.ub
            v.setub(upper)
            budgets.append(dict(variable=name, index=index, baseline=baseline, upper=upper, original_upper=original_upper))
    m._agc_slack_budgets = budgets
    energy_objective = m.obj
    for v in m.q_agc.values():
        v.unfix()
    for t in m.T:
        m.s_agc[t].unfix()
        m.agc_requirement_mw[t] = m._agc["requirement_mw"][t]
    m.del_component("obj")
    m.obj = pyo.Objective(expr=bid_objective_term(m), sense=pyo.minimize)
    success = False
    m._agc_stage = "agc_reservation_lp"
    try:
        info = solve_sced(m)
        awarded = {uid: [float(pyo.value(m.q_agc[uid, t])) for t in m.T] for uid in m.AGC}
        shortage = [max(0., m._agc["requirement_mw"][t] - sum(awarded[uid][int(t)] for uid in m.AGC)) for t in m.T]
        for v in m.q_agc.values():
            v.fix(float(pyo.value(v)))
        for t in m.T:
            m.s_agc[t].fix(shortage[int(t)])
        info["canonical_objective"] = float(pyo.value(m.obj))
        m._agc_auction_budget_values = [float(pyo.value(getattr(m, row["variable"])[row["index"]])) for row in budgets]
        auction_budget_violation = max((max(0., actual-row["upper"])
            for row, actual in zip(budgets, m._agc_auction_budget_values)), default=0.)
        m._agc_auction_info = dict(info, max_slack_budget_violation=auction_budget_violation, stage="agc_only_reservation_lp", award_mw=awarded,
                                  shortage_mw=shortage, energy_continuous_free=True,
                                  prices_available=False)
        m._agc_stage = "reserved_for_energy_sced"
        success = True
        return m._agc_auction_info
    finally:
        m.del_component("obj")
        m.add_component("obj", energy_objective)
        if not success:
            for row in budgets:
                getattr(m, row["variable"])[row["index"]].setub(row["original_upper"])
            m._agc_slack_budgets = []
            m._agc_auction_budget_values = []
            prepare_energy(m)


def _budget_snapshot(m):
    rows = []
    for i, row in enumerate(getattr(m, "_agc_slack_budgets", [])):
        actual = float(pyo.value(getattr(m, row["variable"])[row["index"]]))
        auction = m._agc_auction_budget_values[i]
        rows.append(dict(variable=row["variable"], index=row["index"], baseline=row["baseline"],
                         upper=row["upper"], auction_actual=auction, actual=actual,
                         auction_margin=row["upper"]-auction, margin=row["upper"]-actual))
    return dict(tolerance=m._agc["slack_budget_tolerance"], rows=rows,
                auction_max_violation=max((max(0., -r["auction_margin"]) for r in rows), default=0.),
                final_max_violation=max((max(0., -r["margin"]) for r in rows), default=0.))


def _violation(c):
    body = float(pyo.value(c.body))
    return max(0., (float(pyo.value(c.lower)) - body) if c.has_lb() else 0.,
               (body - float(pyo.value(c.upper))) if c.has_ub() else 0.)


def _diagnostics(m):
    families = {name: max((_violation(m.c_agc_physical[i]) for i in indices), default=0.)
                for name, indices in m._agc_constraint_groups.items()}
    families["requirement"] = max((_violation(c) for c in m.c_agc_requirement.values()), default=0.)
    families["response"] = max((_violation(c) for c in m.c_agc_response.values()), default=0.)
    families["offered_capacity"] = max((max(0., pyo.value(v) - v.ub, -pyo.value(v))
                                       for v in m.q_agc.values()), default=0.)
    families["certified_capacity"] = max((max(0., pyo.value(m.q_agc[uid, t]) - d["capacity_mw"])
                                         for uid, d in m._agc["resources"].items() for t in m.T), default=0.)
    return dict(family_max_violations=families,
                constraint_violation_max_mw=max((v for k, v in families.items() if not k.endswith("_mwh")), default=0.),
                energy_constraint_violation_max_mwh=max((v for k, v in families.items() if k.endswith("_mwh")), default=0.))


def _physical_snapshot(m):
    rows = {}
    md = m._md
    value = lambda v: float(pyo.value(v))
    for uid, technical in m._agc["resources"].items():
        if technical["type"] != "storage":
            d = md.thermal[uid]
            rows[uid] = dict(type=technical["type"], pmin=d["pmin"], pmax=d["pmax"], ramp_mw_per_min=d["ramp"],
                P_mw=[value(m.P[uid,t]) for t in m.T],
                previous_P_mw=[d["init"]["p0"] if t == 0 else value(m.P[uid,t-1]) for t in m.T],
                online=[value(m.u[uid,t]) for t in m.T], startup=[value(m.v[uid,t]) for t in m.T],
                shutdown=[value(m.w[uid,t]) for t in m.T],
                previous_online=[int(bool(d["init"]["online"])) if t == 0 else value(m.u[uid,t-1]) for t in m.T],
                reserve_up_mw=[value(m.r_up[uid,t]) for t in m.T], reserve_down_mw=[value(m.r_dn[uid,t]) for t in m.T])
        else:
            d = md.storage[uid]
            rows[uid] = dict(type="storage", pmax=d["pmax"], eta_d=d["eta_d"],
                E_min_mwh=d["soc_min"]*d["ecap"], E_max_mwh=d["soc_max"]*d["ecap"],
                P_dis_mw=[value(m.Pd[uid,t]) for t in m.T], P_ch_mw=[value(m.Pc[uid,t]) for t in m.T],
                mode_dis=[value(m.u_dis[uid,t]) for t in m.T], mode_ch=[value(m.u_ch[uid,t]) for t in m.T],
                E_start_mwh=[d["soc0"]*d["ecap"] if t == 0 else value(m.E[uid,t-1]) for t in m.T],
                E_end_mwh=[value(m.E[uid,t]) for t in m.T],
                reserve_up_mw=[value(m.rs_up[uid,t]) for t in m.T], reserve_down_mw=[value(m.rs_dn[uid,t]) for t in m.T])
    return rows


def snapshot(m):
    """Detached plain numeric arrays for dispatch, rolling, settlement and CSV."""
    spec = getattr(m, "_agc", {"enabled": False})
    if not spec["enabled"]:
        return dict(enabled=False, resources={}, requirement_mw=[], shortage_mw=[], q_mw={},
                    mileage_ratio=None, period_indices=[])
    return dict(enabled=True, clearing_mode="sequential_reservation", stage=getattr(m, "_agc_stage", None),
                auction_info=getattr(m, "_agc_auction_info", None),
                resources={uid: dict(d) for uid, d in spec["resources"].items()},
                requirement_mw=list(spec["requirement_mw"]),
                shortage_mw=[float(pyo.value(m.s_agc[t])) for t in m.T],
                q_mw={uid: [float(pyo.value(m.q_agc[uid, t])) for t in m.T] for uid in m.AGC},
                mileage_ratio=spec["mileage_ratio"],
                period_indices=[m._case.t0 + int(t) for t in m.T],
                physical=_physical_snapshot(m), diagnostics=_diagnostics(m), slack_budget=_budget_snapshot(m), dt_h=m._md.dt,
                reserve_sustain_h=float(m._case.cfg.mode["reserve"].get("T_dur_h", 1.0)),
                demand_ratio=spec["demand_ratio"], response_minutes=spec["response_minutes"],
                sustain_h=spec["sustain_h"], shortage_penalty=spec["shortage_penalty"])


def prices(m):
    """Final AGC awards are fixed: their requirement dual is not a market price."""
    enabled = getattr(m, "_agc", {}).get("enabled", False)
    missing = [None for _ in m.T] if enabled else []
    return dict(available=False, reason="sequential_awards_fixed_not_joint_marginal_price",
                requirement_marginal_yuan_per_mw_period=list(missing),
                normalized_marginal_yuan_per_mw_mileage=list(missing))
