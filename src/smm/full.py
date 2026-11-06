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



def _finite_number(value):
    try:
        return isinstance(value, (int, float, np.number)) and not isinstance(value, (bool, np.bool_)) and np.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def slack_policy_checks(da_slack, rt_rounds, tolerance=1e-6, *, state_band_required=False) -> dict:
    """Window maxima are not executed-period violations; report them separately."""
    keys = ("shed_mwh", "surplus_mwh", "line_slack_max", "reserve_up_short_max",
            "reserve_dn_short_max", "anchor_slack_max_mwh")
    summaries = [da_slack] + [r["slack"] for r in rt_rounds]
    # Legacy callers without value-method evidence retain their explicitly scoped
    # check. Current value-mode calls require the new field, never infer zero.
    state_band_present = any("state_band_slack_max" in summary for summary in summaries)
    if state_band_required or state_band_present:
        keys += ("state_band_slack_max",)
    complete = bool(rt_rounds) and all(
        all(k in summary and _finite_number(summary[k]) for k in keys) for summary in summaries)
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
        "state_band_slack_summary_available": bool(all(
            "state_band_slack_max" in summary and _finite_number(summary["state_band_slack_max"])
            for summary in summaries)),
        "state_band_slack_max": (float(max(summary["state_band_slack_max"] for summary in summaries))
                                 if all("state_band_slack_max" in summary and _finite_number(summary["state_band_slack_max"])
                                        for summary in summaries) else None),
        "state_band_slack_max_note": "Mixed native-unit diagnostic maximum, not an energy quantity; use typed state-band fields.",
        "rules_satisfied_no_slack": bool(complete and all(
            abs(summary[k]) <= tolerance for summary in summaries for k in keys)),
        "no_slack_scope": "DA_entire_horizon_and_each_RT_lookahead_window:shed,surplus,line,reserve_up,reserve_down,anchor,state_band_when_required_or_recorded;not_a_full_model_or_uncertainty_certificate",
    }


def _terminal_policy(cfg):
    policy = cfg.mode.get("realtime", {}).get("value_method", {}).get("terminal_policy", {})
    mode = policy.get("mode", "hard")
    coefficient = policy.get("penalty_yuan_per_discharge_mwh")
    if mode not in ("hard", "soft") or (mode == "soft" and (
            not _finite_number(coefficient) or float(coefficient) <= 0)):
        raise ValueError("invalid frozen terminal policy")
    return mode, float(coefficient) if mode == "soft" else None


def _discharge_factor(cfg, kind, unit):
    family = {"E": "storage", "Vu": "pumped", "V": "hydro"}[kind]
    spec = next(d for d in cfg.units[family] if d["id"] == unit)
    factor = spec["eta_d"] if kind == "E" else (spec["e_gen"] if kind == "Vu" else 1./(.36*spec["water_rate"]))
    if not _finite_number(factor) or factor <= 0:
        raise ValueError("invalid discharge-energy conversion")
    return float(factor)


def intraday_failure_checks(cfg, rr, da_plan, tolerance=1e-6):
    """A failed ID attempt is neither a success nor permission to reuse stale ID."""
    failures = getattr(rr, "id_failures", [])
    successes = getattr(rr, "id_rounds", [])
    valid = isinstance(failures, list)
    fs = [r for r in failures if isinstance(r, dict)] if valid else []
    valid = valid and len(fs) == len(failures)
    times = [r.get("tau") for r in fs]
    successful = [r.get("tau") for r in successes]
    statuses = {"infeasible", "terminationcondition.infeasible", "kinfeasible", "highsmodelstatus.kinfeasible"}
    for r in fs:
        tau = r.get("tau")
        valid = valid and (isinstance(tau, (int, np.integer)) and not isinstance(tau, bool)
            and 0 <= tau < cfg.T and r.get("phase") in ("scuc", "formal_sced")
            and str(r.get("solver_status", "")).lower() in statuses and r.get("fallback") == "day_ahead"
            and isinstance(r.get("reason"), str) and bool(r["reason"].strip()))
    try:
        valid = valid and len(set(times)) == len(times) and not set(times) & set(successful)
    except TypeError:
        valid = False
    source_ok = bool(valid)
    for r in rr.rt:
        past_f = [x for x in fs if isinstance(x.get("tau"), int) and x["tau"] <= r["t"]]
        past_s = [x for x in successes if x["tau"] <= r["t"]]
        if not past_f or max(x["tau"] for x in past_f) < max((x["tau"] for x in past_s), default=-1):
            continue
        if r.get("handoff_config", {}).get("intraday_attempted") is not True:
            source_ok = False
        metadata = r.get("reference_metadata", {})
        rc, ic = cfg.mode.get("realtime", {}), cfg.mode.get("intraday", {})
        # The frozen policy determines which resources would have inherited ID.
        # Always-DA resources legitimately have no failure reason, including
        # base-locked thermal, disabled handoffs and non-value state policies.
        use_id = rc.get("handoff_source", "intraday") == "intraday"
        flags = rc.get("handoff", {})
        for kind, key, flag in (("E", "E", "storage"), ("Vu", "Vu", "pumped"), ("V", "Vh", "hydro")):
            relevant = (rc.get("hydro", "value") == "value" if kind == "V"
                        else rc.get("storage", {}).get("mode", "value") == "value")
            requires_reason = use_id and flags.get(flag, True) and relevant
            for unit in da_plan.get(key, {}):
                m = metadata.get(kind, {}).get(unit, {})
                if m.get("trajectory_source") != "day_ahead" or (requires_reason and not m.get("fallback_reason")):
                    source_ok = False
        types = {u["id"]: u.get("type") for u in cfg.units.get("thermal", [])}
        rt_lock = set(rc.get("lock_types", ["coal", "gas_ccgt"]))
        inherited = set(ic.get("free_types", [])) if (use_id and flags.get("commitment", True)
                    and rc.get("intraday_commit_rt", "inherit") == "inherit") else set()
        for unit, m in metadata.get("u", {}).items():
            requires_reason = types.get(unit) in inherited - rt_lock
            if m.get("source") != "day_ahead" or (requires_reason and not m.get("fallback_reason")):
                source_ok = False
        for w in r.get("snap", {}).get("value_window", []):
            try:
                kind, unit, period = w["kind"], w["unit"], int(w["period"])
                key = {"E":"E", "Vu":"Vu", "V":"Vh"}[kind]
                expected = float(da_plan[key][unit][period])
                expected_value = 0. if period == cfg.T-1 else float(da_plan["state_values"][kind][unit]["value_native"][period])
                if (not _finite_number(w.get("reference_state")) or abs(w["reference_state"]-expected) > tolerance
                        or not _finite_number(w.get("value_native")) or abs(w["value_native"]-expected_value) > tolerance
                        or metadata[kind][unit].get("value_source") != "day_ahead"):
                    source_ok = False
            except (KeyError, TypeError, ValueError, IndexError):
                source_ok = False
        selected = r.get("thermal_reference_u", {})
        if set(selected) != set(metadata.get("u", {})):
            source_ok = False
        for unit, arr in selected.items():
            expected = np.asarray(da_plan.get("u", {}).get(unit, []))[r["t"]:r["t"]+len(arr)]
            if expected.shape != np.asarray(arr).shape or not np.array_equal(expected, arr):
                source_ok = False
        if cfg.mode.get("realtime", {}).get("hydro", "value") in ("value", "fixed_da", "window_end_da"):
            if set(r.get("hydro_reference_delta", {})) != set(da_plan.get("delta", {})):
                source_ok = False
        for key, arr in r.get("hydro_reference_delta", {}).items():
            expected = np.asarray(da_plan.get("delta", {}).get(key, []))[r["t"]:r["t"]+len(arr)]
            if expected.shape != np.asarray(arr).shape or not np.array_equal(expected, arr):
                source_ok = False
    return dict(intraday_failure_records_valid=bool(valid),
                intraday_failed_reference_evidence_complete=bool(source_ok))


