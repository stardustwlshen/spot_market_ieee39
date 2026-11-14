"""October 24x5 minute rolling contracts and explicitly selected 8x15 mapping.

Pure Python: no solver imports, automatic native execution, pricing or cash.
A planned boundary is never an observation. Only confirmed execution receipts
advance the execution ledger. The full-window/three-step execution partition is
an engineering choice, not a certification of the rule's tstart interpretation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import isclose, isfinite
from numbers import Real
from zoneinfo import ZoneInfo

FIVE = timedelta(minutes=5)
QUARTER = timedelta(minutes=15)
DT_H = 1 / 12
SHANGHAI = ZoneInfo('Asia/Shanghai')
SCHEMA = 'gd.october_roll.selected.v1'
STUB = 'synthetic_stub_no_solver'
STATE_FAMILIES = ('thermal', 'batteries', 'pumped_units')
POWER_FAMILIES = ('thermal', 'battery_charge', 'battery_discharge', 'pumped_generation', 'pumped_pumping')
SL_FIELDS = ('upper_positive', 'upper_negative', 'lower_positive', 'lower_negative')


class OctoberRollInputError(ValueError):
    pass


class ExecutionRejected(RuntimeError):
    """Executor explicitly reports that the requested step did not execute."""


class OctoberRollError(RuntimeError):
    def __init__(self, message, report, cause):
        super().__init__(message)
        self.report, self.cause = deepcopy(report), cause


def _require(ok, message):
    if not ok:
        raise OctoberRollInputError(message)


def _text(value, label):
    _require(type(value) is str and bool(value.strip()), label + ': nonempty source/text required')
    return value


def _time(value, label):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            pass
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             label + ': timezone-aware time required')
    return value


def _number(value, label, minimum=None):
    _require(not isinstance(value, bool) and isinstance(value, Real) and isfinite(float(value)), label + ': finite number required')
    _require(minimum is None or value >= minimum, label + ': below minimum')
    return float(value)


def _keys(value, keys, label):
    _require(type(value) is dict and set(value) == set(keys), label + ': exact fields/resource coverage required')


def _digest(value, label):
    _require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value), label + ': SHA256 required')
    return value


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'bytes_sha256': sha256(value).hexdigest()}
    if isinstance(value, dict):
        _require(all(type(k) is str for k in value), 'fingerprinted dictionary requires string keys')
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    _require(value is None or type(value) in (str, int, float, bool), 'plain JSON-compatible input required')
    return value


def canonical_bytes(value):
    return json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(value):
    return sha256(canonical_bytes(value)).hexdigest()


def _parse(raw):
    _require(type(raw) is bytes, 'immutable source bytes required')
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    def bad(value):
        raise OctoberRollInputError('nonfinite JSON constant: ' + value)
    try:
        data = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
        canonical_bytes(data)
        return data
    except (ValueError, TypeError, UnicodeError) as exc:
        raise OctoberRollInputError('invalid source JSON: ' + str(exc)) from exc


def _close(actual, expected, label):
    _require(isclose(_number(actual, label), _number(expected, label), rel_tol=0., abs_tol=1e-7), label + ': transition arithmetic mismatch')


def _same_numbers(actual, expected, label):
    if isinstance(expected, dict):
        _keys(actual, expected, label)
        for key in expected:
            _same_numbers(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        _close(actual, expected, label)
    else:
        _require(actual == expected, label + ': value mismatch')


@dataclass(frozen=True)
class RollingWindow:
    run_id: str
    window_id: str
    delivery_start: datetime
    information_cutoff: datetime
    timing_profile: str
    timing_source: str
    active_sced_start_index: int = 0

    @property
    def delivery_end(self):
        return self.delivery_start + 24 * FIVE

    @property
    def intervals(self):
        return tuple((self.delivery_start + i * FIVE, self.delivery_start + (i + 1) * FIVE) for i in range(24))

    def validate(self):
        for field in ('run_id', 'window_id', 'timing_source'):
            _text(getattr(self, field), field)
        start, cutoff = _time(self.delivery_start, 'delivery_start'), _time(self.information_cutoff, 'information_cutoff')
        local = start.astimezone(SHANGHAI)
        _require(local.minute % 15 == 0 and local.second == 0 and local.microsecond == 0, 'Shanghai quarter-hour start required')
        _require(type(self.active_sced_start_index) is int and self.active_sced_start_index == 0,
                 'this engineering full-window profile supports active_sced_start_index=0 only; no inferred tstart')
        _require(self.timing_profile in {'official_reference_T_minus_15', 'research_declared_clock'}, 'explicit timing profile required')
        _require(cutoff <= start, 'information cutoff after delivery start')
        if self.timing_profile == 'official_reference_T_minus_15':
            _require(cutoff == start - QUARTER, 'official reference cutoff must equal T-15 minutes')
        return self


@dataclass(frozen=True)
class StateRecord:
    state_id: str
    as_of: datetime
    published_at: datetime
    available_at: datetime
    kind: str
    source: str
    values: dict

    def validate(self):
        _text(self.state_id, 'state_id'); _text(self.source, 'state source')
        _require(self.kind in {'declared_initial', 'external_observation', 'executed_simulation', 'planned_projection'}, 'unknown state kind')
        at, published, available = (_time(getattr(self, k), k) for k in ('as_of', 'published_at', 'available_at'))
        _require(published <= available, 'state publication after availability')
        if self.kind != 'planned_projection':
            _require(at <= published, 'observation/initial state cannot be published before observed boundary')
        validate_state_values(self.values)
        current_day = at.astimezone(SHANGHAI).date().isoformat()
        for battery in self.values['batteries'].values():
            _require(not any(day > current_day and amount != 0 for day, amount in battery['throughput_mwh_by_day'].items()), 'future-day executed throughput cannot already exist')
        return self


def validate_state_values(values):
    _keys(values, ('thermal', 'batteries', 'pumped_plants', 'pumped_units'), 'state')
    for family in values:
        _require(type(values[family]) is dict, 'state family must be explicit, including empty')
        for unit in values[family]:
            _text(unit, family + ' resource')
    for unit, row in values['thermal'].items():
        _keys(row, {'online', 'power_mw', 'state_age_h'} | ({'trajectory'} if 'trajectory' in row else set()), unit)
        if 'trajectory' in row:
            from .gd_october_thermal import validate_cursor
            validate_cursor(row['trajectory'])
            _require(row['online']==1, 'thermal trajectory state must be connected')
        _require(type(row['online']) is int and row['online'] in (0, 1), 'thermal online must be integer binary')
        _number(row['power_mw'], unit, 0.); _number(row['state_age_h'], unit, 0.)
        _require(row['online'] == 1 or row['power_mw'] == 0, 'offline thermal has nonzero power')
    for unit, row in values['batteries'].items():
        _keys(row, ('energy_mwh', 'cumulative_throughput_mwh', 'throughput_mwh_by_day', 'mode', 'mode_elapsed_h', 'net_power_mw'), unit)
        _number(row['energy_mwh'], unit, 0.); _number(row['cumulative_throughput_mwh'], unit, 0.)
        _require(row['mode'] in {'idle', 'charge', 'discharge'}, 'unknown battery actual mode')
        _number(row['mode_elapsed_h'], unit, 0.); _number(row['net_power_mw'], unit)
        _require((row['mode'] == 'idle' and row['net_power_mw'] == 0) or
                 (row['mode'] == 'charge' and row['net_power_mw'] <= 0) or
                 (row['mode'] == 'discharge' and row['net_power_mw'] >= 0), 'battery actual mode/power mismatch')
        _require(type(row['throughput_mwh_by_day']) is dict and bool(row['throughput_mwh_by_day']), 'explicit day throughput history required')
        for day, throughput in row['throughput_mwh_by_day'].items():
            try:
                _require(datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') == day, 'invalid throughput day')
            except (TypeError, ValueError) as exc:
                raise OctoberRollInputError('invalid throughput day') from exc
            _number(throughput, 'daily throughput', 0.)
        _require(sum(row['throughput_mwh_by_day'].values()) <= row['cumulative_throughput_mwh'] + 1e-7,
                 'daily history exceeds cumulative throughput')
    for unit, row in values['pumped_plants'].items():
        _keys(row, ('upper_m3', 'lower_m3'), unit)
        for value in row.values():
            _number(value, unit, 0.)
    for unit, row in values['pumped_units'].items():
        _keys(row, ('mode', 'mode_elapsed_h', 'net_power_mw'), unit)
        _require(row['mode'] in {'off', 'pump', 'generate'}, 'unknown pumped mode')
        _number(row['mode_elapsed_h'], unit, 0.); _number(row['net_power_mw'], unit)
        _require((row['mode'] == 'off' and row['net_power_mw'] == 0) or
                 (row['mode'] == 'pump' and row['net_power_mw'] <= 0) or
                 (row['mode'] == 'generate' and row['net_power_mw'] >= 0), 'pumped actual mode/power mismatch')
    return values


@dataclass(frozen=True)
class Physics:
    batteries: dict
    pumped_plants: dict
    pumped_units: dict
    source: str
    cycle_day_policy: str
    efficiency_basis: str
    thermal_trajectories: dict | None = None

    def validate(self, values):
        if self.thermal_trajectories is not None:
            from .gd_october_thermal import validate_book
            records=validate_book(self.thermal_trajectories)
            _require({d['unit_id'] for d in records}<=set(values['thermal']), 'unknown thermal physics resource')
        else:
            _require(all('trajectory' not in r for r in values['thermal'].values()), 'thermal carryover lacks trajectory physics')
        _text(self.source, 'physics source')
        _require(self.efficiency_basis in {'explicit_per_direction_efficiencies_research', 'symmetric_sqrt_roundtrip'}, 'explicit battery efficiency basis required')
        _require(self.cycle_day_policy == 'delivery_interval_start_Asia_Shanghai', 'explicit cycle-day accounting selection required')
        for family in ('batteries', 'pumped_plants', 'pumped_units'):
            _keys(getattr(self, family), values[family], 'physics ' + family)
        for unit, row in self.batteries.items():
            _keys(row, ('eta_charge', 'eta_discharge', 'min_mwh', 'max_mwh', 'rated_mwh', 'max_cycles_per_day'), unit)
            for key in row:
                _number(row[key], unit + '.' + key, 0.)
            _require(0 < row['eta_charge'] <= 1 and 0 < row['eta_discharge'] <= 1 and row['rated_mwh'] > 0 and row['min_mwh'] <= row['max_mwh'] <= row['rated_mwh'], 'invalid battery physics')
            if self.efficiency_basis == 'symmetric_sqrt_roundtrip':
                _require(row['eta_charge'] == row['eta_discharge'], 'symmetric roundtrip efficiency requires equal square-root factors')
            _require(row['min_mwh'] <= values['batteries'][unit]['energy_mwh'] <= row['max_mwh'], 'initial battery hard energy bound exceeded')
        for unit, row in self.pumped_plants.items():
            _keys(row, ('upper_min_m3', 'upper_max_m3', 'lower_min_m3', 'lower_max_m3'), unit)
            for key, value in row.items():
                _number(value, unit + '.' + key, 0.)
            _require(row['upper_min_m3'] <= row['upper_max_m3'] and row['lower_min_m3'] <= row['lower_max_m3'], 'invalid reservoir bounds')
            for side in ('upper', 'lower'):
                _require(row[side + '_min_m3'] <= values['pumped_plants'][unit][side + '_m3'] <= row[side + '_max_m3'], 'initial reservoir hard bound exceeded')
        for unit, row in self.pumped_units.items():
            _keys(row, ('plant_id', 'pump_m3_per_mwh', 'generate_m3_per_mwh'), unit)
            _require(row['plant_id'] in self.pumped_plants, 'unknown pumped plant')
            _require(_number(row['pump_m3_per_mwh'], unit, 0.) > 0 and _number(row['generate_m3_per_mwh'], unit, 0.) > 0, 'positive water transfer coefficients required')
        return self


def inventory(values):
    return {'battery_energy_mwh': {u: r['energy_mwh'] for u, r in values['batteries'].items()},
            'upper_volume_m3': {u: r['upper_m3'] for u, r in values['pumped_plants'].items()},
            'lower_volume_m3': {u: r['lower_m3'] for u, r in values['pumped_plants'].items()}}


def _validate_power_states(powers, states, values):
    _keys(powers, POWER_FAMILIES, 'dispatch powers'); _keys(states, STATE_FAMILIES, 'dispatch states')
    for family in STATE_FAMILIES:
        _keys(states[family], values[family], 'dispatch state ' + family)
    expected = {'thermal': 'thermal', 'battery_charge': 'batteries', 'battery_discharge': 'batteries',
                'pumped_generation': 'pumped_units', 'pumped_pumping': 'pumped_units'}
    for family, state_family in expected.items():
        _keys(powers[family], values[state_family], family)
        for unit, value in powers[family].items():
            _number(value, family + '.' + unit, None if family == 'battery_charge' else 0.)
            if family == 'battery_charge':
                _require(value <= 0, 'charging power must be nonpositive')
    for unit, online in states['thermal'].items():
        _require(type(online) is int and online in (0, 1), 'thermal dispatch state must be integer binary')
        _require(online or powers['thermal'][unit] == 0, 'offline thermal dispatch power')
    for unit, mode in states['batteries'].items():
        _require(mode in {'idle', 'charge', 'discharge'}, 'unknown battery state')
        charge, discharge = powers['battery_charge'][unit], powers['battery_discharge'][unit]
        _require((mode == 'idle' and charge == discharge == 0) or (mode == 'charge' and discharge == 0) or
                 (mode == 'discharge' and charge == 0), 'battery mode/power mismatch')
    for unit, mode in states['pumped_units'].items():
        _require(mode in {'off', 'pump', 'generate'}, 'unknown pumped state')
        gen, pump = powers['pumped_generation'][unit], powers['pumped_pumping'][unit]
        _require((mode == 'off' and gen == pump == 0) or (mode == 'pump' and gen == 0) or
                 (mode == 'generate' and pump == 0), 'pumped mode/power mismatch')


def advance_state_values(values, *, powers_mw, states, physics, delivery_start, delivery_end):
    """Five-minute arithmetic, not network/reserve/ramp feasibility certification.

    The explicitly selected day policy assigns energy to interval-start Shanghai
    date. Old daily buckets and cumulative throughput survive midnight unchanged.
    """
    validate_state_values(values); physics.validate(values)
    start, end = _time(delivery_start, 'start'), _time(delivery_end, 'end')
    _require(end - start == FIVE, 'state advance must be exactly five minutes')
    _validate_power_states(powers_mw, states, values)
    result = deepcopy(values)
    for unit, row in result['thermal'].items():
        online = states['thermal'][unit]
        if physics.thermal_trajectories is not None:
            from .gd_october_thermal import validate_book, advance_thermal
            records=validate_book(physics.thermal_trajectories)
            result['thermal'][unit]=advance_thermal(row,unit=unit,power=powers_mw['thermal'][unit],online=online,
                records=records,start=start,end=end)
        else:
            row['state_age_h'] = row['state_age_h'] + DT_H if row['online'] == online else DT_H
            row.update(online=online, power_mw=powers_mw['thermal'][unit])
    for unit, row in result['batteries'].items():
        p = physics.batteries[unit]
        charge = -powers_mw['battery_charge'][unit] * p['eta_charge'] * DT_H
        discharge = powers_mw['battery_discharge'][unit] / p['eta_discharge'] * DT_H
        mode = states['batteries'][unit]
        row['mode_elapsed_h'] = row['mode_elapsed_h'] + DT_H if row['mode'] == mode else DT_H
        row.update(mode=mode, net_power_mw=powers_mw['battery_charge'][unit] + powers_mw['battery_discharge'][unit])
        row['energy_mwh'] += charge - discharge
        throughput = charge + discharge
        row['cumulative_throughput_mwh'] += throughput
        day = start.astimezone(SHANGHAI).date().isoformat()
        row['throughput_mwh_by_day'][day] = row['throughput_mwh_by_day'].get(day, 0.) + throughput
        next_day = end.astimezone(SHANGHAI).date().isoformat()
        if next_day != day:
            row['throughput_mwh_by_day'].setdefault(next_day, 0.)
        _require(p['min_mwh'] <= row['energy_mwh'] <= p['max_mwh'], 'battery hard energy bound exceeded')
        _require(row['throughput_mwh_by_day'][day] <= 2 * p['rated_mwh'] * p['max_cycles_per_day'], 'daily cycle budget exceeded')
    for unit, row in result['pumped_units'].items():
        p = physics.pumped_units[unit]; mode = states['pumped_units'][unit]
        gen, pump = powers_mw['pumped_generation'][unit], powers_mw['pumped_pumping'][unit]
        row['mode_elapsed_h'] = row['mode_elapsed_h'] + DT_H if row['mode'] == mode else DT_H
        row.update(mode=mode, net_power_mw=gen - pump)
        transfer = (gen * p['generate_m3_per_mwh'] - pump * p['pump_m3_per_mwh']) * DT_H
        plant = result['pumped_plants'][p['plant_id']]
        plant['upper_m3'] -= transfer; plant['lower_m3'] += transfer
    for unit, row in result['pumped_plants'].items():
        p = physics.pumped_plants[unit]
        for side in ('upper', 'lower'):
            _require(p[side + '_min_m3'] <= row[side + '_m3'] <= p[side + '_max_m3'], 'reservoir hard bound exceeded')
    validate_state_values(result)
    return result


@dataclass(frozen=True)
class IssuedCommand:
    command_id: str
    run_id: str
    window_id: str
    selected_dispatch_id: str
    selected_dispatch_sha256: str
    delivery_start: datetime
    delivery_end: datetime
    published_at: datetime
    available_at: datetime
    source: str
    powers_mw: dict
    states: dict

    def validate(self):
        for field in ('command_id', 'run_id', 'window_id', 'selected_dispatch_id', 'source'):
            _text(getattr(self, field), field)
        _digest(self.selected_dispatch_sha256, 'command selected hash')
        start, end, published, available = (_time(getattr(self, key), key) for key in ('delivery_start', 'delivery_end', 'published_at', 'available_at'))
        _require(end - start == FIVE and published <= available <= start, 'command timing invalid')
        return self


@dataclass(frozen=True)
class BoundaryProjection:
    observation_id: str
    observation_sha256: str
    command_ids: tuple[str, ...]
    command_sha256: tuple[str, ...]
    projected_state: StateRecord
    source: str


@dataclass(frozen=True)
class RollRequest:
    window_number: int
    window: RollingWindow
    available_observations: tuple[StateRecord, ...]
    available_commands: tuple[IssuedCommand, ...]


def project_boundary(*, observation, commands, window, physics, source):
    """Compute, but never register, a boundary from causally available issued plans."""
    window.validate(); observation.validate(); _text(source, 'projection source')
    _require(observation.kind != 'planned_projection', 'projection cannot recursively masquerade as an observation')
    _require(observation.available_at <= window.information_cutoff and observation.as_of <= window.information_cutoff,
             'projection observation unavailable at cutoff')
    if physics.thermal_trajectories is not None:
        from .gd_october_thermal import validate_book
        validate_book(physics.thermal_trajectories,cutoff=window.information_cutoff,run_id=window.run_id)
    values, cursor = deepcopy(observation.values), observation.as_of
    _require(isinstance(commands, tuple), 'explicit ordered command tuple required')
    for command in commands:
        command.validate()
        _require(command.run_id == window.run_id, 'cross-run projection command')
        _require(command.available_at <= window.information_cutoff, 'projection command unavailable at cutoff')
        _require(command.delivery_start == cursor and command.delivery_end <= window.delivery_start, 'projection commands must exactly cover boundary gap without overlap')
        if physics.thermal_trajectories is not None:
            from .gd_october_thermal import validate_book, _active, trajectory_available_at
            for unit in values['thermal']:
                match=_active(validate_book(physics.thermal_trajectories),unit,command.delivery_start)
                if match is not None:
                    _require(_time(trajectory_available_at(match[0]),'curve available')<=command.available_at,
                             'projection command predates its thermal trajectory evidence')
        values = advance_state_values(values, powers_mw=command.powers_mw, states=command.states, physics=physics,
                                      delivery_start=command.delivery_start, delivery_end=command.delivery_end)
        cursor = command.delivery_end
    _require(cursor == window.delivery_start, 'projection missing issued command coverage')
    state = StateRecord('projected:' + window.window_id, cursor, window.information_cutoff, window.information_cutoff,
                        'planned_projection', source, values).validate()
    return BoundaryProjection(observation.state_id, fingerprint(observation), tuple(p.command_id for p in commands),
                              tuple(fingerprint(p) for p in commands), state, source)


@dataclass(frozen=True)
class SourceArtifact:
    source_id: str
    artifact: bytes
    expected_sha256: str
    supporting_artifacts: dict = field(default_factory=dict)

    def validate(self, window, mode):
        _text(self.source_id, 'source id'); _digest(self.expected_sha256, 'source sha')
        _require(type(self.artifact) is bytes and sha256(self.artifact).hexdigest() == self.expected_sha256, 'input source bytes/hash mismatch')
        raw = _parse(self.artifact)
        from .gd_october_rt_contract import _series
        _require(type(raw) is dict and 'identity' in raw, 'source identity required')
        _require(raw['identity'].get('run_id') == window.run_id and raw['identity'].get('window_id') == window.window_id,
                 'input source run/window identity mismatch')
        _require(raw.get('stage') == 'sced', 'rolling physical inputs must be SCED series')
        _require(type(self.supporting_artifacts) is dict, 'explicit supporting source bytes required')
        spec = {key: raw.get(key) for key in ('stage', 'family', 'unit', 'resource_ids')}
        _series(raw, spec, raw['identity'], window.intervals, window.information_cutoff, mode,
                self.supporting_artifacts, {}, self.source_id)
        return raw


@dataclass(frozen=True)
class EffectivePlan:
    plan_id: str
    source: str
    published_at: datetime
    available_at: datetime
    approved_at: datetime
    approval_source: str
    # 24 explicit state rows, not inferred from actual off or initial power.
    states: tuple[dict, ...]
    # Explicit preparation authorizations for each pumped mode transition.
    preparations: tuple[dict, ...]
    minimum_mode_h: dict

    def validate(self, window, initial):
        for key in ('plan_id', 'source', 'approval_source'):
            _text(getattr(self, key), key)
        p, a, approved = (_time(getattr(self, k), k) for k in ('published_at', 'available_at', 'approved_at'))
        _require(p <= a <= window.information_cutoff and p <= approved <= window.information_cutoff, 'effective plan not available/approved at cutoff')
        _require(type(self.states) is tuple and len(self.states) == 24, 'effective plan needs 24 states')
        _require(type(self.preparations) is tuple, 'explicit preparations required, including empty tuple')
        _keys(self.minimum_mode_h, initial['pumped_units'], 'pumped minimum residence inventory')
        for unit, minimums in self.minimum_mode_h.items():
            _keys(minimums, ('off', 'pump', 'generate'), 'explicit minimum mode residence')
            for value in minimums.values():
                _number(value, 'minimum mode duration', 0.)
        preparations = {}
        ids = set()
        for prep in self.preparations:
            _keys(prep, ('preparation_id', 'transition_period', 'unit_id', 'from_mode', 'to_mode', 'preparation_started_at',
                         'ready_at', 'required_h', 'published_at', 'available_at', 'approved_at', 'approval_source', 'source'), 'preparation')
            for field_name in ('source', 'approval_source', 'preparation_id'):
                _text(prep[field_name], 'preparation ' + field_name)
            _require(prep['preparation_id'] not in ids, 'preparation authorization reused')
            ids.add(prep['preparation_id'])
            _require(type(prep['transition_period']) is int and 1 <= prep['transition_period'] <= 24, 'exact preparation transition period required')
            _require(prep['unit_id'] in initial['pumped_units'], 'unknown preparation unit')
            key = (prep['unit_id'], prep['transition_period'])
            _require(key not in preparations, 'ambiguous duplicate transition authorization')
            _require(prep['from_mode'] in {'off', 'pump', 'generate'} and prep['to_mode'] in {'off', 'pump', 'generate'}, 'unknown preparation mode')
            published, available, approved = (_time(prep[k], 'preparation ' + k) for k in ('published_at', 'available_at', 'approved_at'))
            _require(published <= available <= window.information_cutoff and published <= approved <= window.information_cutoff,
                     'preparation unavailable/unapproved or inconsistent publication time')
            began, ready = _time(prep['preparation_started_at'], 'prep began'), _time(prep['ready_at'], 'prep ready')
            _require((ready - began).total_seconds() / 3600 >= _number(prep['required_h'], 'required preparation duration', 0.),
                     'preparation elapsed duration insufficient')
            preparations[key] = prep
        previous = {u: r['mode'] for u, r in initial['pumped_units'].items()}
        age = {u: r['mode_elapsed_h'] for u, r in initial['pumped_units'].items()}
        entered = {u: window.delivery_start - timedelta(hours=age[u]) for u in previous}
        used = set()
        for index, states in enumerate(self.states):
            _keys(states, STATE_FAMILIES, 'effective states')
            for family in STATE_FAMILIES:
                _keys(states[family], initial[family], 'effective state resources')
            for unit, online in states['thermal'].items():
                _require(type(online) is int and online in (0, 1), 'thermal effective state must be binary')
            for mode in states['batteries'].values():
                _require(mode in {'idle', 'charge', 'discharge'}, 'unknown battery effective mode')
            for unit, mode in states['pumped_units'].items():
                _require(mode in {'off', 'pump', 'generate'}, 'unknown pumped effective mode')
                if mode != previous[unit]:
                    key = (unit, index + 1)
                    _require(key in preparations, 'pumped transition lacks a unique explicit preparation authorization')
                    prep = preparations[key]; boundary = window.intervals[index][0]
                    _require(prep['from_mode'] == previous[unit] and prep['to_mode'] == mode, 'preparation mode transition mismatch')
                    _require(previous[unit] == 'off' or mode == 'off', 'direct pump/generate reversal needs unsupported transition physics')
                    _require(age[unit] + 1e-12 >= self.minimum_mode_h[unit][previous[unit]], 'pumped minimum mode residence incomplete')
                    _require(entered[unit] <= _time(prep['preparation_started_at'], 'prep began') <= _time(prep['ready_at'], 'prep ready') <= boundary,
                             'preparation must complete within the specific preceding mode occurrence')
                    used.add(key); entered[unit] = boundary; age[unit] = 0.
                age[unit] += DT_H
                previous[unit] = mode
        _require(used == set(preparations), 'unused or extra preparation authorizations require explicit reconciliation')
        return self


@dataclass(frozen=True)
class TerminalBoundary:
    at: datetime
    inventories: dict
    source: str
    published_at: datetime
    available_at: datetime
    boundary_semantics: str


@dataclass(frozen=True)
class PreparedWindow:
    window: RollingWindow
    projection: BoundaryProjection
    physics: Physics
    inputs: tuple[SourceArtifact, ...]
    mode: str
    effective_plan: EffectivePlan
    terminal: TerminalBoundary
    source: str


def validate_prepared(prepared, request):
    _require(isinstance(prepared, PreparedWindow), 'provider must return PreparedWindow')
    _require(prepared.window == request.window, 'provider substituted requested window')
    window = prepared.window.validate(); _text(prepared.source, 'prepared source')
    _require(prepared.mode in {'research', 'causal'}, 'explicit input mode required')
    observations = {s.state_id: s for s in request.available_observations}
    commands = {p.command_id: p for p in request.available_commands}
    p = prepared.projection
    _require(isinstance(p, BoundaryProjection) and p.observation_id in observations, 'projection observation not in causal request history')
    observation = observations[p.observation_id]
    _require(fingerprint(observation) == p.observation_sha256, 'same-id observation content substitution')
    _require(type(p.command_ids) is tuple and len(set(p.command_ids)) == len(p.command_ids), 'duplicate projection command')
    _require(len(p.command_ids) == len(p.command_sha256), 'projection command fingerprints incomplete')
    selected = []
    for key, digest in zip(p.command_ids, p.command_sha256):
        _require(key in commands and fingerprint(commands[key]) == digest, 'projection command missing or same-id content substitution')
        selected.append(commands[key])
    computed = project_boundary(observation=observation, commands=tuple(selected), window=window, physics=prepared.physics, source=p.source)
    _require(p.projected_state == computed.projected_state, 'provider projected initial differs from issued-plan arithmetic')
    _require(type(prepared.inputs) is tuple and bool(prepared.inputs), 'explicit source input tuple required')
    _require(len({s.source_id for s in prepared.inputs}) == len(prepared.inputs), 'duplicate input source id')
    for source in prepared.inputs:
        source.validate(window, prepared.mode)
    prepared.effective_plan.validate(window, computed.projected_state.values)
    terminal = prepared.terminal
    _require(isinstance(terminal, TerminalBoundary), 'explicit terminal boundary required')
    _require(_time(terminal.at, 'terminal at') == window.delivery_end, 'terminal source at wrong boundary')
    _text(terminal.source, 'terminal source'); _text(terminal.boundary_semantics, 'terminal semantics')
    _require(_time(terminal.published_at, 'terminal publication') <= _time(terminal.available_at, 'terminal available') <= window.information_cutoff, 'terminal target unavailable at cutoff')
    expected_inventory = inventory(computed.projected_state.values)
    _keys(terminal.inventories, expected_inventory, 'terminal inventories')
    for family, rows in expected_inventory.items():
        _keys(terminal.inventories[family], rows, 'terminal resources')
        for value in terminal.inventories[family].values():
            _number(value, 'terminal target', 0.)
    return fingerprint(prepared)


@dataclass(frozen=True)
class WindowSolution:
    artifact: bytes
    selected_dispatch_sha256: str
    backend: str


def make_selected_dispatch(prepared, *, selected_dispatch_id, steps, published_at, available_at, source, backend=STUB):
    """Package an explicitly synthetic result or unverified externally read values.

    This function does not solve, prove feasibility, issue commands or execute.
    """
    _require(backend in {STUB, 'unverified_model_values'}, 'native result certification is not implemented by this packager')
    document = dict(schema_version=SCHEMA, run_id=prepared.window.run_id, window_id=prepared.window.window_id,
        selected_dispatch_id=_text(selected_dispatch_id, 'selected id'), prepared_sha256=fingerprint(prepared),
        delivery_start=prepared.window.delivery_start, delivery_end=prepared.window.delivery_end,
        published_at=published_at, available_at=available_at, source=_text(source, 'selected source'), backend=backend,
        research_only=True, initial_inventory=inventory(prepared.projection.projected_state.values), steps=steps)
    raw = canonical_bytes(document)
    return WindowSolution(raw, sha256(raw).hexdigest(), backend)


def _selected_document(solution, window, expected_identity=None):
    _require(isinstance(solution, WindowSolution), 'typed WindowSolution required')
    _digest(solution.selected_dispatch_sha256, 'selected hash')
    _require(type(solution.artifact) is bytes and sha256(solution.artifact).hexdigest() == solution.selected_dispatch_sha256, 'selected dispatch source hash mismatch')
    data = _parse(solution.artifact)
    return _selected_document_fields(data, solution, window, expected_identity)


def _selected_document_fields(data, solution, window, expected_identity=None, *, schema=SCHEMA,
                              backends=(STUB, 'unverified_model_values'), extra_fields=()):
    """Pure shape/time checks only. This function never certifies solver origin."""
    _keys(data, ('schema_version', 'run_id', 'window_id', 'selected_dispatch_id', 'prepared_sha256', 'delivery_start', 'delivery_end',
                 'published_at', 'available_at', 'source', 'backend', 'research_only', 'initial_inventory', 'steps') + extra_fields, 'selected dispatch')
    _require(data['schema_version'] == schema and data['run_id'] == window.run_id and data['window_id'] == window.window_id, 'selected run/window/schema mismatch')
    for key in ('selected_dispatch_id', 'source'):
        _text(data[key], key)
    _digest(data['prepared_sha256'], 'prepared hash')
    _require(data['backend'] == solution.backend and data['backend'] in backends and data['research_only'] is True,
             'backend mismatch or stub/model values presented as native')
    _require(_time(data['delivery_start'], 'selected start') == window.delivery_start and _time(data['delivery_end'], 'selected end') == window.delivery_end, 'selected delivery coverage mismatch')
    _require(_time(data['published_at'], 'selected publication') <= _time(data['available_at'], 'selected availability') <= window.delivery_start,
             'selected dispatch unavailable before delivery')
    _require(type(data['steps']) is list and len(data['steps']) == 24, 'selected needs exactly 24 five-minute steps')
    for i, step in enumerate(data['steps']):
        _keys(step, ('period', 'delivery_start', 'delivery_end', 'powers_mw', 'states', 'end_inventory', 'reservoir_sl'), 'selected step')
        _require(type(step['period']) is int and step['period'] == i + 1, 'periods must be exact ordered 1..24')
        _require((_time(step['delivery_start'], 'step start'), _time(step['delivery_end'], 'step end')) == window.intervals[i], 'five-minute interval mismatch')
        _keys(step['reservoir_sl'], data['initial_inventory']['upper_volume_m3'], 'four SL plant coverage')
        for sl in step['reservoir_sl'].values():
            _keys(sl, SL_FIELDS, 'four distinct SL')
            for value in sl.values():
                _number(value, 'reservoir SL', 0.)
    identity = dict(run_id=data['run_id'], window_id=data['window_id'], selected_dispatch_id=data['selected_dispatch_id'],
                    selected_dispatch_sha256=solution.selected_dispatch_sha256)
    if expected_identity is not None:
        _require(identity == expected_identity, 'selected identity does not match pinned run/window/selection fingerprint')
    return data, identity


def validate_solution(solution, prepared, prepared_sha256):
    data, identity = _selected_document(solution, prepared.window)
    _require(solution.backend == STUB, 'this runner only permits explicit synthetic stubs; native execution not implemented/authorized')
    _validate_selected_plan(data, prepared, prepared_sha256)
    return data, identity


def _validate_selected_plan(data, prepared, prepared_sha256):
    """Shared physical execution arithmetic, without solver certification."""
    _require(data['prepared_sha256'] == prepared_sha256, 'solution not bound to exact prepared inputs')
    _require(_time(data['published_at'], 'solution publication') >= prepared.window.information_cutoff, 'solution published before information cutoff')
    values = deepcopy(prepared.projection.projected_state.values)
    _same_numbers(data['initial_inventory'], inventory(values), 'selected initial inventory')
    for i, step in enumerate(data['steps']):
        _require(step['states'] == prepared.effective_plan.states[i], 'selected dispatch changes externally effective states')
        values = advance_state_values(values, powers_mw=step['powers_mw'], states=step['states'], physics=prepared.physics,
                                      delivery_start=step['delivery_start'], delivery_end=step['delivery_end'])
        _same_numbers(step['end_inventory'], inventory(values), 'selected planned endpoint')
    _same_numbers(inventory(values), prepared.terminal.inventories, 'selected terminal target')


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    command_id: str
    command_sha256: str
    previous_state_sha256: str
    confirmed_at: datetime
    source: str
    actual_powers_mw: dict
    actual_states: dict
    end_state: StateRecord


def validate_receipt(receipt, command, previous, physics):
    _require(isinstance(receipt, ExecutionReceipt), 'execution needs explicit typed confirmation receipt')
    _text(receipt.receipt_id, 'receipt id'); _text(receipt.source, 'receipt source')
    _require(receipt.command_id == command.command_id and receipt.command_sha256 == fingerprint(command), 'receipt command identity/content mismatch')
    _require(receipt.previous_state_sha256 == fingerprint(previous), 'receipt does not continue exact last confirmed state')
    state = receipt.end_state.validate()
    _require(state.kind in {'executed_simulation', 'external_observation'}, 'receipt cannot confirm a plan as observation')
    _require(state.as_of == command.delivery_end and previous.as_of == command.delivery_start, 'receipt boundary is not this executed five-minute interval')
    _require(command.delivery_end <= _time(receipt.confirmed_at, 'confirmed at') <= state.available_at,
             'execution confirmation time inconsistent with boundary/availability')
    expected = advance_state_values(previous.values, powers_mw=receipt.actual_powers_mw, states=receipt.actual_states,
                                    physics=physics, delivery_start=command.delivery_start, delivery_end=command.delivery_end)
    _same_numbers(state.values, expected, 'receipt end state')
    return state


@dataclass(frozen=True)
class ExecutionAssessment:
    command_sha256: str
    actual_start_sha256: str
    allowed: bool
    source: str
    checked_constraints: tuple[str, ...]


def _execution_assessment(assessment, command, previous):
    _require(isinstance(assessment, ExecutionAssessment), 'actual/projected state deviation needs explicit execution safety assessment')
    _require(assessment.command_sha256 == fingerprint(command) and assessment.actual_start_sha256 == fingerprint(previous),
             'execution assessment has wrong command/actual boundary fingerprint')
    _text(assessment.source, 'execution safety assessment source')
    _require(type(assessment.allowed) is bool, 'explicit execution allowed decision required')
    _require(type(assessment.checked_constraints) is tuple and set(assessment.checked_constraints) ==
             {'thermal_ramp_and_state', 'effective_plan_and_preparation', 'storage_physics', 'network_and_reserve'},
             'execution safety check coverage incomplete')
    if not assessment.allowed:
        raise ExecutionRejected('external safety assessment rejected execution from actual boundary')
    return assessment


def run_october_roll(*, windows, initial_observations, initial_commands, input_provider, solve_window, execute_step,
                     execution_mode, issuance_source, execution_guard=None, event_callback=None):
    """Provider-driven windows; three separately confirmed executions per window.

    All callbacks are trusted adapters, not sandboxes. This version deliberately
    enables only synthetic stub execution. No native solver is invoked here.
    """
    _require(execution_mode == STUB, 'only explicitly synthetic stub execution is enabled')
    return _run_october_execution_engine(windows=windows, initial_observations=initial_observations,
        initial_commands=initial_commands, input_provider=input_provider, solve_window=solve_window,
        execute_step=execute_step, execution_mode=execution_mode, issuance_source=issuance_source,
        execution_guard=execution_guard, event_callback=event_callback, solution_validator=validate_solution,
        receipt_kind='executed_simulation')


def _run_october_execution_engine(*, windows, initial_observations, initial_commands, input_provider, solve_window,
        execute_step, execution_mode, issuance_source, execution_guard, event_callback, solution_validator, receipt_kind):
    """Internal state machine. Callers own origin gates; this engine does not authenticate a solver."""
    _text(issuance_source, 'issuance source')
    _require(type(windows) is tuple and bool(windows), 'explicit window tuple required')
    _require(all(callable(f) for f in (input_provider, solve_window, execute_step)), 'provider/solver/executor callbacks required')
    _require(event_callback is None or callable(event_callback), 'invalid event callback')
    _require(execution_guard is None or callable(execution_guard), 'invalid execution guard')
    for w in windows:
        w.validate()
    _require(len({w.window_id for w in windows}) == len(windows), 'duplicate window id')
    for a, b in zip(windows, windows[1:]):
        _require(a.run_id == b.run_id and b.delivery_start == a.delivery_start + QUARTER and
                 b.information_cutoff >= a.information_cutoff, 'rolling windows must advance fifteen minutes within same run')
    _require(type(initial_observations) is tuple and type(initial_commands) is tuple, 'explicit immutable initial history tuples required')
    observations, commands = deepcopy(list(initial_observations)), deepcopy(list(initial_commands))
    for state in observations:
        state.validate(); _require(state.kind != 'planned_projection', 'initial history cannot contain projected observations')
    for command in commands:
        command.validate(); _require(command.run_id == windows[0].run_id, 'cross-run initial command')
    _require(len({s.state_id for s in observations}) == len(observations), 'duplicate initial observation id')
    _require(len({s.as_of for s in observations}) == len(observations), 'ambiguous initial observations at same boundary')
    _require(len({p.command_id for p in commands}) == len(commands), 'duplicate initial command id')
    report = dict(completed=False, completed_windows=[], confirmed_executions=[], issued_commands=[], events=[],
        last_confirmed_state=None, boundary_deviations=[], execution_assessments=[],
        network_reserve_independently_verified=False, failed_action_status=None, failure_stage=None, execution_mode=execution_mode,
        native_numerical_acceptance=False, official_timing_certified=False, official_rule_implementation=False,
        source_authenticity_verified=False, pricing_available=False, settlement_available=False)
    receipt_ids = set()
    def emit(event, **kwargs):
        entry = dict(event=event, **deepcopy(kwargs)); report['events'].append(entry)
        if event_callback is not None:
            event_callback(deepcopy(entry))
    for number, window in enumerate(windows):
        phase, in_flight = 'input_provider', False
        try:
            emit('window_started', window_number=number)
            request = RollRequest(number, deepcopy(window),
                tuple(deepcopy(s) for s in observations if s.available_at <= window.information_cutoff),
                tuple(deepcopy(c) for c in commands if c.available_at <= window.information_cutoff))
            prepared = deepcopy(input_provider(deepcopy(request)))
            phase = 'input_validation'; prepared_hash = validate_prepared(prepared, request)
            phase = 'solve_window'; solution = deepcopy(solve_window(deepcopy(prepared)))
            phase = 'solution_validation'; selected, identity = solution_validator(solution, prepared, prepared_hash)
            phase = 'issue_three_steps'
            new_commands = []
            for i, step in enumerate(selected['steps'][:3]):
                command = IssuedCommand(f'{window.window_id}:step:{i+1}', window.run_id, window.window_id,
                    identity['selected_dispatch_id'], identity['selected_dispatch_sha256'], *window.intervals[i],
                    _time(selected['published_at'], 'selected published'), _time(selected['available_at'], 'selected available'),
                    issuance_source, deepcopy(step['powers_mw']), deepcopy(step['states'])).validate()
                _require(command.command_id not in {c.command_id for c in commands}, 'new issued command id collision')
                _require(not any(c.delivery_start < command.delivery_end and command.delivery_start < c.delivery_end for c in commands),
                         'overlapping issued execution history needs explicit reconciliation')
                new_commands.append(command)
            emit('commands_pending', window_number=number, commands=tuple(new_commands))
            commands.extend(deepcopy(new_commands)); report['issued_commands'].extend(deepcopy(new_commands))
            emit('three_steps_issued', window_number=number, selected_identity=identity)
            planned_values = deepcopy(prepared.projection.projected_state.values)
            for index, command in enumerate(new_commands):
                phase = 'execution_boundary'
                candidates = [s for s in observations if s.as_of == command.delivery_start and s.available_at <= command.delivery_start]
                _require(len(candidates) == 1, 'execution needs one available actual/explicit simulated start boundary, never projected lookahead')
                previous = deepcopy(candidates[0])
                phase = 'execution_safety'
                deviation = fingerprint(previous.values) != fingerprint(planned_values)
                if deviation:
                    record = dict(command_id=command.command_id, actual_start_sha256=fingerprint(previous),
                                  actual_values_sha256=fingerprint(previous.values), planned_values_sha256=fingerprint(planned_values))
                    report['boundary_deviations'].append(record)
                    _require(execution_guard is not None, 'actual/projected state deviation requires a new explicit execution safety assessment')
                # Independently check storage hard bounds and transition arithmetic.
                advance_state_values(previous.values, powers_mw=command.powers_mw, states=command.states,
                    physics=prepared.physics, delivery_start=command.delivery_start, delivery_end=command.delivery_end)
                if execution_guard is not None:
                    assessment = _execution_assessment(execution_guard(deepcopy(command), deepcopy(previous), deepcopy(planned_values)), command, previous)
                    report['execution_assessments'].append(dict(assessment=deepcopy(assessment), external_reported_safe_only=True,
                        network_reserve_independently_verified=False, source_authenticity_verified=False))
                planned_values = advance_state_values(planned_values, powers_mw=command.powers_mw, states=command.states,
                    physics=prepared.physics, delivery_start=command.delivery_start, delivery_end=command.delivery_end)
                phase = 'execution_pending'
                emit('execution_pending', window_number=number, step=index + 1, command=command, previous_state=previous)
                phase = 'execute_step'; in_flight = True
                receipt = deepcopy(execute_step(deepcopy(command), deepcopy(previous)))
                phase = 'receipt_validation'
                state = validate_receipt(receipt, command, previous, prepared.physics)
                _require(state.kind == receipt_kind, 'runner cannot relabel its receipt as external telemetry or a different execution kind')
                _require(receipt.receipt_id not in receipt_ids, 'duplicate execution receipt id')
                _require(state.state_id not in {s.state_id for s in observations} and state.as_of not in {s.as_of for s in observations}, 'duplicate execution state identity/time')
                receipt_ids.add(receipt.receipt_id); observations.append(deepcopy(state))
                report['confirmed_executions'].append(deepcopy(receipt)); report['last_confirmed_state'] = deepcopy(state)
                in_flight = False; phase = 'confirmation_callback'
                emit('step_confirmed', window_number=number, step=index + 1, receipt_id=receipt.receipt_id, receipt=receipt, storage_transition_arithmetic_verified=True, source_authenticity_verified=False)
            report['completed_windows'].append(dict(window_number=number, selected_identity=identity, prepared_sha256=prepared_hash,
                executed_periods=(1, 2, 3), unexecuted_lookahead_periods=tuple(range(4, 25))))
            phase = 'completion_callback'; emit('window_completed', window_number=number)
        except Exception as exc:
            status = ('explicitly_rejected_not_executed' if isinstance(exc, ExecutionRejected) else
                      'uncertain_after_execution_call_do_not_retry' if in_flight else
                      'confirmed_execution_preserved_do_not_repeat' if phase in {'confirmation_callback', 'completion_callback'} else 'not_executed_for_failed_step')
            report.update(failure_stage=phase, failed_window=number, failed_action_status=status, error=str(exc))
            report['events'].append(dict(event='window_failed', window_number=number, phase=phase, error=str(exc)))
            raise OctoberRollError(f'October roll stopped at window {number} ({phase}): {exc}', report, exc) from exc
    report['completed'] = True
    return deepcopy(report)


@dataclass(frozen=True)
class MappingSelection:
    identity: dict
    power_method: str
    inventory_method: str
    state_method: str
    reservoir_sl_method: str
    source: str
    published_at: datetime
    available_at: datetime
    approved_at: datetime
    approval_source: str


def map_dispatch_to_pricing_report(*, solution, window, expected_identity, selection, lmp_cutoff, asof):
    """A research mapping report, not an official price input or feasibility proof.

    Merely approving 15-to-5 input interpolation is NOT a mapping selection.
    Mixed states or changing any of four SLs stop the whole mapping; none is
    averaged, zero-filled or silently taken from a convenient endpoint.
    """
    return _map_dispatch_to_pricing_report(solution=solution, window=window, expected_identity=expected_identity,
        selection=selection, lmp_cutoff=lmp_cutoff, asof=asof, selected_reader=_selected_document)


def _map_dispatch_to_pricing_report(*, solution, window, expected_identity, selection, lmp_cutoff, asof, selected_reader):
    """Pure mapping shared after the caller's independent source gate."""
    report = dict(status='unavailable', missing=[], rows=[], research_mapping=True, arithmetic_verified=False,
        source_authenticity_verified=False, official_price=False, pricing_feasibility_verified=False,
        settlement_available=False, official_rule_implementation=False,
        official_pricing_blockers=['explicit R3 pricing adapter validation still required', 'official delta and other unresolved pricing semantics are not supplied here'])
    try:
        window.validate(); cutoff, review = _time(lmp_cutoff, 'LMP cutoff'), _time(asof, 'asof')
        _require(window.information_cutoff <= cutoff <= review, 'SCED/LMP/asof timing order invalid')
        data, identity = selected_reader(solution, window, expected_identity)
        _require(_time(data['available_at'], 'selected available') <= cutoff, 'selected source unavailable at LMP cutoff')
        _require(isinstance(selection, MappingSelection), 'explicit power/inventory/state/four-SL mapping selection required; interpolation approval is insufficient')
        _require(selection.identity == identity, 'mapping selection is not bound to same selected dispatch fingerprint')
        _text(selection.source, 'mapping source'); _text(selection.approval_source, 'mapping approval source')
        p, a, approved = (_time(getattr(selection, k), k) for k in ('published_at', 'available_at', 'approved_at'))
        _require(_time(data['available_at'], 'selected availability') <= p <= a <= cutoff and p <= approved <= cutoff, 'mapping selection unavailable/unapproved at cutoff or predates selected source')
        _require((selection.power_method, selection.inventory_method, selection.state_method, selection.reservoir_sl_method) ==
                 ('energy_equivalent_mean', 'corresponding_endpoints', 'require_constant', 'require_constant'), 'unsupported/ambiguous mapping method; no implicit default')
        _keys(data['initial_inventory'], ('battery_energy_mwh', 'upper_volume_m3', 'lower_volume_m3'), 'initial inventories')
        initial = data['initial_inventory']; output = []
        for family, rows in initial.items():
            _require(type(rows) is dict, 'initial inventory resources must be explicit objects')
            for unit, value in rows.items():
                _text(unit, 'initial inventory unit'); _number(value, 'initial inventory', 0.)
        _require(set(initial['upper_volume_m3']) == set(initial['lower_volume_m3']), 'upper/lower plant coverage mismatch')
        reference = data['steps'][0]
        _keys(reference['states'], STATE_FAMILIES, 'mapping initial state families')
        _require(all(type(reference['states'][f]) is dict for f in STATE_FAMILIES), 'mapping state resources must be explicit objects')
        # Only inventory/resource shapes are needed for shared sign/mode checking.
        shape = dict(thermal=reference['states']['thermal'], batteries=initial['battery_energy_mwh'],
                     pumped_units=reference['states']['pumped_units'])
        for step in data['steps']:
            _validate_power_states(step['powers_mw'], step['states'], shape)
            _keys(step['end_inventory'], initial, 'mapping inventory families')
            for family in initial:
                _keys(step['end_inventory'][family], initial[family], 'mapping inventory resources')
                for val in step['end_inventory'][family].values():
                    _number(val, 'inventory', 0.)
        for group in range(8):
            steps = data['steps'][group * 3: group * 3 + 3]
            _require(all(step['states'] == steps[0]['states'] for step in steps), f'LMP interval {group+1}: mixed five-minute states cannot be uniquely aggregated')
            _require(all(step['reservoir_sl'] == steps[0]['reservoir_sl'] for step in steps), f'LMP interval {group+1}: changing four-SL values need an externally justified mapping, not averaging')
            powers = {family: {u: sum(step['powers_mw'][family][u] * DT_H for step in steps) / .25
                              for u in steps[0]['powers_mw'][family]} for family in POWER_FAMILIES}
            energy = {family: {u: powers[family][u] * .25 for u in powers[family]} for family in POWER_FAMILIES}
            output.append(dict(period=group + 1, delivery_start=steps[0]['delivery_start'], delivery_end=steps[-1]['delivery_end'],
                selected_sced_periods=[s['period'] for s in steps], power_mw=powers, energy_mwh=energy,
                start_inventory=deepcopy(initial if group == 0 else data['steps'][group * 3 - 1]['end_inventory']),
                end_inventory=deepcopy(steps[-1]['end_inventory']), states=deepcopy(steps[0]['states']),
                fixed_reservoir_sl=deepcopy(steps[0]['reservoir_sl'])))
        report.update(status='mapped_research_only', rows=output, arithmetic_verified=True, identity=identity,
                      selection_sha256=fingerprint(selection), selected_backend=solution.backend)
    except (ValueError, TypeError, KeyError) as exc:
        report['missing'].append(str(exc))
    return report


