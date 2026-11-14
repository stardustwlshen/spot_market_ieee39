"""Externally supplied thermal AGC reservations, capability audit and gross ledger.

No auction, capability certification, AGC simulation, quality deduction, net
settlement or solver is implemented here. Source kinds are caller declarations,
not authentication. See rebuild docs/22_非零调频衔接与履约边界.md.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import fsum, isfinite
from numbers import Real
from zoneinfo import ZoneInfo

from .gd_regulation_math import PRICE_CAP_YUAN_PER_MW, mileage_payment
from .gd_service_boundaries import (
    AdjustedEnvelope, PhysicalEnvelope, RegulationCapacityResult, RegulationLookaheadSlot,
    apply_regulation_boundaries,
)

SHANGHAI = ZoneInfo('Asia/Shanghai')
EXTERNAL_SCOPE = 'southern_unified_control_area'
SYNTHETIC_SCOPE = 'synthetic_research_only'
THERMAL_KINDS = frozenset({'coal', 'gas'})
RESOURCE_KINDS = THERMAL_KINDS | {'battery', 'pumped_storage', 'wind', 'solar'}
SERVICE = 'agc_mileage'


@dataclass(frozen=True)
class AGCProvenance:
    kind: str  # external_verified means declared by caller, never verified here.
    source: str


@dataclass(frozen=True)
class SourcedAGCValue:
    value: float
    provenance: AGCProvenance
    published_at: datetime
    scope: str


@dataclass(frozen=True)
class TypedAGCResult:
    capacity_result: RegulationCapacityResult
    provenance: AGCProvenance
    uniform_price_yuan_per_mw: SourcedAGCValue | None = None
    control_area_k_max: SourcedAGCValue | None = None


@dataclass(frozen=True)
class ResourceAGCQualification:
    resource_id: str
    kind: str
    market_qualified: bool
    known_at: datetime
    valid_from: datetime
    valid_until: datetime
    provenance: AGCProvenance


@dataclass(frozen=True)
class AGCTrackingCapability:
    resource_id: str
    up_mw: float
    down_mw: float
    known_at: datetime
    valid_from: datetime
    valid_until: datetime
    provenance: AGCProvenance
    response_capability_source: str


@dataclass(frozen=True)
class PreparedAGCWindow:
    energy_stage: str
    intervals: dict[int, tuple[datetime, datetime]]
    information_cutoff: datetime
    envelope: AdjustedEnvelope
    capacity_results: tuple[RegulationCapacityResult, ...]
    result_provenance: dict[str, dict[str, str]]
    typed_results: tuple[TypedAGCResult, ...]
    qualifications: tuple[ResourceAGCQualification, ...]
    audit: dict


@dataclass(frozen=True)
class PumpedReserveBudget:
    """Externally audited aggregate capacities, not inferred from AGC capacity.

    DA uses primary, max_generation, max_pumping; RT uses primary, emergency.
    Irrelevant fields must be None, not an accidental second capacity mapping.
    """
    primary_mw: float
    emergency_mw: float | None
    max_generation_mw: float | None
    max_pumping_mw: float | None
    source: str


@dataclass(frozen=True)
class AGCTraceSample:
    resource_id: str
    at: datetime
    baseline_mw: float
    command_mw: float
    actual_mw: float
    available_at: datetime
    provenance: AGCProvenance


@dataclass(frozen=True)
class AGCHourDelivery:
    resource_id: str
    result_id: str
    hour_start: datetime
    hour_end: datetime
    recorded_at: datetime
    executed: bool
    actual_mileage_mw: float | None
    settlement_performance_m: float | None
    provenance: AGCProvenance
    instruction_source: str | None
    mileage_source: str | None
    performance_source: str | None
    calling_status: str  # awarded | temporary_operator_call
    calling_source: str
    designated_performance_test: bool
    quality_assessment_source: str | None = None
    mileage_published_at: datetime | None = None
    performance_published_at: datetime | None = None


@dataclass(frozen=True)
class AGCMileageRow:
    booking_key: tuple[str, str, datetime]
    result_id: str
    reservation_mw: float
    actual_mileage_mw: float | None
    uniform_price_yuan_per_mw: float | None
    settlement_performance_m: float | None
    gross_compensation_yuan: float | None
    research_gross_estimate_yuan: float | None
    net_compensation_yuan: None
    gross_missing_evidence: tuple[str, ...]
    net_unresolved_reasons: tuple[str, ...]
    audit: dict


@dataclass(frozen=True)
class AGCMileageLedger:
    rows: tuple[AGCMileageRow, ...]
    gross_compensation_yuan: float | None
    research_gross_estimate_yuan: float | None
    net_compensation_yuan: None
    new_booking_keys: frozenset[tuple[str, str, datetime]]
    audit: dict


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ': nonempty source/identifier required')


def _optional_text(value, name):
    if value is not None:
        _text(value, name)


def _number(value, name, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(name + ': finite numeric value required')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(name + ': finite numeric value required') from exc
    if not isfinite(value) or (not signed and value < 0):
        raise ValueError(name + ': finite ' + ('signed' if signed else 'nonnegative') + ' value required')
    return value


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + ': timezone-aware datetime required')
    return value


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + ': explicit Boolean required')


def _provenance(value):
    if not isinstance(value, AGCProvenance) or value.kind not in {'external_verified', 'synthetic'}:
        raise ValueError('typed external_verified/synthetic provenance required')
    _text(value.source, 'provenance source')


def _validity(record, name):
    for field in ('known_at', 'valid_from', 'valid_until'):
        _aware(getattr(record, field), name + '.' + field)
    if record.valid_from >= record.valid_until:
        raise ValueError(name + ': positive validity interval required')
    _provenance(record.provenance)


def _scalar(value, cutoff, name):
    if not isinstance(value, SourcedAGCValue):
        raise ValueError(name + ': typed sourced value required')
    _number(value.value, name)
    _provenance(value.provenance)
    _aware(value.published_at, name + ' publication')
    if value.published_at > cutoff:
        raise ValueError(name + ': future publication is unavailable')
    scope = EXTERNAL_SCOPE if value.provenance.kind == 'external_verified' else SYNTHETIC_SCOPE
    if value.scope != scope:
        raise ValueError(name + ': full control-area or explicit synthetic scope required')


def _results(records, *, cutoff):
    if not isinstance(records, tuple) or not records:
        raise ValueError('explicit typed AGC results tuple required')
    output = {}
    for record in records:
        if not isinstance(record, TypedAGCResult) or not isinstance(record.capacity_result, RegulationCapacityResult):
            raise ValueError('TypedAGCResult with RegulationCapacityResult required')
        result = record.capacity_result
        _text(result.result_id, 'result_id'); _text(result.source, 'capacity result source')
        _provenance(record.provenance)
        for field in ('published_at', 'binding_start', 'binding_end', 'lookahead_end'):
            _aware(getattr(result, field), 'result.' + field)
        if not result.published_at <= cutoff or not result.published_at <= result.binding_start < result.binding_end <= result.lookahead_end:
            raise ValueError('invalid or unavailable AGC result interval')
        if result.stage not in {'day_ahead_prearrangement', 'intraday_formal'}:
            raise ValueError('unsupported AGC stage')
        if result.stage == 'intraday_formal':
            local = result.binding_start.astimezone(SHANGHAI)
            if local.minute or local.second or local.microsecond or result.binding_end != result.binding_start + timedelta(hours=1):
                raise ValueError('formal AGC must bind one Shanghai-aligned hour')
            if result.lookahead_end != result.binding_start + timedelta(hours=6) or not result.binding_start - timedelta(minutes=45) <= result.published_at <= result.binding_start - timedelta(minutes=30):
                raise ValueError('formal AGC requires T-45 to T-30 publication and six-hour lookahead')
        if not isinstance(result.capacity_mw, dict) or not result.capacity_mw:
            raise ValueError('explicit capacity inventory required')
        for resource, quantity in result.capacity_mw.items():
            _text(resource, 'resource_id'); _number(quantity, resource + ' capacity')
        if not isinstance(result.lookahead_slots, tuple):
            raise ValueError('explicit lookahead slots tuple required')
        # apply_regulation_boundaries checks each typed slot and its timing for
        # preparation; validate here too because a ledger can be built alone.
        last_end = result.binding_end
        for slot in result.lookahead_slots:
            if not isinstance(slot, RegulationLookaheadSlot):
                raise ValueError('typed lookahead slot required')
            _aware(slot.start, 'lookahead start'); _aware(slot.end, 'lookahead end')
            if not last_end <= slot.start < slot.end <= result.lookahead_end:
                raise ValueError('overlapping or invalid lookahead interval')
            if not isinstance(slot.capacity_mw, dict) or set(slot.capacity_mw) != set(result.capacity_mw):
                raise ValueError('lookahead capacity inventory must match binding inventory')
            for resource, quantity in slot.capacity_mw.items():
                _number(quantity, resource + ' lookahead capacity')
            last_end = slot.end
        if record.uniform_price_yuan_per_mw is not None:
            if result.stage != 'intraday_formal':
                raise ValueError('DA prearrangement is not a payable formal price')
            _scalar(record.uniform_price_yuan_per_mw, cutoff, 'uniform AGC price')
            if record.uniform_price_yuan_per_mw.value > PRICE_CAP_YUAN_PER_MW:
                raise ValueError('uniform AGC price exceeds 15 yuan/MW')
        if record.control_area_k_max is not None:
            _scalar(record.control_area_k_max, cutoff, 'control-area k_max')
            if record.control_area_k_max.value <= 0:
                raise ValueError('control-area k_max must be positive')
        if result.result_id in output:
            raise ValueError('duplicate result_id; select the effective version explicitly')
        output[result.result_id] = record
    return output


def prepare_agc_window(*, physical, energy_stage, intervals, information_cutoff,
                       results, qualifications, tracking_capabilities,
                       allow_nonbinding_lookahead=False):
    """Validate declarations and apply thermal symmetric reservation once.

    Input physical contains only raw thermal envelopes. The qualifications and
    every capacity result cover the full inventory, including nonthermal zeros.
    Output capacity_results can be passed to workflow ServiceInputs; never use
    the adjusted envelope as that workflow's original physical input.
    """
    physical, intervals, results, qualifications, tracking_capabilities = deepcopy(
        (physical, intervals, results, qualifications, tracking_capabilities))
    cutoff = _aware(information_cutoff, 'information cutoff')
    _results(results, cutoff=cutoff)
    if not isinstance(qualifications, tuple) or not qualifications:
        raise ValueError('full resource qualification tuple required')
    inventory = {}
    for entry in qualifications:
        if not isinstance(entry, ResourceAGCQualification):
            raise ValueError('typed resource qualification required')
        _text(entry.resource_id, 'qualified resource_id')
        if entry.kind not in RESOURCE_KINDS:
            raise ValueError('unsupported resource kind; conventional hydro excluded')
        _boolean(entry.market_qualified, 'market_qualified'); _validity(entry, entry.resource_id)
        if entry.resource_id in inventory:
            raise ValueError('duplicate resource qualification')
        inventory[entry.resource_id] = entry
    thermal = {key for key, value in inventory.items() if value.kind in THERMAL_KINDS}
    if type(physical) is not PhysicalEnvelope:
        raise ValueError('raw PhysicalEnvelope required; cannot deduct an adjusted envelope twice')
    if not isinstance(physical.lower_mw, dict) or {key[0] for key in physical.lower_mw if isinstance(key, tuple) and len(key) == 2} != thermal:
        raise ValueError('raw physical envelope must cover exactly the qualified thermal inventory')
    if not isinstance(tracking_capabilities, tuple):
        raise ValueError('explicit tracking capabilities tuple required')
    for capability in tracking_capabilities:
        if not isinstance(capability, AGCTrackingCapability) or capability.resource_id not in inventory:
            raise ValueError('typed tracking capability for a declared resource required')
        _validity(capability, capability.resource_id + '.tracking')
        _number(capability.up_mw, 'up tracking capability'); _number(capability.down_mw, 'down tracking capability')
        _text(capability.response_capability_source, 'response capability source')
    projected = []
    for wrapped in results:
        result = wrapped.capacity_result
        for capacities in (result.capacity_mw, *(slot.capacity_mw for slot in result.lookahead_slots)):
            if set(capacities) != set(inventory):
                raise ValueError('full AGC inventory required, including explicit nonthermal zeros')
            if any(capacities[key] != 0 for key in set(inventory) - thermal):
                raise ValueError('nonzero storage/pumped/renewable AGC mapping unsupported')
        projected.append(replace(result, capacity_mw={g: result.capacity_mw[g] for g in thermal},
            lookahead_slots=tuple(replace(slot, capacity_mw={g: slot.capacity_mw[g] for g in thermal}) for slot in result.lookahead_slots)))
    envelope = apply_regulation_boundaries(physical, energy_stage=energy_stage, intervals=intervals,
        information_cutoff=cutoff, results=tuple(projected), allow_nonbinding_lookahead=allow_nonbinding_lookahead)
    for key in envelope.reserved_mw:
        if envelope.physical_lower_mw[key] < 0:
            raise ValueError('thermal physical lower capacity must be nonnegative')
        if not physical.online[key] and (envelope.physical_lower_mw[key] != 0 or envelope.physical_upper_mw[key] != 0):
            raise ValueError('offline thermal envelope must have explicit zero available bounds')
    checked = []
    synthetic = any(entry.provenance.kind == 'synthetic' or any(
        value is not None and value.provenance.kind == 'synthetic'
        for value in (entry.uniform_price_yuan_per_mw, entry.control_area_k_max)) for entry in results)
    for (resource, period), quantity in sorted(envelope.reserved_mw.items()):
        if quantity == 0:
            continue
        start, end = intervals[period]
        eligibility = inventory[resource]
        if not eligibility.market_qualified or eligibility.known_at > cutoff or not eligibility.valid_from <= start < end <= eligibility.valid_until:
            raise ValueError(f'{resource}/{period}: nonzero reservation lacks an available valid market qualification')
        candidates = [entry for entry in tracking_capabilities if entry.resource_id == resource
                      and entry.valid_from < end and entry.valid_until > start]
        if len(candidates) != 1:
            raise ValueError(f'{resource}/{period}: missing or overlapping tracking capability declarations')
        capability = candidates[0]
        if capability.known_at > cutoff or not capability.valid_from <= start < end <= capability.valid_until:
            raise ValueError(f'{resource}/{period}: tracking capability is unavailable or does not cover the interval')
        if quantity > min(capability.up_mw, capability.down_mw):
            raise ValueError(f'{resource}/{period}: symmetric AGC exceeds declared up/down tracking capability')
        synthetic |= any(entry.provenance.kind == 'synthetic' for entry in (eligibility, capability))
        checked.append(dict(resource_id=resource, period=period, reserved_mw=quantity,
                            qualification_source=eligibility.provenance.source,
                            tracking_source=capability.provenance.source,
                            response_capability_source=capability.response_capability_source))
    return PreparedAGCWindow(energy_stage, intervals, cutoff, envelope,
        tuple(deepcopy(entry.capacity_result) for entry in results),
        {entry.capacity_result.result_id: {'kind': entry.provenance.kind, 'source': entry.provenance.source} for entry in results},
        results, qualifications, dict(
            boundary_adjustments_applied=1, contains_synthetic_input=synthetic,
            nonzero_thermal_checks=checked, source_authenticity_verified=False,
            market_qualification_certified=False, tracking_performance_measured=False,
            full_control_area_k_max_recomputed=False, uniform_price_recomputed=False,
            nonthermal_nonzero_supported=False, capacity_payment_created=False,
            actual_delivery_verified=False, formal_net_settlement_computed=False,
            required_delivery_evidence=('executed_hour', 'AGC_instructions', 'accepted_mileage_MW',
                'period_settlement_m', 'uniform_price', 'quality_deductions_and_assessments')))


def _series(mapping, keys, name, *, signed=False):
    if not isinstance(mapping, dict) or set(mapping) != set(keys):
        raise ValueError(name + ': exact key coverage required')
    return {key: _number(mapping[key], name, signed=signed) for key in keys}


def _window(window):
    if not isinstance(window, PreparedAGCWindow) or not isinstance(window.envelope, AdjustedEnvelope):
        raise ValueError('PreparedAGCWindow required')
    if window.energy_stage not in {'DA_SCED', 'RT_SCED'}:
        raise ValueError('unsupported energy stage')
    envelope = window.envelope
    keys = set(envelope.reserved_mw)
    for name in ('lower_mw', 'upper_mw', 'physical_lower_mw', 'physical_upper_mw'):
        _series(getattr(envelope, name), keys, name)
    for key in keys:
        q = _number(envelope.reserved_mw[key], 'AGC capacity')
        if envelope.lower_mw[key] != envelope.physical_lower_mw[key] + q or envelope.upper_mw[key] != envelope.physical_upper_mw[key] - q:
            raise ValueError('prepared envelope was changed or AGC deducted more than once')
        if envelope.lower_mw[key] > envelope.upper_mw[key]:
            raise ValueError('prepared AGC envelope is empty')
    if keys != {(resource, period) for resource, _ in keys for period in window.intervals}:
        raise ValueError('prepared window resource/period coverage differs')
    if set(envelope.audit['capacity_is_binding_by_period']) != set(window.intervals):
        raise ValueError('prepared binding coverage differs')
    return envelope


def audit_agc_reserve_budget(window, *, dispatch_mw, capacity_basis, capacity_mapping_source,
                             requirements, pumped_support, ramp_10min_mw=None):
    """Post-dispatch static budget, without a cross-product exclusivity claim.

    requirements matches the gd_dispatch_reserves numeric interface: DA adds
    down_mw and raw system_demand_minus_tie_mw; RT adds a thermal 10-min map.
    Availability maxima are not payments, awards, or measured performance.
    """
    window, dispatch_mw, requirements, pumped_support, ramp_10min_mw = deepcopy(
        (window, dispatch_mw, requirements, pumped_support, ramp_10min_mw))
    envelope = _window(window)
    if capacity_basis not in {'raw_physical', 'after_agc'}:
        raise ValueError('explicit raw_physical/after_agc capacity_basis required')
    _text(capacity_mapping_source, 'capacity_mapping_source')
    keys = set(envelope.reserved_mw); periods = set(window.intervals)
    dispatch = _series(dispatch_mw, keys, 'dispatch', signed=True)
    da = window.energy_stage == 'DA_SCED'
    expected = {'up_mw', 'primary_mw', 'deduction_mw'} | ({'down_mw', 'system_demand_minus_tie_mw'} if da else set())
    if not isinstance(requirements, dict) or set(requirements) != expected:
        raise ValueError('explicit stage reserve requirements required')
    req = {name: _series(value, periods, name, signed=name == 'system_demand_minus_tie_mw') for name, value in requirements.items()}
    if da:
        if ramp_10min_mw is not None:
            raise ValueError('DA budget does not take RT 10-minute capability')
        ramp = None
    else:
        ramp = _series(ramp_10min_mw, keys, 'thermal 10-minute capability')
    if not isinstance(pumped_support, dict) or set(pumped_support) != periods:
        raise ValueError('explicit pumped support per period required, including zeros')
    has_pumped = any(entry.kind == 'pumped_storage' for entry in window.qualifications)
    for value in pumped_support.values():
        if not isinstance(value, PumpedReserveBudget):
            raise ValueError('typed pumped support required')
        _text(value.source, 'pumped support source'); _number(value.primary_mw, 'pumped primary')
        for name in ('max_generation_mw', 'max_pumping_mw') if da else ('emergency_mw',):
            _number(getattr(value, name), 'pumped ' + name)
        for name in ('emergency_mw',) if da else ('max_generation_mw', 'max_pumping_mw'):
            if getattr(value, name) is not None:
                raise ValueError('irrelevant stage pumped capacity must be None')
        if not has_pumped and any(getattr(value, name) not in (0., None) for name in ('primary_mw', 'emergency_mw', 'max_generation_mw', 'max_pumping_mw')):
            raise ValueError('pumped support cannot add absent resources')
    upper = envelope.physical_upper_mw if capacity_basis == 'raw_physical' else envelope.upper_mw
    lower = envelope.physical_lower_mw if capacity_basis == 'raw_physical' else envelope.lower_mw
    resource_rows = []
    for resource, period in sorted(keys):
        key = resource, period; power = dispatch[key]; q = envelope.reserved_mw[key]
        up = envelope.physical_upper_mw[key] - power - q
        down = power - envelope.physical_lower_mw[key] - q
        # Negative power/headroom is recorded as a physical failure; zeroing an
        # unavailable reserve capacity here must not mask that failure below.
        head = max(upper[key] - power, 0.)
        resource_rows.append(dict(resource_id=resource, period=period, dispatch_mw=power,
            reserved_agc_mw=q, agc_up_margin_mw=up, agc_down_margin_mw=down,
            within_agc_envelope=up >= 0 and down >= 0,
            primary_available_mw=min(head, .06 * upper[key]),
            emergency_available_mw=None if da else min(head, ramp[key]),
            capacity_is_binding=bool(envelope.audit['capacity_is_binding_by_period'][period])))
    system_rows = []
    for period in sorted(periods):
        rows = [row for row in resource_rows if row['period'] == period]
        pumped = pumped_support[period]
        primary = fsum(row['primary_available_mw'] for row in rows) + pumped.primary_mw
        row = dict(period=period, primary_available_mw=primary,
                   primary_margin_mw=primary - req['primary_mw'][period])
        if da:
            maximum = fsum(upper[key] for key in keys if key[1] == period) + pumped.max_generation_mw
            minimum = fsum(lower[key] for key in keys if key[1] == period) - pumped.max_pumping_mw
            row.update(operating_up_margin_mw=maximum - req['deduction_mw'][period] - req['system_demand_minus_tie_mw'][period] - req['up_mw'][period],
                operating_down_margin_mw=req['system_demand_minus_tie_mw'][period] - req['down_mw'][period] - minimum)
        else:
            emergency = fsum(r['emergency_available_mw'] for r in rows) + pumped.emergency_mw
            row.update(emergency_available_mw=emergency,
                       emergency_margin_mw=emergency - req['up_mw'][period] - req['deduction_mw'][period])
        row['static_budget_feasible'] = all(value >= 0 for name, value in row.items() if name.endswith('_margin_mw')) and all(r['within_agc_envelope'] for r in rows)
        system_rows.append(row)
    return dict(resource_rows=tuple(resource_rows), system_rows=tuple(system_rows), audit=dict(
        stage=window.energy_stage, capacity_basis=capacity_basis,
        capacity_mapping_source=capacity_mapping_source, capacity_mapping_is_research_interpretation=True,
        pumped_sources={t: pumped_support[t].source for t in periods},
        actual_delivery_verified=False, cross_product_simultaneous_delivery_verified=False,
        cross_product_sum_constraint_added=False, raw_basis_may_share_agc_headroom=capacity_basis == 'raw_physical',
        capacity_payment_created=False, reserve_auction_created=False,
        primary_and_emergency_capacities_are_not_additive=True,
        static_budget_feasible=all(row['static_budget_feasible'] for row in system_rows)))


def audit_agc_trace(window, *, samples, information_cutoff):
    """Check supplied execution samples only; never derive mileage or m.

    No inference of continuous coverage, permitted delay, official tracking
    quality, or command-completion segmentation is made from these samples.
    """
    window, samples = deepcopy((window, samples))
    envelope = _window(window); cutoff = _aware(information_cutoff, 'trace cutoff')
    if not isinstance(samples, tuple) or not samples:
        raise ValueError('nonempty typed trace samples required')
    if window.energy_stage != 'RT_SCED':
        raise ValueError('actual trace requires formal RT capacity, not DA prearrangement')
    seen = set(); rows = []
    for sample in samples:
        if not isinstance(sample, AGCTraceSample):
            raise ValueError('typed AGCTraceSample required')
        _aware(sample.at, 'sample time'); _aware(sample.available_at, 'sample available_at'); _provenance(sample.provenance)
        if not sample.at <= sample.available_at <= cutoff:
            raise ValueError('sample must be observed and available by trace cutoff')
        matches = [period for period, (start, end) in window.intervals.items() if start <= sample.at < end]
        if len(matches) != 1 or (sample.resource_id, matches[0]) not in envelope.reserved_mw:
            raise ValueError('sample is outside the prepared resource/time window')
        period = matches[0]; key = sample.resource_id, period
        if not envelope.audit['capacity_is_binding_by_period'][period]:
            raise ValueError('nonbinding lookahead cannot substantiate actual AGC delivery')
        sample_key = sample.resource_id, sample.at
        if sample_key in seen:
            raise ValueError('duplicate AGC trace sample')
        seen.add(sample_key)
        base, command, actual = (_number(getattr(sample, field), field, signed=True) for field in ('baseline_mw', 'command_mw', 'actual_mw'))
        q = envelope.reserved_mw[key]
        rows.append(dict(resource_id=sample.resource_id, at=sample.at, period=period,
            baseline_within_reserved_envelope=envelope.lower_mw[key] <= base <= envelope.upper_mw[key],
            command_within_reserved_capacity=abs(command - base) <= q,
            command_within_physical_envelope=envelope.physical_lower_mw[key] <= command <= envelope.physical_upper_mw[key],
            actual_within_physical_envelope=envelope.physical_lower_mw[key] <= actual <= envelope.physical_upper_mw[key],
            actual_within_reserved_excursion=abs(actual - base) <= q,
            tracking_error_mw=actual - command, provenance=sample.provenance))
    return dict(rows=tuple(rows), audit=dict(continuous_coverage_verified=False,
        measured_performance_m_computed=False, measured_mileage_computed=False,
        source_authenticity_verified=False, official_tracking_quality_determined=False,
        service_payment_created=False))


def _booking_key(key):
    if not isinstance(key, tuple) or len(key) != 3 or key[0] != SERVICE:
        raise ValueError('booking key must be (agc_mileage, resource_id, hour_start)')
    _text(key[1], 'booking resource'); _aware(key[2], 'booking hour')
    local = key[2].astimezone(SHANGHAI)
    if local.minute or local.second or local.microsecond:
        raise ValueError('booking hour must be Shanghai-hour aligned')


def build_agc_mileage_ledger(*, results, qualifications, deliveries, settlement_cutoff,
                             already_booked_keys=frozenset()):
    """One completed formal hour per resource, gross formula only, no dt/Fm.

    Accepted external metrics need their publication no earlier than hour end
    and no later than settlement_cutoff. All authenticity remains unverified.
    The caller must atomically persist booking keys to prevent cross-run repeats.
    """
    results, qualifications, deliveries, already_booked_keys = deepcopy((results, qualifications, deliveries, already_booked_keys))
    cutoff = _aware(settlement_cutoff, 'settlement cutoff')
    by_id = _results(results, cutoff=cutoff)
    if not isinstance(qualifications, tuple) or not qualifications:
        raise ValueError('explicit resource qualification tuple required')
    inventory = {}
    for entry in qualifications:
        if not isinstance(entry, ResourceAGCQualification) or entry.kind not in RESOURCE_KINDS:
            raise ValueError('typed supported resource qualification required')
        _text(entry.resource_id, 'qualification resource')
        _boolean(entry.market_qualified, 'market_qualified'); _validity(entry, entry.resource_id)
        if entry.resource_id in inventory:
            raise ValueError('duplicate resource qualification')
        inventory[entry.resource_id] = entry
    formal = sorted((entry.capacity_result for entry in results if entry.capacity_result.stage == 'intraday_formal'), key=lambda entry: entry.binding_start)
    if any(left.binding_end > right.binding_start for left, right in zip(formal, formal[1:])):
        raise ValueError('overlapping formal versions; select effective results explicitly')
    for wrapped in results:
        result = wrapped.capacity_result
        for capacities in (result.capacity_mw, *(slot.capacity_mw for slot in result.lookahead_slots)):
            if set(capacities) != set(inventory):
                raise ValueError('result and qualification inventory must match exactly')
            if any(quantity != 0 and inventory[resource].kind not in THERMAL_KINDS for resource, quantity in capacities.items()):
                raise ValueError('nonzero nonthermal AGC delivery unsupported')
    if not isinstance(deliveries, tuple) or not deliveries:
        raise ValueError('nonempty hourly delivery records required')
    if not isinstance(already_booked_keys, frozenset):
        raise ValueError('already_booked_keys must be an explicit frozenset')
    for key in already_booked_keys:
        _booking_key(key)
    seen = set(already_booked_keys); booked = set(); rows = []
    for delivery in deliveries:
        if not isinstance(delivery, AGCHourDelivery):
            raise ValueError('typed hourly delivery required')
        _text(delivery.resource_id, 'delivery resource'); _text(delivery.result_id, 'delivery result_id')
        for field in ('hour_start', 'hour_end', 'recorded_at'):
            _aware(getattr(delivery, field), field)
        _boolean(delivery.executed, 'executed'); _boolean(delivery.designated_performance_test, 'designated_performance_test')
        _provenance(delivery.provenance)
        if not delivery.executed or not delivery.hour_start < delivery.hour_end <= delivery.recorded_at <= cutoff:
            raise ValueError('hour must be executed and complete; evidence publication must be after hour end and available at settlement')
        key = SERVICE, delivery.resource_id, delivery.hour_start
        _booking_key(key)
        if key in seen:
            raise ValueError('duplicate service/resource/hour booking; do not pay each RT quarter')
        seen.add(key)
        if delivery.result_id not in by_id:
            raise ValueError('delivery references unknown effective result')
        wrapped = by_id[delivery.result_id]; result = wrapped.capacity_result
        if result.stage != 'intraday_formal' or (delivery.hour_start, delivery.hour_end) != (result.binding_start, result.binding_end):
            raise ValueError('only the formal binding hour is payable; no DA/lookahead/partial-hour payment')
        if delivery.resource_id not in result.capacity_mw:
            raise ValueError('delivery resource absent from capacity inventory')
        qualification = inventory[delivery.resource_id]
        if qualification.kind not in THERMAL_KINDS:
            raise ValueError('nonthermal actual AGC delivery unsupported')
        if not qualification.market_qualified or qualification.known_at > result.published_at or not qualification.valid_from <= delivery.hour_start < delivery.hour_end <= qualification.valid_until:
            raise ValueError('hourly delivery requires an available valid market qualification')
        if delivery.calling_status not in {'awarded', 'temporary_operator_call'}:
            raise ValueError('explicit awarded or temporary_operator_call status required')
        _text(delivery.calling_source, 'calling source')
        if delivery.calling_status == 'awarded' and result.capacity_mw[delivery.resource_id] <= 0:
            raise ValueError('zero award requires explicit temporary operator calling evidence')
        for field in ('instruction_source', 'mileage_source', 'performance_source', 'quality_assessment_source'):
            _optional_text(getattr(delivery, field), field)
        missing = []
        for value, name in ((delivery.actual_mileage_mw, 'actual_mileage_mw'), (delivery.settlement_performance_m, 'settlement_performance_m')):
            if value is None:
                missing.append(name)
            else:
                _number(value, name)
        for name in ('instruction_source', 'mileage_source', 'performance_source'):
            if getattr(delivery, name) is None:
                missing.append(name)
        for field in ('mileage_published_at', 'performance_published_at'):
            published = getattr(delivery, field)
            if published is None:
                missing.append(field)
            else:
                _aware(published, field)
                if not delivery.hour_end <= published <= delivery.recorded_at:
                    raise ValueError('mileage/performance publication must follow completed hour and precede available record')
        price_input = wrapped.uniform_price_yuan_per_mw
        if price_input is None:
            missing.append('uniform_price_yuan_per_mw')
        price = None if price_input is None else float(price_input.value)
        gross = estimate = None
        synthetic = any(p.kind == 'synthetic' for p in (delivery.provenance, wrapped.provenance, qualification.provenance)) or any(
            value is not None and value.provenance.kind == 'synthetic'
            for value in (price_input, wrapped.control_area_k_max))
        if not missing:
            amount = mileage_payment(delivery.actual_mileage_mw, price, delivery.settlement_performance_m,
                                     designated_performance_test=delivery.designated_performance_test)
            if synthetic:
                estimate = amount
            else:
                gross = amount
            booked.add(key)
        net_unresolved = ('quality_deductions_under_sections_68_69_not_implemented',
                          'complete_assessments_and_cost_allocations_not_implemented')
        if delivery.quality_assessment_source is None:
            net_unresolved += ('quality_assessment_evidence_missing',)
        rows.append(AGCMileageRow(key, result.result_id, float(result.capacity_mw[delivery.resource_id]),
            None if delivery.actual_mileage_mw is None else float(delivery.actual_mileage_mw), price,
            None if delivery.settlement_performance_m is None else float(delivery.settlement_performance_m),
            gross, estimate, None, tuple(missing), net_unresolved, dict(
                contains_synthetic_input=synthetic, source_authenticity_verified=False,
                calling_status=delivery.calling_status, calling_source=delivery.calling_source,
                instruction_source=delivery.instruction_source, mileage_source=delivery.mileage_source,
                performance_source=delivery.performance_source, quality_assessment_source=delivery.quality_assessment_source,
                evidence_recorded_at=delivery.recorded_at, mileage_published_at=delivery.mileage_published_at,
                performance_published_at=delivery.performance_published_at,
                market_qualification_certified=False, qualification_source=qualification.provenance.source, mileage_unit='MW', price_unit='yuan/MW',
                designated_performance_test=delivery.designated_performance_test,
                duration_multiplier_applied=False, Fm_applied=False, actual_energy_entry_created=False)))
    def total(field):
        values = [getattr(row, field) for row in rows]
        if any(value is None for value in values):
            return None
        return _number(fsum(values), field)
    return AGCMileageLedger(tuple(rows), total('gross_compensation_yuan'), total('research_gross_estimate_yuan'),
        None, frozenset(booked), dict(
            settlement_scope='gross_mileage_formula_only', official_net_settlement=False,
            source_authenticity_verified=False, capacity_payment_created=False,
            reserve_difference_trade_created=False, energy_deviation_entry_created=False,
            cross_run_idempotency='caller_must_atomically_persist_service_resource_hour_keys'))