def hydro_terminal_lookahead_checks(cfg, rr, da_plan, tolerance=1e-6, *, input_registry=None):
    """Independently check reference-continuation forecasts, never actual costs.

    Positive shortage is a finite preference cost, not a physical violation.
    Source flows come from saved DA/ID dispatch, not the diagnostic's own arrays.
    """
    if getattr(rr, 'actual_state_required', False) and not getattr(rr, 'states', None):
        raise ValueError('Continuous-day audit requires actual initial states')
    rc = cfg.mode.get("realtime", {})
    mode, coefficient = _terminal_policy(cfg)
    specs = {u["id"]: u for u in cfg.units.get("hydro", [])}
    active = (mode == "soft" and rc.get("hydro", "value") == "value" and bool(specs)
              and rc.get("value_method", {}).get("hydro_lookahead") == "reference_continuation")
    H = int(round(rc.get("lookahead_h", 2.) * 60 / rc.get("interval_min", 15)))
    errors, expected_count, checked_count, shortages, costs = [], 0, 0, [], []
    successes, failures = getattr(rr, "id_rounds", []), getattr(rr, "id_failures", [])
    rounds = {r["t"]: r for r in rr.rt}
    if active and (not rr.rt or len(rounds) != len(rr.rt)):
        errors.append("missing or duplicate RT windows")

    def number(value):
        if not _finite_number(value):
            raise ValueError("nonfinite or nonnumeric field")
        return float(value)

    def equal(value, expected, label):
        if not np.isclose(number(value), number(expected), atol=tolerance, rtol=1e-9):
            raise ValueError(label)

    def source_flow(r, unit, periods):
        meta = r["reference_metadata"]["V"][unit]
        label = meta["trajectory_source"]
        live = max((x for x in successes if x["tau"] <= r["t"]), key=lambda x:x["tau"], default=None)
        last_failure = max((x["tau"] for x in failures if x["tau"] <= r["t"]), default=-1)
        use_id = (cfg.mode.get("intraday", {}).get("enabled", False)
                  and rc.get("handoff_source", "intraday") == "intraday"
                  and rc.get("handoff", {}).get("hydro", True))
        if label == "day_ahead":
            if meta.get("round") is not None:
                raise ValueError("DA source has ID round")
            if use_id and (live is not None or last_failure >= 0) and not meta.get("fallback_reason"):
                raise ValueError("missing per-resource DA fallback reason")
            array, offset = da_plan["Qout"][unit], 0
        else:
            if (not use_id or live is None or live["tau"] <= last_failure
                    or label != f"intraday@{live['tau']}" or meta.get("round") != live["tau"]):
                raise ValueError("stale or invalid ID source")
            array, offset = live["snap"]["Qout"][unit], live["tau"]
        values = np.asarray([number(array[k-offset]) for k in periods])
        if (values < 0).any():
            raise ValueError("negative source outflow")
        return values, meta

    for r in rr.rt:
        t, t1 = r["t"], min(cfg.T, r["t"] + H)
        expected = set(specs) if active and t1 < cfg.T else set()
        expected_count += len(expected)
        rows = r.get("snap", {}).get("hydro_terminal_lookahead")
        if rows is None and not expected:
            rows = []  # Historical hard/last-day windows have no such term.
        if (not isinstance(rows, list) or any(not isinstance(w, dict) for w in rows)
                or len(rows) != len(expected) or {w.get("unit") for w in rows} != expected):
            errors.append(f"window {t}: missing/duplicate/unexpected hydro forecast rows")
            continue
        for w in rows:
            unit = w["unit"]
            try:
                spec = specs[unit];season = spec["seasons"][cfg.base["season"]]
                future = list(range(t1,cfg.T))
                if w["future_periods"] != future:
                    raise ValueError("future time grid")
                equal(w["relative_period"],t1-t-1,"relative period")
                for key, value in (("window_start",t),("tau",t1-1)):
                    if key in w:equal(w[key],value,key)
                if (w.get("kind") != "V" or w.get("native_unit") != "1e4m3"
                        or w.get("forecast_only") is not True or w.get("active") is not True
                        or w.get("slack_zero_means_target_met") is not True
                        or w.get("scope") != "reference_continuation_terminal_preference_not_feasibility_proof"):
                    raise ValueError("forecast scope/activation")
                outflow, meta = source_flow(r,unit,future)
                expected_label = meta["trajectory_source"]
                if (w.get("reference_source_label") != expected_label
                        or w.get("reference_source") != ("day_ahead" if expected_label == "day_ahead" else "intraday")
                        or w.get("reference_round") != meta.get("round")
                        or w.get("reference_fallback_reason") != meta.get("fallback_reason")):
                    raise ValueError("source metadata")
                equal(w["dt_h"],cfg.dt,"dt")
                from .natural_inflow import evidence_natural_values
                natural=evidence_natural_values(cfg,r['snap'],unit,future,source_registry=input_registry)
                if r['snap'].get('input_provenance') is not None:
                    claimed=np.asarray(w['natural_inflow_m3s'],float)
                    if claimed.shape!=natural.shape or not np.allclose(claimed,natural,atol=tolerance,rtol=0):
                        raise ValueError('natural inflow differs from selected issued source')
                else:
                    equal(w['natural_inflow_m3s'],season['qin'],'natural inflow')
                for key in ("reference_outflows_m3s","upstream_arrivals_m3s","upstream_arrival_sources"):
                    if len(w[key]) != len(future):raise ValueError("future array length")
                for actual, ref in zip(w["reference_outflows_m3s"],outflow):equal(actual,ref,"reference Qout")
                arrivals=[];upstream=spec.get("upstream")
                for i,k in enumerate(future):
                    shifted=k-int(spec.get("lag_periods",0))
                    if not upstream:
                        arrival,source=0.,"none_or_before_day"
                    elif shifted<0:
                        from .state_bridge import hydro_before
                        states = getattr(rr, 'states', None)
                        if states:
                            arrival=hydro_before(cfg, states[0], upstream, shifted)
                            source="pre_day_explicit_history" if getattr(states[0], 'continuation', {}) else "pre_day_initial_outflow"
                        else:
                            if w['upstream_arrival_sources'][i] != 'pre_day_initial_outflow':
                                raise ValueError('Explicit cross-day history missing from archive')
                            arrival=hydro_before(cfg, {}, upstream, shifted)
                            source='pre_day_initial_outflow'  # Explicit historical single-day evidence only.
                    elif shifted<t:
                        # Use independently stored executed first-period flow,
                        # not a forecast or the supplied arrival array.
                        arrival=rounds[shifted]["snap"]["Qout"][upstream][0]
                        source="executed_history"
                    else:
                        arrival=source_flow(r,upstream,[shifted])[0][0]
                        source="selected_reference"
                    equal(w["upstream_arrivals_m3s"][i],arrival,"upstream arrival")
                    if w["upstream_arrival_sources"][i] != source:raise ValueError("upstream arrival source")
                    arrivals.append(arrival)
                upstream_meta = r["reference_metadata"]["V"].get(upstream) if upstream else None
                if w.get("upstream_reference_metadata") != upstream_meta:raise ValueError("upstream metadata")
                recovery=float(np.sum(natural+np.asarray(arrivals)-outflow))*cfg.dt*.36
                target=((rr.states[0].hydro[unit] if getattr(rr,"states",None) else season["v0"]) if spec.get("regulation")=="run_of_river" else season["v_end"])
                state=r["snap"]["Vh"][unit][t1-t-1]
                projected=state+recovery;shortage=max(target-projected,0.)
                factor=1./(.36*spec["water_rate"])
                expected_values={"terminal_target":target,"reference_recovery_volume":recovery,
                    "minimum_window_end_volume":target-recovery,"state":state,
                    "projected_terminal_volume":projected,"projected_terminal_shortfall":shortage,
                    "actual_projected_shortfall":shortage,"factor":factor,"discharge_mwh_per_native_unit":factor,
                    "projected_terminal_shortfall_mwh":shortage*factor,
                    "penalty_yuan_per_native_unit":coefficient*factor,
                    "penalty_yuan_per_discharge_mwh":coefficient,"penalty_cost":shortage*factor*coefficient}
                for key,value in expected_values.items():equal(w[key],value,key)
                checked_count+=1;shortages.append(shortage);costs.append(shortage*factor*coefficient)
            except (KeyError,IndexError,TypeError,ValueError,ZeroDivisionError) as exc:
                errors.append(f"window {t} {unit}: {exc}")
    complete=not errors and checked_count==expected_count
    return dict(hydro_terminal_lookahead_checks_active=active,
        hydro_terminal_lookahead_evidence_complete=bool(complete),
        hydro_terminal_lookahead_expected_records=expected_count,
        hydro_terminal_lookahead_checked_records=checked_count,
        hydro_terminal_lookahead_errors=errors[:20],
        hydro_terminal_lookahead_max_forecast_shortfall_1e4m3=max(shortages,default=0.) if complete else None,
        hydro_terminal_lookahead_max_forecast_cost_yuan=max(costs,default=0.) if complete else None,
        hydro_terminal_lookahead_scope="reference_continuation_forecast_not_executed_cost_or_feasibility_proof")


