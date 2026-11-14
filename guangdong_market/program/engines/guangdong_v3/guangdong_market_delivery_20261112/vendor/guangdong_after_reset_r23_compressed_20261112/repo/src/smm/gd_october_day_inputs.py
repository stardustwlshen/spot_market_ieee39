"""Explicit window definitions and causal, initial-only adaptation."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timezone
from .gd_october_roll import (PreparedWindow, project_boundary, validate_prepared,
    apply_model_initials, apply_october_storage_initials, FIVE, QUARTER, SHANGHAI, _require, _text, fingerprint, _time, _parse)
from .gd_october_native_solve import NativeWindowInputs, _assert_real_durations

@dataclass(frozen=True)
class WindowDefinition:
    window: object
    physics: object
    input_sources: tuple
    mode: str
    effective_plan: object
    terminal: object
    bridge_inputs: dict
    known_dispatch_reference: object
    settings: object
    publication: object
    projection_policy: str
    projection_source: str
    prepared_source: str


def validate_definitions(definitions):
    _require(type(definitions) is tuple and 1 <= len(definitions) <= 96,
             'explicit 1..96 window definitions required')
    for d in definitions:
        _require(type(d) is WindowDefinition, 'typed window definition required')
        d.window.validate(); _assert_real_durations(d.window)
        _require(d.window.delivery_start.astimezone(timezone.utc)-d.window.information_cutoff.astimezone(timezone.utc)==QUARTER, 'day runner requires exact T-minus-15 cutoff')
        _require(d.mode in {'causal','research'}, 'explicit input mode required')
        _require(d.projection_policy == 'latest_available_observation_unique_issued_coverage', 'unsupported projection policy')
        if d.physics.thermal_trajectories is not None:
            from .gd_october_thermal import validate_book, require_trajectory_mode
            require_trajectory_mode(d.physics.thermal_trajectories,d.mode)
            validate_book(d.physics.thermal_trajectories,cutoff=d.window.information_cutoff,run_id=d.window.run_id)
        _text(d.projection_source, 'projection source'); _text(d.prepared_source, 'prepared source')
    baseline=definitions[0].physics
    for d in definitions:
        _require(fingerprint((d.physics.batteries,d.physics.pumped_plants,d.physics.pumped_units,d.physics.cycle_day_policy,d.physics.efficiency_basis)) == fingerprint((baseline.batteries,baseline.pumped_plants,baseline.pumped_units,baseline.cycle_day_policy,baseline.efficiency_basis)), 'cross-window numerical physics changes require segmented projection support')
    thermal_conditions = None
    previous_thermal_events = {}
    for d in definitions:
        book=d.physics.thermal_trajectories
        if book is not None:
            from .gd_october_thermal import validate_book
            records=validate_book(book)
            conditions={r['unit_id']:r['parameters'] for r in records}
            events={r['event_id']:r['_sha256'] for r in records}
            _require(all(events.get(k)==v for k,v in previous_thermal_events.items()),
                     'thermal event evidence cannot be replaced or removed across windows')
            previous_thermal_events=events
            if thermal_conditions is None: thermal_conditions=conditions
            _require(conditions==thermal_conditions, 'cross-window thermal resource conditions changed')
        elif thermal_conditions is not None:
            _require(False, 'thermal trajectory physics cannot disappear across windows')
    if thermal_conditions is not None:
        _require(all(d.physics.thermal_trajectories is not None for d in definitions), 'thermal physics must cover every rolling window')
    windows = tuple(d.window for d in definitions)
    _require(len({w.window_id for w in windows}) == len(windows), 'duplicate window')
    for a,b in zip(windows, windows[1:]):
        _require(a.run_id == b.run_id and (b.delivery_start.astimezone(timezone.utc)-a.delivery_start.astimezone(timezone.utc)).total_seconds()==900,
                 'same-run contiguous fifteen-minute windows required')
    return windows


def validate_static_sources(definitions):
    """Validate every explicit source package, without building future initials."""
    from .gd_october_window_job import validate_static_inputs
    from .gd_october_forecast_inputs import reject_future_lineage, ForecastInputError
    for d in definitions:
        b=d.bridge_inputs
        _require(b['mode']==d.mode, 'definition/bridge input mode mismatch')
        _require(_time(b['expected_delivery_start'],'expected start')==d.window.delivery_start, 'source delivery boundary mismatch')
        trajectory=b['physical_inputs']['model_inputs'].get('thermal_trajectories')
        _require((None if trajectory is None else trajectory.get('book'))==d.physics.thermal_trajectories,
                 'static model/roll thermal trajectory source mismatch')
        if trajectory is not None:
            from .gd_october_thermal import validate_book
            _require(set(trajectory)=={'book','window_start','information_cutoff'}, 'thermal context fields invalid')
            _require(_time(trajectory['window_start'],'thermal start')==d.window.delivery_start and
                     _time(trajectory['information_cutoff'],'thermal cutoff')==d.window.information_cutoff,
                     'static thermal window/cutoff mismatch')
            records=validate_book(trajectory['book'],cutoff=d.window.information_cutoff,run_id=d.window.run_id)
            for record in records:
                if any(d.window.delivery_start<=_time(step['start'],'thermal start')<d.window.delivery_end for step in record['steps']):
                    _require(record['effective_plan_id']==d.effective_plan.plan_id, 'static thermal effective plan mismatch')
        validate_static_inputs(window=d.window, settings=d.settings, publication=d.publication,
            bridge_inputs=b, known_dispatch_reference=d.known_dispatch_reference)
        def reject_future(value):
            # Apply the same historical-lineage gate to every retained JSON
            # artifact, including legacy metadata that is not a new snapshot.
            # A false outer/core flag cannot clear a future-truth source kind.
            try:
                reject_future_lineage(value)
            except ForecastInputError as exc:
                _require(False, 'future actual forbidden in causal day chronology: ' + str(exc))
        for source in d.input_sources:
            reject_future(source.validate(d.window,d.mode))
        for raw in b['source_artifacts'].values():
            _require(type(raw) is bytes, 'immutable retained source bytes required')
            # Opaque CSV/PDF evidence is allowed, but JSON-shaped metadata must
            # use the same strict parser as the source contracts. In particular
            # duplicate keys cannot erase an earlier future-truth declaration.
            _require(not raw.startswith((b'\xff\xfe', b'\xfe\xff', b'\x00\x00\xfe\xff')),
                     'retained JSON source must use UTF-8, not UTF-16/32')
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                if raw.removeprefix(b'\xef\xbb\xbf').lstrip().startswith((b'{',b'[')):
                    raise ValueError('invalid UTF-8 retained JSON source') from exc
                continue
            if not text.replace('\x00','').lstrip().startswith(('{','[')):continue
            _require('\x00' not in text, 'retained JSON source must use UTF-8, not UTF-16/32')
            _require(len(raw)<=32*1024*1024, 'retained JSON source exceeds size bound')
            try:parsed=_parse(raw)
            except RecursionError as exc:
                raise ValueError('retained JSON source nesting exceeds bound') from exc
            pending=[(parsed,0)];count=0
            while pending:
                node,depth=pending.pop();count+=1
                _require(depth<=128 and count<=1000000, 'retained JSON source nesting/node bound exceeded')
                if type(node) is dict:pending.extend((child,depth+1) for child in node.values())
                elif type(node) is list:pending.extend((child,depth+1) for child in node)
            reject_future(parsed)
    return len(definitions)


def prepare_definition(definition, request):
    """Select only request history, never read next-window receipts or projections."""
    d = deepcopy(definition); w = request.window
    _require(d.window == w, 'wrong window definition')
    validate_definitions((d,))
    observations = request.available_observations
    _require(observations and len({s.state_id for s in observations})==len(observations)
             and len({s.as_of for s in observations})==len(observations), 'missing/ambiguous observation history')
    for s in observations:
        s.validate()
        _require(s.kind != 'planned_projection' and s.available_at <= w.information_cutoff and s.as_of <= w.information_cutoff,
                 'future/projected observation in causal request')
    commands = request.available_commands
    _require(len({c.command_id for c in commands}) == len(commands), 'duplicate command history')
    for c in commands:
        c.validate()
        _require(c.run_id == w.run_id and c.available_at <= w.information_cutoff, 'unavailable/cross-run command')
    ordered = sorted(commands, key=lambda c:c.delivery_start)
    _require(all(a.delivery_end<=b.delivery_start for a,b in zip(ordered,ordered[1:])), 'overlapping command history')
    observation = max(observations, key=lambda s:s.as_of)
    cursor, selected = observation.as_of, []
    while cursor < w.delivery_start:
        matches = [c for c in commands if c.delivery_start == cursor]
        _require(len(matches)==1, 'missing or ambiguous issued coverage')
        selected.append(matches[0]); cursor=matches[0].delivery_end
    projection = project_boundary(observation=observation, commands=tuple(selected), window=w, physics=d.physics, source=d.projection_source)
    prepared = PreparedWindow(w,projection,d.physics,d.input_sources,d.mode,d.effective_plan,d.terminal,d.prepared_source)
    validate_prepared(prepared,request)
    bridge = deepcopy(d.bridge_inputs)
    physical = bridge['physical_inputs']; core=physical['model_inputs']
    patched = apply_model_initials(core,core['storage_spec'],projection.projected_state,
                                  operating_day=w.delivery_start.astimezone(SHANGHAI).date().isoformat())
    physical['model_inputs']=patched['core_inputs']
    if core['storage_spec'] is not None:
        physical['model_inputs']['storage_spec']=apply_october_storage_initials(core['storage_spec'],projection.projected_state,window=w)
    physical['initial_state']=dict(at=w.delivery_start.isoformat(),kind='planned_projection',source=d.projection_source)
    # Derived initial evidence only: never mutate original contract/source bytes.
    physical['sources']['initial_state']=dict(physical['sources']['initial_state'],
        source=d.projection_source,published_at=w.information_cutoff.isoformat(),available_at=w.information_cutoff.isoformat())
    return NativeWindowInputs(prepared,bridge,d.known_dispatch_reference,d.settings,d.publication)
