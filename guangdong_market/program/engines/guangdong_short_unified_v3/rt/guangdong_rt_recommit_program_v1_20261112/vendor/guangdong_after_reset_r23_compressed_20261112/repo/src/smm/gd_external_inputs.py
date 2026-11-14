"""Strict, solver-free JSON inputs rebuilt from the phase-4 public contracts.

An input hash proves consistency, not truth, approval, or source authenticity.
No files, network, optimization, current-rule certification or booking is done.
"""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
import re
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from .gd_agc_delivery import (
    AGCHourDelivery, ResourceAGCQualification, TypedAGCResult,
    build_agc_mileage_ledger, _results, _booking_key, THERMAL_KINDS, RESOURCE_KINDS,
)
from .gd_energy_cash import (
    EnergyCashScope, EnergyPriceInput, EnergyVolumeInput, calculate_energy_cash, _scope,
)

SCHEMA_VERSION = "gd.external.v1"
AGC_UNITS = {"capacity": "MW", "mileage": "MW", "price": "yuan/MW", "performance": "dimensionless"}
ENERGY_UNITS = {"energy": "MWh", "price": "yuan/MWh", "cash": "yuan"}


class ExternalInputError(ValueError):
    """Invalid schema, identity, provenance, coverage, timing or integrity."""


def _require(condition, message):
    if not condition:
        raise ExternalInputError(message)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label + ": nonempty text required")
    return value


def _object(value, required, label, optional=()):
    _require(type(value) is dict, label + ": JSON object required")
    _require(set(required) <= set(value) <= set(required) | set(optional),
             label + ": missing or unknown fields")
    return value


def _list(value, label):
    _require(type(value) is list, label + ": JSON array required")
    return value


def _time(value, label):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExternalInputError(label + ": ISO datetime required") from exc
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             label + ": timezone-aware ISO datetime required")
    return value


def json_safe(value):
    """Serialize validated typed reports, including booking keys and timestamps."""
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [json_safe(v) for v in sorted(value, key=repr)]
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON member: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ExternalInputError("non-finite JSON constant: " + value)


def _json_tree(value):
    if type(value) is dict:
        _require(all(type(k) is str for k in value), "JSON object keys must be strings")
        for item in value.values():
            _json_tree(item)
    elif type(value) is list:
        for item in value:
            _json_tree(item)
    elif type(value) is float:
        _require(isfinite(value), "non-finite JSON number")
    else:
        _require(value is None or type(value) in (str, int, bool), "only plain JSON values accepted")


def read_json_document(document):
    """Parse bytes/text/object without paths, eval, coercion or duplicate keys."""
    if isinstance(document, (str, bytes)):
        try:
            document = json.loads(document, object_pairs_hook=_pairs, parse_constant=_constant)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ExternalInputError("invalid JSON: " + str(exc)) from exc
    _require(type(document) is dict, "top-level JSON object required")
    try:
        _json_tree(document)
        # Detach every mutable caller-owned object and reject unsupported types.
        return json.loads(json.dumps(document, allow_nan=False, ensure_ascii=False))
    except (OverflowError, ValueError, RecursionError) as exc:
        raise ExternalInputError("invalid JSON values") from exc


def content_sha256(document):
    """SHA256 of UTF-8 sorted compact JSON, excluding content_sha256 only.

    This is a local integrity helper, not an external signature verifier.
    """
    data = read_json_document(document)
    data.pop("content_sha256", None)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _decode(cls, value, label):
    """Decode only the explicit dataclass schema. Never import types from JSON."""
    origin, args = get_origin(cls), get_args(cls)
    if origin in (Union, UnionType):
        for member in args:
            try:
                return _decode(member, value, label)
            except (ExternalInputError, ValueError, TypeError):
                pass
        raise ExternalInputError(label + ": unsupported union value")
    if cls is type(None):
        _require(value is None, label + ": null required")
        return None
    if origin is Literal:
        _require(any(type(value) is type(x) and value == x for x in args), label + ": invalid literal")
        return value
    if cls is datetime:
        _require(type(value) is str, label + ": ISO datetime string required")
        return _time(value, label)
    if cls in (str, bool, int):
        _require(type(value) is cls, label + ": " + cls.__name__ + " required")
        return value
    if cls is float:
        _require(type(value) in (int, float), label + ": numeric value required, not bool/string")
        try:
            result = float(value)
        except OverflowError as exc:
            raise ExternalInputError(label + ": finite number required") from exc
        _require(isfinite(result), label + ": finite number required")
        return result
    if origin is tuple:
        _list(value, label)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v, label + "[]") for v in value)
        _require(len(value) == len(args), label + ": tuple length mismatch")
        return tuple(_decode(c, v, label + "[]") for c, v in zip(args, value))
    if origin is dict:
        _object(value, (), label, value.keys() if type(value) is dict else ())
        return {_decode(args[0], k, label + ".key"): _decode(args[1], v, label + ".value") for k, v in value.items()}
    if isinstance(cls, type) and is_dataclass(cls):
        spec = fields(cls)
        required = {f.name for f in spec if f.default is MISSING and f.default_factory is MISSING}
        _object(value, required, label, {f.name for f in spec} - required)
        hints = get_type_hints(cls)
        return cls(**{k: _decode(hints[k], v, label + "." + k) for k, v in value.items()})
    raise ExternalInputError(label + ": unsupported schema type")