def state_band_policy_checks(cfg, rounds, tolerance=1e-6, *, day_end_targets=None) -> dict:
    """Check detached value-band evidence, preserving units and execution scope.

    A feasible softened equation does not mean the original target was met.
    Window endpoints are forecasts; only the final executed first period is an
    actual day-end result. Missing evidence is unknown, never an implied zero.
    """
    rc = cfg.mode["realtime"]
    groups = [("E", "storage", "storage", "mwh"),
              ("Vu", "pumped", "pumped", "1e4m3"),
              ("V", "hydro", "hydro", "1e4m3")]
    expected = {(kind, d["id"]) for kind, group, _, _ in groups
                if (rc.get("hydro", "value") == "value" if kind == "V"
                    else rc.get("storage", {}).get("mode", "value") == "value")
                for d in cfg.units.get(group, [])}
    active = bool(expected)
    complete = not active or bool(rounds)
    try:
        terminal_mode, terminal_coefficient = _terminal_policy(cfg)
    except (ValueError, TypeError):
        terminal_mode, terminal_coefficient, complete = "invalid", None, False
    emergency_max = 0.
    executed_terminal_records = []
    equation_error = 0.
    maxima = {kind: 0. for kind, _, _, _ in groups}
    counts = {kind: 0 for kind, _, _, _ in groups}
    day_end = {kind: 0. for kind, _, _, _ in groups}
    executed_day_end = {kind: None for kind, _, _, _ in groups}
    records = []
    for r in rounds:
        rows = r.get("snap", {}).get("value_window")
        if rows is None:
            if active:
                complete = False
            continue
        if not isinstance(rows, list) or any(not isinstance(w, dict) for w in rows):
            complete = False
            continue
        keys = [(w.get("kind"), w.get("unit")) for w in rows]
        if len(keys) != len(set(keys)) or set(keys) != expected:
            complete = False
        round_max = 0.
        touched = set()
        for w in rows:
            kind, unit = w.get("kind"), w.get("unit")
            if (kind, unit) not in expected:
                complete = False
                continue
            try:
                actual = np.asarray(r["snap"][{"E": "E", "Vu": "Vu", "V": "Vh"}[kind]][unit], float)
                local, period = int(w["relative_period"]), int(w["period"])
                values = np.asarray([w[k] for k in ("state", "lower", "upper", "slack_up", "slack_dn")], float)
                if (not len(actual) or not np.isfinite(actual).all() or not np.isfinite(values).all()
                        or local != len(actual)-1 or local != w["relative_period"]
                        or period != w["period"] or period != int(r["t"])+local
                        or values[1] > values[2]+tolerance or min(values[3:]) < -tolerance
                        or not isinstance(w["soft"], (bool, np.bool_))):
                    raise ValueError("invalid state-band evidence")
                state, lo, hi, up, down = values
                is_day_end = period == cfg.T-1
                if w.get("label") != ("day_end" if is_day_end else "band"):
                    raise ValueError("incorrect endpoint label")
                if is_day_end and day_end_targets is not None:
                    expected_lo, expected_hi = day_end_targets[kind, unit]
                    if abs(lo-expected_lo) > tolerance or abs(hi-expected_hi) > tolerance:
                        raise ValueError("day-end band differs from actual DA/declared target")
                planned = is_day_end and terminal_mode == "soft"
                if terminal_mode == "soft" or "terminal_policy" in w:
                    if (w.get("terminal_policy") != terminal_mode
                            or w.get("planned_terminal_soft") is not planned):
                        raise ValueError("terminal policy evidence disagrees with frozen policy")
                residual = max(lo-state, state-hi, 0.)
                slack = up+down
                error = max(abs(state-actual[local]), abs(up-max(state-hi, 0.)), abs(down-max(lo-state, 0.)))
                if planned:
                    factor = _discharge_factor(cfg, kind, unit)
                    for name, expected_value in (("terminal_penalty_yuan_per_discharge_mwh", terminal_coefficient),
                            ("discharge_energy_factor", factor), ("deviation_discharge_mwh", residual*factor),
                            ("terminal_penalty_cost", residual*factor*terminal_coefficient)):
                        if not _finite_number(w.get(name)) or not np.isclose(w[name], expected_value, atol=tolerance, rtol=1e-9):
                            raise ValueError("terminal conversion/cost evidence mismatch")
                    if local == 0:
                        executed_terminal_records.append(dict(kind=kind, unit=unit, deviation_native=float(residual),
                            deviation_discharge_mwh=residual*factor, penalty_cost=residual*factor*terminal_coefficient))
                else:
                    emergency_max = max(emergency_max, slack)
                if not w["soft"] and not planned:
                    error = max(error, slack, residual)
                equation_error = max(equation_error, error)
                maxima[kind] = max(maxima[kind], slack)
                round_max = max(round_max, slack)
                if slack > tolerance:
                    touched.add(kind)
                if is_day_end:
                    day_end[kind] = max(day_end[kind], residual)
                    if local == 0:
                        executed_day_end[kind] = max(executed_day_end[kind] or 0., residual)
                records.append(dict(window_start=int(r["t"]), period=period, kind=kind, unit=unit,
                                    label=w["label"], deviation_native=float(residual), slack_native=float(slack),
                                    executed=local == 0, soft=bool(w["soft"]), planned_terminal_soft=planned))
            except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                complete = False
        for kind in touched:
            counts[kind] += 1
        scalar = r.get("slack", {}).get("state_band_slack_max")
        if active and (not _finite_number(scalar) or abs(float(scalar)-round_max) > tolerance):
            complete = False
    out = dict(state_band_checks_active=active, state_band_checks_available=bool(complete),
               state_band_constraints_satisfied=bool(complete and equation_error <= tolerance),
               state_band_targets_satisfied=bool(complete and equation_error <= tolerance
                                                 and max(maxima.values()) <= tolerance),
               state_band_equation_max_error=float(equation_error), state_band_records=records,
               state_band_check_scope="formal_RT_window_endpoints;executed_day_end_only_when_relative_period_zero;native_units_not_summed")
    out.update(terminal_policy_mode=terminal_mode,
        terminal_policy_compliant=bool(complete and equation_error <= tolerance
            and (terminal_mode == "soft" or max(day_end.values()) <= tolerance)),
        planned_terminal_deviation_records=[x for x in records if x["planned_terminal_soft"]],
        executed_terminal_deviation_records=executed_terminal_records,
        executed_terminal_penalty_cost=(sum(x["penalty_cost"] for x in executed_terminal_records)
            if complete and executed_terminal_records else None),
        executed_terminal_targets_zero_deviation=(all(x is not None and x <= tolerance for kind,x in executed_day_end.items()
            if any(k == kind for k,u in expected)) if complete and any(x is not None for x in executed_day_end.values()) else None),
        emergency_state_band_slack_max=emergency_max if complete else None,
        emergency_band_fallback_count=sum(bool(r.get("fallback")) for r in rounds))
    for kind, _, label, unit in groups:
        out[f"{label}_state_band_slack_max_{unit}"] = maxima[kind] if complete else None
        out[f"{label}_state_band_slack_periods"] = counts[kind] if complete else None
        out[f"{label}_day_end_window_deviation_max_{unit}"] = day_end[kind] if complete else None
        out[f"{label}_executed_day_end_deviation_{unit}"] = executed_day_end[kind] if complete else None
    return out


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
               hydro_reference_zone_count_mismatch=0, hydro_reference_evidence_complete=True,
               hydro_unit_count_violation_max=0., hydro_stable_zone_power_excess_max_mw=0.,
               hydro_window_records=[])
    soft_tracking = (cfg.mode["realtime"].get("tracking_policy", {}).get("mode") == "soft_deliverable_energy"
                     and cfg.mode["realtime"].get("hydro", "value") == "window_end_da")   # 旧软跟踪仅 window_end_da
    specs = {h["id"]: h for h in cfg.units["hydro"]}
    da_sn = rolling.snapshot(da_res.model)
    if len(rr.states) not in (len(rr.rt),len(rr.rt)+1) or [r["t"] for r in rr.rt] != list(range(len(rr.rt))):
        out["hydro_window_checks_available"] = False
    def check_snapshot(sn, stage, start, state=None, round_result=None):
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
            try:
                from .natural_inflow import evidence_natural_values
                expected_natural=evidence_natural_values(cfg,sn,h,range(start,start+len(power)),
                    source_registry=getattr(da_res.case,"input_provider",None))
                if not np.allclose(np.asarray(meta['qin_m3s']),expected_natural,atol=tolerance,rtol=0):
                    raise ValueError('natural flow diagnostic differs from frozen selected inputs')
            except (ValueError,KeyError,TypeError):
                out['hydro_window_checks_available']=False
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
            # Independent aggregate physical checks, regardless of selected plan.
            if "n_units" in spec and "zones" in spec:
                expected_zone_keys = {(h, z) for z in range(len(spec["zones"]))}
                actual_zone_keys = {key for key in sn["delta"] if key[0] == h}
                if actual_zone_keys != expected_zone_keys:
                    out["hydro_window_checks_available"] = False
                else:
                    counts = np.array([sn["delta"][h,z] for z in range(len(spec["zones"]))], float)
                    out["hydro_unit_count_violation_max"] = max(out["hydro_unit_count_violation_max"],
                        float(np.abs(counts-np.round(counts)).max()), float(np.maximum(-counts, 0.).max()),
                        float(np.maximum(counts.sum(axis=0)-spec["n_units"], 0.).max()))
                    minimum = np.array([z[0] for z in spec["zones"]]) @ counts
                    maximum = np.array([z[1] for z in spec["zones"]]) @ counts
                    out["hydro_stable_zone_power_excess_max_mw"] = max(out["hydro_stable_zone_power_excess_max_mw"],
                        float(np.maximum(minimum-power, 0.).max()), float(np.maximum(power-maximum, 0.).max()))
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
                reference_source = "not_locked"
                if cfg.mode["realtime"].get("hydro", "value") in ("value","fixed_da","window_end_da"):
                    selected = (round_result or {}).get("hydro_reference_delta")
                    sources = (round_result or {}).get("reference_metadata", {}).get("V", {})
                    reference_source = sources.get(h, {}).get("trajectory_source", "day_ahead_legacy" if selected is None else "unknown")
                    if selected is not None and reference_source == "unknown":
                        out["hydro_reference_evidence_complete"] = False
                    if selected is None:
                        selected = {key: a[start:start+len(power)] for key,a in da_sn["delta"].items()}
                        if (cfg.mode.get("intraday", {}).get("enabled", False)
                                and cfg.mode["realtime"].get("hydro", "value") == "value"):
                            out["hydro_reference_evidence_complete"] = False
                    for key, arr in sn["delta"].items():
                        if key[0] != h:
                            continue
                        expected = np.asarray(selected.get(key, []), float)
                        if (expected.shape != np.asarray(arr).shape or not np.isfinite(expected).all()
                                or not np.equal(expected, np.round(expected)).all()):
                            out["hydro_reference_evidence_complete"] = False
                            continue
                        mismatch = int(np.count_nonzero(np.asarray(arr) != expected))
                        out["hydro_reference_zone_count_mismatch"] += mismatch
                        if reference_source in ("day_ahead", "day_ahead_legacy"):
                            out["hydro_da_zone_count_mismatch"] += mismatch
                out["hydro_window_records"].append(dict(window_start=start,window_end_exclusive=start+len(power),station=h,
                    reference_source=reference_source,
                    start_volume_1e4m3=float(meta["v_start_1e4m3"]),start_power_mw=float(meta["p_start_mw"]),
                    terminal_target_1e4m3=target,terminal_actual_1e4m3=float(volume[-1]),terminal_error_1e4m3=terminal_error,terminal_kind=meta.get("terminal_kind"),
                    tracking_equation_error_1e4m3=tracking_equation_error,
                    water_balance_max_err_1e4m3=water_error,station_ramp_excess_max_mw=ramp_excess,
                    remaining_energy_budget_mwh=budget,window_generation_mwh=float(power.sum()*cfg.dt)))
    check_snapshot(da_sn,"DA",0)
    if getattr(da_res.case,'selected_inputs',None) is not None:
        for r in getattr(rr,'id_rounds',[]):
            check_snapshot(r['snap'],'ID',r['tau'])
    for r,state in zip(rr.rt,rr.states):
        check_snapshot(r["snap"],"RT",r["t"],state,r)
    numeric = [v for k,v in out.items() if k not in ("hydro_window_checks_available","hydro_window_records","hydro_tracking_target_deviation_max_1e4m3","hydro_reference_evidence_complete")]
    out["hydro_window_constraints_satisfied"] = bool(out["hydro_window_checks_available"] and out["hydro_reference_evidence_complete"] and all(np.isfinite(v) and v<=tolerance for v in numeric))
    out["hydro_check_scope"] = ("DA_ID_RT_selected_issue_inflow;" if getattr(da_res.case,"selected_inputs",None) is not None else "") + "DA_and_RT_station_aggregate_ramp,water,volume,eco,outflow,budget;RT_actual_state,selected_reference_zone_count,independent_unit_count_and_zone_power,hard_window_terminal_or_soft_tracking_slack_consistency_as_configured;soft_target_deviation_is_not_physical_infeasibility;not_individual_turbine_ramp"
    return out


