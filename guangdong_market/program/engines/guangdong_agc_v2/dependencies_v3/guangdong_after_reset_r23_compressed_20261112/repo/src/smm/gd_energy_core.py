"""Explicit thermal/DC energy-physics subset for Guangdong-reference research.

Builds SCUC or fixed-commitment SCED, never invokes a solver. This is NOT the
complete provincial market. See rebuild docs/09_省内能量物理核心.md for limitations.
All prices/penalties and the deliberately restricted transition model are input.
"""
from __future__ import annotations

from copy import deepcopy
import math
from numbers import Real

import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn

from .gd_horizon import EnergyHorizon


class EnergyInputError(ValueError):
    """An incomplete, inconsistent or unsupported physical-model contract."""


# Import acceptance only, with no relative term. Never add these tolerances to
# model bounds or adjust source state. See rebuild docs/24_数值状态边界处理.md.
STATE_BOUNDARY_ABS_TOLERANCES = {'MW': 1e-7, 'MWh': 1e-7, 'm3': 1e-7}


def _state_boundary_audit():
    return {'policy': 'absolute_initial_state_acceptance_preserve_raw_v1',
            'absolute_tolerance_by_unit': dict(STATE_BOUNDARY_ABS_TOLERANCES),
            'relative_tolerance': 0., 'state_values_adjusted': False,
            'physical_constraints_relaxed': False,
            'acceptance_is_feasibility_certificate': False, 'checks': []}


def _state_in_bounds(value, label, *, lower, upper, unit, source, audit):
    """Accept a tiny state-bound residual without normalizing the model value."""
    raw = _num(value, label)
    lower = _num(lower, label + '.lower')
    upper = _num(upper, label + '.upper')
    if lower > upper:
        raise EnergyInputError(f'{label}: lower state bound exceeds upper bound')
    tolerance = STATE_BOUNDARY_ABS_TOLERANCES[unit]
    residual = raw - lower if raw < lower else raw - upper if raw > upper else 0.
    # Compare the residual, rather than adding epsilon to a potentially large
    # bound: the accepted error never scales with unit rating or reservoir size.
    if abs(residual) > tolerance:
        raise EnergyInputError(f'{label}: initial state outside hard bounds '
                               f'[{lower}, {upper}] {unit}; residual {residual} '
                               f'exceeds absolute import tolerance {tolerance}')
    audit['checks'].append({'field': label, 'unit': unit, 'raw_value': raw,
        'model_value': raw, 'source': source, 'lower_bound': lower, 'upper_bound': upper,
        'signed_boundary_residual': residual, 'absolute_tolerance': tolerance,
        'accepted_by_tolerance': residual != 0., 'model_adjustment': 0.})
    return raw