@dataclass(frozen=True)
class ExternalMetadata:
    schema_version: str
    input_type: str
    run_id: str
    selected_dispatch: str
    source: str
    content_sha256: str
    timezone: str
    published_at: datetime
    available_at: datetime
    asof: datetime
    units: dict[str, str]


def _envelope(document, *, input_type, expected_run_id, expected_selected_dispatch, asof, units):
    data = read_json_document(document)
    _object(data, ("schema_version", "input_type", "run_id", "selected_dispatch", "source", "content_sha256",
                   "timezone", "published_at", "available_at", "units", "payload"), "envelope")
    _text(expected_run_id, "expected_run_id")
    _text(expected_selected_dispatch, "expected_selected_dispatch")
    _require(data["schema_version"] == SCHEMA_VERSION and data["input_type"] == input_type, "unsupported schema or input_type")
    _require(data["run_id"] == expected_run_id, "run_id mismatch")
    _require(data["selected_dispatch"] == expected_selected_dispatch, "selected_dispatch mismatch")
    _text(data["source"], "source")
    _require(data["timezone"] == "Asia/Shanghai", "timezone must explicitly be Asia/Shanghai")
    _require(data["units"] == units, "unit mapping mismatch; no automatic conversion")
    digest = data["content_sha256"]
    _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "lowercase SHA256 required")
    _require(digest == content_sha256(data), "content_sha256 mismatch")
    published, available, cutoff = (_time(data["published_at"], "published_at"),
        _time(data["available_at"], "available_at"), _time(asof, "asof"))
    _require(published <= available <= cutoff, "publication <= availability <= asof required")
    metadata = ExternalMetadata(SCHEMA_VERSION, input_type, expected_run_id, expected_selected_dispatch,
        data["source"], digest, data["timezone"], published, available, cutoff, dict(units))
    return metadata, data["payload"]


def _record(cls, wrapped, label, metadata, *, completed_at=None):
    _object(wrapped, ("published_at", "available_at", "data"), label)
    published, available = _time(wrapped["published_at"], label + ".published_at"), _time(wrapped["available_at"], label + ".available_at")
    _require(published <= available <= metadata.available_at <= metadata.asof,
             label + ": publication <= availability <= envelope availability <= asof required")
    _require(published <= metadata.published_at, label + ": record published after envelope publication")
    record = _decode(cls, wrapped["data"], label + ".data")
    if completed_at is not None:
        end = completed_at(record)
        if end is not None:
            _require(end <= published, label + ": actual interval end must precede publication")
    return record, published, available


def _audit(metadata):
    return dict(metadata=json_safe(metadata), source_authenticity_verified=False,
        input_hash_is_authentication=False, source_is_caller_declaration=True,
        full_official_settlement_verified=False, reconstruction_basis="phase4_independent_rebuild_not_recovered_phase6")


@dataclass(frozen=True)
class ImportedEnergyInputs:
    metadata: ExternalMetadata
    scope: EnergyCashScope
    volumes: tuple[EnergyVolumeInput, ...]
    prices: tuple[EnergyPriceInput, ...]
    settlement_interval_source: str


def _energy_prices(wrapped_rows, metadata):
    prices = []
    for wrapper in _list(wrapped_rows, "prices"):
        # A published RT clearing/research price can precede delivery. Unlike
        # actual energy, price evidence does not require a completed interval.
        price, published, available = _record(EnergyPriceInput, wrapper, "price", metadata)
        prices.append(price)
    return tuple(prices)


