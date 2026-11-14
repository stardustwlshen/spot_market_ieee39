"""Solver-free, explicit RT chronology, selected-DA and information contracts.

No legacy offline array becomes an issued forecast. No solved future state
becomes an observation before its actual availability time. See rebuild docs21.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from math import isfinite
from numbers import Real
from pathlib import Path
from zoneinfo import ZoneInfo

from .gd_horizon import EnergyHorizon, EnergyPeriod


class RTInputError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise RTInputError(message)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), name + ': explicit source/text required')
    return value


def _time(value, name):
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             name + ': timezone-aware datetime required')
    return value


def _num(value, name, minimum=None):
    _require(not isinstance(value, bool) and isinstance(value, Real) and isfinite(float(value)), name + ': finite number required')
    _require(minimum is None or value >= minimum, name + ': below minimum')
    return float(value)


def _intervals(intervals):
    _require(isinstance(intervals, dict) and bool(intervals), 'explicit ordered global intervals required')
    previous = None
    for key, pair in intervals.items():
        _require(type(key) is int and key >= 0, 'global period must be nonnegative int')
        _require(isinstance(pair, tuple) and len(pair) == 2, 'immutable interval pair required')
        start, end = (_time(t, 'interval') for t in pair)
        _require(end - start == timedelta(minutes=15), 'RT requires quarter-hour intervals')
        local = start.astimezone(ZoneInfo('Asia/Shanghai'))
        _require(not local.second and not local.microsecond and local.minute % 15 == 0, 'RT starts must align with Shanghai quarter-hours')
        _require(previous is None or start == previous, 'global intervals must be ordered and contiguous')
        previous = end
    return intervals


@dataclass(frozen=True)
class RTWindow:
    global_periods: tuple[int, ...]
    intervals: dict[int, tuple[datetime, datetime]]
    information_cutoff: datetime
    requested_periods: int
    tail_policy: str
    time_mapping_source: str

    def validate(self):
        _require(isinstance(self.global_periods, tuple) and bool(self.global_periods), 'nonempty global period tuple required')
        _require(len(set(self.global_periods)) == len(self.global_periods), 'duplicate global periods')
        _require(type(self.requested_periods) is int and self.requested_periods > 0, 'explicit positive window length required')
        _require(self.tail_policy in {'reject', 'truncate_research'}, 'explicit tail policy required')
        _text(self.time_mapping_source, 'time_mapping_source')
        _intervals(self.intervals)
        _require(tuple(self.intervals) == self.global_periods, 'global period order and interval map disagree')
        _require(len(self.global_periods) <= self.requested_periods, 'window exceeds requested length')
        _require(not self.truncated or self.tail_policy == 'truncate_research', 'missing next-day input cannot be filled or silently truncated')
        _require(_time(self.information_cutoff, 'cutoff') == self.start - timedelta(minutes=15), 'RT cutoff must equal T-15 minutes')
        return self

    @property
    def start(self):
        return self.intervals[self.global_periods[0]][0]

    @property
    def end(self):
        return self.intervals[self.global_periods[-1]][1]

    @property
    def truncated(self):
        return len(self.global_periods) < self.requested_periods

    @property
    def local_to_global(self):
        return dict(enumerate(self.global_periods))

    @property
    def global_to_local(self):
        return {g: l for l, g in self.local_to_global.items()}

    @property
    def local_intervals(self):
        return {l: self.intervals[g] for l, g in self.local_to_global.items()}

    @property
    def horizon(self):
        return EnergyHorizon(tuple(EnergyPeriod(i, .25, .25, 'delivery', i == 0)
            for i in self.local_to_global), 'real_time', self.time_mapping_source).validate()

    def audit(self):
        self.validate()
        return dict(global_periods=self.global_periods, local_to_global=self.local_to_global,
            requested_periods=self.requested_periods, actual_periods=len(self.global_periods),
            missing_tail_periods=self.requested_periods-len(self.global_periods),
            tail_policy=self.tail_policy, truncated=self.truncated,
            information_cutoff=self.information_cutoff.isoformat(), first_execution_start=self.start.isoformat(),
            first_execution_end=self.local_intervals[0][1].isoformat(),
            official_window_length_verified=False, continuous_official_window_claimed=False,
            time_mapping_source=self.time_mapping_source)


def make_rt_window(*, intervals, first_global_period, window_periods, tail_policy, time_mapping_source):
    _intervals(intervals)
    _require(type(first_global_period) is int and first_global_period in intervals, 'unknown first global period')
    _require(type(window_periods) is int and window_periods > 0, 'explicit positive window length required')
    ids = tuple(intervals)
    start = ids.index(first_global_period)
    chosen = ids[start:start+window_periods]
    return RTWindow(chosen, {g: intervals[g] for g in chosen}, intervals[first_global_period][0]-timedelta(minutes=15),
                    window_periods, tail_policy, time_mapping_source).validate()


def slice_period_series(values, window, *, label):
    """Explicit scalar period series. Never infer a period axis from dictionary shape."""
    window.validate()
    _require(isinstance(values, dict), label + ': period dictionary required')
    _require(set(window.global_periods) <= set(values), label + ': missing global period, no padding')
    return {l: deepcopy(values[g]) for l, g in window.local_to_global.items()}


def slice_resource_series(values, window, *, resource_ids, label):
    window.validate()
    _require(isinstance(resource_ids, tuple) and len(set(resource_ids)) == len(resource_ids), label + ': explicit unique resource inventory required')
    _require(isinstance(values, dict), label + ': resource-period dictionary required')
    expected = {(r, g) for r in resource_ids for g in window.global_periods}
    _require(expected <= set(values), label + ': missing resource/global coverage')
    return {(r, l): deepcopy(values[r, g]) for r in resource_ids for l, g in window.local_to_global.items()}


@dataclass(frozen=True)
class ForecastVintage:
    vintage_id: str
    kind: str
    source: str
    issued_at: datetime | None
    available_at: datetime | None
    intervals: dict[int, tuple[datetime, datetime]]
    # Exact declared series x periods, e.g. ('load_mw', '31', 4).
    values: dict[tuple[str, str, int], float]
    series: tuple[tuple[str, str], ...]
    uses_future_actual: bool


def map_forecast(vintage, window, *, required_series, allowed_kind):
    _require(isinstance(vintage, ForecastVintage), 'typed forecast vintage required')
    window.validate()
    for key in ('vintage_id', 'source'):
        _text(getattr(vintage, key), key)
    _require(allowed_kind in {'issued_forecast', 'offline_truth_conditioned_research'}, 'explicit forecast mode required')
    _require(vintage.kind == allowed_kind, 'forecast mode mismatch; offline data cannot silently become issued')
    _require(type(vintage.uses_future_actual) is bool, 'explicit future-actual disclosure required')
    _intervals(vintage.intervals)
    _require(isinstance(vintage.series, tuple) and len(set(vintage.series)) == len(vintage.series), 'unique immutable forecast series required')
    _require(isinstance(required_series, tuple) and bool(required_series) and len(set(required_series)) == len(required_series), 'nonempty exact required forecast series required')
    for family, resource in vintage.series:
        _text(family, 'forecast family'); _text(resource, 'forecast resource')
    _require(set(vintage.series) == set(required_series), 'forecast resource/family inventory mismatch')
    expected = {(f, r, g) for f, r in vintage.series for g in vintage.intervals}
    _require(isinstance(vintage.values, dict) and set(vintage.values) == expected, 'forecast values must exactly cover vintage series and periods')
    for value in vintage.values.values():
        _num(value, 'forecast')
    _require(set(window.global_periods) <= set(vintage.intervals), 'forecast does not cover requested window')
    _require(all(vintage.intervals[g] == window.intervals[g] for g in window.global_periods), 'forecast period timestamps mismatch')
    if vintage.kind == 'issued_forecast':
        issued, available = _time(vintage.issued_at, 'forecast issued_at'), _time(vintage.available_at, 'forecast available_at')
        _require(issued <= available <= window.information_cutoff, 'forecast is not available at cutoff')
        _require(vintage.uses_future_actual is False, 'future actual conditioning forbidden in issued-forecast path')
    else:
        _require(vintage.issued_at is None and vintage.available_at is None, 'offline truth-conditioned curves must not invent issue/availability timestamps')
        _require(vintage.uses_future_actual is True, 'offline truth-conditioned research requires explicit future-actual label')
    values = {(f, r, l): float(vintage.values[f, r, g]) for f, r in required_series for l, g in window.local_to_global.items()}
    return dict(values=values, audit=dict(vintage_id=vintage.vintage_id, kind=vintage.kind, source=vintage.source,
        issued_at=None if vintage.issued_at is None else vintage.issued_at.isoformat(),
        available_at=None if vintage.available_at is None else vintage.available_at.isoformat(),
        uses_future_actual=vintage.uses_future_actual, causal_online_forecast=vintage.kind == 'issued_forecast',
        authenticity_verified=False, coverage_verified=True))


DA_STAGE = 'DA_SCED_fixed_state_continuous_polish'
DA_FIELDS = ('thermal_commitment', 'thermal_power_mw', 'battery_energy_mwh', 'battery_direction',
             'pumped_mode', 'pumped_generation_mw', 'upper_volume_m3', 'lower_volume_m3')


@dataclass(frozen=True)
class SelectedDAReference:
    stage: str
    source: str
    source_sha256: str
    available_at: datetime
    intervals: dict[int, tuple[datetime, datetime]]
    thermal_ids: tuple[str, ...]
    battery_ids: tuple[str, ...]
    pumped_unit_ids: tuple[str, ...]
    pumped_plant_ids: tuple[str, ...]
    # family -> exact (resource_id, global_period) map. End inventories are at end of interval.
    values: dict[str, dict]

    def validate(self):
        _require(self.stage == DA_STAGE, 'all DA references must come from selected_dispatch continuous polish')
        _text(self.source, 'DA source')
        _require(isinstance(self.source_sha256, str) and len(self.source_sha256) == 64 and all(x in '0123456789abcdef' for x in self.source_sha256), 'DA source SHA256 required')
        _time(self.available_at, 'DA available_at'); _intervals(self.intervals)
        _require(isinstance(self.values, dict) and set(self.values) == set(DA_FIELDS), 'exact DA reference families required')
        inventories = [self.thermal_ids, self.battery_ids, self.pumped_unit_ids, self.pumped_plant_ids]
        for ids in inventories:
            _require(isinstance(ids, tuple) and len(set(ids)) == len(ids), 'unique immutable DA inventory required')
            for uid in ids: _text(uid, 'DA resource')
        families = (self.thermal_ids, self.thermal_ids, self.battery_ids, self.battery_ids,
                    self.pumped_unit_ids, self.pumped_unit_ids, self.pumped_plant_ids, self.pumped_plant_ids)
        for name, ids in zip(DA_FIELDS, families):
            vals = self.values[name]
            _require(isinstance(vals, dict) and set(vals) == {(r, g) for r in ids for g in self.intervals}, name + ': exact DA reference coverage required')
            for value in vals.values():
                if name == 'pumped_mode':
                    _require(isinstance(value, str) and value in {'off', 'pump', 'generate'}, 'invalid DA pumped mode')
                elif name in {'thermal_commitment', 'battery_direction'}:
                    _require(type(value) is int and value in ((0, 1) if name == 'thermal_commitment' else (-1, 0, 1)), name + ': exact integer state required')
                else:
                    _num(value, name, 0.)
        return self


def map_da_reference(reference, window):
    _require(isinstance(reference, SelectedDAReference), 'SelectedDAReference required')
    reference.validate(); window.validate()
    _require(reference.available_at <= window.information_cutoff, 'DA selected reference unavailable at cutoff')
    _require(set(window.global_periods) <= set(reference.intervals), 'DA reference does not cover requested window')
    _require(all(reference.intervals[g] == window.intervals[g] for g in window.global_periods), 'DA and RT absolute intervals mismatch')
    values = {name: {(r, window.global_to_local[g]): deepcopy(value) for (r, g), value in cells.items() if g in window.global_to_local}
              for name, cells in reference.values.items()}
    hour_ends, day_ends = [], []
    for l, (_, end) in window.local_intervals.items():
        local = end.astimezone(ZoneInfo('Asia/Shanghai'))
        if not local.minute and not local.second and not local.microsecond:
            hour_ends.append(l)
            if not local.hour: day_ends.append(l)
    _require(len(day_ends) <= 1, 'multiple delivery-day ends require explicit daily budget contracts')
    start_date = window.start.astimezone(ZoneInfo('Asia/Shanghai')).date()
    _require(all(pair[0].astimezone(ZoneInfo('Asia/Shanghai')).date() == start_date for pair in window.local_intervals.values()),
             'cross-day windows require next-day DA/forecast and explicit budget reset adapter')
    return dict(values=values, anchor_periods=tuple(hour_ends), day_end_period=day_ends[0] if day_ends else None,
        battery_da_anchor_mwh={(r, t): values['battery_energy_mwh'][r, t] for r in reference.battery_ids for t in hour_ends},
        audit=dict(stage=reference.stage, source=reference.source, source_sha256=reference.source_sha256,
            references_are_selected_dispatch=True, inventories_at='interval_end', no_original_sced_mix=True))


def read_selected_da_csv(path, *, intervals, available_at, thermal_ids, battery_ids, pumped_unit_ids, pumped_plant_ids, source):
    """Read an existing exported selected dispatch, without importing any optimizer.

    A sibling metrics file binds the stage. File hash is of exact CSV bytes;
    provenance authenticity is not authenticated by this read-only utility.
    """
    path = Path(path)
    _require(path.parent.name == DA_STAGE and path.name == 'variables.csv', 'select the explicit polished dispatch variables.csv, not original SCED')
    metrics = json.loads((path.parent / 'metrics.json').read_text())
    upstream = metrics.get('upstream_dispatch_metrics')
    _require(isinstance(upstream, dict), 'selected DA source must retain upstream original MIP metrics')
    backend = metrics.get('backend')
    _require(backend in {'copt', 'highs'}, 'selected dispatch requires explicit supported backend')
    for label, record, stage in (('selected polish', metrics, DA_STAGE), ('upstream MIP', upstream, 'DA_SCED')):
        _require(record.get('stage') == stage and record.get('backend') == backend, label + ': incorrect selected dispatch/backend identity')
        _require(record.get('termination') in ({'optimal'} if stage == DA_STAGE else {'optimal', 'maxTimeLimit'}),
                 label + ': unaccepted termination')
        _require(_num(record.get('requested_mip_rel_gap'), label + ' requested gap') == .001,
                 label + ': requested gap must equal .001')
        _require(record.get('requested_gap_met') is True and _num(record.get('gap'), label + ' achieved gap', 0.) <= .001 + 1e-9,
                 label + ': original requested .001 gap must actually be met')
    data = path.read_bytes()
    rows = list(csv.DictReader(data.decode('utf-8-sig').splitlines()))
    all_values = {}
    for row in rows:
        _require(row['name'] not in all_values, 'duplicate CSV variable')
        try: value = float(row['value'])
        except (TypeError, ValueError) as exc: raise RTInputError('invalid CSV numeric variable') from exc
        _num(value, row['name'])
        all_values[row['name']] = value
    def read(component, *indices):
        name = component + '[' + ','.join(str(x) for x in indices) + ']'
        _require(name in all_values, 'missing selected DA variable: ' + name)
        return all_values[name]
    def binary(component, *indices):
        v = read(component, *indices)
        _require(min(abs(v), abs(v-1)) <= 1e-6, 'nonbinary selected DA state')
        return int(v > .5)
    values = {name: {} for name in DA_FIELDS}
    for t in intervals:
        for uid in thermal_ids:
            values['thermal_commitment'][uid, t] = binary('online', uid, t)
            values['thermal_power_mw'][uid, t] = read('power', uid, t)
        for uid in battery_ids:
            values['battery_energy_mwh'][uid, t] = read('gd_storage.energy', uid, t)
            charge, discharge = binary('gd_storage.charge_mode', uid, t), binary('gd_storage.discharge_mode', uid, t)
            _require(charge + discharge <= 1, 'conflicting selected DA battery modes')
            values['battery_direction'][uid, t] = discharge - charge
        for uid in pumped_unit_ids:
            modes = [mode for mode in ('off', 'pump', 'generate') if binary('gd_storage.mode', uid, t, mode)]
            _require(len(modes) == 1, 'selected DA pumped modes not unique')
            values['pumped_mode'][uid, t] = modes[0]
            values['pumped_generation_mw'][uid, t] = read('gd_storage.generate', uid, t)
        for pid in pumped_plant_ids:
            values['upper_volume_m3'][pid, t] = read('gd_storage.upper_volume', pid, t)
            values['lower_volume_m3'][pid, t] = read('gd_storage.lower_volume', pid, t)
    return SelectedDAReference(DA_STAGE, source, hashlib.sha256(data).hexdigest(), available_at, deepcopy(intervals),
        thermal_ids, battery_ids, pumped_unit_ids, pumped_plant_ids, values).validate()


@dataclass(frozen=True)
class BoundaryObservation:
    observation_id: str
    observed_at: datetime
    available_at: datetime
    kind: str
    source: str
    state: dict

    def validate(self):
        _text(self.observation_id, 'observation_id'); _text(self.source, 'observation source')
        _require(self.kind in {'external_observation', 'executed_simulation', 'declared_research_initial'}, 'explicit observation kind required')
        _require(_time(self.observed_at, 'observed_at') <= _time(self.available_at, 'available_at'), 'observation cannot be available before it occurs')
        _require(isinstance(self.state, dict) and bool(self.state), 'nonempty explicit observation state required')
        return self


@dataclass(frozen=True)
class IssuedPlan:
    plan_id: str
    issued_at: datetime
    available_at: datetime
    start: datetime
    end: datetime
    source: str
    kind: str
    payload: dict

    def validate(self):
        _text(self.plan_id, 'plan_id'); _text(self.source, 'plan source')
        _require(self.kind in {'external_issued_execution', 'issued_research_execution'}, 'explicit issued execution plan kind required')
        _require(_time(self.issued_at, 'issued_at') <= _time(self.available_at, 'available_at') <= _time(self.start, 'plan start') < _time(self.end, 'plan end'), 'invalid issued execution chronology')
        _require(isinstance(self.payload, dict) and bool(self.payload), 'explicit plan payload required')
        return self


@dataclass(frozen=True)
class ProjectionSegment:
    plan_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BoundaryProjection:
    observation: BoundaryObservation
    projected_at: datetime
    state: dict
    plans: tuple[IssuedPlan, ...]
    segments: tuple[ProjectionSegment, ...]
    source: str


def validate_boundary_projection(projection, window):
    _require(isinstance(projection, BoundaryProjection), 'explicit BoundaryProjection required')
    window.validate(); projection.observation.validate()
    _require(projection.observation.available_at <= window.information_cutoff, 'future actual initial state unavailable at cutoff')
    _require(_time(projection.projected_at, 'projected_at') == window.start, 'projected boundary must be execution start T')
    _text(projection.source, 'boundary projection source')
    _require(isinstance(projection.state, dict) and bool(projection.state), 'explicit projected state required')
    _require(isinstance(projection.plans, tuple) and isinstance(projection.segments, tuple), 'immutable plans and projection segments required')
    plans = {}
    for plan in projection.plans:
        _require(isinstance(plan, IssuedPlan), 'typed issued plans required'); plan.validate()
        _require(plan.plan_id not in plans, 'duplicate projection plan')
        _require(plan.available_at <= window.information_cutoff, 'projection plan unavailable at cutoff')
        plans[plan.plan_id] = plan
    cursor, used = projection.observation.observed_at, set()
    _require(cursor <= projection.projected_at, 'observation occurs after projected boundary')
    for segment in projection.segments:
        _require(isinstance(segment, ProjectionSegment), 'typed projection segment required')
        _require(segment.plan_id in plans, 'unidentified projection plan')
        plan = plans[segment.plan_id]
        _require(_time(segment.start, 'segment start') == cursor < _time(segment.end, 'segment end') <= projection.projected_at, 'projection intervals must exactly and continuously cover observation to T')
        _require(plan.start <= segment.start and segment.end <= plan.end, 'projection exceeds issued plan validity')
        used.add(segment.plan_id); cursor = segment.end
    _require(cursor == projection.projected_at and used == set(plans), 'projection coverage incomplete or unreferenced plans')
    if not projection.segments:
        _require(projection.state == projection.observation.state, 'zero-length projection must preserve observed state')
    return dict(observation_id=projection.observation.observation_id, observation_kind=projection.observation.kind,
        observed_at=projection.observation.observed_at.isoformat(), available_at=projection.observation.available_at.isoformat(),
        projected_at=projection.projected_at.isoformat(), projection_source=projection.source,
        plan_ids=tuple(plans), future_actual_used=False, projection_arithmetic_verified=False,
        projection_is_not_a_future_observation=True)
