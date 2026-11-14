"""Guangdong spinning-reserve gross-compensation ledger, not a market/solver.

2025 Southern ancillary-service rules §§22, 33–36 and table 1. The
operator's accepted quantity, physical capability, and money are separate.
Unverified settlement branches remain None, never implicit zero. Even a fully
calculated gross ledger is not a final settled bill: deductions and individual
cost allocations are outside this module. See ../docs/05_广东旋转备用独立账_证据与边界.md
in the rebuild's parent directory for the evidence and explicit limitations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import math
from numbers import Real
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

RULE_SOURCE = "https://nfj.nea.gov.cn/xwzx/tzgg/202502/P020250214585645069049.pdf"
REFERENCE_R6_GUANGDONG_2025_YUAN_PER_MW_HOUR = 30.0
REFERENCE_K4_GUANGDONG_2025 = 0.5
OPERATING_TIMEZONE = ZoneInfo("Asia/Shanghai")
ESTIMATE_POLICY = "cap_at_requirement_no_redistribution"
SUPPORTED_KINDS = frozenset({"coal", "gas", "pumped_storage", "battery", "wind", "solar"})
PAYABLE_KINDS = frozenset({"coal", "gas"})


@dataclass(frozen=True)
class ReserveResource:
    resource_id: str
    kind: str
    rated_power_mw: float
    eligibility_source: str | None = None


@dataclass(frozen=True)
class ReserveObservation:
    resource_id: str
    online: bool
    actual_power_mw: float
    declared_max_power_mw: float
    physical_available_up_mw: float
    physical_capability_source: str
    operator_recognized: bool
    recognition_source: str | None = None
    non_self_derating_evidence: str | None = None


@dataclass(frozen=True)
class ExecutedReserveInterval:
    interval_id: str
    start: datetime
    end: datetime
    system_lower_limit_mw: float
    lower_limit_source: str
    executed: bool
    service_period_source: str
    observations: tuple[ReserveObservation, ...]
    complete_pool_source: str | None = None


@dataclass(frozen=True)
class DailyAssessment:
    resource_id: str
    operating_day: date
    status: Literal["cleared", "cancelled_self_reason", "unknown"]
    evidence_source: str | None = None


@dataclass(frozen=True)
class CompensationRate:
    yuan_per_mw_hour: float
    source: str
    applicable_operating_days: frozenset[date]
    applicability_confirmed: bool = False


@dataclass(frozen=True)
class CostSharing:
    """Aggregate §36 split only, not allocation to individual payers."""

    generation_coefficient: float
    source: str


@dataclass(frozen=True)
class ResourceLedgerRow:
    interval_id: str
    resource_id: str
    operating_day: date
    duration_hours: float
    physical_available_up_mw: float
    operator_service_mw: float
    operator_service_mw_hours: float
    assessment_status: str
    compensation_gross_yuan: float | None
    scenario_estimate_yuan: float | None
    unresolved_reasons: tuple[str, ...]


@dataclass(frozen=True)
class IntervalLedger:
    interval_id: str
    operating_day: date
    duration_hours: float
    system_lower_limit_mw: float
    physical_available_up_mw: float
    operator_service_mw_hours: float
    oversupply_cap_yuan: float | None
    compensation_gross_yuan: float | None
    scenario_estimate_yuan: float | None
    generation_cohort_cost_yuan: float | None
    user_cohort_cost_yuan: float | None
    unresolved_reasons: tuple[str, ...]
    rows: tuple[ResourceLedgerRow, ...]


@dataclass(frozen=True)
class SpinningReserveLedger:
    intervals: tuple[IntervalLedger, ...]
    operator_service_mw_hours: float
    compensation_gross_yuan: float | None
    scenario_estimate_yuan: float | None
    generation_cohort_cost_yuan: float | None
    user_cohort_cost_yuan: float | None
    rate: CompensationRate
    cost_sharing: CostSharing | None
    estimate_policy: str | None
    resources: tuple[ReserveResource, ...]
    input_intervals: tuple[ExecutedReserveInterval, ...]
    daily_assessments: tuple[DailyAssessment, ...]
    is_final_settlement: bool = False
    coverage: str = "gross_only_excludes_deductions_and_individual_cost_allocations"


def _number(value: Real, name: str, *, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name}: finite real number required")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}: numeric overflow") from exc
    if not math.isfinite(result) or (not signed and result < 0):
        raise ValueError(f"{name}: finite {'signed' if signed else 'nonnegative'} number required")
    return result


def _source(value: str | None, name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty source/identifier required")


def _boolean(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name}: explicit Boolean required")


def _sum(values: Sequence[float], name: str) -> float:
    try:
        result = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"{name}: numeric overflow") from exc
    return _number(result, name)


def _known_sum(values: Sequence[float | None], name: str) -> float | None:
    return None if any(v is None for v in values) else _sum(values, name)


def _resource(resource: ReserveResource) -> None:
    _source(resource.resource_id, "resource_id")
    if resource.kind not in SUPPORTED_KINDS:
        raise ValueError("unsupported resource kind; conventional hydro is excluded")
    if _number(resource.rated_power_mw, "rated_power_mw") <= 0:
        raise ValueError("rated_power_mw must be positive")
    _source(resource.eligibility_source, "eligibility_source", optional=True)
    if resource.kind not in PAYABLE_KINDS and resource.eligibility_source is not None:
        raise ValueError("spinning-reserve compensation eligibility not supported for this resource kind")


def recognized_service_mw(
    resource: ReserveResource, observation: ReserveObservation
) -> float:
    """§33 accepted MW, not physical reserve, dispatch, or cash.

    Values must be constant (or correctly segmented) over the executed interval.
    Physical capability must have been validated upstream for its response and
    duration requirements. This function only checks elementary consistency.
    """
    _resource(resource)
    if observation.resource_id != resource.resource_id:
        raise ValueError("observation resource does not match")
    _boolean(observation.online, "online")
    _boolean(observation.operator_recognized, "operator_recognized")
    power = _number(observation.actual_power_mw, "actual_power_mw", signed=True)
    maximum = _number(observation.declared_max_power_mw, "declared_max_power_mw")
    physical = _number(observation.physical_available_up_mw, "physical_available_up_mw")
    rated = float(resource.rated_power_mw)
    if power > rated or power < (-rated if resource.kind in {"battery", "pumped_storage"} else 0):
        raise ValueError("actual power outside rated bounds")
    if maximum > rated:
        raise ValueError("declared maximum exceeds rated power")
    if physical > rated - power:
        raise ValueError("physical capability exceeds rated upward power range")
    _source(observation.physical_capability_source, "physical_capability_source")
    _source(observation.recognition_source, "recognition_source", optional=True)
    _source(observation.non_self_derating_evidence, "non_self_derating_evidence", optional=True)
    if not observation.online and (physical > 0 or power != 0):
        raise ValueError("offline resource cannot have online power or spinning capability")
    if observation.non_self_derating_evidence is not None:
        if resource.kind not in PAYABLE_KINDS or maximum >= rated:
            raise ValueError("non-self derating exception requires a derated eligible thermal resource")
    if not observation.operator_recognized:
        return 0.0
    if not observation.online:
        raise ValueError("recognized spinning service requires an online resource")
    if resource.kind not in PAYABLE_KINDS:
        raise ValueError("spinning-reserve compensation is excluded or unverified for this resource kind")
    _source(resource.eligibility_source, "eligibility_source")
    _source(observation.recognition_source, "recognition_source")
    effective = rated if observation.non_self_derating_evidence is not None else maximum
    baseline = max(power, 0.5 * rated) if resource.kind == "coal" else power
    return _number(max(0.0, effective - baseline), "operator service MW")


def _interval_time(interval: ExecutedReserveInterval) -> tuple[date, float]:
    _source(interval.interval_id, "interval_id")
    _boolean(interval.executed, "executed")
    if not interval.executed:
        raise ValueError("only executed intervals are payable; lookahead is forbidden")
    for value in (interval.start, interval.end):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("interval timestamps must be timezone-aware")
    start = interval.start.astimezone(OPERATING_TIMEZONE)
    end = interval.end.astimezone(OPERATING_TIMEZONE)
    duration = (interval.end.astimezone(timezone.utc) - interval.start.astimezone(timezone.utc)).total_seconds() / 3600
    if duration <= 0:
        raise ValueError("executed interval duration must be positive")
    day = start.date()
    midnight = datetime.combine(day + timedelta(days=1), time(), tzinfo=OPERATING_TIMEZONE)
    if end > midnight:
        raise ValueError("split intervals at operating-day boundary")
    _number(interval.system_lower_limit_mw, "system_lower_limit_mw")
    _source(interval.lower_limit_source, "lower_limit_source")
    _source(interval.service_period_source, "service_period_source")
    _source(interval.complete_pool_source, "complete_pool_source", optional=True)
    return day, _number(duration, "duration_hours")


def build_spinning_reserve_ledger(
    resources: Sequence[ReserveResource],
    intervals: Sequence[ExecutedReserveInterval],
    daily_assessments: Sequence[DailyAssessment],
    *,
    rate: CompensationRate,
    cost_sharing: CostSharing | None = None,
    estimate_policy: str | None = None,
) -> SpinningReserveLedger:
    """Build a separate audited gross ledger without inference from old r_up.

    A confirmed gross calculation needs: a positive oversupply branch, a complete
    pool, cleared daily assessments, and a rate verified for that operating day.
    Zero accepted service and evidenced daily cancellation pay zero. The
    undersupply/equality branch and cancellation denominator are unresolved.

    Optional estimates explicitly adopt min(supply, requirement) and retain the
    pre-cancellation denominator, assuming unknown assessments are cleared.
    They are scenario values, not bounds or settled money. No rounding to cents
    is imposed because a prescribed settlement rounding rule is not established.
    """
    price = _number(rate.yuan_per_mw_hour, "yuan_per_mw_hour")
    _source(rate.source, "rate source")
    _boolean(rate.applicability_confirmed, "applicability_confirmed")
    if any(type(day) is not date for day in rate.applicable_operating_days):
        raise ValueError("rate applicable days must be dates")
    if estimate_policy not in {None, ESTIMATE_POLICY}:
        raise ValueError("unknown estimate_policy")
    if cost_sharing is not None:
        k4 = _number(cost_sharing.generation_coefficient, "generation_coefficient")
        if k4 > 1:
            raise ValueError("generation_coefficient must not exceed 1")
        _source(cost_sharing.source, "cost sharing source")
    registry = {}
    for resource in resources:
        _resource(resource)
        if resource.resource_id in registry:
            raise ValueError("duplicate resource_id")
        registry[resource.resource_id] = resource
    if not registry:
        raise ValueError("resource registry cannot be empty")
    metadata = {}
    for interval in intervals:
        if interval.interval_id in metadata:
            raise ValueError("duplicate interval_id")
        metadata[interval.interval_id] = _interval_time(interval)
    ordered = sorted(intervals, key=lambda row: row.start.astimezone(timezone.utc))
    if not ordered:
        raise ValueError("at least one executed interval is required")
    for previous, following in zip(ordered, ordered[1:]):
        if previous.end.astimezone(timezone.utc) > following.start.astimezone(timezone.utc):
            raise ValueError("overlapping executed intervals are forbidden")
    assessments = {}
    for assessment in daily_assessments:
        if assessment.resource_id not in registry:
            raise ValueError("assessment refers to an unknown resource")
        if type(assessment.operating_day) is not date:
            raise ValueError("assessment operating_day must be a date")
        if assessment.status not in {"cleared", "cancelled_self_reason", "unknown"}:
            raise ValueError("invalid daily assessment status")
        _source(assessment.evidence_source, "assessment evidence", optional=assessment.status == "unknown")
        key = (assessment.resource_id, assessment.operating_day)
        if key in assessments:
            raise ValueError("duplicate resource/day assessment")
        assessments[key] = assessment.status

    results = []
    for interval in ordered:
        day, hours = metadata[interval.interval_id]
        observations = {}
        quantities = {}
        services_mw = {}
        for observation in interval.observations:
            rid = observation.resource_id
            if rid in observations:
                raise ValueError("duplicate resource observation in interval")
            if rid not in registry:
                raise ValueError("observation refers to an unknown resource")
            services_mw[rid] = recognized_service_mw(registry[rid], observation)
            quantities[rid] = _number(services_mw[rid] * hours, "service MW hours")
            observations[rid] = observation
        if set(observations) != set(registry):
            raise ValueError("each interval must list every registered resource explicitly")
        total_service = _sum(list(quantities.values()), "total service MW hours")
        total_physical = _sum([float(o.physical_available_up_mw) for o in observations.values()], "physical MW total")
        required_quantity = _number(float(interval.system_lower_limit_mw) * hours, "lower limit MW hours")
        oversupply = total_service > required_quantity
        cap = _number(required_quantity * price, "oversupply cap") if oversupply else None
        positive = [rid for rid, quantity in quantities.items() if quantity > 0]
        reasons = []
        if positive:
            if not oversupply:
                reasons.append("supply_at_or_below_lower_limit_rule_unverified")
            if interval.complete_pool_source is None:
                reasons.append("complete_compensation_pool_evidence_missing")
            if not rate.applicability_confirmed or day not in rate.applicable_operating_days:
                reasons.append("rate_applicability_not_confirmed_for_operating_day")
            if any(assessments.get((rid, day), "unknown") == "unknown" for rid in positive):
                reasons.append("daily_assessment_evidence_missing")
            if any(assessments.get((rid, day)) == "cancelled_self_reason" for rid in positive):
                reasons.append("cancellation_denominator_and_redistribution_rule_unverified")
        estimate_pool = None
        if estimate_policy is not None:
            estimate_pool = _number(min(total_service, required_quantity) * price, "scenario estimate pool")
        rows = []
        for rid in sorted(observations):
            quantity = quantities[rid]
            status = assessments.get((rid, day), "unknown")
            row_reasons = tuple(reasons) if quantity > 0 else ()
            if quantity == 0 or status == "cancelled_self_reason":
                gross = 0.0
            elif reasons:
                gross = None
            else:
                gross = _number(cap * (quantity / total_service), "resource compensation")
            estimate = None
            if estimate_pool is not None:
                estimate = 0.0 if quantity == 0 or status == "cancelled_self_reason" else _number(
                    estimate_pool * (quantity / total_service), "resource scenario estimate"
                )
            rows.append(ResourceLedgerRow(
                interval.interval_id, rid, day, hours,
                float(observations[rid].physical_available_up_mw), services_mw[rid], quantity,
                status, gross, estimate, row_reasons,
            ))
        gross = _known_sum([row.compensation_gross_yuan for row in rows], "interval compensation")
        estimate = _known_sum([row.scenario_estimate_yuan for row in rows], "interval estimate")
        generation_cost = user_cost = None
        # An all-cancelled cohort has zero recipient cash, but the disposition
        # of the original cap is unresolved. Do not make payer costs zero by
        # silently assuming the cancelled share was removed from the pool.
        if cost_sharing is not None and gross is not None and not reasons:
            generation_cost = _number(gross * k4, "generation cohort cost")
            user_cost = _number(gross - generation_cost, "user cohort cost")
        results.append(IntervalLedger(
            interval.interval_id, day, hours, float(interval.system_lower_limit_mw),
            total_physical, total_service, cap, gross, estimate,
            generation_cost, user_cost, tuple(reasons), tuple(rows),
        ))
    return SpinningReserveLedger(
        tuple(results),
        _sum([row.operator_service_mw_hours for row in results], "ledger service quantity"),
        _known_sum([row.compensation_gross_yuan for row in results], "ledger compensation"),
        _known_sum([row.scenario_estimate_yuan for row in results], "ledger estimate"),
        _known_sum([row.generation_cohort_cost_yuan for row in results], "ledger generation cost"),
        _known_sum([row.user_cohort_cost_yuan for row in results], "ledger user cost"),
        replace(rate, applicable_operating_days=frozenset(rate.applicable_operating_days)),
        cost_sharing, estimate_policy, tuple(resources),
        tuple(replace(item, observations=tuple(item.observations)) for item in ordered),
        tuple(daily_assessments),
    )
