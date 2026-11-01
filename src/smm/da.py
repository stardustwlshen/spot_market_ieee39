"""日前出清流程：SCUC(MILP) → 固定整数 → SCED(LP) → 对偶求 LMP → 结果表。"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import results as R
from . import scuc
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
    model: object = None


def run_da(case: DACase, verbose: bool = True) -> DAResult:
    cfg = case.cfg
    log = print if verbose else (lambda *a, **k: None)
    t0 = time.perf_counter()
    m = scuc.build_model(case)
    log(f"[DA] 模型构建 {time.perf_counter()-t0:.1f}s，开始 SCUC（HiGHS MILP）…")
    info = scuc.solve_scuc(m, log)
    log(f"[DA] SCUC {info['termination']}，用时 {info['time_s']:.1f}s，目标 {info['objective']:.0f}，gap {info['gap']:.4%}")
    if not cfg.base["solver"].get("storage_binary", True):
        log("[DA] 警告：storage_binary=false，储能充放互斥被松弛（仅供对比试验）")
    info["storage_simultaneous"] = scuc.storage_simultaneous(m)
    scuc.fix_integers(m)
    sced = scuc.solve_sced(m)
    log(f"[DA] SCED(LP) 用时 {sced['time_s']:.1f}s，目标 {sced['objective']:.0f}")
    slack = scuc.slack_summary(m)
    pricing = None
    tables = {}
    pcfg = cfg.mode.get("pumped", {})
    n_pf = scuc.fix_pumped_for_pricing(m, pcfg.get("pricing", "fixed"), float(pcfg.get("pricing_eps", 0.1)))
    if n_pf and not scuc.has_slack(m):
        pricing = scuc.solve_sced(m)
        log(f"[DA] 抽蓄定价处理（{pcfg.get('pricing', 'fixed')}）后重算价格，用时 {pricing['time_s']:.1f}s")
    if scuc.has_slack(m):
        # 调度用大罚因子；定价改用定价罚因子重算（方案：松弛不把价格推到 1e6）
        tables_dispatch = _tables(m, scuc.extract_prices(m), cfg)
        tables["dispatch_with_slack_units"] = tables_dispatch["units"]
        scuc.set_penalties(m, {k: float(v) for k, v in cfg.mode["pricing_penalties"].items()})
        pricing = scuc.solve_sced(m)
        pricing["slack"] = scuc.slack_summary(m)
        log(f"[DA] 存在松弛 {slack}，已按定价罚因子重算价格")
    prices = scuc.extract_prices(m)
    lim = cfg.mode["price_limits"]
    prices = R.clamp_prices(prices, lim["clear_min"], lim["clear_max"])
    tables.update(_tables(m, prices, cfg))
    costs = R.cost_breakdown(m)
    return DAResult(case, info, sced, pricing, slack, prices, tables, costs, m)


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
        lmp_min=float(p["lmp_clamped"].min()), lmp_max=float(p["lmp_clamped"].max()),
        lmp_raw_min=float(p["lmp"].min()), lmp_raw_max=float(p["lmp"].max()),
        lmp_load_weighted_avg=_lw_avg(res),
        energy_price_min=float(p["energy"].min()), energy_price_max=float(p["energy"].max()),
        congested_lines=cong_summary.reset_index().to_dict("records"),
        curtailment_mwh=float(curtail), lmp_decomp_max_err=dec_err,
        scuc_time_s=res.scuc_info["time_s"], scuc_gap=res.scuc_info["gap"], sced_time_s=res.sced_info["time_s"],
        slack=res.slack, storage_simultaneous=res.scuc_info.get("storage_simultaneous"),
        pricing_rerun=res.pricing_info is not None,
        pumped=R.pumped_summary(res.tables.get("pumped"), res.tables["prices"], res.case.cfg.dt),
    )


def _lw_avg(res):
    case = res.case
    lmp = res.prices["lmp_clamped"]
    idx = {b: i for i, b in enumerate(case.net.buses)}
    num = sum((case.load_bus[b] * lmp[idx[b]]).sum() for b in case.load_bus)
    den = sum(case.load_bus[b].sum() for b in case.load_bus)
    return float(num / den)
