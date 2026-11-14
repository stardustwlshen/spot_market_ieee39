"""October-2026 SCED storage physics; no solver and no inferred operating plan.

All dispatch powers are MW magnitudes. ``signed_charge``/``signed_pump`` expose
negative withdrawals, so each energy bid is counted once with its market sign.
This module is deliberately independent of the historical storage core.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math
from numbers import Real
from zoneinfo import ZoneInfo

import pyomo.environ as pyo

SCHEMA = "gd_october_storage_v1"
SL_SIDES = ("upper_plus", "upper_minus", "lower_plus", "lower_minus")
UNITS = {"power": "MW", "energy": "MWh", "volume": "m3", "time": "h", "energy_price": "yuan/MWh"}


class OctoberStorageInputError(ValueError):
    """Missing, inconsistent, nonfinite, or unsupported explicit input."""


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise OctoberStorageInputError(f"{label}: exact fields required: {sorted(keys)}")


def _num(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise OctoberStorageInputError(f"{label}: finite numeric value required")
    value = float(value)
    if minimum is not None and value < minimum:
        raise OctoberStorageInputError(f"{label}: value below {minimum}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise OctoberStorageInputError(f"{label}: explicit nonempty source/identifier required")
    return value


def _time(value, label):
    _text(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OctoberStorageInputError(f"{label}: ISO timestamp required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OctoberStorageInputError(f"{label}: timezone required")
    return parsed


def _record(raw, fields, label):
    _keys(raw, set(fields) | {"sources"}, label)
    _keys(raw["sources"], fields, label + ".sources")
    for field in fields:
        _text(raw["sources"][field], label + ".sources." + field)
    return deepcopy(raw)


def _series(raw, periods, label, minimum=None):
    _keys(raw, periods, label)
    return {t: _num(raw[t], f"{label}[{t}]", minimum) for t in periods}


def _bool_series(raw, periods, label):
    _keys(raw, periods, label)
    if any(type(raw[t]) is not bool for t in periods):
        raise OctoberStorageInputError(f"{label}: explicit booleans required")
    return dict(raw)


def _mode_power(mode, power, modes, label):
    if mode not in modes:
        raise OctoberStorageInputError(f"{label}: unsupported fixed mode")
    power = _num(power, label + ".net_power_mw")
    if (mode in {"off", "idle"} and power != 0) or (mode == "pump" and power >= 0) or (mode == "charge" and power > 0) or (mode in {"generate", "discharge"} and power < 0):
        raise OctoberStorageInputError(f"{label}: actual mode and power conflict")
    return power


def _plan(raw, initial, modes, periods, window_start, cutoff, label):
    _keys(initial, {"as_of", "available_at", "mode", "net_power_mw", "mode_elapsed_h", "kind", "source"}, label + ".actual_initial")
    _text(initial["source"], label + ".actual_initial.source")
    if _time(initial["as_of"], label + ".as_of") != window_start:
        raise OctoberStorageInputError(f"{label}: initial state must be at this window boundary")
    if initial["kind"] not in {"telemetry", "confirmed_simulated_execution", "causal_plan_projection"}:
        raise OctoberStorageInputError(f"{label}: explicit observed/executed/projected state kind required")
    available = _time(initial["available_at"], label + ".available_at")
    if available > cutoff or (initial["kind"] != "causal_plan_projection" and available < window_start):
        raise OctoberStorageInputError(f"{label}: initial state is not causally available at the information cutoff")
    _mode_power(initial["mode"], initial["net_power_mw"], modes, label)
    _num(initial["mode_elapsed_h"], label + ".mode_elapsed_h", 0)
    _keys(raw, {"preceding_mode", "modes", "published_at", "source", "transition_authorizations", "minimum_mode_h"}, label + ".effective_plan")
    _text(raw["source"], label + ".effective_plan.source")
    if _time(raw["published_at"], label + ".published_at") > cutoff:
        raise OctoberStorageInputError(f"{label}: effective plan was unavailable at information cutoff")
    if raw["preceding_mode"] != initial["mode"]:
        raise OctoberStorageInputError(f"{label}: effective plan preceding mode conflicts with actual/projected boundary")
    _keys(raw["modes"], periods, label + ".modes")
    _keys(raw["minimum_mode_h"], modes, label + ".minimum_mode_h")
    minimum_age = {mode: _num(value, label + ".minimum_mode_h", 0) for mode, value in raw["minimum_mode_h"].items()}
    previous = initial["mode"]
    age = float(initial["mode_elapsed_h"])
    entered = window_start - timedelta(hours=age)
    changes = {}
    entered_before = {}
    for t in periods:
        now = raw["modes"][t]
        if now not in modes:
            raise OctoberStorageInputError(f"{label}[{t}]: fixed effective mode required; free integers unsupported")
        if previous != now:
            if age < minimum_age[previous] - 1e-12:
                raise OctoberStorageInputError(f"{label}[{t}]: preceding mode minimum residence not completed")
            if modes == {"generate", "pump", "off"} and previous != "off" and now != "off":
                raise OctoberStorageInputError(f"{label}[{t}]: direct pump/generate reversal requires unsupported transition physics")
            changes[t] = (previous, now)
            entered_before[t] = entered
            entered = window_start + timedelta(hours=periods.index(t) / 12)
            age = 0.
        previous = now
        age += 1 / 12
    _keys(raw["transition_authorizations"], changes, label + ".transition_authorizations")
    for t, (before, after) in changes.items():
        a = raw["transition_authorizations"][t]
        _keys(a, {"from_mode", "to_mode", "preparation_required_h", "preparation_started_at", "preparation_completed_at", "issued_at", "source"}, label + ".transition")
        _text(a["source"], label + ".transition.source")
        if _time(a["issued_at"], label + ".transition.issued_at") > cutoff:
            raise OctoberStorageInputError(f"{label}: transition evidence unavailable at cutoff")
        required = _num(a["preparation_required_h"], label + ".preparation_required_h", 0)
        began = _time(a["preparation_started_at"], label + ".preparation_started_at")
        completed = _time(a["preparation_completed_at"], label + ".preparation_completed_at")
        boundary = window_start + timedelta(hours=periods.index(t) / 12)
        if (a["from_mode"], a["to_mode"]) != (before, after) or began < entered_before[t] or completed > boundary or (completed - began).total_seconds() / 3600 < required:
            raise OctoberStorageInputError(f"{label}[{t}]: transition not covered by completed effective preparation")
    return dict(raw["modes"])


_BATTERY = {"bus", "energy_capacity_mwh", "roundtrip_efficiency", "initial_energy_mwh", "final_energy_mwh",
    "energy_min_mwh", "energy_max_mwh", "charge_min_mw", "charge_max_mw", "discharge_min_mw", "discharge_max_mw",
    "charge_bid_yuan_per_mwh", "discharge_bid_yuan_per_mwh", "cycle_limit", "cycle_throughput_used_mwh",
    "cumulative_throughput_used_mwh", "cycle_budget_id", "actual_initial", "effective_plan", "reserve_eligibility", "boundary_interpretation", "final_energy_at", "soc_conversion", "agc_award", "ramp_up_mw_per_h", "ramp_down_mw_per_h", "cycle_budget_scope", "cycle_budgets"}
_UNIT = {"bus", "pump_power_mw", "generation_min_mw", "generation_max_mw", "pump_m3_per_mwh", "generation_m3_per_mwh",
    "pump_bid_yuan_per_mwh", "generation_bid_yuan_per_mwh", "actual_initial", "effective_plan", "agc_up_mw", "agc_down_mw",
    "agc_boundary_interpretation", "operating_reserve_eligible", "primary_eligible", "primary_fraction",
    "standby_available_mw", "standby_preparation_h", "contingency_response_h", "ramp_up_mw_per_h", "ramp_down_mw_per_h", "standby_timing_model"}
_PLANT = {"units", "upper_initial_m3", "lower_initial_m3", "upper_min_m3", "upper_max_m3", "lower_min_m3", "lower_max_m3",
    "da_upper_target_m3", "da_lower_target_m3", "upper_band_m3", "lower_band_m3", "penalty_profile",
    "contingency_eligible", "contingency_limit_mw", "state_source", "slack_time_index"}


def _validate_batteries(data, periods, buses, start, cutoff):
    if not isinstance(data, dict):
        raise OctoberStorageInputError("batteries must be an explicit mapping")
    result = {}
    for uid, raw in data.items():
        _text(uid, "battery ID")
        d = _record(raw, _BATTERY, uid)
        if d["bus"] not in buses:
            raise OctoberStorageInputError(f"{uid}: unknown bus")
        for k in ("energy_capacity_mwh", "roundtrip_efficiency", "initial_energy_mwh", "final_energy_mwh", "cycle_limit", "cycle_throughput_used_mwh", "cumulative_throughput_used_mwh"):
            d[k] = _num(d[k], uid + "." + k, 0)
        if d["energy_capacity_mwh"] <= 0 or not 0 < d["roundtrip_efficiency"] <= 1:
            raise OctoberStorageInputError(f"{uid}: positive rating and efficiency in (0,1] required")
        d["budget_day_by_period"] = {t: (start.astimezone(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=5 * i)).date().isoformat() for i, t in enumerate(periods)}
        days = tuple(dict.fromkeys(d["budget_day_by_period"].values()))
        if d["cycle_budget_scope"] != "operating_day_Asia_Shanghai" or d["cycle_budget_id"] != days[0]:
            raise OctoberStorageInputError(f"{uid}: explicit Shanghai operating-day cycle budget required")
        _keys(d["cycle_budgets"], days, uid + ".cycle_budgets")
        for day, budget in d["cycle_budgets"].items():
            _keys(budget, {"limit_cycles", "used_throughput_mwh", "source"}, uid + ".cycle_budgets." + day)
            _text(budget["source"], uid + ".cycle_budgets.source")
            limit = _num(budget["limit_cycles"], uid + ".cycle_budgets.limit_cycles", 0)
            used = _num(budget["used_throughput_mwh"], uid + ".cycle_budgets.used_throughput_mwh", 0)
            if used > 2 * d["energy_capacity_mwh"] * limit or (day != days[0] and used != 0):
                raise OctoberStorageInputError(f"{uid}: invalid already-executed throughput for operating day")
        first_budget = d["cycle_budgets"][days[0]]
        if first_budget["limit_cycles"] != d["cycle_limit"] or first_budget["used_throughput_mwh"] != d["cycle_throughput_used_mwh"]:
            raise OctoberStorageInputError(f"{uid}: scalar carry conflicts with dated cycle budget")
        d["eta"] = math.sqrt(d["roundtrip_efficiency"])
        if _time(d["final_energy_at"], uid + ".final_energy_at") != start + timedelta(hours=len(periods) / 12):
            raise OctoberStorageInputError(f"{uid}: final energy target must name this explicit window-end time")
        conversion = d["soc_conversion"]
        _keys(conversion, {"input_unit", "initial", "final", "source"}, uid + ".soc_conversion")
        _text(conversion["source"], uid + ".soc_conversion.source")
        if conversion["input_unit"] not in {"percent", "MWh"}:
            raise OctoberStorageInputError(f"{uid}: SOC percent/MWh conversion must be explicit")
        for field in ("initial", "final"):
            original = _num(conversion[field], uid + ".soc_conversion." + field, 0)
            if conversion["input_unit"] == "percent" and original > 100:
                raise OctoberStorageInputError(f"{uid}: SOC percentage exceeds 100")
            expected = original * d["energy_capacity_mwh"] / 100 if conversion["input_unit"] == "percent" else original
            if not math.isclose(expected, d[field + "_energy_mwh"], rel_tol=0, abs_tol=1e-12):
                raise OctoberStorageInputError(f"{uid}: declared SOC conversion conflicts with MWh inventory")
        for k in ("energy_min_mwh", "energy_max_mwh", "charge_min_mw", "charge_max_mw", "discharge_min_mw", "discharge_max_mw", "charge_bid_yuan_per_mwh", "discharge_bid_yuan_per_mwh", "ramp_up_mw_per_h", "ramp_down_mw_per_h"):
            d[k] = _series(d[k], periods, uid + "." + k, None if "bid" in k else 0)
        for t in periods:
            for kind, unit in (("energy", "mwh"), ("charge", "mw"), ("discharge", "mw")):
                if d[f"{kind}_min_{unit}"][t] > d[f"{kind}_max_{unit}"][t]:
                    raise OctoberStorageInputError(f"{uid}[{t}]: reversed hard bounds")
            if d["energy_max_mwh"][t] > d["energy_capacity_mwh"]:
                raise OctoberStorageInputError(f"{uid}: energy maximum exceeds rating")
        if not d["energy_min_mwh"][periods[0]] <= d["initial_energy_mwh"] <= d["energy_max_mwh"][periods[0]]:
            raise OctoberStorageInputError(f"{uid}: initial inventory outside hard bounds")
        if not d["energy_min_mwh"][periods[-1]] <= d["final_energy_mwh"] <= d["energy_max_mwh"][periods[-1]]:
            raise OctoberStorageInputError(f"{uid}: declared final inventory outside hard bounds")
        if d["cycle_throughput_used_mwh"] > 2 * d["energy_capacity_mwh"] * d["cycle_limit"]:
            raise OctoberStorageInputError(f"{uid}: carried cycle budget already exhausted")
        if d["cumulative_throughput_used_mwh"] < d["cycle_throughput_used_mwh"]:
            raise OctoberStorageInputError(f"{uid}: cumulative throughput below current-budget throughput")
        for k in ("cycle_budget_id", "boundary_interpretation"):
            _text(d[k], uid + "." + k)
        if d["reserve_eligibility"] != "excluded_unverified":
            raise OctoberStorageInputError(f"{uid}: battery reserve participation not verified by this component")
        award = d["agc_award"]
        _keys(award, {"status", "up_mw", "down_mw", "source"}, uid + ".agc_award")
        _text(award["source"], uid + ".agc_award.source")
        up = _series(award["up_mw"], periods, uid + ".agc_award.up_mw", 0)
        down = _series(award["down_mw"], periods, uid + ".agc_award.down_mw", 0)
        if award["status"] != "explicitly_no_award" or any(up.values()) or any(down.values()):
            raise OctoberStorageInputError(f"{uid}: nonzero/unknown battery AGC requires unsupported explicit mode-boundary mapping")
        d["modes"] = _plan(d["effective_plan"], d["actual_initial"], {"charge", "discharge", "idle"}, periods, start, cutoff, uid)
        result[uid] = d
    return result


def _penalty(raw, periods, label):
    if not isinstance(raw, dict) or raw.get("status") not in {"unresolved", "specified"}:
        raise OctoberStorageInputError(f"{label}: explicit penalty status required; no default MP")
    if raw["status"] == "unresolved":
        _keys(raw, {"status", "source", "reason"}, label)
        _text(raw["source"], label + ".source"); _text(raw["reason"], label + ".reason")
        return deepcopy(raw)
    _keys(raw, {"status", "unit", "time_index", "values", "source", "interpretation_source"}, label)
    _text(raw["source"], label + ".source"); _text(raw["interpretation_source"], label + ".interpretation_source")
    if raw["unit"] not in {"yuan/m3/step", "yuan/(m3*h)"} or raw["time_index"] != "per_5min_end":
        raise OctoberStorageInputError(f"{label}: explicit volume penalty units and time index required")
    _keys(raw["values"], SL_SIDES, label + ".values")
    result = deepcopy(raw)
    result["values"] = {side: _series(raw["values"][side], periods, label + "." + side, 0) for side in SL_SIDES}
    return result


def _validate_pumped(data, periods, buses, start, cutoff):
    if not isinstance(data, dict):
        raise OctoberStorageInputError("pumped must be an explicit mapping")
    plants, units, owners = {}, {}, {}
    for pid, raw in data.items():
        _text(pid, "pumped plant ID")
        p = _record(raw, _PLANT, pid)
        if not isinstance(p["units"], dict) or not p["units"]:
            raise OctoberStorageInputError(f"{pid}: explicit nonempty physical unit mapping required")
        for k in ("upper_initial_m3", "lower_initial_m3"):
            p[k] = _num(p[k], pid + "." + k, 0)
        for k in ("upper_min_m3", "upper_max_m3", "lower_min_m3", "lower_max_m3", "da_upper_target_m3", "da_lower_target_m3", "upper_band_m3", "lower_band_m3", "contingency_limit_mw"):
            p[k] = _series(p[k], periods, pid + "." + k, 0)
        for side in ("upper", "lower"):
            if any(p[side + "_min_m3"][t] > p[side + "_max_m3"][t] for t in periods):
                raise OctoberStorageInputError(f"{pid}: reversed reservoir hard bounds")
            if not p[side + "_min_m3"][periods[0]] <= p[side + "_initial_m3"] <= p[side + "_max_m3"][periods[0]]:
                raise OctoberStorageInputError(f"{pid}: initial reservoir outside hard bounds")
        _keys(p["state_source"], {"as_of", "available_at", "kind", "source"}, pid + ".state_source")
        if _time(p["state_source"]["as_of"], pid + ".state_source.as_of") != start or p["state_source"]["kind"] not in {"telemetry", "confirmed_simulated_execution", "causal_plan_projection"}:
            raise OctoberStorageInputError(f"{pid}: explicit reservoir state at window boundary required")
        available = _time(p["state_source"]["available_at"], pid + ".state_source.available_at")
        if available > cutoff or (p["state_source"]["kind"] != "causal_plan_projection" and available < start):
            raise OctoberStorageInputError(f"{pid}: reservoir state unavailable at information cutoff")
        _text(p["state_source"]["source"], pid + ".state_source.source")
        if p["slack_time_index"] != "per_5min_end":
            raise OctoberStorageInputError(f"{pid}: SL temporal mapping must explicitly be per_5min_end")
        p["penalty_profile"] = _penalty(p["penalty_profile"], periods, pid + ".penalty_profile")
        p["contingency_eligible"] = _bool_series(p["contingency_eligible"], periods, pid + ".contingency_eligible")
        for uid, uraw in p["units"].items():
            _text(uid, "pumped unit ID")
            if uid in units:
                raise OctoberStorageInputError("pumped unit IDs must be globally unique")
            d = _record(uraw, _UNIT, uid)
            if d["bus"] not in buses:
                raise OctoberStorageInputError(f"{uid}: unknown bus")
            for k in ("pump_power_mw", "generation_min_mw", "generation_max_mw", "pump_m3_per_mwh", "generation_m3_per_mwh", "pump_bid_yuan_per_mwh", "generation_bid_yuan_per_mwh", "agc_up_mw", "agc_down_mw", "primary_fraction", "standby_available_mw", "standby_preparation_h", "contingency_response_h", "ramp_up_mw_per_h", "ramp_down_mw_per_h"):
                d[k] = _series(d[k], periods, uid + "." + k, None if "bid" in k else 0)
            for k in ("operating_reserve_eligible", "primary_eligible"):
                d[k] = _bool_series(d[k], periods, uid + "." + k)
            _text(d["agc_boundary_interpretation"], uid + ".agc_boundary_interpretation")
            if d["standby_timing_model"] not in {"preparation_after_minimum_off", "preparation_during_minimum_off"}:
                raise OctoberStorageInputError(f"{uid}: explicit standby preparation/minimum-off timing model required")
            d["modes"] = _plan(d["effective_plan"], d["actual_initial"], {"generate", "pump", "off"}, periods, start, cutoff, uid)
            preceding_mode = d["actual_initial"]["mode"]
            state_age = d["actual_initial"]["mode_elapsed_h"]
            for t in periods:
                mode = d["modes"][t]
                if mode != preceding_mode:
                    state_age = 0.
                remaining_off = max(0., d["effective_plan"]["minimum_mode_h"]["off"] - state_age) if mode == "off" else 0.
                state_age += 1 / 12
                preceding_mode = mode
                if d["generation_min_mw"][t] > d["generation_max_mw"][t] or d["primary_fraction"][t] > 1:
                    raise OctoberStorageInputError(f"{uid}[{t}]: inconsistent limits/primary fraction")
                if min(d["pump_m3_per_mwh"][t], d["generation_m3_per_mwh"][t]) <= 0:
                    raise OctoberStorageInputError(f"{uid}[{t}]: positive m3/MWh conversions required")
                if mode == "pump" and d["pump_power_mw"][t] <= 0:
                    raise OctoberStorageInputError(f"{uid}[{t}]: pumping requires positive fixed withdrawal")
                if mode != "generate" and (d["agc_up_mw"][t] or d["agc_down_mw"][t]):
                    raise OctoberStorageInputError(f"{uid}[{t}]: non-generating AGC unsupported, cannot silently discard it")
                if d["generation_min_mw"][t] + d["agc_down_mw"][t] > d["generation_max_mw"][t] - d["agc_up_mw"][t]:
                    raise OctoberStorageInputError(f"{uid}[{t}]: AGC consumes the operating interval")
                standby = d["standby_available_mw"][t]
                if standby > d["generation_max_mw"][t] or (mode != "off" and standby):
                    raise OctoberStorageInputError(f"{uid}[{t}]: standby capacity conflicts with mode or rating")
                ready_delay = (remaining_off + d["standby_preparation_h"][t] if d["standby_timing_model"] == "preparation_after_minimum_off" else max(remaining_off, d["standby_preparation_h"][t]))
                if standby and ready_delay > d["contingency_response_h"][t]:
                    raise OctoberStorageInputError(f"{uid}[{t}]: standby preparation exceeds explicit response capability")
                if standby > d["ramp_up_mw_per_h"][t] * max(0., d["contingency_response_h"][t] - ready_delay) + 1e-10:
                    raise OctoberStorageInputError(f"{uid}[{t}]: standby capacity exceeds post-preparation ramp capability")
            units[uid] = d; owners[uid] = pid
        for t in periods:
            current = [units[u] for u in p["units"]]
            live_modes = {d["modes"][t] for d in current} - {"off"}
            if live_modes == {"pump", "generate"}:
                raise OctoberStorageInputError(f"{pid}[{t}]: mixed plant pumping/generation requires an explicit plant-mode formulation")
            available = sum(d["generation_max_mw"][t] - d["agc_up_mw"][t] if d["modes"][t] == "generate" else d["standby_available_mw"][t] for d in current)
            lower = sum(d["generation_min_mw"][t] + d["agc_down_mw"][t] for d in current if d["modes"][t] == "generate")
            limit = p["contingency_limit_mw"][t]
            if limit > available or ("pump" in live_modes and limit != 0):
                raise OctoberStorageInputError(f"{pid}[{t}]: contingency limit exceeds externally available capacity")
            if p["contingency_eligible"][t] and not live_modes == {"pump"} and limit < lower:
                raise OctoberStorageInputError(f"{pid}[{t}]: contingency limit below mandatory scheduled generation")
        plants[pid] = p
    return plants, units, owners


def attach_gd_october_storage(model, spec):
    """Attach physics only and return ports. Caller connects injection/cost ONCE.

    No objective is created or modified here. ``economic_objective_complete`` is
    false when an MP profile is unresolved: zero attached penalty is a missing
    term marker, not a decision that the official penalty equals zero.
    """
    if hasattr(model, "october_storage"):
        raise OctoberStorageInputError("October storage already attached; duplicate cost/injection forbidden")
    _keys(spec, {"schema_version", "run_id", "window_start", "information_cutoff", "batteries", "pumped", "units", "source"}, "storage spec")
    if spec["schema_version"] != SCHEMA or spec["units"] != UNITS:
        raise OctoberStorageInputError("explicit October schema and canonical units required")
    _text(spec["run_id"], "run_id"); _text(spec["source"], "spec.source")
    start = _time(spec["window_start"], "window_start")
    cutoff = _time(spec["information_cutoff"], "information_cutoff")
    if cutoff != start - timedelta(minutes=15):
        raise OctoberStorageInputError("information cutoff must explicitly be window start minus 15 minutes")
    local = start.astimezone(ZoneInfo("Asia/Shanghai"))
    if local.minute % 15 or local.second or local.microsecond:
        raise OctoberStorageInputError("window start must be an exact 15-minute dispatch boundary")
    periods, buses = tuple(model.T), tuple(model.B)
    horizon = getattr(model, "_gd_october_horizon", getattr(model, "_gd_horizon", None))
    if horizon is None or tuple(horizon.ids) != periods or len(periods) != 24:
        raise OctoberStorageInputError("explicit 24-period October SCED horizon required")
    horizon.validate()
    duration = horizon.duration_h
    if any(abs(duration[t] - 1 / 12) > 1e-12 for t in periods):
        raise OctoberStorageInputError("all dispatch intervals must be five minutes")
    batteries = _validate_batteries(spec["batteries"], periods, buses, start, cutoff)
    plants, units, owners = _validate_pumped(spec["pumped"], periods, buses, start, cutoff)
    if set(batteries) & set(units):
        raise OctoberStorageInputError("battery and pumped unit IDs overlap")
    previous = {t: periods[i - 1] if i else None for i, t in enumerate(periods)}
    model.october_storage = s = pyo.Block()
    s.BAT = pyo.Set(initialize=tuple(batteries), ordered=True)
    s.PS = pyo.Set(initialize=tuple(plants), ordered=True)
    s.U = pyo.Set(initialize=tuple(units), ordered=True)
    s.SL = pyo.Set(initialize=SL_SIDES, ordered=True)
    s.charge = pyo.Var(s.BAT, model.T, domain=pyo.NonNegativeReals, initialize=0)
    s.discharge = pyo.Var(s.BAT, model.T, domain=pyo.NonNegativeReals, initialize=0)
    s.energy = pyo.Var(s.BAT, model.T, domain=pyo.NonNegativeReals, bounds=lambda _, b, t: (batteries[b]["energy_min_mwh"][t], batteries[b]["energy_max_mwh"][t]), initialize=lambda _, b, t: batteries[b]["initial_energy_mwh"])
    for b, d in batteries.items():
        for t in periods:
            s.charge[b, t].setlb(d["charge_min_mw"][t] if d["modes"][t] == "charge" else 0)
            s.charge[b, t].setub(d["charge_max_mw"][t] if d["modes"][t] == "charge" else 0)
            s.discharge[b, t].setlb(d["discharge_min_mw"][t] if d["modes"][t] == "discharge" else 0)
            s.discharge[b, t].setub(d["discharge_max_mw"][t] if d["modes"][t] == "discharge" else 0)
    s.signed_charge = pyo.Expression(s.BAT, model.T, rule=lambda _, b, t: -s.charge[b, t])
    s.battery_net = pyo.Expression(s.BAT, model.T, rule=lambda _, b, t: s.discharge[b, t] - s.charge[b, t])
    s.c_battery_ramp_up = pyo.Constraint(s.BAT, model.T, rule=lambda _, b, t: s.battery_net[b, t] - (batteries[b]["actual_initial"]["net_power_mw"] if previous[t] is None else s.battery_net[b, previous[t]]) <= batteries[b]["ramp_up_mw_per_h"][t] * duration[t])
    s.c_battery_ramp_down = pyo.Constraint(s.BAT, model.T, rule=lambda _, b, t: (batteries[b]["actual_initial"]["net_power_mw"] if previous[t] is None else s.battery_net[b, previous[t]]) - s.battery_net[b, t] <= batteries[b]["ramp_down_mw_per_h"][t] * duration[t])
    s.c_battery_balance = pyo.Constraint(s.BAT, model.T, rule=lambda _, b, t: s.energy[b, t] == (batteries[b]["initial_energy_mwh"] if previous[t] is None else s.energy[b, previous[t]]) + duration[t] * (batteries[b]["eta"] * s.charge[b, t] - s.discharge[b, t] / batteries[b]["eta"]))
    s.c_battery_final = pyo.Constraint(s.BAT, rule=lambda _, b: s.energy[b, periods[-1]] == batteries[b]["final_energy_mwh"])
    s.period_throughput = pyo.Expression(s.BAT, model.T, rule=lambda _, b, t: duration[t] * (s.discharge[b, t] / batteries[b]["eta"] + s.charge[b, t] * batteries[b]["eta"]))
    s.cycle_throughput = pyo.Expression(s.BAT, model.T, rule=lambda _, b, t: batteries[b]["cycle_budgets"][batteries[b]["budget_day_by_period"][t]]["used_throughput_mwh"] + sum(s.period_throughput[b, q] for q in periods[:periods.index(t) + 1] if batteries[b]["budget_day_by_period"][q] == batteries[b]["budget_day_by_period"][t]))
    s.cumulative_throughput = pyo.Expression(s.BAT, model.T, rule=lambda _, b, t: batteries[b]["cumulative_throughput_used_mwh"] + sum(s.period_throughput[b, q] for q in periods[:periods.index(t) + 1]))
    s.c_battery_cycle = pyo.Constraint(s.BAT, model.T, rule=lambda _, b, t: s.cycle_throughput[b, t] <= 2 * batteries[b]["energy_capacity_mwh"] * batteries[b]["cycle_budgets"][batteries[b]["budget_day_by_period"][t]]["limit_cycles"])
    s.generation = pyo.Var(s.U, model.T, domain=pyo.NonNegativeReals, initialize=0)
    s.pump = pyo.Var(s.U, model.T, domain=pyo.NonNegativeReals, initialize=0)
    s.primary = pyo.Var(s.U, model.T, domain=pyo.NonNegativeReals, initialize=0)
    for u, d in units.items():
        for t in periods:
            generating = d["modes"][t] == "generate"
            s.generation[u, t].setlb(d["generation_min_mw"][t] + d["agc_down_mw"][t] if generating else 0)
            s.generation[u, t].setub(d["generation_max_mw"][t] - d["agc_up_mw"][t] if generating else 0)
            s.pump[u, t].fix(d["pump_power_mw"][t] if d["modes"][t] == "pump" else 0)
            if not generating or not d["primary_eligible"][t]:
                s.primary[u, t].fix(0)
    s.signed_pump = pyo.Expression(s.U, model.T, rule=lambda _, u, t: -s.pump[u, t])
    s.pumped_net = pyo.Expression(s.U, model.T, rule=lambda _, u, t: s.generation[u, t] - s.pump[u, t])
    s.c_pumped_ramp_up = pyo.Constraint(s.U, model.T, rule=lambda _, u, t: s.pumped_net[u, t] - (units[u]["actual_initial"]["net_power_mw"] if previous[t] is None else s.pumped_net[u, previous[t]]) <= units[u]["ramp_up_mw_per_h"][t] * duration[t])
    s.c_pumped_ramp_down = pyo.Constraint(s.U, model.T, rule=lambda _, u, t: (units[u]["actual_initial"]["net_power_mw"] if previous[t] is None else s.pumped_net[u, previous[t]]) - s.pumped_net[u, t] <= units[u]["ramp_down_mw_per_h"][t] * duration[t])
    s.online_max = pyo.Expression(s.U, model.T, rule=lambda _, u, t: units[u]["generation_max_mw"][t] if units[u]["modes"][t] == "generate" else 0.)
    s.c_primary_headroom = pyo.Constraint(s.U, model.T, rule=lambda _, u, t: s.primary[u, t] <= s.online_max[u, t] - units[u]["agc_up_mw"][t] - s.generation[u, t])
    s.c_primary_fraction = pyo.Constraint(s.U, model.T, rule=lambda _, u, t: s.primary[u, t] <= s.online_max[u, t] * units[u]["primary_fraction"][t])
    s.upper = pyo.Var(s.PS, model.T, domain=pyo.NonNegativeReals, bounds=lambda _, p, t: (plants[p]["upper_min_m3"][t], plants[p]["upper_max_m3"][t]), initialize=lambda _, p, t: plants[p]["upper_initial_m3"])
    s.lower = pyo.Var(s.PS, model.T, domain=pyo.NonNegativeReals, bounds=lambda _, p, t: (plants[p]["lower_min_m3"][t], plants[p]["lower_max_m3"][t]), initialize=lambda _, p, t: plants[p]["lower_initial_m3"])
    s.transfer_to_upper = pyo.Expression(s.PS, model.T, rule=lambda _, p, t: duration[t] * sum(units[u]["pump_m3_per_mwh"][t] * s.pump[u, t] - units[u]["generation_m3_per_mwh"][t] * s.generation[u, t] for u in plants[p]["units"]))
    s.c_upper_balance = pyo.Constraint(s.PS, model.T, rule=lambda _, p, t: s.upper[p, t] == (plants[p]["upper_initial_m3"] if previous[t] is None else s.upper[p, previous[t]]) + s.transfer_to_upper[p, t])
    s.c_lower_balance = pyo.Constraint(s.PS, model.T, rule=lambda _, p, t: s.lower[p, t] == (plants[p]["lower_initial_m3"] if previous[t] is None else s.lower[p, previous[t]]) - s.transfer_to_upper[p, t])
    s.slack = pyo.Var(s.PS, s.SL, model.T, domain=pyo.NonNegativeReals, initialize=0)
    s.c_upper_band = pyo.Constraint(s.PS, model.T, rule=lambda _, p, t: (plants[p]["da_upper_target_m3"][t] - plants[p]["upper_band_m3"][t], s.upper[p, t] - s.slack[p, "upper_plus", t] + s.slack[p, "upper_minus", t], plants[p]["da_upper_target_m3"][t] + plants[p]["upper_band_m3"][t]))
    s.c_lower_band = pyo.Constraint(s.PS, model.T, rule=lambda _, p, t: (plants[p]["da_lower_target_m3"][t] - plants[p]["lower_band_m3"][t], s.lower[p, t] - s.slack[p, "lower_plus", t] + s.slack[p, "lower_minus", t], plants[p]["da_lower_target_m3"][t] + plants[p]["lower_band_m3"][t]))
    s.plant_generation = pyo.Expression(s.PS, model.T, rule=lambda _, p, t: sum(s.generation[u, t] for u in plants[p]["units"]))
    s.plant_pump = pyo.Expression(s.PS, model.T, rule=lambda _, p, t: sum(s.pump[u, t] for u in plants[p]["units"]))
    def contingency(_, p, t):
        d = plants[p]
        if not d["contingency_eligible"][t]:
            return 0.
        if any(units[u]["modes"][t] == "pump" for u in d["units"]):
            return s.plant_pump[p, t]
        return d["contingency_limit_mw"][t] - s.plant_generation[p, t]
    s.contingency = pyo.Expression(s.PS, model.T, rule=contingency)
    eligible = [(p, t) for p in plants for t in periods if plants[p]["contingency_eligible"][t] and not any(units[u]["modes"][t] == "pump" for u in plants[p]["units"])]
    s.c_contingency_nonnegative = pyo.Constraint(eligible, rule=lambda _, p, t: s.plant_generation[p, t] <= plants[p]["contingency_limit_mw"][t])
    s.energy_bid_cost = pyo.Expression(expr=sum(duration[t] * (d["discharge_bid_yuan_per_mwh"][t] * s.discharge[b, t] - d["charge_bid_yuan_per_mwh"][t] * s.charge[b, t]) for b, d in batteries.items() for t in periods) + sum(duration[t] * (d["generation_bid_yuan_per_mwh"][t] * s.generation[u, t] - d["pump_bid_yuan_per_mwh"][t] * s.pump[u, t]) for u, d in units.items() for t in periods))
    s.inventory_penalty_cost = pyo.Expression(expr=sum(d["penalty_profile"]["values"][side][t] * s.slack[p, side, t] * (duration[t] if d["penalty_profile"]["unit"] == "yuan/(m3*h)" else 1.) for p, d in plants.items() if d["penalty_profile"]["status"] == "specified" for side in SL_SIDES for t in periods))
    s.cost = pyo.Expression(expr=s.energy_bid_cost + s.inventory_penalty_cost)
    s.injection = pyo.Expression(model.B, model.T, rule=lambda _, bus, t: sum(s.battery_net[b, t] for b, d in batteries.items() if d["bus"] == bus) + sum(s.pumped_net[u, t] for u, d in units.items() if d["bus"] == bus))
    def capacity(t, upper):
        return sum((d["generation_max_mw"][t] - d["agc_up_mw"][t] if upper else d["generation_min_mw"][t] + d["agc_down_mw"][t]) if d["modes"][t] == "generate" and d["operating_reserve_eligible"][t] else 0. for u, d in units.items())
    s.positive_capacity = pyo.Expression(model.T, rule=lambda _, t: capacity(t, True))
    s.negative_minimum = pyo.Expression(model.T, rule=lambda _, t: capacity(t, False))
    missing = [f"{p}.penalty_profile" for p, d in plants.items() if d["penalty_profile"]["status"] == "unresolved"]
    audit = {"schema_version": SCHEMA, "run_id": spec["run_id"], "window_start": spec["window_start"], "information_cutoff": spec["information_cutoff"], "units": deepcopy(UNITS),
        "official_algorithm_certified": False, "physical_equations_constructed": True, "economic_objective_complete": not missing,
        "missing_economic_parameters": missing, "inventory_mp_printed_in_sced_objective": False,
        "mp_is_external_interpretation": True, "state_selection": "fixed_external_effective_plan_not_revalidated",
        "actual_initial_preserved": True, "hourly_da_soc_anchors": False, "value_function_cost": False,
        "contingency_zero_dispatch_uses_explicit_limited_capacity": True, "primary_capacity_uses_physical_online_max": True,
        "agc_boundary_is_explicit_external_interpretation": True, "slack_time_index": "per_5min_end",
        "reserve_capability_basis": "operator_declared_limits_with_formula_and_preparation_checks",
        "reserve_sustained_energy_verified": False, "reserve_network_deliverability_verified": False,
        "reserve_contingency_security_verified": False, "response_time_is_sustain_duration": False,
        "reserve_basis": "explicit_eligible_generation_capacity_only", "feasibility_certified": False}
    model._gd_october_storage_raw_inputs = deepcopy(spec)
    model._gd_october_storage_inputs = {"batteries": batteries, "plants": plants, "units": units, "owners": owners}
    model._gd_october_storage_audit = audit
    return {"injection_mw": {(b, t): s.injection[b, t] for b in buses for t in periods}, "cost_expr": s.cost,
        "positive_capacity_mw": {t: s.positive_capacity[t] for t in periods}, "negative_minimum_mw": {t: s.negative_minimum[t] for t in periods},
        "primary_mw": {t: sum(s.primary[u, t] for u in units) for t in periods}, "contingency_mw": {t: sum(s.contingency[p, t] for p in plants) for t in periods},
        "reserve_basis": audit["reserve_basis"], "audit": deepcopy(audit)}


def reservoir_slack_snapshot(model, *, selected_dispatch_source, window_id, selected_dispatch_id, selected_dispatch_sha256):
    """Read all four SL values without claiming this is a solved dispatch.

    The caller must independently verify selected-dispatch feasibility. A later
    5-to-15-minute price mapping must keep this source and cannot average SLs
    without an explicitly validated mapping of the translated bands.
    """
    _text(selected_dispatch_source, "selected_dispatch_source")
    _text(window_id, "window_id"); _text(selected_dispatch_id, "selected_dispatch_id")
    if not isinstance(selected_dispatch_sha256, str) or len(selected_dispatch_sha256) != 64 or any(c not in "0123456789abcdef" for c in selected_dispatch_sha256):
        raise OctoberStorageInputError("selected_dispatch_sha256: exact lowercase SHA256 required")
    audit = model._gd_october_storage_audit
    attached_identity = getattr(model, "_gd_october_identity", None)
    declared_identity = {"run_id": audit["run_id"], "window_id": window_id, "selected_dispatch_id": selected_dispatch_id, "selected_dispatch_sha256": selected_dispatch_sha256}
    if attached_identity is not None and attached_identity != declared_identity:
        raise OctoberStorageInputError("SL snapshot identity differs from byte-bound model identity")
    s = model.october_storage
    return {"origin": "october_sced_four_translated_slacks", "run_id": audit["run_id"], "window_start": audit["window_start"],
        "window_id": window_id, "selected_dispatch_id": selected_dispatch_id, "selected_dispatch_sha256": selected_dispatch_sha256, "periods": tuple(model.T), "unit": "m3", "time_index": "per_5min_end", "source": selected_dispatch_source,
        "values": {p: {side: {t: _num(pyo.value(s.slack[p, side, t], exception=False), f"{p}.{side}[{t}]", 0) for t in model.T} for side in SL_SIDES} for p in s.PS}}


def validate_fixed_reservoir_slacks(snapshot, *, run_id, window_start, window_id, selected_dispatch_id, selected_dispatch_sha256, periods, plant_ids):
    """Validate exact same-run 5-minute source for downstream fixed parameters."""
    _keys(snapshot, {"origin", "run_id", "window_start", "window_id", "selected_dispatch_id", "selected_dispatch_sha256", "periods", "unit", "time_index", "source", "values"}, "four SL snapshot")
    if snapshot["origin"] != "october_sced_four_translated_slacks" or snapshot["run_id"] != run_id or _time(snapshot["window_start"], "snapshot.window_start") != _time(window_start, "expected.window_start") or tuple(snapshot["periods"]) != tuple(periods) or snapshot["unit"] != "m3" or snapshot["time_index"] != "per_5min_end":
        raise OctoberStorageInputError("SL source/version/run/window/time mapping mismatch")
    _text(snapshot["source"], "snapshot.source")
    _text(window_id, "window_id"); _text(selected_dispatch_id, "selected_dispatch_id")
    if not isinstance(selected_dispatch_sha256, str) or len(selected_dispatch_sha256) != 64 or any(c not in "0123456789abcdef" for c in selected_dispatch_sha256):
        raise OctoberStorageInputError("selected dispatch SHA256 required")
    if snapshot["window_id"] != window_id or snapshot["selected_dispatch_id"] != selected_dispatch_id or snapshot["selected_dispatch_sha256"] != selected_dispatch_sha256:
        raise OctoberStorageInputError("fixed SL selected dispatch identity/hash mismatch")
    _keys(snapshot["values"], plant_ids, "snapshot.plants")
    result = {}
    for p in plant_ids:
        _keys(snapshot["values"][p], SL_SIDES, "snapshot.sides")
        for side in SL_SIDES:
            for t, value in _series(snapshot["values"][p][side], periods, f"{p}.{side}", 0).items():
                result[p, side, t] = value
    return result


def storage_boundary_state(model, *, through_period, source):
    """Read an explicit prefix boundary, never silently use the two-hour end.

    This is candidate-state extraction, not execution confirmation or telemetry.
    The caller is responsible for accepting only confirmed executed receipts.
    """
    _text(source, "boundary.source")
    periods = tuple(model.T)
    if through_period not in periods:
        raise OctoberStorageInputError("explicit known boundary period required")
    n = periods.index(through_period) + 1
    elapsed = n / 12
    t = through_period
    s = model.october_storage
    inputs = model._gd_october_storage_inputs
    start = _time(model._gd_october_storage_audit["window_start"], "window_start")
    def mode_state(d, net):
        mode, age = d["actual_initial"]["mode"], d["actual_initial"]["mode_elapsed_h"]
        for q in periods[:n]:
            now = d["modes"][q]
            age = age + 1 / 12 if now == mode else 1 / 12
            mode = now
        return {"mode": mode, "mode_elapsed_h": age, "net_power_mw": _num(pyo.value(net, exception=False), "boundary.net_power")}
    return {"as_of": (start + timedelta(hours=elapsed)).isoformat(), "through_period": t, "source": source,
        "kind": "unconfirmed_candidate_not_telemetry", "batteries": {b: {"energy_mwh": _num(pyo.value(s.energy[b, t], exception=False), "boundary.energy", 0), "day_throughput_mwh": _num(pyo.value(s.cycle_throughput[b, t], exception=False), "boundary.throughput", 0), "cumulative_throughput_mwh": _num(pyo.value(s.cumulative_throughput[b, t], exception=False), "boundary.cumulative", 0), "cycle_budget_id": d["budget_day_by_period"][t], "day_throughput_mwh_by_day": {day: budget["used_throughput_mwh"] + sum(_num(pyo.value(s.period_throughput[b, q], exception=False), "boundary.day_throughput", 0) for q in periods[:n] if d["budget_day_by_period"][q] == day) for day, budget in d["cycle_budgets"].items()}, **mode_state(d, s.battery_net[b, t])} for b, d in inputs["batteries"].items()},
        "pumped_plants": {p: {"upper_volume_m3": _num(pyo.value(s.upper[p, t], exception=False), "boundary.upper", 0), "lower_volume_m3": _num(pyo.value(s.lower[p, t], exception=False), "boundary.lower", 0)} for p in inputs["plants"]},
        "pumped_units": {u: mode_state(d, s.pumped_net[u, t]) for u, d in inputs["units"].items()}}


def advance_storage_state(state, dispatch, parameters, *, duration_h, cycle_day, source):
    """Pure five-minute integration for confirmed execution OR causal plan projection.

    It does not turn an issued plan into telemetry, verify startup readiness, or
    certify inventory feasibility. Caller checks effective plan and hard bounds.
    Daily throughput history is retained across a caller-labelled day boundary.
    ``source`` identifies the caller's observation/issued plan, never invented.
    """
    _text(source, "advance.source")
    dt = _num(duration_h, "duration_h", 0)
    if abs(dt - 1 / 12) > 1e-12:
        raise OctoberStorageInputError("advance requires an explicitly five-minute interval")
    _text(cycle_day, "cycle_day")
    try:
        if datetime.strptime(cycle_day, "%Y-%m-%d").date().isoformat() != cycle_day:
            raise ValueError
    except ValueError as exc:
        raise OctoberStorageInputError("cycle_day must explicitly identify YYYY-MM-DD") from exc
    _keys(state, {"batteries", "pumped_plants", "pumped_units"}, "advance.state")
    if any(not isinstance(state[group], dict) for group in state):
        raise OctoberStorageInputError("all state resource groups must be mappings")
    _keys(dispatch, {"batteries", "pumped_units"}, "advance.dispatch")
    _keys(parameters, {"batteries", "pumped_units"}, "advance.parameters")
    for group in ("batteries", "pumped_units"):
        _keys(dispatch[group], state[group], "dispatch." + group)
        _keys(parameters[group], state[group], "parameters." + group)
    result = deepcopy(state)
    for b, raw in state["batteries"].items():
        _keys(raw, {"energy_mwh", "day_throughput_mwh", "cumulative_throughput_mwh", "cycle_day", "day_throughput_mwh_by_day"}, "state." + b)
        _keys(dispatch["batteries"][b], {"charge_mw", "discharge_mw"}, "dispatch." + b)
        _keys(parameters["batteries"][b], {"roundtrip_efficiency"}, "parameters." + b)
        efficiency = _num(parameters["batteries"][b]["roundtrip_efficiency"], b + ".efficiency", 0)
        if not 0 < efficiency <= 1:
            raise OctoberStorageInputError("roundtrip efficiency must be in (0,1]")
        charge = _num(dispatch["batteries"][b]["charge_mw"], b + ".charge", 0)
        discharge = _num(dispatch["batteries"][b]["discharge_mw"], b + ".discharge", 0)
        if charge and discharge:
            raise OctoberStorageInputError("simultaneous battery charging/discharging forbidden")
        history = raw["day_throughput_mwh_by_day"]
        if not isinstance(history, dict) or raw["cycle_day"] not in history:
            raise OctoberStorageInputError("explicit current cycle-day history required")
        for day in history:
            try:
                if datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise OctoberStorageInputError("throughput history requires ISO operating-day keys") from exc
        normalized = {day: _num(value, b + ".history", 0) for day, value in history.items()}
        if any(day > raw["cycle_day"] and value for day, value in normalized.items()):
            raise OctoberStorageInputError("future-day executed throughput is impossible")
        if _num(raw["day_throughput_mwh"], b + ".day_throughput", 0) != normalized[raw["cycle_day"]]:
            raise OctoberStorageInputError("current throughput conflicts with retained day history")
        cumulative = _num(raw["cumulative_throughput_mwh"], b + ".cumulative", 0)
        if cumulative < math.fsum(normalized.values()):
            raise OctoberStorageInputError("cumulative throughput below retained daily history")
        if cycle_day < raw["cycle_day"]:
            raise OctoberStorageInputError("cannot rewind throughput accounting day")
        eta = math.sqrt(efficiency)
        throughput = dt * (charge * eta + discharge / eta)
        energy = _num(raw["energy_mwh"], b + ".energy", 0) + dt * (charge * eta - discharge / eta)
        if energy < 0:
            raise OctoberStorageInputError("projected battery energy is negative")
        normalized[cycle_day] = normalized.get(cycle_day, 0.) + throughput
        result["batteries"][b] = {"energy_mwh": energy, "day_throughput_mwh": normalized[cycle_day],
            "cumulative_throughput_mwh": cumulative + throughput, "cycle_day": cycle_day,
            "day_throughput_mwh_by_day": normalized}
    for p, raw in state["pumped_plants"].items():
        _keys(raw, {"upper_m3", "lower_m3"}, "state." + p)
        result["pumped_plants"][p] = {k: _num(v, p + "." + k, 0) for k, v in raw.items()}
    for u, raw in state["pumped_units"].items():
        _keys(raw, {"mode", "elapsed_h", "net_power_mw"}, "state." + u)
        d, par = dispatch["pumped_units"][u], parameters["pumped_units"][u]
        _keys(d, {"generation_mw", "pump_mw", "mode"}, "dispatch." + u)
        _keys(par, {"plant_id", "pump_m3_per_mwh", "generation_m3_per_mwh"}, "parameters." + u)
        if par["plant_id"] not in result["pumped_plants"]:
            raise OctoberStorageInputError("pumped unit references unknown reservoir pair")
        _mode_power(raw["mode"], raw["net_power_mw"], {"generate", "pump", "off"}, u + ".initial")
        generation = _num(d["generation_mw"], u + ".generation", 0)
        pump = _num(d["pump_mw"], u + ".pump", 0)
        if generation and pump:
            raise OctoberStorageInputError("simultaneous pumping and generation forbidden")
        _mode_power(d["mode"], generation - pump, {"generate", "pump", "off"}, u + ".dispatch")
        kp = _num(par["pump_m3_per_mwh"], u + ".pump_conversion", 0)
        kg = _num(par["generation_m3_per_mwh"], u + ".generation_conversion", 0)
        if min(kp, kg) <= 0:
            raise OctoberStorageInputError("positive water conversion coefficients required")
        transfer = dt * (kp * pump - kg * generation)
        plant = result["pumped_plants"][par["plant_id"]]
        plant["upper_m3"] += transfer; plant["lower_m3"] -= transfer
        age = _num(raw["elapsed_h"], u + ".elapsed_h", 0)
        result["pumped_units"][u] = {"mode": d["mode"], "elapsed_h": age + dt if raw["mode"] == d["mode"] else dt, "net_power_mw": generation - pump}
    if any(v < 0 for p in result["pumped_plants"].values() for v in p.values()):
        raise OctoberStorageInputError("projected reservoir volume is negative")
    return result
