"""Solver-free external boundary contract, not a pumped-start scheduler.

GDE2026 RT SCED (23), p113, and RT LMP (17), p123, use the initial
*planned* mode for first-point no-start. Observations remain physical inputs.
See rebuild docs/18_抽蓄实时初始计划边界.md and docs/20_抽蓄实时计划与准备接线.md.
Preparation records are external assertions, never autonomous start scheduling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isclose, isfinite
from numbers import Real
from typing import Literal


PumpedMode = Literal["off", "pump", "generate"]
PlanKind = Literal["day_ahead", "intraday", "real_time_dispatch", "dispatch_adjustment"]
ProvenanceKind = Literal["external_asserted", "synthetic"]

RULE_SOURCES = (
    "GDE2026 §6.6.2.1(23), printed p113: initial planned off prohibits first-point start",
    "GDE2026 §6.6.2.2(17), printed p123: same prohibition in RT LMP",
    "GDE2026 §5.3.1.4(5), printed p13: measured reservoirs plus existing RT plan",
    "https://nxepta.com/attached/file/20260205/20260205011253766.pdf",
)
SOURCE_LIMITATIONS = (
    "Source and approval strings are caller assertions, not authenticated dispatch approval.",
    "The official wall-clock/prior-plan-index mapping of t=0 is not established.",
    "Mode validity is an external hold assertion, not future actual telemetry.",
    "No preparation duration, planned-off ready override, or implicit DA/RT-tail selection is inferred.",
    "An on planned mode removes only this no-start condition; it does not authorize a start.",
    "Reservoir projection arithmetic, physical limits and preparation feasibility are not verified.",
    "Validity intervals are external inputs; no official numeric staleness threshold is invented.",
    "The pure validator does not construct a storage model; autonomous rolling cold start is unsupported.",
    "Explicit mode paths assume issued-plan execution; their projected state is not actual telemetry.",
    "Mode-path arithmetic does not certify ramps, availability, plant-wide limits or reservoir feasibility.",
)


class PumpedBoundaryInputError(ValueError):
    """Incomplete, inconsistent, unavailable or out-of-scope input."""


class UnsupportedPumpedBoundaryError(NotImplementedError):
    """An explicit physical/plan deviation needs a separate boundary process."""


@dataclass(frozen=True, slots=True)
class PumpedUnitIdentity:
    unit_id: str
    plant_id: str


@dataclass(frozen=True, slots=True)
class ObservedPumpedState:
    unit_id: str
    mode: PumpedMode
    observed_at: datetime
    available_at: datetime
    mode_elapsed_h: float
    source: str
    quality: Literal["usable"]
    mode_valid_until: datetime
    mode_validity_source: str


@dataclass(frozen=True, slots=True)
class ExternalPumpedPlan:
    plan_id: str
    unit_ids: tuple[str, ...]
    kind: PlanKind
    provenance: ProvenanceKind
    source: str
    issued_at: datetime
    published_at: datetime
    approved_at: datetime
    approval_status: Literal["approved"]
    approval_id: str
    approval_source: str
    effective_from: datetime
    effective_until: datetime


@dataclass(frozen=True, slots=True)
class InitialPlannedPumpedState:
    unit_id: str
    mode: PumpedMode
    plan_id: str
    effective_at: datetime
    point_mapping_source: str
    executability_source: str


@dataclass(frozen=True, slots=True)
class ReservoirPlanSegment:
    plan_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class ReservoirProjection:
    plant_id: str
    measured_at: datetime
    available_at: datetime
    measured_upper_m3: float
    measured_lower_m3: float
    measurement_source: str
    projected_at: datetime
    projected_upper_m3: float
    projected_lower_m3: float
    projection_id: str
    projection_source: str
    plan_segments: tuple[ReservoirPlanSegment, ...]


@dataclass(frozen=True, slots=True)
class ApprovedPumpedPreparation:
    """One externally approved start target, with readiness known by cutoff.

    not_ready is explicit absence of completed preparation, not a forecast of
    when completion will occur. Each record targets exactly one start instant.
    """
    preparation_id: str
    unit_id: str
    plan_id: str
    target_mode: Literal["pump", "generate"]
    target_effective_time: datetime
    preparation_status: Literal["ready", "not_ready"]
    ready_at: datetime | None
    valid_until: datetime
    issued_at: datetime
    published_at: datetime
    approved_at: datetime
    approval_status: Literal["approved"]
    approval_id: str
    approval_source: str
    source: str
    provenance: ProvenanceKind
    ready_source: str


@dataclass(frozen=True, slots=True)
class PumpedModePlanSegment:
    """One constant-output issued execution slice, not a startup trajectory."""
    plan_id: str
    start: datetime
    end: datetime
    prior_mode: PumpedMode
    mode: PumpedMode
    generation_mw: float
    pumping_mw: float
    generation_mwh: float
    pumping_mwh: float
    start_stop_events: int
    preparation_id: str | None
    source: str


@dataclass(frozen=True, slots=True)
class PumpedModeProjection:
    """Explicit observed inputs and physical parameters for a plan projection.

    Generation limits apply to these historical/frozen constant-output slices.
    The storage adapter also checks dwell and fixed pump power against its own
    resource inventory; no official parameter or preparation time is inferred.
    """
    projection_id: str
    unit_id: str
    source: str
    execution_assumption: Literal["issued_plan_following_not_observed"]
    observed_generation_mw: float
    observed_remaining_start_stop_events: int
    observed_power_and_events_source: str
    min_off_h: float
    min_pump_h: float
    min_generate_h: float
    pump_power_mw: float
    generation_min_mw: float
    generation_max_mw: float
    physical_parameters_source: str
    segments: tuple[PumpedModePlanSegment, ...]


@dataclass(frozen=True, slots=True)
class PumpedPlanBoundaryInput:
    inventory: tuple[PumpedUnitIdentity, ...]
    information_cutoff: datetime
    model_initial_time: datetime
    first_calculation_time: datetime
    time_mapping_source: str
    intraday_state_market_enabled: bool
    observations: tuple[ObservedPumpedState, ...]
    initial_plans: tuple[InitialPlannedPumpedState, ...]
    plans: tuple[ExternalPumpedPlan, ...]
    reservoirs: tuple[ReservoirProjection, ...]
    preparations: tuple[ApprovedPumpedPreparation, ...]
    mode_projections: tuple[PumpedModeProjection, ...] = ()


@dataclass(frozen=True, slots=True)
class PumpedUnitBoundary:
    unit_id: str
    actual_state: ObservedPumpedState
    initial_plan: InitialPlannedPumpedState
    physical_initial_mode: PumpedMode
    physical_mode_elapsed_h: float
    first_period_off_required: bool
    actual_plan_modes_differ: bool
    mode_projection: PumpedModeProjection | None = None
    physical_initial_generation_mw: float | None = None
    physical_remaining_start_stop_events: int | None = None
    consumed_projection_preparation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PumpedBoundaryAudit:
    rule_sources: tuple[str, ...]
    limitations: tuple[str, ...]
    observation_age_at_cutoff_h: tuple[tuple[str, float], ...]
    plan_age_at_cutoff_h: tuple[tuple[str, float], ...]
    differing_mode_unit_ids: tuple[str, ...]
    synthetic_plan_ids: tuple[str, ...]
    validated_preparation_ids: tuple[str, ...]
    synthetic_preparation_ids: tuple[str, ...]
    approval_authenticity_verified: bool = False
    official_initial_time_mapping_verified: bool = False
    reservoir_projection_arithmetic_verified: bool = False
    preparation_feasibility_verified: bool = False
    integrated_with_storage_core: bool = False
    autonomous_rolling_cold_start_supported: bool = False
    official_staleness_limit_h: None = None
    mode_projection_ids: tuple[str, ...] = ()
    mode_projected_unit_ids: tuple[str, ...] = ()
    consumed_projection_preparation_ids: tuple[str, ...] = ()
    mode_projection_arithmetic_verified: bool = False
    mode_projection_is_actual_observation: bool = False
    mode_projection_full_physical_feasibility_verified: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedPumpedPlanBoundary:
    inputs: PumpedPlanBoundaryInput
    units: tuple[PumpedUnitBoundary, ...]
    audit: PumpedBoundaryAudit


def _require(condition, message):
    if not condition:
        raise PumpedBoundaryInputError(message)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label + ": nonempty text required")


def _time(value, label):
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             label + ": timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _number(value, label):
    message = label + ": finite nonnegative real required"
    _require(not isinstance(value, bool) and isinstance(value, Real), message)
    try:
        converted = float(value)
    except (OverflowError, ValueError) as error:
        raise PumpedBoundaryInputError(message) from error
    _require(isfinite(converted) and converted >= 0, message)
    return converted


def _mode(value, label):
    _require(isinstance(value, str) and value in ("off", "pump", "generate"),
             label + ": off, pump or generate required")


def _records(values, record_type, key, label):
    _require(isinstance(values, tuple), label + ": immutable tuple required")
    result = {}
    for record in values:
        _require(isinstance(record, record_type), label + ": typed record required")
        name = getattr(record, key)
        _text(name, label + "." + key)
        _require(name not in result, label + ": duplicate " + name)
        result[name] = record
    return result


def _inventory(values, label):
    rows = _records(values, PumpedUnitIdentity, "unit_id", label)
    _require(bool(rows), label + ": nonempty inventory required")
    for row in rows.values():
        _text(row.plant_id, label + ".plant_id")
    return {uid: row.plant_id for uid, row in rows.items()}


def _exact(rows, expected, label):
    _require(set(rows) == set(expected), label + ": exact inventory coverage required")


def _integer(value, label):
    _require(type(value) is int and value >= 0, label + ": nonnegative integer required")
    return value


def _project_mode(path, observation, initial, cutoff, plans, plan_intervals, preparations):
    """Compute a sourced constant-output path; never mutate an observation."""
    uid = path.unit_id
    for field in ("projection_id", "source", "observed_power_and_events_source", "physical_parameters_source"):
        _text(getattr(path, field), uid + ".mode_projection." + field)
    _require(path.execution_assumption == "issued_plan_following_not_observed",
             uid + ": explicit issued-plan execution assumption required; not an observation")
    limits = {field: _number(getattr(path, field), uid + "." + field) for field in (
        "min_off_h", "min_pump_h", "min_generate_h", "pump_power_mw", "generation_min_mw", "generation_max_mw")}
    _require(limits["generation_min_mw"] <= limits["generation_max_mw"],
             uid + ": inconsistent frozen generation limits")
    power = _number(path.observed_generation_mw, uid + ".observed_generation_mw")
    _require(observation.mode == "generate" or power == 0.,
             uid + ": observed non-generating mode requires zero generation power")
    remaining = _integer(path.observed_remaining_start_stop_events, uid + ".observed_remaining_start_stop_events")
    _require(isinstance(path.segments, tuple) and bool(path.segments),
             uid + ": nonempty immutable mode projection segments tuple required")
    cursor = _time(observation.observed_at, uid + ".observed_at")
    mode, age = observation.mode, float(observation.mode_elapsed_h)
    used, consumed = set(), []
    for segment in path.segments:
        _require(isinstance(segment, PumpedModePlanSegment), uid + ": typed mode segment required")
        _text(segment.source, uid + ".segment.source")
        _text(segment.plan_id, uid + ".segment.plan_id")
        _require(segment.plan_id in plans, uid + ": missing referenced mode projection plan")
        _require(segment.plan_id not in used, uid + ": duplicate mode projection plan; one slice per issued plan required")
        start = _time(segment.start, uid + ".segment.start")
        end = _time(segment.end, uid + ".segment.end")
        _require(start == cursor and start < end <= initial,
                 uid + ": ordered continuous mode projection coverage required, without gaps or overlaps")
        plan = plans[segment.plan_id]
        valid_start, valid_end = plan_intervals[segment.plan_id]
        _require(plan.kind in ("real_time_dispatch", "dispatch_adjustment"),
                 uid + ": mode projection requires an existing RT or dispatch-adjustment plan")
        _require(uid in plan.unit_ids and valid_start <= start and end <= valid_end,
                 uid + ": mode projection plan outside unit/time scope")
        issued = _time(plan.issued_at, uid + ".segment.plan.issued_at")
        _require(_time(plan.published_at, uid) <= min(start, cutoff)
                 and _time(plan.approved_at, uid) <= min(start, cutoff),
                 uid + ": mode projection plan unavailable or unapproved before execution")
        _mode(segment.prior_mode, uid + ".segment.prior_mode")
        _mode(segment.mode, uid + ".segment.mode")
        _require(segment.prior_mode == mode, uid + ": segment prior mode differs from observed/previous mode")
        changed = segment.mode != mode
        _require(not (changed and mode != "off" and segment.mode != "off"),
                 uid + ": impossible direct pump/generate transition")
        expected_events = int(changed and mode != "off") + int(changed and segment.mode != "off")
        events = _integer(segment.start_stop_events, uid + ".segment.start_stop_events")
        _require(events == expected_events, uid + ": segment start/stop events disagree with mode transition")
        if changed:
            _require(age >= limits["min_" + mode + "_h"] - 1e-9,
                     uid + ": projected transition violates minimum mode dwell")
        _require(remaining >= events, uid + ": mode projection exceeds remaining start/stop event budget")
        if changed and segment.mode != "off":
            _text(segment.preparation_id, uid + ".segment.preparation_id")
            _require(segment.preparation_id in preparations, uid + ": missing historical start readiness")
            record = preparations[segment.preparation_id]
            _require(record.unit_id == uid and record.plan_id == segment.plan_id
                     and record.target_mode == segment.mode
                     and _time(record.target_effective_time, uid) == start
                     and record.preparation_status == "ready",
                     uid + ": historical start readiness must match exact unit/plan/mode/time and be ready")
            # Do not let a newly published record authorize an older issued
            # start. This is explicit research causality, not an official
            # preparation duration inferred from the delivery interval.
            _require(_time(record.ready_at, uid) <= issued
                     and _time(record.published_at, uid) <= issued
                     and _time(record.approved_at, uid) <= issued,
                     uid + ": historical readiness must be known and approved at original plan issuance")
            _require(record.preparation_id not in consumed, uid + ": duplicate historical start readiness")
            consumed.append(record.preparation_id)
        else:
            _require(segment.preparation_id is None, uid + ": non-start segment cannot consume start readiness")
        values = {field: _number(getattr(segment, field), uid + ".segment." + field) for field in (
            "generation_mw", "pumping_mw", "generation_mwh", "pumping_mwh")}
        power, pumping = values["generation_mw"], values["pumping_mw"]
        if segment.mode == "off":
            _require(power == pumping == 0., uid + ": off segment requires zero power")
        elif segment.mode == "pump":
            _require(power == 0. and pumping == limits["pump_power_mw"],
                     uid + ": pump segment requires fixed pumping power and zero generation")
        else:
            _require(pumping == 0. and limits["generation_min_mw"] <= power <= limits["generation_max_mw"],
                     uid + ": generate segment violates frozen generation power limits or pumping exclusion")
        duration = (end-start).total_seconds()/3600
        for label, mw in (("generation", power), ("pumping", pumping)):
            expected_energy = mw * duration
            _require(isfinite(expected_energy) and isclose(values[label + "_mwh"], expected_energy,
                     rel_tol=1e-12, abs_tol=1e-9), uid + ": constant-output segment energy must equal power times duration")
        age = duration if changed else age + duration
        _require(isfinite(age), uid + ": nonfinite projected mode elapsed time")
        mode, remaining, cursor = segment.mode, remaining-events, end
        used.add(segment.plan_id)
    _require(cursor == initial, uid + ": incomplete mode projection coverage")
    return mode, age, power, remaining, tuple(consumed), used


def validate_pumped_plan_boundary(
    data: PumpedPlanBoundaryInput, *, expected_inventory: tuple[PumpedUnitIdentity, ...]
) -> ValidatedPumpedPlanBoundary:
    """Check external assertions without authenticating, solving or changing state.

    expected_inventory must come from the caller's physical resource inventory.
    Plan eligibility is deliberately not returned as a general start permission.
    Explicit preparation records support only externally asserted readiness.
    Initial planned mode never rewrites physical mode or gives start permission.
    A separate explicit issued path can project state without claiming telemetry.
    """
    _require(isinstance(data, PumpedPlanBoundaryInput), "PumpedPlanBoundaryInput required")
    expected = _inventory(expected_inventory, "expected_inventory")
    inventory = _inventory(data.inventory, "inventory")
    _require(inventory == expected, "inventory: exact expected unit/plant mapping required")
    cutoff = _time(data.information_cutoff, "information_cutoff")
    initial = _time(data.model_initial_time, "model_initial_time")
    first = _time(data.first_calculation_time, "first_calculation_time")
    _require(cutoff <= first and initial <= first, "cutoff and initial boundary cannot exceed first calculation")
    _text(data.time_mapping_source, "time_mapping_source")
    _require(type(data.intraday_state_market_enabled) is bool, "explicit intraday enabled Boolean required")

    actual = _records(data.observations, ObservedPumpedState, "unit_id", "observations")
    planned = _records(data.initial_plans, InitialPlannedPumpedState, "unit_id", "initial_plans")
    plans = _records(data.plans, ExternalPumpedPlan, "plan_id", "plans")
    reservoirs = _records(data.reservoirs, ReservoirProjection, "plant_id", "reservoirs")
    preparations = _records(data.preparations, ApprovedPumpedPreparation, "preparation_id", "preparations")
    mode_projections = _records(data.mode_projections, PumpedModeProjection, "unit_id", "mode_projections")
    _require(set(mode_projections) <= set(inventory), "mode projection unit outside inventory")
    projection_ids = set()
    for path in mode_projections.values():
        _text(path.projection_id, "mode_projection.projection_id")
        _require(path.projection_id not in projection_ids, "duplicate mode projection ID")
        projection_ids.add(path.projection_id)
    _exact(actual, inventory, "observations")
    _exact(planned, inventory, "initial_plans")
    _exact(reservoirs, set(inventory.values()), "reservoirs")

    plan_intervals = {}
    for pid, plan in plans.items():
        _require(isinstance(plan.unit_ids, tuple) and bool(plan.unit_ids), pid + ": unit_ids tuple required")
        for uid in plan.unit_ids:
            _text(uid, pid + ".unit_ids")
        _require(len(set(plan.unit_ids)) == len(plan.unit_ids) and set(plan.unit_ids) <= set(inventory),
                 pid + ": duplicate or out-of-scope plan units")
        _require(plan.kind in ("day_ahead", "intraday", "real_time_dispatch", "dispatch_adjustment"),
                 pid + ": unsupported plan kind")
        _require(plan.provenance in ("external_asserted", "synthetic"), pid + ": explicit provenance required")
        _require(plan.kind != "intraday" or data.intraday_state_market_enabled,
                 pid + ": intraday source requires explicitly enabled state market")
        for field in ("source", "approval_id", "approval_source"):
            _text(getattr(plan, field), pid + "." + field)
        _require(plan.approval_status == "approved", pid + ": explicitly approved plan required")
        issued = _time(plan.issued_at, pid + ".issued_at")
        published = _time(plan.published_at, pid + ".published_at")
        approved = _time(plan.approved_at, pid + ".approved_at")
        _require(issued <= published <= cutoff and issued <= approved <= cutoff,
                 pid + ": future or inconsistent issuance/publication/approval at cutoff")
        start = _time(plan.effective_from, pid + ".effective_from")
        end = _time(plan.effective_until, pid + ".effective_until")
        _require(start < end, pid + ": nonempty effective interval required")
        plan_intervals[pid] = (start, end)

    used_plans = set()
    start_targets = {}
    for rid, record in preparations.items():
        _text(record.unit_id, rid + ".unit_id")
        _text(record.plan_id, rid + ".plan_id")
        _require(record.unit_id in inventory and record.plan_id in plans,
                 rid + ": preparation unit/plan outside inventory")
        _require(record.target_mode in ("pump", "generate"), rid + ": on target_mode required")
        _require(record.provenance in ("external_asserted", "synthetic"), rid + ": explicit provenance required")
        for field in ("source", "approval_id", "approval_source", "ready_source"):
            _text(getattr(record, field), rid + "." + field)
        _require(record.approval_status == "approved", rid + ": explicitly approved preparation required")
        _require(record.preparation_status in ("ready", "not_ready"), rid + ": ready/not_ready status required")
        issued = _time(record.issued_at, rid + ".issued_at")
        published = _time(record.published_at, rid + ".published_at")
        approved = _time(record.approved_at, rid + ".approved_at")
        target = _time(record.target_effective_time, rid + ".target_effective_time")
        valid_until = _time(record.valid_until, rid + ".valid_until")
        _require(issued <= published <= cutoff and issued <= approved <= cutoff,
                 rid + ": future or inconsistent preparation issuance/publication/approval at cutoff")
        start, end = plan_intervals[record.plan_id]
        _require(record.unit_id in plans[record.plan_id].unit_ids and start <= target < end,
                 rid + ": preparation target outside plan unit/time scope")
        _require(target < valid_until, rid + ": preparation target is past or outside validity")
        if record.preparation_status == "ready":
            ready = _time(record.ready_at, rid + ".ready_at")
            _require(issued <= ready <= published and ready <= target,
                     rid + ": readiness must be completed and published by cutoff, not future predicted ready")
        else:
            _require(record.ready_at is None, rid + ": not_ready must not claim a completed ready_at")
        key = (record.unit_id, target)
        _require(key not in start_targets, rid + ": duplicate unit/start target")
        start_targets[key] = record
        used_plans.add(record.plan_id)

    unit_boundaries = []
    consumed_projection_preparations = []
    for uid in inventory:
        observation, point = actual[uid], planned[uid]
        _mode(observation.mode, uid + ".actual_mode")
        _text(observation.source, uid + ".observation.source")
        _text(observation.mode_validity_source, uid + ".mode_validity_source")
        _require(observation.quality == "usable", uid + ": usable observation quality required")
        observed = _time(observation.observed_at, uid + ".observed_at")
        available = _time(observation.available_at, uid + ".available_at")
        valid_until = _time(observation.mode_valid_until, uid + ".mode_valid_until")
        _require(observed <= available <= cutoff, uid + ": future or inconsistent observation availability")
        path = mode_projections.get(uid)
        if path is None:
            _require(observed <= initial < valid_until,
                     uid + ": observation validity must cover initial boundary; stale or future observation")
        else:
            _require(observed < initial and observed < valid_until,
                     uid + ": mode projection needs a usable observation strictly before initial boundary")
        elapsed = _number(observation.mode_elapsed_h, uid + ".mode_elapsed_h")
        projected_elapsed = elapsed + (initial - observed).total_seconds() / 3600
        _require(isfinite(projected_elapsed), uid + ": nonfinite projected mode elapsed time")
        physical_mode, power, remaining, consumed = observation.mode, None, None, ()
        if path is not None:
            physical_mode, projected_elapsed, power, remaining, consumed, path_plans = _project_mode(
                path, observation, initial, cutoff, plans, plan_intervals, preparations)
            used_plans.update(path_plans)
            consumed_projection_preparations.extend(consumed)

        _mode(point.mode, uid + ".initial_planned_mode")
        _text(point.plan_id, uid + ".plan_id")
        _text(point.point_mapping_source, uid + ".point_mapping_source")
        _text(point.executability_source, uid + ".executability_source")
        _require(_time(point.effective_at, uid + ".effective_at") == initial,
                 uid + ": initial plan point must exactly match explicit model initial boundary")
        _require(point.plan_id in plans, uid + ": missing referenced initial plan")
        plan = plans[point.plan_id]
        start, end = plan_intervals[point.plan_id]
        _require(uid in plan.unit_ids and start <= initial < end,
                 uid + ": initial plan expired or outside unit/time scope")
        differs = observation.mode != point.mode
        preparation = start_targets.get((uid, first))
        prepared_off_deviation = (
            observation.mode == "off" and point.mode in ("pump", "generate")
            and initial == first and preparation is not None
            and preparation.preparation_status == "ready"
            and preparation.target_mode == point.mode and preparation.plan_id == point.plan_id)
        if observed == initial and differs and not prepared_off_deviation:
            raise UnsupportedPumpedBoundaryError(
                uid + ": same-instant actual/plan deviation requires an updated external boundary "
                "or an explicitly ready matching off-to-on start target; this is a support limit, not an official prohibition")
        used_plans.add(point.plan_id)
        unit_boundaries.append(PumpedUnitBoundary(
            uid, observation, point, physical_mode, projected_elapsed, point.mode == "off", differs,
            path, power, remaining, consumed))

    for rid, record in preparations.items():
        _require(_time(record.target_effective_time, rid) >= initial or rid in consumed_projection_preparations,
                 rid + ": preparation target is past or outside a validated mode projection")

    for pid, projection in reservoirs.items():
        for field in ("measurement_source", "projection_id", "projection_source"):
            _text(getattr(projection, field), pid + "." + field)
        volumes = {field: _number(getattr(projection, field), pid + "." + field) for field in (
            "measured_upper_m3", "measured_lower_m3", "projected_upper_m3", "projected_lower_m3")}
        measured = _time(projection.measured_at, pid + ".measured_at")
        available = _time(projection.available_at, pid + ".available_at")
        projected = _time(projection.projected_at, pid + ".projected_at")
        _require(measured <= available <= cutoff and measured <= initial,
                 pid + ": future or inconsistent reservoir measurement availability")
        _require(projected == initial, pid + ": reservoir projection must end at model initial boundary")
        _require(isinstance(projection.plan_segments, tuple), pid + ": immutable plan_segments tuple required")
        plant_units = {uid for uid, owner in inventory.items() if owner == pid}
        cursor = measured
        for segment in projection.plan_segments:
            _require(isinstance(segment, ReservoirPlanSegment), pid + ": typed reservoir segment required")
            _text(segment.plan_id, pid + ".segment.plan_id")
            _require(segment.plan_id in plans, pid + ": missing referenced reservoir plan")
            start = _time(segment.start, pid + ".segment.start")
            end = _time(segment.end, pid + ".segment.end")
            _require(start == cursor and start < end <= initial,
                     pid + ": ordered continuous projection coverage required, without gaps or overlaps")
            plan = plans[segment.plan_id]
            valid_start, valid_end = plan_intervals[segment.plan_id]
            _require(plan.kind in ("real_time_dispatch", "dispatch_adjustment"),
                     pid + ": reservoir projection requires an existing RT or dispatch-adjustment plan")
            _require(plant_units <= set(plan.unit_ids) and valid_start <= start and end <= valid_end,
                     pid + ": reservoir plan outside plant/time scope")
            used_plans.add(segment.plan_id)
            cursor = end
        _require(cursor == initial, pid + ": incomplete reservoir projection coverage")
        if measured == initial:
            _require(volumes["measured_upper_m3"] == volumes["projected_upper_m3"]
                     and volumes["measured_lower_m3"] == volumes["projected_lower_m3"],
                     pid + ": zero-length projection cannot change measured volumes")

    _exact(plans, used_plans, "referenced plans")
    audit = PumpedBoundaryAudit(
        RULE_SOURCES, SOURCE_LIMITATIONS,
        tuple((uid, (cutoff - _time(actual[uid].observed_at, uid)).total_seconds() / 3600) for uid in inventory),
        tuple((pid, (cutoff - _time(plan.issued_at, pid)).total_seconds() / 3600) for pid, plan in plans.items()),
        tuple(row.unit_id for row in unit_boundaries if row.actual_plan_modes_differ),
        tuple(pid for pid, plan in plans.items() if plan.provenance == "synthetic"),
        tuple(preparations),
        tuple(rid for rid, record in preparations.items() if record.provenance == "synthetic"),
        mode_projection_ids=tuple(path.projection_id for path in mode_projections.values()),
        mode_projected_unit_ids=tuple(mode_projections),
        consumed_projection_preparation_ids=tuple(consumed_projection_preparations),
        mode_projection_arithmetic_verified=bool(mode_projections),
    )
    return ValidatedPumpedPlanBoundary(data, tuple(unit_boundaries), audit)