def apply_model_initials(core_inputs, storage_spec, projected_state, *, operating_day):
    """Return explicit patches for provider assembly, never alter a model in place.

    The storage patch uses this module's canonical state field names. Providers
    translate it to the versioned storage schema, preserving source and kind;
    an actual-off state does not set future allowed/effective operating modes.
    """
    projected_state.validate()
    _text(operating_day, 'operating day')
    core = deepcopy(core_inputs)
    _keys(projected_state.values['thermal'], core['thermal'], 'core initial inventory')
    for unit, initial in projected_state.values['thermal'].items():
        core['thermal'][unit].update(initial_online=initial['online'], initial_power_mw=initial['power_mw'],
                                    initial_state_age_h=initial['state_age_h'])
        if core.get('thermal_trajectories') is not None:
            from .gd_october_thermal import initial_trajectory_kind
            core['thermal'][unit]['initial_state_kind']=initial_trajectory_kind(core['thermal_trajectories']['book']) if 'trajectory' in initial else 'stable'
    storage = deepcopy(storage_spec)
    patch = {family: deepcopy(projected_state.values[family]) for family in ('batteries', 'pumped_plants', 'pumped_units')}
    for unit, row in patch['batteries'].items():
        _require(operating_day in row['throughput_mwh_by_day'], 'explicit day bucket required for model day-cycle initial')
        row['day_throughput_mwh'] = row['throughput_mwh_by_day'][operating_day]
    return dict(core_inputs=core, storage_spec=storage, storage_initial_patch=patch,
                state_source=dict(state_id=projected_state.state_id, state_sha256=fingerprint(projected_state),
                                  as_of=projected_state.as_of, kind=projected_state.kind, source=projected_state.source))


