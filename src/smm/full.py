"""一键全流程：日前 →（可选日内）→ 实时 15 min 滚动 SCED → 两级偏差结算，并输出表、图与摘要。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import plots, rolling, settlement
from .da import run_da, summarize



def execution_safety_checks(da_res, rt_tab, tolerance_mw=1e-5) -> dict:
    """Check dispatched flows against the actual scenario limits, not pricing flows.

    Network feasibility alone is not a certification of all unit constraints. Missing
    or non-finite flow data is a failure, never a silent zero-overload pass.
    """
    rates = da_res.case.net.branches.set_index("branch_id").rate_mw
    da_periods = set(range(da_res.case.T or da_res.case.cfg.T))
    sys_periods = pd.to_numeric(rt_tab["system"].period, errors="coerce").to_numpy(dtype=float)
    system_valid = bool(len(sys_periods) and np.isfinite(sys_periods).all()
                        and (sys_periods == np.floor(sys_periods)).all()
                        and len(set(sys_periods)) == len(sys_periods))
    rt_periods = set(sys_periods) if system_valid else set()
    out = {"rt_system_periods_valid": system_valid,
           "safety_tolerance_mw": float(tolerance_mw),
           "physical_quantity_source": "fixed_integer_SCED_dispatch_snapshot",
           "price_source": "see_per_stage_pricing_source;raw_duals_and_clipped_settlement_prices_are_distinct"}
    for stage, frame in (("da", da_res.tables.get("prices", pd.DataFrame())), ("rt", rt_tab["prices"])):
        out[f"{stage}_pricing_sources"] = sorted(frame.pricing_source.dropna().unique().tolist()) if "pricing_source" in frame else ["legacy_source_not_recorded"]
        if {"dispatch_raw_lmp", "dispatch_energy", "dispatch_congestion"}.issubset(frame):
            out[f"{stage}_dispatch_raw_lmp_decomposition_max_err"] = float(np.abs(frame.dispatch_raw_lmp-frame.dispatch_energy-frame.dispatch_congestion).max())
    for stage, lines in (("da", da_res.tables["lines"]), ("rt", rt_tab["lines"])):
        limits = lines.line.map(rates).to_numpy(dtype=float)
        flows = lines.flow_mw.to_numpy(dtype=float)
        periods = da_periods if stage == "da" else rt_periods
        expected = {(line, period) for line in rates.index for period in periods}
        actual = set(zip(lines.line, lines.period))
        valid = (bool(expected) and actual == expected and rates.index.is_unique
                 and len(lines) > 0 and np.isfinite(flows).all()
                 and np.isfinite(limits).all() and (limits > 0).all()
                 and not lines.duplicated(["line", "period"]).any()
                 and len(lines) == len(expected))
        if valid:
            excess = np.maximum(np.abs(flows) - limits, 0.0)
            violation = excess > tolerance_mw
            out[f"{stage}_line_overload_count"] = int(violation.sum())
            out[f"{stage}_line_overload_periods"] = int(lines.loc[violation, "period"].nunique())
            out[f"{stage}_line_overload_max_mw"] = float(excess.max())
            out[f"{stage}_line_loading_max"] = float((np.abs(flows) / limits).max())
            out[f"{stage}_network_feasible"] = not bool(violation.any())
        else:
            for suffix in ("line_overload_count", "line_overload_periods", "line_overload_max_mw", "line_loading_max"):
                out[f"{stage}_{suffix}"] = None
            out[f"{stage}_network_feasible"] = False
        out[f"{stage}_line_data_valid"] = bool(valid)
    da_lines = da_res.tables["lines"]
    out["da_dispatch_line_slack_max_mw"] = float(da_lines.slack_mw.max()) if len(da_lines) else None
    # Raw prices decompose; clipped settlement prices need not do so.
    rp = rt_tab["prices"]
    out["rt_raw_lmp_decomposition_max_err"] = float(np.abs(rp.lmp_raw - rp.energy - rp.congestion).max())
    out["network_feasible"] = out["da_network_feasible"] and out["rt_network_feasible"]
    return out



def slack_policy_checks(da_slack, rt_rounds, tolerance=1e-6) -> dict:
    """Window maxima are not executed-period violations; report them separately."""
    keys = ("shed_mwh", "surplus_mwh", "line_slack_max", "reserve_up_short_max",
            "reserve_dn_short_max", "anchor_slack_max_mwh")
    summaries = [da_slack] + [r["slack"] for r in rt_rounds]
    complete = bool(rt_rounds) and all(
        all(k in summary and np.isfinite(summary[k]) for k in keys) for summary in summaries)
    split = {}
    for kind, key in (("storage", "storage_anchor_slack_max_mwh"), ("pumped", "pumped_anchor_slack_max_1e4m3"), ("hydro", "hydro_anchor_slack_max_1e4m3")):
        available = bool(rt_rounds) and all(key in r["slack"] and np.isfinite(r["slack"][key]) for r in rt_rounds)
        split[key] = float(max(r["slack"][key] for r in rt_rounds)) if available else None
        split[f"{kind}_anchor_slack_periods"] = int(sum(r["slack"][key] > tolerance for r in rt_rounds)) if available else None
    return {
        **split,
        "anchor_slack_max_mwh_legacy_note": "DEPRECATED:mixed_numeric_max_of_storage_MWh_and_pumped_or_hydro_1e4m3;not_an_energy_quantity;use_split_fields",
        "anchor_slack_periods": int(sum(r["slack"].get("anchor_slack_max_mwh", 0.0) > tolerance for r in rt_rounds)),
        "slack_summary_data_valid": bool(complete),
        "rules_satisfied_no_slack": bool(complete and all(
            abs(summary[k]) <= tolerance for summary in summaries for k in keys)),
        "no_slack_scope": "DA_entire_horizon_and_each_RT_lookahead_window:shed,surplus,line,reserve_up,reserve_down,anchor;not_a_full_model_or_uncertainty_certificate",
    }


def hydro_window_checks(da_res, rr, tolerance=1e-5) -> dict:
    """Station-aggregate hydro checks using detached, unit-labelled window evidence.

    A hard window target is not a per-hour DA power lock. Lagged incoming water
    and state are reported by the constructed model; the separate audit script
    additionally reconstructs history routing independently.
    """
    cfg = da_res.case.cfg
    if not cfg.units["hydro"]:
        return {"hydro_window_checks_available": True, "hydro_window_constraints_satisfied": True,
                "hydro_window_records": []}
    out = dict(hydro_window_checks_available=True, hydro_water_balance_max_err_1e4m3=0.,
               hydro_window_terminal_max_err_1e4m3=0., hydro_tracking_equation_max_err_1e4m3=0.,
               hydro_tracking_target_deviation_max_1e4m3=0., hydro_station_ramp_excess_max_mw=0.,
               hydro_da_station_ramp_excess_max_mw=0., hydro_state_volume_continuity_max_err_1e4m3=0.,
               hydro_state_power_continuity_max_err_mw=0., hydro_energy_budget_excess_max_mwh=0.,
               hydro_volume_bound_excess_max_1e4m3=0., hydro_ecological_shortage_max_m3s=0.,
               hydro_outflow_limit_excess_max_m3s=0., hydro_da_zone_count_mismatch=0,
               hydro_window_records=[])
    soft_tracking = cfg.mode["realtime"].get("tracking_policy", {}).get("mode") == "soft_deliverable_energy"
    specs = {h["id"]: h for h in cfg.units["hydro"]}
    da_sn = rolling.snapshot(da_res.model)
    if len(rr.states) not in (len(rr.rt),len(rr.rt)+1) or [r["t"] for r in rr.rt] != list(range(len(rr.rt))):
        out["hydro_window_checks_available"] = False
    def check_snapshot(sn, stage, start, state=None):
        metadata = sn.get("hydro_constraints", {})
        if set(metadata) != set(specs):
            out["hydro_window_checks_available"] = False
            return
        if any(set(sn.get(key,{})) != set(specs) for key in ("Ph","Vh","Qout")) or set(sn.get("delta",{})) != set(da_sn["delta"]):
            out["hydro_window_checks_available"] = False
            return
        for h, meta in metadata.items():
            power = np.asarray(sn["Ph"][h], float); volume = np.asarray(sn["Vh"][h], float)
            arrays = [power,volume,np.asarray(sn["Qout"][h],float)] + [np.asarray(meta.get(k,[]),float) for k in ("qin_m3s","upstream_in_m3s","ramp_up_limit_mw","ramp_down_limit_mw")]
            arrays += [np.asarray(a,float) for (uid,z),a in sn["delta"].items() if uid==h]
            scalars=[meta.get("p_start_mw",np.nan),meta.get("v_start_1e4m3",np.nan)]
            if stage=="RT": scalars += [state.hydro.get(h,np.nan),state.hydro_p.get(h,np.nan)]
            if not len(power) or any(len(a)!=len(power) or not np.isfinite(a).all() for a in arrays) or not np.isfinite(scalars).all():
                out["hydro_window_checks_available"] = False
                continue
            prev_p = np.r_[meta["p_start_mw"], power[:-1]]
            prev_v = np.r_[meta["v_start_1e4m3"], volume[:-1]]
            delta_p = power-prev_p
            ramp_excess = max(0., float(np.max(delta_p-np.asarray(meta["ramp_up_limit_mw"]))),
                              float(np.max(-delta_p-np.asarray(meta["ramp_down_limit_mw"]))))
            key = "hydro_da_station_ramp_excess_max_mw" if stage == "DA" else "hydro_station_ramp_excess_max_mw"
            out[key] = max(out[key], ramp_excess)
            expected_v = prev_v + (np.asarray(meta["qin_m3s"])+np.asarray(meta["upstream_in_m3s"])-np.asarray(sn["Qout"][h]))*cfg.dt*.36
            water_error = float(np.abs(volume-expected_v).max())
            out["hydro_water_balance_max_err_1e4m3"] = max(out["hydro_water_balance_max_err_1e4m3"],water_error)
            target = meta.get("v_end_target_1e4m3")
            if stage=="RT" and (soft_tracking or cfg.mode["realtime"].get("hydro")=="window_end_da"):
                k=start+len(power)-1
                if target is None or not np.isfinite(target) or k>=len(da_sn["Vh"][h]):
                    out["hydro_window_checks_available"]=False
                else:
                    out["hydro_window_terminal_max_err_1e4m3"] = max(out["hydro_window_terminal_max_err_1e4m3"],abs(float(target)-float(da_sn["Vh"][h][k])))
            if target is not None and not np.isfinite(target):
                out["hydro_window_checks_available"]=False
                target=None
            terminal_error = abs(float(volume[-1])-target) if target is not None else None
            tracking_equation_error = None
            if stage == "RT" and soft_tracking:
                up = meta.get("tracking_slack_up_1e4m3")
                down = meta.get("tracking_slack_down_1e4m3")
                if (meta.get("terminal_kind") != "soft_da_window_end" or target is None
                        or up is None or down is None or not np.isfinite([up, down]).all()
                        or min(up, down) < -tolerance):
                    out["hydro_window_checks_available"] = False
                else:
                    # Positive penalty selects minimal nonnegative deviation slacks.
                    tracking_equation_error = max(abs(up-max(0.,float(volume[-1])-target)),
                                                  abs(down-max(0.,target-float(volume[-1]))))
                    out["hydro_tracking_equation_max_err_1e4m3"] = max(out["hydro_tracking_equation_max_err_1e4m3"],tracking_equation_error)
                    out["hydro_tracking_target_deviation_max_1e4m3"] = max(out["hydro_tracking_target_deviation_max_1e4m3"],terminal_error)
            elif terminal_error is not None:
                out["hydro_window_terminal_max_err_1e4m3"] = max(out["hydro_window_terminal_max_err_1e4m3"],terminal_error)
            budget = meta.get("remaining_energy_budget_mwh")
            if budget is not None:
                out["hydro_energy_budget_excess_max_mwh"] = max(out["hydro_energy_budget_excess_max_mwh"], float(power.sum()*cfg.dt-budget),0.)
            spec=specs[h]; season=spec["seasons"][cfg.base["season"]]
            vmin,vmax=float(season["vmin"]),float(season["vmax"])
            if spec.get("regulation")=="run_of_river":
                vmax=float(season["v0"]);vmin=vmax-float(spec.get("pondage",0) or 0)
            out["hydro_volume_bound_excess_max_1e4m3"] = max(out["hydro_volume_bound_excess_max_1e4m3"], float(np.max(vmin-volume)),float(np.max(volume-vmax)),0.)
            q=np.asarray(sn["Qout"][h])
            out["hydro_ecological_shortage_max_m3s"] = max(out["hydro_ecological_shortage_max_m3s"],float(np.max(float(season["qeco"])-q)),0.)
            if spec.get("qout_max") is not None:
                out["hydro_outflow_limit_excess_max_m3s"] = max(out["hydro_outflow_limit_excess_max_m3s"],float(np.max(q-float(spec["qout_max"]))),0.)
            if stage=="RT":
                out["hydro_state_volume_continuity_max_err_1e4m3"] = max(out["hydro_state_volume_continuity_max_err_1e4m3"],abs(float(meta["v_start_1e4m3"])-state.hydro[h]))
                out["hydro_state_power_continuity_max_err_mw"] = max(out["hydro_state_power_continuity_max_err_mw"],abs(float(meta["p_start_mw"])-state.hydro_p[h]))
                if cfg.mode["realtime"].get("hydro") in ("fixed_da","window_end_da"):
                    for (uid,z),arr in sn["delta"].items():
                        if uid==h:
                            expected=da_sn["delta"][(uid,z)][start:start+len(arr)]
                            out["hydro_da_zone_count_mismatch"] += int(np.count_nonzero(np.asarray(arr)!=expected))
                out["hydro_window_records"].append(dict(window_start=start,window_end_exclusive=start+len(power),station=h,
                    start_volume_1e4m3=float(meta["v_start_1e4m3"]),start_power_mw=float(meta["p_start_mw"]),
                    terminal_target_1e4m3=target,terminal_actual_1e4m3=float(volume[-1]),terminal_error_1e4m3=terminal_error,terminal_kind=meta.get("terminal_kind"),
                    tracking_equation_error_1e4m3=tracking_equation_error,
                    water_balance_max_err_1e4m3=water_error,station_ramp_excess_max_mw=ramp_excess,
                    remaining_energy_budget_mwh=budget,window_generation_mwh=float(power.sum()*cfg.dt)))
    check_snapshot(da_sn,"DA",0)
    for r,state in zip(rr.rt,rr.states):
        check_snapshot(r["snap"],"RT",r["t"],state)
    numeric = [v for k,v in out.items() if k not in ("hydro_window_checks_available","hydro_window_records","hydro_tracking_target_deviation_max_1e4m3")]
    out["hydro_window_constraints_satisfied"] = bool(out["hydro_window_checks_available"] and all(np.isfinite(v) and v<=tolerance for v in numeric))
    out["hydro_check_scope"] = "DA_and_RT_station_aggregate_ramp,water,volume,eco,outflow,budget;RT_actual_state,DA_zone_count,hard_window_terminal_or_soft_tracking_slack_consistency_as_configured;soft_target_deviation_is_not_physical_infeasibility;not_individual_turbine_ramp"
    return out


def checks(da_res, rr, rt_tab) -> dict:
    """连续性与平衡性校核（测试同样使用）。"""
    cfg = da_res.case.cfg
    dt = cfg.dt
    sto = {s["id"]: s for s in cfg.units["storage"]}
    out = {}
    # SOC 连续：每时段起点 = 上一时段实际终点，且满足充放电方程
    st = rt_tab["storage"]
    err_cont, err_eq = 0.0, 0.0
    for s, d in st.groupby("unit"):
        d = d.sort_values("period")
        e0 = sto[s]["soc0"] * sto[s]["ecap"]
        prev = np.r_[e0, d.E_mwh.to_numpy()[:-1]]
        err_cont = max(err_cont, float(np.abs(d.E_start.to_numpy() - prev).max()))
        calc = d.E_start + (sto[s]["eta_c"] * d.P_ch - d.P_dis / sto[s]["eta_d"]) * dt
        err_eq = max(err_eq, float(np.abs(calc - d.E_mwh).max()))
        out.setdefault("storage_simultaneous", 0)
        out["storage_simultaneous"] += int(((d.P_ch > 1e-6) & (d.P_dis > 1e-6)).sum())
    out["soc_continuity_err_mwh"] = err_cont
    out["soc_equation_err_mwh"] = err_eq
    # 机组组合连续：锁定类型（煤电/CCGT）在日内各轮与实时均等于日前
    lock = set(cfg.mode["realtime"]["lock_types"])
    da_u = da_res.tables["units"].set_index(["unit", "period"]).on
    mism_id = mism_rt = 0
    ru = rt_tab["units"]
    for u in cfg.units["thermal"]:
        if u["type"] not in lock:
            continue
        for r in rr.id_rounds:
            arr = r["snap"]["u"][u["id"]]
            mism_id += int(sum(int(arr[k]) != int(da_u[(u["id"], r["tau"] + k)]) for k in range(len(arr))))
        d = ru[ru.unit == u["id"]]
        mism_rt += int(sum(int(o) != int(da_u[(u["id"], p)]) for o, p in zip(d.on, d.period)))
    out["locked_commitment_mismatch_id"] = mism_id
    out["locked_commitment_mismatch_rt"] = mism_rt
    # 实时锁定机组开机状态 = 执行时所用参考计划（无日内时即日前）
    mism = 0
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    for r in rr.rt:
        plan = rr.plans[r["plan_idx"]]
        for g, arr in r["snap"]["u"].items():
            if types[g] in lock:
                mism += int(arr[0] != plan["u"][g][r["t"]])
    out["rt_vs_plan_commitment_mismatch"] = mism
    # 水电执行日前曲线
    rc = cfg.mode["realtime"]
    if rc.get("hydro", "fixed_da") == "fixed_da":
        dah = da_res.tables["units"].set_index(["unit", "period"]).P_mw
        dh = ru[ru.type == "hydro"]
        out["hydro_vs_da_err_mw"] = float(max([abs(p - dah[(u, t)]) for u, t, p in zip(dh.unit, dh.period, dh.P_mw)] or [0.0]))
    # 储能：锚点满足情况（实时各窗口末 E 相对日前 E 的偏差占比）/ 模式 A 跟随日前
    sc = rc["storage"]
    da_sto = da_res.tables["storage"].set_index(["unit", "period"])
    if sc.get("mode") == "fixed_da":
        out["storage_vs_da_err_mw"] = float(max(abs((r.P_dis - r.P_ch) - (da_sto.loc[(r.unit, r.period), "P_dis"] - da_sto.loc[(r.unit, r.period), "P_ch"]))
                                                for r in st.itertuples()))
    # 抽水蓄能：上库库容连续与平衡方程、同时抽发、工况沿用日前、fixed_da 出力偏差
    pst = rt_tab.get("pumped")
    if pst is not None and len(pst):
        pcfg = {p["id"]: p for p in cfg.units.get("pumped", [])}
        da_ps = da_res.tables["pumped"].set_index(["unit", "period"])
        ec = eq = dev = 0.0
        mode_mis = 0
        for pid, d in pst.groupby("unit"):
            d = d.sort_values("period")
            c = pcfg[pid]
            prev = np.r_[float(c["vup0"]), d.V_up.to_numpy()[:-1]]
            ec = max(ec, float(np.abs(d.V_start.to_numpy() - prev).max()))
            calc = d.V_start + (d.P_pump / c["e_pump"] - d.P_gen / c["e_gen"]) * dt
            eq = max(eq, float(np.abs(calc - d.V_up).max()))
            for r in d.itertuples():
                dd = da_ps.loc[(pid, r.period)]
                mode_mis += int(r.n_gen != dd.n_gen or r.n_pump != dd.n_pump)
                dev = max(dev, abs(r.P_gen - dd.P_gen) + abs(r.P_pump - dd.P_pump))
        out["pumped_continuity_err"] = ec
        out["pumped_equation_err"] = eq
        out["pumped_simultaneous"] = int(((pst.P_gen > 1e-6) & (pst.P_pump > 1e-6)).sum())
        out["pumped_mode_vs_da_mismatch"] = mode_mis
        out["pumped_vs_da_dev_mw"] = float(dev)
    out["anchor_relaxed_periods"] = int(sum(1 for r in rr.rt if r.get("anchor_relaxed", 0)))
    out["anchor_slack_max_mwh"] = float(max(r["slack"].get("anchor_slack_max_mwh", 0.0) for r in rr.rt))
    out.update(slack_policy_checks(da_res.slack, rr.rt))
    from .regulation import snapshot as regulation_snapshot
    agc_states = [regulation_snapshot(da_res.model)] + [r["snap"].get("regulation", {}) for r in rr.rt]
    agc_short = [float(v) for sn in agc_states if sn.get("enabled") for v in sn["shortage_mw"]]
    out["regulation_shortage_max_mw_all_windows"] = max(agc_short, default=0.)
    out["regulation_demand_met_all_windows"] = max(agc_short, default=0.) <= 1e-6
    out["all_declared_market_requirements_without_slack"] = out["rules_satisfied_no_slack"] and out["regulation_demand_met_all_windows"]
    # 最小开停机（实际执行序列）
    viol = 0
    for u in cfg.units["thermal"]:
        d = ru[ru.unit == u["id"]].sort_values("period").on.to_numpy()
        init = u["init"]
        seq = np.r_[[1 if init["online"] else 0] * int(round(init["hours"] / dt)), d]
        runs, cur, n = [], seq[0], 0
        for x in seq:
            if x == cur:
                n += 1
            else:
                runs.append((cur, n)); cur, n = x, 1
        for k, (val, n) in enumerate(runs[1:], 1):           # 中间完整段
            need = u["ut"] if val == 1 else u["dt"]
            if n * dt < need - 1e-9:
                viol += 1
    out["min_updown_violations"] = viol
    # 实时功率平衡
    sysd = rt_tab["system"]
    gen = rt_tab["units"].groupby("period").P_mw.sum()
    tie = sum(np.asarray(s)[: len(sysd)] for _, (b, s) in da_res.case.tie.items())
    bal = gen.to_numpy() + tie + sysd.shed_mw.to_numpy() - sysd.surplus_mw.to_numpy() - sysd.load_mw.to_numpy()
    out["rt_balance_err_mw"] = float(np.abs(bal).max())
    inj = rt_tab["prices"].groupby("period").inj_mw.sum()
    out["rt_injection_sum_err_mw"] = float(np.abs(inj).max())
    out.update(execution_safety_checks(da_res, rt_tab))
    out.update(hydro_window_checks(da_res, rr))
    tol = out["safety_tolerance_mw"]
    # Deliberately separate checked physical constraints from anchors/forecast-window
    # reserve shortages. Neither a monetary reconciliation nor balance proves safety.
    residuals = ("rt_balance_err_mw", "rt_injection_sum_err_mw", "soc_continuity_err_mwh",
                 "soc_equation_err_mwh", "pumped_continuity_err", "pumped_equation_err")
    violations = ("storage_simultaneous", "pumped_simultaneous", "min_updown_violations",
                  "locked_commitment_mismatch_rt", "rt_vs_plan_commitment_mismatch")
    out["physical_feasible_checked_constraints"] = bool(
        out["network_feasible"] and all(out.get(k, 0.0) <= tol for k in residuals)
        and all(out.get(k, 0) == 0 for k in violations)
        and float(sysd.shed_mw.max()) <= tol and float(sysd.surplus_mw.max()) <= tol)
    out["physical_feasibility_scope"] = "DA:network_limits_only;RT:network_limits,balance,SOC_and_pumped_continuity,exclusivity,commitment,min_updown_completed_interior_runs_only,no_shed_or_surplus;not_all_model_constraints"
    return out


def run_full(cfg, case, log=print, periods: int | None = None, *, progress_callback=None, should_cancel=None) -> dict:
    t0 = time.perf_counter()
    if should_cancel and should_cancel():
        from .live_runner import RunCancelled
        raise RunCancelled("Cancelled before DA")
    if progress_callback:
        progress_callback({"phase": "da_start", "completed": 0, "total": cfg.T if periods is None else min(cfg.T, periods)})
    da = run_da(case, verbose=True, stage_callback=progress_callback) if progress_callback else run_da(case, verbose=True)
    t_da = time.perf_counter() - t0
    if progress_callback:
        progress_callback({"phase": "da_done", "da": da})
    def emit(event):
        if progress_callback:
            progress_callback(dict(event, da=da))
    rr = rolling.run_rolling(da, periods=periods, log=log, progress_callback=emit, should_cancel=should_cancel)
    if should_cancel and should_cancel():
        from .live_runner import RunCancelled
        raise RunCancelled("Cancelled before settlement")
    t1 = time.perf_counter()
    rt_tab = rolling.rt_tables(da, rr)
    id_tab = rolling.intraday_tables(da, rr)
    st = settlement.settle(da, rr)
    t_set = time.perf_counter() - t1
    from .live_runner import completion_metadata
    return dict(**completion_metadata(cfg.T,len(rr.rt)),da=da, rr=rr, rt=rt_tab, id=id_tab, settle=st, checks=checks(da, rr, rt_tab),
                time_s=dict(da=t_da, intraday=rr.time_s["intraday"], realtime=rr.time_s["realtime"], settlement=t_set,
                            total=time.perf_counter() - t0))


def summarize_full(res) -> dict:
    da, st, rt = res["da"], res["settle"], res["rt"]
    cfg = da.case.cfg
    T = len(res["rr"].rt)
    pda, prt = st["prices"]["da"], st["prices"]["rt"]
    net = da.case.net
    load_da = np.array([[da.case.load_bus.get(b, np.zeros(cfg.T))[t] for t in range(T)] for b in net.buses])
    load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
    wda = (pda * load_da).sum(0) / load_da.sum(0)
    wrt = (prt * load_rt).sum(0) / load_rt.sum(0)
    sp = prt - pda
    bt = st["by_type"].set_index("type")
    fe = bt.reindex(["load", "wind", "pv"]).fillna(0)
    from .live_runner import completion_metadata
    scope = completion_metadata(cfg.T, T)
    s = dict(
        **scope,
        rule_set=cfg.rule_set, rule_name=cfg.mode["name"], date=cfg.base["date"], season=cfg.base["season"], periods=T,
        intraday=dict(enabled=bool(cfg.mode["intraday"].get("enabled", False)), interval_h=cfg.mode["intraday"]["interval_h"], rounds=len(res["rr"].id_rounds),
                      lock_types=cfg.mode["intraday"]["lock_types"]),
        realtime=dict(interval_min=cfg.mode["realtime"]["interval_min"], lookahead_h=cfg.mode["realtime"]["lookahead_h"],
                      storage_mode=cfg.mode["realtime"]["storage"]["mode"], anchor=cfg.mode["realtime"]["storage"]["anchor"],
                      x=cfg.mode["realtime"]["storage"]["x"], hydro=cfg.mode["realtime"].get("hydro", "fixed_da"),
                      anchor_relaxed_periods=res["checks"]["anchor_relaxed_periods"],
                      anchor_slack_periods=res["checks"]["anchor_slack_periods"],
                      anchor_slack_max_mwh=res["checks"]["anchor_slack_max_mwh"],
                      anchor_slack_max_mwh_legacy_note=res["checks"]["anchor_slack_max_mwh_legacy_note"],
                      storage_anchor_slack_max_mwh=res["checks"]["storage_anchor_slack_max_mwh"],
                      pumped_anchor_slack_max_1e4m3=res["checks"]["pumped_anchor_slack_max_1e4m3"],
                      storage_anchor_slack_periods=res["checks"]["storage_anchor_slack_periods"],
                      pumped_anchor_slack_periods=res["checks"]["pumped_anchor_slack_periods"],
                      hydro_anchor_slack_max_1e4m3=res["checks"]["hydro_anchor_slack_max_1e4m3"],
                      hydro_anchor_slack_periods=res["checks"]["hydro_anchor_slack_periods"],
                      tracking_policy=cfg.mode["realtime"].get("tracking_policy"),
                      repriced_periods=int(rt["system"].repriced.sum()),
                      shed_mwh=float(rt["system"].shed_mw.sum() * cfg.dt), surplus_mwh=float(rt["system"].surplus_mw.sum() * cfg.dt),
                      curtail_mwh=float(rt["system"].curtail_mw.sum() * cfg.dt)),
        da=dict(objective=da.sced_info["objective"], scuc_objective=da.scuc_info["objective"], gap=da.scuc_info["gap"],
                curtail_mwh=float(da.tables["units"].curtail_mw.sum() * cfg.dt)),
        spread=dict(da_lw_avg=float((wda * load_da.sum(0)).sum() / load_da.sum()),
                    rt_lw_avg=float((wrt * load_rt.sum(0)).sum() / load_rt.sum()),
                    lw_spread_mean=float((wrt - wda).mean()), lw_spread_abs_mean=float(np.abs(wrt - wda).mean()),
                    lw_spread_max=float((wrt - wda).max()), lw_spread_min=float((wrt - wda).min()),
                    nodal_spread_abs_max=float(np.abs(sp).max()), nodal_spread_rms=float(np.sqrt((sp ** 2).mean())),
                    rt_lmp_min=float(prt.min()), rt_lmp_max=float(prt.max()),
                    da_lmp_min=float(pda.min()), da_lmp_max=float(pda.max()),
                    da_lw_tail=float((wda[84:] * load_da.sum(0)[84:]).sum() / load_da.sum(0)[84:].sum()) if T > 84 else float("nan"),
                    rt_lw_tail=float((wrt[84:] * load_rt.sum(0)[84:]).sum() / load_rt.sum(0)[84:].sum()) if T > 84 else float("nan"),
                    rt_lw_hourly=[float(x) for x in (wrt[: T // 4 * 4].reshape(-1, 4).mean(1))],
                    da_lw_hourly=[float(x) for x in (wda[: T // 4 * 4].reshape(-1, 4).mean(1))]),
        settlement_by_type={k: {c: float(v) for c, v in r.items()} for k, r in bt.iterrows()},
        forecast_error=dict(dev_abs_mwh=float(fe.dev_abs_mwh.sum()), rt_amount=float(fe.rt_amount.sum()),
                            dev_pnl=float(fe.dev_pnl.sum()),
                            by_type={k: dict(dev_mwh=float(fe.loc[k, "dev_mwh"]), dev_abs_mwh=float(fe.loc[k, "dev_abs_mwh"]),
                                             rt_amount=float(fe.loc[k, "rt_amount"]), dev_pnl=float(fe.loc[k, "dev_pnl"]))
                                     for k in fe.index}),
        reconciliation=st["reconciliation"].set_index("stage").to_dict(orient="index"),
        checks=res["checks"], time_s=res["time_s"],
        pumped=pumped_full_summary(res),
        running_hours=running_hours(res),
    )
    return s


def pumped_full_summary(res) -> dict:
    """抽蓄：日前/实时发电抽水电量、小时数、综合效率及两结算电能量净收入；不计算容量市场收入。"""
    da, st, rt = res["da"], res["settle"], res["rt"]
    cfg = da.case.cfg
    dt = cfg.dt
    out = {}
    T = len(res["rr"].rt)
    ent = st["by_entity"].set_index("entity") if "entity" in st["by_entity"] else st["by_entity"]
    for p in cfg.units.get("pumped", []):
        pid = p["id"]
        dps = da.tables["pumped"]
        dps = dps[(dps.unit == pid) & (dps.period < T)]
        rps = rt["pumped"][rt["pumped"].unit == pid]
        stc = cfg.mode["realtime"]["storage"]
        o = dict(bus=p["bus"], rules="storage", da_mode=(da.case.bids["units"].get(pid) or {}).get("mode", "price_quantity"),
                 rt_mode=stc.get("mode", "sced_anchor") + ("/" + stc.get("anchor", "hourly_eq") if stc.get("mode", "sced_anchor") == "sced_anchor" else ""))
        for tag, d in (("da", dps), ("rt", rps)):
            eg, ep = float(d.P_gen.sum() * dt), float(d.P_pump.sum() * dt)
            o[tag] = dict(gen_mwh=eg, pump_mwh=ep, cycle_eff=eg / ep if ep > 1e-6 else None,
                          gen_hours=float((d.P_gen > 1e-3).sum() * dt), pump_hours=float((d.P_pump > 1e-3).sum() * dt),
                          V_up_end=float(d.V_up.iloc[-1]) if len(d) else None)
        rev = {}
        for kind in ("发电", "抽水"):
            e = f"{pid}_{kind}"
            if e in ent.index:
                r = ent.loc[e]
                rev[kind] = {c: float(r[c]) for c in ("da_amount", "rt_amount", "total") if c in r}
        o["settlement"] = rev
        o["energy_net_revenue"] = float(sum(v.get("total", 0.0) for v in rev.values()))
        out[pid] = o
    return out


def running_hours(res) -> dict:
    """运行小时（日前 / 实时）：火电各类型开机小时、水电发电小时、抽蓄发电/抽水小时。"""
    da, rt = res["da"], res["rt"]
    dt = da.case.cfg.dt
    T = len(res["rr"].rt)
    out = {}
    for tag, u in (("da", da.tables["units"][da.tables["units"].period < T]), ("rt", rt["units"])):
        th = u[u.type.isin(["coal", "gas_ccgt", "gas_gt"])]
        o = {f"{k}_unit_hours": float((g.on > 0).sum() * dt) for k, g in th.groupby("type")}
        o["thermal_unit_hours"] = float((th.on > 0).sum() * dt)
        if tag == "da":
            # Match scuc.prepare: per-case initial state overrides configured init.
            initial = {g["id"]: g["init"] for g in da.case.cfg.units["thermal"]}
            initial.update((getattr(da.case, "init", None) or {}).get("thermal", {}))
        else:
            states = res["rr"].states
            if len(th) and not states:
                raise ValueError("Thermal start reporting requires actual RT initial state")
            initial = states[0].thermal if states else {}
        starts = 0
        for unit, group in th.groupby("unit"):
            if unit not in initial:
                raise ValueError(f"Thermal start reporting missing initial state for {unit}")
            online = group.sort_values("period").on.to_numpy(dtype=float)
            prior = float(initial[unit]["online"])
            if not np.isfinite(np.r_[prior, online]).all():
                raise ValueError("Thermal start reporting requires finite online states")
            starts += int(np.count_nonzero(np.diff(np.r_[prior > 0, online > 0].astype(int)) > 0))
        o["thermal_starts"] = starts
        hy = u[u.type == "hydro"]
        o["hydro_gen_hours"] = {k: float((g.P_mw > 1e-3).sum() * dt) for k, g in hy.groupby("unit")}
        ps = u[u.type == "pumped"]
        o["pumped_gen_hours"] = float((ps.P_mw > 1e-3).sum() * dt)
        o["pumped_pump_hours"] = float((ps.P_mw < -1e-3).sum() * dt)
        out[tag] = o
    return out


def actual_state_evidence(state) -> dict:
    """Every SysState field; history includes executed indices [0,t), never future NaNs.

    Truncated histories are sufficient to construct a window at t. A simulator
    continuing to append state must reallocate its future buffers separately.
    """
    from dataclasses import asdict
    raw=asdict(state)
    raw["hydro_hist"]={h:np.asarray(a)[:state.t].tolist() for h,a in state.hydro_hist.items()}
    raw["pumped_hist"]={p:{key:np.asarray(a)[:state.t].tolist() for key,a in hist.items()}
                        for p,hist in state.pumped_hist.items()}
    def convert(x):
        if isinstance(x,dict):return {str(k):convert(v) for k,v in x.items()}
        if isinstance(x,(tuple,list)):return [convert(v) for v in x]
        if isinstance(x,np.ndarray):return convert(x.tolist())
        if isinstance(x,np.generic):return convert(x.item())
        if isinstance(x,float) and not np.isfinite(x):
            raise ValueError("Executed initial-state evidence contains a non-finite value")
        return x
    return convert(raw)


def write_pricing_evidence(da, rounds, directory: Path) -> dict:
    """Export clone diagnostics without ever substituting clone dispatch quantities."""
    def numeric(value):
        if isinstance(value, dict): return {str(k): numeric(v) for k,v in value.items()}
        if isinstance(value, (list, tuple)): return [numeric(v) for v in value]
        if isinstance(value, np.ndarray): return numeric(value.tolist())
        if isinstance(value, np.generic): return numeric(value.item())
        if isinstance(value, float) and not np.isfinite(value): return None
        return value
    evidence = dict(physical_quantity_source="formal_fixed_integer_sced",
        settlement_quantity_source="formal_DA_and_executed_RT_first_period",
        pricing_quantities_executable=False,
        raw_prices="selected_price_solve_duals",settlement_prices="selected_raw_prices_clipped_to_market_limits",
        da=getattr(da,"pricing_info",None),
        rt_windows=[dict(window_start=r["t"],diagnostics=r.get("pricing_info")) for r in rounds])
    (directory/"auxiliary_pricing_evidence.json").write_text(json.dumps(numeric(evidence),ensure_ascii=False,allow_nan=False),encoding="utf-8")
    scopes=[("DA",0,evidence["da"])]+[("RT",r["window_start"],r["diagnostics"]) for r in evidence["rt_windows"]]
    for key in ("bands","slack_differences","reserve_differences","state_differences","removed_rt_tracking","anchor_costs"):
        rows=[dict(stage=stage,window_start=start,**row) for stage,start,info in scopes
              if info is not None for row in info.get(key,[])]
        pd.DataFrame(rows).to_csv(directory/f"auxiliary_pricing_{key}.csv",index=False)
    return evidence


def write_full_outputs(res, d: Path, make_plots=True) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    da, st, rt, idt = res["da"], res["settle"], res["rt"], res["id"]
    from .live_runner import completion_metadata, atomic_json
    scope = completion_metadata(da.case.cfg.T, len(res["rr"].rt))
    atomic_json(d/"output_scope.json",scope)
    state_evidence=dict(scope="actual_initial_state_before_each_executed_RT_window",
        history_scope="absolute executed indices [0,t); future buffers intentionally omitted",
        state_fields="all SysState dataclass fields; pumped_used is cumulative executed starts",
        windows=[actual_state_evidence(state) for state in res["rr"].states[:len(res["rr"].rt)]],
        final_executed_state=actual_state_evidence(res["rr"].states[-1]) if len(res["rr"].states)==len(res["rr"].rt)+1 else None)
    (d/"rolling_actual_states.json").write_text(json.dumps(state_evidence,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    write_pricing_evidence(da, res["rr"].rt, d)
    stage_data = {"da": getattr(da, "stage_diagnostics", {}), "rt_windows": [{"t": r["t"], "stages": r.get("stage_diagnostics", {})} for r in res["rr"].rt]}
    (d / "stage_diagnostics.json").write_text(json.dumps(stage_data, ensure_ascii=False, default=float), encoding="utf-8")
    cfg = da.case.cfg
    tracking_rows = [dict(window_start=r["t"], **row) for r in res["rr"].rt for row in r["snap"].get("tracking", [])]
    (d / "rt_tracking_window_evidence.json").write_text(json.dumps(tracking_rows, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(tracking_rows).to_csv(d / "rt_tracking_windows.csv", index=False)
    from .results import reserve_snapshot
    reserve_evidence={"da":reserve_snapshot(da.model),"rt_windows":[r["snap"]["reserve"] for r in res["rr"].rt]}
    (d / "reserve_window_evidence.json").write_text(json.dumps(reserve_evidence,ensure_ascii=False),encoding="utf-8")
    # Full-precision window evidence for independent hydro state/lag/target audits.
    def hydro_evidence(sn):
        data = {key: {h: np.asarray(a).tolist() for h,a in sn.get(key, {}).items()}
                for key in ("Ph", "Vh", "Qout")}
        data["delta"] = [{"station": h, "zone": int(z), "values": np.asarray(a).tolist()}
                         for (h,z),a in sn.get("delta", {}).items()]
        data["constraints"] = sn.get("hydro_constraints", {})
        return data
    evidence = dict(dt_h=cfg.dt, season=cfg.base["season"], hydro_mode=cfg.mode["realtime"].get("hydro"),
                    tracking_policy=cfg.mode["realtime"].get("tracking_policy"),
                    specifications=cfg.units["hydro"], da=hydro_evidence(rolling.snapshot(da.model)), rt_windows=[])
    for r,state in zip(res["rr"].rt,res["rr"].states):
        evidence["rt_windows"].append(dict(t=r["t"], snapshot=hydro_evidence(r["snap"]),
            state=dict(volume=state.hydro, power=getattr(state,"hydro_p",{}), online=getattr(state,"hydro_online",{}),
                       energy_used_mwh=state.hydro_used,
                       outflow_history={h:np.asarray(a[:state.t]).tolist() for h,a in state.hydro_hist.items()})))
    (d / "hydro_window_evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,
        default=lambda x:x.tolist() if hasattr(x,"tolist") else float(x)),encoding="utf-8")
    f = dict(index=False, float_format="%.4f")
    da.tables["units"].to_csv(d / "da_unit_awards.csv", **f)
    da.tables["prices"].to_csv(d / "da_nodal_prices.csv", **f)
    da.tables["storage"].to_csv(d / "da_storage.csv", **f)
    da.tables["hydro"].to_csv(d / "da_hydro.csv", **f)
    da.tables["pumped"].to_csv(d / "da_pumped.csv", **f)
    idt["summary"].to_csv(d / "id_rounds_summary.csv", **f)
    idt["plans"].to_csv(d / "id_plans.csv", **f)
    idt["prices"].to_csv(d / "id_prices_indicative.csv", **f)
    idt["storage"].to_csv(d / "id_storage.csv", **f)
    for k in ("units", "prices", "lines", "storage", "system", "pumped", "hydro"):
        rt[k].to_csv(d / f"rt_{k}.csv", **f)
    rt["prices"].pivot(index="bus", columns="period", values="lmp").to_csv(d / "rt_lmp_matrix.csv", float_format="%.2f")
    st["detail"].assign(settlement_scope=scope["settlement_scope"], completed_scope=scope["completed_scope"], expected_periods=scope["expected_periods"], executed_periods=scope["executed_periods"]).to_csv(d / "settlement_detail.csv", **f)
    st["by_entity"].assign(settlement_scope=scope["settlement_scope"], completed_scope=scope["completed_scope"], expected_periods=scope["expected_periods"], executed_periods=scope["executed_periods"]).to_csv(d / "settlement_by_entity.csv", **f)
    st["by_type"].assign(settlement_scope=scope["settlement_scope"], completed_scope=scope["completed_scope"], expected_periods=scope["expected_periods"], executed_periods=scope["executed_periods"]).to_csv(d / "settlement_by_type.csv", **f)
    st["reconciliation"].to_csv(d / "settlement_reconciliation.csv", **f)
    st["line_rent"].to_csv(d / "settlement_line_rent.csv", **f)
    pd.DataFrame(res["checks"].get("hydro_window_records",[])).to_csv(d / "hydro_window_targets.csv",**f)
    from .profit import analyze as analyze_profit
    profits=analyze_profit(res)
    for key in ("by_period","by_unit","by_type"):
        profits[key].assign(completed_scope=scope["completed_scope"],settlement_scope=scope["settlement_scope"],
                            expected_periods=scope["expected_periods"],executed_periods=scope["executed_periods"]).to_csv(d/f"operating_profit_{key}.csv",**f)
    atomic_json(d/"fixed_cost_profile.json",profits["profile"])
    s = summarize_full(res)
    s["operating_profit"]=dict(profits["summary"],**scope)
    provenance_path=d/"run_provenance.json"
    if provenance_path.exists():
        provenance=json.loads(provenance_path.read_text(encoding="utf-8"))
        s["provenance"]=dict(record="run_provenance.json",captured_at_utc=provenance["captured_at_utc"],
            git_head=provenance["git_head"],git_dirty=provenance["git_dirty"],
            source_fingerprint=provenance["source"]["content_fingerprint"],input_fingerprint=provenance["inputs"]["content_fingerprint"])
    else:
        s["provenance"]=dict(available=False,reason="run-start provenance not captured; later source is not substituted")
    s["da_summary"] = summarize(da)
    from .reserve_checks import audit as audit_reserve_evidence
    s["reserve"] = audit_reserve_evidence(reserve_evidence)
    s["reserve"].update(storage_reserve_enabled=reserve_evidence["da"]["storage_reserve_enabled"],
                        down_enabled=reserve_evidence["da"]["down_enabled"],T_dur_h=reserve_evidence["da"]["T_dur_h"],
                        scope="DA_and_all_RT_planning_windows;RT_shortage_max_uses_executed_first_period_only;no_activation_network_or_response_certification")
    from . import regulation, regulation_results
    da_reg, da_reg_system = regulation_results.table_for_snapshot(regulation.snapshot(da.model), da.prices.get("regulation", {}))
    da_reg.to_csv(d / "da_regulation_indicative.csv", **f)
    da_reg_system.to_csv(d / "da_regulation_system_indicative.csv", **f)
    rt["reserve"].to_csv(d / "rt_reserve.csv", **f)
    rt["reserve_system"].to_csv(d / "rt_reserve_system.csv", **f)
    pd.DataFrame(reserve_evidence["da"]["resources"]).to_csv(d / "da_reserve.csv",**f)
    pd.DataFrame(reserve_evidence["da"]["system"]).to_csv(d / "da_reserve_system.csv",**f)
    rt["regulation"].to_csv(d / "rt_regulation.csv", **f)
    rt["regulation_system"].to_csv(d / "rt_regulation_system.csv", **f)
    s["regulation"] = regulation_results.summarize(rt["regulation"], rt["regulation_system"])
    agc_snapshots = {"da": regulation.snapshot(da.model), "rt_windows": [r["snap"].get("regulation", {}) for r in res["rr"].rt]}
    (d / "regulation_window_snapshots.json").write_text(json.dumps(agc_snapshots, ensure_ascii=False), encoding="utf-8")
    if s["regulation"]["enabled"]:
        diagnostics = [x["diagnostics"] for x in [agc_snapshots["da"], *agc_snapshots["rt_windows"]]]
        for key in ("constraint_violation_max_mw", "energy_constraint_violation_max_mwh"):
            s["regulation"][key] = max(x[key] for x in diagnostics)
        s["regulation"]["slack_budget_final_max_violation"] = max(x["slack_budget"]["final_max_violation"] for x in [agc_snapshots["da"], *agc_snapshots["rt_windows"]])
        s["regulation"]["slack_budget_auxiliary_max_violation"] = max(x["slack_budget"]["auction_max_violation"] for x in [agc_snapshots["da"], *agc_snapshots["rt_windows"]])
        s["regulation"]["da_shortage_max_mw"] = max(agc_snapshots["da"]["shortage_mw"], default=0.)
        s["regulation"]["stage_counts"] = dict(energy_scuc=1+len(res["rr"].rt)+len(res["rr"].id_rounds), regulation_allocation_lp=1+len(res["rr"].rt)+len(res["rr"].id_rounds), final_energy_sced=1+len(res["rr"].rt)+len(res["rr"].id_rounds))
        s["regulation"]["physical_check_scope"] = "DA_and_all_RT_lookahead_windows_AGC_mode_headroom_response_baseline_ramp_SOC_budget;not_seconds_AGC_or_network_activation_certificate"
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    if make_plots:
        net = da.case.net
        T = s["periods"]
        load_da = np.array([[da.case.load_bus.get(b, np.zeros(96))[t] for t in range(T)] for b in net.buses])
        load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
        plots.da_vs_rt_lmp(st["prices"]["da"], st["prices"]["rt"], load_da, load_rt, list(net.buses), d / "lmp_da_vs_rt.png")
        plots.rolling_plans(idt["plans"], idt["storage"], rt["units"], rt["storage"], da.tables["units"], da.tables["storage"],
                            d / "rolling_plans.png", x=cfg.mode["realtime"]["storage"]["x"]
                            if cfg.mode["realtime"]["storage"]["mode"] == "sced_anchor" and cfg.mode["realtime"]["storage"]["anchor"] == "window_end_pct" else None)
        plots.settlement_bars(st["by_type"], st["by_entity"], d / "settlement_bars.png")
    return s
