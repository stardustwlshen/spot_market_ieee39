"""Internal October fixed-state HiGHS path. Importing this module never solves.

The issuance receipt traces this Python call chain, not a cryptographic trust or
physical execution certificate. Mocked solver calls remain mock-only evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from weakref import WeakSet

import pyomo.environ as pyo

from . import gd_solve, highs_solver
from .gd_october_roll import (
    PreparedWindow, RollRequest, WindowSolution, Physics, _require, _text, _time,
    _number, _keys, _same_numbers, canonical_bytes, fingerprint, inventory,
    validate_prepared, extract_october_model_steps, _selected_document_fields,
    _validate_selected_plan, _map_dispatch_to_pricing_report,
)
from .gd_october_sced import require_october_economic_objective, assert_october_integrity
from .gd_october_input_bridge import assert_october_input_binding

SCHEMA = 'gd.october_native.selected.v1'
BACKEND = 'highs'
MOCK_BACKEND = 'mock_internal_solver_flow'
_ORIGINAL_SOLVE_DISPATCH = gd_solve.solve_dispatch
_NATIVE_CALLABLES = (
    (gd_solve, 'solve_dispatch', 'smm.gd_solve'),
    (highs_solver.HighsSolver, '__init__', 'smm.highs_solver'),
    (highs_solver.HighsSolver, 'solve', 'smm.highs_solver'),
    (highs_solver, 'run_child', 'smm.highs_solver'),
    (highs_solver, 'child_environment', 'smm.highs_solver'),
    (highs_solver, 'serialize_model', 'smm.highs_solver'),
    (highs_solver, 'validate_options', 'smm.highs_solver'),
    (highs_solver, 'validate_solution', 'smm.highs_solver'),
    (highs_solver.subprocess.Popen, '__init__', 'subprocess'),
    (highs_solver.subprocess.Popen, 'wait', 'subprocess'),
)
_ORIGINAL_CALLABLES = tuple(getattr(owner, name) for owner, name, _ in _NATIVE_CALLABLES)
_ORIGINAL_HiGHS_CLASS = highs_solver.HighsSolver
_ORIGINAL_POPEN = highs_solver.subprocess.Popen


def _callable_source_basename(function):
    # Support actual Windows paths without weakening module/function identity gates.
    return getattr(getattr(function, '__code__', None), 'co_filename', '').replace('\\', '/').rsplit('/', 1)[-1]


def _original_internal_solver_path():
    # Ordinary monkeypatches at either adapter layer are test evidence, not
    # native runs. This is traceability, not a sandbox against hostile Python.
    return highs_solver.native_solver_path_intact()



def _assert_real_durations(window):
    utc = lambda t: _time(t, 'absolute clock').astimezone(timezone.utc)
    _require((utc(window.delivery_end) - utc(window.delivery_start)).total_seconds() == 7200,
             'native horizon must be 7200 actual UTC seconds, not DST wall-clock hours')
    _require(all((utc(b) - utc(a)).total_seconds() == 300 for a,b in window.intervals),
             'native interval must be 300 actual UTC seconds')
    if window.timing_profile == 'official_reference_T_minus_15':
        _require((utc(window.delivery_start)-utc(window.information_cutoff)).total_seconds() == 900,
                 'T-minus-15 requires 900 actual UTC seconds')

_ISSUED = WeakSet()
_EXTRA_FIELDS = ('input_bundle_sha256', 'solve_intent_sha256', 'physical_model_sha256',
                 'known_dispatch_reference', 'solver_call_provenance')


@dataclass(frozen=True)
class NativeSolveSettings:
    time_limit_s: float
    mip_rel_gap: float = .001
    tee: bool = False

    def validate(self):
        _require(_number(self.time_limit_s, 'time limit', 0) > 0, 'positive time limit required')
        _require(0 <= _number(self.mip_rel_gap, 'gap') <= .001, 'gap must not exceed 0.001')
        _require(type(self.tee) is bool, 'tee must be Boolean')
        return self


@dataclass(frozen=True)
class ResultPublication:
    """Historical simulation clocks must be declared, never called wall clock."""
    selected_dispatch_id: str
    source: str
    clock_basis: str
    published_at: datetime | None = None
    available_at: datetime | None = None

    def validate(self, window):
        _text(self.selected_dispatch_id, 'RT selected id'); _text(self.source, 'RT source')
        _require(self.clock_basis in {'runtime_wall_clock', 'declared_research_clock'}, 'explicit publication clock basis required')
        if self.clock_basis == 'runtime_wall_clock':
            _require(self.published_at is None and self.available_at is None, 'runtime clock cannot be caller backdated')
        else:
            p, a = _time(self.published_at, 'research publication'), _time(self.available_at, 'research availability')
            _require(window.information_cutoff <= p <= a <= window.delivery_start, 'declared research publication outside window')
        return self


@dataclass(frozen=True)
class NativeWindowInputs:
    prepared: PreparedWindow
    bridge_inputs: dict
    known_dispatch_reference: object
    settings: NativeSolveSettings
    publication: ResultPublication


@dataclass(frozen=True, eq=False)
class _CallReceipt:
    artifact: bytes
    prepared_sha256: str
    provenance: bytes
    rt_reference: object

    def __deepcopy__(self, memo):
        return self  # Preserve the internal issued-object identity, not a dict flag.


@dataclass(frozen=True)
class NativeWindowSolution(WindowSolution):
    _call_receipt: object

    @property
    def provenance(self):
        return json.loads(self._call_receipt.provenance)

    @property
    def rt_reference(self):
        return deepcopy(self._call_receipt.rt_reference)


def _constant(values, label):
    _require(type(values) is dict and tuple(values) == tuple(range(1, 25)), label + ': 24 ordered values required')
    first = values[1]
    _require(all(v == first for v in values.values()), label + ': time-varying physics needs a segmented adapter')
    return first


def _assert_prepared_model(model, prepared, bridge_inputs):
    """Compare the actual built model, not a caller's claim about matching it."""
    w, state = prepared.window, prepared.projection.projected_state
    audit = model._gd_october_input_bridge_audit
    _require(audit['identity']['run_id'] == w.run_id and audit['identity']['window_id'] == w.window_id, 'model/prepared run/window mismatch')
    contract = json.loads(bridge_inputs['contract_document'])
    _require(_time(contract['delivery_start'], 'contract start') == w.delivery_start and
             _time(contract['information_cutoff']['sced'], 'contract cutoff') == w.information_cutoff, 'model/prepared clock mismatch')
    _require(bridge_inputs['mode'] == prepared.mode, 'model/prepared causal mode mismatch')
    sources = bridge_inputs['source_artifacts']
    sced_refs = {r['source_id']: r['source_sha256'] for r in contract['series_sources']
                 if json.loads(sources[r['source_id']])['stage'] == 'sced'}
    _require({s.source_id: s.expected_sha256 for s in prepared.inputs} == sced_refs, 'prepared SCED sources differ from model bridge sources')
    for src in prepared.inputs:
        _require(src.artifact == sources[src.source_id], 'prepared source bytes differ from model')
        _require(all(k in sources and sources[k] == v for k, v in src.supporting_artifacts.items()), 'prepared supporting source differs from model')
    physical = bridge_inputs['physical_inputs']
    _require(physical['initial_state']['kind'] == 'planned_projection' and physical['initial_state']['source'] == state.source,
             'model initial source must name the verified projection')
    _require(physical['terminal_state']['source'] == prepared.terminal.source, 'model/roll terminal source mismatch')
    _require(physical['state_schedule']['source'] == prepared.effective_plan.source, 'model/roll effective plan source mismatch')
    thermal = model._energy_inputs['thermal']
    _keys(state.values['thermal'], thermal, 'thermal initial resources')
    for unit, d in thermal.items():
        boundary={k:v for k,v in state.values['thermal'][unit].items() if k!='trajectory'}
        _same_numbers(boundary, dict(online=d['initial_online'], power_mw=d['initial_power_mw'],
                      state_age_h=d['initial_state_age_h']), 'model thermal projected boundary')
    trajectory=model._gd_october_inputs.get('thermal_trajectories')
    _require((None if trajectory is None else trajectory['book'])==prepared.physics.thermal_trajectories,
             'model/roll thermal trajectory bytes mismatch')
    if trajectory is not None:
        from .gd_october_thermal import validate_book, _active, cursor_for
        records=validate_book(trajectory['book'],cutoff=w.information_cutoff,run_id=w.run_id)
        for record in records:
            if any(w.delivery_start<=_time(s['start'],'curve start')<w.delivery_end for s in record['steps']):
                _require(record['effective_plan_id']==prepared.effective_plan.plan_id,'thermal curve/effective plan identity mismatch')
        for unit,row in state.values['thermal'].items():
            match=_active(records,unit,w.delivery_start)
            expected=None if match is None or match[1]==0 else cursor_for(match[0],match[1])
            _require(row.get('trajectory')==expected,'model/roll inherited thermal cursor mismatch')
    storage = getattr(model, '_gd_october_storage_inputs', dict(batteries={}, plants={}, units={}, owners={}))
    for family, key in [('batteries','batteries'), ('pumped_plants','plants'), ('pumped_units','units')]:
        _keys(state.values[family], storage[key], 'model storage resources ' + family)
    batteries, plants, units = {}, {}, {}
    for uid, d in storage['batteries'].items():
        b = state.values['batteries'][uid]
        _same_numbers(b['energy_mwh'], d['initial_energy_mwh'], 'battery projected inventory')
        _same_numbers(b['cumulative_throughput_mwh'], d['cumulative_throughput_used_mwh'], 'battery cumulative throughput')
        for day, budget in d['cycle_budgets'].items():
            _same_numbers(b['throughput_mwh_by_day'].get(day, 0.), budget['used_throughput_mwh'], 'battery dated throughput')
            _same_numbers(budget['limit_cycles'], d['cycle_limit'], 'different daily limits need segmented physics')
        _same_numbers(prepared.terminal.inventories['battery_energy_mwh'][uid], d['final_energy_mwh'], 'battery model/roll terminal')
        batteries[uid] = dict(eta_charge=d['eta'], eta_discharge=d['eta'], rated_mwh=d['energy_capacity_mwh'],
            min_mwh=_constant(d['energy_min_mwh'], uid + ' minimum'), max_mwh=_constant(d['energy_max_mwh'], uid + ' maximum'),
            max_cycles_per_day=d['cycle_limit'])
    for pid, d in storage['plants'].items():
        _same_numbers(state.values['pumped_plants'][pid], dict(upper_m3=d['upper_initial_m3'], lower_m3=d['lower_initial_m3']), 'reservoir projected inventory')
        record = d['state_source']
        _require(_time(record['as_of'],'reservoir initial asof') == state.as_of and
                 _time(record['available_at'],'reservoir initial availability') == state.available_at and
                 record['kind'] == 'causal_plan_projection' and record['source'] == state.source, 'reservoir initial source/time mismatch')
        plants[pid] = {k: _constant(d[k], pid + '.' + k) for k in ('upper_min_m3','upper_max_m3','lower_min_m3','lower_max_m3')}
    for uid, d in storage['units'].items():
        units[uid] = dict(plant_id=storage['owners'][uid], pump_m3_per_mwh=_constant(d['pump_m3_per_mwh'], uid + '.pump coefficient'),
                         generate_m3_per_mwh=_constant(d['generation_m3_per_mwh'], uid + '.generation coefficient'))
        _same_numbers(prepared.effective_plan.minimum_mode_h[uid], d['effective_plan']['minimum_mode_h'], 'pumped minimum residence')
    for family, key in [('batteries','batteries'), ('pumped_units','units')]:
        for uid, d in storage[key].items():
            b, initial = state.values[family][uid], d['actual_initial']
            effective = d['effective_plan']
            _require(effective['source'] == prepared.effective_plan.source and
                     _time(effective['published_at'],'storage plan publication') == prepared.effective_plan.published_at,
                     'storage/roll effective plan source/time mismatch')
            for name in ('mode', 'mode_elapsed_h', 'net_power_mw'):
                _require(b[name] == initial[name], 'storage projected mode/power/age mismatch')
            _require(_time(initial['as_of'], 'initial asof') == state.as_of and
                     _time(initial['available_at'], 'initial availability') == state.available_at and
                     initial['kind'] == 'causal_plan_projection' and initial['source'] == state.source, 'storage initial source/time mismatch')
    preparations = {(p['unit_id'],p['transition_period']):p for p in prepared.effective_plan.preparations}
    model_preparations = {(uid,t):p for uid,d in storage['units'].items() for t,p in d['effective_plan']['transition_authorizations'].items()}
    _require(set(preparations) == set(model_preparations), 'model/roll transition authorization coverage mismatch')
    for key, authorization in model_preparations.items():
        p = preparations[key]
        for dest, src in [('from_mode','from_mode'),('to_mode','to_mode'),('required_h','preparation_required_h'),('source','source')]:
            _require(p[dest] == authorization[src], 'model/roll preparation authorization mismatch')
        for dest, src in [('preparation_started_at','preparation_started_at'),('ready_at','preparation_completed_at'),('published_at','issued_at')]:
            _require(_time(p[dest],dest) == _time(authorization[src],src), 'model/roll preparation time mismatch')
    expected = Physics(batteries, plants, units, prepared.physics.source,
                       'delivery_interval_start_Asia_Shanghai', 'symmetric_sqrt_roundtrip')
    _require(prepared.physics.efficiency_basis == expected.efficiency_basis, 'model uses symmetric sqrt roundtrip efficiency')
    for key in ('batteries','pumped_plants','pumped_units'):
        _same_numbers(getattr(prepared.physics,key), getattr(expected,key), 'model/roll physics ' + key)
    for i, plan in enumerate(prepared.effective_plan.states, 1):
        expected_states = dict(thermal={g:int(pyo.value(model.online[g,i])) for g in model.G},
            batteries={u:d['modes'][i] for u,d in storage['batteries'].items()},
            pumped_units={u:d['modes'][i] for u,d in storage['units'].items()})
        _require(plan == expected_states, 'model/roll effective modes mismatch')
    return {'model_prepared_binding_verified': True, 'window_constant_physics_verified': True}


