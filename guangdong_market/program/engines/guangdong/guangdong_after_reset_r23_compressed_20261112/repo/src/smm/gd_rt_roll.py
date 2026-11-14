"""Provider-driven RT rolling orchestration, with a strict T-15 information gate.

This module imports no optimizer. Inject a single-window solver explicitly;
contract stubs are labelled and cannot count as native numerical acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from numbers import Real

from .gd_rt_inputs import (RTInputError, RTWindow, SelectedDAReference, ForecastVintage,
    BoundaryObservation, IssuedPlan, BoundaryProjection, _require, _text, _time, _num,
    map_da_reference, map_forecast, validate_boundary_projection, make_rt_window)


@dataclass(frozen=True)
class ApprovedCommitmentAdjustment:
    adjustment_id: str
    issued_at: datetime
    available_at: datetime
    approved_at: datetime
    approval_source: str
    source: str
    # Explicit global (thermal unit, period) replacements; no implicit startup.
    values: dict


@dataclass(frozen=True)
class RTWindowRequest:
    window_number: int
    window: RTWindow
    available_observations: tuple[BoundaryObservation, ...]
    available_execution_plans: tuple[IssuedPlan, ...]


@dataclass(frozen=True)
class PreparedRTWindow:
    window: RTWindow
    da_reference: SelectedDAReference
    forecast: ForecastVintage
    forecast_mode: str
    boundary: BoundaryProjection
    commitment_adjustments: tuple[ApprovedCommitmentAdjustment, ...]
    # Exact kwargs for run_real_time_reference. Services/PS/physical policies are
    # externally assembled, not guessed here or manufactured from DA lookahead.
    solve_kwargs: dict
    source: str


@dataclass(frozen=True)
class RTWindowOutcome:
    backend: str
    # Include original RT_SCED metrics even when selected result is polished.
    stage_metrics: tuple[dict, ...]
    first_boundary: dict
    first_interval_payload: dict
    reserve_diagnostics: dict
    source: str


class RTRollError(RuntimeError):
    def __init__(self, message, *, report, cause):
        super().__init__(message)
        self.report = deepcopy(report)
        self.completed_windows = deepcopy(report['completed_windows'])
        self.events = deepcopy(report['events'])
        self.cause = cause


def _commitment(prepared, mapped):
    window = prepared.window
    values = deepcopy(mapped['values']['thermal_commitment'])
    _require(isinstance(prepared.commitment_adjustments, tuple), 'explicit adjustment tuple required, including empty')
    changed = set(); ids = set()
    for adj in prepared.commitment_adjustments:
        _require(isinstance(adj, ApprovedCommitmentAdjustment), 'typed commitment adjustment required')
        for field in ('adjustment_id', 'approval_source', 'source'): _text(getattr(adj, field), field)
        _require(adj.adjustment_id not in ids, 'duplicate commitment adjustment ID'); ids.add(adj.adjustment_id)
        issued = _time(adj.issued_at, 'adjustment issued_at')
        _require(issued <= _time(adj.available_at, 'adjustment available_at') <= window.information_cutoff,
                 'commitment adjustment not available at cutoff')
        _require(issued <= _time(adj.approved_at, 'adjustment approved_at') <= window.information_cutoff,
                 'commitment adjustment not approved at cutoff')
        _require(isinstance(adj.values, dict) and bool(adj.values), 'explicit adjustment values required')
        for (unit, global_period), value in adj.values.items():
            _require((unit, global_period) not in changed, 'overlapping commitment adjustments require explicit reconciliation')
            _require(unit in prepared.da_reference.thermal_ids and global_period in window.global_periods, 'adjustment outside exact thermal/window scope')
            _require(type(value) is int and value in (0, 1), 'adjustment must be binary')
            changed.add((unit, global_period)); values[unit, window.global_to_local[global_period]] = value
    return values


def apply_projected_boundary(core_inputs, storage_inputs, projection):
    """Copy explicit projected physical state into initial fields, without resets.

    Uses terminal_thermal_state / terminal_storage_state field conventions.
    The projection is not validated against a cutoff until prepare validation.
    """
    _require(isinstance(projection, BoundaryProjection), 'BoundaryProjection required')
    core, storage = deepcopy(core_inputs), deepcopy(storage_inputs)
    state = projection.state
    _require(isinstance(state, dict) and set(state) == {'thermal', 'storage'}, 'projected state requires thermal and storage sections')
    _require(set(state['thermal']) == set(core['thermal']), 'projected thermal inventory mismatch')
    for unit, row in core['thermal'].items():
        initial = state['thermal'][unit]
        for source_key, target_key in (('online', 'initial_online'), ('power_mw', 'initial_power_mw'), ('state_age_h', 'initial_state_age_h')):
            _require(source_key in initial, unit + ': missing projected thermal field')
            row[target_key] = deepcopy(initial[source_key])
        if 'initial_state_kind' in initial:
            row['initial_state_kind'] = initial['initial_state_kind']
    if storage is None:
        _require(state['storage'] is None, 'projected storage supplied without physical inventory')
    else:
        st = state['storage']
        _require(isinstance(st, dict), 'explicit projected storage section required')
        _require(set(st['batteries']) == set(storage['batteries']) and set(st['pumped_plants']) == set(storage['pumped_plants']), 'projected storage inventory mismatch')
        units = {uid for plant in storage['pumped_plants'].values() for uid in plant['units']}
        _require(set(st['pumped_units']) == units, 'projected pumped unit inventory mismatch')
        for uid, row in storage['batteries'].items():
            for key in ('initial_energy_mwh', 'cycle_throughput_used_mwh', 'initial_hour_direction'):
                row[key] = deepcopy(st['batteries'][uid][key])
        for pid, row in storage['pumped_plants'].items():
            for key in ('upper_initial_m3', 'lower_initial_m3'): row[key] = deepcopy(st['pumped_plants'][pid][key])
            for uid, unit in row['units'].items():
                for key in ('initial_mode', 'initial_mode_elapsed_h', 'initial_generation_mw', 'remaining_start_stop_events'):
                    unit[key] = deepcopy(st['pumped_units'][uid][key])
    return core, storage


def validate_request_history(prepared, request):
    """Fail closed on substituted observations/plans, including same-ID mutation.

    New external observations must enter initial_observations (with their real
    availability times) or execute_first receipts. The provider cannot silently
    introduce a reset or convert an unissued lookahead into historical execution.
    """
    _require(isinstance(prepared, PreparedRTWindow), 'provider must return PreparedRTWindow')
    observations = {item.observation_id: item for item in request.available_observations}
    observation = prepared.boundary.observation
    _require(observation.observation_id in observations and observation == observations[observation.observation_id],
             'projected observation must exactly match available request history; new external data needs explicit ingestion')
    plans = {item.plan_id: item for item in request.available_execution_plans}
    for plan in prepared.boundary.plans:
        _require(plan.plan_id in plans and plan == plans[plan.plan_id],
                 'projection plan must exactly match previously issued available request history')
    return dict(observation_id=observation.observation_id, plan_ids=tuple(p.plan_id for p in prepared.boundary.plans),
                historical_id_and_content_consistency_verified=True)


def validate_prepared_window(prepared, expected_window):
    _require(isinstance(prepared, PreparedRTWindow), 'provider must return PreparedRTWindow')
    _require(prepared.window == expected_window, 'provider changed requested window')
    window = prepared.window
    _text(prepared.source, 'prepared input source')
    da = map_da_reference(prepared.da_reference, window)
    boundary_audit = validate_boundary_projection(prepared.boundary, window)
    kw = prepared.solve_kwargs
    _require(isinstance(kw, dict), 'explicit solve kwargs required')
    required = {'core_inputs', 'reserve', 'services', 'settings', 'commitment', 'state_source'}
    allowed = required | {'storage_inputs', 'pricing', 'piecewise_bids', 'stage_callback'}
    _require(required <= set(kw) and set(kw) <= allowed, 'invalid single-window workflow kwargs')
    _require('stage_callback' not in kw, 'runner owns stage callback to retain stage failure events')
    core = kw['core_inputs']; storage = kw.get('storage_inputs')
    _require(core['horizon'] == window.horizon, 'physical horizon differs from requested local chronology')
    _require(set(core['thermal']) == set(prepared.da_reference.thermal_ids), 'DA thermal inventory mismatch')
    expected_commitment = _commitment(prepared, da)
    _require(kw['commitment'] == expected_commitment, 'RT thermal commitment must follow selected DA plus explicit approved changes')
    series = tuple((family, bus) for family in ('load_mw', 'renewable_mw', 'physical_tie_mw') for bus in core['buses'])
    forecast = map_forecast(prepared.forecast, window, required_series=series, allowed_kind=prepared.forecast_mode)
    for family in ('load_mw', 'renewable_mw', 'physical_tie_mw'):
        expected = {(bus, t): forecast['values'][family, bus, t] for bus in core['buses'] for t in window.horizon.ids}
        _require(core[family] == expected, family + ': solver inputs differ from declared forecast vintage')
    projected_core, projected_storage = apply_projected_boundary(core, storage, prepared.boundary)
    _require(projected_core == core and projected_storage == storage, 'model initials differ from explicitly projected boundary')
    service, settings, state_source = kw['services'], kw['settings'], kw['state_source']
    _require(service.intervals == window.local_intervals and service.information_cutoff == window.information_cutoff, 'service clock/cutoff mismatch')
    _require(service.forecast_source == prepared.forecast.source, 'service forecast provenance differs from vintage')
    _require(settings.threads == 1 and type(settings.threads) is int and settings.mip_rel_gap == .001 and settings.require_gap_target is True,
             'rolling requires single-thread HiGHS, gap=.001 and stop-on-gap enabled')
    _require(state_source.actual_observed_at == prepared.boundary.observation.observed_at, 'state source must retain original observation time')
    _require(state_source.actual_initial_state_source == prepared.boundary.observation.source and state_source.initial_boundary_mapping_source == prepared.boundary.source,
             'state source differs from observed/projected provenance')
    if storage is None:
        _require(not (prepared.da_reference.battery_ids or prepared.da_reference.pumped_unit_ids or prepared.da_reference.pumped_plant_ids),
                 'selected DA storage inventory cannot be omitted from RT physical inputs')
    else:
        physical_units = [uid for plant in storage['pumped_plants'].values() for uid in plant['units']]
        _require(len(physical_units) == len(set(physical_units)) and set(physical_units) == set(prepared.da_reference.pumped_unit_ids),
                 'DA pumped unit inventory mismatch')
        _require(set(storage['batteries']) == set(prepared.da_reference.battery_ids) and set(storage['pumped_plants']) == set(prepared.da_reference.pumped_plant_ids), 'DA storage inventory mismatch')
        _require(storage['anchor_periods'] == da['anchor_periods'] and storage['day_end_period'] == da['day_end_period'], 'hour-end/day-end storage alignment differs from absolute intervals')
        for uid, row in storage['batteries'].items():
            _require(row['da_anchor_mwh'] == {t: da['battery_da_anchor_mwh'][uid, t] for t in da['anchor_periods']}, 'battery hour-end anchors must use selected DA end inventories')
            if row['direction_policy'] == 'follow_da':
                _require(row['da_direction'] == {t: da['values']['battery_direction'][uid, t] for t in window.horizon.ids}, 'battery direction must use selected DA')
        if storage['pumped_plants']:
            _require(storage.get('rt_pumped_boundary_policy') == 'external_initial_plan' and storage.get('pumped_plan_boundary') is not None,
                     'rolling PS requires strict external initial plan and explicit preparation records')
            pb = storage['pumped_plan_boundary']
            _require(pb.information_cutoff == window.information_cutoff and pb.model_initial_time == pb.first_calculation_time == window.start,
                     'pumped boundary must use this cutoff and explicit initial T mapping')
            _require(isinstance(pb.preparations, tuple), 'explicit pumped preparations tuple required')
        for pid, row in storage['pumped_plants'].items():
            for side in ('upper', 'lower'):
                _require(row['da_' + side + '_m3'] == {t: da['values'][side + '_volume_m3'][pid, t] for t in window.horizon.ids}, 'pumped DA reservoir references must use selected dispatch')
    return dict(window=window.audit(), forecast=forecast['audit'], da=da['audit'], boundary=boundary_audit,
                source=prepared.source, commitment_adjustment_ids=tuple(a.adjustment_id for a in prepared.commitment_adjustments),
                pumped_preparation_authenticity_verified=False, full_official_market_verified=False)


def _validate_outcome(outcome, window, execution_mode):
    _require(isinstance(outcome, RTWindowOutcome), 'solver callback must return RTWindowOutcome')
    _text(outcome.source, 'outcome source')
    expected = 'highs' if execution_mode == 'native_highs' else 'contract_stub_no_solver'
    _require(outcome.backend == expected, 'backend mismatch, no fallback or stub-as-native allowed')
    _require(isinstance(outcome.stage_metrics, tuple) and bool(outcome.stage_metrics), 'all original stage metrics required')
    original = []
    for metrics in outcome.stage_metrics:
        _require(isinstance(metrics, dict) and metrics.get('backend') == expected, 'stage backend mismatch')
        if metrics.get('stage') == 'RT_SCED': original.append(metrics)
        _require(metrics.get('requested_mip_rel_gap') == .001 and metrics.get('requested_gap_met') is True, 'original stage requested gap unmet; no execution or downstream windows')
        _require(_num(metrics.get('gap'), 'actual gap', 0.) <= .001 + 1e-9, 'reported gap exceeds target')
        _require(metrics.get('termination') in {'optimal', 'maxTimeLimit', 'stub_validated'}, 'unaccepted stage termination')
        if execution_mode == 'native_highs':
            _require(metrics.get('termination') != 'stub_validated', 'stub is not native acceptance')
            _require(bool(metrics.get('solver_version')) and bool(metrics.get('native_status')) and
                     isinstance(metrics.get('solver_settings'), dict), 'native solver version/status/settings evidence required')
    _require(len(original) == 1, 'exactly one original RT_SCED metric record required')
    _require(isinstance(outcome.first_boundary, dict) and outcome.first_boundary.get('through_period') == 0,
             'outcome must return only first local-period boundary, never lookahead end')
    for row in outcome.first_boundary.get('thermal', {}).values():
        _require('through_period' not in row or row['through_period'] == 0, 'thermal boundary contains lookahead state')
    storage_state = outcome.first_boundary.get('storage')
    if storage_state is not None:
        _require(isinstance(storage_state, dict) and storage_state.get('through_period') == 0, 'storage boundary contains lookahead state')
    _require(outcome.first_boundary.get('observation_status') == 'solved_plan_boundary_not_a_future_actual_observation', 'solved boundary must retain plan, not future observation label')
    _require(isinstance(outcome.first_interval_payload, dict) and bool(outcome.first_interval_payload), 'explicit first-interval command payload required')
    rd = outcome.reserve_diagnostics
    required = {'primary_requirement_mw', 'primary_provided_mw', 'primary_shortfall_mw',
                'emergency_requirement_mw', 'emergency_provided_mw', 'emergency_shortfall_mw',
                'thermal_emergency_available_mw', 'pumped_emergency_available_mw', 'source'}
    _require(isinstance(rd, dict) and required <= set(rd), 'explicit actual RT first-period reserve capability/shortfall diagnostics required')
    _text(rd['source'], 'reserve source')
    for key in required - {'source'}: _num(rd[key], key, -1e-6)
    for family in ('primary', 'emergency'):
        _require(rd[family+'_provided_mw'] + rd[family+'_shortfall_mw'] + 1e-5 >= rd[family+'_requirement_mw'], 'reserve diagnostic infeasible')
    _require(rd['emergency_provided_mw'] <= rd['thermal_emergency_available_mw'] + rd['pumped_emergency_available_mw'] + 1e-5, 'provided emergency reserve exceeds actual RT capability')
    return outcome


def run_rt_roll(*, intervals, execution_periods, window_periods, tail_policy, time_mapping_source,
                input_provider, solve_window, execute_first, initial_observations, initial_execution_plans,
                execution_mode, issuance_source, event_callback=None):
    """Run explicit windows. All providers are trusted research adapters, not sandboxes.

    execute_first receives only the issued first-interval plan and must return a
    real or simulated observation at its end. Such receipts remain hidden from
    earlier cutoffs, even though this offline sequential loop has computed them.
    """
    _require(execution_mode in {'native_highs', 'contract_stub_no_solver'}, 'explicit native/stub execution mode required')
    _text(issuance_source, 'issuance source')
    _require(all(callable(x) for x in (input_provider, solve_window, execute_first)), 'explicit provider, solver and execution callbacks required')
    _require(event_callback is None or callable(event_callback), 'event callback must be callable')
    _require(isinstance(execution_periods, tuple) and bool(execution_periods) and len(set(execution_periods)) == len(execution_periods), 'explicit unique execution-period tuple required')
    windows = [make_rt_window(intervals=intervals, first_global_period=g, window_periods=window_periods,
                             tail_policy=tail_policy, time_mapping_source=time_mapping_source) for g in execution_periods]
    for previous, current in zip(windows, windows[1:]):
        _require(previous.local_intervals[0][1] == current.start, 'execution sequence must be contiguous; skipped intervals need external execution records')
    _require(isinstance(initial_observations, tuple) and isinstance(initial_execution_plans, tuple), 'immutable initial observation/plan tuples required')
    observations, plans = deepcopy(list(initial_observations)), deepcopy(list(initial_execution_plans))
    for observation in observations:
        _require(isinstance(observation, BoundaryObservation), 'typed initial observation required'); observation.validate()
    for plan in plans:
        _require(isinstance(plan, IssuedPlan), 'typed initial issued plan required'); plan.validate()
    _require(len({o.observation_id for o in observations}) == len(observations), 'duplicate initial observation ID')
    _require(len({p.plan_id for p in plans}) == len(plans), 'duplicate initial execution plan ID')
    report = dict(completed_windows=[], issued_execution_plans=[], execution_observations=[], events=[],
                  execution_mode=execution_mode, native_numerical_acceptance=False, full_official_market_verified=False,
                  completed=False, stopped_at=None, failure_stage=None, failed_stage_metrics=[], failed_workflow_events=[],
                  causal_state_information_gate=True, provider_authenticity_verified=False)
    def emit(event, **details):
        entry = dict(event=event, **deepcopy(details)); report['events'].append(entry)
        if event_callback is not None: event_callback(deepcopy(entry))
    locked_da_reference = None
    for number, window in enumerate(windows):
        phase, outcome = 'input_provider', None
        try:
            emit('window_started', window_number=number, window=window.audit())
            request = RTWindowRequest(number, deepcopy(window),
                tuple(deepcopy(o) for o in observations if o.available_at <= window.information_cutoff),
                tuple(deepcopy(p) for p in plans if p.available_at <= window.information_cutoff))
            prepared = deepcopy(input_provider(deepcopy(request)))
            phase = 'input_validation'
            history_audit = validate_request_history(prepared, request)
            if locked_da_reference is not None:
                _require(prepared.da_reference == locked_da_reference,
                         'selected DA reference identity/content changed between rolling windows')
            audit = validate_prepared_window(prepared, window)
            if locked_da_reference is None:
                locked_da_reference = deepcopy(prepared.da_reference)
                report['selected_da_source_sha256'] = locked_da_reference.source_sha256
            audit['selected_da_identity_locked_across_windows'] = True
            audit['request_history'] = history_audit
            emit('window_inputs_validated', window_number=number, audit=audit)
            phase = 'solve_window'
            def stage_callback(event):
                emit('solver_stage', window_number=number, detail=event)
            if execution_mode == 'native_highs':
                from .gd_reference_workflow import native_workflow_path_intact
                _require(native_workflow_path_intact(), 'patched solver path cannot produce native RT evidence')
            outcome = solve_window(deepcopy(prepared), stage_callback)
            if execution_mode == 'native_highs':
                _require(native_workflow_path_intact(), 'native solver path changed during RT solve')
            phase = 'outcome_validation'; _validate_outcome(outcome, window, execution_mode)
            emit('window_solved', window_number=number, stage_metrics=outcome.stage_metrics,
                 reserve_diagnostics=outcome.reserve_diagnostics)
            phase = 'issue_first_interval'
            plan_id = 'rt_roll_window_' + str(number) + '_global_' + str(window.global_periods[0])
            _require(plan_id not in {p.plan_id for p in plans}, 'issued plan ID collides with existing history')
            plan = IssuedPlan(plan_id, window.information_cutoff, window.information_cutoff,
                window.start, window.local_intervals[0][1], issuance_source, 'issued_research_execution',
                dict(command=deepcopy(outcome.first_interval_payload), planned_boundary=deepcopy(outcome.first_boundary))).validate()
            plans.append(plan); report['issued_execution_plans'].append(deepcopy(plan))
            emit('first_interval_issued', window_number=number, plan_id=plan.plan_id)
            phase = 'execute_first'
            receipt = execute_first(deepcopy(plan))
            _require(isinstance(receipt, BoundaryObservation), 'execution callback must return typed observation receipt')
            receipt.validate()
            _require(receipt.kind in {'external_observation', 'executed_simulation'}, 'execution receipt cannot be a declared initial or a plan')
            _require(receipt.observed_at == plan.end, 'execution receipt must be at first interval end')
            _require(receipt.observation_id not in {o.observation_id for o in observations}, 'duplicate execution observation ID')
            observations.append(deepcopy(receipt)); report['execution_observations'].append(deepcopy(receipt))
            report['completed_windows'].append(dict(window_number=number, window=deepcopy(window), audit=deepcopy(audit),
                stage_metrics=deepcopy(outcome.stage_metrics), first_boundary=deepcopy(outcome.first_boundary),
                reserve_diagnostics=deepcopy(outcome.reserve_diagnostics), execution_observation=deepcopy(receipt)))
            phase = 'completion_callback'
            emit('window_completed', window_number=number, observed_at=receipt.observed_at.isoformat())
        except Exception as exc:
            report.update(stopped_at=number, failure_stage=phase, error=str(exc),
                failed_execution_outcome=('executed_confirmed_do_not_retry' if phase == 'completion_callback' else
                    'uncertain_after_issue_do_not_retry_automatically' if phase == 'execute_first' else 'not_executed'))
            if isinstance(outcome, RTWindowOutcome): report['failed_stage_metrics'] = deepcopy(outcome.stage_metrics)
            stages = getattr(exc, 'stages', {})
            if stages: report['failed_stage_metrics'] = [deepcopy(item.metrics) for item in stages.values()]
            report['failed_workflow_events'] = deepcopy(getattr(exc, 'events', []))
            report['events'].append(dict(event='window_failed', window_number=number, phase=phase, error=str(exc)))
            raise RTRollError('RT roll stopped at window ' + str(number) + ' (' + phase + '): ' + str(exc), report=report, cause=exc) from exc
    report['completed'] = True
    report['native_numerical_acceptance'] = execution_mode == 'native_highs'
    return deepcopy(report)


def outcome_from_workflow(result):
    """Read native single-window results; does not solve or relabel a plan as actual."""
    import pyomo.environ as pyo
    _require(isinstance(result, dict) and 'selected_dispatch' in result, 'single-window workflow result required')
    selected = result['selected_dispatch']; model = selected.model
    _require(model._energy_horizon.mode == 'real_time' and model._energy_horizon.ids[0] == 0, 'local RT horizon required')
    read = lambda value: float(pyo.value(value))
    reserve = model.gd_reserve; req = reserve._policy_scope['requirements']; t = 0
    thermal_available = sum(min(max(0., read(reserve.emergency_thermal[g, t]) +
        read(reserve.c_emergency_head[g, t].upper) - read(reserve.c_emergency_head[g, t].body)),
        _emergency_ramp_from_row(reserve, g, t)) for g in reserve.G)
    # Actual plant emergency variable is equality-defined by online generation
    # headroom plus interruptible pumping in gd_dispatch_reserves, not DA credit.
    pumped = sum(read(reserve.emergency_pumped[p, t]) for p in reserve.PS)
    rd = dict(primary_requirement_mw=float(req['primary_mw'][t]),
        primary_provided_mw=sum(read(reserve.primary_thermal[g, t]) for g in reserve.G)+sum(read(reserve.primary_pumped[p, t]) for p in reserve.PS),
        primary_shortfall_mw=read(reserve.primary_short[t]),
        emergency_requirement_mw=float(req['up_mw'][t])+float(req['deduction_mw'][t]),
        emergency_provided_mw=sum(read(reserve.emergency_thermal[g, t]) for g in reserve.G)+pumped,
        emergency_shortfall_mw=read(reserve.emergency_short[t]), thermal_emergency_available_mw=thermal_available,
        pumped_emergency_available_mw=pumped, source='selected RT dispatch reserve rows, not DA static available capacity')
    payload = dict(thermal_power_mw={g: read(model.power[g, t]) for g in model.G},
                   thermal_online={g: int(round(read(model.online[g, t]))) for g in model.G})
    if hasattr(model, 'gd_storage'):
        st = model.gd_storage
        payload['battery_charge_mw'] = {u: read(st.charge[u, t]) for u in st.BAT}
        payload['battery_discharge_mw'] = {u: read(st.discharge[u, t]) for u in st.BAT}
        payload['pumped_generation_mw'] = {u: read(st.generate[u, t]) for u in st.UNIT}
        payload['pumped_pumping_mw'] = {u: read(st.pump[u, t]) for u in st.UNIT}
    return RTWindowOutcome('highs', tuple(deepcopy(stage.metrics) for stage in result['stages'].values()),
        deepcopy(result['executed_boundary']), payload, rd, 'native single-window selected dispatch')


def _emergency_ramp_from_row(reserve, unit, period):
    import pyomo.environ as pyo
    return float(pyo.value(reserve.c_emergency_ramp[unit, period].upper))