def load_contract_energy_inputs(document, *, expected_run_id, expected_selected_dispatch, asof):
    """Import source-backed deductions, not full contract cash or official settlement."""
    metadata, payload = _envelope(document, input_type="contract_energy", expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, units=ENERGY_UNITS)
    _object(payload, ("scope", "volumes", "prices", "settlement_interval_source"), "contract_energy payload")
    _text(payload["settlement_interval_source"], "settlement_interval_source")
    scope = _decode(EnergyCashScope, payload["scope"], "scope")
    _scope(scope)
    volumes = tuple(_record(EnergyVolumeInput, wrapper, "volume", metadata,
        completed_at=lambda q: q.key.end if q.actual_mwh.kind != "missing" else None)[0]
        for wrapper in _list(payload["volumes"], "volumes"))
    prices = _energy_prices(payload["prices"], metadata)
    # Existing implementation validates all scope/records before arithmetic.
    calculate_energy_cash(scope=scope, volumes=volumes, prices=prices)
    return ImportedEnergyInputs(metadata, scope, volumes, prices, payload["settlement_interval_source"])


def build_contract_energy_report(document, *, expected_run_id, expected_selected_dispatch, asof):
    inputs = load_contract_energy_inputs(document, expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof)
    report = calculate_energy_cash(scope=inputs.scope, volumes=inputs.volumes, prices=inputs.prices)
    report["metadata"] = json_safe(inputs.metadata)
    report["audit"].update(_audit(inputs.metadata), settlement_interval_source=inputs.settlement_interval_source,
        ledger_scope="energy_deviation_with_explicit_deductions_only", full_contract_cash_calculated=False)
    return report


@dataclass(frozen=True)
class AGCDeliveryKey:
    resource_id: str
    result_id: str
    hour_start: datetime
    hour_end: datetime


@dataclass(frozen=True)
class ImportedAGCInputs:
    metadata: ExternalMetadata
    results: tuple[TypedAGCResult, ...]
    qualifications: tuple[ResourceAGCQualification, ...]
    deliveries: tuple[AGCHourDelivery, ...]
    expected_delivery_keys: tuple[AGCDeliveryKey, ...]
    missing_delivery_keys: tuple[AGCDeliveryKey, ...]
    already_booked_keys: frozenset[tuple[str, str, datetime]]
    coverage_source: str