def _checked_result(result, model, settings):
    _require(isinstance(result, gd_solve.DispatchSolution) and result.model is model, 'internal solver returned a different model or result type')
    metrics = result.metrics
    _require(type(metrics) is dict and metrics.get('backend') == BACKEND, 'solver result backend inconsistent with internal HiGHS path')
    _require(str(result.native_result.solver.termination_condition) == 'optimal' and metrics.get('termination') == 'optimal', 'fixed-state continuous LP must actually terminate optimal')
    _require(not any(not v.fixed and v.is_integer() for v in model.component_data_objects(pyo.Var)), 'native October path must be fixed-state continuous LP')
    objective = _number(pyo.value(model.objective), 'model objective')
    bound = _number(result.native_result.problem.lower_bound, 'native lower bound')
    upper = _number(result.native_result.problem.upper_bound, 'native upper bound')
    tol = 1e-5 + 1e-7 * max(1., abs(objective), abs(bound))
    for value, name in [(bound,'LP lower bound'), (upper,'LP upper bound'),
                        (metrics.get('objective_yuan'),'reported objective'), (metrics.get('best_bound_yuan'),'reported bound')]:
        _require(abs(_number(value, name) - objective) <= tol, name + ' disagrees with optimal LP objective')
    _require(metrics.get('objective_offset_yuan') == 0 and metrics.get('integer_variables') == 0 and
             metrics.get('requested_mip_rel_gap') == settings.mip_rel_gap and metrics.get('requested_gap_met') is True,
             'solver settings/LP/gap evidence inconsistent')
    _number(metrics.get('time_s'), 'solver duration', 0)
    return dict(termination='optimal', objective_yuan=objective, best_bound_yuan=bound,
                optimal_bound_absolute_tolerance=tol, metrics=deepcopy(metrics))


