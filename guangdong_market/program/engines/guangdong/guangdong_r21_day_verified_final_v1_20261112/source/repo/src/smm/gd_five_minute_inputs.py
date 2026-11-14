"""Pure, audited 15-to-5-minute simulation input conversion; never a solver.

Interval means use zero-order hold, conserving each supplied quarter-hour's
energy. Instantaneous samples use linear interpolation and retain sample
semantics: their interpolant integral is not measured energy. All outputs stay
research-only. Bytes, explicit semantics, physical bounds and timestamps are
required; this module never reads files, invents endpoints or selects a vintage.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo


class FiveMinuteInputError(ValueError):
    """Incomplete, inconsistent, ambiguous or unsupported conversion input."""


RESULT_SCHEMA = 'gd.five_minute.derivation.v1'
SERIES_SCHEMA = 'gd.october_rt.series.v1'
SAMPLES_SCHEMA = 'gd.five_minute.samples.v1'
SHANGHAI = ZoneInfo('Asia/Shanghai')
QUARTER = timedelta(minutes=15)
FIVE = timedelta(minutes=5)
_BASE_FIELDS = {'schema_version', 'identity', 'stage', 'family', 'unit',
                'resource_ids', 'source_uri', 'provenance',
                'native_interval_minutes', 'transformation', 'derivation', 'rows'}
_KINDS = {'issued_forecast', 'declared_boundary', 'observed_actual',
          'synthetic_research', 'offline_future_actual_research'}


def _require(ok, message):
    if not ok:
        raise FiveMinuteInputError(message)


def _keys(value, keys, name):
    _require(isinstance(value, dict) and set(value) == set(keys),
             f'{name}: exact fields required: {sorted(keys)}')


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), f'{name}: nonempty text required')
    return value


def _number(value, name):
    _require(type(value) in {int, float}, f'{name}: finite number, not bool, required')
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise FiveMinuteInputError(f'{name}: finite number required') from exc
    _require(math.isfinite(result), f'{name}: finite number required')
    return result


def _sum(values):
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise FiveMinuteInputError('nonfinite cumulative energy/integral') from exc
    _require(math.isfinite(result), 'nonfinite cumulative energy/integral')
    return result


def _sha(value, name):
    _require(isinstance(value, str) and len(value) == 64
             and all(c in '0123456789abcdef' for c in value), f'{name}: lowercase SHA256 required')
    return value


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise FiveMinuteInputError('finite JSON-compatible data required') from exc


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, f'duplicate JSON member: {key}')
        result[key] = value
    return result


def _constant(value):
    raise FiveMinuteInputError(f'nonfinite JSON number: {value}')


def _time(value, name):
    _require(isinstance(value, str), f'{name}: timezone-aware ISO string required')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FiveMinuteInputError(f'{name}: invalid ISO timestamp') from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             f'{name}: timezone-aware timestamp required')
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(SHANGHAI).isoformat()


def _aligned(value, name):
    local = value.astimezone(SHANGHAI)
    _require(local.minute % 15 == 0 and not local.second and not local.microsecond,
             f'{name}: Shanghai quarter-hour alignment required')


def _source(source_artifact, expected_source_sha256, value_semantics):
    _require(type(source_artifact) is bytes and bool(source_artifact), 'immutable nonempty source bytes required')
    digest = hashlib.sha256(source_artifact).hexdigest()
    _require(digest == _sha(expected_source_sha256, 'expected_source_sha256'), 'source bytes/hash mismatch')
    try:
        raw_text = source_artifact.decode('utf-8')
        data = json.loads(raw_text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, FiveMinuteInputError):
            raise
        raise FiveMinuteInputError('source must be strict UTF-8 JSON') from exc
    _keys(data, _BASE_FIELDS, 'source')
    expected_schema = SERIES_SCHEMA if value_semantics == 'interval_mean_power' else SAMPLES_SCHEMA
    _require(data['schema_version'] == expected_schema, 'source schema does not match declared value semantics')
    _require(data['stage'] == 'sced', 'conversion is for SCED research inputs only; no LMP resampling')
    _require(data['unit'] == 'MW', 'explicit MW power unit required; no implicit unit conversion')
    _require(type(data['native_interval_minutes']) is int and data['native_interval_minutes'] == 15,
             'source native_interval_minutes must be 15')
    _require(data['transformation'] == 'none' and data['derivation'] is None,
             'source must be the original untransformed 15-minute artifact with derivation=null')
    _keys(data['identity'], {'run_id', 'window_id', 'selected_dispatch_id', 'selected_dispatch_sha256'}, 'identity')
    for name, value in data['identity'].items():
        _sha(value, name) if name.endswith('sha256') else _text(value, name)
    family = _text(data['family'], 'family')
    _require(family in {'load_mw', 'wind_mw', 'solar_mw', 'net_load_mw'} or family.startswith('boundary_'),
             'explicit supported power family required')
    _text(data['source_uri'], 'source_uri')
    ids = data['resource_ids']
    _require(isinstance(ids, list) and bool(ids) and all(isinstance(x, str) and x.strip() for x in ids)
             and len(set(ids)) == len(ids), 'nonempty unique explicit resource_ids required')
    _require(isinstance(data['rows'], list) and bool(data['rows']), 'source rows required')
    _keys(data['provenance'], {'kind', 'uses_future_actual', 'published_at', 'available_at'}, 'provenance')
    p = data['provenance']
    _require(isinstance(p['kind'], str) and p['kind'] in _KINDS, 'unknown source kind; no relabeling or inferred provenance')
    _require(type(p['uses_future_actual']) is bool, 'uses_future_actual must be an explicit boolean')
    published = _time(p['published_at'], 'published_at')
    available = _time(p['available_at'], 'available_at')
    _require(published <= available, 'source available_at precedes published_at')
    if p['kind'] in {'issued_forecast', 'declared_boundary', 'observed_actual'}:
        _require(not p['uses_future_actual'], 'future actual cannot be relabeled as issued forecast, boundary or observed_actual')
    if p['kind'] == 'offline_future_actual_research':
        _require(p['uses_future_actual'], 'offline_future_actual_research must preserve future-actual risk')
    return data, raw_text, digest, published, available


def _bounds(bounds_by_resource, data):
    _keys(bounds_by_resource, data['resource_ids'], 'bounds_by_resource')
    limits = {}
    for rid in data['resource_ids']:
        bounds = bounds_by_resource[rid]
        _keys(bounds, {'minimum_mw', 'maximum_mw', 'source'}, f'bounds.{rid}')
        low = _number(bounds['minimum_mw'], f'{rid}.minimum_mw')
        high = _number(bounds['maximum_mw'], f'{rid}.maximum_mw')
        _text(bounds['source'], f'{rid}.bounds.source')
        _require(low <= high, f'{rid}: minimum exceeds maximum')
        if data['family'] in {'load_mw', 'wind_mw', 'solar_mw'}:
            _require(low >= 0, f'{rid}: load/wind/solar lower bound must be nonnegative')
        limits[rid] = (low, high)
    return limits


def _rows(data, semantics, start, end, limits):
    means = semantics == 'interval_mean_power'
    required = {'resource_id', 'delivery_start', 'delivery_end', 'value'} if means else {'resource_id', 'timestamp', 'value'}
    indexed = {rid: {} for rid in data['resource_ids']}
    for index, row in enumerate(data['rows']):
        _keys(row, required, f'rows[{index}]')
        rid = row['resource_id']
        _require(isinstance(rid, str) and rid in indexed, 'unknown resource in source rows')
        stamp = _time(row['delivery_start'] if means else row['timestamp'], f'rows[{index}].timestamp')
        _aligned(stamp, 'source timestamp')
        if means:
            stop = _time(row['delivery_end'], f'rows[{index}].delivery_end')
            _require(stop - stamp == QUARTER, 'each source mean must cover exactly 15 elapsed minutes')
        _require(stamp not in indexed[rid], f'{rid}: duplicate source timestamp, including equivalent timezone representations')
        value = _number(row['value'], f'rows[{index}].value')
        low, high = limits[rid]
        _require(low <= value <= high, f'{rid}: source value outside explicit physical bounds; no clipping')
        indexed[rid][stamp] = (row['value'], index)
    expected = []
    stamp = start
    while stamp < end or (not means and stamp == end):
        expected.append(stamp)
        stamp += QUARTER
    for rid, points in indexed.items():
        _require(list(points) == expected,
                 f'{rid}: exact ordered source coverage required; missing/extra/unsorted timestamps or missing endpoint; no extrapolation')
    return indexed, expected


def convert_five_minute_inputs(source_artifact: bytes, *, expected_source_sha256: str,
                               source_id: str, value_semantics: str, method: str,
                               bounds_by_resource: dict, information_cutoff: str,
                               derivation_available_at: str, requested_start: str,
                               requested_end: str) -> dict:
    """Return a detached JSON-compatible conversion with full source and audit.

    ``interval_mean_power`` requires ``zero_order_hold`` and quarter-hour
    interval rows. ``instantaneous_sample`` requires ``linear`` and timestamp
    rows including both endpoints. Requests must match the source's entire
    coverage. A single immutable source_id/hash is one declared vintage, never
    a row-wise splice of forecasts. Every resource needs finite explicit bounds.

    An issued forecast/boundary and its derivation must already be available at
    the explicit cutoff. Hindsight sources may be unavailable then, but remain
    offline research and preserve their future-actual flag. Actuals cannot be
    relabeled as forecasts through an argument to this function.
    """
    _require(isinstance(value_semantics, str) and value_semantics in {'interval_mean_power', 'instantaneous_sample'},
             'explicit interval_mean_power or instantaneous_sample semantics required')
    expected_method = 'zero_order_hold' if value_semantics == 'interval_mean_power' else 'linear'
    _require(method == expected_method, f'{value_semantics} requires method={expected_method}; ordinary linear interpolation does not conserve interval-mean energy')
    _text(source_id, 'source_id')
    data, raw_text, digest, published, available = _source(source_artifact, expected_source_sha256, value_semantics)
    cutoff = _time(information_cutoff, 'information_cutoff')
    derived_at = _time(derivation_available_at, 'derivation_available_at')
    start = _time(requested_start, 'requested_start')
    end = _time(requested_end, 'requested_end')
    _aligned(start, 'requested_start'); _aligned(end, 'requested_end')
    _require(start < end and (end-start) % QUARTER == timedelta(0), 'positive whole-quarter requested range required')
    # Prevent accidental unbounded allocation before validating the actual rows.
    count = int((end-start) / QUARTER)
    per_resource = count if value_semantics == 'interval_mean_power' else count + 1
    _require(len(data['rows']) == per_resource * len(data['resource_ids']),
             'source row count does not exactly cover requested range; missing endpoint or missing interval')
    _require(derived_at >= available, 'derivation cannot be available before its source')
    p = data['provenance']
    if p['kind'] in {'issued_forecast', 'declared_boundary'}:
        _require(available <= cutoff and derived_at <= cutoff,
                 'forecast/boundary source or derivation was unavailable at information cutoff')
    limits = _bounds(bounds_by_resource, data)
    indexed, points = _rows(data, value_semantics, start, end, limits)
    if p['kind'] == 'observed_actual':
        _require(end <= published and available <= cutoff,
                 'future actual cannot be labeled observed_actual; use explicit offline_future_actual_research')
    output_rows, energy_rows, resource_reports = [], [], {}
    means = value_semantics == 'interval_mean_power'
    for rid in data['resource_ids']:
        source_values = [indexed[rid][stamp][0] for stamp in points]
        generated = []
        resource_energy = []
        for i in range(count):
            stamp = points[i]
            left, source_index = indexed[rid][stamp]
            if means:
                small = [left, left, left]
            else:
                right = indexed[rid][points[i+1]][0]
                # Convex combinations preserve per-resource bounds, including
                # signed net-load bounds. No clipping or total-load adjustment.
                small = ([left, left, left] if left == right else
                         [left, (2. / 3.) * left + (1. / 3.) * right,
                          (1. / 3.) * left + (2. / 3.) * right])
            for k, value in enumerate(small):
                _require(limits[rid][0] <= _number(value, 'derived value') <= limits[rid][1],
                         f'{rid}: numerical interpolation escaped physical bounds')
                ts = stamp + k * FIVE
                row = dict(resource_id=rid, value=value)
                if means:
                    row.update(delivery_start=_iso(ts), delivery_end=_iso(ts+FIVE))
                else:
                    row['timestamp'] = _iso(ts)
                output_rows.append(row); generated.append(value)
            if means:
                original_energy = float(left) * .25
                derived_energy = _sum(float(value) / 12. for value in small)
                deviation = derived_energy - original_energy
                _require(math.isclose(derived_energy, original_energy, rel_tol=1e-12, abs_tol=1e-10),
                         'energy conservation exceeded numerical tolerance')
                item = dict(resource_id=rid, source_row_index=source_index,
                            delivery_start=_iso(stamp), delivery_end=_iso(stamp+QUARTER),
                            source_energy_mwh=original_energy, derived_energy_mwh=derived_energy,
                            energy_deviation_mwh=deviation)
            else:
                # Trapezoids integrate the chosen interpolant, not an unknown
                # real trajectory. A source sample does not define 0.25*x MWh.
                four = [*small, right]
                derived_energy = _sum((float(a)/2. + float(b)/2.) / 12.
                                          for a, b in zip(four, four[1:]))
                item = dict(resource_id=rid, source_row_index=source_index,
                            delivery_start=_iso(stamp), delivery_end=_iso(stamp+QUARTER),
                            source_energy_mwh=None, derived_energy_mwh=derived_energy,
                            energy_deviation_mwh=None)
            energy_rows.append(item); resource_energy.append(item)
        if not means:
            last = indexed[rid][end][0]
            output_rows.append(dict(resource_id=rid, timestamp=_iso(end), value=last))
            generated.append(last)
        source_total = _sum(x['source_energy_mwh'] for x in resource_energy) if means else None
        derived_total = _sum(x['derived_energy_mwh'] for x in resource_energy)
        _number(derived_total, 'derived integral')
        if means:
            _require(math.isclose(derived_total, source_total, rel_tol=1e-12, abs_tol=1e-10),
                     'total energy conservation exceeded numerical tolerance')
        resource_reports[rid] = dict(source_min_mw=min(source_values), source_max_mw=max(source_values),
            derived_min_mw=min(generated), derived_max_mw=max(generated),
            max_source_point_deviation_mw=0., source_total_mwh=source_total,
            derived_total_mwh=derived_total, total_energy_deviation_mwh=(derived_total-source_total if means else None))
    derivation = dict(method=method, source_ref=dict(source_id=source_id, source_sha256=digest),
                      value_semantics=value_semantics, classification='derived_simulation',
                      is_measurement=False, official_interpolation_rule=False)
    converted = {k: deepcopy(v) for k, v in data.items() if k not in {'rows', 'provenance', 'transformation'}}
    converted.update(rows=output_rows, transformation='external_derived_simulation_5min',
        provenance=dict(kind='derived_simulation_5min', uses_future_actual=p['uses_future_actual'],
                        published_at=_iso(derived_at), available_at=_iso(derived_at)), derivation=derivation)
    request = dict(source_id=source_id, value_semantics=value_semantics, method=method,
        bounds_by_resource=deepcopy(bounds_by_resource), information_cutoff=information_cutoff,
        derivation_available_at=derivation_available_at, requested_start=requested_start, requested_end=requested_end)
    result = dict(schema_version=RESULT_SCHEMA, classification='derived_simulation',
        is_measurement=False, official_interpolation_rule=False, certified=False,
        causal_online_forecast=False, interval_minutes=5, value_semantics=value_semantics,
        source_sha256=digest, source_artifact_utf8=raw_text, original_source=deepcopy(data),
        conversion_request=request, series_source=converted if means else None,
        sample_source=None if means else converted,
        audit=dict(timezone='Asia/Shanghai', source_authenticity_verified=False,
            source_forecast_version=dict(source_id=source_id, source_sha256=digest),
            source_published_at=p['published_at'], source_available_at=p['available_at'],
            source_available_by_cutoff=available <= cutoff,
            source_information_causal_eligible=(p['kind'] in {'issued_forecast', 'declared_boundary'}
                                               and not p['uses_future_actual'] and derived_at <= cutoff),
            uses_future_actual=p['uses_future_actual'], source_kind=p['kind'], research_only=True,
            original_interval_energy_known_from_declared_means=means,
            original_interval_energy_measured=False, energy_preservation_claim=means,
            integral_basis='declared_interval_means' if means else 'linear_interpolant_only_not_measured',
            energy_tolerance=dict(relative=1e-12, absolute_mwh=1e-10),
            source_rows=len(data['rows']), derived_rows=len(output_rows),
            interpolation_makes_no_measurement_claim=True, no_extrapolation=True,
            bounds_by_resource=deepcopy(bounds_by_resource), resource_reports=resource_reports,
            per_interval_energy=energy_rows))
    # Strict finite serialization also catches overflow in cumulative diagnostics.
    result['content_sha256'] = hashlib.sha256(_canonical(result).encode('utf-8')).hexdigest()
    return result


def validate_five_minute_result(result: dict, *, expected_source_sha256: str) -> dict:
    """Recompute from retained original bytes, checking mutation and relabeling.

    The expected source hash must come from the caller's trusted selection, not
    from the possibly mutated result. This checks consistency, not authenticity.
    """
    _require(isinstance(result, dict), 'result must be a mapping')
    try:
        raw = result['source_artifact_utf8']
        request = result['conversion_request']
        _require(isinstance(raw, str) and isinstance(request, dict), 'retained source/request missing')
        expected = convert_five_minute_inputs(raw.encode('utf-8'), expected_source_sha256=expected_source_sha256,
                                             **request)
        _require(_canonical(result) == _canonical(expected), 'derived result mutation or relabeling detected')
    except (KeyError, TypeError) as exc:
        raise FiveMinuteInputError('malformed retained source/conversion request') from exc
    return deepcopy(expected)