def checks(da_res, rr, rt_tab) -> dict:
    """连续性与平衡性校核（测试同样使用）。"""
    from .result_retention import validate_compact_rounds
    validate_compact_rounds(rr)
    cfg = da_res.case.cfg
    dt = cfg.dt
    sto = {s["id"]: s for s in cfg.units["storage"]}
    out = {}
    # SOC 连续：每时段起点 = 上一时段实际终点，且满足充放电方程
    st = rt_tab["storage"]
    err_cont, err_eq = 0.0, 0.0
    for s, d in st.groupby("unit"):
        d = d.sort_values("period")
        e0 = float(rr.states[0].storage[s])
        prev = np.r_[e0, d.E_mwh.to_numpy()[:-1]]
        err_cont = max(err_cont, float(np.abs(d.E_start.to_numpy() - prev).max()))
        calc = d.E_start + (sto[s]["eta_c"] * d.P_ch - d.P_dis / sto[s]["eta_d"]) * dt
        err_eq = max(err_eq, float(np.abs(calc - d.E_mwh).max()))
        out.setdefault("storage_simultaneous", 0)
        out["storage_simultaneous"] += int(((d.P_ch > 1e-6) & (d.P_dis > 1e-6)).sum())
    out["soc_continuity_err_mwh"] = err_cont
    out["soc_equation_err_mwh"] = err_eq
    # 日内、实时分别按本阶段锁定类型校核；日内自由类仍可在实时按日前锁定。
    lock_id = set(cfg.mode.get("intraday", {}).get("lock_types", ["coal", "gas_ccgt"]))
    lock = set(cfg.mode["realtime"]["lock_types"])
    da_u = da_res.tables["units"].set_index(["unit", "period"]).on
    mism_id = mism_rt = 0
    ru = rt_tab["units"]
    for u in cfg.units["thermal"]:
        if u["type"] in lock_id:
            for r in rr.id_rounds:
                arr = r["snap"]["u"][u["id"]]
                mism_id += int(sum(int(arr[k]) != int(da_u[(u["id"], r["tau"] + k)]) for k in range(len(arr))))
        if u["type"] in lock:
            d = ru[ru.unit == u["id"]]
            mism_rt += int(sum(int(o) != int(da_u[(u["id"], p)]) for o, p in zip(d.on, d.period)))
    out["locked_commitment_mismatch_id"] = mism_id
    out["locked_commitment_mismatch_rt"] = mism_rt
    # 实时锁定机组开机状态 = 执行时所用参考计划（无日内时即日前）
    mism = 0
    thermal_reference_complete = True
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    for r in rr.rt:
        selected = r.get("thermal_reference_u")
        if selected is not None:
            required_locked = {g for g, typ in types.items() if typ in lock}
            metadata = r.get("reference_metadata", {}).get("u")
            ho = r.get("handoff_config", {})
            active_rounds = [x for x in getattr(rr, "id_rounds", []) if x["tau"] <= r["t"]]
            if (ho.get("intraday_attempted", bool(active_rounds)) and ho.get("source") == "intraday" and ho.get("commitment") is True
                    and ho.get("commit_rt") == "inherit"):
                free = set(ho.get("levels", {}).get("intraday", {}).get("free_types", []))
                required_locked |= {g for g, typ in types.items() if typ in free}
            if (set(selected) != required_locked or metadata is None or set(selected) != set(metadata)):
                thermal_reference_complete = False
            for g, expected in selected.items():
                actual = np.asarray(r["snap"].get("u", {}).get(g, []), float)
                expected = np.asarray(expected, float)
                if (g not in types or not len(actual) or expected.shape != actual.shape
                        or not np.isfinite(expected).all() or not np.isin(expected, [0, 1]).all()):
                    thermal_reference_complete = False
                    continue
                mism += int(np.count_nonzero(actual != expected))
                source = (metadata or {}).get(g, {})
                if source.get("source") == "day_ahead":
                    try:
                        baseline = np.array([da_u[(g, r["t"]+k)] for k in range(len(actual))], float)
                        mism += int(np.count_nonzero(expected != baseline))
                    except (KeyError, TypeError, ValueError):
                        thermal_reference_complete = False
                elif source.get("round") is not None:
                    history = next((x for x in active_rounds if x["tau"] == source["round"]), None)
                    baseline = np.asarray((history or {}).get("snap", {}).get("u", {}).get(g, []), float)
                    offset = r["t"]-int(source["round"])
                    baseline = baseline[offset:offset+len(actual)]
                    if baseline.shape != expected.shape or not np.isfinite(baseline).all():
                        thermal_reference_complete = False
                    else:
                        mism += int(np.count_nonzero(expected != baseline))
                else:
                    thermal_reference_complete = False
        else:
            # Old outputs carry only the reference plan. Do not reinterpret them
            # using new handoff semantics, and never silently certify inherited GT.
            plan = rr.plans[r["plan_idx"]]
            for g, arr in r["snap"]["u"].items():
                if types[g] in lock:
                    mism += int(arr[0] != plan["u"][g][r["t"]])
            if cfg.mode.get("intraday", {}).get("enabled", False):
                thermal_reference_complete = False
    out["rt_vs_plan_commitment_mismatch"] = mism
    out["thermal_reference_evidence_complete"] = thermal_reference_complete
    if getattr(rr, "id_failures", []):
        out.update(intraday_failure_checks(cfg, rr, rolling._da_plan_arrays(da_res)))
        thermal_reference_complete &= out["intraday_failed_reference_evidence_complete"]
        out["thermal_reference_evidence_complete"] = thermal_reference_complete
    else:
        out.update(intraday_failure_records_valid=True, intraday_failed_reference_evidence_complete=True)
    # 水电执行日前曲线
    rc = cfg.mode["realtime"]
    if rc.get("hydro", "value") == "fixed_da":
        dah = da_res.tables["units"].set_index(["unit", "period"]).P_mw
        dh = ru[ru.type == "hydro"]
        out["hydro_vs_da_err_mw"] = float(max([abs(p - dah[(u, t)]) for u, t, p in zip(dh.unit, dh.period, dh.P_mw)] or [0.0]))
    # 储能：锚点满足情况（实时各窗口末 E 相对日前 E 的偏差占比）/ 模式 A 跟随日前
    sc = rc["storage"]
    da_sto = da_res.tables["storage"].set_index(["unit", "period"])
    if sc.get("mode") == "fixed_da":
        # Disabled batteries leave a valid empty comparison, including in
        # pumped-only scenarios which use this same realtime storage policy.
        out["storage_vs_da_err_mw"] = float(max((abs((r.P_dis - r.P_ch) - (da_sto.loc[(r.unit, r.period), "P_dis"] - da_sto.loc[(r.unit, r.period), "P_ch"]))
                                                for r in st.itertuples()), default=0.0))
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
            prev = np.r_[float(rr.states[0].pumped[pid]["V"]), d.V_up.to_numpy()[:-1]]
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
    day_end_targets = {}
    for kind, group, table, column in (("E","storage","storage","E_mwh"), ("Vu","pumped","pumped","V_up")):
        for unit in cfg.units.get(group, []):
            rows = da_res.tables[table]
            target = float(rows[rows.unit == unit["id"]].sort_values("period")[column].iloc[-1])
            day_end_targets[kind,unit["id"]] = (target,target)
    for unit in cfg.units.get("hydro", []):
        season = unit["seasons"][cfg.base["season"]]
        day_end_targets["V",unit["id"]] = ((rr.states[0].hydro[unit["id"]],rr.states[0].hydro[unit["id"]]) if unit.get("regulation") == "run_of_river"
            else (season["v_end"],season["vmax"]))
    band_checks = state_band_policy_checks(cfg, rr.rt, day_end_targets=day_end_targets)
    out.update(band_checks)
    out.update(hydro_terminal_lookahead_checks(cfg, rr, rolling._da_plan_arrays(da_res),input_registry=getattr(da_res.case,"input_provider",None)))
    out.update(slack_policy_checks(da_res.slack, rr.rt,
                                   state_band_required=band_checks["state_band_checks_active"]))
    out["rules_satisfied_no_slack"] = (out["rules_satisfied_no_slack"]
                                        and out["state_band_targets_satisfied"])
    from .regulation import snapshot as regulation_snapshot
    agc_states = [regulation_snapshot(da_res.model)] + [r["snap"].get("regulation", {}) for r in rr.rt]
    agc_short = [float(v) for sn in agc_states if sn.get("enabled") for v in sn["shortage_mw"]]
    out["regulation_shortage_max_mw_all_windows"] = max(agc_short, default=0.)
    out["regulation_demand_met_all_windows"] = max(agc_short, default=0.) <= 1e-6
    out["all_declared_market_requirements_without_slack"] = out["rules_satisfied_no_slack"] and out["regulation_demand_met_all_windows"]
    # Exact inherited duration plus every actual transition; no rounded prehistory.
    from .state_bridge import thermal_duration_audit, terminal_obligations
    duration_errors = thermal_duration_audit(cfg, rr.states[0], rr.rt)
    out['min_updown_violations'] = len(duration_errors)
    out['min_updown_violation_details'] = duration_errors
    out['terminal_pending_obligations'] = terminal_obligations(cfg, rr.states[-1]) if len(rr.states) == len(rr.rt)+1 else None
    out['terminal_obligations_scope'] = 'remaining_obligations_pending_future_execution_not_verified'
    from .state_bridge import pumped_execution_audit
    pumped_audit = pumped_execution_audit(cfg, rr.states[0], rr.rt)
    out['pumped_cross_day_history_and_start_budget_valid'] = pumped_audit['valid']
    out['pumped_execution_violations'] = pumped_audit['violations']
    from .hydro_commitment import audit_execution as audit_hydro_commitment
    commitment_audit = audit_hydro_commitment(cfg, getattr(rr.states[0], 'hydro_commitment', {}), rr.rt)
    out['hydro_identified_commitment_active'] = commitment_audit['active']
    if commitment_audit['active']:
        commitment_audit['valid'] = (commitment_audit['valid'] and len(rr.states) == len(rr.rt)+1
            and commitment_audit['terminal_state'] == rr.states[-1].hydro_commitment)
    out['hydro_identified_commitment_execution_valid'] = commitment_audit['valid']

    # 实时功率平衡
    sysd = rt_tab["system"]
    # TIE is now included in rt_units; add the independently saved fixed plan once.
    units = rt_tab["units"]
    gen = units[units.type != "tie"].groupby("period").P_mw.sum()
    tie = sum(np.asarray(s)[: len(sysd)] for _, (b, s) in da_res.case.tie.items())
    bal = gen.to_numpy() + tie + sysd.shed_mw.to_numpy() - sysd.surplus_mw.to_numpy() - sysd.load_mw.to_numpy()
    out["rt_balance_err_mw"] = float(np.abs(bal).max())
    inj = rt_tab["prices"].groupby("period").inj_mw.sum()
    out["rt_injection_sum_err_mw"] = float(np.abs(inj).max())
    out.update(execution_safety_checks(da_res, rt_tab))
    out.update(hydro_window_checks(da_res, rr))
    from .hydro_daily_energy import audit as audit_daily_energy
    out.update(audit_daily_energy(cfg, rolling.snapshot(da_res.model), rr, bids=getattr(da_res.case, "bids", None),input_registry=getattr(da_res.case,"input_provider",None)))
    from .storage_anchors import hard_anchor_checks
    out.update(hard_anchor_checks(cfg, rolling.snapshot(da_res.model), rr.rt))
    tol = out["safety_tolerance_mw"]
    # Deliberately separate checked physical constraints from anchors/forecast-window
    # reserve shortages. Neither a monetary reconciliation nor balance proves safety.
    residuals = ("rt_balance_err_mw", "rt_injection_sum_err_mw", "soc_continuity_err_mwh",
                 "soc_equation_err_mwh", "pumped_continuity_err", "pumped_equation_err")
    violations = ("storage_simultaneous", "pumped_simultaneous", "min_updown_violations",
                  "locked_commitment_mismatch_rt", "rt_vs_plan_commitment_mismatch")
    out["physical_feasible_checked_constraints"] = bool(
        out["network_feasible"] and thermal_reference_complete and pumped_audit["valid"] and commitment_audit["valid"] and all(out.get(k, 0.0) <= tol for k in residuals)
        and all(out.get(k, 0) == 0 for k in violations)
        and float(sysd.shed_mw.max()) <= tol and float(sysd.surplus_mw.max()) <= tol)
    out["physical_feasibility_scope"] = "DA:network_limits_only;RT:network_limits,balance,SOC_and_pumped_continuity,exclusivity,commitment,min_updown_inherited_initial_and_all_executed_transitions,no_shed_or_surplus;not_all_model_constraints"
    return out


