"""Research-only forecast scenarios. Standalone, not an installed market feature.

DA baseline is declared before the day. Actual paths are synthetic correlated
innovations. RT vintages consume only observations strictly before issue_tick.
All coefficients are assumptions, not fitted errors or provincial rules.
"""
import hashlib
import json
import math
import random
from copy import deepcopy

PARAMETERS = {
    'wind': {'rho': .94, 'sigma': .035, 'correction_decay': .90},
    'solar': {'rho': .82, 'sigma': .055, 'correction_decay': .72},
    'load': {'rho': .97, 'sigma': .006, 'correction_decay': .94},
}
LEVELS = {'none': 0., 'low': .5, 'normal': 1., 'high': 2.}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def validate_resource(resource):
    required = {'kind', 'bus', 'capacity_mw', 'day_ahead_mw', 'source'}
    if not required <= resource.keys():
        raise ValueError('resource requires explicit kind/bus/capacity/day_ahead/source')
    if resource['kind'] not in PARAMETERS or not resource['source']:
        raise ValueError('unknown resource kind or missing source')
    x = resource['day_ahead_mw']; cap = resource['capacity_mw']
    if len(x) != 96 or not math.isfinite(cap) or cap <= 0:
        raise ValueError('requires 96 quarter hours and positive declared capacity')
    if any(not math.isfinite(v) or v < 0 or v > cap for v in x):
        raise ValueError('baseline outside declared bounds')
    if resource['kind'] == 'solar':
        # Night is declared, never inferred from realised output/clouds.
        mask = resource.get('daylight')
        if not isinstance(mask, list) or len(mask) != 96 or any(type(v) is not bool for v in mask):
            raise ValueError('solar requires explicit 96-cell daylight mask')
        if any(v != 0 for v, sun in zip(x, mask) if not sun):
            raise ValueError('solar baseline nonzero at declared night')

def generate(resources, levels, seed=0):
    """Return reproducible synthetic actuals around a supplied pre-day baseline.

    Solar errors scale with daylight envelope, wind with installed capacity,
    load with pre-day demand. AR(1) innovations avoid independent step noise.
    Resource-local RNG makes adding another unit leave existing paths unchanged.
    """
    result = {'schema': 'research.forecast.v1', 'assumption': True,
              'notice': '合成研究预测误差；非实测、非官方误差参数。尚未接入出清入口。',
              'parameters': deepcopy(PARAMETERS), 'levels': dict(levels), 'seed': seed,
              'resources': {}}
    for name, source in resources.items():
        validate_resource(source)
        kind = source['kind']; level = levels.get(kind, 'none')
        if level not in LEVELS: raise ValueError('unknown error level')
        scale = LEVELS[level]; p = PARAMETERS[kind]; cap = source['capacity_mw']
        rng = random.Random(int(digest([seed, name]), 16)); err = 0.; actual = []
        for t, base in enumerate(source['day_ahead_mw']):
            err = p['rho'] * err + p['sigma'] * rng.gauss(0., 1.) * scale
            amplitude = base if kind in ('solar', 'load') else cap
            value = max(0., min(cap, base + amplitude * err))
            if kind == 'solar' and not source['daylight'][t]: value = 0.
            actual.append(value)
        result['resources'][name] = {**deepcopy(source), 'actual_mw': actual,
                                      'source_digest': digest(source)}
    result['bundle_digest'] = digest(result)
    return result

def vintage(resource, issue_tick, observed_prefix, horizon=8):
    """Forecast [issue_tick, issue_tick+horizon), strictly no future actuals.

    Caller passes only measurements available BEFORE issue_tick. The returned
    first interval is a forecast, not a claim of perfectly measured execution.
    """
    validate_resource(resource)
    if not 0 <= issue_tick < 96 or len(observed_prefix) != issue_tick:
        raise ValueError('observation prefix must end strictly before issue tick')
    if any(not math.isfinite(v) or v < 0 or v > resource['capacity_mw'] for v in observed_prefix):
        raise ValueError('invalid observed prefix')
    kind = resource['kind']; base = resource['day_ahead_mw']; cap = resource['capacity_mw']
    decay = PARAMETERS[kind]['correction_decay']; residual = 0.
    if issue_tick:
        previous = issue_tick - 1
        residual = observed_prefix[-1] - base[previous]
        # Do not carry yesterday/night PV residual through sunrise.
        if kind == 'solar':
            residual = residual/base[previous] if base[previous] > 0 else 0.
    values = []
    for lead, t in enumerate(range(issue_tick, min(96, issue_tick+horizon)), 1):
        correction = residual * decay**lead
        if kind == 'solar': correction *= base[t]
        value = max(0., min(cap, base[t]+correction))
        if kind == 'solar' and not resource['daylight'][t]: value = 0.
        values.append(value)
    return {'issued_tick': issue_tick, 'observed_end_exclusive': issue_tick,
            'forecast_mw': values, 'uses_future_actual': False,
            'input_digest': digest([resource, issue_tick, observed_prefix, horizon])}

def verify_bus_mapping(resources, bus_baseline, tolerance=1e-6):
    """Fail closed if declared components do not reconstruct the model seed."""
    aggregated = {}
    for resource in resources.values():
        validate_resource(resource)
        if resource['kind'] == 'load': continue
        bus = str(resource['bus']); row = aggregated.setdefault(bus, [0.]*96)
        for t, v in enumerate(resource['day_ahead_mw']): row[t] += v
    all_buses = set(aggregated) | set(map(str, bus_baseline))
    for bus in all_buses:
        a = aggregated.get(bus, [0.]*96); b = bus_baseline.get(bus, [0.]*96)
        if len(b) != 96 or any(abs(x-y)>tolerance for x,y in zip(a,b)):
            raise ValueError('wind/solar components do not match seed bus '+bus)
    return True