def preflight_october_native_window(inputs, *, request):
    """Run the exact native pre-solve gates without initializing any solver."""
    from .gd_october_native_inputs import build_october_native_model, assert_october_native_input_binding
    _require(isinstance(inputs, NativeWindowInputs) and isinstance(request, RollRequest), 'typed native inputs and causal request required')
    prepared = deepcopy(inputs.prepared)
    prepared_hash = validate_prepared(prepared, request)
    _assert_real_durations(prepared.window)
    settings, publication = inputs.settings.validate(), inputs.publication.validate(prepared.window)
    bridge = deepcopy(inputs.bridge_inputs)
    intent = dict(solver='HiGHS', stage='october_fixed_state_continuous_dispatch', threads=1,
        time_limit_s=settings.time_limit_s, mip_rel_gap=settings.mip_rel_gap, tee=settings.tee,
        objective_offset_yuan=0., fixed_state_lp=True, prepared_sha256=prepared_hash,
        selected_dispatch_id=publication.selected_dispatch_id, publication=publication.__dict__)
    model = build_october_native_model(known_dispatch_reference=inputs.known_dispatch_reference, solve_intent=intent, **bridge)
    before = assert_october_native_input_binding(model)
    assert_october_input_binding(model)
    require_october_economic_objective(model)
    assert_october_integrity(model)
    _assert_prepared_model(model, prepared, bridge)
    _require(not any(not v.fixed and v.is_integer() for v in model.component_data_objects(pyo.Var)), 'fixed-state LP required before solve')
    return dict(model=model, prepared=prepared, prepared_hash=prepared_hash, settings=settings,
                publication=publication, bridge=bridge, intent=intent, before=before)