def run_full(cfg, case, log=print, periods: int | None = None, *, progress_callback=None, should_cancel=None, retain_id_models: bool = True, phase_callback=None) -> dict:
    if type(retain_id_models) is not bool:
        raise ValueError("retain_id_models must be boolean")
    from .input_validation import validate_requested_periods
    validate_requested_periods(periods)
    if not bool(getattr(cfg, 'base', {}).get('solver', {}).get('storage_binary', True)):
        raise ValueError('storage_binary=false仅用于日前/独立LP研究对照，不支持滚动实际执行')
    def phase(name):
        if phase_callback is not None:
            phase_callback(name)
    t0 = time.perf_counter()
    phase("DA")
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
    phase("rolling")
    rr = rolling.run_rolling(da, periods=periods, log=log, progress_callback=emit, should_cancel=should_cancel,
                             **({} if retain_id_models else {"retain_id_models": False}))
    from .hydro_daily_energy import audit as audit_daily_energy
    phase("daily_energy_audit")
    daily_check = audit_daily_energy(cfg, rolling.snapshot(da.model), rr, bids=getattr(da.case, "bids", None),input_registry=getattr(da.case,"input_provider",None))
    if daily_check["hydro_daily_minimum_active"] and (not daily_check["hydro_daily_minimum_evidence_valid"]
            or daily_check.get("hydro_daily_minimum_final_satisfied") is False):
        error = RuntimeError("水电硬日电量下限验收失败；保留已执行前缀，不生成成功结算："
                             + "; ".join(daily_check["hydro_daily_minimum_errors"]))
        error.daily_energy_evidence = daily_check
        raise error
    if should_cancel and should_cancel():
        from .live_runner import RunCancelled
        raise RunCancelled("Cancelled before settlement")
    t1 = time.perf_counter()
    phase("result_tables")
    rt_tab = rolling.rt_tables(da, rr)
    id_tab = rolling.intraday_tables(da, rr)
    phase("settlement")
    st = settlement.settle(da, rr)
    t_set = time.perf_counter() - t1
    from .live_runner import completion_metadata
    phase("execution_checks")
    checked = checks(da, rr, rt_tab)
    phase("full_complete")
    return dict(**completion_metadata(cfg.T,len(rr.rt),dt_h=cfg.dt),da=da, rr=rr, rt=rt_tab, id=id_tab, settle=st, checks=checked,
                time_s=dict(da=t_da, intraday=rr.time_s["intraday"], realtime=rr.time_s["realtime"], settlement=t_set,
                            total=time.perf_counter() - t0))


