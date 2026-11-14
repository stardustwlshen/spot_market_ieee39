"""Explicit Guangdong-reference battery and fixed-speed pumped-storage physics.

This is an additive physical component, not a complete Guangdong market. All
powers are positive magnitudes; injection is generation minus pumping/charging.
See ../docs/10_储能抽蓄物理核心.md for clauses, omissions and assumptions.
No solver, value-function objective, interregional trade, or conventional hydro.
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from numbers import Real

import pyomo.environ as pyo

from .gd_horizon import EnergyHorizon
from .gd_energy_core import _state_boundary_audit, _state_in_bounds
from .gd_pumped_plan_boundary import (PumpedUnitIdentity, validate_pumped_plan_boundary)

STAGES = {"DA_SCUC", "DA_SCED", "RT_SCED"}
SCUC_AGGREGATION_POLICIES = {"single_unit", "unit_disaggregated_research"}
BATTERY_KEYS = {
    "bus", "energy_capacity_mwh", "roundtrip_efficiency", "initial_energy_mwh",
    "energy_min_mwh", "energy_max_mwh", "charge_min_mw", "charge_max_mw",
    "discharge_min_mw", "discharge_max_mw", "cycle_limit", "cycle_throughput_used_mwh",
    "final_energy_bounds_mwh", "charge_bid_yuan_per_mwh", "discharge_bid_yuan_per_mwh",
    "da_anchor_mwh", "direction_policy", "da_direction", "initial_hour_direction",
    "anchor_policy", "sources",
}
PLANT_KEYS = {
    "units", "upper_initial_m3", "lower_initial_m3", "upper_min_m3", "upper_max_m3",
    "lower_min_m3", "lower_max_m3", "upper_final_bounds_m3", "lower_final_bounds_m3",
    "da_upper_m3", "da_lower_m3", "upper_band_m3", "lower_band_m3",
    "band_penalty_yuan_per_m3_step", "max_generating_units", "max_pumping_units",
    "min_online_units", "max_online_units", "max_generation_increase", "max_pumping_increase",
    "sources",
}
UNIT_KEYS = {
    "bus", "pump_power_mw", "generation_min_mw", "generation_max_mw",
    "pump_available", "generation_available", "pump_m3_per_mwh", "generation_m3_per_mwh",
    "initial_mode", "initial_mode_elapsed_h", "initial_generation_mw", "min_generate_h",
    "min_pump_h", "min_off_h", "remaining_start_stop_events",
    "pump_bid_yuan_per_mwh", "generation_bid_yuan_per_mwh", "sources",
}
BAND_SIDES = ("upper_low", "upper_high", "lower_low", "lower_high")


def _number(value, name, *, minimum=0., positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name}: finite numeric input required")
    value = float(value)
    if (minimum is not None and value < minimum) or (positive and value <= 0):
        raise ValueError(f"{name}: invalid numeric range")
    return value


def _integer(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name}: nonnegative integer within limits required")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty source or identifier required")
    return value


def _record(record, keys, name):
    if not isinstance(record, dict) or set(record) != keys:
        raise ValueError(f"{name}: exact keys required, missing={keys-set(record) if isinstance(record, dict) else keys}, "
                         f"extra={set(record)-keys if isinstance(record, dict) else set()}")
    sources = record["sources"]
    needed = keys - {"sources", "units"}
    if not isinstance(sources, dict) or not needed <= set(sources):
        raise ValueError(f"{name}: source for every parameter required")
    for key in needed:
        _text(sources[key], f"{name}.sources.{key}")
    return dict(record)


def _series(value, ids, name, *, minimum=0., integer=False, maximum=None):
    if not isinstance(value, dict) or set(value) != set(ids):
        raise ValueError(f"{name}: exact period coverage required")
    if integer:
        return {t: _integer(value[t], name, maximum) for t in ids}
    return {t: _number(value[t], name, minimum=minimum) for t in ids}


def _bounds(value, name, *, required):
    if value is None and not required:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name}: explicit (lower, upper) required")
    lo, hi = (_number(v, name) for v in value)
    if lo > hi:
        raise ValueError(f"{name}: lower exceeds upper")
    return lo, hi


def _ordered_subset(value, ids, name):
    if not isinstance(value, tuple) or any(type(t) is not int for t in value):
        raise ValueError(f"{name}: immutable tuple of period IDs required")
    if len(set(value)) != len(value) or value != tuple(t for t in ids if t in value):
        raise ValueError(f"{name}: ordered unique in-horizon IDs required")
    return value


def _clock(horizon, stage, clock_start_h, anchor_periods, day_end_period, cycle_periods):
    horizon.validate()
    if (stage == "RT_SCED" and horizon.mode not in {"real_time", "component"}) or (stage != "RT_SCED" and horizon.mode == "real_time"):
        raise ValueError("storage stage and horizon mode disagree")
    ids = horizon.ids
    clock_start_h = _number(clock_start_h, "clock_start_h")
    anchors = _ordered_subset(anchor_periods, ids, "anchor_periods")
    cycles = _ordered_subset(cycle_periods, ids, "cycle_periods")
    groups, ends, day_ends = {}, [], []
    for period in horizon.periods:
        t = period.index
        start, end = clock_start_h + horizon.start_h[t], clock_start_h + horizon.end_h[t]
        if period.role == "representative":
            groups[t] = f"representative:{t}"
            continue
        hour = math.floor(start + 1e-9)
        if end > hour + 1 + 1e-9:
            raise ValueError("delivery periods must not cross a clock-hour boundary")
        groups[t] = f"hour:{hour}"
        if abs(end - round(end)) < 1e-9:
            ends.append(t)
        if abs(end / 24 - round(end / 24)) < 1e-9:
            day_ends.append(t)
    expected = tuple(ends) if stage == "RT_SCED" else ()
    if anchors != expected:
        raise ValueError("anchor_periods must explicitly cover exactly the RT clock-hour ends")
    if len(day_ends) > 1 or day_end_period != (day_ends[0] if day_ends else None):
        raise ValueError("day_end_period must identify the actual delivery-day end, not a representative tail")
    if day_end_period is not None and type(day_end_period) is not int:
        raise ValueError("day_end_period must be an integer ID")
    return clock_start_h, groups, anchors, cycles


def _batteries(data, ids, anchors, stage, day_end, clock_start, core, numeric_state_audit):
    result = {}
    for uid, original in data.items():
        _text(uid, "battery ID")
        r = _record(original, BATTERY_KEYS, uid)
        _text(r["bus"], f"{uid}.bus")
        if any((r["bus"], t) not in core.other_injection for t in ids):
            raise ValueError(f"{uid}: bus absent from energy core")
        for key in ("energy_capacity_mwh", "roundtrip_efficiency"):
            r[key] = _number(r[key], f"{uid}.{key}", positive=True)
        if r["roundtrip_efficiency"] > 1:
            raise ValueError("roundtrip_efficiency cannot exceed one")
        r["eta"] = math.sqrt(r["roundtrip_efficiency"])
        r["cycle_limit"] = _number(r["cycle_limit"], f"{uid}.cycle_limit")
        for key in ("energy_min_mwh", "energy_max_mwh", "charge_min_mw", "charge_max_mw",
                    "discharge_min_mw", "discharge_max_mw", "charge_bid_yuan_per_mwh", "discharge_bid_yuan_per_mwh"):
            r[key] = _series(r[key], ids, f"{uid}.{key}", minimum=None if "bid" in key else 0.)
        for t in ids:
            for prefix in ("energy", "charge", "discharge"):
                unit = "mwh" if prefix == "energy" else "mw"
                if r[f"{prefix}_min_{unit}"][t] > r[f"{prefix}_max_{unit}"][t]:
                    raise ValueError(f"{uid}: inconsistent {prefix} limits")
            if r["energy_max_mwh"][t] > r["energy_capacity_mwh"]:
                raise ValueError(f"{uid}: energy bound exceeds rated capacity")
        r["initial_energy_mwh"] = _state_in_bounds(r["initial_energy_mwh"], f"{uid}.initial_energy_mwh",
            lower=r["energy_min_mwh"][ids[0]], upper=r["energy_max_mwh"][ids[0]], unit="MWh",
            source=r["sources"]["initial_energy_mwh"], audit=numeric_state_audit)
        r["cycle_throughput_used_mwh"] = _state_in_bounds(r["cycle_throughput_used_mwh"],
            f"{uid}.cycle_throughput_used_mwh", lower=0.,
            upper=2 * r["energy_capacity_mwh"] * r["cycle_limit"], unit="MWh",
            source=r["sources"]["cycle_throughput_used_mwh"], audit=numeric_state_audit)
        r["final_energy_bounds_mwh"] = _bounds(r["final_energy_bounds_mwh"], f"{uid}.final", required=False)
        if r["final_energy_bounds_mwh"] is not None:
            if day_end is None:
                raise ValueError("final battery bounds supplied without day end")
            lo, hi = r["final_energy_bounds_mwh"]
            if lo < r["energy_min_mwh"][day_end] or hi > r["energy_max_mwh"][day_end]:
                raise ValueError("battery final bounds must lie within hard bounds")
        r["da_anchor_mwh"] = _series(r["da_anchor_mwh"], anchors, f"{uid}.da_anchor_mwh")
        for t in anchors:
            if r["da_anchor_mwh"][t] > r["energy_capacity_mwh"]:
                raise ValueError("DA anchor exceeds rated battery capacity")
        if r["direction_policy"] not in {"hourly_only", "follow_da"}:
            raise ValueError("unsupported battery direction policy")
        if r["direction_policy"] == "follow_da" and stage != "RT_SCED":
            raise ValueError("follow_da is an explicit RT research restriction")
        r["da_direction"] = _series(r["da_direction"], ids if r["direction_policy"] == "follow_da" else (), f"{uid}.da_direction", minimum=None)
        if any(d not in (-1., 0., 1.) for d in r["da_direction"].values()):
            raise ValueError("DA directions must be -1 charge, 0 idle, +1 discharge")
        if type(r["initial_hour_direction"]) is not int or r["initial_hour_direction"] not in (-1, 0, 1):
            raise ValueError("initial_hour_direction must be -1, 0 or 1")
        if abs(clock_start-round(clock_start)) < 1e-9 and r["initial_hour_direction"] != 0:
            raise ValueError("at an hour boundary no preceding same-hour direction exists")
        policy = r["anchor_policy"]
        if not isinstance(policy, dict) or policy.get("mode") not in {"hard", "emergency"}:
            raise ValueError("explicit hard/emergency anchor_policy required")
        if policy["mode"] == "hard":
            if set(policy) != {"mode"}:
                raise ValueError("hard anchor policy has no ignored penalty settings")
        else:
            if stage != "RT_SCED" or not anchors:
                raise ValueError("emergency anchor policy requires RT hourly anchors")
            if set(policy) != {"mode", "penalty_yuan_per_mwh", "reason", "approval_source"}:
                raise ValueError("emergency anchoring requires penalty, reason and approval_source")
            policy = dict(policy)
            policy["penalty_yuan_per_mwh"] = _number(policy["penalty_yuan_per_mwh"], "anchor penalty", positive=True)
            _text(policy["reason"], "emergency reason")
            _text(policy["approval_source"], "emergency approval source")
        r["anchor_policy"] = dict(policy)
        result[uid] = r
    return result


def _pumped(data, ids, stage, day_end, core, scuc_aggregation_policy, numeric_state_audit):
    plants, units, owner = {}, {}, {}
    for pid, original in data.items():
        _text(pid, "plant ID")
        r = _record(original, PLANT_KEYS, pid)
        if not isinstance(r["units"], dict) or not r["units"]:
            raise ValueError("each pumped plant requires explicit physical units")
        if stage == "DA_SCUC" and len(r["units"]) != 1 and scuc_aggregation_policy == "single_unit":
            raise NotImplementedError("multi-unit SCUC requires explicit unit_disaggregated_research policy; official pc aggregation is not implemented")
        for uid, raw_unit in r["units"].items():
            _text(uid, "pumped unit ID")
            if uid in units:
                raise ValueError("pumped unit IDs must be globally unique")
            u = _record(raw_unit, UNIT_KEYS, uid)
            _text(u["bus"], f"{uid}.bus")
            if any((u["bus"], t) not in core.other_injection for t in ids):
                raise ValueError(f"{uid}: bus absent from energy core")
            for key in ("pump_power_mw", "pump_m3_per_mwh", "generation_m3_per_mwh"):
                u[key] = _number(u[key], f"{uid}.{key}", positive=True)
            if u["pump_m3_per_mwh"] > u["generation_m3_per_mwh"]:
                raise ValueError("pump/generation conversion implies roundtrip efficiency above one")
            for key in ("initial_mode_elapsed_h", "min_generate_h", "min_pump_h", "min_off_h"):
                u[key] = _number(u[key], f"{uid}.{key}")
            u["remaining_start_stop_events"] = _integer(u["remaining_start_stop_events"], "remaining events")
            for key in ("generation_min_mw", "generation_max_mw", "pump_bid_yuan_per_mwh", "generation_bid_yuan_per_mwh"):
                u[key] = _series(u[key], ids, f"{uid}.{key}", minimum=None if "bid" in key else 0.)
            for key in ("pump_available", "generation_available"):
                u[key] = _series(u[key], ids, f"{uid}.{key}", integer=True, maximum=1)
            for t in ids:
                if not 0 <= u["generation_min_mw"][t] <= u["generation_max_mw"][t] or u["generation_max_mw"][t] <= 0:
                    raise ValueError("positive generation maximum and consistent bounds required")
            if u["initial_mode"] not in {"generate", "pump", "off"}:
                raise NotImplementedError("unsupported pumped physical mode")
            generating = u["initial_mode"] == "generate"
            u["initial_generation_mw"] = _state_in_bounds(u["initial_generation_mw"],
                f"{uid}.initial_generation_mw", lower=u["generation_min_mw"][ids[0]] if generating else 0.,
                upper=u["generation_max_mw"][ids[0]] if generating else 0., unit="MW",
                source=u["sources"]["initial_generation_mw"], audit=numeric_state_audit)
            units[uid], owner[uid] = u, pid
        members = tuple(r["units"])
        first = units[members[0]]
        homogeneous_fields = ["generation_min_mw", "generation_max_mw"]
        if scuc_aggregation_policy == "unit_disaggregated_research":
            homogeneous_fields += ["pump_m3_per_mwh", "generation_m3_per_mwh", "min_generate_h", "min_pump_h", "min_off_h"]
        for uid in members:
            u = units[uid]
            if u["bus"] != first["bus"] or u["pump_power_mw"] != first["pump_power_mw"] or any(
                    u[k] != first[k] for k in homogeneous_fields):
                raise NotImplementedError("heterogeneous or multi-node pumped plants require a separate physical adapter")
        if {units[u]["initial_mode"] for u in members} >= {"generate", "pump"}:
            raise ValueError("initial plant state violates same-plant same-direction operation")
        for key in ("upper_min_m3", "upper_max_m3", "lower_min_m3", "lower_max_m3"):
            r[key] = _series(r[key], ids, f"{pid}.{key}")
        for reservoir in ("upper", "lower"):
            for t in ids:
                if r[f"{reservoir}_min_m3"][t] > r[f"{reservoir}_max_m3"][t]:
                    raise ValueError("inconsistent reservoir hard bounds")
            initial_key = f"{reservoir}_initial_m3"
            r[initial_key] = _state_in_bounds(r[initial_key], f"{pid}.{initial_key}",
                lower=r[f"{reservoir}_min_m3"][ids[0]], upper=r[f"{reservoir}_max_m3"][ids[0]],
                unit="m3", source=r["sources"][initial_key], audit=numeric_state_audit)
            key = f"{reservoir}_final_bounds_m3"
            r[key] = _bounds(r[key], f"{pid}.{key}", required=day_end is not None)
            if day_end is None and r[key] is not None:
                raise ValueError("reservoir final bounds supplied without day end")
            if day_end is not None and (r[key][0] < r[f"{reservoir}_min_m3"][day_end] or r[key][1] > r[f"{reservoir}_max_m3"][day_end]):
                raise ValueError("reservoir final bounds must lie within physical bounds")
            for key in (f"da_{reservoir}_m3", f"{reservoir}_band_m3"):
                r[key] = _series(r[key], ids if stage == "RT_SCED" else (), f"{pid}.{key}")
        penalties = r["band_penalty_yuan_per_m3_step"]
        if not isinstance(penalties, dict) or set(penalties) != (set(BAND_SIDES) if stage == "RT_SCED" else set()):
            raise ValueError("four separate RT reservoir-band penalty series required")
        r["band_penalty_yuan_per_m3_step"] = {}
        for side, values in penalties.items():
            values = _series(values, ids, f"{pid}.band_penalty.{side}")
            if any(value <= 0 for value in values.values()):
                raise ValueError("reservoir band penalties must be strictly positive")
            r["band_penalty_yuan_per_m3_step"][side] = values
        for key in ("max_generating_units", "max_pumping_units", "min_online_units", "max_online_units", "max_generation_increase", "max_pumping_increase"):
            r[key] = _series(r[key], ids, f"{pid}.{key}", integer=True, maximum=len(members))
        for t in ids:
            if r["min_online_units"][t] > r["max_online_units"][t]:
                raise ValueError("inconsistent pumped online-unit limits")
        r["members"], r["bus"] = members, first["bus"]
        plants[pid] = r
    return plants, units, owner



def _json_boundary(value):
    """Portable public audit; typed objects remain in a private block attribute."""
    if is_dataclass(value):
        return _json_boundary(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_boundary(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_boundary(item) for item in value]
    return value


def _rt_pumped_boundary(stage, policy, data, plants, units, owners, horizon, sources):
    """Keep physical state and planned-off logic separate, before model mutation."""
    if stage != "RT_SCED" or not units:
        if data is not None or policy not in (None, "not_applicable"):
            raise ValueError("RT pumped boundary fields are inapplicable without RT pumped units")
        return None, {}, {}, {"policy": "not_applicable"}
    if policy not in ("legacy_actual_as_plan_research", "external_initial_plan"):
        raise ValueError("explicit rt_pumped_boundary_policy required for RT pumped units")
    _text(sources.get("rt_pumped_boundary_policy"), "source_notes.rt_pumped_boundary_policy")
    if policy == "legacy_actual_as_plan_research":
        if data is not None:
            raise ValueError("legacy actual-as-plan cannot mix an external pumped boundary")
        return None, {uid: unit["initial_mode"] == "off" for uid, unit in units.items()}, {}, {
            "policy": policy, "source": sources["rt_pumped_boundary_policy"],
            "integrated_with_storage_core": False, "external_prepared_start_supported": False,
            "autonomous_rolling_cold_start_supported": False,
            "limitation": "Explicit legacy single-window research: actual mode substitutes for initial planned mode."}
    validated = validate_pumped_plan_boundary(data, expected_inventory=tuple(
        PumpedUnitIdentity(uid, owners[uid]) for uid in units))
    if data.model_initial_time != data.first_calculation_time:
        raise NotImplementedError("external_initial_plan currently requires model_initial_time == first_calculation_time; "
                                  "a frozen-interval physical path is required for other mappings")
    rows = {row.unit_id: row for row in validated.units}
    for uid, unit in units.items():
        row = rows[uid]
        if unit["initial_mode"] != row.physical_initial_mode or not math.isclose(
                unit["initial_mode_elapsed_h"], row.physical_mode_elapsed_h, rel_tol=0., abs_tol=1e-9):
            raise ValueError(f"{uid}: physical initial mode/elapsed must match observed or explicit projected boundary; never substitute planned mode")
        if row.mode_projection is not None:
            projection = row.mode_projection
            for name in ("min_off_h", "min_pump_h", "min_generate_h", "pump_power_mw"):
                if getattr(projection, name) != unit[name]:
                    raise ValueError(f"{uid}: projected physical parameter {name} must match storage resource")
            if row.physical_initial_generation_mw != unit["initial_generation_mw"]:
                raise ValueError(f"{uid}: physical initial power must match explicit issued-plan projection")
            if row.physical_remaining_start_stop_events != unit["remaining_start_stop_events"]:
                raise ValueError(f"{uid}: remaining event budget must match explicit issued-plan projection")
    for projection in data.reservoirs:
        plant = plants[projection.plant_id]
        if (plant["upper_initial_m3"] != projection.projected_upper_m3
                or plant["lower_initial_m3"] != projection.projected_lower_m3):
            raise ValueError(f"{projection.plant_id}: physical initial reservoirs must match external projected boundary")
    target_period = {data.first_calculation_time + timedelta(hours=horizon.start_h[t]): t for t in horizon.ids}
    permissions = {(uid, t, mode): 0 for uid in units for t in horizon.ids for mode in ("pump", "generate")}
    consumed = set(validated.audit.consumed_projection_preparation_ids)
    for record in data.preparations:
        if record.preparation_id in consumed:
            # Past readiness is evidence for an already-issued projected slice,
            # never a fresh in-horizon permission or a reusable start credit.
            continue
        if record.target_effective_time not in target_period:
            raise ValueError(f"{record.preparation_id}: preparation target must exactly match an in-horizon period start")
        t = target_period[record.target_effective_time]
        point = rows[record.unit_id].initial_plan
        if t == horizon.ids[0] and (record.plan_id != point.plan_id or (
                point.mode != "off" and record.target_mode != point.mode)):
            raise ValueError(f"{record.preparation_id}: first-period preparation must match initial plan ID and on target mode")
        permissions[record.unit_id, t, record.target_mode] = int(record.preparation_status == "ready")
    audit = _json_boundary(validated.audit)
    audit.update(policy=policy, source=sources["rt_pumped_boundary_policy"],
        integrated_with_storage_core=True, external_prepared_start_supported=True,
        autonomous_rolling_cold_start_supported=False,
        time_mapping_policy="explicit_coincident_initial_and_first_period_research",
        initial_power_policy="independent supplied physical initial_generation_mw must match explicit issued-plan projection when present; never inferred from initial planned mode",
        inputs=_json_boundary(data),
        units={uid: {"actual_mode": rows[uid].actual_state.mode,
                     "physical_initial_mode": rows[uid].physical_initial_mode,
                     "physical_state_source": ("issued_plan_projection_not_actual_observation"
                        if rows[uid].mode_projection is not None else "observed_mode_held_to_initial_boundary"),
                     "initial_planned_mode": rows[uid].initial_plan.mode,
                     "physical_mode_elapsed_h": rows[uid].physical_mode_elapsed_h,
                     "physical_initial_generation_mw": units[uid]["initial_generation_mw"],
                     "first_period_off_required": rows[uid].first_period_off_required}
               for uid in units},
        start_permissions=[{"unit_id": uid, "period": t, "target_mode": mode, "ready": bool(allowed)}
                           for (uid, t, mode), allowed in permissions.items()])
    return validated, {uid: rows[uid].first_period_off_required for uid in units}, permissions, audit


def attach_storage(model, *, core, horizon, stage, batteries, pumped_plants,
                   clock_start_h, anchor_periods, day_end_period, cycle_periods,
                   rt_start_stop_budget_policy, source_notes, block_name="gd_storage",
                   scuc_aggregation_policy="single_unit", rt_pumped_boundary_policy=None,
                   pumped_plan_boundary=None):
    """Validate, attach, and atomically add physical injection and cost ports.

    ``cost_expression`` is already included in ``core.other_cost`` on return.
    Input records use the exact key constants above, each with a ``sources``
    entry for every parameter. Unsupported fields are rejected, never ignored.
    Source notes also declare the conservative no-unfinished-terminal-dwell
    assumption and reserve-capacity mapping. No numeric policy defaults exist.
    """
    if not isinstance(scuc_aggregation_policy, str) or scuc_aggregation_policy not in SCUC_AGGREGATION_POLICIES:
        raise ValueError("unsupported SCUC aggregation policy")
    if stage not in STAGES or not isinstance(horizon, EnergyHorizon):
        raise ValueError("supported stage and explicit EnergyHorizon required")
    expected_budget_policy = "enforce_remaining_research" if stage == "RT_SCED" else "not_applicable"
    if rt_start_stop_budget_policy != expected_budget_policy:
        raise ValueError("explicit stage-appropriate RT start/stop budget policy required")
    if not isinstance(block_name, str) or not block_name or hasattr(model, block_name):
        raise ValueError("storage block name invalid or already attached")
    if hasattr(core, "_energy_scope") and core._energy_scope["mode"] != ("SCUC" if stage == "DA_SCUC" else "SCED"):
        raise ValueError("storage stage and thermal energy-core stage disagree")
    if core.model() is not model.model():
        raise ValueError("storage and energy core must belong to the same model")
    core_horizon = getattr(core, "_energy_horizon", None)
    if not isinstance(core_horizon, EnergyHorizon):
        raise ValueError("core must expose its complete EnergyHorizon contract")
    core_horizon.validate()
    horizon.validate()
    # Period IDs alone are unsafe: different dt changes energy and cost while
    # keeping bus indices compatible. Frozen dataclass equality compares the
    # full ordered periods, durations, ramp times, roles, settlement flags,
    # horizon mode and all source/representative mapping declarations.
    if horizon != core_horizon:
        raise ValueError("storage and energy core must share the complete horizon contract")
    if not hasattr(core, "other_injection") or not hasattr(core, "other_cost"):
        raise ValueError("core requires other_injection and other_cost Expression ports")
    if core.other_injection.ctype is not pyo.Expression or core.other_cost.ctype is not pyo.Expression or core.other_cost.is_indexed():
        raise ValueError("storage integration ports must be mutable scalar/indexed Expressions")
    if not isinstance(batteries, dict) or not isinstance(pumped_plants, dict):
        raise ValueError("battery and plant dictionaries required")
    if set(batteries) & set(pumped_plants):
        raise ValueError("battery and plant IDs must be distinct")
    needed = {"clock_start_h", "anchor_periods", "day_end_period", "cycle_periods", "terminal_dwell_policy", "reserve_capacity_mapping", "rt_start_stop_budget_policy"}
    if scuc_aggregation_policy == "unit_disaggregated_research":
        needed.add("scuc_aggregation_policy")
    if not isinstance(source_notes, dict) or not needed <= set(source_notes):
        raise ValueError("explicit time, cycle, terminal-dwell and reserve-mapping sources required")
    for key in needed:
        _text(source_notes[key], f"source_notes.{key}")
    clock_start_h, groups, anchors, cycles = _clock(horizon, stage, clock_start_h, anchor_periods, day_end_period, cycle_periods)
    ids = horizon.ids
    if hasattr(core, "T") and tuple(core.T) != ids:
        raise ValueError("storage horizon differs from energy core periods")
    numeric_state_audit = _state_boundary_audit()
    bs = _batteries(batteries, ids, anchors, stage, day_end_period, clock_start_h, core, numeric_state_audit)
    ps, us, owners = _pumped(pumped_plants, ids, stage, day_end_period, core, scuc_aggregation_policy, numeric_state_audit)
    plan_boundary, first_off, start_permissions, plan_audit = _rt_pumped_boundary(
        stage, rt_pumped_boundary_policy, pumped_plan_boundary, ps, us, owners, horizon, source_notes)
    if set(bs) & set(us):
        raise ValueError("battery and pumped unit IDs must be distinct")
    identities = {("battery", key) for key in bs} | {("plant", key) for key in ps} | {("pumped_unit", key) for key in us}
    previous_ids = set(getattr(core, "_gd_storage_resource_ids", set()))
    if previous_ids & identities:
        raise ValueError("storage resource already injected; duplicate attachment rejected")

    # Build detached: all input validation and construction precedes mutation.
    b = pyo.Block(concrete=True)
    b.T = pyo.Set(initialize=ids, ordered=True)
    b.BAT = pyo.Set(initialize=list(bs))
    b.PLANT = pyo.Set(initialize=list(ps))
    b.UNIT = pyo.Set(initialize=list(us))
    b.MODES = pyo.Set(initialize=("generate", "pump", "off"))
    b.ANCHOR = pyo.Set(initialize=anchors, ordered=True)
    b.BAND = pyo.Set(initialize=BAND_SIDES)
    b.energy = pyo.Var(b.BAT, b.T, bounds=lambda _, u, t: (bs[u]["energy_min_mwh"][t], bs[u]["energy_max_mwh"][t]))
    b.charge = pyo.Var(b.BAT, b.T, domain=pyo.NonNegativeReals)
    b.discharge = pyo.Var(b.BAT, b.T, domain=pyo.NonNegativeReals)
    b.charge_mode = pyo.Var(b.BAT, b.T, domain=pyo.Binary)
    b.discharge_mode = pyo.Var(b.BAT, b.T, domain=pyo.Binary)
    b.anchor_low = pyo.Var(b.BAT, b.ANCHOR, domain=pyo.NonNegativeReals)
    b.anchor_high = pyo.Var(b.BAT, b.ANCHOR, domain=pyo.NonNegativeReals)
    b.battery_net_power = pyo.Expression(b.BAT, b.T, rule=lambda _, u, t: b.discharge[u, t]-b.charge[u, t])
    b.soc = pyo.Expression(b.BAT, b.T, rule=lambda _, u, t: b.energy[u, t]/bs[u]["energy_capacity_mwh"])
    b.c_battery_power = pyo.ConstraintList()
    b.c_battery_direction = pyo.ConstraintList()
    b.c_battery_final = pyo.ConstraintList()
    b.c_battery_cycle = pyo.ConstraintList()
    b.c_battery_balance = pyo.Constraint(b.BAT, b.T, rule=lambda _, u, t:
        b.energy[u, t] == (bs[u]["initial_energy_mwh"] if horizon.previous[t] is None else b.energy[u, horizon.previous[t]])
        + horizon.duration_h[t]*(bs[u]["eta"]*b.charge[u, t]-b.discharge[u, t]/bs[u]["eta"]))
    b.c_battery_anchor = pyo.Constraint(b.BAT, b.ANCHOR, rule=lambda _, u, t:
        b.energy[u, t]+b.anchor_low[u, t]-b.anchor_high[u, t] == bs[u]["da_anchor_mwh"][t])
    for uid, r in bs.items():
        for t in ids:
            for power, mode, prefix in ((b.charge, b.charge_mode, "charge"), (b.discharge, b.discharge_mode, "discharge")):
                b.c_battery_power.add(power[uid, t] >= r[f"{prefix}_min_mw"][t]*mode[uid, t])
                b.c_battery_power.add(power[uid, t] <= r[f"{prefix}_max_mw"][t]*mode[uid, t])
            b.c_battery_power.add(b.charge_mode[uid, t]+b.discharge_mode[uid, t] <= 1)
            # Pairwise condition exactly disallows both directions within a clock hour.
            for other in ids:
                if groups[other] == groups[t] and other != t:
                    b.c_battery_direction.add(b.charge_mode[uid, t]+b.discharge_mode[uid, other] <= 1)
            if groups[t] == groups[ids[0]] and r["initial_hour_direction"]:
                (b.discharge_mode if r["initial_hour_direction"] < 0 else b.charge_mode)[uid, t].fix(0)
            if r["direction_policy"] == "follow_da":
                direction = r["da_direction"][t]
                if direction <= 0:
                    b.discharge_mode[uid, t].fix(0)
                if direction >= 0:
                    b.charge_mode[uid, t].fix(0)
        if cycles:
            b.c_battery_cycle.add(r["cycle_throughput_used_mwh"] + sum(
                horizon.duration_h[t]*(b.discharge[uid, t]/r["eta"]+r["eta"]*b.charge[uid, t]) for t in cycles)
                <= 2*r["energy_capacity_mwh"]*r["cycle_limit"])
        if r["final_energy_bounds_mwh"] is not None:
            lo, hi = r["final_energy_bounds_mwh"]
            b.c_battery_final.add((lo, b.energy[uid, day_end_period], hi))
        if r["anchor_policy"]["mode"] == "hard":
            for t in anchors:
                b.anchor_low[uid, t].fix(0)
                b.anchor_high[uid, t].fix(0)

    b.generate = pyo.Var(b.UNIT, b.T, domain=pyo.NonNegativeReals)
    b.pump = pyo.Var(b.UNIT, b.T, domain=pyo.NonNegativeReals)
    b.mode = pyo.Var(b.UNIT, b.T, b.MODES, domain=pyo.Binary)
    b.start = pyo.Var(b.UNIT, b.T, b.MODES, domain=pyo.Binary)
    b.stop = pyo.Var(b.UNIT, b.T, b.MODES, domain=pyo.Binary)
    b.plant_pump_mode = pyo.Var(b.PLANT, b.T, domain=pyo.Binary)
    b.upper_volume = pyo.Var(b.PLANT, b.T, bounds=lambda _, p, t: (ps[p]["upper_min_m3"][t], ps[p]["upper_max_m3"][t]))
    b.lower_volume = pyo.Var(b.PLANT, b.T, bounds=lambda _, p, t: (ps[p]["lower_min_m3"][t], ps[p]["lower_max_m3"][t]))
    b.band_deviation = pyo.Var(b.PLANT, b.T, b.BAND, domain=pyo.NonNegativeReals)
    b.unit_net_power = pyo.Expression(b.UNIT, b.T, rule=lambda _, u, t: b.generate[u, t]-b.pump[u, t])
    b.plant_generation = pyo.Expression(b.PLANT, b.T, rule=lambda _, p, t: sum(b.generate[u, t] for u in ps[p]["members"]))
    b.plant_pumping = pyo.Expression(b.PLANT, b.T, rule=lambda _, p, t: sum(b.pump[u, t] for u in ps[p]["members"]))
    b.plant_net_power = pyo.Expression(b.PLANT, b.T, rule=lambda _, p, t: b.plant_generation[p, t]-b.plant_pumping[p, t])
    b.water_transfer_to_upper = pyo.Expression(b.PLANT, b.T, rule=lambda _, p, t: horizon.duration_h[t]*sum(
        us[u]["pump_m3_per_mwh"]*b.pump[u, t]-us[u]["generation_m3_per_mwh"]*b.generate[u, t] for u in ps[p]["members"]))
    b.c_upper_balance = pyo.Constraint(b.PLANT, b.T, rule=lambda _, p, t: b.upper_volume[p, t] == (
        ps[p]["upper_initial_m3"] if horizon.previous[t] is None else b.upper_volume[p, horizon.previous[t]])+b.water_transfer_to_upper[p, t])
    b.c_lower_balance = pyo.Constraint(b.PLANT, b.T, rule=lambda _, p, t: b.lower_volume[p, t] == (
        ps[p]["lower_initial_m3"] if horizon.previous[t] is None else b.lower_volume[p, horizon.previous[t]])-b.water_transfer_to_upper[p, t])
    for name in ("unit_power", "transition", "initial_dwell", "minimum_dwell", "terminal_dwell", "preparation", "external_readiness", "plant_modes", "plant_counts", "plant_increase", "start_stop_budget", "reservoir_final", "reservoir_band", "scuc_ramp"):
        setattr(b, "c_"+name, pyo.ConstraintList())

    def prior_mode(uid, t, q):
        previous = horizon.previous[t]
        return int(us[uid]["initial_mode"] == q) if previous is None else b.mode[uid, previous, q]

    for uid, u in us.items():
        for t in ids:
            b.c_unit_power.add(sum(b.mode[uid, t, q] for q in b.MODES) == 1)
            b.c_unit_power.add(b.pump[uid, t] == u["pump_power_mw"]*b.mode[uid, t, "pump"])
            b.c_unit_power.add(b.generate[uid, t] >= u["generation_min_mw"][t]*b.mode[uid, t, "generate"])
            b.c_unit_power.add(b.generate[uid, t] <= u["generation_max_mw"][t]*b.mode[uid, t, "generate"])
            b.c_unit_power.add(b.mode[uid, t, "pump"] <= u["pump_available"][t])
            b.c_unit_power.add(b.mode[uid, t, "generate"] <= u["generation_available"][t])
            b.c_transition.add(prior_mode(uid, t, "pump")+b.mode[uid, t, "generate"] <= 1)
            b.c_transition.add(prior_mode(uid, t, "generate")+b.mode[uid, t, "pump"] <= 1)
            for q in b.MODES:
                b.c_transition.add(b.mode[uid, t, q]-prior_mode(uid, t, q) == b.start[uid, t, q]-b.stop[uid, t, q])
                b.c_transition.add(b.start[uid, t, q]+b.stop[uid, t, q] <= 1)
                dwell = u[f"min_{q}_h"]
                if u["initial_mode"] == q and horizon.start_h[t]+u["initial_mode_elapsed_h"] < dwell-1e-9:
                    b.c_initial_dwell.add(b.mode[uid, t, q] == 1)
                for other in ids:
                    elapsed = horizon.start_h[other]-horizon.start_h[t]
                    if 0 <= elapsed < dwell-1e-9:
                        b.c_minimum_dwell.add(b.mode[uid, other, q] >= b.start[uid, t, q])
                if horizon.end_h[ids[-1]]-horizon.start_h[t] < dwell-1e-9:
                    b.c_terminal_dwell.add(b.start[uid, t, q] == 0)
        if stage == "RT_SCED" and first_off[uid]:
            b.c_preparation.add(b.mode[uid, ids[0], "off"] == 1)
        if plan_boundary is not None:
            for t in ids:
                for mode in ("pump", "generate"):
                    b.c_external_readiness.add(b.start[uid, t, mode] <= start_permissions[uid, t, mode])
        if stage.startswith("DA") or rt_start_stop_budget_policy == "enforce_remaining_research":
            b.c_start_stop_budget.add(sum(b.start[uid, t, q]+b.stop[uid, t, q] for t in ids for q in ("generate", "pump")) <= u["remaining_start_stop_events"])

    system_capacity = {}
    for pid, r in ps.items():
        members = r["members"]
        single = us[members[0]]
        system_capacity[pid] = {
            "max_generation_mw": {t: single["generation_max_mw"][t]*min(r["max_generating_units"][t], r["max_online_units"][t], sum(us[u]["generation_available"][t] for u in members)) for t in ids},
            "max_pumping_mw": {t: single["pump_power_mw"]*min(r["max_pumping_units"][t], r["max_online_units"][t], sum(us[u]["pump_available"][t] for u in members)) for t in ids},
        }
        for t in ids:
            generates = sum(b.mode[u, t, "generate"] for u in members)
            pumps = sum(b.mode[u, t, "pump"] for u in members)
            b.c_plant_modes.add(pumps <= len(members)*b.plant_pump_mode[pid, t])
            b.c_plant_modes.add(pumps >= b.plant_pump_mode[pid, t])
            b.c_plant_modes.add(generates <= len(members)*(1-b.plant_pump_mode[pid, t]))
            b.c_plant_counts.add(generates <= r["max_generating_units"][t])
            b.c_plant_counts.add(pumps <= r["max_pumping_units"][t])
            b.c_plant_counts.add((r["min_online_units"][t], generates+pumps, r["max_online_units"][t]))
            b.c_plant_increase.add(sum(b.mode[u, t, "generate"]-prior_mode(u, t, "generate") for u in members) <= r["max_generation_increase"][t])
            b.c_plant_increase.add(sum(b.mode[u, t, "pump"]-prior_mode(u, t, "pump") for u in members) <= r["max_pumping_increase"][t])
            if stage == "DA_SCUC":
                previous_generation = (sum(us[u]["initial_generation_mw"] for u in members)
                    if horizon.previous[t] is None else b.plant_generation[pid, horizon.previous[t]])
                b.c_scuc_ramp.add(b.plant_generation[pid, t]-previous_generation
                    <= r["max_generation_increase"][t]*single["generation_max_mw"][t])
            for reservoir, volume in (("upper", b.upper_volume), ("lower", b.lower_volume)):
                if stage == "RT_SCED":
                    target, width = r[f"da_{reservoir}_m3"][t], r[f"{reservoir}_band_m3"][t]
                    b.c_reservoir_band.add(volume[pid, t]+b.band_deviation[pid, t, reservoir+"_low"] >= target-width)
                    b.c_reservoir_band.add(volume[pid, t]-b.band_deviation[pid, t, reservoir+"_high"] <= target+width)
                else:
                    for side in ("low", "high"):
                        b.band_deviation[pid, t, reservoir+"_"+side].fix(0)
        if day_end_period is not None:
            for reservoir, volume in (("upper", b.upper_volume), ("lower", b.lower_volume)):
                lo, hi = r[f"{reservoir}_final_bounds_m3"]
                b.c_reservoir_final.add((lo, volume[pid, day_end_period], hi))

    b.battery_bid_cost = pyo.Expression(expr=sum(horizon.duration_h[t]*(r["discharge_bid_yuan_per_mwh"][t]*b.discharge[u, t]-r["charge_bid_yuan_per_mwh"][t]*b.charge[u, t]) for u, r in bs.items() for t in ids))
    b.pumped_bid_cost = pyo.Expression(expr=sum(horizon.duration_h[t]*(r["generation_bid_yuan_per_mwh"][t]*b.generate[u, t]-r["pump_bid_yuan_per_mwh"][t]*b.pump[u, t]) for u, r in us.items() for t in ids))
    b.anchor_penalty_cost = pyo.Expression(expr=sum(r["anchor_policy"]["penalty_yuan_per_mwh"]*(b.anchor_low[u, t]+b.anchor_high[u, t]) for u, r in bs.items() if r["anchor_policy"]["mode"] == "emergency" for t in anchors))
    b.reservoir_band_cost = pyo.Expression(expr=sum(r["band_penalty_yuan_per_m3_step"][side][t]*b.band_deviation[p, t, side] for p, r in ps.items() for side in r["band_penalty_yuan_per_m3_step"] for t in ids))
    b.cost_expression = pyo.Expression(expr=b.battery_bid_cost+b.pumped_bid_cost+b.anchor_penalty_cost+b.reservoir_band_cost)
    b.online_generation_capacity = pyo.Expression(b.PLANT, b.T, rule=lambda _, p, t: sum(us[u]["generation_max_mw"][t]*b.mode[u, t, "generate"] for u in ps[p]["members"]))
    # GDE2026 p108: P_pp,max = sum(P_i,on,max). Installed/available standby
    # units are not current generation-online capacity. Readiness to start is
    # separately gated and does not itself qualify offline emergency reserve.
    b.emergency_generation_capacity = pyo.Expression(b.PLANT, b.T,
        rule=lambda _, p, t: b.online_generation_capacity[p, t])
    if stage == "RT_SCED":
        reserve_ports = {p: {
            "net_power": {t: b.plant_net_power[p, t] for t in ids},
            "primary_capacity": {t: b.online_generation_capacity[p, t] for t in ids},
            "generation_power": {t: b.plant_generation[p, t] for t in ids},
            "pumping_power": {t: b.plant_pumping[p, t] for t in ids},
            "pumping_mode": {t: b.plant_pump_mode[p, t] for t in ids},
            "emergency_generation_capacity": {t: b.emergency_generation_capacity[p, t] for t in ids},
            "generation_cap_upper_mw": dict(system_capacity[p]["max_generation_mw"]),
            "pumping_cap_upper_mw": dict(system_capacity[p]["max_pumping_mw"]),
            "minimum_pumping_mw": {t: us[ps[p]["members"][0]]["pump_power_mw"] for t in ids},
        } for p in ps}
    elif stage == "DA_SCUC" and scuc_aggregation_policy == "unit_disaggregated_research":
        # Only this explicit research branch aggregates the SCUC primary
        # reserve headroom before the reserve component applies the group min.
        # Physical dispatch, transitions and event budgets remain per unit.
        reserve_ports = {p: {
            "net_power": {t: b.plant_net_power[p, t] for t in ids},
            "primary_capacity": dict(system_capacity[p]["max_generation_mw"]),
            "plant_id": p,
        } for p in ps}
    else:
        reserve_ports = {u: {
            "net_power": {t: b.unit_net_power[u, t] for t in ids},
            "primary_capacity": {t: (system_capacity[owners[u]]["max_generation_mw"][t] if stage == "DA_SCUC" else us[u]["generation_max_mw"][t]*b.mode[u, t, "generate"]) for t in ids},
            "plant_id": owners[u],
        } for u in us}
    b._pumped_plan_boundary = plan_boundary
    b._storage_horizon = horizon
    b._storage_inputs = deepcopy({"batteries": bs, "plants": ps, "units": us})
    b.reserve_ports = reserve_ports
    b.pumped_system_capacity = system_capacity if stage.startswith("DA") else None
    b.audit = {"stage": stage, "sources": dict(source_notes), "horizon": horizon.audit(),
               "numeric_state_boundary": numeric_state_audit,
               "battery_sources": {u: dict(r["sources"]) for u, r in bs.items()},
               "plant_sources": {p: dict(r["sources"]) for p, r in ps.items()},
               "unit_sources": {u: dict(r["sources"]) for u, r in us.items()},
               "anchor_policy": {u: dict(r["anchor_policy"]) for u, r in bs.items()},
               "clock_start_h": clock_start_h, "anchor_periods": anchors, "day_end_period": day_end_period,
               "cycle_periods": cycles, "terminal_dwell_policy": "forbid_unfinished_new_state",
               "rt_start_stop_budget_policy": rt_start_stop_budget_policy,
               "scuc_aggregation_policy": scuc_aggregation_policy,
               "scuc_aggregation_policy_source": source_notes.get("scuc_aggregation_policy",
                   "Compatibility baseline: DA SCUC permits one physical unit per optimization unit."),
               "scuc_research_path_active": stage == "DA_SCUC" and scuc_aggregation_policy == "unit_disaggregated_research",
               "official_multiunit_scuc_aggregation_reproduced": False,
               "primary_reserve_granularity": {"DA_SCUC": "optimization_unit", "DA_SCED": "generating_unit", "RT_SCED": "plant"}[stage],
               "scuc_research_groups": {p: {"bus": r["bus"], "unit_ids": list(r["members"])} for p, r in ps.items()}
                   if stage == "DA_SCUC" and scuc_aggregation_policy == "unit_disaggregated_research" else {},
               "rolling_cold_start_supported": False,
               "external_prepared_start_supported": plan_boundary is not None,
               "rt_pumped_boundary_policy": plan_audit["policy"],
               "pumped_plan_boundary": plan_audit,
               "rt_emergency_pumped_capacity": {
                   "mapping": "sum_current_generating_unit_maximum" if stage == "RT_SCED" else "not_applicable",
                   "source": "GDE2026 RT SCED 6.6.2.1(8)(d), printed p108: P_pp,max=sum(P_i,on,max); online definition p105; RT LMP p120",
                   "offline_standby_capacity_inferred": False,
                   "preparation_confers_reserve_qualification": False,
                   "installed_and_da_system_capacity_unchanged": True},
               "cold_start_boundary_status": ("external_initial_plan_and_known_ready_required_per_start"
                   if plan_boundary is not None else "unsupported_without_external_preparation_or_initial_plan_state"),
               "battery_in_reserve_sum": False, "conventional_hydro": False, "value_objective": False,
               "full_guangdong_equivalence_verified": False, "band_penalty_unit": "yuan/m3/step",
               "unsupported": ["official multi-unit SCUC optimization-unit aggregation", "variable-speed or heterogeneous pumped plant", "multi-node plant", "hydraulic head and natural inflow", "piecewise storage bids", "AGC boundary mapping", "plant-control boundary mode", "rolling cold-start from actual off without external preparation boundary"]}

    injections = {}
    for uid, r in bs.items():
        for t in ids:
            key = (r["bus"], t)
            injections[key] = injections.get(key, 0)+b.battery_net_power[uid, t]
    for pid, r in ps.items():
        for t in ids:
            key = (r["bus"], t)
            injections[key] = injections.get(key, 0)+b.plant_net_power[pid, t]
    snapshots = {key: core.other_injection[key].expr for key in injections}
    cost_snapshot = core.other_cost.expr
    had_registry = hasattr(core, "_gd_storage_resource_ids")
    try:
        model.add_component(block_name, b)
        for key, injection in injections.items():
            core.other_injection[key].set_value(snapshots[key]+injection)
        core.other_cost.set_value(cost_snapshot+b.cost_expression)
        core._gd_storage_resource_ids = previous_ids | identities
    except Exception:
        for key, expression in snapshots.items():
            core.other_injection[key].set_value(expression)
        core.other_cost.set_value(cost_snapshot)
        if model.component(block_name) is b:
            model.del_component(block_name)
        if had_registry:
            core._gd_storage_resource_ids = previous_ids
        elif hasattr(core, "_gd_storage_resource_ids"):
            delattr(core, "_gd_storage_resource_ids")
        raise
    return b


def terminal_storage_state(block, *, through_period):
    """Read an executed boundary, never automatically the end of a lookahead.

    The caller must verify solution feasibility first. Returned values preserve
    actual energy/volume, same-hour direction and unit state ages; they do not
    replace observations with DA targets or reset daily budgets at midnight.
    ``delivery_day_ended`` tells orchestration when an explicit daily reset is
    needed. This reader does not certify solver status or physical feasibility.
    """
    horizon = block._storage_horizon
    if type(through_period) is not int or through_period not in horizon.ids:
        raise ValueError("explicit in-horizon executed boundary required")
    n = horizon.ids.index(through_period)
    included = horizon.ids[:n+1]
    end = horizon.end_h[through_period]
    clock_end = block.audit["clock_start_h"]+end
    on_hour = abs(clock_end-round(clock_end)) < 1e-9

    def read(expression):
        value = pyo.value(expression, exception=False)
        return _number(value, "finite solved state", minimum=None)

    def binary(expression):
        value = read(expression)
        if min(abs(value), abs(value-1.)) > 1e-6:
            raise ValueError("executed storage state must be integral")
        return int(value > .5)

    result = {"through_period": through_period, "clock_end_h": clock_end,
              "delivery_day_ended": through_period == block.audit["day_end_period"],
              "batteries": {}, "pumped_plants": {}, "pumped_units": {},
              "solution_feasibility_verified_by_reader": False,
              "rolling_cold_start_supported": False,
              "external_prepared_start_supported": block.audit["external_prepared_start_supported"],
              "rt_pumped_boundary_policy": block.audit["rt_pumped_boundary_policy"],
              "cold_start_requires_external_preparation_units": [],
              "rt_start_stop_budget_policy": block.audit["rt_start_stop_budget_policy"]}
    for uid, r in block._storage_inputs["batteries"].items():
        energy = read(block.energy[uid, through_period])
        used = r["cycle_throughput_used_mwh"] + sum(horizon.duration_h[t]*(
            read(block.discharge[uid, t])/r["eta"]+r["eta"]*read(block.charge[uid, t]))
            for t in included if t in block.audit["cycle_periods"])
        direction = 0
        if not on_hour:
            hour = math.floor(clock_end+1e-9)
            directions = set()
            if math.floor(block.audit["clock_start_h"]+1e-9) == hour and r["initial_hour_direction"]:
                directions.add(r["initial_hour_direction"])
            for t in included:
                if math.floor(block.audit["clock_start_h"]+horizon.start_h[t]+1e-9) == hour:
                    if binary(block.charge_mode[uid, t]): directions.add(-1)
                    if binary(block.discharge_mode[uid, t]): directions.add(1)
            if len(directions) > 1:
                raise ValueError("executed battery history reverses within an hour")
            direction = next(iter(directions), 0)
        result["batteries"][uid] = dict(initial_energy_mwh=energy, cycle_throughput_used_mwh=used,
                                        initial_hour_direction=direction)
    for pid in block.PLANT:
        result["pumped_plants"][pid] = dict(upper_initial_m3=read(block.upper_volume[pid, through_period]),
                                           lower_initial_m3=read(block.lower_volume[pid, through_period]))
    for uid, u in block._storage_inputs["units"].items():
        previous = u["initial_mode"]
        last_change = None
        events = 0
        for t in included:
            current = [q for q in block.MODES if binary(block.mode[uid, t, q])]
            if len(current) != 1:
                raise ValueError("executed pumped unit requires exactly one physical mode")
            current = current[0]
            if current != previous:
                if {current, previous} == {"generate", "pump"}:
                    raise ValueError("executed pumped history contains a direct pump/generate transition")
                last_change = horizon.start_h[t]
                events += int(current != "off")+int(previous != "off")
            previous = current
        age = end-last_change if last_change is not None else u["initial_mode_elapsed_h"]+end
        remaining_events = u["remaining_start_stop_events"]-events
        if remaining_events < 0:
            raise ValueError("executed pumped history exceeds remaining start/stop event budget")
        if previous == "off":
            result["cold_start_requires_external_preparation_units"].append(uid)
        result["pumped_units"][uid] = dict(initial_mode=previous, initial_mode_elapsed_h=age,
            initial_generation_mw=read(block.generate[uid, through_period]),
            remaining_start_stop_events=remaining_events, executed_start_stop_events=events,
            minimum_state_remaining_h=max(0., u[f"min_{previous}_h"]-age))
    return result
