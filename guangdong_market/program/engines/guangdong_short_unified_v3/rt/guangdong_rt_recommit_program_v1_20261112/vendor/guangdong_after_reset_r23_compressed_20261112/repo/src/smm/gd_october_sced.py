"""October-2026 Guangdong 24 x 5-minute SCED, explicitly limited subset.

Unsolved continuous model. Stable commitments or explicitly confirmed external
thermal trajectories; no reserve auction, price policy, or solver invocation.
Rules identify formulas, not missing operating parameters. See docs/50.
"""
from __future__ import annotations

from copy import deepcopy
import math
from hashlib import sha256
import json

import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn

from .gd_horizon import EnergyHorizon, EnergyPeriod
from .gd_energy_core import (
    EnergyInputError, _num, _text, _keys, _series, _binary,
    build_energy_core, assert_linear_energy_model,
)

RULE_VERSION = 'southern_region_2026_v1_20261023'
RULE_SHA256 = '978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf'
PERIODS = tuple(range(1, 25))
RESERVE_SHARING = 'independent_rule_requirements_shared_headroom'
CAPACITY_BASIS = 'explicit_eligible_generation_capacity_only'
FIXED_CAPACITY_SCHEMA = 'gd.october.fixed_renewable_absolute_capacity.v2'
FIXED_RESEARCH_CAPACITY_SCHEMA = 'gd.october.fixed_renewable_absolute_capacity.v4'


def october_sced_horizon(time_mapping_source: str) -> EnergyHorizon:
    """First three flags are execution candidates, NOT execution/settlement proof."""
    return EnergyHorizon(tuple(EnergyPeriod(t, 1/12, 1/12, 'delivery', t <= 3)
                               for t in PERIODS), 'real_time_5min',
                         time_mapping_source).validate()


def _sources(mapping, names, label):
    _keys(mapping, names, label)
    return {k: _text(v, f'{label}.{k}') for k, v in mapping.items()}


def _eligibility(values, periods, label):
    _keys(values, periods, label)
    return {t: _binary(values[t], f'{label}[{t}]') for t in periods}