def intraday_run_summary(cfg, rr):
    failures = getattr(rr, "id_failures", [])
    return dict(enabled=bool(cfg.mode["intraday"].get("enabled", False)),
        interval_h=cfg.mode["intraday"]["interval_h"], rounds=len(rr.id_rounds),
        lock_types=cfg.mode["intraday"]["lock_types"], failed_rounds=failures,
        failure_count=len(failures), attempted_rounds=len(rr.id_rounds)+len(failures))


def write_intraday_failures(rr, directory):
    pd.DataFrame(getattr(rr, "id_failures", []), columns=["tau","phase","solver_status","reason","fallback"]).to_csv(
        directory / "id_failed_rounds.csv", index=False)


def _weighted_or_none(values, weights):
    values, weights = np.asarray(values), np.asarray(weights)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    return float(np.average(values[valid], weights=weights[valid])) if valid.any() else None


def _finite_stat(values, reducer):
    values = np.asarray(values)
    finite = values[np.isfinite(values)]
    return float(reducer(finite)) if finite.size else None


def _hourly_means(values, dt):
    """Preserve arithmetic-time mean, using overlap with actual complete hours."""
    values = np.asarray(values)
    start = np.arange(len(values)) * dt
    return [_weighted_or_none(values, np.maximum(0., np.minimum(start + dt, h + 1.) - np.maximum(start, h)))
            for h in range(int(np.floor(len(values) * dt + 1e-9)))]


