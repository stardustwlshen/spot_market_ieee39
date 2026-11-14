"""Closed, versioned, finite data-only codec. No result receipts are loadable."""
import base64
import json
import math
from dataclasses import fields
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .gd_october_roll import (RollingWindow, StateRecord, Physics, IssuedCommand,
    BoundaryProjection, RollRequest, SourceArtifact, EffectivePlan, TerminalBoundary, PreparedWindow)
from .gd_october_native_solve import NativeWindowInputs, NativeSolveSettings, ResultPublication
from .gd_october_run_identity import KnownDispatchReference

from .gd_october_day_inputs import WindowDefinition
from .gd_october_native_roll import DeclaredActualStep

from .gd_october_native_snapshot import ResearchPricingExperiment, SnapshotPublication, NativeSCEDSnapshotInputs

SCHEMA = 'gd.october.data.v1'
CLASSES = {c.__name__: c for c in (RollingWindow, StateRecord, Physics, IssuedCommand,
    BoundaryProjection, RollRequest, SourceArtifact, EffectivePlan, TerminalBoundary,
    PreparedWindow, NativeWindowInputs, NativeSolveSettings, ResultPublication, KnownDispatchReference,
    WindowDefinition, DeclaredActualStep, ResearchPricingExperiment, SnapshotPublication, NativeSCEDSnapshotInputs)}


def _encode(v, depth=0):
    if depth > 100: raise ValueError("data nesting exceeds 100")
    t = type(v)
    if v is None or t in (bool, int, str):
        return v
    if t is float:
        if not math.isfinite(v): raise ValueError('nonfinite number')
        return v
    if t is bytes: return {'tag': 'bytes', 'value': base64.b64encode(v).decode('ascii')}
    if t is datetime:
        if v.tzinfo is None or v.utcoffset() is None: raise ValueError('aware datetime required')
        if type(v.tzinfo) not in (timezone, ZoneInfo): raise ValueError('unsupported timezone type')
        return {'tag': 'datetime', 'value': v.isoformat(), 'fold': v.fold,
                'zone': v.tzinfo.key if type(v.tzinfo) is ZoneInfo else None}
    if t in (list, tuple): return {'tag': t.__name__, 'value': [_encode(x, depth+1) for x in v]}
    if t is dict: return {'tag': 'dict', 'value': [[_encode(k, depth+1), _encode(x, depth+1)] for k,x in v.items()]}
    if t in CLASSES.values():
        return {'tag': 'class', 'name': t.__name__, 'value': {f.name:_encode(getattr(v,f.name), depth+1) for f in fields(v)}}
    raise ValueError('unsupported data class/type: ' + t.__name__)


def _exact(v, names):
    if type(v) is not dict or set(v) != set(names): raise ValueError('unexpected fields')


def _decode(v, depth=0):
    if depth > 100: raise ValueError("data nesting exceeds 100")
    if v is None or type(v) in (bool, int, str): return v
    if type(v) is float:
        if not math.isfinite(v): raise ValueError('nonfinite number')
        return v
    if type(v) is not dict: raise ValueError('tagged data required')
    tag = v.get('tag')
    if tag == 'class':
        _exact(v, ('tag','name','value'))
        if type(v['name']) is not str or v['name'] not in CLASSES: raise ValueError('unknown class')
        cls = CLASSES[v['name']]
        # R7 Physics had no optional thermal-trajectory book. Preserve decoding
        # old stable packages without accepting arbitrary missing fields.
        names=[f.name for f in fields(cls)]
        if cls is Physics and 'thermal_trajectories' not in v['value']:
            names.remove('thermal_trajectories')
        _exact(v['value'], names)
        return cls(**{k:_decode(x, depth+1) for k,x in v['value'].items()})
    if tag == 'datetime':
        _exact(v, ('tag','value','fold','zone'))
        if type(v['fold']) is not int or v['fold'] not in (0,1): raise ValueError('invalid fold')
        d = datetime.fromisoformat(v['value']).replace(fold=v['fold'])
        if d.tzinfo is None or d.utcoffset() is None: raise ValueError('aware datetime required')
        if v['zone'] is not None:
            if type(v['zone']) is not str or v['zone'].startswith('/') or '..' in v['zone'].split('/'):
                raise ValueError('invalid zone key')
            zoned = d.replace(tzinfo=ZoneInfo(v['zone']))
            if zoned.utcoffset() != d.utcoffset(): raise ValueError('timezone offset mismatch')
            d = zoned
        return d
    _exact(v, ('tag','value'))
    if tag == 'bytes': return base64.b64decode(v['value'], validate=True)
    if tag not in ('list','tuple','dict'): raise ValueError('unknown tag')
    if type(v['value']) is not list: raise ValueError('array required')
    if tag != 'dict':
        values = [_decode(x, depth+1) for x in v['value']]
        return tuple(values) if tag == 'tuple' else values
    result = {}
    for pair in v['value']:
        if type(pair) is not list or len(pair) != 2: raise ValueError('key/value pair required')
        key, value = (_decode(x, depth+1) for x in pair)
        try:
            if key in result: raise ValueError('duplicate or colliding dictionary key')
            result[key] = value
        except TypeError as e: raise ValueError('unhashable dictionary key') from e
    return result


def _pairs(pairs):
    result = {}
    for k,v in pairs:
        if k in result: raise ValueError('duplicate JSON key')
        result[k] = v
    return result


def dumps(value):
    return json.dumps({'schema':SCHEMA, 'value':_encode(value)}, ensure_ascii=False,
                      allow_nan=False, separators=(',',':')).encode('utf-8')


def loads(raw):
    if type(raw) is not bytes: raise ValueError('bytes required')
    if len(raw) > 64 * 1024 * 1024: raise ValueError('job exceeds 64 MiB')
    obj = json.loads(raw, object_pairs_hook=_pairs,
                     parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite number')))
    _exact(obj, ('schema','value'))
    if obj['schema'] != SCHEMA: raise ValueError('unknown codec version')
    try:
        return _decode(obj['value'])
    except (TypeError, KeyError, RecursionError) as exc:
        raise ValueError('invalid codec data: ' + str(exc)) from exc