def _reserve_inputs(raw, thermal, periods, buses, renewable_available):
    fields = {'rule_version', 'requirements', 'thermal', 'load_side', 'sources',
              'sharing_policy', 'contingency_slack', 'renewable_capacity'}
    if isinstance(raw, dict) and 'capacity_accounting' in raw:
        fields.add('capacity_accounting')
    _keys(raw, fields, 'reserves')
    if raw['rule_version'] != RULE_VERSION:
        raise EnergyInputError('reserves require the October 2026 official rule version')
    if raw['sharing_policy'] != RESERVE_SHARING:
        raise EnergyInputError('reserve sharing interpretation must be explicitly selected')
    names = {'positive_mw', 'negative_mw', 'primary_mw', 'contingency_mw',
             'positive_network_deduction_mw', 'contingency_deduction_mw',
             'negative_demand_mw'}
    _keys(raw['requirements'], names, 'reserve.requirements')
    req = {k: _series(v, periods, 'reserve.' + k, 0)
           for k, v in raw['requirements'].items()}
    sources = _sources(raw['sources'], names | {'sharing_policy', 'negative_demand_basis',
                       'capacity_qualification_basis'}, 'reserve.sources')
    _keys(raw['thermal'], thermal, 'reserve.thermal')
    units = {}
    for g, d in raw['thermal'].items():
        _keys(d, {'primary_eligible', 'primary_fraction', 'contingency_eligible',
                  'contingency_class', 'ramp_10min_mw', 'sources'}, g + '.reserve')
        r = {k: _eligibility(d[k], periods, g + '.' + k)
             for k in ('primary_eligible', 'contingency_eligible')}
        r['primary_fraction'] = _series(d['primary_fraction'], periods, g + '.primary_fraction', 0)
        if any(v > 1 for v in r['primary_fraction'].values()):
            raise EnergyInputError('primary capacity fraction must lie in [0,1]')
        r['ramp_10min_mw'] = _series(d['ramp_10min_mw'], periods, g + '.ramp_10min_mw', 0)
        if d['contingency_class'] not in ('fixed', 'nonfixed'):
            raise EnergyInputError('explicit fixed/nonfixed thermal accident-reserve class required')
        r['contingency_class'] = d['contingency_class']
        if d['contingency_class'] == 'fixed' and any(
                thermal[g]['fixed_power_mw'].get(t) is None for t in periods):
            raise EnergyInputError(f'{g}: fixed contingency class requires every fixed power')
        if d['contingency_class'] == 'nonfixed' and any(
                thermal[g]['fixed_power_mw'].get(t) is not None for t in periods):
            raise EnergyInputError(f'{g}: nonfixed contingency class contradicts fixed power')
        r['sources'] = _sources(d['sources'], {'primary_eligible', 'primary_fraction',
                   'contingency_eligible', 'contingency_class', 'ramp_10min_mw'}, g + '.reserve.sources')
        units[g] = r
    if not isinstance(raw['load_side'], dict):
        raise EnergyInputError('explicit load_side mapping, possibly empty, required')
    load_side = {}
    for q, d in raw['load_side'].items():
        _text(q, 'load-side resource ID')
        _keys(d, {'eligible', 'capacity_mw', 'source', 'qualification_source'}, q)
        load_side[q] = {'eligible': _eligibility(d['eligible'], periods, q + '.eligible'),
            'capacity_mw': _series(d['capacity_mw'], periods, q + '.capacity_mw', 0),
            'source': _text(d['source'], q + '.source'),
            'qualification_source': _text(d['qualification_source'], q + '.qualification_source')}
    slack = deepcopy(raw['contingency_slack'])
    _keys(slack, {'policy', 'penalty_yuan_per_mw_h', 'source'}, 'contingency_slack')
    _text(slack['source'], 'contingency_slack.source')
    if slack['policy'] == 'hard':
        if slack['penalty_yuan_per_mw_h'] is not None:
            raise EnergyInputError('hard contingency reserve must explicitly have no slack penalty')
    elif slack['policy'] == 'soft':
        slack['penalty_yuan_per_mw_h'] = _num(slack['penalty_yuan_per_mw_h'],
                                                    'contingency_slack.penalty', positive=True)
    else:
        raise EnergyInputError('explicit hard/soft contingency slack policy required')
    _keys(raw['renewable_capacity'], buses, 'reserve.renewable_capacity')
    renewable_capacity = {}
    for bus, d in raw['renewable_capacity'].items():
        _keys(d, {'participation', 'positive_mw', 'negative_mw', 'source', 'qualification_source'},
              'reserve.renewable_capacity.' + str(bus))
        if d['participation'] not in ('excluded_unverified', 'explicit_qualified'):
            raise EnergyInputError('explicit renewable qualification or exclusion required; dispatch baseline is not capacity')
        x = {k: _series(d[k], periods, 'renewable_capacity.' + k, 0) for k in ('positive_mw', 'negative_mw')}
        x['participation'] = d['participation']
        for name in ('source', 'qualification_source'):
            x[name] = _text(d[name], 'renewable_capacity.' + name)
        for t in periods:
            if d['participation'] == 'excluded_unverified' and (x['positive_mw'][t] or x['negative_mw'][t]):
                raise EnergyInputError('excluded renewable units cannot receive capacity credit')
            if not 0 <= x['negative_mw'][t] <= x['positive_mw'][t] <= _num(renewable_available[bus, t], 'renewable availability', 0):
                raise EnergyInputError('renewable qualified capacity must fit forecast availability and ordered min/max')
        renewable_capacity[bus] = x
    return req, units, load_side, slack, sources, renewable_capacity


def _fixed_capacity_inputs(raw, renewable_capacity, renewable_available, buses, periods, renewable_mode):
    """V2 absolute fixed-energy accounting, never auxiliary-service eligibility.

    Legacy inputs omit this field and retain their historical algebra. For V2,
    fixed renewable injection has absolute max=min=F and zero incremental
    reserve. Keeping the legacy eligibility credit at zero avoids double credit.
    This extension deliberately rejects dispatchable/curtailed renewables.
    """
    _keys(raw, {'schema_version', 'rule_sha256', 'source', 'source_ref', 'interpretation',
                'renewable_fixed_injection_mw', 'incremental_up_mw',
                'incremental_down_mw', 'qualifications_asserted'}, 'capacity_accounting')
    if raw['schema_version'] not in (FIXED_CAPACITY_SCHEMA,FIXED_RESEARCH_CAPACITY_SCHEMA) or raw['rule_sha256'] != RULE_SHA256:
        raise EnergyInputError('explicit V2 or research V4 fixed renewable absolute-capacity rule identity required')
    if raw['interpretation'] != 'fixed_energy_absolute_max_equals_min_no_incremental_reserve':
        raise EnergyInputError('fixed absolute capacity must not be interpreted as incremental reserve')
    _text(raw['source'], 'capacity_accounting.source')
    _keys(raw['source_ref'], {'source_id', 'source_sha256'}, 'capacity_accounting.source_ref')
    _text(raw['source_ref']['source_id'], 'capacity_accounting source ID')
    digest = raw['source_ref']['source_sha256']
    if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise EnergyInputError('capacity_accounting requires a source SHA256')
    if raw['qualifications_asserted'] is not False:
        raise EnergyInputError('fixed-energy accounting cannot assert renewable reserve qualification')
    if any(_num(raw[k], 'capacity_accounting.' + k, 0) != 0 for k in ('incremental_up_mw', 'incremental_down_mw')):
        raise EnergyInputError('fixed renewable incremental reserve must be zero')
    if renewable_mode != 'fixed_exogenous':
        raise EnergyInputError('V2 fixed capacity requires fixed_exogenous renewable injection')
    _keys(raw['renewable_fixed_injection_mw'], buses, 'capacity_accounting.renewable_fixed_injection_mw')
    fixed = {}
    for b in buses:
        d = renewable_capacity[b]
        if d['participation'] != 'excluded_unverified' or any(d[k][t] for k in ('positive_mw', 'negative_mw') for t in periods):
            raise EnergyInputError('fixed renewable energy and qualified capacity must not be double credited')
        fixed[b] = _series(raw['renewable_fixed_injection_mw'][b], periods, 'fixed renewable injection', 0)
        for t in periods:
            if abs(fixed[b][t] - _num(renewable_available[b, t], 'renewable availability', 0)) > 1e-8:
                raise EnergyInputError('absolute renewable max=min must equal its fixed forecast injection')
    result = deepcopy(raw)
    result['renewable_fixed_injection_mw'] = fixed
    return result