def _num(value, label, minimum=None, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise EnergyInputError(f"{label}: finite number required")
    value = float(value)
    if not math.isfinite(value) or (minimum is not None and value < minimum) or (positive and value <= 0):
        raise EnergyInputError(f"{label}: finite number with valid sign required")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EnergyInputError(f"{label}: nonempty identifier/source required")
    return value


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise EnergyInputError(f"{label}: exact key coverage required: {sorted(map(str, expected))}")


def _series(value, periods, label, minimum=None):
    _keys(value, periods, label)
    return {t: _num(value[t], f"{label}[{t}]", minimum) for t in periods}


def _binary(value, label):
    if type(value) is not int or value not in (0, 1):
        raise EnergyInputError(f"{label}: explicit integer 0 or 1 required")
    return value


def _bounded_count(value, label):
    if value is not None and (type(value) is not int or value < 0):
        raise EnergyInputError(f"{label}: nonnegative integer or explicit None required")
    return value


_THERMAL_KEYS = {
    'bus', 'bid_base_mw', 'base_bid_yuan_per_h', 'segments',
    'min_mw', 'max_mw', 'ramp_up_mw_per_h', 'ramp_down_mw_per_h',
    'startup_output_limit_mw', 'shutdown_output_limit_mw',
    'startup_yuan', 'shutdown_yuan', 'min_up_h', 'min_down_h',
    'initial_online', 'initial_power_mw', 'initial_state_age_h', 'initial_state_kind',
    'status', 'fixed_power_mw', 'max_starts', 'max_stops',
    'transition_model', 'terminal_policy', 'source',
}


def _thermal_inputs(thermal, buses, periods, numeric_state_audit, trajectory_initial_units=()):
    if not isinstance(thermal, dict):
        raise EnergyInputError('thermal must be an explicit mapping, possibly empty')
    output = {}
    for g, raw in thermal.items():
        _text(g, 'thermal ID'); _keys(raw, _THERMAL_KEYS, g)
        d = deepcopy(raw)
        _text(d['source'], g + '.source')
        if d['bus'] not in buses:
            raise EnergyInputError(f'{g}: unknown bus')
        if d['transition_model'] != 'instant_with_output_limits':
            raise EnergyInputError(f'{g}: multi-period startup/shutdown trajectories unsupported')
        if d['initial_state_kind'] != 'stable' and not (g in trajectory_initial_units and d['initial_state_kind'] in ('externally_confirmed_trajectory', 'research_assumption_trajectory')):
            raise EnergyInputError(f'{g}: unfinished initial startup/shutdown trajectory unsupported')
        if d['terminal_policy'] != 'carry_out':
            raise EnergyInputError(f'{g}: explicit carry_out terminal policy required')
        for k in ('bid_base_mw', 'min_up_h', 'min_down_h', 'initial_state_age_h'):
            d[k] = _num(d[k], g + '.' + k, 0)
        d['initial_online'] = _binary(d['initial_online'], g + '.initial_online')
        for k in ('min_mw', 'max_mw', 'ramp_up_mw_per_h', 'ramp_down_mw_per_h',
                  'startup_output_limit_mw', 'shutdown_output_limit_mw', 'startup_yuan', 'shutdown_yuan'):
            d[k] = _series(d[k], periods, g + '.' + k, 0)
        d['base_bid_yuan_per_h'] = _series(d['base_bid_yuan_per_h'], periods, g + '.base_bid_yuan_per_h')
        if not isinstance(d['segments'], tuple):
            raise EnergyInputError(f'{g}: segments must be an explicit tuple')
        segments = {}
        for segment in d['segments']:
            _keys(segment, {'key', 'width_mw', 'price_yuan_per_mwh'}, g + '.segment')
            key = _text(segment['key'], g + '.segment.key')
            if key in segments:
                raise EnergyInputError(f'{g}: duplicate bid segment')
            segments[key] = {
                'width_mw': _num(segment['width_mw'], g + '.width_mw', positive=True),
                'price_yuan_per_mwh': _series(segment['price_yuan_per_mwh'], periods, g + '.price_yuan_per_mwh'),
            }
        d['segments'] = segments
        capacity = math.fsum([d['bid_base_mw']] + [s['width_mw'] for s in segments.values()])
        if not math.isfinite(capacity) or capacity <= 0:
            raise EnergyInputError(f'{g}: positive finite bid-covered capacity required')
        d['bid_capacity_mw'] = capacity
        for t in periods:
            if not d['bid_base_mw'] <= d['min_mw'][t] <= d['max_mw'][t] <= capacity:
                raise EnergyInputError(f'{g}[{t}]: bid base <= minimum <= maximum <= bid capacity required')
            for k in ('startup_output_limit_mw', 'shutdown_output_limit_mw'):
                if not d['bid_base_mw'] <= d[k][t] <= capacity:
                    raise EnergyInputError(f'{g}[{t}]: transition output limits must fit supported stable-output subset')
            prices = [s['price_yuan_per_mwh'][t] for s in segments.values()]
            if prices != sorted(prices):
                raise EnergyInputError(f'{g}[{t}]: nondecreasing bid prices required; nonconvex segment ordering unsupported')
        d['initial_power_mw'] = _state_in_bounds(d['initial_power_mw'], g + '.initial_power_mw',
            lower=d['bid_base_mw'] if d['initial_online'] and g not in trajectory_initial_units else 0.,
            upper=capacity if d['initial_online'] else 0., unit='MW',
            source=d['source'], audit=numeric_state_audit)
        _keys(d['status'], periods, g + '.status')
        _keys(d['fixed_power_mw'], periods, g + '.fixed_power_mw')
        for t in periods:
            if d['status'][t] not in {'free', 'on', 'off', 'unavailable'}:
                raise EnergyInputError(f'{g}[{t}]: unsupported status')
            fixed = d['fixed_power_mw'][t]
            if fixed is not None:
                fixed = _num(fixed, f'{g}.fixed_power_mw[{t}]', 0)
                d['fixed_power_mw'][t] = fixed
                if fixed > capacity or (d['status'][t] in {'off', 'unavailable'} and fixed != 0):
                    raise EnergyInputError(f'{g}[{t}]: fixed output conflicts with availability/capacity')
                if fixed > 0 and not d['min_mw'][t] <= fixed <= d['max_mw'][t]:
                    raise EnergyInputError(f'{g}[{t}]: fixed output outside online limits')
                if fixed == 0 and d['status'][t] == 'on' and d['min_mw'][t] > 0:
                    raise EnergyInputError(f'{g}[{t}]: forced online and fixed zero conflict')
        for k in ('max_starts', 'max_stops'):
            _bounded_count(d[k], g + '.' + k)
        output[g] = d
    return output


def _network_inputs(lines, sections, buses, periods, reference_buses):
    if not isinstance(lines, dict) or not isinstance(sections, dict):
        raise EnergyInputError('lines and sections must be explicit mappings')
    ls, ss = {}, {}
    neighbors = {b: set() for b in buses}
    for key, raw in lines.items():
        _text(key, 'line ID')
        _keys(raw, {'from_bus', 'to_bus', 'susceptance_mw_per_rad', 'lower_mw', 'upper_mw', 'source'}, key)
        d = deepcopy(raw)
        if d['from_bus'] not in neighbors or d['to_bus'] not in neighbors or d['from_bus'] == d['to_bus']:
            raise EnergyInputError(f'{key}: invalid line endpoints')
        d['susceptance_mw_per_rad'] = _num(d['susceptance_mw_per_rad'], key + '.susceptance', positive=True)
        d['lower_mw'] = _series(d['lower_mw'], periods, key + '.lower_mw')
        d['upper_mw'] = _series(d['upper_mw'], periods, key + '.upper_mw')
        _text(d['source'], key + '.source')
        if any(d['lower_mw'][t] > d['upper_mw'][t] for t in periods):
            raise EnergyInputError(f'{key}: lower network bound exceeds upper bound')
        neighbors[d['from_bus']].add(d['to_bus']); neighbors[d['to_bus']].add(d['from_bus'])
        ls[key] = d
    for key, raw in sections.items():
        _text(key, 'section ID')
        _keys(raw, {'line_weights', 'lower_mw', 'upper_mw', 'source'}, key)
        d = deepcopy(raw)
        if not isinstance(d['line_weights'], dict) or not d['line_weights'] or not set(d['line_weights']) <= set(ls):
            raise EnergyInputError(f'{key}: nonempty section mapping to known lines required')
        d['line_weights'] = {l: _num(w, key + '.line_weight') for l, w in d['line_weights'].items()}
        if not any(d['line_weights'].values()):
            raise EnergyInputError(f'{key}: section must have a nonzero line weight')
        d['lower_mw'] = _series(d['lower_mw'], periods, key + '.lower_mw')
        d['upper_mw'] = _series(d['upper_mw'], periods, key + '.upper_mw')
        _text(d['source'], key + '.source')
        if any(d['lower_mw'][t] > d['upper_mw'][t] for t in periods):
            raise EnergyInputError(f'{key}: lower section bound exceeds upper bound')
        ss[key] = d
    if not isinstance(reference_buses, (tuple, list)) or len(set(reference_buses)) != len(reference_buses) or not set(reference_buses) <= set(buses):
        raise EnergyInputError('explicit unique reference_buses required')
    unseen = set(buses)
    while unseen:
        root = next(iter(unseen)); component = set(); stack = [root]
        while stack:
            node = stack.pop()
            if node not in component:
                component.add(node); stack.extend(neighbors[node] - component)
        unseen -= component
        if len(component & set(reference_buses)) != 1:
            raise EnergyInputError('exactly one reference bus per connected component required')
    return ls, ss


def build_energy_core(*, horizon, buses, thermal, load_mw, renewable_mw,
                      physical_tie_mw, lines, sections, reference_buses,
                      penalties_yuan_per_mwh, source_notes, balance_policy,
                      shortfall_cap_policy, renewable_mode, renewable_bid_yuan_per_mwh,
                      renewable_curtailment_penalty_yuan_per_mwh, mode='SCUC', commitment=None,
                      _trajectory_initial_units=()):
    """Return an unsolved ConcreteModel with explicit additive injection/cost ports.

    The full input schema and restrictive transition contract are in doc 09.
    SCED fixes every commitment and transition variable; no MIP is reoptimized.
    """
    if not isinstance(horizon, EnergyHorizon):
        raise EnergyInputError('an EnergyHorizon contract is required')
    horizon.validate()
    if mode not in {'SCUC', 'SCED'}:
        raise EnergyInputError('mode must be SCUC or SCED')
    if balance_policy not in {'hard', 'soft'}:
        raise EnergyInputError('balance_policy must explicitly be hard or soft')
    if shortfall_cap_policy not in {'unbounded_slack', 'load_capped'}:
        raise EnergyInputError('shortfall_cap_policy must explicitly be unbounded_slack or load_capped')
    if renewable_mode not in {'fixed_exogenous', 'dispatchable_bus_aggregate'}:
        raise EnergyInputError('unsupported renewable mode')
    if not isinstance(buses, (tuple, list)) or not buses or len(set(buses)) != len(buses):
        raise EnergyInputError('nonempty ordered unique bus sequence required')
    buses = tuple(_text(b, 'bus ID') for b in buses)
    periods = horizon.ids
    duration, ramp_elapsed, previous = horizon.duration_h, horizon.ramp_elapsed_h, horizon.previous
    starts = horizon.start_h
    if not all(math.isfinite(v) for v in (*starts.values(), *horizon.end_h.values())):
        raise EnergyInputError('finite cumulative horizon time required')
    bt = {(b, t) for b in buses for t in periods}
    exogenous = {}
    for name, series, minimum in [('load', load_mw, 0), ('renewable', renewable_mw, 0), ('physical_tie', physical_tie_mw, None)]:
        _keys(series, bt, name)
        exogenous[name] = {k: _num(v, f'{name}[{k}]', minimum) for k, v in series.items()}
    renewable_prices = {}
    for name, series, minimum in [('bid', renewable_bid_yuan_per_mwh, None),
                                 ('curtailment_penalty', renewable_curtailment_penalty_yuan_per_mwh, 0)]:
        _keys(series, bt, 'renewable_' + name)
        renewable_prices[name] = {k: _num(v, f'renewable_{name}[{k}]', minimum) for k, v in series.items()}
    numeric_state_audit = _state_boundary_audit()
    if _trajectory_initial_units and (mode != 'SCED' or horizon.mode != 'real_time_5min'):
        raise EnergyInputError('trajectory initial extension is restricted to October fixed SCED')
    thermal = _thermal_inputs(thermal, buses, periods, numeric_state_audit, _trajectory_initial_units)
    lines, sections = _network_inputs(lines, sections, buses, periods, reference_buses)
    pkeys = {'line', 'section', 'balance_shortfall', 'balance_surplus'}
    _keys(penalties_yuan_per_mwh, pkeys, 'penalties_yuan_per_mwh')
    penalties = {k: _num(v, 'penalty.' + k, positive=True) for k, v in penalties_yuan_per_mwh.items()}
    nkeys = {'thermal', 'transition', 'network', 'penalties', 'exogenous', 'boundary_basis',
             'balance_policy', 'shortfall_cap_policy', 'renewable_policy'}
    if not isinstance(source_notes, dict) or not nkeys <= set(source_notes):
        raise EnergyInputError('required source_notes missing')
    for key, value in source_notes.items():
        _text(key, 'source note key'); _text(value, key + '.source_note')
    if mode == 'SCED':
        _keys(commitment, {(g, t) for g in thermal for t in periods}, 'commitment')
        commitment = {k: _binary(v, f'commitment[{k}]') for k, v in commitment.items()}
        for g, d in thermal.items():
            for t in periods:
                required = 1 if d['status'][t] == 'on' else 0 if d['status'][t] in {'off', 'unavailable'} else None
                if required is not None and commitment[g, t] != required:
                    raise EnergyInputError(f'{g}[{t}]: fixed commitment conflicts with status')
    elif commitment is not None:
        raise EnergyInputError('SCUC must not receive a fixed SCED commitment')

    m = pyo.ConcreteModel(name='gd_energy_core_' + mode)
    m.B = pyo.Set(initialize=buses, ordered=True)
    m.T = pyo.Set(initialize=periods, ordered=True)
    m.G = pyo.Set(initialize=tuple(thermal), ordered=True)
    m.L = pyo.Set(initialize=tuple(lines), ordered=True)
    m.S = pyo.Set(initialize=tuple(sections), ordered=True)
    m.GK = pyo.Set(dimen=2, initialize=[(g, k) for g, d in thermal.items() for k in d['segments']])
    m.demand_mw = pyo.Param(m.B, m.T, initialize=exogenous['load'], mutable=True, within=pyo.NonNegativeReals)
    m.renewable_available = pyo.Param(m.B, m.T, initialize=exogenous['renewable'], within=pyo.NonNegativeReals)
    m.renewable_power = pyo.Var(m.B, m.T, domain=pyo.NonNegativeReals, initialize=exogenous['renewable'])
    m.renewable_curtailment = pyo.Var(m.B, m.T, domain=pyo.NonNegativeReals, initialize=0.)
    m.c_renewable_available = pyo.Constraint(m.B, m.T, rule=lambda _, b, t:
        m.renewable_power[b, t] + m.renewable_curtailment[b, t] == m.renewable_available[b, t])
    if renewable_mode == 'fixed_exogenous':
        for b in buses:
            for t in periods:
                m.renewable_power[b, t].fix(exogenous['renewable'][b, t])
                m.renewable_curtailment[b, t].fix(0.)
    m.physical_tie = pyo.Param(m.B, m.T, initialize=exogenous['physical_tie'], within=pyo.Reals)
    # Named Expressions, not Python snapshots, preserve later additive blocks.
    m.other_injection = pyo.Expression(m.B, m.T, rule=lambda *_: 0.)
    m.other_cost = pyo.Expression(expr=0.)
    domain = pyo.Binary if mode == 'SCUC' else pyo.UnitInterval
    m.online = pyo.Var(m.G, m.T, domain=domain, initialize=0)
    m.start = pyo.Var(m.G, m.T, domain=domain, initialize=0)
    m.stop = pyo.Var(m.G, m.T, domain=domain, initialize=0)
    m.power = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.segment_power = pyo.Var(m.GK, m.T, domain=pyo.NonNegativeReals, initialize=0)
    if mode == 'SCED':
        for g, d in thermal.items():
            for t in periods:
                before = d['initial_online'] if previous[t] is None else commitment[g, previous[t]]
                now = commitment[g, t]
                m.online[g, t].fix(now); m.start[g, t].fix(max(0, now - before)); m.stop[g, t].fix(max(0, before - now))

    def prev_online(g, t):
        return thermal[g]['initial_online'] if previous[t] is None else m.online[g, previous[t]]

    def prev_power(g, t):
        return thermal[g]['initial_power_mw'] if previous[t] is None else m.power[g, previous[t]]

    m.c_commitment = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.online[g, t] - prev_online(g, t) == m.start[g, t] - m.stop[g, t])
    m.c_transition_exclusive = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.start[g, t] + m.stop[g, t] <= 1)
    m.c_segment_capacity = pyo.Constraint(m.GK, m.T, rule=lambda _, g, k, t: m.segment_power[g, k, t] <= thermal[g]['segments'][k]['width_mw'] * m.online[g, t])
    m.c_power_sum = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] == thermal[g]['bid_base_mw'] * m.online[g, t] + sum(m.segment_power[g, k, t] for k in thermal[g]['segments']))
    m.online_min = pyo.Expression(m.G, m.T, rule=lambda _, g, t: thermal[g]['min_mw'][t] * m.online[g, t])
    m.online_max = pyo.Expression(m.G, m.T, rule=lambda _, g, t: thermal[g]['max_mw'][t] * m.online[g, t])
    m.c_power_min = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] >= m.online_min[g, t])
    m.c_power_max = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] <= m.online_max[g, t])
    m.up_headroom = pyo.Expression(m.G, m.T, rule=lambda _, g, t: m.online_max[g, t] - m.power[g, t])
    m.down_headroom = pyo.Expression(m.G, m.T, rule=lambda _, g, t: m.power[g, t] - m.online_min[g, t])
    m.c_ramp_up = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] - prev_power(g, t) <= thermal[g]['ramp_up_mw_per_h'][t] * ramp_elapsed[t] * prev_online(g, t) + thermal[g]['startup_output_limit_mw'][t] * m.start[g, t])
    m.c_ramp_down = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: prev_power(g, t) - m.power[g, t] <= thermal[g]['ramp_down_mw_per_h'][t] * ramp_elapsed[t] * m.online[g, t] + thermal[g]['shutdown_output_limit_mw'][t] * m.stop[g, t])
    # Time windows use physical chain elapsed time, not arbitrary integer IDs.
    up_windows = {(g, t): [s for s in periods if starts[s] <= starts[t] and starts[t] - starts[s] < d['min_up_h'] - 1e-10] for g, d in thermal.items() for t in periods}
    down_windows = {(g, t): [s for s in periods if starts[s] <= starts[t] and starts[t] - starts[s] < d['min_down_h'] - 1e-10] for g, d in thermal.items() for t in periods}
    m.c_min_up = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: sum(m.start[g, s] for s in up_windows[g, t]) <= m.online[g, t])
    m.c_min_down = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: sum(m.stop[g, s] for s in down_windows[g, t]) <= 1 - m.online[g, t])
    initial = {(g, t): d['initial_online'] for g, d in thermal.items() for t in periods
               if d['initial_state_age_h'] + starts[t] < (d['min_up_h'] if d['initial_online'] else d['min_down_h']) - 1e-10}
    m.c_initial_min_duration = pyo.Constraint(list(initial), rule=lambda _, g, t: m.online[g, t] == initial[g, t])
    statuses = {(g, t): 1 if d['status'][t] == 'on' else 0 for g, d in thermal.items() for t in periods if d['status'][t] != 'free'}
    m.c_status = pyo.Constraint(list(statuses), rule=lambda _, g, t: m.online[g, t] == statuses[g, t])
    fixed_outputs = {(g, t): d['fixed_power_mw'][t] for g, d in thermal.items() for t in periods if d['fixed_power_mw'][t] is not None}
    m.c_fixed_power = pyo.Constraint(list(fixed_outputs), rule=lambda _, g, t: m.power[g, t] == fixed_outputs[g, t])
    max_starts = [g for g, d in thermal.items() if d['max_starts'] is not None]
    max_stops = [g for g, d in thermal.items() if d['max_stops'] is not None]
    m.c_max_starts = pyo.Constraint(max_starts, rule=lambda _, g: sum(m.start[g, t] for t in periods) <= thermal[g]['max_starts'])
    m.c_max_stops = pyo.Constraint(max_stops, rule=lambda _, g: sum(m.stop[g, t] for t in periods) <= thermal[g]['max_stops'])

    m.angle = pyo.Var(m.B, m.T, domain=pyo.Reals, initialize=0)
    m.flow = pyo.Var(m.L, m.T, domain=pyo.Reals, initialize=0)
    m.line_over = pyo.Var(m.L, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.line_under = pyo.Var(m.L, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.section_over = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.section_under = pyo.Var(m.S, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.balance_shortfall = pyo.Var(m.B, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.balance_surplus = pyo.Var(m.B, m.T, domain=pyo.NonNegativeReals, initialize=0)
    if balance_policy == 'hard':
        for b in buses:
            for t in periods:
                m.balance_shortfall[b, t].fix(0.); m.balance_surplus[b, t].fix(0.)
    if shortfall_cap_policy == 'load_capped':
        m.c_shortfall_cap = pyo.Constraint(m.B, m.T, rule=lambda _, b, t: m.balance_shortfall[b, t] <= m.demand_mw[b, t])
    m.c_reference = pyo.Constraint(reference_buses, m.T, rule=lambda _, b, t: m.angle[b, t] == 0)
    m.c_dc_flow = pyo.Constraint(m.L, m.T, rule=lambda _, l, t: m.flow[l, t] == lines[l]['susceptance_mw_per_rad'] * (m.angle[lines[l]['from_bus'], t] - m.angle[lines[l]['to_bus'], t]))
    m.c_line_upper = pyo.Constraint(m.L, m.T, rule=lambda _, l, t: m.flow[l, t] <= lines[l]['upper_mw'][t] + m.line_over[l, t])
    m.c_line_lower = pyo.Constraint(m.L, m.T, rule=lambda _, l, t: m.flow[l, t] >= lines[l]['lower_mw'][t] - m.line_under[l, t])
    m.section_flow = pyo.Expression(m.S, m.T, rule=lambda _, s, t: sum(w * m.flow[l, t] for l, w in sections[s]['line_weights'].items()))
    m.c_section_upper = pyo.Constraint(m.S, m.T, rule=lambda _, s, t: m.section_flow[s, t] <= sections[s]['upper_mw'][t] + m.section_over[s, t])
    m.c_section_lower = pyo.Constraint(m.S, m.T, rule=lambda _, s, t: m.section_flow[s, t] >= sections[s]['lower_mw'][t] - m.section_under[s, t])
    m.net_load = pyo.Expression(m.T, rule=lambda _, t: sum(m.demand_mw[b, t] - m.renewable_power[b, t] - m.physical_tie[b, t] - m.other_injection[b, t] for b in buses))
    m.raw_system_demand_minus_tie = pyo.Expression(m.T, rule=lambda _, t:
        sum(m.demand_mw[b, t] - m.physical_tie[b, t] for b in buses))
    at_bus = {b: [g for g, d in thermal.items() if d['bus'] == b] for b in buses}
    outgoing = {b: [l for l, d in lines.items() if d['from_bus'] == b] for b in buses}
    incoming = {b: [l for l, d in lines.items() if d['to_bus'] == b] for b in buses}
    m.c_balance = pyo.Constraint(m.B, m.T, rule=lambda _, b, t:
        sum(m.power[g, t] for g in at_bus[b]) + m.renewable_power[b, t] + m.physical_tie[b, t] + m.other_injection[b, t]
        + m.balance_shortfall[b, t] - m.balance_surplus[b, t] - m.demand_mw[b, t]
        == sum(m.flow[l, t] for l in outgoing[b]) - sum(m.flow[l, t] for l in incoming[b]))

    m.thermal_bid_cost = pyo.Expression(expr=sum(duration[t] * (
        d['base_bid_yuan_per_h'][t] * m.online[g, t]
        + sum(s['price_yuan_per_mwh'][t] * m.segment_power[g, k, t] for k, s in d['segments'].items()))
        for g, d in thermal.items() for t in periods))
    m.renewable_bid_cost = pyo.Expression(expr=sum(duration[t] * renewable_prices['bid'][b, t] * m.renewable_power[b, t]
                                                  for b in buses for t in periods))
    m.renewable_curtailment_cost = pyo.Expression(expr=sum(duration[t] * renewable_prices['curtailment_penalty'][b, t] * m.renewable_curtailment[b, t]
                                                          for b in buses for t in periods))
    m.energy_bid_cost = pyo.Expression(expr=m.thermal_bid_cost + m.renewable_bid_cost)
    m.startup_cost = pyo.Expression(expr=sum(d['startup_yuan'][t] * m.start[g, t] for g, d in thermal.items() for t in periods))
    m.shutdown_cost = pyo.Expression(expr=sum(d['shutdown_yuan'][t] * m.stop[g, t] for g, d in thermal.items() for t in periods))
    m.network_penalty_cost = pyo.Expression(expr=sum(duration[t] * (
        penalties['line'] * sum(m.line_over[l, t] + m.line_under[l, t] for l in lines)
        + penalties['section'] * sum(m.section_over[s, t] + m.section_under[s, t] for s in sections)) for t in periods))
    m.balance_penalty_cost = pyo.Expression(expr=sum(duration[t] * (
        penalties['balance_shortfall'] * m.balance_shortfall[b, t] + penalties['balance_surplus'] * m.balance_surplus[b, t]) for b in buses for t in periods))
    m.total_cost = pyo.Expression(expr=m.energy_bid_cost + m.renewable_curtailment_cost + m.startup_cost + m.shutdown_cost + m.network_penalty_cost + m.balance_penalty_cost + m.other_cost)
    m.objective = pyo.Objective(expr=m.total_cost, sense=pyo.minimize)
    m._energy_horizon = horizon
    m._energy_inputs = {'thermal': thermal, 'lines': lines, 'sections': sections,
                        'source_notes': deepcopy(source_notes), 'penalties_yuan_per_mwh': penalties,
                        'renewable_prices': renewable_prices}
    m._energy_scope = {'mode': mode, 'supported_subset_only': True,
        'conventional_hydro': False, 'cross_regional_market': False,
        'renewable_mode': renewable_mode, 'renewable_inventory_granularity': 'bus_aggregate_research',
        'balance_policy': balance_policy, 'shortfall_cap_policy': shortfall_cap_policy,
        'net_load_is_GD_operating_reserve_D_minus_T': False,
        'transition_model': 'instant_with_output_limits', 'initial_state_kind': 'stable',
        'startup_cost_model': 'explicit_uniform_per_event', 'terminal_policy': 'carry_out',
        'network_model': 'lossless_dc_angles_no_contingency',
        'final_rule_lmp': False, 'settlement_or_service_payments_in_cost': False,
        'max_transition_count_scope': 'entire_explicit_horizon',
        'requested_highs_relative_gap': .001, 'horizon': horizon.audit(),
        'numeric_state_boundary': numeric_state_audit}
    assert_linear_energy_model(m)
    return m


def assert_linear_energy_model(model, *, require_fixed_commitment=False, require_lp=False):
    """Reject SOS, nonlinear or nonfinite active mathematics after any attachments.

    This is a structural audit, not a feasibility, policy or solver certificate.
    """
    if any(True for _ in model.component_data_objects(pyo.SOSConstraint, active=True)):
        raise EnergyInputError('SOS constraints are unsupported')
    supported = {pyo.Block, pyo.Set, pyo.RangeSet, pyo.Param, pyo.Var, pyo.Constraint, pyo.Expression, pyo.Objective, pyo.Suffix}
    for component in model.component_objects(active=True, descend_into=True):
        if component.ctype not in supported:
            raise EnergyInputError(f'{component.name}: unsupported algebraic component')
    objectives = list(model.component_data_objects(pyo.Objective, active=True))
    if len(objectives) != 1:
        raise EnergyInputError('exactly one active objective required')
    if require_lp:
        for variable in model.component_data_objects(pyo.Var, active=True):
            if variable.is_integer() and not variable.fixed:
                raise EnergyInputError(f'{variable.name}: unfixed integer in requested LP')
    if require_fixed_commitment:
        for name in ('online', 'start', 'stop'):
            if not hasattr(model, name) or any(not x.fixed for x in getattr(model, name).values()):
                raise EnergyInputError('all thermal commitments/transitions must be explicitly fixed')
    for variable in model.component_data_objects(pyo.Var, active=True):
        for bound in (variable.lb, variable.ub):
            if bound is not None and not math.isfinite(float(pyo.value(bound))):
                raise EnergyInputError(f'{variable.name}: nonfinite variable bound')
        if (variable.fixed and variable.value is None) or (variable.value is not None and not math.isfinite(float(variable.value))):
            raise EnergyInputError(f'{variable.name}: nonfinite or missing fixed value')
    rows = list(model.component_data_objects(pyo.Constraint, active=True))
    for item in [*rows, *objectives]:
        expr = item.body if item.ctype is pyo.Constraint else item.expr
        try:
            repn = generate_standard_repn(expr, compute_values=True)
            valid = repn.is_linear() and math.isfinite(float(repn.constant or 0)) and all(math.isfinite(float(x)) for x in repn.linear_coefs)
            if item.ctype is pyo.Constraint:
                valid = valid and all(bound is None or math.isfinite(float(pyo.value(bound))) for bound in (item.lower, item.upper))
        except (TypeError, ValueError, OverflowError) as exc:
            raise EnergyInputError(f'{item.name}: invalid finite linear expression') from exc
        if not valid:
            raise EnergyInputError(f'{item.name}: finite linear expression required')
    return {'linear': True, 'sos': False, 'active_constraints': len(rows), 'active_objectives': 1,
            'feasibility_checked': False, 'solver_called': False}


def thermal_reserve_ports(model, *, ramp_10min_mw=None):
    """Expose capacity ports; optional RT ramp capability must be separately supplied."""
    periods = tuple(model.T)
    if ramp_10min_mw is not None:
        _keys(ramp_10min_mw, set(model.G), 'thermal RT ramp ports')
        ramps = {g: _series(ramp_10min_mw[g], periods, g + '.ramp_10min_mw', 0) for g in model.G}
    result = {}
    for g in model.G:
        result[g] = {key: {t: component[g, t] for t in periods} for key, component in (
            ('power', model.power), ('online_max', model.online_max), ('online_min', model.online_min))}
        if ramp_10min_mw is not None:
            result[g]['ramp_10min_mw'] = ramps[g]
    return result


def terminal_thermal_state(model, *, through_period=None):
    """Read a feasible solution boundary, with residual obligations for continuation.

    Caller must verify solver feasibility first. through_period can be the last
    D-day delivery point or first executed RT point, excluding lookahead tails.
    """
    horizon = model._energy_horizon
    periods = horizon.ids
    if through_period is None:
        through_period = periods[-1]
    if through_period not in periods:
        raise EnergyInputError('unknown terminal boundary period')
    upto = periods[:periods.index(through_period) + 1]
    end = horizon.end_h[through_period]
    result = {}
    for g, d in model._energy_inputs['thermal'].items():
        prev = d['initial_online']; last_change = None
        for t in upto:
            raw = _num(pyo.value(model.online[g, t], exception=False), f'{g}.online[{t}]', 0)
            if min(abs(raw), abs(raw - 1)) > 1e-6:
                raise EnergyInputError('terminal state requires integral commitment solution')
            current = int(round(raw))
            if current != prev:
                last_change = horizon.start_h[t]
            prev = current
        age = end - last_change if last_change is not None else d['initial_state_age_h'] + end
        power = _num(pyo.value(model.power[g, through_period], exception=False), g + '.terminal_power', 0)
        minimum = d['min_up_h'] if prev else d['min_down_h']
        result[g] = {'online': prev, 'power_mw': power, 'state_age_h': age,
                     'remaining_min_state_h': max(0., minimum - age),
                     'initial_state_kind': 'stable', 'through_period': through_period,
                     'boundary_elapsed_h': end, 'feasibility_certified_by_this_function': False}
    return result