def load_external_agc_inputs(document, *, expected_run_id, expected_selected_dispatch, asof, expected_resource_ids):
    """Validate exact declared AGC scope before any payment arithmetic."""
    metadata, payload = _envelope(document, input_type="agc_actual", expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, units=AGC_UNITS)
    _object(payload, ("results", "qualifications", "deliveries", "expected_delivery_keys", "already_booked_keys", "coverage_source"), "agc payload")
    _text(payload["coverage_source"], "coverage_source")
    _require(type(expected_resource_ids) is tuple and bool(expected_resource_ids), "explicit expected_resource_ids tuple required")
    for uid in expected_resource_ids:
        _text(uid, "expected resource")
    _require(len(set(expected_resource_ids)) == len(expected_resource_ids), "duplicate expected resource")
    results = []
    for wrapper in _list(payload["results"], "results"):
        result, published, available = _record(TypedAGCResult, wrapper, "AGC result", metadata)
        _require(result.capacity_result.published_at <= published, "result publication follows wrapper publication")
        for scalar in (result.uniform_price_yuan_per_mw, result.control_area_k_max):
            if scalar is not None:
                _require(scalar.published_at <= published, "scalar publication follows wrapper publication")
        results.append(result)
    results = tuple(results)
    by_id = _results(results, cutoff=metadata.asof)
    qualifications = []
    for wrapper in _list(payload["qualifications"], "qualifications"):
        q, published, available = _record(ResourceAGCQualification, wrapper, "qualification", metadata)
        _require(q.known_at == available, "qualification known_at must equal record available_at")
        _require(q.kind in RESOURCE_KINDS and q.valid_from < q.valid_until, "invalid qualification resource/validity")
        _text(q.resource_id, "qualification resource")
        _require(q.provenance.kind in {"external_verified", "synthetic"}, "invalid qualification provenance")
        _text(q.provenance.source, "qualification source")
        qualifications.append(q)
    qualifications = tuple(qualifications)
    inventory = {q.resource_id: q for q in qualifications}
    _require(len(inventory) == len(qualifications) and set(inventory) == set(expected_resource_ids), "qualification inventory mismatch or duplicates")
    formal = sorted((r.capacity_result for r in results), key=lambda r: r.binding_start)
    _require(all(r.stage == "intraday_formal" for r in formal), "actual AGC requires formal hourly results only")
    _require(all(l.binding_end <= r.binding_start for l, r in zip(formal, formal[1:])), "overlapping formal AGC versions")
    mandatory = set()
    for result in formal:
        for capacities in (result.capacity_mw, *(slot.capacity_mw for slot in result.lookahead_slots)):
            _require(set(capacities) == set(inventory), "AGC result resource inventory mismatch")
            _require(all(v == 0 or inventory[k].kind in THERMAL_KINDS for k, v in capacities.items()), "nonzero nonthermal AGC unsupported")
        for uid, capacity in result.capacity_mw.items():
            if capacity > 0:
                mandatory.add(AGCDeliveryKey(uid, result.result_id, result.binding_start, result.binding_end))
    expected = _decode(tuple[AGCDeliveryKey, ...], payload["expected_delivery_keys"], "expected_delivery_keys")
    _require(bool(expected) and len(set(expected)) == len(expected), "nonempty unique expected delivery keys required")
    _require(mandatory <= set(expected), "positive formal awards missing from expected delivery coverage")
    for key in expected:
        _require(key.resource_id in inventory and inventory[key.resource_id].kind in THERMAL_KINDS, "delivery scope resource outside thermal inventory")
        _require(key.result_id in by_id, "delivery scope references unknown result")
        result = by_id[key.result_id].capacity_result
        _require((key.hour_start, key.hour_end) == (result.binding_start, result.binding_end), "delivery scope interval mismatch")
        _require(key.hour_end <= metadata.asof, "actual delivery scope hour not complete at asof")
        qualification = inventory[key.resource_id]
        _require(qualification.market_qualified and qualification.known_at <= result.published_at
                 and qualification.valid_from <= key.hour_start < key.hour_end <= qualification.valid_until,
                 "expected delivery requires available valid market qualification")
    deliveries, seen = [], set()
    for wrapper in _list(payload["deliveries"], "deliveries"):
        d, published, available = _record(AGCHourDelivery, wrapper, "delivery", metadata, completed_at=lambda d: d.hour_end)
        _require(d.recorded_at == available, "delivery recorded_at must equal available_at")
        for value in (d.mileage_published_at, d.performance_published_at):
            if value is not None:
                _require(d.hour_end <= value <= published, "metric publication must follow completed hour and precede record publication")
        key = AGCDeliveryKey(d.resource_id, d.result_id, d.hour_start, d.hour_end)
        _require(key in expected and key not in seen, "duplicate or out-of-scope AGC delivery")
        seen.add(key)
        deliveries.append(d)
    booked = _decode(tuple[tuple[str, str, datetime], ...], payload["already_booked_keys"], "already_booked_keys")
    _require(len(set(booked)) == len(booked), "duplicate prior booking key")
    for service, uid, at in booked:
        _booking_key((service, uid, at))
        _require(service == "agc_mileage" and uid in inventory, "invalid prior booking key")
        _require(at <= metadata.asof, "future prior booking key")
    # Validates qualification and delivery semantics even with missing metrics.
    if deliveries:
        build_agc_mileage_ledger(results=results, qualifications=qualifications, deliveries=tuple(deliveries),
            settlement_cutoff=metadata.asof, already_booked_keys=frozenset(booked))
    return ImportedAGCInputs(metadata, results, qualifications, tuple(deliveries), expected,
        tuple(k for k in expected if k not in seen), frozenset(booked), payload["coverage_source"])


def build_external_agc_report(document, *, expected_run_id, expected_selected_dispatch, asof, expected_resource_ids):
    inputs = load_external_agc_inputs(document, expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, expected_resource_ids=expected_resource_ids)
    ledger = None
    if inputs.deliveries:
        ledger = json_safe(build_agc_mileage_ledger(results=inputs.results, qualifications=inputs.qualifications,
            deliveries=inputs.deliveries, settlement_cutoff=inputs.metadata.asof, already_booked_keys=inputs.already_booked_keys))
    complete = ledger is not None and not inputs.missing_delivery_keys and all(not r["gross_missing_evidence"] for r in ledger["rows"])
    if ledger is not None and not complete:
        ledger["gross_compensation_yuan"] = None
        ledger["research_gross_estimate_yuan"] = None
    audit = _audit(inputs.metadata)
    audit.update(coverage_source=inputs.coverage_source, declared_scope_exact_coverage=bool(complete),
        actual_missing_values_replaced_with_zero=False, cross_run_booking_persisted=False,
        expected_resource_inventory_certified=False, official_net_settlement=False)
    status = "unavailable" if not complete else ("research_gross_estimate" if any(r["audit"]["contains_synthetic_input"] for r in ledger["rows"]) else "external_gross_formula")
    return dict(status=status, metadata=json_safe(inputs.metadata), ledger=ledger,
        missing_delivery_keys=json_safe(inputs.missing_delivery_keys), audit=audit)