def _agc_inputs(raw, thermal, commitment, periods):
    _keys(raw, {'thermal', 'boundary_interpretation_source'}, 'agc')
    _text(raw['boundary_interpretation_source'], 'agc.boundary_interpretation_source')
    _keys(raw['thermal'], thermal, 'agc.thermal')
    result = {}
    for g, d in raw['thermal'].items():
        _keys(d, {'up_mw', 'down_mw', 'basis', 'source'}, g + '.agc')
        if d['basis'] not in ('explicit_no_reservation', 'awarded_symmetric_agc', 'external_effective_directional_boundary'):
            raise EnergyInputError('AGC requires an awarded effective boundary or explicit no reservation; forecast reserve unsupported')
        x = {'up_mw': _series(d['up_mw'], periods, g + '.agc.up', 0),
             'down_mw': _series(d['down_mw'], periods, g + '.agc.down', 0),
             'basis': d['basis'], 'source': _text(d['source'], g + '.agc.source')}
        for t in periods:
            up, down = x['up_mw'][t], x['down_mw'][t]
            if d['basis'] == 'awarded_symmetric_agc' and up != down:
                raise EnergyInputError('formal AGC award is symmetric; directional effective bounds need their own explicit external basis')
            if d['basis'] == 'explicit_no_reservation' and (up or down):
                raise EnergyInputError('explicit_no_reservation must contain only explicit zeros')
            if not commitment[g, t] and (up or down):
                raise EnergyInputError('offline thermal cannot receive AGC reservation')
            if up + down > thermal[g]['max_mw'][t] - thermal[g]['min_mw'][t]:
                raise EnergyInputError('AGC reservations invert the available operating interval')
        result[g] = x
    return result