def summarize_full(res) -> dict:
    da, st, rt = res["da"], res["settle"], res["rt"]
    cfg = da.case.cfg
    T = len(res["rr"].rt)
    pda, prt = st["prices"]["da"], st["prices"]["rt"]
    net = da.case.net
    load_da = np.array([[da.case.load_bus.get(b, np.zeros(cfg.T))[t] for t in range(T)] for b in net.buses])
    load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
    da_weights, rt_weights = load_da.sum(0), load_rt.sum(0)
    wda = np.divide((pda * load_da).sum(0), da_weights, out=np.full(T, np.nan), where=da_weights > 0)
    wrt = np.divide((prt * load_rt).sum(0), rt_weights, out=np.full(T, np.nan), where=rt_weights > 0)
    tail_weights = np.maximum(0., np.arange(T) * cfg.dt + cfg.dt - np.maximum(np.arange(T) * cfg.dt, 21.))
    sp = prt - pda
    bt = st["by_type"].set_index("type")
    fe = bt.reindex(["load", "wind", "pv"]).fillna(0)
    from .live_runner import completion_metadata
    scope = completion_metadata(cfg.T, T,dt_h=cfg.dt)
    s = dict(
        hydro_daily_minimum={key.removeprefix("hydro_daily_minimum_"): value
            for key, value in res["checks"].items() if key.startswith("hydro_daily_minimum_")},
        hydro_terminal_lookahead={key.removeprefix("hydro_terminal_lookahead_"):value
            for key,value in res["checks"].items() if key.startswith("hydro_terminal_lookahead_")},
        **scope,
        rule_set=cfg.rule_set, rule_name=cfg.mode["name"], date=cfg.base["date"], season=cfg.base["season"], periods=T,
        intraday=intraday_run_summary(cfg, res["rr"]),
        realtime=dict(interval_min=cfg.mode["realtime"]["interval_min"], lookahead_h=cfg.mode["realtime"]["lookahead_h"],
                      storage_mode=cfg.mode["realtime"]["storage"]["mode"], anchor=cfg.mode["realtime"]["storage"]["anchor"],
                      x=cfg.mode["realtime"]["storage"]["x"], hydro=cfg.mode["realtime"].get("hydro", "value"),
                      value_method=cfg.mode["realtime"].get("value_method"),
                      state_band_checks_available=res["checks"].get("state_band_checks_available"),
                      state_band_targets_satisfied=res["checks"].get("state_band_targets_satisfied"),
                      **{key: res["checks"].get(key) for key in ("terminal_policy_mode", "terminal_policy_compliant",
                          "executed_terminal_targets_zero_deviation", "executed_terminal_deviation_records",
                          "executed_terminal_penalty_cost", "emergency_state_band_slack_max", "emergency_band_fallback_count")},
                      state_band_slack_max=res["checks"].get("state_band_slack_max"),
                      **{key: value for key, value in res["checks"].items()
                         if key != "emergency_state_band_slack_max" and ("_state_band_slack_" in key
                         or "_day_end_window_deviation_" in key or "_executed_day_end_deviation_" in key)},
                      fallback_count=int(sum(1 for r in res["rr"].rt if r.get("fallback"))),
                      fallback_periods=[int(r["t"]) for r in res["rr"].rt if r.get("fallback")],
                      state_deviation_count=int(sum(len(r.get("state_deviations") or []) for r in res["rr"].rt)),
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
                      storage_anchor_policy=rolling.policy_metadata(cfg),
                      tracking_policy=(cfg.mode["realtime"].get("tracking_policy")
                                       if cfg.mode["realtime"].get("hydro") == "window_end_da"
                                       else dict(mode="not_applicable", reason=f"hydro={cfg.mode['realtime'].get('hydro')}")),
                      repriced_periods=int(rt["system"].repriced.sum()),
                      shed_mwh=float(rt["system"].shed_mw.sum() * cfg.dt), surplus_mwh=float(rt["system"].surplus_mw.sum() * cfg.dt),
                      curtail_mwh=float(rt["system"].curtail_mw.sum() * cfg.dt)),
        da=dict(objective=da.sced_info["objective"], scuc_objective=da.scuc_info["objective"], gap=da.scuc_info["gap"],
                termination=da.scuc_info.get("termination"), best_bound=da.scuc_info.get("best_bound"),
                requested_mip_gap=cfg.base["solver"]["mip_rel_gap"],
                requested_gap_met=bool(np.isfinite(da.scuc_info["gap"]) and da.scuc_info["gap"] <= cfg.base["solver"]["mip_rel_gap"]+1e-9),
                curtail_mwh=float(da.tables["units"].curtail_mw.sum() * cfg.dt)),
        spread=dict(da_lw_avg=_weighted_or_none(wda, da_weights),
                    rt_lw_avg=_weighted_or_none(wrt, rt_weights),
                    lw_spread_mean=_finite_stat(wrt - wda, np.mean), lw_spread_abs_mean=_finite_stat(np.abs(wrt - wda), np.mean),
                    lw_spread_max=_finite_stat(wrt - wda, np.max), lw_spread_min=_finite_stat(wrt - wda, np.min),
                    lw_spread_defined_periods=int(np.isfinite(wrt - wda).sum()),
                    lw_undefined_policy="zero_load_periods_excluded;all_undefined_is_null",
                    nodal_spread_abs_max=float(np.abs(sp).max()), nodal_spread_rms=float(np.sqrt((sp ** 2).mean())),
                    rt_lmp_min=float(prt.min()), rt_lmp_max=float(prt.max()),
                    da_lmp_min=float(pda.min()), da_lmp_max=float(pda.max()),
                    da_lw_tail=_weighted_or_none(wda, da_weights * tail_weights),
                    rt_lw_tail=_weighted_or_none(wrt, rt_weights * tail_weights),
                    rt_lw_hourly=_hourly_means(wrt, cfg.dt),
                    da_lw_hourly=_hourly_means(wda, cfg.dt)),
        # A zero-net-energy type has no defined average price. Preserve that
        # unknown as JSON null, without masking invalid quantities or cash.
        settlement_by_type={k: {c: (None if c == "avg_price" and not np.isfinite(v) else float(v))
                                for c, v in r.items()} for k, r in bt.iterrows()},
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
                 rt_mode=stc.get("mode", "value") + ("/" + stc.get("anchor", "hourly_eq") if stc.get("mode", "value") == "sced_anchor" else ""))
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


def write_pricing_evidence(da, rounds, directory: Path, id_rounds=()) -> dict:
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
        rt_windows=[dict(window_start=r["t"],diagnostics=r.get("pricing_info")) for r in rounds],
        id_windows=[dict(window_start=r["tau"],diagnostics=r.get("pricing_info")) for r in id_rounds])
    (directory/"auxiliary_pricing_evidence.json").write_text(json.dumps(numeric(evidence),ensure_ascii=False,allow_nan=False),encoding="utf-8")
    scopes=([("DA",0,evidence["da"])]
        +[("ID",r["window_start"],r["diagnostics"]) for r in evidence["id_windows"]]
        +[("RT",r["window_start"],r["diagnostics"]) for r in evidence["rt_windows"]])
    for key in ("bands","slack_differences","reserve_differences","state_differences","removed_rt_tracking","anchor_costs"):
        rows=[dict(stage=stage,window_start=start,**row) for stage,start,info in scopes
              if info is not None for row in info.get(key,[])]
        pd.DataFrame(rows).to_csv(directory/f"auxiliary_pricing_{key}.csv",index=False)
    return evidence