def extract_model_step(model, period, *, window, field_accessors, read_value):
    """Extract a selected model point using explicit versioned component accessors.

    field_accessors maps powers_mw/states/end_inventory/reservoir_sl to callables
    (model, period)->nested plain dictionaries. read_value converts model values.
    No solver import, no inference of native optimality, no execution receipt.
    """
    window.validate()
    _require(type(period) is int and 1 <= period <= 24, 'model extraction period must be 1..24')
    horizon = getattr(model, '_gd_october_horizon', getattr(model, '_energy_horizon', None))
    _require(horizon is not None and horizon.mode == 'real_time_5min' and tuple(horizon.ids) == tuple(range(1, 25)),
             'model extraction requires the new 24x5min physical horizon, not old RT15')
    _keys(field_accessors, ('powers_mw', 'states', 'end_inventory', 'reservoir_sl'), 'explicit model field accessors')
    _require(callable(read_value) and all(callable(f) for f in field_accessors.values()), 'model value/accessor callbacks required')
    def read_tree(tree, states=False):
        if type(tree) is dict:
            return {k: read_tree(v, states) for k, v in tree.items()}
        if states and type(tree) is str:
            return tree
        value = read_value(tree)
        if states:
            _require(type(value) is int and value in (0, 1), 'state accessor must explicitly return integer binary, no rounding')
            return value
        return _number(value, 'model value')
    start, end = window.intervals[period - 1]
    return dict(period=period, delivery_start=start.isoformat(), delivery_end=end.isoformat(),
                **{k: read_tree(f(model, period), k == 'states') for k, f in field_accessors.items()})


