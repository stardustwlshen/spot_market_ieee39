"""Solver-free October RT input contract; no dispatch, interpolation or pricing.

The 24x5min and 8x15min *full data grids* are distinct from actual rolling
optimization indices. Time endpoint conventions and cross-grid mappings remain
external engineering declarations, never official-rule certification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
import re
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "gd.october_rt.contract.v1"
SERIES_SCHEMA = "gd.october_rt.series.v1"
MAPPING_SCHEMA = "gd.october_rt.mapping.v1"
PROFILE = "southern_2026_10_rt_full_data_grid_v1"
RULE_VERSION = "southern_spot_2026_v1.0_20261023"
TIMEZONE = "Asia/Shanghai"
STAGES = {"sced": (24, 5), "lmp": (8, 15)}
IDENTITY_FIELDS = {"run_id", "window_id", "selected_dispatch_id", "selected_dispatch_sha256"}
SERIES_FIELDS = {"schema_version", "identity", "stage", "family", "unit", "resource_ids",
                 "source_uri", "provenance", "native_interval_minutes", "transformation",
                 "derivation", "rows"}
REQUIRED_FIELDS = {"schema_version", "profile", "rule_version", "timezone", "identity",
    "delivery_start", "active_sced_start_index", "timing_declaration", "information_cutoff",
    "required_series", "series_sources", "selected_dispatch_source", "mapping_source", "content_sha256"}


class OctoberRTContractError(ValueError):
    def __init__(self, code, path, message):
        self.code, self.path, self.message = code, path, message
        super().__init__(f"{path}: {message}")


def _check(condition, code, path, message):
    if not condition:
        raise OctoberRTContractError(code, path, message)


def _object(value, fields, path):
    _check(type(value) is dict, "invalid_type", path, "object required")
    missing = sorted(set(fields) - set(value))
    _check(not missing, "missing", path, "missing fields: " + ", ".join(missing))
    _check(set(value) == set(fields), "unknown_fields", path, "unexpected fields")
    return value


def _text(value, path):
    _check(type(value) is str and bool(value.strip()), "missing", path, "nonempty declaration/source required")
    return value


def _digest(value, path):
    _check(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
           "invalid_hash", path, "lowercase SHA256 required")
    return value


def _time(value, path):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    _check(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
           "invalid_time", path, "timezone-aware ISO datetime required")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result, "duplicate_json_key", key, "duplicate JSON member")
        result[key] = value
    return result


def _constant(value):
    raise OctoberRTContractError("nonfinite", "document", "non-finite JSON constant: " + value)


def _json_tree(value, path="document"):
    if type(value) is dict:
        _check(all(type(k) is str for k in value), "invalid_type", path, "JSON keys must be strings")
        for key, child in value.items():
            _json_tree(child, path + "." + key)
    elif type(value) is list:
        for index, child in enumerate(value):
            _json_tree(child, f"{path}[{index}]")
    elif type(value) is float:
        _check(isfinite(value), "nonfinite", path, "finite values required")
    else:
        _check(value is None or type(value) in (str, int, bool), "invalid_type", path, "plain JSON values required")


def _document(value):
    try:
        if isinstance(value, (str, bytes)):
            value = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        _check(type(value) is dict, "invalid_type", "document", "JSON object required")
        _json_tree(value)
        return deepcopy(value)
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, OctoberRTContractError):
            raise
        raise OctoberRTContractError("invalid_json", "document", str(exc)) from exc


def content_sha256(document):
    """Local canonical JSON integrity, not authenticity or signature verification."""
    value = _document(document)
    value.pop("content_sha256", None)
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _identity(value, expected, path):
    _object(value, IDENTITY_FIELDS, path)
    for key in IDENTITY_FIELDS:
        _text(value[key], path + "." + key)
    _digest(value["selected_dispatch_sha256"], path + ".selected_dispatch_sha256")
    _check(value == expected, "identity_mismatch", path, "run/window/selected dispatch identity mismatch")


def _resources(value, path):
    _check(type(value) is list and all(type(x) is str and x.strip() for x in value),
           "invalid_resources", path, "explicit resource-id array required")
    _check(len(value) == len(set(value)), "duplicate_resource", path, "duplicate resource id")
    return value


def _grid(start, minutes, count):
    return tuple((start + timedelta(minutes=i * minutes), start + timedelta(minutes=(i + 1) * minutes))
                 for i in range(count))


def _source(ref, sources, used, path, *, parse=True):
    _check(ref is not None, "missing", path, "explicit source reference required; no fallback")
    _object(ref, {"source_id", "source_sha256"}, path)
    source_id = _text(ref["source_id"], path + ".source_id")
    digest = _digest(ref["source_sha256"], path + ".source_sha256")
    _check(source_id in sources, "missing", path, f"missing source bytes: {source_id}")
    raw = sources[source_id]
    _check(type(raw) is bytes, "invalid_type", path, "source bytes required; no path or network fallback")
    _check(sha256(raw).hexdigest() == digest, "source_hash_mismatch", path, "source bytes SHA256 mismatch")
    _check(source_id not in used or used[source_id] == digest,
           "source_identity_mismatch", path, "source id reused with different digest")
    used[source_id] = digest
    return _document(raw) if parse else raw


def _provenance(value, cutoff, mode, rows, path):
    _object(value, {"kind", "uses_future_actual", "published_at", "available_at"}, path)
    kind = value["kind"]
    _check(type(kind) is str and kind in {"issued_forecast", "declared_boundary", "observed_actual", "synthetic_research",
                   "offline_future_actual_research", "derived_simulation_5min"},
           "invalid_provenance", path, "unknown provenance kind")
    future = value["uses_future_actual"]
    _check(type(future) is bool, "invalid_provenance", path, "explicit future-actual Boolean required")
    published = _time(value["published_at"], path + ".published_at")
    available = _time(value["available_at"], path + ".available_at")
    _check(published <= available <= cutoff, "unavailable_at_cutoff", path,
           "publication <= availability <= stage information cutoff required")
    research = kind in {"synthetic_research", "offline_future_actual_research", "derived_simulation_5min"}
    _check(not research or mode == "research", "research_only", path, "synthetic/derived/offline source is not causal input")
    if kind in {"issued_forecast", "declared_boundary", "observed_actual"}:
        _check(not future, "future_actual_leak", path, "future actual cannot be a causal forecast/boundary/observation")
    if kind == "offline_future_actual_research":
        _check(future, "invalid_provenance", path, "offline future-actual source must retain future-actual label")
    if kind == "observed_actual":
        _check(all(_time(r["delivery_end"], path) <= published for r in rows), "future_actual_leak", path,
               "observed actual interval must have ended by publication")
    return research, published, available


def _rows(rows, resources, intervals, family, path):
    _check(type(rows) is list, "invalid_type", path, "explicit row array required")
    expected = {(r, s, e) for r in resources for s, e in intervals}
    found = set()
    for index, row in enumerate(rows):
        loc = f"{path}[{index}]"
        _object(row, {"resource_id", "delivery_start", "delivery_end", "value"}, loc)
        resource = _text(row["resource_id"], loc + ".resource_id")
        start, end = _time(row["delivery_start"], loc), _time(row["delivery_end"], loc)
        key = (resource, start, end)
        _check(key not in found, "duplicate_point", loc, "duplicate resource/delivery interval")
        _check(key in expected, "grid_or_resource_mismatch", loc,
               "unexpected resource, window, interval duration or timestamp")
        value = row["value"]
        try:
            finite = type(value) in (int, float) and isfinite(value)
        except OverflowError:
            finite = False
        _check(finite, "nonfinite", loc + ".value", "finite numeric value required, not Boolean")
        if family in {"load_mw", "wind_mw", "solar_mw"}:
            _check(value >= 0, "invalid_value", loc + ".value", "load/wind/solar power must be nonnegative")
        found.add(key)
    missing = expected - found
    _check(not missing, "missing", path, f"missing {len(missing)} resource/delivery points; no filling or interpolation")


def _series(raw, spec, identity, intervals, cutoff, mode, sources, used, path, *, original=False):
    _object(raw, SERIES_FIELDS, path)
    _check(raw["schema_version"] == SERIES_SCHEMA, "version_mismatch", path, "unsupported series schema")
    _identity(raw["identity"], identity, path + ".identity")
    for key in ("stage", "family", "unit", "resource_ids"):
        _check(raw[key] == spec[key], "series_mismatch", path + "." + key, "declared family/unit/resource inventory mismatch")
    _text(raw["source_uri"], path + ".source_uri")
    _resources(raw["resource_ids"], path + ".resource_ids")
    _rows(raw["rows"], spec["resource_ids"], intervals, spec["family"], path + ".rows")
    research, published, available = _provenance(raw["provenance"], cutoff, mode, raw["rows"], path + ".provenance")
    target_minutes = int((intervals[0][1] - intervals[0][0]).total_seconds() // 60)
    native = raw["native_interval_minutes"]
    _check(type(native) is int, "resolution_mismatch", path, "explicit native resolution in minutes required")
    if raw["transformation"] == "none":
        _check(native == target_minutes, "resolution_mismatch", path,
               "native interval differs; old 15min data cannot be relabelled as native 5min")
        _check(raw["derivation"] is None and raw["provenance"]["kind"] != "derived_simulation_5min",
               "missing_derivation", path, "derived inputs must retain derivation declaration")
    elif raw["transformation"] == "source_backed_forecast_resampling_v1":
        # Independently reproduce the research profile from retained original
        # forecast/conditioning bytes. Never trust a converter's Boolean audit.
        from .gd_october_forecast_inputs import verify_forecast_series, ForecastInputError
        _check(not original, "unsupported_transformation", path, "nested forecast transformations unsupported")
        try:
            verify_forecast_series(raw, intervals=intervals, cutoff=cutoff, mode=mode,
                                   sources=sources, used=used)
        except ForecastInputError as exc:
            raise OctoberRTContractError(exc.code, path + "." + exc.path, exc.message) from exc
        research = True
    else:
        _check(not original and raw["transformation"] == "external_derived_simulation_5min" and
               raw["stage"] == "sced" and native == 15 and target_minutes == 5 and
               raw["provenance"]["kind"] == "derived_simulation_5min",
               "unsupported_transformation", path, "only explicit research 15-to-5 derived simulation input is supported")
        derivation = _object(raw["derivation"], {"method", "source_ref", "value_semantics", "classification",
                                              "is_measurement", "official_interpolation_rule"}, path + ".derivation")
        _text(derivation["method"], path + ".derivation.method")
        _check(derivation["value_semantics"] == "interval_mean_power" and derivation["method"] == "zero_order_hold",
               "invalid_semantics", path, "this profile accepts only interval-mean power with external zero-order hold; not instantaneous samples")
        _check(derivation["classification"] == "derived_simulation" and derivation["is_measurement"] is False
               and derivation["official_interpolation_rule"] is False,
               "false_certification", path, "derived inputs are neither measurements nor official interpolation rules")
        parent = _source(derivation["source_ref"], sources, used, path + ".derivation.source_ref")
        parent_intervals = _grid(intervals[0][0], 15, 8)
        _series(parent, spec, identity, parent_intervals, cutoff, mode, sources, used, path + ".original", original=True)
        pp = parent["provenance"]
        _check(raw["provenance"]["uses_future_actual"] == pp["uses_future_actual"],
               "future_actual_leak", path, "derived input must retain original future-actual label")
        _check(_time(pp["published_at"], path) <= published and _time(pp["available_at"], path) <= available,
               "derivation_time_mismatch", path, "derived publication/availability cannot precede its original source")
        # Independent verification, not a trusted claim from a converter audit.
        # Exactly three equal 5min means span each validated 15min interval;
        # therefore its MW * 1/4 h energy is retained without interpolation.
        original_values = {(row["resource_id"], _time(row["delivery_start"], path)): row["value"]
                           for row in parent["rows"]}
        origin = intervals[0][0]
        for row in raw["rows"]:
            point = _time(row["delivery_start"], path)
            coarse_start = origin + timedelta(minutes=15 * int((point - origin).total_seconds() // 900))
            _check(row["value"] == original_values[(row["resource_id"], coarse_start)],
                   "derived_value_mismatch", path + ".rows",
                   "zero_order_hold mismatch for " + row["resource_id"] + " at " + point.isoformat())
        research = True
    return research


def _mapping(raw, identity, start, sced_grid, lmp_grid, cutoff, path):
    _object(raw, {"schema_version", "identity", "delivery_start", "source_uri", "published_at", "available_at",
                 "selected_dispatch_available_at", "state_mapping", "power_mapping", "inventory_boundary_mapping", "certified", "rows"}, path)
    _check(raw["schema_version"] == MAPPING_SCHEMA, "version_mismatch", path, "unsupported mapping schema")
    _identity(raw["identity"], identity, path + ".identity")
    _check(_time(raw["delivery_start"], path) == start, "window_mismatch", path, "mapping window mismatch")
    for key in ("source_uri", "state_mapping", "power_mapping"):
        _text(raw[key], path + "." + key)
    inventory = _object(raw["inventory_boundary_mapping"],
                        {"operating_day_initial", "rolling_initial", "window_terminal", "source"},
                        path + ".inventory_boundary_mapping")
    for key, value in inventory.items():
        _text(value, path + ".inventory_boundary_mapping." + key)
    _check(raw["certified"] is False, "false_certification", path, "external mapping cannot claim official certification")
    published, available, selected = (_time(raw[k], path + "." + k)
        for k in ("published_at", "available_at", "selected_dispatch_available_at"))
    _check(published <= available <= cutoff and selected <= available, "unavailable_at_cutoff", path,
           "mapping and selected dispatch must be available by LMP cutoff")
    _check(type(raw["rows"]) is list, "invalid_type", path + ".rows", "mapping rows required")
    targets = set()
    for index, row in enumerate(raw["rows"]):
        loc = f"{path}.rows[{index}]"
        _object(row, {"lmp_delivery_start", "sced_delivery_starts"}, loc)
        target = _time(row["lmp_delivery_start"], loc)
        _check(target in {s for s, _ in lmp_grid}, "window_mismatch", loc, "unknown LMP target")
        _check(target not in targets, "duplicate_point", loc, "duplicate LMP mapping target")
        _check(type(row["sced_delivery_starts"]) is list and bool(row["sced_delivery_starts"]),
               "missing", loc, "explicit SCED source points required; no default average/end point")
        points = [_time(t, loc) for t in row["sced_delivery_starts"]]
        _check(len(points) == len(set(points)), "duplicate_point", loc, "duplicate SCED mapping point")
        _check(set(points) <= {s for s, _ in sced_grid}, "window_mismatch", loc, "SCED source outside same window")
        targets.add(target)
    _check(targets == {s for s, _ in lmp_grid},
           "missing", path + ".rows", "mapping must explicitly cover all 8 LMP targets")


def validate_october_rt_contract(document, *, expected_identity, expected_delivery_start, asof,
                                 source_artifacts, mode):
    """Return specific missing/unavailable reasons; never synthesize data or cash.

    Only the caller-supplied bytes are read. A validated result is detached from
    caller mutation, but remains an uncertified input exchange contract.
    """
    report = dict(status="unavailable", schema_version=SCHEMA_VERSION, profile=PROFILE, missing=[], issues=[],
                  certified=False, official_rule_implementation=False, official_price=False,
                  settlement_available=False, source_authenticity_verified=False,
                  mapping_arithmetic_verified=False, derived_arithmetic_verified=False,
                  causal_eligible=False, strict_cost_comparability_verified=False,
                  comparison_with_legacy_da_actual="requires_explicit_resolution_and_feasible_set_difference_declaration",
                  resolution_profile={"sced": {"points": 24, "interval_minutes": 5},
                                      "lmp": {"points": 8, "interval_minutes": 15}},
                  derivation_profiles=[], data=None, source_sha256={})
    used = {}

    def capture(fn):
        try:
            return fn()
        except OctoberRTContractError as exc:
            report["issues"].append(dict(code=exc.code, path=exc.path, message=exc.message))
            if exc.code == "missing":
                report["missing"].append(dict(path=exc.path, message=exc.message))
            return None

    def header():
        data = _document(document)
        _object(data, REQUIRED_FIELDS, "contract")
        for key, expected in (("schema_version", SCHEMA_VERSION), ("profile", PROFILE),
                              ("rule_version", RULE_VERSION), ("timezone", TIMEZONE)):
            _check(data[key] == expected, "version_mismatch", key, "unsupported or legacy profile/version/timezone")
        _check(type(mode) is str and mode in {"causal", "research"}, "invalid_mode", "mode", "explicit causal or research mode required")
        _check(type(source_artifacts) is dict, "invalid_type", "source_artifacts", "explicit source-id to bytes dictionary required")
        _identity(expected_identity, expected_identity, "expected_identity")
        _identity(data["identity"], expected_identity, "identity")
        _digest(data["content_sha256"], "content_sha256")
        _check(data["content_sha256"] == content_sha256(data), "content_hash_mismatch", "content_sha256", "contract content hash mismatch")
        start = _time(data["delivery_start"], "delivery_start")
        _check(start == _time(expected_delivery_start, "expected_delivery_start"), "window_mismatch", "delivery_start", "external expected window mismatch")
        local = start.astimezone(ZoneInfo(TIMEZONE))
        _check(not local.second and not local.microsecond and local.minute % 15 == 0,
               "grid_alignment", "delivery_start", "Shanghai quarter-hour alignment required")
        index = data["active_sced_start_index"]
        _check(type(index) is int and 0 <= index < 24, "invalid_active_index", "active_sced_start_index", "explicit 0..23 full-data-grid index required")
        timing = _object(data["timing_declaration"], {"basis", "source", "description", "interval_convention"}, "timing_declaration")
        _check(timing["basis"] == "external_engineering_declaration" and timing["interval_convention"] == "left_closed_right_open",
               "unsupported_timing", "timing_declaration", "explicit engineering [start,end) convention required")
        _text(timing["source"], "timing_declaration.source"); _text(timing["description"], "timing_declaration.description")
        cutoffs = _object(data["information_cutoff"], STAGES, "information_cutoff")
        cutoffs = {key: _time(value, "information_cutoff." + key) for key, value in cutoffs.items()}
        _check(cutoffs["sced"] <= cutoffs["lmp"] <= _time(asof, "asof"), "unavailable_at_cutoff", "information_cutoff",
               "SCED cutoff <= LMP cutoff <= external asof required")
        return data, start, cutoffs

    parsed = capture(header)
    if parsed is None:
        return report
    data, start, cutoffs = parsed
    grids = {stage: _grid(start, minutes, count) for stage, (count, minutes) in STAGES.items()}

    def specifications():
        specs = data["required_series"]
        _check(type(specs) is list, "invalid_type", "required_series", "explicit stage/family inventory required")
        result = {}
        for index, spec in enumerate(specs):
            path = f"required_series[{index}]"
            _object(spec, {"stage", "family", "unit", "resource_ids", "absence_source"}, path)
            _check(type(spec["stage"]) is str and spec["stage"] in STAGES, "invalid_stage", path, "SCED/LMP stage required")
            _text(spec["family"], path + ".family"); _text(spec["unit"], path + ".unit")
            family = spec["family"]
            _check(family in {"load_mw", "wind_mw", "solar_mw"} or family.startswith("boundary_"),
                   "unsupported_family", path, "explicit load/wind/solar/boundary family required")
            _check(spec["unit"] in {"MW", "MWh", "m3", "dimensionless"},
                   "unit_mismatch", path, "physical boundary units required; price/cash units are not RT input quantities")
            if family.endswith("_mw"):
                _check(spec["unit"] == "MW", "unit_mismatch", path, "power requires MW, not MWh or prices")
            if family.endswith("_mwh"):
                _check(spec["unit"] == "MWh", "unit_mismatch", path, "energy requires MWh")
            resources = _resources(spec["resource_ids"], path + ".resource_ids")
            if not resources:
                _text(spec["absence_source"], path + ".absence_source")
            else:
                _check(spec["absence_source"] is None, "invalid_absence", path, "absence_source must be null for populated resource set")
            key = (spec["stage"], family)
            _check(key not in result, "duplicate_series", path, "duplicate stage/family declaration")
            result[key] = spec
        for stage in STAGES:
            families = {f for s, f in result if s == stage}
            _check({"load_mw", "wind_mw", "solar_mw"} <= families and any(f.startswith("boundary_") for f in families),
                   "missing", "required_series." + stage, "load/wind/solar and explicit boundary families required")
        return result

    specs = capture(specifications)
    selected = capture(lambda: _source(data["selected_dispatch_source"], source_artifacts, used, "selected_dispatch_source", parse=False))
    if selected is not None:
        capture(lambda: _check(sha256(selected).hexdigest() == expected_identity["selected_dispatch_sha256"],
                              "selected_hash_mismatch", "selected_dispatch_source", "raw selected dispatch differs from identity"))
    mapping = capture(lambda: _source(data["mapping_source"], source_artifacts, used, "mapping_source"))
    if mapping is not None:
        capture(lambda: _mapping(mapping, expected_identity, start, grids["sced"], grids["lmp"], cutoffs["lmp"], "mapping"))
    capture(lambda: _check(type(data["series_sources"]) is list, "invalid_type", "series_sources", "source-reference array required"))
    series, seen, research_flags = [], set(), []
    if type(data["series_sources"]) is list and specs is not None:
        for index, ref in enumerate(data["series_sources"]):
            path = f"series_sources[{index}]"
            raw = capture(lambda: _source(ref, source_artifacts, used, path))
            if raw is None:
                continue

            def validate_series():
                _object(raw, SERIES_FIELDS, path)
                _check(type(raw["stage"]) is str and type(raw["family"]) is str,
                       "invalid_type", path, "stage and family strings required")
                key = (raw["stage"], raw["family"])
                _check(key in specs, "unexpected_series", path, "undeclared stage/family source")
                _check(key not in seen, "duplicate_series", path, "duplicate stage/family source")
                seen.add(key)
                is_research = _series(raw, specs[key], expected_identity, grids[key[0]], cutoffs[key[0]], mode, source_artifacts, used, path)
                series.append(raw); research_flags.append(is_research)
            capture(validate_series)
        capture(lambda: _check(seen == set(specs), "missing", "series_sources", "missing stage/family sources: " + repr(sorted(set(specs) - seen))))
    if report["issues"]:
        return report
    report.update(status="validated", causal_eligible=not any(research_flags), source_sha256=dict(used),
                  derivation_profiles=[deepcopy(s["derivation"]) for s in series if s["derivation"] is not None],
                  derived_arithmetic_verified=any(s["derivation"] is not None for s in series),
                  data=dict(contract=deepcopy(data), series=series, mapping=mapping,
                            original_series={(s["derivation"].get("source_ref") or s["derivation"].get("snapshot_ref"))["source_id"]:
                                _document(source_artifacts[(s["derivation"].get("source_ref") or s["derivation"].get("snapshot_ref"))["source_id"]])
                                for s in series if s["derivation"] is not None},
                            grids={stage: [{"delivery_start": s.isoformat(), "delivery_end": e.isoformat()}
                                           for s, e in intervals] for stage, intervals in grids.items()}))
    return report