def build_october_sced(*, horizon, commitment, reserves, agc, storage_spec,
                       implementation_sources, renewable_dispatch_profile=None, thermal_trajectories=None,
                       storage_capacity_accounting=None, **energy_inputs):
    """Build, do not solve, the supported October SCED physical model.

    ``energy_inputs`` is the explicit gd_energy_core schema, without mode or
    horizon. Renewable injections are fixed or explicitly aggregated nonpriced
    forecast/deviation units; priced renewable guarantee-factor policy is absent.
    ``storage_spec=None`` explicitly means no storage; nonempty specs are passed
    to attach_gd_october_storage. Reserve/AGC numbers have no policy defaults.
    """
    if not isinstance(horizon, EnergyHorizon):
        raise EnergyInputError('explicit EnergyHorizon required')
    horizon.validate()
    if horizon.mode != 'real_time_5min' or horizon.ids != PERIODS:
        raise EnergyInputError('October SCED requires real_time_5min with IDs 1 through 24')
    if 'mode' in energy_inputs:
        raise EnergyInputError('October SCED fixes commitment; mode must not be supplied')
    unsupported_objects = {'unit_groups', 'group_power_limits', 'group_energy_limits'} & set(energy_inputs)
    if unsupported_objects:
        raise EnergyInputError('unsupported physical objects require a dedicated extension: ' + ', '.join(sorted(unsupported_objects)))
    sources = _sources(implementation_sources,
        {'effective_commitment', 'scope_exclusions', 'objective_units',
         'network_translation', 'dispatch_penalties', 'renewable_fixed_boundary'},
        'implementation_sources')
    thermal = energy_inputs.get('thermal')
    if not isinstance(thermal, dict):
        raise EnergyInputError('explicit thermal mapping required')
    _keys(commitment, {(g, t) for g in thermal for t in PERIODS}, 'commitment')
    commitment = {k: _binary(v, f'commitment[{k}]') for k, v in commitment.items()}
    # Reject legacy instant transitions rather than presenting them as the new
    # official startup/shutdown trajectory. No capability is inferred from age.
    for g, d in thermal.items():
        if not isinstance(d, dict) or 'initial_online' not in d:
            raise EnergyInputError(f'{g}: explicit initial online state required')
        initial = _binary(d['initial_online'], g + '.initial_online')
        if thermal_trajectories is None and any(commitment[g, t] != initial for t in PERIODS):
            raise EnergyInputError(f'{g}: unsupported transition/startup trajectory in stable-commitment subset')
    trajectory_context = None
    if thermal_trajectories is not None:
        if type(thermal_trajectories) is not dict or set(thermal_trajectories)!={'book','window_start','information_cutoff'}:
            raise EnergyInputError('unsupported physical objects: unrecognized thermal trajectory contract')
        from .gd_october_thermal import validate_model_context
        trajectory_context = validate_model_context(thermal_trajectories, thermal, commitment)
    renewable_mode = energy_inputs.get('renewable_mode')
    if renewable_mode == 'dispatchable_bus_aggregate':
        _keys(renewable_dispatch_profile, {'kind', 'aggregation', 'source', 'aggregation_source',
              'reserve_capacity_source'}, 'renewable_dispatch_profile')
        if renewable_dispatch_profile['kind'] != 'nonpriced_forecast_with_deviation':
            raise EnergyInputError('only nonpriced forecast/deviation renewable policy is supported')
        if renewable_dispatch_profile['aggregation'] != 'same_bus_identical_offer_and_penalty':
            raise EnergyInputError('explicit same-bus identical offer/penalty aggregation required')
        for name in ('source', 'aggregation_source', 'reserve_capacity_source'):
            _text(renewable_dispatch_profile[name], 'renewable_dispatch_profile.' + name)
        bids = energy_inputs.get('renewable_bid_yuan_per_mwh')
        if not isinstance(bids, dict) or any(_num(v, 'nonpriced renewable bid') != 0 for v in bids.values()):
            raise EnergyInputError('nonpriced renewable forecast/deviation subset requires explicit zero energy bids')
    elif renewable_mode == 'fixed_exogenous':
        if renewable_dispatch_profile is not None:
            raise EnergyInputError('fixed exogenous renewables must not receive an unused dispatch profile')
    else:
        raise EnergyInputError('unsupported renewable mode for October physical subset')
    # Existing builder validates complete finite thermal/network/price inputs.
    m = build_energy_core(horizon=horizon, mode='SCED', commitment=commitment,
        _trajectory_initial_units=() if trajectory_context is None else trajectory_context['inherited'], **energy_inputs)
    if trajectory_context is not None:
        from .gd_october_thermal import attach_thermal_model
        attach_thermal_model(m, trajectory_context)
    req, units, load_side, slack, reserve_sources, renewable_capacity = _reserve_inputs(
        reserves, m._energy_inputs['thermal'], PERIODS, tuple(m.B), energy_inputs['renewable_mw'])
    fixed_capacity = (_fixed_capacity_inputs(reserves['capacity_accounting'], renewable_capacity,
        energy_inputs['renewable_mw'], tuple(m.B), PERIODS, renewable_mode)
        if 'capacity_accounting' in reserves else None)
    av = _agc_inputs(agc, m._energy_inputs['thermal'], commitment, PERIODS)
    if trajectory_context is not None:
        for g,t in trajectory_context['cells']:
            if av[g]['up_mw'][t] or av[g]['down_mw'][t] or units[g]['primary_eligible'][t] or units[g]['contingency_eligible'][t]:
                raise EnergyInputError('fixed thermal trajectory requires explicit no AGC/primary/contingency eligibility')
    m._gd_october_horizon = horizon
    m._gd_horizon = horizon
    m.dt_hours = pyo.Param(m.T, initialize=horizon.duration_h)
    m.effective_max = pyo.Expression(m.G, m.T, rule=lambda _, g, t:
        m.online_max[g, t] - av[g]['up_mw'][t])
    m.effective_min = pyo.Expression(m.G, m.T, rule=lambda _, g, t:
        m.online_min[g, t] + av[g]['down_mw'][t])
    m.c_agc_upper = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] <= m.effective_max[g, t])
    m.c_agc_lower = pyo.Constraint(m.G, m.T, rule=lambda _, g, t: m.power[g, t] >= m.effective_min[g, t])

    # The October rule uses translated two-sided bands, not separately enlarged
    # upper/lower bounds. Retain distinct nonnegative positive/negative slacks.
    lines, sections = m._energy_inputs['lines'], m._energy_inputs['sections']
    m.c_line_upper.deactivate(); m.c_line_lower.deactivate()
    m.c_section_upper.deactivate(); m.c_section_lower.deactivate()
    m.c_october_line_band = pyo.Constraint(m.L, m.T, rule=lambda _, l, t:
        (lines[l]['lower_mw'][t], m.flow[l, t] - m.line_over[l, t] + m.line_under[l, t], lines[l]['upper_mw'][t]))
    m.c_october_section_band = pyo.Constraint(m.S, m.T, rule=lambda _, s, t:
        (sections[s]['lower_mw'][t], m.section_flow[s, t] - m.section_over[s, t] + m.section_under[s, t], sections[s]['upper_mw'][t]))

    storage_ports = None
    if storage_spec is not None:
        from .gd_october_storage import attach_gd_october_storage
        storage_ports = attach_gd_october_storage(m, storage_spec)
        _keys(storage_ports, {'injection_mw', 'cost_expr', 'positive_capacity_mw',
             'negative_minimum_mw', 'primary_mw', 'contingency_mw', 'reserve_basis',
             'audit'}, 'storage_ports')
        if storage_ports['reserve_basis'] != CAPACITY_BASIS:
            raise EnergyInputError('storage capacity ports must contain explicit qualified capacity only, with no nonparticipant credit')
        _keys(storage_ports['injection_mw'], {(b, t) for b in m.B for t in m.T}, 'storage injection')
        for key in ('positive_capacity_mw', 'negative_minimum_mw', 'primary_mw', 'contingency_mw'):
            _keys(storage_ports[key], PERIODS, 'storage.' + key)
        for b in m.B:
            for t in m.T:
                m.other_injection[b, t].set_value(storage_ports['injection_mw'][b, t])
        m.other_cost.set_value(storage_ports['cost_expr'])

    storage_accounting = None
    if storage_capacity_accounting is not None:
        if storage_ports is None:
            raise EnergyInputError("storage capacity accounting requires explicit storage")
        from .gd_october_storage_capacity_v3 import attach_baseline
        storage_accounting = attach_baseline(m, storage_capacity_accounting, storage_spec)

    def storage_baseline(t):
        return m.storage_absolute_baseline[t] if storage_accounting is not None else 0.

    def storage(key, t):
        return storage_ports[key][t] if storage_ports is not None else 0.

    m.primary_thermal = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals, initialize=0)
    m.contingency_thermal = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals, initialize=0)
    for g in m.G:
        for t in m.T:
            if not commitment[g, t] or not units[g]['primary_eligible'][t]:
                m.primary_thermal[g, t].fix(0.)
            if not commitment[g, t] or not units[g]['contingency_eligible'][t]:
                m.contingency_thermal[g, t].fix(0.)
    m.c_primary_headroom = pyo.Constraint(m.G, m.T, rule=lambda _, g, t:
        m.primary_thermal[g, t] + m.power[g, t] <= m.effective_max[g, t])
    # Coefficient cap retains physical online Pmax from §3.4.3.4; AGC is removed
    # from usable headroom. A separate operational interpretation is recorded.
    m.c_primary_fraction = pyo.Constraint(m.G, m.T, rule=lambda _, g, t:
        m.primary_thermal[g, t] <= units[g]['primary_fraction'][t] * m.online_max[g, t])
    m.c_contingency_headroom = pyo.Constraint(m.G, m.T, rule=lambda _, g, t:
        m.contingency_thermal[g, t] + m.power[g, t] <= m.effective_max[g, t])
    m.c_contingency_ramp = pyo.Constraint(m.G, m.T, rule=lambda _, g, t:
        m.contingency_thermal[g, t] <= units[g]['ramp_10min_mw'][t] * commitment[g, t])
    m.contingency_shortfall = pyo.Var(m.T, domain=pyo.NonNegativeReals, initialize=0)
    if slack['policy'] == 'hard':
        for t in m.T:
            m.contingency_shortfall[t].fix(0.)
    m.reserve_physical_tie = pyo.Expression(m.T, rule=lambda _, t:
        sum(m.physical_tie[b, t] for b in m.B))
    m.total_demand = pyo.Expression(m.T, rule=lambda _, t: sum(m.demand_mw[b, t] for b in m.B))
    def fixed_renewable(t):
        return (sum(fixed_capacity['renewable_fixed_injection_mw'][b][t] for b in m.B)
                if fixed_capacity is not None else 0.)

    m.positive_capacity = pyo.Expression(m.T, rule=lambda _, t:
        sum(m.effective_max[g, t] for g in m.G) + storage('positive_capacity_mw', t) + m.reserve_physical_tie[t]
        + sum(renewable_capacity[b]['positive_mw'][t] for b in m.B) + fixed_renewable(t) + storage_baseline(t))
    m.negative_minimum = pyo.Expression(m.T, rule=lambda _, t:
        sum(m.effective_min[g, t] for g in m.G) + storage('negative_minimum_mw', t) + m.reserve_physical_tie[t]
        + sum(renewable_capacity[b]['negative_mw'][t] for b in m.B) + fixed_renewable(t) + storage_baseline(t)
        - sum(d['eligible'][t] * d['capacity_mw'][t] for d in load_side.values()))
    m.c_positive_reserve = pyo.Constraint(m.T, rule=lambda _, t:
        m.positive_capacity[t] - req['positive_network_deduction_mw'][t] >= m.total_demand[t] + req['positive_mw'][t])
    m.c_negative_reserve = pyo.Constraint(m.T, rule=lambda _, t:
        m.negative_minimum[t] <= req['negative_demand_mw'][t] - req['negative_mw'][t])
    m.c_primary_reserve = pyo.Constraint(m.T, rule=lambda _, t:
        (req['primary_mw'][t], sum(m.primary_thermal[g, t] for g in m.G) + storage('primary_mw', t) + 0*m.total_demand[t], None))
    m.c_contingency_reserve = pyo.Constraint(m.T, rule=lambda _, t:
        sum(m.contingency_thermal[g, t] for g in m.G) + storage('contingency_mw', t)
        + m.contingency_shortfall[t] >= req['contingency_mw'][t] + req['contingency_deduction_mw'][t])
    m.contingency_penalty_cost = pyo.Expression(expr=0. if slack['policy'] == 'hard' else
        sum(horizon.duration_h[t] * slack['penalty_yuan_per_mw_h'] * m.contingency_shortfall[t] for t in m.T))
    m.total_cost.set_value(m.energy_bid_cost + m.renewable_curtailment_cost
        + m.network_penalty_cost + m.balance_penalty_cost + m.other_cost + m.contingency_penalty_cost)
    # Old standalone helper metadata is not an October certification.
    m._energy_scope['october_adapter'] = True
    from .gd_october_thermal import is_research_book
    research_thermal = thermal_trajectories is not None and is_research_book(thermal_trajectories['book'])
    m._energy_scope['transition_model'] = ('stable_fixed_commitment_only' if trajectory_context is None else
        'research_assumption_fixed_thermal_curves' if research_thermal else 'externally_confirmed_fixed_thermal_curves')
    if trajectory_context is not None:
        m._energy_scope['initial_state_kind']='explicit_stable_or_research_trajectory' if research_thermal else 'explicit_stable_or_confirmed_trajectory'
        m._energy_scope['startup_cost_model']='external_fixed_trajectory_cost_and_stable_bid_only'
    m._energy_scope['network_model'] = 'lossless_dc_angles_with_translated_bands'
    m._gd_october_inputs = {'reserves': deepcopy(reserves), 'agc': deepcopy(agc),
        'implementation_sources': sources, 'commitment': dict(commitment)}
    if thermal_trajectories is not None:
        m._gd_october_inputs['thermal_trajectories'] = deepcopy(thermal_trajectories)
    m._gd_october_scope = {'rule_version': RULE_VERSION, 'rule_sha256': RULE_SHA256,
        'periods': 24, 'period_minutes': 5, 'cost_unit': 'yuan',
        'power_unit': 'MW', 'ramp_input_unit': 'MW/hour',
        'thermal_minimum_residence_policy': None if trajectory_context is None else 'shutdown_curve_start_conservative',
        'thermal_curve_ramp_unit': None if trajectory_context is None else ('MW/minute_research_assumption' if research_thermal else 'MW/minute_external_confirmed_condition'),
        'thermal_curve_cost_is_all_in_startup_cost': False,
        'research_thermal_assumptions': research_thermal, 'operational_source_verified': False,
        'thermal_transitions_supported': trajectory_context is not None, 'stable_effective_commitment_only': trajectory_context is None,
        'thermal_primary_representation': 'existence_equivalent_hypograph_not_award',
        'primary_coefficient_basis': 'physical_online_max_headroom_net_of_agc',
        'sharing_policy': RESERVE_SHARING, 'reserve_sources': reserve_sources,
        'negative_demand_is_independent_input': True, 'storage_capacity_basis': CAPACITY_BASIS,
        'storage': storage_ports['audit'] if storage_ports is not None else 'explicitly_absent',
        'unapproved_forecast_agc_enabled': False, 'cross_province_trade_optimized': False,
        'physical_tie_preserved': True, 'conventional_hydro': False,
        'renewable_scope': renewable_mode, 'renewable_dispatch_profile': deepcopy(renewable_dispatch_profile),
        'nonparticipant_reserve_baseline_credit': False,
        'renewable_capacity': deepcopy(renewable_capacity),
        'nodal_load_slack_distribution_is_engineering_interpretation': True,
        'dc_ptdf_equivalence_with_nonzero_load_slack_verified': False,
        'load_side_delivery_verified': False,
        'reserve_response_delivery_verified': False,
        'unsupported': (['unconfirmed_thermal_startup_shutdown_trajectories'] if trajectory_context is not None else ['thermal_startup_shutdown_trajectories']) + ['priced_renewable_guarantee_factor_policy',
                        'unit_group_limits', 'security_contingency_constraints', 'network_losses',
                        'official_5_to_15_price_mapping', 'official_price_policy'],
        'dispatch_penalties_are_officially_verified': False,
        'execution_candidates': [1, 2, 3], 'execution_confirmed': False,
        'solver_called': False, 'numerical_optimality_verified': False,
        'whole_day_feasibility_verified': False, 'official_algorithm_certified': False}
    if fixed_capacity is not None:
        m._gd_october_scope['capacity_accounting'] = deepcopy(fixed_capacity)
        m._gd_october_scope['nonparticipant_reserve_baseline_credit'] = True
        m._gd_october_scope['fixed_energy_credit_is_incremental_reserve'] = False
        m._gd_october_scope['renewable_reserve_qualification_asserted'] = False
    if storage_accounting is not None:
        m._gd_october_inputs['storage_capacity_accounting'] = deepcopy(storage_accounting)
        m._gd_october_scope['storage_capacity_accounting'] = deepcopy(storage_accounting)
        m._gd_october_scope['nonparticipant_reserve_baseline_credit'] = True
        m._gd_october_scope['storage_baseline_is_incremental_reserve'] = False
        m._gd_october_scope['storage_source_bytes_verified'] = False
    economic_complete = storage_ports is None or storage_ports['audit'].get('economic_objective_complete') is True
    m._gd_october_scope['economic_objective_complete'] = economic_complete
    m._gd_october_scope['economic_parameters_missing'] = ([] if economic_complete else
        deepcopy(storage_ports['audit'].get('missing_economic_parameters', ['storage_objective_profile'])))
    assert_linear_energy_model(m, require_fixed_commitment=True, require_lp=True)
    if not economic_complete:
        # Zero placeholder in a detached expression is not an economic choice.
        # The model has physical constraints only, not an active cost objective.
        m.objective.deactivate()
    m._gd_october_signature = _mathematical_signature(m)
    return m