def write_full_outputs(res, d: Path, make_plots=True) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    da, st, rt, idt = res["da"], res["settle"], res["rt"], res["id"]
    from .live_runner import completion_metadata, atomic_json
    from .result_retention import validate_compact_rounds, archive_state_evidence
    validate_compact_rounds(res['rr'])
    if getattr(res['rr'], 'id_models_retained', True) is False:
        atomic_json(d/'id_state_value_evidence.json',archive_state_evidence(res['rr']))
    scope = completion_metadata(da.case.cfg.T, len(res["rr"].rt),dt_h=da.case.cfg.dt)
    atomic_json(d/"output_scope.json",scope)
    state_evidence=dict(scope="actual_initial_state_before_each_executed_RT_window",
        history_scope="absolute executed indices [0,t); future buffers intentionally omitted",
        state_fields="all SysState dataclass fields; pumped_used is cumulative executed starts",
        windows=[actual_state_evidence(state) for state in res["rr"].states[:len(res["rr"].rt)]],
        final_executed_state=actual_state_evidence(res["rr"].states[-1]) if len(res["rr"].states)==len(res["rr"].rt)+1 else None)
    (d/"rolling_actual_states.json").write_text(json.dumps(state_evidence,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    write_pricing_evidence(da, res["rr"].rt, d, res["rr"].id_rounds)
    solver_rows = [dict(stage="DA", window_start=0, **da.scuc_info)] + [
        dict(stage="RT", window_start=r["t"], **r["mip"]) for r in res["rr"].rt] + [
        dict(stage="ID", window_start=r["tau"], **r["mip"]) for r in res["rr"].id_rounds]
    for row in solver_rows:
        target = (da.case.cfg.base["solver"]["mip_rel_gap"] if row["stage"] == "DA" else
                  da.case.cfg.mode["intraday"].get("mip_rel_gap", .005) if row["stage"] == "ID" else
                  da.case.cfg.mode["realtime"].get("mip_rel_gap", .01))
        row["requested_mip_gap"] = target
        gap = row.get("gap")
        row["requested_gap_met"] = bool(gap is not None and np.isfinite(gap) and gap <= target+1e-9)
    pd.DataFrame(solver_rows).drop(columns=["warm_start", "regulation_allocation"], errors="ignore").to_csv(d/"solver_windows.csv",index=False)

    stage_data = {"da": getattr(da, "stage_diagnostics", {}), "rt_windows": [{"t": r["t"], "stages": r.get("stage_diagnostics", {})} for r in res["rr"].rt]}
    stage_data.update(schema_version=2,
        id_rounds=[dict(tau=r["tau"], issue_t=r.get("issue_t"), stages=r.get("stage_diagnostics", {}),
                        solution_quality=r.get("solution_quality", {})) for r in res["rr"].id_rounds],
        id_failures=getattr(res["rr"], "id_failures", []),
        id_failed_stages=getattr(res["rr"], "id_failed_stages", []))
    (d / "stage_diagnostics.json").write_text(json.dumps(stage_data, ensure_ascii=False, default=float), encoding="utf-8")
    cfg = da.case.cfg
    tracking_rows = [dict(window_start=r["t"], **row) for r in res["rr"].rt for row in r["snap"].get("tracking", [])]
    (d / "rt_tracking_window_evidence.json").write_text(json.dumps(tracking_rows, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(tracking_rows).to_csv(d / "rt_tracking_windows.csv", index=False)
    from .value_outputs import write_value_outputs   # 第二轮H项：价值曲线、窗口末偏差、偏差带/回退、价值定价时段、辅助vs对偶价差
    write_value_outputs(da, res["rr"], d)
    from .results import reserve_snapshot
    reserve_evidence={"da":reserve_snapshot(da.model),"rt_windows":[r["snap"]["reserve"] for r in res["rr"].rt],
                      "id_windows":[r["snap"]["reserve"] for r in res["rr"].id_rounds]}
    (d / "reserve_window_evidence.json").write_text(json.dumps(reserve_evidence,ensure_ascii=False),encoding="utf-8")
    # Full-precision window evidence for independent hydro state/lag/target audits.
    def hydro_evidence(sn):
        data = {key: {h: np.asarray(a).tolist() for h,a in sn.get(key, {}).items()}
                for key in ("Ph", "Vh", "Qout", "u", "E", "Vu")}
        data["delta"] = [{"station": h, "zone": int(z), "values": np.asarray(a).tolist()}
                         for (h,z),a in sn.get("delta", {}).items()]
        data["constraints"] = sn.get("hydro_constraints", {})
        data["hydro_daily_energy"] = sn.get("hydro_daily_energy", {})
        if sn.get('input_provenance') is not None:
            data['input_provenance']=sn['input_provenance']
        return data
    from .hydro_daily_energy import id_state_values
    evidence = dict(dt_h=cfg.dt, season=cfg.base["season"], hydro_mode=cfg.mode["realtime"].get("hydro"),
                    tracking_policy=cfg.mode["realtime"].get("tracking_policy"),
                    specifications=cfg.units["hydro"], da=hydro_evidence(rolling.snapshot(da.model)), rt_windows=[],
                    id_rounds=[dict(tau=r["tau"],snapshot=hydro_evidence(r["snap"]),state_values=id_state_values(r)) for r in res["rr"].id_rounds],
                    id_failures=getattr(res['rr'],'id_failures',[]))
    for r,state in zip(res["rr"].rt,res["rr"].states):
        evidence["rt_windows"].append(dict(t=r["t"], snapshot=hydro_evidence(r["snap"]),
            handoff_config=r.get("handoff_config", {}), thermal_reference_u=r.get("thermal_reference_u", {}),
            value_window=r["snap"].get("value_window", []),
            reference_metadata=r.get("reference_metadata"),
            hydro_reference_delta=([{ "station": h, "zone": int(z), "values": np.asarray(a).tolist() }
                                    for (h,z),a in r["hydro_reference_delta"].items()]
                                   if "hydro_reference_delta" in r else None),
            state=dict(volume=state.hydro, power=getattr(state,"hydro_p",{}), online=getattr(state,"hydro_online",{}),
                       energy_used_mwh=state.hydro_used,
                       outflow_history={h:np.asarray(a[:state.t]).tolist() for h,a in state.hydro_hist.items()})))
    if da.case.input_provider is not None:
        evidence['causal_input_registry']=da.case.input_provider.archive(da.case.day_start_utc)
        evidence['project_timezone']=da.case.input_provider.forecaster.timezone
        from .forecast_diagnostics import make_diagnostic_context
        evidence['diagnostic_context']=make_diagnostic_context(da.case.cfg.base,
            da.case.input_provider.source_manifest(),da.case.input_provider.day_summary(da.case.day_start_utc),
            da.case.selected_inputs.content_hash)
    (d / "hydro_window_evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,
        default=lambda x:x.tolist() if hasattr(x,"tolist") else float(x)),encoding="utf-8")
    f = dict(index=False, float_format="%.4f")
    from .output_catalog import write_da_tables, write_catalog, write_profile_evidence
    write_da_tables(da, d)
    write_profile_evidence(da.case, d)
    if da.case.selected_inputs is not None:
        from .forecast_diagnostics import write_post_execution_diagnostics
        write_post_execution_diagnostics(evidence,d)
    write_intraday_failures(res["rr"], d)
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
    if getattr(res['rr'], 'id_models_retained', True) is False:
        from .result_retention import ARCHIVE_CONTRACT, conversion_parameters
        s['id_result_retention'] = dict(ARCHIVE_CONTRACT)
        s['id_state_value_parameters'] = conversion_parameters(cfg)
    from .hydro_spill_tiebreak import RULE as WATER_SELECTION_RULE, audit_selection_evidence
    selection=dict(version=1,rule=WATER_SELECTION_RULE,da=da.sced_info["hydro_water_selection"],
        rt_windows=[dict(window_start=r["t"],certificate=r["sced"]["hydro_water_selection"]) for r in res["rr"].rt],
        id_windows=[dict(window_start=r["tau"],certificate=r["sced"]["hydro_water_selection"]) for r in res["rr"].id_rounds])
    (d/"hydro_water_selection_evidence.json").write_text(json.dumps(selection,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    s["hydro_water_selection"]=audit_selection_evidence(selection,evidence,
        json.loads((d/"stage_diagnostics.json").read_text()),da.case.cfg.units["hydro"],len(res["rr"].rt),len(res["rr"].id_rounds))
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
    # 第二轮J项：结果页顶部规则行读取本次实际限价（川2026总体方案 五（二）2：申报/出清下限−50），不借用当前配置
    s["price_limits"] = {k: float(v) for k, v in da.case.cfg.mode["price_limits"].items() if isinstance(v, (int, float))}
    from .reserve_checks import audit as audit_reserve_evidence
    s["reserve"] = audit_reserve_evidence(reserve_evidence)
    s["reserve"].update(evidence_version=reserve_evidence["da"]["evidence_version"],
                        hydro_reserve_outflow_formulation=reserve_evidence["da"]["hydro_reserve_outflow_formulation"],
                        hydro_reserve_outflow_enabled=reserve_evidence["da"]["hydro_reserve_outflow_enabled"],
                        hydro_reserve_water_formulation=reserve_evidence["da"]["hydro_reserve_water_formulation"],
                        hydro_reserve_water_enabled=reserve_evidence["da"]["hydro_reserve_water_enabled"],
                        storage_reserve_enabled=reserve_evidence["da"]["storage_reserve_enabled"],
                        down_enabled=reserve_evidence["da"]["down_enabled"],T_dur_h=reserve_evidence["da"]["T_dur_h"],
                        scope="DA_ID_and_all_RT_planning_windows;RT_shortage_max_uses_executed_first_period_only;no_activation_network_or_response_certification")
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
    plot_files = []
    if make_plots:
        plot_files += plots.dispatch_plots(da.tables, d, dt_h=cfg.dt)
        plot_files += plots.dispatch_plots(rt, d, stage="rt", dt_h=cfg.dt)
        if da.case.selected_inputs is None:
            plots.forecast_plot(da.case.profiles, d / "forecast_vs_actual.png", dt_h=cfg.dt)
        if any(c.endswith("_actual") and c[:-7]+"_da" in da.case.profiles for c in da.case.profiles):
            plot_files.append("forecast_vs_actual.png")
        net = da.case.net
        T = s["periods"]
        load_da = np.array([[da.case.load_bus.get(b, np.zeros(cfg.T))[t] for t in range(T)] for b in net.buses])
        load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
        plots.da_vs_rt_lmp(st["prices"]["da"], st["prices"]["rt"], load_da, load_rt, list(net.buses), d / "lmp_da_vs_rt.png", dt_h=cfg.dt)
        plots.rolling_plans(idt["plans"], idt["storage"], rt["units"], rt["storage"], da.tables["units"], da.tables["storage"],
                            d / "rolling_plans.png", dt_h=cfg.dt, x=cfg.mode["realtime"]["storage"]["x"]
                            if cfg.mode["realtime"]["storage"]["mode"] == "sced_anchor" and cfg.mode["realtime"]["storage"]["anchor"] == "window_end_pct" else None)
        plots.settlement_bars(st["by_type"], st["by_entity"], d / "settlement_bars.png")
        plot_files += ["lmp_da_vs_rt.png", "rolling_plans.png", "settlement_bars.png"]
    write_catalog(d, scope, full=True, plot_files=plot_files)
    return s