def apply_october_storage_initials(storage_spec, state, *, window):
    """Copy a verified boundary into the new storage schema without unlocking plans.

    Caller must validate the projection against request history first. This is a
    schema/unit adapter, not independent authentication of the supplied state.
    Already-declared terminal targets, effective plans and preparation records
    are preserved; a preceding-mode mismatch stops rather than revises a plan.
    """
    state.validate(); window.validate()
    _require(state.as_of == window.delivery_start and state.available_at <= window.information_cutoff,
             'storage model initial must describe this boundary and be available at cutoff')
    result = deepcopy(storage_spec)
    _require(result.get('schema_version') == 'gd_october_storage_v1' and result.get('run_id') == window.run_id,
             'storage adapter schema/run mismatch')
    _require(_time(result['window_start'], 'storage start') == window.delivery_start and
             _time(result['information_cutoff'], 'storage cutoff') == window.information_cutoff, 'storage adapter clock mismatch')
    _keys(result['batteries'], state.values['batteries'], 'storage batteries')
    _keys(result['pumped'], state.values['pumped_plants'], 'storage pumped plants')
    units = {u for plant in result['pumped'].values() for u in plant['units']}
    _require(units == set(state.values['pumped_units']), 'storage pumped unit inventory mismatch')
    kind = {'planned_projection':'causal_plan_projection', 'executed_simulation':'confirmed_simulated_execution',
            'external_observation':'telemetry', 'declared_initial':'confirmed_simulated_execution'}[state.kind]
    # A declared initial is still explicitly research; the model's execution-kind
    # slot cannot establish a real receipt. Keep the actual StateRecord alongside.
    _require(state.kind != 'declared_initial', 'declared initial needs an explicit observed/simulated/projection kind before physical model adaptation')
    def mode_initial(values, effective):
        _require(effective['preceding_mode'] == values['mode'], 'new actual/projected mode requires a newly reconciled effective plan, not automatic unlock')
        return dict(as_of=state.as_of.isoformat(), available_at=state.available_at.isoformat(), kind=kind, source=state.source,
                    mode=values['mode'], net_power_mw=values['net_power_mw'], mode_elapsed_h=values['mode_elapsed_h'])
    days = tuple(dict.fromkeys(a.astimezone(SHANGHAI).date().isoformat() for a, _ in window.intervals))
    for unit, row in result['batteries'].items():
        carried = state.values['batteries'][unit]
        _keys(row['cycle_budgets'], days, 'explicit window cycle budgets')
        _require(days[0] in carried['throughput_mwh_by_day'], 'initial operating-day throughput needs an explicit carried bucket')
        row['initial_energy_mwh'] = carried['energy_mwh']
        row['cumulative_throughput_used_mwh'] = carried['cumulative_throughput_mwh']
        row['cycle_budget_id'] = days[0]
        for day in days:
            value = carried['throughput_mwh_by_day'].get(day, 0.)
            _require(day == days[0] or value == 0., 'future cycle budget cannot contain already executed throughput')
            row['cycle_budgets'][day]['used_throughput_mwh'] = value
            row['cycle_budgets'][day]['source'] = state.source + '; future-day zero is declared interval-start day accounting'
        row['cycle_throughput_used_mwh'] = row['cycle_budgets'][days[0]]['used_throughput_mwh']
        row['cycle_limit'] = row['cycle_budgets'][days[0]]['limit_cycles']
        row['actual_initial'] = mode_initial(carried, row['effective_plan'])
        conversion = row['soc_conversion']
        _require(conversion['input_unit'] in {'MWh','percent'}, 'unsupported existing SOC conversion')
        conversion['initial'] = carried['energy_mwh'] if conversion['input_unit'] == 'MWh' else carried['energy_mwh'] / row['energy_capacity_mwh'] * 100.
        conversion['source'] = state.source + '; explicit conversion of carried initial inventory; terminal value unchanged'
        for key in ('initial_energy_mwh','cumulative_throughput_used_mwh','cycle_budget_id','cycle_budgets','cycle_throughput_used_mwh','cycle_limit','actual_initial','soc_conversion'):
            row['sources'][key] = state.source
    for plant_id, row in result['pumped'].items():
        carried = state.values['pumped_plants'][plant_id]
        row['upper_initial_m3'], row['lower_initial_m3'] = carried['upper_m3'], carried['lower_m3']
        row['state_source'] = dict(as_of=state.as_of.isoformat(), available_at=state.available_at.isoformat(), kind=kind, source=state.source)
        for key in ('upper_initial_m3', 'lower_initial_m3', 'state_source'):
            row['sources'][key] = state.source
        for unit, unit_row in row['units'].items():
            unit_row['actual_initial'] = mode_initial(state.values['pumped_units'][unit], unit_row['effective_plan'])
            unit_row['sources']['actual_initial'] = state.source
    return result


