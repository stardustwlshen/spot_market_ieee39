"""日前出清：能量SCUC → 固定整数 →（启用时独立调频分配）→ 正式能量SCED及同源量价。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from . import results as R
from . import scuc, regulation
from .case import DACase


@dataclass
class DAResult:
    case: DACase
    scuc_info: dict
    sced_info: dict
    pricing_info: dict | None
    slack: dict
    prices: dict
    tables: dict = field(default_factory=dict)
    costs: dict = field(default_factory=dict)
    model: object = None             # physical SCED dispatch; never the auxiliary pricing solve
    pricing_model: object = None     # compatibility field; standard pricing never uses an auxiliary model
    scuc_snapshot: dict = field(default_factory=dict)  # incumbent values before integer fixing
    stage_comparison: dict = field(default_factory=dict)
    stage_diagnostics: dict = field(default_factory=dict)


def _variable_snapshot(m) -> dict:
    """Detached numeric values: later LP solves cannot overwrite the SCUC incumbent."""
    values = {v.local_name: {i: float(pyo.value(v[i])) for i in v}
              for v in m.component_objects(pyo.Var, active=True)}
    values["flow"] = {i: float(pyo.value(m.flow[i])) for i in m.flow}
    return values


def _stage_comparison(incumbent, dispatch, info, sced) -> dict:
    # Compare continuous physical quantities, not integer domain conversions or prices.
    names = ("P", "Pw", "Ph", "Pd", "Pc", "Pg", "Pp", "flow")
    delta = {name: max((abs(value - pyo.value(getattr(dispatch, name)[i]))
                        for i, value in incumbent.get(name, {}).items()), default=0.0)
             for name in names}
    integer_mismatch = sum(abs(value - pyo.value(getattr(dispatch, name)[i])) > 1e-5
                           for name in scuc.INT_VARS for i, value in incumbent.get(name, {}).items())
    objective_tolerance = max(1e-5, 1e-8 * max(abs(info["objective"]), abs(sced["objective"]), 1.0))
    return dict(integer_mismatch=int(integer_mismatch), objective_tolerance=objective_tolerance,
                objective_nonincrease=bool(sced["objective"] <= info["objective"] + objective_tolerance),
                scuc_objective=float(info["objective"]), sced_objective=float(sced["objective"]),
                sced_minus_scuc_objective=float(sced["objective"] - info["objective"]),
                scuc_gap=float(info["gap"]), physical_max_delta_mw=max(delta.values()),
                physical_delta_by_variable_mw=delta,
                dispatch_source="fixed_integer_sced",
                interpretation="SCED reoptimizes continuous variables with SCUC integers fixed; "
                               "degeneracy and a near-optimal incumbent do not imply identical dispatch.")


def run_da(case: DACase, verbose: bool = True, *, stage_callback=None) -> DAResult:
    from .stage_diagnostics import capture
    stages = {}
    def record_stage(name, model, info):
        stages[name] = capture(model, name, info)
        if stage_callback:
            stage_callback({"phase": "da_stage", "stage": name, "stage_diagnostics": stages})
    cfg = case.cfg
    log = print if verbose else (lambda *a, **k: None)
    t0 = time.perf_counter()
    m = scuc.build_model(case)
    log(f"[DA] 模型构建 {time.perf_counter()-t0:.1f}s，开始 SCUC（HiGHS MILP）…")
    regulation.prepare_energy(m)
    info = scuc.solve_scuc(m, log)
    log(f"[DA] SCUC {info['termination']}，用时 {info['time_s']:.1f}s，目标 {info['objective']:.0f}，gap {info['gap']:.4%}")
    if not cfg.base["solver"].get("storage_binary", True):
        log("[DA] 警告：storage_binary=false，储能充放互斥被松弛（仅供对比试验）")
    info["storage_simultaneous"] = scuc.storage_simultaneous(m)
    record_stage("scuc_preplan", m, info)
    incumbent = _variable_snapshot(m)
    scuc.fix_integers(m)
    allocation = regulation.clear_reservations(m)
    info["regulation_allocation"] = allocation
    if allocation is not None:
        record_stage("agc_auxiliary", m, allocation)
    sced = scuc.solve_sced(m)
    log(f"[DA] SCED(LP) 用时 {sced['time_s']:.1f}s，目标 {sced['objective']:.0f}")
    record_stage("energy_sced", m, sced)
    slack = scuc.slack_summary(m)
    comparison = _stage_comparison(incumbent, m, info, sced)
    if cfg.mode.get("regulation", {}).get("enabled", False):
        comparison.update(objective_nonincrease=None, objective_comparison_applicable=False,
                          comparison_boundaries="SCUC_preplan_q0_vs_final_SCED_fixed_regulation_awards_and_slack_budgets",
                          interpretation="Sequential energy SCUC -> independent constrained regulation allocation -> final energy SCED. "
                          "All SCUC integers are inherited; new reservation bounds intentionally differ. Only final SCED quantities/prices execute and settle.")
    else:
        comparison["objective_comparison_applicable"] = True
    # Standard pricing only: the unchanged fixed-integer SCED supplies both
    # physical dispatch and raw dual prices, including any physical slack costs.
    # Do not replace penalties or relax anchors for a second pricing solve.
    pricing = None
    pricing_model = None
    tables = {}
    prices = scuc.extract_prices(m)
    lim = cfg.mode["price_limits"]
    prices = R.clamp_prices(prices, lim["clear_min"], lim["clear_max"])
    tables.update(_tables(m, prices, cfg))
    tables["lines"]["flow_source"] = "standard_sced"
    tables["lines"]["dual_source"] = "standard_sced"
    costs = R.cost_breakdown(m)
    return DAResult(case, info, sced, pricing, slack, prices, tables, costs, m,
                    pricing_model=pricing_model, scuc_snapshot=incumbent, stage_comparison=comparison, stage_diagnostics=stages)


def _tables(m, prices, cfg):
    lim = cfg.mode["price_limits"]
    if "lmp_clamped" not in prices:
        prices = R.clamp_prices(prices, lim["clear_min"], lim["clear_max"])
    return dict(units=R.unit_results(m, prices), prices=R.price_tables(m, prices, lim["clear_min"], lim["clear_max"]),
                lines=R.line_results(m, prices), storage=R.storage_results(m), hydro=R.hydro_results(m), pumped=R.pumped_results(m),
                system=R.system_results(m, prices))


def summarize(res: DAResult) -> dict:
    p = res.prices
    lines = res.tables["lines"]
    lines = lines.assign(mu_abs=lines.mu_plus.abs() + lines.mu_minus.abs())
    cong = lines[lines.congested]
    cong_summary = cong.groupby("line").agg(periods=("period", "nunique"), max_loading=("loading", "max"),
                                            max_mu=("mu_abs", "max")).sort_values("periods", ascending=False)
    u = res.tables["units"]
    curtail = u[u.type.isin(["wind", "pv"])].curtail_mw.sum() * res.case.cfg.dt
    dec_err = float(np.abs(p["lmp"] - p["lmp_decomp"]).max())
    return dict(
        rule_set=res.case.cfg.rule_set, date=res.case.cfg.base["date"], season=res.case.cfg.base["season"],
        bid_version=res.case.bids.get("version"),
        objective=res.sced_info["objective"], bid_cost_total=res.costs["bid_cost_total"],
        startup_cost=res.costs["thermal_startup"],
        regulation_performance_adjusted_score_cost=res.costs.get("regulation_performance_adjusted_score_cost",0.),
        regulation_shortage_penalty=res.costs.get("regulation_shortage_penalty",0.),
        bid_cost_total_scope="energy_bid_components_only_not_regulation_payment",
        regulation_score_scope="auxiliary_allocation_score_only;not_final_energy_objective_or_procurement_payment",
        lmp_min=float(p["lmp_clamped"].min()), lmp_max=float(p["lmp_clamped"].max()),
        lmp_raw_min=float(p["lmp"].min()), lmp_raw_max=float(p["lmp"].max()),
        lmp_load_weighted_avg=_lw_avg(res),
        energy_price_min=float(p["energy"].min()), energy_price_max=float(p["energy"].max()),
        congested_lines=cong_summary.reset_index().to_dict("records"),
        curtailment_mwh=float(curtail), lmp_decomp_max_err=dec_err,
        scuc_time_s=res.scuc_info["time_s"], scuc_gap=res.scuc_info["gap"], sced_time_s=res.sced_info["time_s"],
        slack=res.slack, storage_simultaneous=res.scuc_info.get("storage_simultaneous"),
        pricing_rerun=res.pricing_info is not None,
        dispatch_source="fixed_integer_sced",
        pricing_source="standard_sced",
        settlement_price_rule="clip_raw_lmp_to_clear_limits",
        stage_comparison=res.stage_comparison,
        pricing_info=res.pricing_info,
        pumped=R.pumped_summary(res.tables.get("pumped"), res.tables["prices"], res.case.cfg.dt),
    )


def _lw_avg(res):
    case = res.case
    lmp = res.prices["lmp_clamped"]
    idx = {b: i for i, b in enumerate(case.net.buses)}
    num = sum((case.load_bus[b] * lmp[idx[b]]).sum() for b in case.load_bus)
    den = sum(case.load_bus[b].sum() for b in case.load_bus)
    return float(num / den)