def _mathematical_signature(model):
    def number(x):
        return None if x is None else float(pyo.value(x))

    def linear(expr):
        repn = generate_standard_repn(expr, compute_values=True)
        if not repn.is_linear():
            raise EnergyInputError('nonlinear replacement of October mathematics')
        return (float(repn.constant or 0.), sorted((v.name, float(c))
                for v, c in zip(repn.linear_vars, repn.linear_coefs)))

    payload = {
        'root_active': model.active,
        'blocks': [(b.name, b.active) for b in model.component_data_objects(pyo.Block, active=None)],
        'sets': [(s.name, [str(v) for v in s]) for s in model.component_objects(pyo.Set, active=None)],
        'variables': [(v.name, str(v.domain), number(v.lb), number(v.ub), v.fixed,
                       number(v) if v.fixed else None)
                      for v in model.component_data_objects(pyo.Var, active=None)],
        'parameters': [(p.name, str(index), number(p[index]))
                       for p in model.component_objects(pyo.Param, active=None) for index in p],
        'rows': [(c.name, c.active, number(c.lower), linear(c.body), number(c.upper))
                 for c in model.component_data_objects(pyo.Constraint, active=None)],
        'objectives': [(o.name, o.active, int(o.sense), linear(o.expr))
                       for o in model.component_data_objects(pyo.Objective, active=None)],
        'economic_objective_complete': model._gd_october_scope['economic_objective_complete'],
        'source_contract_repr': repr(model._gd_october_inputs),
        'scope_repr': repr(model._gd_october_scope),
        'energy_source_inputs_repr': repr(model._energy_inputs),
    }
    # allow_nan=False also rejects nonfinite replacement coefficients/parameters.
    try:
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise EnergyInputError('nonfinite or malformed October model signature') from exc
    return sha256(encoded).hexdigest()