def extract_october_model_steps(model, *, window, read_value):
    """Read all new-model steps, with immutable-input and all-row assignment audit.

    Values remain unconfirmed model candidates. Even an analytic feasible witness
    is not an optimizer result, execution receipt, official price or cash source.
    """
    window.validate()
    from .gd_october_sced import assert_october_integrity, inspect_october_assignment
    assert_october_integrity(model)
    if hasattr(model, '_gd_october_identity'):
        from .gd_october_input_bridge import assert_october_input_binding
        assert_october_input_binding(model)
        _require(model._gd_october_identity['run_id'] == window.run_id and model._gd_october_identity['window_id'] == window.window_id,
                 'bound model run/window does not match extraction request')
    assignment = inspect_october_assignment(model)
    _require(assignment['assignment_satisfies_model'], 'cannot extract an infeasible/unset physical assignment: ' + '; '.join(assignment['failures'][:5]))
    storage = getattr(model, 'october_storage', None)
    inputs = getattr(model, '_gd_october_storage_inputs', None)
    if storage is not None:
        audit = model._gd_october_storage_audit
        _require(audit['run_id'] == window.run_id and _time(audit['window_start'], 'storage model start') == window.delivery_start,
                 'storage model run/delivery window mismatch')
    def online(m, t):
        result = {}
        for g in m.G:
            raw = _number(read_value(m.online[g, t]), 'model thermal state')
            _require(raw in (0., 1.), 'no rounding fractional model commitment')
            result[g] = int(raw)
        return result
    accessors = dict(
        powers_mw=lambda m,t: dict(thermal={g:m.power[g,t] for g in m.G},
            battery_charge={} if storage is None else {b:storage.signed_charge[b,t] for b in storage.BAT},
            battery_discharge={} if storage is None else {b:storage.discharge[b,t] for b in storage.BAT},
            pumped_generation={} if storage is None else {u:storage.generation[u,t] for u in storage.U},
            pumped_pumping={} if storage is None else {u:storage.pump[u,t] for u in storage.U}),
        states=lambda m,t: dict(thermal=online(m,t), batteries={} if inputs is None else {b:d['modes'][t] for b,d in inputs['batteries'].items()},
            pumped_units={} if inputs is None else {u:d['modes'][t] for u,d in inputs['units'].items()}),
        end_inventory=lambda m,t: dict(battery_energy_mwh={} if storage is None else {b:storage.energy[b,t] for b in storage.BAT},
            upper_volume_m3={} if storage is None else {p:storage.upper[p,t] for p in storage.PS},
            lower_volume_m3={} if storage is None else {p:storage.lower[p,t] for p in storage.PS}),
        reservoir_sl=lambda m,t: {} if storage is None else {p:{dest:storage.slack[p,src,t] for dest,src in
            zip(SL_FIELDS,('upper_plus','upper_minus','lower_plus','lower_minus'))} for p in storage.PS})
    # Generic extractor demands exact integer states. Preserve known Python int
    # accessors and delegate every model expression to the supplied reader.
    reader = lambda x: x if type(x) is int else read_value(x)
    steps = tuple(extract_model_step(model,t,window=window,field_accessors=accessors,read_value=reader) for t in range(1,25))
    return dict(steps=steps, assignment_audit=assignment, backend='unverified_model_values', native_numerical_acceptance=False,
                execution_confirmed=False, source_authenticity_verified=False)
