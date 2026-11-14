"""Verified source bytes -> an unsolved October 24 x 5-minute physical model.

No solver, file lookup, historical fallback, inferred parameter, load-netting,
price mapping, or dispatch selection occurs here. The selected-dispatch identity
is a source/replay binding, not a claim that this unsolved model reproduces it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
import math

from .gd_energy_core import EnergyInputError
from .gd_october_rt_contract import validate_october_rt_contract
from .gd_october_sced import build_october_sced, october_sced_horizon, assert_october_integrity

MAPPING_SCHEMA = 'gd.october.physical_resource_mapping.v1'
BRIDGE_SCHEMA = 'gd.october.physical_input_bridge.v1'
FAMILIES = ('load_mw', 'wind_mw', 'solar_mw', 'boundary_tie_mw')
IDENTITY_KEYS = {'run_id', 'window_id', 'selected_dispatch_id', 'selected_dispatch_sha256'}
CORE_KEYS = {'commitment', 'reserves', 'agc', 'storage_spec', 'implementation_sources',
             'buses', 'thermal', 'lines', 'sections', 'reference_buses',
             'penalties_yuan_per_mwh', 'source_notes', 'balance_policy',
             'shortfall_cap_policy', 'renewable_mode', 'renewable_dispatch_profile', 'renewable_bid_yuan_per_mwh',
             'renewable_curtailment_penalty_yuan_per_mwh'}
PHYSICAL_SOURCES = {'thermal', 'network', 'storage', 'reserves', 'agc',
                    'effective_commitment', 'initial_state', 'terminal_state',
                    'penalties', 'period_mapping'}


@dataclass(frozen=True)
class _InputBinding:
    identity: tuple
    source_audit_sha256: str
    physical_model_sha256: str


class OctoberInputBridgeError(EnergyInputError):
    """Missing, inconsistent or unsupported source-to-model wiring."""


def _require(condition, message):
    if not condition:
        raise OctoberInputBridgeError(message)


def _keys(value, expected, label):
    _require(type(value) is dict and set(value) == set(expected),
             f'{label}: exact fields/coverage required: {sorted(map(str, expected))}')


def _text(value, label):
    _require(type(value) is str and bool(value.strip()), f'{label}: explicit source/text required')
    return value


def _number(value, label):
    _require(type(value) in (int, float), f'{label}: finite numeric value, not Boolean, required')
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    _require(finite, f'{label}: finite value required')
    return value


def _time(value, label):
    _require(type(value) is str, f'{label}: timezone-aware ISO text required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise OctoberInputBridgeError(f'{label}: invalid time') from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             f'{label}: timezone-aware time required')
    return parsed.astimezone(timezone.utc)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        _require(key not in out, f'duplicate JSON member: {key}')
        out[key] = value
    return out


def _constant(value):
    raise OctoberInputBridgeError('nonfinite JSON constant: ' + value)


def _json(raw, label):
    _require(type(raw) is bytes and bool(raw), f'{label}: nonempty immutable bytes required')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, OctoberInputBridgeError):
            raise
        raise OctoberInputBridgeError(f'{label}: strict UTF-8 JSON required') from exc
    _require(type(result) is dict, f'{label}: JSON object required')
    return result


def _identity(value, expected, label):
    _keys(value, IDENTITY_KEYS, label)
    _require(value == expected, f'{label}: run/window/selected identity mismatch')


def _source(ref, artifacts, label):
    _keys(ref, {'source_id', 'source_sha256'}, label)
    sid = _text(ref['source_id'], label + '.source_id')
    digest = ref['source_sha256']
    _require(type(digest) is str and len(digest) == 64 and
             all(c in '0123456789abcdef' for c in digest), f'{label}: SHA256 required')
    _require(sid in artifacts and type(artifacts[sid]) is bytes,
             f'{label}: original source bytes missing: {sid}')
    raw = artifacts[sid]
    _require(sha256(raw).hexdigest() == digest, f'{label}: source hash mismatch')
    return raw


def _availability(value, cutoff, label):
    _keys(value, {'source', 'published_at', 'available_at'}, label)
    _text(value['source'], label + '.source')
    _require(_time(value['published_at'], label) <= _time(value['available_at'], label) <= cutoff,
             f'{label}: publication <= availability <= SCED cutoff required')


def _physical_inputs(raw, identity, grid, cutoff):
    _keys(raw, {'identity', 'period_intervals', 'sources', 'state_schedule',
                'initial_state', 'terminal_state', 'model_inputs'}, 'physical_inputs')
    _identity(raw['identity'], identity, 'physical_inputs.identity')
    rows = raw['period_intervals']
    _require(type(rows) is list and len(rows) == 24, 'physical_inputs: all 24 absolute-time periods required')
    for t, (row, expected) in enumerate(zip(rows, grid), 1):
        _keys(row, {'period', 'delivery_start', 'delivery_end'}, f'period_intervals[{t}]')
        _require(type(row['period']) is int and row['period'] == t,
                 'physical_inputs: ordered period IDs 1..24 required')
        for field in ('delivery_start', 'delivery_end'):
            _require(_time(row[field], field) == _time(expected[field], field),
                     'physical_inputs: period time/window mismatch')
    _keys(raw['model_inputs'], CORE_KEYS | ({'thermal_trajectories'} if 'thermal_trajectories' in raw['model_inputs'] else set()) | ({'storage_capacity_accounting'} if 'storage_capacity_accounting' in raw['model_inputs'] else set()), 'model_inputs')
    trajectory=raw['model_inputs'].get('thermal_trajectories')
    if trajectory is not None:
        from .gd_october_thermal import validate_book
        _keys(trajectory, ('book','window_start','information_cutoff'), 'thermal trajectory context')
        _require(_time(trajectory['window_start'],'thermal start')==_time(grid[0]['delivery_start'],'grid start') and
                 _time(trajectory['information_cutoff'],'thermal cutoff')==cutoff,'thermal context/source clock mismatch')
        validate_book(trajectory['book'],cutoff=cutoff,run_id=identity['run_id'])
    source_fields = PHYSICAL_SOURCES | ({'renewable_policy'} if
        raw['model_inputs']['renewable_mode'] == 'dispatchable_bus_aggregate' else set())
    _keys(raw['sources'], source_fields, 'physical_inputs.sources')
    for name, source in raw['sources'].items():
        _availability(source, cutoff, 'physical_inputs.sources.' + name)
    schedule = raw['state_schedule']
    _keys(schedule, {'method', 'is_measurement', 'source'}, 'state_schedule')
    _require(schedule['method'] == 'explicit_5min_effective_schedule',
             'state_schedule: only explicit 5min effective schedules supported; no implicit DA replication')
    _require(schedule['is_measurement'] is False,
             'state_schedule: effective commitments/states are not raw measurements')
    _text(schedule['source'], 'state_schedule.source')
    initial = raw['initial_state']
    _keys(initial, {'at', 'kind', 'source'}, 'initial_state')
    _require(_time(initial['at'], 'initial_state.at') == _time(grid[0]['delivery_start'], 'start'),
             'initial_state: must refer to this window start, not yesterday or the previous lookahead endpoint')
    _require(initial['kind'] in {'observed_state', 'planned_projection', 'synthetic_research_state'},
             'initial_state: explicit observed/projected/synthetic state kind required')
    _text(initial['source'], 'initial_state.source')
    if initial['kind'] == 'observed_state':
        _require(_time(initial['at'], 'initial_state.at') <=
                 _time(raw['sources']['initial_state']['published_at'], 'initial_state publication'),
                 'initial_state: future state cannot be an already observed state')
    terminal = raw['terminal_state']
    _keys(terminal, {'at', 'kind', 'source'}, 'terminal_state')
    _require(_time(terminal['at'], 'terminal_state.at') == _time(grid[-1]['delivery_end'], 'end') and
             terminal['kind'] == 'explicit_window_terminal_boundary',
             'terminal_state: separate explicit window-end boundary required')
    _text(terminal['source'], 'terminal_state.source')
    return deepcopy(raw['model_inputs'])


def _mapping(raw, identity, grid, cutoff, series, references, buses):
    _keys(raw, {'schema_version', 'identity', 'delivery_start', 'delivery_end', 'provenance',
                'quantity_semantics', 'load_accounting', 'aggregation_policy', 'rows', 'absent_bus_families'}, 'resource_mapping')
    _require(raw['schema_version'] == MAPPING_SCHEMA, 'resource_mapping: unsupported schema')
    _identity(raw['identity'], identity, 'resource_mapping.identity')
    for field, endpoint in (('delivery_start', grid[0]['delivery_start']),
                            ('delivery_end', grid[-1]['delivery_end'])):
        _require(_time(raw[field], field) == _time(endpoint, field), 'resource_mapping: window mismatch')
    _availability(raw['provenance'], cutoff, 'resource_mapping.provenance')
    _require(raw['quantity_semantics'] == 'balance_scope_load_separate_modelled_injections_positive_tie_import',
             'resource_mapping: explicit balance-scope load/separate modelled injections/positive tie import semantics required')
    accounting = raw['load_accounting']
    _keys(accounting, {'balance_load_scope', 'local_generation_accounting', 'local_generation_source',
                       'negative_reserve_demand_basis', 'negative_reserve_demand_source',
                       'modelled_injections_already_deducted'}, 'load_accounting')
    for name in set(accounting) - {'modelled_injections_already_deducted'}:
        _text(accounting[name], 'load_accounting.' + name)
    _require(accounting['modelled_injections_already_deducted'] is False,
             'load_accounting: separately modelled injections cannot already be deducted from the supplied load')
    _require(raw['aggregation_policy'] == 'sum_mw_at_declared_bus_no_scaling',
             'resource_mapping: only explicit MW summation with no scaling is supported')
    _require(type(raw['rows']) is list, 'resource_mapping.rows: explicit array required')
    expected = {(f, rid) for f, s in series.items() for rid in s['resource_ids']}
    assigned, targets, occupied = {}, set(), set()
    for row in raw['rows']:
        _keys(row, {'family', 'source_resource_id', 'model_resource_id', 'bus', 'series_source', 'source',
                    'minimum_mw', 'maximum_mw', 'bounds_source'},
              'resource_mapping.row')
        family, rid = row['family'], row['source_resource_id']
        _text(family, 'mapping family'); _text(rid, 'source resource')
        key = (family, rid)
        _require(key in expected, 'resource_mapping: unexpected family/source resource')
        _require(key not in assigned, 'resource_mapping: duplicate source-resource allocation')
        target = _text(row['model_resource_id'], 'model_resource_id')
        _require(target not in targets, 'resource_mapping: duplicate model resource allocation')
        _require(type(row['bus']) is str and row['bus'] in buses, 'resource_mapping: unknown bus')
        _text(row['source'], 'resource_mapping.row.source')
        low, high = (_number(row[n], 'resource_mapping.' + n) for n in ('minimum_mw', 'maximum_mw'))
        _text(row['bounds_source'], 'resource_mapping.bounds_source')
        _require(low <= high and (family == 'boundary_tie_mw' or low >= 0),
                 'resource_mapping: inconsistent explicit physical power bounds')
        _keys(row['series_source'], {'source_id', 'source_sha256'}, 'mapping.series_source')
        _require(row['series_source'] == references[family], 'resource_mapping: series source reference mismatch')
        assigned[key] = row; targets.add(target); occupied.add((family, row['bus']))
    _require(set(assigned) == expected, 'resource_mapping: exact source-resource coverage required; no dropping or filling')
    absent = raw['absent_bus_families']
    _require(type(absent) is list, 'resource_mapping: explicit absent_bus_families array required')
    absent_keys = set()
    for row in absent:
        _keys(row, {'family', 'bus', 'source'}, 'absent_bus_family')
        _text(row['family'], 'absent family'); _text(row['bus'], 'absent bus')
        key = (row['family'], row['bus'])
        _require(key in {(f, b) for f in FAMILIES for b in buses}, 'unknown absent bus/family')
        _require(key not in absent_keys and key not in occupied, 'duplicate or contradictory bus/family absence')
        _text(row['source'], 'absent_bus_family.source'); absent_keys.add(key)
    _require(absent_keys | occupied == {(f, b) for f in FAMILIES for b in buses},
             'resource_mapping: absent bus/family sources missing; zeros cannot be inferred')
    return assigned



def _renewable_profile(params, mapping, sources):
    """Bind an explicitly selected profile to dated, same-identity evidence.

    Core validates the allowed policy, zero offers and complete bus-period
    tariffs. The profile is passed through unchanged, never manufactured here.
    Resource-level offer equivalence remains the provider's sourced declaration,
    not an authenticity claim inferred from a bus-level aggregate.
    """
    profile = params['renewable_dispatch_profile']
    if params['renewable_mode'] == 'fixed_exogenous':
        _require(profile is None, 'fixed exogenous mode cannot carry an unused dispatch profile')
        return
    _keys(profile, {'kind', 'aggregation', 'source', 'aggregation_source',
                    'reserve_capacity_source'}, 'renewable_dispatch_profile')
    _require(profile['kind'] == 'nonpriced_forecast_with_deviation',
             'only nonpriced forecast/deviation renewable policy is supported')
    _require(profile['aggregation'] == 'same_bus_identical_offer_and_penalty',
             'explicit same-bus identical offer/penalty aggregation required')
    for name in ('source', 'aggregation_source', 'reserve_capacity_source'):
        _text(profile[name], 'renewable_dispatch_profile.' + name)
    _require(profile['source'] == sources['renewable_policy']['source'],
             'renewable_dispatch_profile: dated policy source mismatch')
    _require(profile['aggregation_source'] == mapping['provenance']['source'],
             'renewable_dispatch_profile: same-identity dated aggregation source mismatch')
    _require(profile['reserve_capacity_source'] == params['reserves']['sources']['capacity_qualification_basis'],
             'renewable_dispatch_profile: reserve qualification source mismatch')

def build_october_model_from_contract(contract_document: bytes, *, source_artifacts,
                                      expected_identity, expected_delivery_start, asof, mode,
                                      physical_inputs, resource_mapping_ref):
    """Build the real physical model after independently verifying original bytes.

    ``physical_inputs`` is the explicit Python October-core schema in a sourced,
    same-identity, absolute-time envelope. It cannot supply the three exogenous
    arrays or a replacement horizon. ``resource_mapping_ref`` is a caller-pinned
    source-id/SHA256 reference into ``source_artifacts``, not a trusted validation
    report. Hash consistency does not establish authenticity of the sources.
    """
    _require(type(contract_document) is bytes and bool(contract_document),
             'contract_document: original immutable bytes required; validated reports are not accepted')
    _require(type(source_artifacts) is dict and all(type(k) is str and type(v) is bytes
                                                   for k, v in source_artifacts.items()),
             'source_artifacts: explicit source-id to immutable bytes required')
    artifacts = dict(source_artifacts)
    report = validate_october_rt_contract(contract_document, expected_identity=expected_identity,
        expected_delivery_start=expected_delivery_start, asof=asof, source_artifacts=artifacts, mode=mode)
    _require(report['status'] == 'validated', 'contract revalidation failed: ' +
             '; '.join(x['code'] + ': ' + x['message'] for x in report['issues']))
    data = report['data']; contract = data['contract']; grid = data['grids']['sced']
    _require(contract['active_sced_start_index'] == 0,
             'bridge supports a full 24-period window only; nonzero active prefix is not silently reoptimized')
    identity = deepcopy(contract['identity'])
    cutoff = _time(contract['information_cutoff']['sced'], 'SCED cutoff')
    params = _physical_inputs(physical_inputs, identity, grid, cutoff)
    from .gd_october_thermal import require_trajectory_mode, is_research_book
    thermal_book = (params.get('thermal_trajectories') or {}).get('book')
    require_trajectory_mode(thermal_book, mode)
    if mode != 'research':
        _require(physical_inputs['initial_state']['kind'] != 'synthetic_research_state',
                 'synthetic initial state requires research mode')
    _require(params['renewable_mode'] in ('fixed_exogenous', 'dispatchable_bus_aggregate'),
             'unsupported explicit renewable mode; no automatic policy selection')
    storage = params['storage_spec']
    if storage is not None:
        _require(type(storage) is dict and storage.get('run_id') == identity['run_id'],
                 'storage_spec: run identity mismatch or missing run_id')
        _require(_time(storage.get('window_start'), 'storage_spec.window_start') ==
                 _time(grid[0]['delivery_start'], 'window start'), 'storage_spec: window mismatch')
        _require(_time(storage.get('information_cutoff'), 'storage_spec.information_cutoff') == cutoff,
                 'storage_spec: information cutoff mismatch')
    buses = params['buses']
    _require(type(buses) in (tuple, list) and bool(buses) and
             all(type(b) is str and b.strip() for b in buses) and len(set(buses)) == len(buses),
             'model_inputs.buses: nonempty unique ordered bus inventory required')
    series = {s['family']: s for s in data['series'] if s['stage'] == 'sced'}
    _require(set(series) == set(FAMILIES),
             'bridge supports exactly load/wind/solar/boundary_tie_mw; extra boundaries must not be silently ignored')
    _require(all(s['unit'] == 'MW' for s in series.values()), 'physical exogenous series must be MW')
    references = {}
    for ref in contract['series_sources']:
        src = _json(artifacts[ref['source_id']], 'series source')
        if src['stage'] == 'sced':
            references[src['family']] = deepcopy(ref)
    mapping_raw = _source(resource_mapping_ref, artifacts, 'resource_mapping_ref')
    mapping = _json(mapping_raw, 'resource mapping')
    assigned = _mapping(mapping, identity, grid, cutoff, series, references, buses)
    _require(mapping['load_accounting']['negative_reserve_demand_source'] ==
             params['reserves']['sources']['negative_demand_basis'],
             'load_accounting: negative reserve demand basis source mismatch')
    _renewable_profile(params, mapping, physical_inputs['sources'])
    times = {_time(row['delivery_start'], 'grid'): t for t, row in enumerate(grid, 1)}
    terms = {(f, b, t): [] for f in FAMILIES for b in buses for t in times.values()}
    resources = []
    for family, source in series.items():
        for row in source['rows']:
            allocation = assigned[family, row['resource_id']]
            _require(allocation['minimum_mw'] <= row['value'] <= allocation['maximum_mw'],
                     'resource_mapping: source value outside explicit physical bounds; no clipping')
            t = times[_time(row['delivery_start'], 'series delivery_start')]
            terms[family, allocation['bus'], t].append(row['value'])
            resources.append({'family': family, 'source_resource_id': row['resource_id'],
                'model_resource_id': allocation['model_resource_id'], 'bus': allocation['bus'],
                'period': t, 'delivery_start': row['delivery_start'], 'delivery_end': row['delivery_end'],
                'value_mw': row['value'], 'series_source': deepcopy(references[family]),
                'mapping_source': allocation['source']})
    totals = {}
    try:
        for key, values in terms.items():
            totals[key] = math.fsum(values)
            _require(math.isfinite(totals[key]), 'nonfinite aggregate MW')
    except (OverflowError, ValueError) as exc:
        raise OctoberInputBridgeError('nonfinite aggregate MW') from exc
    load = {(b, t): totals['load_mw', b, t] for b in buses for t in times.values()}
    renewable = {(b, t): totals['wind_mw', b, t] + totals['solar_mw', b, t]
                 for b in buses for t in times.values()}
    tie = {(b, t): totals['boundary_tie_mw', b, t] for b in buses for t in times.values()}
    _require(all(math.isfinite(v) for v in renewable.values()), 'nonfinite combined renewable MW')
    storage_binding = None
    if 'storage_capacity_accounting' in params:
        from .gd_october_storage_capacity_v3 import validate_bridge
        storage_binding = validate_bridge(physical_inputs=physical_inputs, source_artifacts=artifacts,
            contract_document=contract_document, resource_mapping_ref=resource_mapping_ref, mode=mode)
        for name in ('source_ref', 'original_physical_ref'):
            ref = storage_binding[name]
            report['source_sha256'][ref['source_id']] = ref['source_sha256']
    correction_binding = None
    accounting = params['reserves'].get('capacity_accounting')
    if accounting is not None and accounting.get('schema_version') == 'gd.october.fixed_renewable_absolute_capacity.v4':
        from .gd_october_fixed_renewable_capacity_v4 import validate_bridge as validate_fixed_research
        correction_binding = validate_fixed_research(
            physical_inputs=({**physical_inputs, 'model_inputs': {k:v for k,v in physical_inputs['model_inputs'].items() if k != 'storage_capacity_accounting'}} if storage_binding is not None else physical_inputs),
            source_artifacts=artifacts, contract_document=contract_document, resource_mapping_ref=resource_mapping_ref,
            expected_identity=identity, expected_delivery_start=expected_delivery_start, asof=asof, mode=mode)
        for name in ('source_ref','original_bridge_ref'):
            ref=correction_binding[name]
            report['source_sha256'][ref['source_id']]=ref['source_sha256']
    elif accounting is not None:
        _require(mode == 'research', 'V2 fixed-energy accounting is an explicit research interpretation')
        original = _source(accounting.get('source_ref'), artifacts, 'capacity_accounting.source_ref')
        correction = _json(original, 'capacity correction source')
        _require(correction.get('schema_version') == 'gd.october.ieee39_capacity_correction.v2' and
                 correction.get('rule_sha256') == accounting.get('rule_sha256') and
                 correction.get('source_kind') == 'research_rule_interpretation' and
                 correction.get('renewable_reserve_qualification_asserted') is False and
                 correction.get('operational_source_verified') is False and
                 correction.get('official_market_accepted') is False,
                 'capacity correction source must retain research/nonqualification identity')
        base_ref = correction.get('base_scenario_ref')
        base_original = _source(base_ref, artifacts, 'capacity correction base_scenario_ref')
        from .gd_october_ieee39_capacity_v2 import validate_bridge_correction
        correction_binding = validate_bridge_correction(correction_raw=original, base_raw=base_original,
            physical_inputs=({**physical_inputs, 'model_inputs': {k:v for k,v in physical_inputs['model_inputs'].items() if k != 'storage_capacity_accounting'}} if storage_binding is not None else physical_inputs), expected_identity=identity, contract_document=contract_document,
            resource_mapping_ref=resource_mapping_ref, source_artifacts=artifacts)
        # Both new executed inputs participate in the retained source audit.
        for ref in (accounting['source_ref'], base_ref):
            report['source_sha256'][ref['source_id']] = ref['source_sha256']
    horizon = october_sced_horizon(physical_inputs['sources']['period_mapping']['source'])
    model = build_october_sced(horizon=horizon, load_mw=load, renewable_mw=renewable,
                              physical_tie_mw=tie, **params)
    # Preserve the same identity; never create or select a new dispatch in wiring.
    model._gd_october_identity = deepcopy(identity)
    model._gd_october_input_bridge_audit = {
        'schema_version': BRIDGE_SCHEMA, 'identity': identity,
        'contract_document_sha256': sha256(contract_document).hexdigest(),
        'contract_content_sha256': contract['content_sha256'],
        'source_sha256': dict(report['source_sha256']),
        'resource_mapping_ref': deepcopy(resource_mapping_ref), 'resource_mapping': mapping,
        'period_intervals': deepcopy(physical_inputs['period_intervals']),
        'resource_values': resources,
        'renewable_mode': params['renewable_mode'],
        'renewable_dispatch_profile': deepcopy(params['renewable_dispatch_profile']),
        'renewable_input_role': 'fixed_exogenous_injection' if params['renewable_mode'] == 'fixed_exogenous' else 'forecast_availability_not_fixed_dispatch',
        'renewable_aggregation_authenticity_verified': False,
        'research_thermal_assumptions': is_research_book(thermal_book),
        'operational_source_verified': False,
        'series_provenance': {f: deepcopy(s['provenance']) for f, s in series.items()},
        'derivation_profiles': deepcopy(report['derivation_profiles']),
        'uses_future_actual': any(s['provenance']['uses_future_actual'] for s in data['series']),
        'derived_simulation': any(s['derivation'] is not None for s in series.values()),
        'derived_arithmetic_verified': report['derived_arithmetic_verified'],
        'causal_eligible': report['causal_eligible'] and physical_inputs['initial_state']['kind'] != 'synthetic_research_state',
        'physical_sources': deepcopy(physical_inputs['sources']),
        'state_schedule': deepcopy(physical_inputs['state_schedule']),
        'initial_state': deepcopy(physical_inputs['initial_state']),
        'terminal_state': deepcopy(physical_inputs['terminal_state']),
        'zero_cells_have_explicit_absence_sources': True, 'load_netted': False,
        'scaling_applied': False, 'da_state_replication_supported': False,
        'selected_dispatch_role': 'source_replay_identity_not_new_solution_certificate',
        'selected_dispatch_values_reproduced': False,
        'lmp_series_used_in_physical_model': False, 'source_authenticity_verified': False,
        'physical_model_sha256': model._gd_october_signature,
        'economic_objective_complete': model._gd_october_scope['economic_objective_complete'],
        'model_built': True, 'solver_called': False, 'numerical_feasibility_verified': False,
        'whole_day_feasibility_verified': False, 'official_algorithm_certified': False,
        'strict_cost_comparability_verified': False,
    }
    # The bytes-hash set includes the physical resource mapping in addition to
    # every original/derived series, selected dispatch and pricing declaration.
    if storage_binding is not None:
        model._gd_october_input_bridge_audit['storage_capacity_accounting_binding'] = deepcopy(storage_binding)
    if correction_binding is not None:
        model._gd_october_input_bridge_audit['capacity_accounting_binding'] = deepcopy(correction_binding)
    model._gd_october_input_bridge_audit['source_sha256'][resource_mapping_ref['source_id']] = resource_mapping_ref['source_sha256']
    model._gd_october_input_binding = _InputBinding(
        tuple(sorted(identity.items())), _audit_sha256(model._gd_october_input_bridge_audit),
        model._gd_october_signature)
    assert_october_input_binding(model)
    return model


def _audit_sha256(audit):
    try:
        raw = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError) as exc:
        raise OctoberInputBridgeError('input binding: malformed source audit') from exc
    return sha256(raw).hexdigest()


def assert_october_input_binding(model):
    """Recheck source identity, audit and sealed mathematics before downstream use.

    A build-time Boolean is not an enduring validation certificate. This gate
    permits free-variable witness assignments, but rejects changed constraints,
    objectives, parameter values, variable bounds/fixes, source audit or identity.
    It is an in-process consistency guard, not a signature against hostile Python
    code that can replace both the guard and its stored evidence.
    """
    binding = getattr(model, '_gd_october_input_binding', None)
    _require(isinstance(binding, _InputBinding), 'input binding: bridge-built model required')
    identity = getattr(model, '_gd_october_identity', None)
    _require(type(identity) is dict and tuple(sorted(identity.items())) == binding.identity,
             'input binding: selected identity changed after construction')
    _require(_audit_sha256(getattr(model, '_gd_october_input_bridge_audit', None)) == binding.source_audit_sha256,
             'input binding: source audit changed after construction')
    _require(getattr(model, '_gd_october_signature', None) == binding.physical_model_sha256,
             'input binding: original physical model signature changed')
    assert_october_integrity(model)
    return {'input_binding_verified': True, 'model_integrity_verified': True,
            'identity': dict(binding.identity), 'physical_model_sha256': binding.physical_model_sha256,
            'source_audit_sha256': binding.source_audit_sha256,
            'source_authenticity_verified': False, 'selected_dispatch_values_reproduced': False,
            'solver_called': False, 'numerical_feasibility_verified': False,
            'whole_day_feasibility_verified': False, 'official_algorithm_certified': False}
