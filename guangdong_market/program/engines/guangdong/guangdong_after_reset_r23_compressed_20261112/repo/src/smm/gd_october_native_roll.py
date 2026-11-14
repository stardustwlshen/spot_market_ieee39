"""October internal solve plus simulated execution or supplied actual records.

No hardware/control-room adapter is implemented. Actual records are explicit
input evidence; simulation callbacks must not perform physical device actions.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

from .gd_october_roll import (
    OctoberRollError, ExecutionRejected, ExecutionReceipt, _require, _time, _text, fingerprint, canonical_bytes, _run_october_execution_engine,
)
from .gd_october_native_solve import (
    NativeWindowInputs, solve_october_native_window, validate_october_native_solution, _assert_real_durations,
)


@dataclass(frozen=True)
class DeclaredActualStep:
    """Supplied observations; no claim they executed a newly selected command."""
    record_id: str
    delivery_start: datetime
    delivery_end: datetime
    previous_state_sha256: str
    confirmed_at: datetime
    source: str
    actual_powers_mw: dict
    actual_states: dict
    end_state: object


def run_october_native_roll(*, windows, initial_observations, initial_commands,
        input_provider, execution_mode, issuance_source, simulation_executor=None,
        actual_inputs=None, execution_guard=None, event_callback=None, allow_mock_flow=False, solution_callback=None):
    """Only the internal HiGHS path may solve. No caller-provided solve callback.

    ``declared_actual_input`` consumes supplied actual observations, never
    calls a device. Mock flows require explicit opt-in and never acquire native
    numerical acceptance, regardless of their backend/status strings.
    """
    _require(type(allow_mock_flow) is bool, 'explicit mock flow choice required')
    _require(execution_mode in {'simulated_execution', 'declared_actual_input'}, 'execution receipt mode must be independent of solver backend')
    _require(type(windows) is tuple and bool(windows), 'explicit window tuple required')
    for w in windows:
        w.validate(); _assert_real_durations(w)
    for a,b in zip(windows,windows[1:]):
        _require((b.delivery_start.astimezone(timezone.utc)-a.delivery_start.astimezone(timezone.utc)).total_seconds()==900,
                 'rolling advance requires 900 actual UTC seconds')
    actual_associations, actual_snapshots = [], ()
    if execution_mode == 'simulated_execution':
        _require(callable(simulation_executor) and actual_inputs is None, 'simulation callback only; no actual records in simulation mode')
        executor, receipt_kind = simulation_executor, 'executed_simulation'
    else:
        _require(simulation_executor is None and type(actual_inputs) is tuple, 'declared actual mode requires immutable supplied observations, not a device callback')
        records = deepcopy(actual_inputs)
        actual_snapshots = tuple(canonical_bytes(r) for r in records)
        _require(all(isinstance(r, DeclaredActualStep) for r in records), 'typed declared actual steps required')
        _require(len({r.record_id for r in records}) == len(records), 'duplicate actual record identity')
        _require(len({(_time(r.delivery_start,'actual start'),_time(r.delivery_end,'actual end')) for r in records}) == len(records), 'ambiguous actual record interval')
        for record in records:
            _text(record.record_id, 'actual record id'); _text(record.source, 'actual source')
        def executor(command, previous):
            matches = [r for r in records if _time(r.delivery_start,'actual start') == command.delivery_start and
                       _time(r.delivery_end,'actual end') == command.delivery_end]
            if len(matches) != 1:
                raise ExecutionRejected('no unique declared actual input for this interval; no device action performed')
            record = matches[0]
            _require(record.previous_state_sha256 == fingerprint(previous), 'declared actual input does not continue actual boundary')
            # This is a newly declared research association, not modification of
            # an external receipt or evidence that the new command was sent.
            actual_associations.append(dict(record_id=record.record_id, record_sha256=fingerprint(record),
                command_sha256=fingerprint(command), association='postsolve_research_comparison_only',
                command_delivery_or_execution_authenticity_verified=False))
            return ExecutionReceipt('declared:'+record.record_id,command.command_id,fingerprint(command),
                record.previous_state_sha256,record.confirmed_at,record.source,
                deepcopy(record.actual_powers_mw),deepcopy(record.actual_states),deepcopy(record.end_state))
        receipt_kind = 'external_observation'
    pending, requests, solutions = {}, {}, []
    def provider(request):
        native = deepcopy(input_provider(deepcopy(request)))
        _require(isinstance(native, NativeWindowInputs), 'native provider must return NativeWindowInputs, not a model/result')
        pending[request.window.window_id], requests[request.window.window_id] = native, deepcopy(request)
        return deepcopy(native.prepared)
    def solve(prepared):
        if event_callback is not None:
            event_callback(dict(event='native_solve_attempted', window_id=prepared.window.window_id))
        return solve_october_native_window(pending[prepared.window.window_id], request=requests[prepared.window.window_id])
    def validate(solution, prepared, prepared_sha256):
        selected, identity = validate_october_native_solution(solution, prepared, prepared_sha256)
        if not allow_mock_flow and not solution.provenance['native_numerical_acceptance'] and event_callback is not None:
            event_callback(dict(event='mock_solution_rejected', window_id=prepared.window.window_id, provenance=deepcopy(solution.provenance)))
        _require(allow_mock_flow or solution.provenance['native_numerical_acceptance'], 'mock flow is not native execution acceptance; explicit test opt-in required')
        if solution_callback is not None:
            solution_callback(deepcopy(solution), deepcopy(prepared), deepcopy(identity))
        solutions.append(deepcopy(solution))
        return selected, identity
    def decorate(report):
        report.update(solver_backend='highs' if solutions and all(s.provenance['native_numerical_acceptance'] for s in solutions) else 'not_native_accepted',
            native_numerical_acceptance=bool(report['completed']) and len(solutions)==len(windows) and all(s.provenance['native_numerical_acceptance'] for s in solutions),
            requested_window_count=len(windows), solved_window_count=len(solutions),
            all_requested_executions_confirmed=bool(report['completed']) and len(report['confirmed_executions'])==3*len(windows),
            native_accepted_windows=sum(s.provenance['native_numerical_acceptance'] for s in solutions),
            selected_solutions=tuple(solutions), actual_device_actions_performed=False,
            execution_receipt_kind=receipt_kind, declared_actual_associations=deepcopy(actual_associations),
            declared_actual_input_snapshots=actual_snapshots,
            actual_snapshot_semantics='canonical serialization of supplied records, not authenticated upstream raw bytes',
            command_delivery_or_execution_authenticity_verified=False, mock_flow_only=any(s.provenance['mock_flow_only'] for s in solutions))
        return report
    try:
        result = _run_october_execution_engine(windows=windows, initial_observations=initial_observations,
            initial_commands=initial_commands, input_provider=provider, solve_window=solve, execute_step=executor,
            execution_mode=execution_mode, issuance_source=issuance_source, execution_guard=execution_guard,
            event_callback=event_callback, solution_validator=validate, receipt_kind=receipt_kind)
    except OctoberRollError as exc:
        raise OctoberRollError(str(exc), decorate(exc.report), exc.cause) from exc
    return decorate(result)