def solve_october_native_window(inputs, *, request):
    """Build and solve internally; never accept an externally produced solution."""
    from .gd_october_native_inputs import assert_october_native_input_binding, bind_october_rt_result
    context = preflight_october_native_window(inputs, request=request)
    model, prepared, prepared_hash, settings, publication, bridge, intent, before = (
        context[k] for k in ("model", "prepared", "prepared_hash", "settings", "publication", "bridge", "intent", "before"))
    started = datetime.now(timezone.utc)
    genuine_entry = _original_internal_solver_path()
    result = gd_solve.solve_dispatch(model, stage=intent['stage'], time_limit_s=settings.time_limit_s,
        threads=1, mip_rel_gap=settings.mip_rel_gap, objective_offset_yuan=0., tee=settings.tee)
    ended = datetime.now(timezone.utc)
    genuine_entry = genuine_entry and _original_internal_solver_path()
    # No success artifact exists before every post-solve gate passes.
    after = assert_october_native_input_binding(model)
    _require(before == after, 'source/input binding changed during solver call')
    assert_october_input_binding(model); assert_october_integrity(model); require_october_economic_objective(model)
    _assert_prepared_model(model, prepared, bridge)
    check = _checked_result(result, model, settings)
    extraction = extract_october_model_steps(model, window=prepared.window, read_value=pyo.value)
    provenance = dict(call_path='gd_october_native_solve.solve_october_native_window -> gd_solve.solve_dispatch -> HighsSolver',
        evidence_kind='internal_highs_call' if genuine_entry else 'mock_internal_solver_flow',
        native_numerical_acceptance=genuine_entry, solver_called=genuine_entry,
        mock_flow_only=not genuine_entry, solver_backend='highs' if genuine_entry else MOCK_BACKEND,
        invocation_started_at=started, invocation_completed_at=ended, threads=1,
        solver_version=check['metrics'].get('solver_version'),
        solver_settings=check['metrics'].get('solver_settings'),
        original_objective_unchanged=True, fixed_state_lp=True, all_rows_and_bounds_checked=True,
        publication_clock_basis=publication.clock_basis, input_mode=prepared.mode,
        uses_future_actual=after['uses_future_actual'], derived_simulation=after['derived_simulation'],
        input_causal_eligible=after['causal_eligible'], causal_execution_certified=False,
        research_thermal_assumptions=after['research_thermal_assumptions'], operational_source_verified=False,
        source_authenticity_verified=False,
        official_algorithm_certified=False, whole_day_feasibility_verified=False,
        execution_confirmed=False, **check)
    p = ended if publication.clock_basis == 'runtime_wall_clock' else publication.published_at
    a = ended if publication.clock_basis == 'runtime_wall_clock' else publication.available_at
    backend = BACKEND if genuine_entry else MOCK_BACKEND
    document = dict(schema_version=SCHEMA, run_id=prepared.window.run_id, window_id=prepared.window.window_id,
        selected_dispatch_id=publication.selected_dispatch_id, prepared_sha256=prepared_hash,
        delivery_start=prepared.window.delivery_start, delivery_end=prepared.window.delivery_end,
        published_at=p, available_at=a, source=publication.source, backend=backend, research_only=True,
        initial_inventory=inventory(prepared.projection.projected_state.values), steps=extraction['steps'],
        solver_call_provenance=provenance,
        **{k: after[k] for k in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256','known_dispatch_reference')})
    raw = canonical_bytes(document)
    temporary = WindowSolution(raw, sha256(raw).hexdigest(), backend)
    parsed, _ = _selected_document_fields(json.loads(raw), temporary, prepared.window, schema=SCHEMA,
        backends=(BACKEND,MOCK_BACKEND), extra_fields=_EXTRA_FIELDS)
    _validate_selected_plan(parsed, prepared, prepared_hash)
    reference = bind_october_rt_result(model, selected_dispatch_id=publication.selected_dispatch_id, artifact=raw)
    receipt = _CallReceipt(raw, prepared_hash, canonical_bytes(provenance), reference)
    _ISSUED.add(receipt)
    return NativeWindowSolution(raw, temporary.selected_dispatch_sha256, backend, receipt)


def _native_selected_document(solution, window, expected_identity=None):
    _require(isinstance(solution, NativeWindowSolution), 'internal native solution receipt required; backend strings do not certify origin')
    receipt = solution._call_receipt
    _require(isinstance(receipt, _CallReceipt) and receipt in _ISSUED, 'solution was not issued by internal solver path')
    _require(type(solution.artifact) is bytes and solution.artifact == receipt.artifact and
             sha256(solution.artifact).hexdigest() == solution.selected_dispatch_sha256, 'internal selected bytes/hash changed')
    data = json.loads(solution.artifact)
    _require(canonical_bytes(data['solver_call_provenance']) == receipt.provenance, 'solver provenance changed')
    return _selected_document_fields(data, solution, window, expected_identity, schema=SCHEMA,
                                    backends=(BACKEND,MOCK_BACKEND), extra_fields=_EXTRA_FIELDS)


def validate_october_native_solution(solution, prepared, prepared_sha256):
    data, identity = _native_selected_document(solution, prepared.window)
    _require(solution._call_receipt.prepared_sha256 == prepared_sha256, 'internal result belongs to a different prepared input')
    _validate_selected_plan(data, prepared, prepared_sha256)
    return data, identity


def map_october_native_to_pricing_report(**kwargs):
    """Explicit same-RT research mapping only. No official price or settlement."""
    return _map_dispatch_to_pricing_report(**kwargs, selected_reader=_native_selected_document)