def assert_october_integrity(model):
    """Reject structural/parameter edits; assignment of free variables is allowed."""
    expected = getattr(model, '_gd_october_signature', None)
    if not isinstance(expected, str) or _mathematical_signature(model) != expected:
        raise EnergyInputError('October model integrity changed: constraints, parameters, fixed state or objective were altered')
    return {'model_integrity_verified': True, 'input_authenticity_verified': False,
            'solver_called': False}


def require_october_economic_objective(model):
    """Mandatory gate for any downstream economic solve/export/price adapter."""
    if not hasattr(model, '_gd_october_scope') or not model._gd_october_scope.get('economic_objective_complete'):
        raise EnergyInputError('economic objective incomplete: explicit operating penalty profile required')
    if not model.objective.active:
        raise EnergyInputError('economic objective is inactive')
    assert_october_integrity(model)
    return {'economic_objective_complete': True, 'official_parameters_certified': False,
            'solver_called': False}


def audit_october_structure(model):
    """Finite linear row audit, also for explicit physical-constraints-only mode."""
    if not hasattr(model, '_gd_october_scope'):
        raise EnergyInputError('an October SCED model is required')
    assert_october_integrity(model)
    for (g, t), expected in model._gd_october_inputs['commitment'].items():
        previous = model._energy_inputs['thermal'][g]['initial_online'] if t==1 else model._gd_october_inputs['commitment'][g,t-1]
        if (pyo.value(model.online[g,t], exception=False)!=expected or
            pyo.value(model.start[g,t], exception=False)!=max(0,expected-previous) or
            pyo.value(model.stop[g,t], exception=False)!=max(0,previous-expected)):
            raise EnergyInputError('effective commitment has been altered')
    if model._gd_october_scope['economic_objective_complete']:
        return assert_linear_energy_model(model, require_fixed_commitment=True, require_lp=True)
    if any(True for _ in model.component_data_objects(pyo.Objective, active=True)):
        raise EnergyInputError('incomplete objective must remain inactive')
    for name in ('online', 'start', 'stop'):
        if any(not v.fixed for v in getattr(model, name).values()):
            raise EnergyInputError('thermal combination must remain fixed')
    for v in model.component_data_objects(pyo.Var, active=True):
        if v.is_integer() and not v.fixed:
            raise EnergyInputError('unfixed integer in continuous SCED subset')
        if any(x is not None and not math.isfinite(float(pyo.value(x))) for x in (v.lb, v.ub)):
            raise EnergyInputError('nonfinite variable bound')
    rows = list(model.component_data_objects(pyo.Constraint, active=True))
    for c in rows:
        r = generate_standard_repn(c.body, compute_values=True)
        if not r.is_linear() or not math.isfinite(float(r.constant or 0)) or any(not math.isfinite(float(x)) for x in r.linear_coefs):
            raise EnergyInputError('nonfinite or nonlinear physical constraint')
        if any(x is not None and not math.isfinite(float(pyo.value(x))) for x in (c.lower, c.upper)):
            raise EnergyInputError('nonfinite constraint bound')
    return {'linear': True, 'active_constraints': len(rows), 'active_objectives': 0,
            'economic_objective_complete': False, 'solver_called': False, 'feasibility_checked': False}


