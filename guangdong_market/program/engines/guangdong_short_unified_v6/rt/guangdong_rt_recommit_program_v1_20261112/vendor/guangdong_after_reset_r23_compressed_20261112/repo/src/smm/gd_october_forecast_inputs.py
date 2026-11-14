"""Source-backed, solver-free research forecasts for October rolling inputs.

All times are absolute and timezone aware. An eligible forecast may predict
future targets, but its issue, availability and conditioning history must be at
or before T-15. Linear resampling integrates *instantaneous* power; interval
means are subdivided by explicit zero-order hold only. Neither is an official
interpolation rule. This module never reads files or invents a missing tail.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

SNAPSHOT_SCHEMA = 'gd.october.forecast_snapshot.v1'
LINEAGE_SCHEMA = 'gd.october.forecast_lineage.v1'
OBSERVATION_SCHEMA = 'gd.october.forecast_observation.v1'
TRANSFORMATION = 'source_backed_forecast_resampling_v1'
FAMILIES = ('load_mw', 'wind_mw', 'solar_mw', 'boundary_tie_mw')
FIVE = timedelta(minutes=5)
QUARTER = timedelta(minutes=15)
SHANGHAI = ZoneInfo('Asia/Shanghai')


class ForecastInputError(ValueError):
    def __init__(self, code, path, message, missing_spans=()):
        self.code, self.path, self.message = code, path, message
        self.missing_spans = deepcopy(list(missing_spans))
        super().__init__(f'{code} at {path}: {message}')


def _require(condition, code, path, message):
    if not condition:
        raise ForecastInputError(code, path, message)


def _keys(obj, keys, path):
    _require(type(obj) is dict and set(obj) == set(keys), 'invalid_schema', path,
             'exact fields required: ' + ', '.join(sorted(keys)))


def _text(value, path):
    _require(type(value) is str and bool(value.strip()), 'invalid_text', path, 'nonempty text required')
    return value


def _time(value, path):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            pass
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             'invalid_time', path, 'timezone-aware time required')
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(SHANGHAI).isoformat()


def _number(value, path, family):
    try:
        ok = type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        ok = False
    _require(ok, 'invalid_value', path, 'finite numeric MW, never Boolean, required')
    _require(family == 'boundary_tie_mw' or value >= 0, 'invalid_value', path, 'nonnegative MW required')
    return value


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def source_ref(source_id, raw):
    _text(source_id, 'source_id')
    _require(type(raw) is bytes and bool(raw), 'invalid_bytes', source_id, 'nonempty immutable bytes required')
    return dict(source_id=source_id, source_sha256=sha256(raw).hexdigest())


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate_key', key, 'duplicate JSON member')
        result[key] = value
    return result


def _constant(value):
    raise ForecastInputError('invalid_json', 'source', 'nonfinite JSON constant ' + value)


def parse_bytes(raw, path='source'):
    _require(type(raw) is bytes and bool(raw), 'invalid_bytes', path, 'nonempty immutable bytes required')
    _require(len(raw) <= 32 * 1024 * 1024, 'source_limit', path, 'source exceeds 32 MiB bound')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ForecastInputError):
            raise
        raise ForecastInputError('invalid_json', path, 'strict UTF-8 JSON required') from exc
    _require(type(value) is dict, 'invalid_schema', path, 'JSON object required')
    pending, count = [(value, 0)], 0
    while pending:
        node, depth = pending.pop(); count += 1
        _require(depth <= 128 and count <= 1000000, 'source_limit', path, 'JSON nesting/node bound exceeded')
        if type(node) is float:
            _require(math.isfinite(node), 'invalid_json', path, 'nonfinite JSON number')
        elif type(node) is dict:
            pending.extend((child, depth+1) for child in node.values())
        elif type(node) is list:
            pending.extend((child, depth+1) for child in node)
    return value


def reject_future_lineage(value, path='lineage'):
    """Known historical future-truth markers cannot be hidden beneath a wrapper.

    This is a consistency gate, not proof that provider declarations are true.
    """
    if type(value) is dict:
        for key, child in value.items():
            loc = path + '.' + key
            if key in {'uses_future_actual', 'future_actual_in_core_inputs', 'uses_future_truth',
                       'future_truth_conditioned', 'future_actual_conditioned'}:
                _require(child is False, 'future_actual_lineage', loc, 'future-actual lineage is forbidden')
            if key in {'forecast_source_kind', 'forecast_kind', 'kind', 'source_kind'} and type(child) is str:
                forbidden = ('future_truth' in child.lower() or 'future_actual' in child.lower())
                _require(not forbidden, 'future_actual_lineage', loc, 'forbidden source kind: ' + child)
            reject_future_lineage(child, loc)
    elif type(value) is list:
        for index, child in enumerate(value):
            reject_future_lineage(child, f'{path}[{index}]')


def _artifact(ref, artifacts, used, path):
    _keys(ref, {'source_id', 'source_sha256'}, path)
    sid = _text(ref['source_id'], path + '.source_id')
    digest = ref['source_sha256']
    _require(type(digest) is str and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
             'invalid_hash', path, 'lowercase SHA256 required')
    _require(sid in artifacts and type(artifacts[sid]) is bytes and bool(artifacts[sid]),
             'missing_source', path, 'original source bytes missing: ' + sid)
    raw = artifacts[sid]
    _require(sha256(raw).hexdigest() == digest, 'source_hash_mismatch', path, 'source bytes changed: ' + sid)
    _require(sid not in used or used[sid] == digest, 'ambiguous_source', path, 'source id reused')
    used[sid] = digest
    return raw


def _refs(refs, path, allow_empty=False):
    _require(type(refs) is list and (allow_empty or bool(refs)), 'invalid_schema', path, 'explicit source-reference list required')
    keys = []
    for ref in refs:
        _keys(ref, {'source_id', 'source_sha256'}, path)
        keys.append(_text(ref['source_id'], path))
    _require(len(set(keys)) == len(keys), 'duplicate_source', path, 'duplicate source reference')


def _original(ref, artifacts, used, path, *, visiting=(), observed_by=None, checked=None):
    if checked is None:
        checked = set()
    _require(len(visiting) < 64 and len(checked) + len(visiting) < 4096,
             'source_limit', path, 'lineage depth/node bound exceeded')
    _require(ref.get('source_id') not in visiting, 'cyclic_lineage', path, 'cyclic original-source lineage')
    raw = _artifact(ref, artifacts, used, path)
    # Original CSV/opaque provider bytes are retained, never interpreted as an
    # issued forecast automatically. JSON metadata is scanned recursively.
    if raw.lstrip().startswith((b'{', b'[')):
        document = parse_bytes(raw, path)
        reject_future_lineage(document, path)
        _require(document.get('schema_version') != SNAPSHOT_SCHEMA, 'unsupported_nested_forecast', path,
                 'forecast snapshots used as upstream originals need a separate explicit dependency profile')
        child_observed_by = observed_by
        if document.get('schema_version') == LINEAGE_SCHEMA:
            _, a, h = _lineage_header(document, path)
            if observed_by is not None:
                _require(h <= observed_by[0] and a <= observed_by[1], 'unavailable_lineage', path,
                         'transitive lineage information/availability exceeds parent conditioning history')
            child_observed_by = (h, a)
        if document.get('schema_version') == OBSERVATION_SCHEMA:
            observed, published, available = _observation(document, path)
            if observed_by is not None:
                _require(observed <= observed_by[0] and available <= observed_by[1],
                         'unavailable_lineage', path, 'transitive observation exceeds conditioning history')
            child_observed_by = (observed, published)
        def references(value):
            if type(value) is dict:
                if {'source_id', 'source_sha256'} & set(value):
                    _keys(value, {'source_id', 'source_sha256'}, path + '.upstream_reference')
                    yield value
                else:
                    for child in value.values():
                        yield from references(child)
            elif type(value) is list:
                for child in value:
                    yield from references(child)
        cache_key = (ref['source_id'], observed_by)
        if cache_key not in checked:
            for nested in references(document):
                _original(nested, artifacts, used, path + '.upstream',
                          visiting=visiting + (ref['source_id'],), observed_by=child_observed_by, checked=checked)
            checked.add(cache_key)
        return document
    return None


def _observation(raw, path):
    reject_future_lineage(raw, path)
    _keys(raw, {'schema_version', 'observation_id', 'family', 'resource_id', 'observed_at', 'published_at',
                'available_at', 'value_mw', 'source_uri', 'provenance', 'original_refs'}, path)
    _require(raw['schema_version'] == OBSERVATION_SCHEMA, 'invalid_schema', path, 'unsupported observation schema')
    _require(raw['family'] in FAMILIES, 'unsupported_family', path, 'continuous MW families only')
    for field in ('observation_id', 'resource_id', 'source_uri'):
        _text(raw[field], path + '.' + field)
    _keys(raw['provenance'], {'kind', 'uses_future_actual'}, path + '.provenance')
    _require(type(raw['provenance']['kind']) is str and raw['provenance']['kind'] in {'observed_measurement', 'synthetic_research_observation'} and
             raw['provenance']['uses_future_actual'] is False,
             'invalid_provenance', path, 'explicit measured or synthetic observation required')
    observed, published, available = (_time(raw[k], path + '.' + k)
                                     for k in ('observed_at', 'published_at', 'available_at'))
    _require(observed <= published <= available, 'invalid_time', path, 'observation <= publication <= availability required')
    _number(raw['value_mw'], path + '.value_mw', raw['family'])
    _refs(raw['original_refs'], path + '.original_refs', allow_empty=True)
    return observed, published, available


def _lineage_header(raw, path):
    _keys(raw, {'schema_version', 'kind', 'source_uri', 'published_at', 'available_at',
                'information_end', 'uses_future_actual', 'original_refs'}, path)
    _require(raw['schema_version'] == LINEAGE_SCHEMA and type(raw['kind']) is str and raw['kind'] in
             {'observed_history', 'fixed_exogenous_research', 'documented_forecast_inputs'},
             'invalid_schema', path, 'explicit supported lineage schema/kind required')
    _text(raw['source_uri'], path + '.source_uri')
    p, a, h = (_time(raw[k], path + '.' + k) for k in ('published_at', 'available_at', 'information_end'))
    _require(p <= a and h <= a, 'unavailable_lineage', path,
             'lineage publication/information end must not exceed availability')
    _refs(raw['original_refs'], path + '.original_refs')
    return p, a, h


def _lineage(ref, artifacts, used, cutoff, issued, path):
    raw = parse_bytes(_artifact(ref, artifacts, used, path), path)
    reject_future_lineage(raw, path)
    p, a, h = _lineage_header(raw, path)
    _require(a <= issued and h <= cutoff and a <= cutoff,
             'unavailable_lineage', path, 'lineage publication/availability and information end exceed conditioning cutoff/issue')
    observations = []
    for index, original in enumerate(raw['original_refs']):
        loc = f'{path}.original_refs[{index}]'
        doc = _original(original, artifacts, used, loc, observed_by=(h, a))
        if doc is not None and doc.get('schema_version') == OBSERVATION_SCHEMA:
            observed, _, available = _observation(doc, loc)
            _require(observed <= h and available <= a, 'unavailable_lineage', loc, 'observation unavailable to declared lineage')
            observations.append(doc)
    return raw, observations


def _inventory(family, resources):
    _require(type(family) is str and family in FAMILIES, 'unsupported_family', 'family', 'only explicit continuous MW families; never binary states')
    _require(type(resources) is list and bool(resources) and all(type(x) is str and x.strip() for x in resources)
             and len(set(resources)) == len(resources), 'invalid_resources', 'resource_ids', 'nonempty unique resource inventory required')


def _snapshot(ref, artifacts, used, *, full=True):
    path = 'snapshot.' + str(ref.get('source_id', '?')) if type(ref) is dict else 'snapshot'
    raw = parse_bytes(_artifact(ref, artifacts, used, path), path)
    _keys(raw, {'schema_version', 'snapshot_id', 'family', 'unit', 'resource_ids', 'source_uri',
                'issued_at', 'available_at', 'provenance', 'input_sources', 'construction',
                'native_interval_minutes', 'value_semantics', 'coverage', 'rows'}, path)
    _require(raw['schema_version'] == SNAPSHOT_SCHEMA, 'invalid_schema', path, 'unsupported snapshot schema')
    _inventory(raw['family'], raw['resource_ids'])
    _require(raw['unit'] == 'MW', 'invalid_unit', path, 'MW only')
    for key in ('snapshot_id', 'source_uri'):
        _text(raw[key], path + '.' + key)
    issued, available = (_time(raw[k], path + '.' + k) for k in ('issued_at', 'available_at'))
    _require(issued <= available, 'invalid_time', path, 'issue <= availability required')
    if not full:
        return raw, issued, available
    reject_future_lineage(raw, path)
    _keys(raw['provenance'], {'kind', 'uses_future_actual', 'conditioning_information_cutoff'}, path + '.provenance')
    _require(type(raw['provenance']['kind']) is str and raw['provenance']['kind'] in {'issued_forecast', 'synthetic_research_forecast'} and
             raw['provenance']['uses_future_actual'] is False,
             'invalid_provenance', path, 'issued or explicitly synthetic forecast required')
    conditioning = _time(raw['provenance']['conditioning_information_cutoff'], path + '.conditioning_cutoff')
    _require(conditioning <= issued, 'future_conditioning', path, 'conditioning history must end before issue')
    _refs(raw['input_sources'], path + '.input_sources')
    observations = []
    for index, source in enumerate(raw['input_sources']):
        _, obs = _lineage(source, artifacts, used, conditioning, issued, f'{path}.input_sources[{index}]')
        observations.extend(obs)
    _require(type(raw['native_interval_minutes']) is int and raw['native_interval_minutes'] == 15,
             'unsupported_resolution', path, 'this explicit profile supports 15-minute source data only')
    _require(type(raw['value_semantics']) is str and raw['value_semantics'] in {'interval_mean_power', 'instantaneous_power'},
             'invalid_semantics', path, 'explicit interval mean or instantaneous power required')
    coverage = raw['coverage']
    _require(type(coverage) is list and bool(coverage), 'invalid_coverage', path, 'explicit nonempty coverage segments required')
    segments = []
    for i, segment in enumerate(coverage):
        _keys(segment, {'delivery_start', 'delivery_end'}, path + '.coverage')
        s, e = (_time(segment[k], path) for k in ('delivery_start', 'delivery_end'))
        _require(s < e and (e - s) % QUARTER == timedelta(0), 'invalid_coverage', path, 'positive 15-minute aligned segment required')
        for point in (s, e):
            local = point.astimezone(SHANGHAI)
            _require(not local.second and not local.microsecond and local.minute % 15 == 0,
                     'invalid_coverage', path, 'Shanghai quarter-hour source grid required')
        _require(not segments or segments[-1][1] <= s, 'invalid_coverage', path, 'ordered nonoverlapping coverage segments required')
        segments.append((s, e))
    expected = set()
    points = raw['value_semantics'] == 'instantaneous_power'
    for start, end in segments:
        cursor = start
        while cursor < end or (points and cursor == end):
            expected.update((resource, cursor) for resource in raw['resource_ids'])
            cursor += QUARTER
    _require(type(raw['rows']) is list, 'invalid_schema', path, 'explicit rows required')
    values = {}
    for i, row in enumerate(raw['rows']):
        loc = f'{path}.rows[{i}]'
        fields = {'resource_id', 'target_at', 'value'} if points else {'resource_id', 'delivery_start', 'delivery_end', 'value'}
        _keys(row, fields, loc)
        key = (row['resource_id'], _time(row['target_at'] if points else row['delivery_start'], loc))
        _require(type(key[0]) is str and key in expected and key not in values,
                 'coverage_mismatch', loc, 'unexpected/duplicate point or resource')
        if not points:
            _require(_time(row['delivery_end'], loc) - key[1] == QUARTER,
                     'coverage_mismatch', loc, 'source intervals must be exactly 15 minutes')
        values[key] = _number(row['value'], loc, raw['family'])
    _require(set(values) == expected, 'coverage_mismatch', path, 'declared coverage is incomplete; missing source rows cannot be filled')
    construction = raw['construction']
    _keys(construction, {'method', 'settings'}, path + '.construction')
    _require(type(construction['method']) is str and construction['method'] in {'externally_issued', 'persistence_research_v1', 'synthetic_exogenous_harmonic_prior_v1'},
             'unsupported_method', path, 'explicit external or persistence research method required')
    if construction['method'] == 'synthetic_exogenous_harmonic_prior_v1':
        from .gd_october_research_forecast import verify_prior_snapshot
        verify_prior_snapshot(raw, artifacts)
    elif construction['method'] == 'externally_issued':
        _keys(construction['settings'], {'source'}, path + '.construction.settings')
        _text(construction['settings']['source'], path + '.construction.source')
    else:
        _keys(construction['settings'], {'history_cutoff', 'selection', 'extrapolation_is_declared_forecast'}, path + '.construction.settings')
        settings = construction['settings']
        _require(raw['provenance']['kind'] == 'synthetic_research_forecast' and
                 _time(settings['history_cutoff'], path) == conditioning and
                 settings['selection'] == 'latest_available_observation_per_resource' and
                 settings['extrapolation_is_declared_forecast'] is True,
                 'invalid_method', path, 'persistence is explicitly synthetic forecasting, never hidden resampling extrapolation')
        selected = _latest_observations(observations, raw['family'], raw['resource_ids'], conditioning)
        _require(all(value == selected[rid]['value_mw'] for (rid, _), value in values.items()),
                 'forecast_value_mismatch', path, 'persistence rows do not reproduce retained eligible observations')
    return raw, issued, available, segments, values


def _window(delivery_start, information_cutoff, stage):
    start, cutoff = _time(delivery_start, 'delivery_start'), _time(information_cutoff, 'information_cutoff')
    local = start.astimezone(SHANGHAI)
    _require(not local.second and not local.microsecond and local.minute % 15 == 0,
             'invalid_window', 'delivery_start', 'Shanghai quarter-hour aligned start required')
    _require(start - cutoff == QUARTER, 'invalid_cutoff', 'information_cutoff', 'exact T-minus-15 cutoff required')
    _require(type(stage) is str and stage in {'sced', 'lmp'}, 'invalid_stage', 'stage', 'sced/lmp required')
    step = FIVE if stage == 'sced' else QUARTER
    return start, cutoff, tuple((start + i * step, start + (i + 1) * step) for i in range(24 if stage == 'sced' else 8))


def _missing_spans(intervals, segments, family, resources):
    missing = [(s, e) for s, e in intervals if not any(cs <= s and e <= ce for cs, ce in segments)]
    merged = []
    for start, end in missing:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [dict(family=family, resource_ids=list(resources), delivery_start=_iso(s), delivery_end=_iso(e),
                 missing_minutes=int((e-s).total_seconds() // 60)) for s, e in merged]


def _select(snapshot_refs, artifacts, *, family, resource_ids, cutoff):
    _inventory(family, resource_ids)
    _refs(snapshot_refs, 'snapshot_refs', allow_empty=True)
    candidates = []
    # Catalogue headers can be inspected, but unavailable values/lineage never
    # influence selection, resampling or the retained selected input digest.
    for ref in snapshot_refs:
        raw, issued, available = _snapshot(ref, artifacts, {}, full=False)
        _require(raw['family'] == family and raw['resource_ids'] == resource_ids,
                 'inventory_mismatch', 'snapshot_refs', 'catalogue must name exactly this family and resource inventory')
        if issued <= cutoff and available <= cutoff:
            candidates.append((issued, available, ref))
    _require(bool(candidates), 'no_eligible_snapshot', family, 'no forecast issued and available at T-minus-15')
    latest = max((issued, available) for issued, available, _ in candidates)
    selected = [ref for issued, available, ref in candidates if (issued, available) == latest]
    _require(len(selected) == 1, 'ambiguous_snapshot', family, 'same latest issue/availability requires explicit disambiguation')
    used = {}
    parsed = _snapshot(selected[0], artifacts, used)
    return selected[0], parsed, used


def _resample(parsed, intervals, family, resources):
    raw, _, _, segments, values = parsed
    missing = _missing_spans(intervals, segments, family, resources)
    if missing:
        raise ForecastInputError('missing_forecast_coverage', family,
            'latest eligible snapshot does not cover ' + '; '.join(x['delivery_start'] + ' to ' + x['delivery_end'] for x in missing), missing)
    rows = []
    for resource in resources:
        for start, end in intervals:
            segment_start = next(s for s, e in segments if s <= start and end <= e)
            coarse = segment_start + ((start - segment_start) // QUARTER) * QUARTER
            if raw['value_semantics'] == 'interval_mean_power':
                value = values[resource, coarse]
            else:
                # Exact mean of the piecewise-linear function over [start,end).
                # The 5/15-minute grid lies wholly inside a single coarse cell.
                left, right = values[resource, coarse], values[resource, coarse + QUARTER]
                weight = (((start-coarse).total_seconds() + (end-coarse).total_seconds()) / 2) / 900
                value = (1 - weight) * left + weight * right
                _number(value, 'derived value', family)
            rows.append(dict(resource_id=resource, delivery_start=_iso(start), delivery_end=_iso(end), value=value))
    return rows


def build_forecast_series(*, snapshot_refs, source_artifacts, identity, family, resource_ids,
                          delivery_start, information_cutoff, stage='sced'):
    """Select latest eligible revision and return a byte-bound research series.

    A short newer revision fails with exact missing spans; no silent fallback to
    older data. Multi-day coverage belongs explicitly to the selected snapshot.
    Output is compatible with RT contract / SourceArtifact in research mode,
    not native 5-minute data and not official price or LMP mapping evidence.
    """
    start, cutoff, intervals = _window(delivery_start, information_cutoff, stage)
    _require(type(source_artifacts) is dict, 'invalid_sources', 'source_artifacts', 'explicit source bytes dictionary required')
    _keys(identity, {'run_id', 'window_id', 'selected_dispatch_id', 'selected_dispatch_sha256'}, 'identity')
    for key, value in identity.items():
        _text(value, 'identity.' + key)
    digest = identity['selected_dispatch_sha256']
    _require(len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'invalid_hash', 'identity', 'selected source SHA256 required')
    try:
        ref, parsed, used = _select(snapshot_refs, source_artifacts, family=family, resource_ids=resource_ids, cutoff=cutoff)
    except ForecastInputError as exc:
        if exc.code == 'no_eligible_snapshot':
            raise ForecastInputError(exc.code, exc.path, exc.message,
                                     _missing_spans(intervals, (), family, resource_ids)) from exc
        raise
    raw, issued, available, _, _ = parsed
    rows = _resample(parsed, intervals, family, resource_ids)
    method = 'zero_order_hold_interval_means' if raw['value_semantics'] == 'interval_mean_power' else 'piecewise_linear_interval_mean'
    series = dict(schema_version='gd.october_rt.series.v1', identity=deepcopy(identity), stage=stage,
        family=family, unit='MW', resource_ids=list(resource_ids), source_uri=raw['source_uri'],
        provenance=dict(kind='derived_simulation_5min' if stage == 'sced' else 'synthetic_research',
                        uses_future_actual=False, published_at=_iso(issued), available_at=_iso(available)),
        native_interval_minutes=15, transformation=TRANSFORMATION,
        derivation=dict(method=method, snapshot_ref=deepcopy(ref),
            information_cutoff=_iso(cutoff), value_semantics='interval_mean_power',
            classification='causal_conditioned_research_simulation', is_measurement=False,
            official_interpolation_rule=False), rows=rows)
    return dict(series=series, series_document=canonical_bytes(series),
        source_artifacts={sid: source_artifacts[sid] for sid in used},
        audit=dict(selected_snapshot_ref=deepcopy(ref), selection_policy='latest_issue_then_availability_at_cutoff',
            information_cutoff=_iso(cutoff), conditioning_information_cutoff=raw['provenance']['conditioning_information_cutoff'],
            source_sha256=used, target_start=_iso(start), target_end=_iso(intervals[-1][1]),
            method=method, causal_information_boundary_verified=True, causal_eligible=False,
            research_only=True, uses_future_actual=False, source_authenticity_verified=False,
            native_solver_verified=False, official_price=None, settlement=None, cash=None))


def verify_forecast_series(raw, *, intervals, cutoff, mode, sources, used):
    """RT contract calls this to recompute from source bytes, not adapter flags."""
    _require(mode == 'research', 'research_only', 'series', 'forecast resampling remains research-only')
    derivation = raw['derivation']
    _keys(derivation, {'method', 'snapshot_ref', 'information_cutoff', 'value_semantics', 'classification',
                       'is_measurement', 'official_interpolation_rule'}, 'series.derivation')
    _require(_time(derivation['information_cutoff'], 'derivation.cutoff') <= _time(cutoff, 'stage.cutoff'),
             'unavailable_at_cutoff', 'series', 'derivation cutoff exceeds stage cutoff')
    built = build_forecast_series(snapshot_refs=[derivation['snapshot_ref']], source_artifacts=sources,
        identity=raw['identity'], family=raw['family'], resource_ids=raw['resource_ids'],
        delivery_start=intervals[0][0], information_cutoff=derivation['information_cutoff'], stage=raw['stage'])
    expected = built['series']
    # ISO-equivalent timestamp representations are acceptable; everything else
    # including arithmetic, provenance and method must match source recompute.
    normalized = deepcopy(raw)
    for row in normalized['rows']:
        for key in ('delivery_start', 'delivery_end'):
            row[key] = _iso(_time(row[key], key))
    for key in ('published_at', 'available_at'):
        normalized['provenance'][key] = _iso(_time(normalized['provenance'][key], key))
    normalized['derivation']['information_cutoff'] = _iso(_time(derivation['information_cutoff'], 'cutoff'))
    _require(normalized == expected, 'derived_value_mismatch', 'series', 'forecast series differs from independently recomputed source')
    used.update(built['audit']['source_sha256'])
    return True


def _latest_observations(observations, family, resources, cutoff):
    selected = {}
    ids = set()
    for row in observations:
        _observation(row, 'observation')
        _require(row['observation_id'] not in ids, 'ambiguous_observation', 'observation', 'duplicate observation ID')
        ids.add(row['observation_id'])
        _require(row['family'] == family and row['resource_id'] in resources,
                 'inventory_mismatch', 'observation', 'observation outside declared family/resource inventory')
        if _time(row['observed_at'], 'observed_at') > cutoff or _time(row['available_at'], 'available_at') > cutoff:
            continue
        rid = row['resource_id']
        rank = (_time(row['observed_at'], 'observed_at'), _time(row['available_at'], 'available_at'))
        if rid in selected:
            previous = selected[rid]
            prior = (_time(previous['observed_at'], 'observed_at'), _time(previous['available_at'], 'available_at'))
            _require(prior != rank, 'ambiguous_observation', rid, 'same latest observation/availability is ambiguous')
            if prior > rank:
                continue
        selected[rid] = row
    _require(set(selected) == set(resources), 'missing_observation', 'observations', 'eligible history required for every resource')
    return selected


def produce_persistence_research_snapshot(*, observation_refs, source_artifacts, snapshot_id, family,
        resource_ids, issued_at, available_at, history_cutoff, target_start, target_end, method):
    """Explicit synthetic persistence forecast; never implicit last-day fill.

    The caller chooses the method and target horizon. Only the latest supplied
    observation known at history_cutoff is retained per resource. Future rows
    or later availability cannot change either forecasts or selected bytes.
    This method is a transparent research baseline, not an accuracy claim.
    """
    _require(method == 'persistence_research_v1', 'unsupported_method', 'method', 'explicit persistence_research_v1 selection required')
    _inventory(family, resource_ids)
    _text(snapshot_id, 'snapshot_id')
    issue, available, history = (_time(x, name) for x, name in
        ((issued_at, 'issued_at'), (available_at, 'available_at'), (history_cutoff, 'history_cutoff')))
    _require(history <= issue <= available, 'invalid_time', 'producer', 'history cutoff <= issue <= availability required')
    start, end = _time(target_start, 'target_start'), _time(target_end, 'target_end')
    _require(start >= issue and start < end and (end-start) % QUARTER == timedelta(0),
             'invalid_coverage', 'producer', 'positive future 15-minute target horizon required')
    _refs(observation_refs, 'observation_refs')
    observations, references = [], {}
    for ref in observation_refs:
        raw = parse_bytes(_artifact(ref, source_artifacts, {}, 'observation_ref'))
        _observation(raw, 'observation')
        observations.append(raw)
        references[raw['observation_id']] = ref
    selected = _latest_observations(observations, family, resource_ids, history)
    selected_refs = [deepcopy(references[selected[rid]['observation_id']]) for rid in resource_ids]
    used = {}
    for ref in selected_refs:
        raw = _original(ref, source_artifacts, used, 'selected_observation')
        for original in raw['original_refs']:
            _original(original, source_artifacts, used, 'selected_observation.original')
    lineage = dict(schema_version=LINEAGE_SCHEMA, kind='observed_history',
        source_uri='research://persistence/' + snapshot_id, published_at=_iso(history), available_at=_iso(history),
        information_end=_iso(history), uses_future_actual=False, original_refs=selected_refs)
    lineage_id = snapshot_id + ':conditioning'; lineage_bytes = canonical_bytes(lineage)
    _require(lineage_id not in source_artifacts and snapshot_id not in source_artifacts,
             'duplicate_source', 'producer', 'output source IDs must not overwrite original bytes')
    artifacts = {sid: source_artifacts[sid] for sid in used}
    artifacts[lineage_id] = lineage_bytes
    rows = []
    cursor = start
    while cursor < end:
        for rid in resource_ids:
            rows.append(dict(resource_id=rid, delivery_start=_iso(cursor), delivery_end=_iso(cursor+QUARTER),
                             value=selected[rid]['value_mw']))
        cursor += QUARTER
    snapshot = dict(schema_version=SNAPSHOT_SCHEMA, snapshot_id=snapshot_id, family=family, unit='MW',
        resource_ids=list(resource_ids), source_uri='research://persistence/' + snapshot_id,
        issued_at=_iso(issue), available_at=_iso(available),
        provenance=dict(kind='synthetic_research_forecast', uses_future_actual=False,
                        conditioning_information_cutoff=_iso(history)),
        input_sources=[source_ref(lineage_id, lineage_bytes)],
        construction=dict(method=method, settings=dict(history_cutoff=_iso(history),
            selection='latest_available_observation_per_resource', extrapolation_is_declared_forecast=True)),
        native_interval_minutes=15, value_semantics='interval_mean_power',
        coverage=[dict(delivery_start=_iso(start), delivery_end=_iso(end))], rows=rows)
    raw = canonical_bytes(snapshot); artifacts[snapshot_id] = raw; ref = source_ref(snapshot_id, raw)
    _snapshot(ref, artifacts, {})
    return dict(snapshot_ref=ref, snapshot_document=raw, source_artifacts=artifacts,
                selected_observation_ids=[selected[rid]['observation_id'] for rid in resource_ids],
                research_only=True, source_authenticity_verified=False, uses_future_actual=False)


def rolling_day_requirements(day_start, *, window_count=96):
    start = _time(day_start, 'day_start'); local = start.astimezone(SHANGHAI)
    _require(local.hour == local.minute == local.second == local.microsecond == 0,
             'invalid_day', 'day_start', 'Shanghai midnight required')
    _require(type(window_count) is int and 1 <= window_count <= 96, 'invalid_count', 'window_count', '1..96 windows required')
    windows = []
    for index in range(window_count):
        t = start + index * QUARTER
        windows.append(dict(window_index=index, delivery_start=_iso(t), information_cutoff=_iso(t-QUARTER),
                            forecast_end=_iso(t+timedelta(hours=2)), terminal_boundary_at=_iso(t+timedelta(hours=2))))
    return dict(timezone='Asia/Shanghai', interval_convention='left_closed_right_open', windows=windows,
        continuous_forecasts_required=dict(delivery_start=_iso(start), delivery_end=windows[-1]['forecast_end']),
        effective_5min_plan_required=dict(delivery_start=_iso(start), delivery_end=windows[-1]['forecast_end']),
        seed_commands_required=dict(delivery_start=_iso(start-QUARTER), delivery_end=_iso(start)),
        initial_observation_no_later_than=_iso(start-QUARTER), independent_first_delivery_boundary_at=_iso(start),
        terminal_boundaries_required=[w['terminal_boundary_at'] for w in windows],
        binary_state_interpolation_allowed=False, day_repetition_allowed=False, implicit_tail_extrapolation_allowed=False)


def assess_multiday_forecast_inputs(*, day_start, inventories, source_artifacts, window_count=96):
    """Explicit four-family multi-day interface with exact per-window gaps.

    This only checks continuous forecasts. It does not fabricate effective
    plans, boundary states, known dispatch, mappings, reserve or AGC policies.
    """
    requirements = rolling_day_requirements(day_start, window_count=window_count)
    _keys(inventories, FAMILIES, 'inventories')
    for family, spec in inventories.items():
        _keys(spec, {'resource_ids', 'snapshot_refs'}, 'inventories.' + family)
        _inventory(family, spec['resource_ids'])
    results = []
    for window in requirements['windows']:
        _, cutoff, intervals = _window(window['delivery_start'], window['information_cutoff'], 'sced')
        for family, spec in inventories.items():
            row = dict(window_index=window['window_index'], family=family, status='unavailable')
            try:
                ref, parsed, used = _select(spec['snapshot_refs'], source_artifacts,
                    family=family, resource_ids=spec['resource_ids'], cutoff=cutoff)
                _resample(parsed, intervals, family, spec['resource_ids'])
                row.update(status='covered_research_input', selected_snapshot_ref=ref, source_sha256=used)
            except ForecastInputError as exc:
                spans = (_missing_spans(intervals, (), family, spec['resource_ids'])
                         if exc.code == 'no_eligible_snapshot' else exc.missing_spans)
                row.update(code=exc.code, path=exc.path, message=exc.message, missing_spans=spans)
            results.append(row)
    ready = all(r['status'] == 'covered_research_input' for r in results)
    return dict(schema_version='gd.october.multiday_forecast_readiness.v1', requirements=requirements,
        forecast_coverage_complete=ready, windows=results, ready_for_native_day=False,
        other_required_inputs=['explicit_5min_effective_plans', 'dated_physical_policies_and_resource_mapping',
            'known_dispatch_reference', 'initial_observation_and_seed_commands', 'independent_first_delivery_boundary',
            'explicit_per_window_terminal_boundaries', 'reserve_and_AGC_inputs'],
        research_only=True, source_authenticity_verified=False, official_price=None, settlement=None, cash=None)
