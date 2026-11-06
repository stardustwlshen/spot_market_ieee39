"""Immutable, absolute-time contracts for opt-in causal_physical_v3.

No solver or forecasting object needs access to a full future actual trajectory.
All values are interval means. The first version deliberately supports 15 min.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict, is_dataclass
from datetime import datetime, timezone
from collections.abc import Mapping
from types import MappingProxyType
import hashlib
import json
import math
import numpy as np

MODEL = 'causal_physical_v3'
PRESET_VERSION = 'physical_research_20261106_v1'
SLOT_SECONDS = 900
SUBSTEP_SECONDS = 300
PRE_ROLL_DAYS = 35
KINDS = ('wind', 'pv', 'load', 'natural_inflow')


def plain(value):
    if is_dataclass(value):
        return {k: plain(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def digest(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def immutable_array(values, dtype=float):
    a = np.asarray(values, dtype=dtype)
    return np.frombuffer(a.tobytes(), dtype=a.dtype).reshape(a.shape)


def delay_seconds(minutes):
    """Conservative whole-second availability: fractional delays round UP."""
    if isinstance(minutes,(bool,np.bool_)) or not isinstance(minutes,(int,float,np.integer,np.floating)) or not math.isfinite(minutes) or minutes<0:
        raise ValueError('observation delay must be finite and nonnegative')
    return int(math.ceil(float(minutes)*60))


def utc_seconds(value):
    if isinstance(value, bool):
        raise ValueError('time must be an explicit UTC timestamp')
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timestamp requires an explicit timezone')
    seconds = value.timestamp()
    if not math.isfinite(seconds) or int(seconds) != seconds:
        raise ValueError('timestamp must have whole-second resolution')
    return int(seconds)


def utc_iso(value):
    return datetime.fromtimestamp(utc_seconds(value), timezone.utc).isoformat().replace('+00:00', 'Z')


def grid_slot(value):
    seconds = utc_seconds(value)
    if seconds % SLOT_SECONDS:
        raise ValueError('causal_physical_v3 requires a 15-minute absolute grid')
    return seconds // SLOT_SECONDS


@dataclass(frozen=True)
class ResourceSpec:
    resource_id: str
    kind: str
    scale: float
    group_id: str = ''
    public_params: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not self.resource_id or self.kind not in KINDS:
            raise ValueError('resource identity/kind is invalid')
        if isinstance(self.scale, bool) or not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError('capacity/reference scale must be finite and positive')
        object.__setattr__(self, 'public_params', freeze(self.public_params))

    @property
    def variable(self):
        return {'load': 'load_mw', 'natural_inflow': 'local_natural_qin_m3s'}.get(self.kind, 'available_power_mw')

    @property
    def unit(self):
        return 'm3/s' if self.kind == 'natural_inflow' else 'MW'


@dataclass(frozen=True)
class ObservationSeries:
    resource_id: str
    starts: np.ndarray
    values: np.ndarray
    available_at: np.ndarray
    source: str = 'synthetic_completed_interval'

    def __post_init__(self):
        # Validate BEFORE integer conversion: truncating an arrival time can admit
        # an observation that was not yet available at the publication boundary.
        for key in ('starts','available_at'):
            original=getattr(self,key);raw=np.asarray(original)
            if raw.ndim!=1:
                raise ValueError('observation time columns must be one dimensional')
            # Fast NumPy path for long repeated observation prefixes; validate
            # fractional/range constraints before dtype conversion, not by coercion.
            mixed_bool=(not isinstance(original,np.ndarray) and any(isinstance(v,(bool,np.bool_)) for v in original))
            valid=not mixed_bool and raw.dtype.kind in 'iuf'
            if valid and raw.dtype.kind=='u':valid=not (raw>np.iinfo(np.int64).max).any()
            if valid and raw.dtype.kind=='f':
                valid=bool(np.isfinite(raw).all() and (raw==np.trunc(raw)).all()
                    and (raw>=-2**63).all() and (raw<2**63).all())
            if not valid:
                raise ValueError('observation timestamps require finite whole seconds within int64 range')
        if np.asarray(self.values).ndim!=1:
            raise ValueError('observation values must be one dimensional')
        for key, dtype in (('starts', np.int64), ('values', float), ('available_at', np.int64)):
            object.__setattr__(self, key, immutable_array(getattr(self, key), dtype))
        if not (len(self.starts) == len(self.values) == len(self.available_at)):
            raise ValueError('observation columns must have equal lengths')
        if not np.isfinite(self.values).all() or (self.values < 0).any():
            raise ValueError('observations must be finite and nonnegative')
        if (self.starts % SLOT_SECONDS).any() or (np.diff(self.starts) <= 0).any():
            raise ValueError('observation intervals must be unique, sorted and aligned')
        if (self.available_at < self.starts + SLOT_SECONDS).any():
            raise ValueError('interval means are unavailable before interval end')

    @property
    def content_hash(self):
        h = hashlib.sha256(self.resource_id.encode() + self.source.encode())
        for a in (self.starts, self.values, self.available_at):
            h.update(a.tobytes())
        return h.hexdigest()


@dataclass(frozen=True)
class ObservationPrefix:
    issue_time_utc: str
    series: tuple[ObservationSeries, ...]

    def __post_init__(self):
        object.__setattr__(self,'series',tuple(self.series))
        issue = utc_seconds(self.issue_time_utc)
        object.__setattr__(self,'issue_time_utc',utc_iso(issue))
        if len({s.resource_id for s in self.series}) != len(self.series):
            raise ValueError('duplicate observation resource')
        if any((s.available_at > issue).any() for s in self.series):
            raise ValueError('future/unavailable observation in prefix')

    @property
    def content_hash(self):
        return digest({s.resource_id: s.content_hash for s in self.series})


@dataclass(frozen=True)
class PublicEvent:
    event_id: str
    resource_id: str
    released_at: str
    valid_start: str
    valid_end: str
    baseline_value: float  # CF, cloud index, load ratio, or natural m3/s
    source: str = 'user_declared_synthetic_public_guidance'

    def __post_init__(self):
        for key in ('released_at','valid_start','valid_end'):
            object.__setattr__(self,key,utc_iso(getattr(self,key)))
        if not self.event_id or not self.resource_id:
            raise ValueError('public event identity required')
        if utc_seconds(self.valid_end) <= utc_seconds(self.valid_start):
            raise ValueError('public event interval is empty')
        if not math.isfinite(self.baseline_value) or self.baseline_value < 0:
            raise ValueError('public baseline must be finite and nonnegative')


@dataclass(frozen=True)
class PredictionRequest:
    issue_time_utc: str
    target_start_utc: str
    target_end_utc: str
    stage: str = 'DA'
    policy_id: str = 'baseline'
    observation_policy_id: str = 'completed_intervals'
    policy_overrides: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for key in ('issue_time_utc','target_start_utc','target_end_utc'):
            object.__setattr__(self,key,utc_iso(getattr(self,key)))
        overrides = self.policy_overrides.items() if isinstance(self.policy_overrides, Mapping) else self.policy_overrides
        normalized = tuple(sorted((str(k), str(v)) for k,v in overrides))
        if len(dict(normalized)) != len(normalized): raise ValueError('duplicate policy override')
        object.__setattr__(self, 'policy_overrides', normalized)
        i, a, b = map(grid_slot, (self.issue_time_utc, self.target_start_utc, self.target_end_utc))
        if a < i or b <= a or self.stage not in ('DA', 'ID', 'RT'):
            raise ValueError('invalid issue/target interval/stage')
        if self.observation_policy_id != 'completed_intervals':
            raise ValueError('forecast requests cannot grant current/future actual access')


    def policy_for(self, resource):
        overrides = dict(self.policy_overrides)
        return overrides.get(resource.resource_id, overrides.get(resource.kind, self.policy_id))


@dataclass(frozen=True)
class ResourceSeries:
    resource_id: str
    kind: str
    variable: str
    unit: str
    values: tuple[float, ...]

    def __post_init__(self):
        if self.kind not in KINDS or not self.resource_id:
            raise ValueError('invalid resource series identity/kind')
        variable={'load':'load_mw','natural_inflow':'local_natural_qin_m3s'}.get(self.kind,'available_power_mw')
        unit='m3/s' if self.kind=='natural_inflow' else 'MW'
        if self.variable!=variable or self.unit!=unit:
            raise ValueError('resource series variable/unit mismatch')
        if any(isinstance(x,(bool,np.bool_)) for x in self.values):
            raise ValueError('boolean is not a physical input value')
        object.__setattr__(self, 'values', tuple(float(x) for x in self.values))
        if not all(math.isfinite(x) and x >= 0 for x in self.values):
            raise ValueError('input trajectory must be finite and nonnegative')


@dataclass(frozen=True)
class ForecastFrame:
    request: PredictionRequest
    series: tuple[ResourceSeries, ...]
    information_set_id: str
    observation_prefix_hash: str
    public_information_hash: str
    policy_hash: str
    model_version: str = MODEL
    preset_version: str = PRESET_VERSION

    def __post_init__(self):
        object.__setattr__(self,'series',tuple(self.series))
        n = grid_slot(self.request.target_end_utc) - grid_slot(self.request.target_start_utc)
        if not self.series or len({s.resource_id for s in self.series}) != len(self.series):
            raise ValueError('forecast resources must be unique and nonempty')
        if any(len(s.values) != n for s in self.series):
            raise ValueError('forecast interval coverage is incomplete')

    @property
    def content_hash(self):
        # stage is a usage label, not an extra random source; include all other dependencies.
        data = plain(self)
        data['request'].pop('stage')
        return digest(data)

    @property
    def forecast_id(self):
        return self.content_hash

    def select(self, start, end):
        a, b, origin = grid_slot(start), grid_slot(end), grid_slot(self.request.target_start_utc)
        if a < origin or b > grid_slot(self.request.target_end_utc) or b <= a:
            raise ValueError('requested input slice is outside issued coverage')
        series = tuple(ResourceSeries(s.resource_id, s.kind, s.variable, s.unit,
                                      s.values[a-origin:b-origin]) for s in self.series)
        return SelectedInputs(utc_iso(start), utc_iso(end), self.request.issue_time_utc, series,
                              self.forecast_id, self.information_set_id,
                              tuple('issued_forecast' for _ in range(b-a)))

    def to_dict(self):
        result = plain(self)
        result.update(forecast_id=self.forecast_id, content_hash=self.content_hash)
        i = utc_seconds(self.request.issue_time_utc)
        a = utc_seconds(self.request.target_start_utc)
        result['intervals'] = [dict(interval_start_utc=utc_iso(a+k*SLOT_SECONDS),
            interval_end_utc=utc_iso(a+(k+1)*SLOT_SECONDS),
            lead_start_h=(a+k*SLOT_SECONDS-i)/3600,
            lead_end_h=(a+(k+1)*SLOT_SECONDS-i)/3600,
            lead_mid_h=(a+(k+.5)*SLOT_SECONDS-i)/3600) for k in range(len(self.series[0].values))]
        return result


@dataclass(frozen=True)
class SelectedInputs:
    start_utc: str
    end_utc: str
    issue_time_utc: str
    series: tuple[ResourceSeries, ...]
    forecast_id: str
    information_set_id: str
    sources: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self,'series',tuple(self.series))
        object.__setattr__(self,'sources',tuple(self.sources))
        for key in ('start_utc','end_utc','issue_time_utc'):
            object.__setattr__(self,key,utc_iso(getattr(self,key)))
        n = grid_slot(self.end_utc) - grid_slot(self.start_utc)
        if n <= 0 or grid_slot(self.start_utc) < grid_slot(self.issue_time_utc):
            raise ValueError('selected input times are invalid')
        if len(set(s.resource_id for s in self.series)) != len(self.series):
            raise ValueError('duplicate selected resource')
        if len(self.sources) != n or any(len(s.values) != n for s in self.series):
            raise ValueError('selected input coverage is incomplete')
        if any(s not in ('issued_forecast', 'current_interval_perfect_measurement') for s in self.sources):
            raise ValueError('invalid selected input source')
        if 'current_interval_perfect_measurement' in self.sources[1:]:
            raise ValueError('only the first execution interval may receive research measurement')
        if self.sources[0] == 'current_interval_perfect_measurement' and self.start_utc != self.issue_time_utc:
            raise ValueError('current interval observation must start at issue time')

    def values(self, resource_id, start=None, end=None, *, kind=None):
        s = next((s for s in self.series if s.resource_id == resource_id), None)
        if s is None or (kind is not None and s.kind != kind):
            raise ValueError(f'missing or wrong-kind input resource: {resource_id}')
        a = grid_slot(start or self.start_utc) - grid_slot(self.start_utc)
        b = grid_slot(end or self.end_utc) - grid_slot(self.start_utc)
        if a < 0 or b > len(s.values) or b <= a:
            raise ValueError('selected inputs lack requested interval coverage')
        return np.array(s.values[a:b], dtype=float)

    def with_current_measurement(self, values):
        if self.start_utc != self.issue_time_utc or set(values) != {s.resource_id for s in self.series}:
            raise ValueError('current research measurement must cover all resources at this issue')
        series = tuple(ResourceSeries(s.resource_id, s.kind, s.variable, s.unit,
                         (values[s.resource_id],) + s.values[1:]) for s in self.series)
        return SelectedInputs(self.start_utc, self.end_utc, self.issue_time_utc, series,
                              self.forecast_id, self.information_set_id,
                              ('current_interval_perfect_measurement',) + self.sources[1:])

    @property
    def content_hash(self):
        return digest(self)

    def evidence(self):
        return dict(model_version=MODEL, forecast_id=self.forecast_id,
            information_set_id=self.information_set_id, selected_inputs_hash=self.content_hash,
            issue_time_utc=self.issue_time_utc, start_utc=self.start_utc, end_utc=self.end_utc,
            sources=list(self.sources), rows=plain(self.series))