def inspect_october_assignment(model, *, tolerance=1e-7):
    """Evaluate an assigned witness, without claiming solve/optimality/execution.

    This checks ALL active rows and variable bounds; unset/nonfinite values fail.
    Caller-supplied values are not promoted to solver or telemetry evidence.
    """
    tolerance = _num(tolerance, 'assignment tolerance', 0)
    if not hasattr(model, '_gd_october_scope'):
        raise EnergyInputError('an October SCED model is required')
    audit_october_structure(model)
    failures = []
    for v in model.component_data_objects(pyo.Var, active=True):
        x = pyo.value(v, exception=False)
        if x is None or not math.isfinite(float(x)):
            failures.append(v.name + ': missing/nonfinite value'); continue
        if (v.lb is not None and x < pyo.value(v.lb) - tolerance) or (v.ub is not None and x > pyo.value(v.ub) + tolerance):
            failures.append(v.name + ': outside variable bounds')
    for c in model.component_data_objects(pyo.Constraint, active=True):
        x = pyo.value(c.body, exception=False)
        if x is None or not math.isfinite(float(x)):
            failures.append(c.name + ': missing/nonfinite body'); continue
        if (c.lower is not None and x < pyo.value(c.lower) - tolerance) or (c.upper is not None and x > pyo.value(c.upper) + tolerance):
            failures.append(c.name + ': constraint violation')
    return {'assignment_satisfies_model': not failures, 'failures': failures,
            'absolute_tolerance': tolerance, 'solver_called': False,
            'optimality_proven': False, 'execution_confirmed': False,
            'whole_day_feasibility_verified': False, 'official_algorithm_certified': False}
