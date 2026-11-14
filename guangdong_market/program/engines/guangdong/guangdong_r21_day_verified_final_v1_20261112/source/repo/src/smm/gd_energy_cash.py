"""Caller-supplied DA/RT energy-deviation cash, never an official total ledger.

Prices must be explicitly selected/classified. Missing data remain unavailable;
research/synthetic inputs cannot become real settlement cash. No solver or
legacy settlement/reserve-auction code is imported. All quantities are MWh.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import fsum
from zoneinfo import ZoneInfo

from .gd_price_research import EvidenceValue, SelectedCashPrice, _number, _text


class EnergyCashInputError(ValueError):
    """Invalid, ambiguous, duplicate, or inconsistent cash-input coverage."""


@dataclass(frozen=True)
class SettlementKey:
    resource: str
    resource_kind: str
    settlement_unit: str
    node: str
    direction: str
    period: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class QuarterHourEligibility:
    resource: str
    source: str


@dataclass(frozen=True)
class EnergyCashScope:
    keys: tuple[SettlementKey, ...]
    source: str
    interval_scope: str
    quarter_hour_eligibility: tuple[QuarterHourEligibility, ...] = ()
    # Explicit scope prevents treating price-taking renewable/user-side modes
    # as ordinary generator/storage DA+RT deviation settlement.
    settlement_mode: str = "participating_generator_storage_DA_RT_deviation"


@dataclass(frozen=True)
class DayAheadDeduction:
    category: str
    energy_mwh: EvidenceValue


@dataclass(frozen=True)
class EnergyVolumeInput:
    key: SettlementKey
    day_ahead_mwh: EvidenceValue
    actual_mwh: EvidenceValue
    day_ahead_deductions: tuple[DayAheadDeduction, ...]


@dataclass(frozen=True)
class EnergyPriceInput:
    key: SettlementKey
    selected_price: SelectedCashPrice
    interval_mapping_source: str


def _aware(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EnergyCashInputError(f"{label}: timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _tuple(value, cls, label, *, nonempty=False):
    if not isinstance(value, tuple) or (nonempty and not value) or not all(isinstance(x, cls) for x in value):
        raise EnergyCashInputError(f"{label}: {'nonempty ' if nonempty else ''}typed tuple required")


def _key_dict(key):
    result = asdict(key)
    result.update(start=key.start.isoformat(), end=key.end.isoformat())
    return result


def _scope(scope):
    if not isinstance(scope, EnergyCashScope):
        raise EnergyCashInputError("EnergyCashScope required")
    _text(scope.source, "settlement scope source")
    if scope.interval_scope not in {"explicit_intervals", "complete_day"}:
        raise EnergyCashInputError("explicit_intervals or complete_day scope required")
    if scope.settlement_mode != "participating_generator_storage_DA_RT_deviation":
        raise EnergyCashInputError("only participating generator/storage DA/RT deviation mode supported")
    _tuple(scope.keys, SettlementKey, "scope keys", nonempty=True)
    _tuple(scope.quarter_hour_eligibility, QuarterHourEligibility, "15-minute eligibility")
    eligible = {}
    for e in scope.quarter_hour_eligibility:
        _text(e.resource, "eligible resource")
        _text(e.source, "15-minute eligibility source")
        if e.resource in eligible:
            raise EnergyCashInputError("duplicate 15-minute resource eligibility")
        eligible[e.resource] = e.source
    keys, units, resources, intervals, storage_pairs, used_eligibility = set(), {}, {}, {}, {}, set()
    direction_units = {}
    for key in scope.keys:
        for label in ("resource", "settlement_unit", "node", "period"):
            _text(getattr(key, label), label)
        if key.resource_kind not in {"thermal", "renewable", "battery", "pumped"}:
            raise EnergyCashInputError("supported provincial resource kind required")
        if key.direction not in {"export", "import"}:
            raise EnergyCashInputError("explicit signed export/import direction required")
        storage = key.resource_kind in {"battery", "pumped"}
        if not storage and key.direction != "export":
            raise EnergyCashInputError("generator unit cannot use storage import direction")
        start, end = _aware(key.start, "start"), _aware(key.end, "end")
        minutes = (end-start).total_seconds()/60
        local = start.astimezone(ZoneInfo("Asia/Shanghai"))
        if minutes not in {15, 60} or local.second or local.microsecond or local.minute % int(minutes):
            raise EnergyCashInputError("settlement interval must be aligned Shanghai hour or quarter-hour")
        if minutes == 15:
            if not storage or key.resource not in eligible:
                raise EnergyCashInputError("15-minute settlement requires storage/pumped resource with explicit eligibility")
            used_eligibility.add(key.resource)
        if key in keys:
            raise EnergyCashInputError("duplicate settlement key")
        keys.add(key)
        identity = (key.resource, key.resource_kind, key.node, key.direction)
        if key.settlement_unit in units and units[key.settlement_unit] != identity:
            raise EnergyCashInputError("settlement unit cannot change resource/node/kind/direction")
        units[key.settlement_unit] = identity
        direction_identity = (key.resource, key.direction)
        if direction_identity in direction_units and direction_units[direction_identity] != key.settlement_unit:
            raise EnergyCashInputError("resource/direction cannot switch settlement units within scope")
        direction_units[direction_identity] = key.settlement_unit
        resource_identity = (key.resource_kind, key.node)
        if key.resource in resources and resources[key.resource] != resource_identity:
            raise EnergyCashInputError("resource cannot change kind or physical node within scope")
        resources[key.resource] = resource_identity
        intervals.setdefault(key.settlement_unit, []).append((start, end, key.period))
        if storage:
            pair = storage_pairs.setdefault((key.resource, start, end, key.period), set())
            if key.direction in pair:
                raise EnergyCashInputError("duplicate resource/direction interval under different settlement units")
            pair.add(key.direction)
    if used_eligibility != set(eligible):
        raise EnergyCashInputError("unused 15-minute eligibility declaration")
    if any(pair != {"export", "import"} for pair in storage_pairs.values()):
        raise EnergyCashInputError("storage/pumped scope must cover both separate directions, including explicit zeros")
    # Also forbid duplicate generator intervals under alternate unit IDs.
    identities = [(k.resource, k.direction, _aware(k.start, "start"), _aware(k.end, "end")) for k in scope.keys]
    if len(identities) != len(set(identities)):
        raise EnergyCashInputError("resource/direction interval appears under multiple settlement keys")
    day_bounds = set()
    for sequence in intervals.values():
        sequence.sort()
        if len({p for _, _, p in sequence}) != len(sequence):
            raise EnergyCashInputError("period identifier repeated for a settlement unit")
        for left, right in zip(sequence, sequence[1:]):
            if right[0] < left[1]:
                raise EnergyCashInputError("overlapping settlement intervals")
            if scope.interval_scope == "complete_day" and right[0] != left[1]:
                raise EnergyCashInputError("complete_day cannot contain interval gaps")
        if scope.interval_scope == "complete_day":
            start, end = sequence[0][0], sequence[-1][1]
            local = start.astimezone(ZoneInfo("Asia/Shanghai"))
            if local.hour or local.minute or local.second or local.microsecond or end-start != timedelta(days=1):
                raise EnergyCashInputError("complete_day must cover one Shanghai midnight-to-midnight day per unit")
            day_bounds.add((start, end))
    if scope.interval_scope == "complete_day" and len(day_bounds) != 1:
        raise EnergyCashInputError("complete_day units must cover the same day")
    return keys


def _value(value, label, *, direction=None):
    if not isinstance(value, EvidenceValue):
        raise EnergyCashInputError(f"{label}: typed EvidenceValue required")
    value.validate(label)
    if value.kind != "missing" and direction is not None:
        v = _number(value.value, label)
        if (direction == "export" and v < 0) or (direction == "import" and v > 0):
            raise EnergyCashInputError(f"{label}: quantity sign inconsistent with settlement direction")


def _sum(values, label):
    try:
        return _number(fsum(values), label)
    except OverflowError as exc:
        raise EnergyCashInputError(f"{label}: cash arithmetic overflow") from exc


def calculate_energy_cash(*, scope: EnergyCashScope,
        volumes: tuple[EnergyVolumeInput, ...], prices: tuple[EnergyPriceInput, ...]) -> dict:
    """Calculate only explicit in-scope energy deviations, with exact key joins.

    Absent expected records produce unavailable rows. Unexpected/duplicate or
    malformed records raise instead of being dropped. Complete totals require
    all records; no absent values, contracts or prices are replaced with zero.
    """
    keys = _scope(scope)
    _tuple(volumes, EnergyVolumeInput, "volume inputs")
    _tuple(prices, EnergyPriceInput, "price inputs")
    qmap, pmap = {}, {}
    for q in volumes:
        if not isinstance(q.key, SettlementKey) or q.key not in keys:
            raise EnergyCashInputError("volume key outside declared resource/node/time scope")
        if q.key in qmap:
            raise EnergyCashInputError("duplicate volume key")
        _value(q.day_ahead_mwh, "DA energy", direction=q.key.direction)
        _value(q.actual_mwh, "actual energy", direction=q.key.direction)
        _tuple(q.day_ahead_deductions, DayAheadDeduction, "day-ahead deductions", nonempty=True)
        expected = {"mid_long_term_net_contract"}
        storage = q.key.resource_kind in {"battery", "pumped"}
        if not storage:
            expected |= {"base", "agency_grid_export"}
        categories = [d.category for d in q.day_ahead_deductions]
        if len(set(categories)) != len(categories) or set(categories) != expected:
            raise EnergyCashInputError("exact applicable DA deduction categories required, including sourced zeros")
        for d in q.day_ahead_deductions:
            # Storage net sell/buy contracts are assigned to separate signed units.
            # Generator net contracts need not have the same sign as generation.
            _value(d.energy_mwh, "deduction."+d.category, direction=q.key.direction if storage else None)
        qmap[q.key] = q
    for p in prices:
        if not isinstance(p.key, SettlementKey) or p.key not in keys:
            raise EnergyCashInputError("price key outside declared resource/node/time scope")
        if not isinstance(p.selected_price, SelectedCashPrice):
            raise EnergyCashInputError("only explicit SelectedCashPrice inputs accepted, not raw duals/reports")
        p.selected_price.validate()
        _text(p.interval_mapping_source, "price settlement-interval mapping source")
        selected = p.selected_price
        if selected.node != p.key.node or selected.period != p.key.period:
            raise EnergyCashInputError("selected price node/period must match the exact settlement key")
        key = p.key, selected.stage
        if key in pmap:
            raise EnergyCashInputError("duplicate stage price key")
        pmap[key] = p
    rows = []
    for key in scope.keys:
        q = qmap.get(key)
        da, rt = pmap.get((key, "DA")), pmap.get((key, "RT"))
        missing = []
        evidence = []
        if q is None:
            missing.append("volume_record")
        else:
            evidence.extend([("DA_energy", q.day_ahead_mwh), ("actual_energy", q.actual_mwh)])
            evidence.extend(("DA_deduction."+d.category, d.energy_mwh) for d in q.day_ahead_deductions)
        for stage, p in (("DA", da), ("RT", rt)):
            if p is None:
                missing.append(stage+"_price_record")
            else:
                evidence.append((stage+"_price", p.selected_price.value))
        missing.extend(f"{label}: {e.missing_reason}" for label, e in evidence if e.kind == "missing")
        row = dict(key=_key_dict(key), status="unavailable" if missing else
            ("research_cash" if any(e.kind in {"research", "synthetic"} for _, e in evidence) else "external_data_cash"),
            missing=missing, day_ahead_deviation_mwh=None, realtime_deviation_mwh=None,
            day_ahead_cash_yuan=None, realtime_cash_yuan=None, energy_deviation_cash_yuan=None,
            volume_input=None if q is None else asdict(q),
            price_inputs={stage: None if p is None else asdict(p) for stage, p in (("DA", da), ("RT", rt))})
        # ISO timestamps in every nested record make the returned ledger JSON-safe.
        if row["volume_input"] is not None:
            row["volume_input"]["key"] = _key_dict(key)
        for p in row["price_inputs"].values():
            if p is not None:
                p["key"] = _key_dict(key)
        if not missing:
            deductions = _sum([_number(d.energy_mwh.value, "deduction") for d in q.day_ahead_deductions], "deduction sum")
            qda, actual = _number(q.day_ahead_mwh.value, "DA energy"), _number(q.actual_mwh.value, "actual energy")
            dda = _sum([qda]+[-_number(d.energy_mwh.value, "deduction") for d in q.day_ahead_deductions], "DA deviation")
            drt = _sum([actual, -qda], "RT deviation")
            cda = _number(dda * _number(da.selected_price.value.value, "DA price"), "DA cash")
            crt = _number(drt * _number(rt.selected_price.value.value, "RT price"), "RT cash")
            row.update(day_ahead_deviation_mwh=dda, realtime_deviation_mwh=drt,
                day_ahead_cash_yuan=cda, realtime_cash_yuan=crt,
                energy_deviation_cash_yuan=_sum([cda, crt], "row total"))
        rows.append(row)
    available = all(r["status"] != "unavailable" for r in rows)
    status = ("research_cash" if any(r["status"] == "research_cash" for r in rows) else "external_data_cash") if available else "unavailable"
    total = {field: _sum([r[field] for r in rows], field+" total") if available else None
             for field in ("day_ahead_cash_yuan", "realtime_cash_yuan", "energy_deviation_cash_yuan")}
    return dict(status=status, rows=rows, totals=total,
        audit=dict(scope_source=scope.source, interval_scope=scope.interval_scope,
            settlement_mode=scope.settlement_mode,
            quarter_hour_eligibility=[asdict(e) for e in scope.quarter_hour_eligibility],
            source_document="https://nxepta.com/attached/file/20260205/20260205011306600.pdf",
            source_sections=["5.1", "5.2", "5.3.3", "7.2.4", "7.2.5", "7.3.2", "7.3.3"],
            evidence_scope="Guangdong_2026_reference_not_current_regional_version_certification",
            quantity_unit="MWh", price_unit="yuan/MWh", cash_unit="yuan",
            declared_scope_exact_coverage=available, missing_row_count=sum(r["status"] == "unavailable" for r in rows),
            input_provenance_is_caller_declaration=True, actual_meter_authenticity_verified=False,
            full_resource_inventory_certified=False, publication_mapping_verified=False,
            full_official_settlement_verified=False, official_cash_published=False,
            price_clipping_applied=False, automatic_time_aggregation=False,
            reserve_auction_cash_created=False, uplift_created=False,
            excluded_cash_components=["mid_long_term_full_contract", "contract_congestion", "base_and_agency_contract_cash",
                "system_operation_and_startup_compensation", "regulation_and_spinning_compensation",
                "assessment_allocation_rebates", "retail_and_user_uniform_settlement_point", "tax_breakdown", "costs_and_profit"],
            excluded_components_are_zero=False))
