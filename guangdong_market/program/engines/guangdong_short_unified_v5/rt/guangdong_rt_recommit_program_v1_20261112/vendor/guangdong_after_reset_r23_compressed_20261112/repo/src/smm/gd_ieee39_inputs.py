"""Read-only IEEE39 data migration, never a solver or a legacy market runner.

A frozen snapshot keeps the original curves and evaluation truth separate from
DA model inputs. Missing new-rule/research parameters produce a not-runnable
report, never fabricated zeros. This first adapter supports only research DA96.
See rebuild docs/16_IEEE39输入迁移方案.md for the four migration categories.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import math
from numbers import Real
from zoneinfo import ZoneInfo

from .gd_horizon import EnergyHorizon
from .gd_piecewise_bids import PiecewiseEnergyBidCurve, ResourceEnergyBid


class IEEE39InputError(ValueError):
    """Malformed, ambiguous, unsupported, or incompletely sourced input."""


_GROUPS = ('thermal', 'renewable', 'storage', 'pumped', 'tie', 'hydro')
_PROVENANCE = {'config', 'network', 'profiles', 'bids', 'initial_state'}
_SOURCE_KINDS = {'research_assumption', 'uploaded_unverified', 'frozen_research_archive',
                 'declared_research_initial', 'observed_actual', 'executed_simulation',
                 'offline_future_truth_conditioned'}
_THERMAL_POLICY = {'startup_output_limit_mw', 'shutdown_output_limit_mw', 'shutdown_yuan',
                   'max_starts', 'max_stops', 'status', 'fixed_power_mw',
                   'transition_model', 'terminal_policy', 'initial_state_kind'}
_ENERGY_POLICY = {'balance_policy', 'shortfall_cap_policy', 'penalties_yuan_per_mwh',
                  'renewable_curtailment_penalty_yuan_per_mwh'}
_BATTERY_POLICY = {'cycle_limit', 'cycle_throughput_used_mwh', 'final_energy_bounds_mwh',
                   'direction_policy', 'initial_hour_direction', 'anchor_policy'}
_UNIT_POLICY = {'initial_mode_elapsed_h', 'min_generate_h', 'min_pump_h', 'min_off_h',
                'remaining_start_stop_events', 'pump_available', 'generation_available'}
_PLANT_POLICY = {'upper_final_bounds_m3', 'lower_final_bounds_m3', 'max_generating_units',
                 'max_pumping_units', 'min_online_units', 'max_online_units',
                 'max_generation_increase', 'max_pumping_increase'}
_STORAGE_POLICY = {'cycle_periods', 'scuc_aggregation_policy', 'rt_start_stop_budget_policy',
                   'terminal_dwell_policy', 'reserve_capacity_mapping'}
_POLICY_NAMES = ('thermal_policy', 'energy_policy', 'battery_policy', 'pumped_unit_policy',
                 'pumped_plant_policy', 'storage_policy')
_PHYSICAL_FIELDS = {
    'thermal': {'id','bus','pmin','pmax','ramp','ut','dt','init'},
    'renewable': {'id','bus','cap','type'},
    'storage': {'id','bus','pmax','ecap','eta_c','eta_d','soc_min','soc_max','soc0'},
    'pumped': {'id','bus','n_units','unit_gen_min','unit_gen_max','unit_pump','variable_speed',
               'e_gen','e_pump','v_total','vup_min','vup_max','vdn_min','vdn_max','vup0','init_mode'},
    'tie': {'id','bus','pmax'}, 'hydro': set(),
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise IEEE39InputError(f'{name}: explicit nonempty text required')
    return value


def _num(value, name, minimum=0., *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise IEEE39InputError(f'{name}: finite numeric value required')
    value = float(value)
    if (minimum is not None and value < minimum) or (positive and value <= 0):
        raise IEEE39InputError(f'{name}: invalid sign')
    return value


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise IEEE39InputError(f'{name}: integer >= {minimum} required')
    return value


def _keys(value, keys, name):
    keys = tuple(keys)
    def identity(key):
        return (type(key), tuple(identity(x) for x in key) if isinstance(key, tuple) else key)
    if not isinstance(value, dict) or {identity(k) for k in value} != {identity(k) for k in keys}:
        raise IEEE39InputError(f'{name}: exact fields/IDs required: {sorted(map(str, keys))}')


def _source(value, name):
    _keys(value, {'kind', 'source'} | ({'sha256'} if isinstance(value, dict) and 'sha256' in value else set()), name)
    if value['kind'] not in _SOURCE_KINDS:
        raise IEEE39InputError(f'{name}: explicit research/unverified/observed source kind required')
    _text(value['source'], name+'.source')
    if 'sha256' in value:
        digest = value['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise IEEE39InputError(f'{name}: lowercase SHA256 required')
    return f"{value['kind']}: {value['source']}"


def _plain(value):
    if isinstance(value, dict):
        if len({str(k) for k in value}) != len(value):
            raise IEEE39InputError('snapshot contains colliding string-normalized keys')
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, 'item'):
        return _plain(value.item())
    return value


def _json(value):
    try:
        return json.dumps(_plain(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (ValueError, TypeError) as exc:
        raise IEEE39InputError('snapshot must contain finite JSON-compatible data') from exc


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _semantic(value):
    """Sources identify provenance, not a second physical scenario."""
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items() if k not in {
            'source', 'sources', 'quantity_source', 'price_source', 'availability_source',
            'mapping_source', 'time_mapping_source'}}
    if isinstance(value, (tuple, list)):
        return [_semantic(v) for v in value]
    return value


@dataclass(frozen=True)
class FrozenIEEE39Inputs:
    """Immutable canonical bytes; payload() always returns a detached copy."""
    canonical_payload: str
    sha256: str

    def __post_init__(self):
        if not isinstance(self.canonical_payload, str) or hashlib.sha256(self.canonical_payload.encode()).hexdigest() != self.sha256:
            raise IEEE39InputError('frozen snapshot hash mismatch')
        try:
            data = json.loads(self.canonical_payload)
        except (ValueError, TypeError) as exc:
            raise IEEE39InputError('invalid canonical snapshot') from exc
        if _json(data) != self.canonical_payload:
            raise IEEE39InputError('snapshot must be canonical JSON')

    def payload(self):
        return json.loads(self.canonical_payload)


@dataclass(frozen=True)
class IEEE39MappingOptions:
    horizon: EnergyHorizon
    dc_base_mva: float
    dc_base_source: str
    delivery_start: datetime
    information_cutoff: datetime
    thermal_policy: dict | None = None
    energy_policy: dict | None = None
    battery_policy: dict | None = None
    pumped_unit_policy: dict | None = None
    pumped_plant_policy: dict | None = None
    storage_policy: dict | None = None
    # Every supplied policy family needs its own explicit source kind and text.
    sources: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IEEE39MigrationReport:
    ready_for_component_build: bool
    runnable: bool
    required_inputs: tuple[str, ...]
    unsupported_capabilities: tuple[str, ...]
    preserved_physics: tuple[str, ...]
    replaced_legacy_policies: tuple[str, ...]
    selected_research_constraints: dict
    hashes: dict
    audit: dict


@dataclass(frozen=True)
class IEEE39ReferenceInputs:
    core_inputs: dict | None
    storage_inputs: dict | None
    piecewise_offer_inputs: dict
    physics: dict
    # Deliberately not part of core_inputs; never passed to a solver by this module.
    evaluation_actual: dict
    report: IEEE39MigrationReport


def _network_payload(network):
    if hasattr(network, 'branches') and hasattr(network, 'load_share'):
        return dict(buses=list(network.buses), branches=network.branches.to_dict(orient='records'),
                    load_share={str(k): float(v) for k, v in network.load_share.items()},
                    base_load_mw=float(network.base_load_mw), ref_bus=network.ref_bus,
                    rate_basis='already_scaled')
    if not isinstance(network, dict):
        raise IEEE39InputError('Network object or explicit network mapping required')
    result = deepcopy(network)
    if hasattr(result.get('branches'), 'to_dict'):
        result['branches'] = result['branches'].to_dict(orient='records')
    return result


def freeze_ieee39_inputs(*, config, network, profiles, bids, initial_state, provenance):
    """Capture already-selected inputs; never load, regenerate, fill, or persist.

    Mapping network contract: buses, branches, load_share, base_load_mw, ref_bus,
    rate_basis='raw'|'already_scaled'. A legacy Network already contains scaling.
    Explicit initial_state group maps may be empty only for declared research
    configuration initials; observed/executed sources require complete groups.
    """
    if hasattr(config, 'base') and hasattr(config, 'units') and hasattr(config, 'mode'):
        config = dict(base=config.base, units=config.units, mode=config.mode)
    _keys(config, {'base', 'units', 'mode'}, 'config')
    if hasattr(profiles, 'columns'):
        if not profiles.columns.is_unique:
            raise IEEE39InputError('duplicate profile columns')
        profiles = {c: profiles[c].tolist() for c in profiles.columns}
    data = _plain(deepcopy(dict(config=config, network=_network_payload(network), profiles=profiles,
                               bids=bids, initial_state=initial_state, provenance=provenance)))
    _validate_snapshot(data)
    payload = _json(data)
    return FrozenIEEE39Inputs(payload, hashlib.sha256(payload.encode()).hexdigest())


def _curve_segments(segments, cap, direction, name):
    if not isinstance(segments, list) or not segments:
        raise IEEE39InputError(f'{name}: complete original price-quantity curve required')
    points, prices = [0.], []
    for record in segments:
        _keys(record, {'p_start', 'p_end', 'price'}, name)
        start = _num(record['p_start'], name+'.p_start')
        end = _num(record['p_end'], name+'.p_end')
        if start != points[-1] or end <= start:
            raise IEEE39InputError(f'{name}: contiguous increasing quantities from zero required')
        points.append(end); prices.append(_num(record['price'], name+'.price', None))
    if abs(points[-1]-cap) > 1e-8:
        raise IEEE39InputError(f'{name}: original curve must cover exactly rated capacity')
    if any((a > b if direction == 'supply' else a < b) for a, b in zip(prices, prices[1:])):
        raise IEEE39InputError(f'{name}: nonconvex source price order unsupported')
    return tuple(points), tuple(prices)


def _validate_snapshot(d):
    _keys(d, {'config', 'network', 'profiles', 'bids', 'initial_state', 'provenance'}, 'snapshot')
    _keys(d['config'], {'base', 'units', 'mode'}, 'config')
    _keys(d['provenance'], _PROVENANCE, 'provenance')
    for name, value in d['provenance'].items():
        _source(value, 'provenance.'+name)
    if d['provenance']['profiles']['kind'] not in {'offline_future_truth_conditioned', 'uploaded_unverified', 'frozen_research_archive'}:
        raise IEEE39InputError('offline profiles cannot be relabeled as causal issued forecasts')
    kind = d['provenance']['initial_state']['kind']
    if kind not in {'declared_research_initial', 'observed_actual', 'executed_simulation'}:
        raise IEEE39InputError('explicit initial-state kind required')
    base, units = d['config']['base'], d['config']['units']
    if not isinstance(base, dict) or not isinstance(units, dict) or not isinstance(d['config']['mode'], dict):
        raise IEEE39InputError('config base/units/mode dictionaries required')
    if base.get('periods') != 96 or isinstance(base.get('periods'), bool) or base.get('dt_h') != .25:
        raise IEEE39InputError('input profiles must explicitly be 96 quarter hours')
    if base.get('simulation_timezone') != 'Asia/Shanghai' or base.get('tie_line_mode') != 'supply':
        raise IEEE39InputError('explicit Shanghai clock and fixed physical tie supply required')
    if units.get('hydro') != []:
        raise IEEE39InputError('conventional hydro must explicitly be absent')
    inventory = {}
    for group in _GROUPS:
        rows = units.get(group)
        if not isinstance(rows, list):
            raise IEEE39InputError(f'units.{group}: explicit list required')
        for row in rows:
            if not isinstance(row, dict):
                raise IEEE39InputError('resource record must be a mapping')
            if not _PHYSICAL_FIELDS[group] <= set(row):
                raise IEEE39InputError(f'{group}: missing physical fields {sorted(_PHYSICAL_FIELDS[group]-set(row))}')
            uid = _text(row.get('id'), 'resource ID')
            if uid in inventory or row.get('enabled', True) is not True:
                raise IEEE39InputError('unique active-only resources required; disabled inputs must be resolved explicitly')
            inventory[uid] = group
    n = d['network']
    _keys(n, {'buses', 'branches', 'load_share', 'base_load_mw', 'ref_bus', 'rate_basis'}, 'network')
    buses = n['buses']
    if not isinstance(buses, list) or len(buses) != 39 or set(buses) != set(range(1, 40)) or any(type(b) is not int for b in buses):
        raise IEEE39InputError('explicit ordered IEEE39 buses 1..39 required')
    if type(n['ref_bus']) is not int or n['ref_bus'] != 31:
        raise IEEE39InputError('legacy IEEE39 reference bus must remain 31')
    _keys(n['load_share'], map(str, buses), 'load_share')
    shares = [_num(n['load_share'][str(b)], 'load_share') for b in buses]
    if not math.isclose(math.fsum(shares), 1., abs_tol=1e-12, rel_tol=0):
        raise IEEE39InputError('load shares must sum to one')
    _num(n['base_load_mw'], 'base_load_mw', positive=True)
    if n['rate_basis'] not in {'raw', 'already_scaled'}:
        raise IEEE39InputError('network rate_basis must explicitly identify prior scaling')
    if not isinstance(n['branches'], list) or not n['branches']:
        raise IEEE39InputError('network branches required')
    lines, neighbors = set(), {b:set() for b in buses}
    for row in n['branches']:
        if not isinstance(row, dict) or not {'branch_id','from_bus','to_bus','x_pu','rate_mw'} <= set(row):
            raise IEEE39InputError('complete branch fields required')
        lid = _text(row['branch_id'], 'branch ID')
        a, b = row['from_bus'], row['to_bus']
        if lid in lines or type(a) is not int or type(b) is not int or a not in neighbors or b not in neighbors or a == b:
            raise IEEE39InputError('invalid/duplicate network branch')
        lines.add(lid); neighbors[a].add(b); neighbors[b].add(a)
        _num(row['x_pu'], lid+'.x_pu', positive=True); _num(row['rate_mw'], lid+'.rate_mw', positive=True)
    reached, pending = set(), [31]
    while pending:
        b = pending.pop()
        if b not in reached:
            reached.add(b); pending.extend(neighbors[b]-reached)
    if reached != set(buses):
        raise IEEE39InputError('current adapter requires the connected IEEE39 topology; islands need explicit separate references')
    scales = base.get('line_scale', {})
    if not isinstance(scales, dict) or not set(scales) <= lines:
        raise IEEE39InputError('line_scale contains unknown branch')
    for value in scales.values(): _num(value, 'line scale', positive=True)
    _num(base.get('line_capacity_multiplier'), 'explicit global line multiplier', positive=True)
    for group in _GROUPS:
        for u in units[group]:
            if type(u.get('bus')) is not int or u['bus'] not in neighbors:
                raise IEEE39InputError(f"{u['id']}: unknown physical bus")
    profiles = d['profiles']
    if (not isinstance(profiles, dict) or profiles.get('period') != list(range(1,97))
            or any(type(t) is not int for t in profiles.get('period', []))):
        raise IEEE39InputError('profile period must cover ordered 1..96')
    if 'time' in profiles and profiles['time'] != [f'{t//4:02d}:{15*(t%4):02d}' for t in range(96)]:
        raise IEEE39InputError('profile time labels must match ordered Shanghai quarters')
    caps = {'load':None, **{u['id']:_num(u['cap'], u['id']+'.cap', positive=True) for u in units['renewable']}}
    for uid, cap in caps.items():
        for stage in ('actual','da','rt'):
            values = profiles.get(f'{uid}_{stage}')
            if not isinstance(values, list) or len(values) != 96:
                raise IEEE39InputError(f'missing complete profiles: {uid}_{stage}')
            for value in values:
                x = _num(value, f'{uid}_{stage}')
                if cap is not None and x > cap+1e-8:
                    raise IEEE39InputError(f'{uid}: forecast/actual exceeds rated capacity')
    for u in units['tie']:
        cap = _num(u['pmax'], 'tie capacity', positive=True)
        values = profiles.get(u['id']+'_sched')
        if not isinstance(values, list) or len(values) != 96 or any(_num(x,'tie schedule') > cap for x in values):
            raise IEEE39InputError('complete physical tie schedule within capacity required')
    expected = {u for u, group in inventory.items() if group != 'tie'}
    bids = d['bids']
    if not isinstance(bids, dict) or 'units' not in bids:
        raise IEEE39InputError('complete bids mapping required')
    _keys(bids['units'], expected, 'bids.units; automatic filling is prohibited')
    for group in ('thermal','renewable','storage','pumped'):
        for u in units[group]:
            bid = bids['units'][u['id']]
            if not isinstance(bid,dict): raise IEEE39InputError(u['id']+': bid record must be a mapping')
            if bid.get('mode') != 'price_quantity':
                raise IEEE39InputError('this adapter requires explicitly priced original curves; quantity-only needs a separate declared mapping')
            if group in ('thermal','renewable'):
                cap = _num(u['pmax'] if group == 'thermal' else u['cap'], u['id']+'.capacity', positive=True)
                _curve_segments(bid.get('segments'), cap, 'supply', u['id'])
            elif group == 'storage':
                cap = _num(u['pmax'], u['id']+'.pmax', positive=True)
                for key, direction in [('dis_segments','supply'),('ch_segments','demand')]:
                    _curve_segments(bid.get(key), cap, direction, u['id']+'.'+key)
            else:
                count = _integer(u.get('n_units'), u['id']+'.n_units', 1)
                if u.get('variable_speed') is not False:
                    raise IEEE39InputError('only explicitly fixed-speed pumped storage is supported')
                for key, capacity, direction in [('gen_segments','unit_gen_max','supply'),('pump_segments','unit_pump','demand')]:
                    _curve_segments(bid.get(key), count*_num(u[capacity],capacity,positive=True), direction, u['id']+'.'+key)
    initial = d['initial_state']
    _keys(initial, {'thermal','storage','pumped','observed_at'}, 'initial_state')
    for group in ('thermal','storage','pumped'):
        if not isinstance(initial[group], dict): raise IEEE39InputError('initial state groups must be mappings')
        ids = {u['id'] for u in units[group]}
        if initial[group] or kind != 'declared_research_initial':
            _keys(initial[group], ids, 'initial_state.'+group)
    if kind != 'declared_research_initial' and initial['observed_at'] is None:
        raise IEEE39InputError('observed/executed state needs an absolute observation timestamp')
    if initial['observed_at'] is not None:
        _aware(initial['observed_at'], 'observed_at')
    return d


def _aware(value, name):
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc: raise IEEE39InputError(f'{name}: invalid timestamp') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IEEE39InputError(f'{name}: timezone-aware timestamp required')
    return value


def _period_series(value, ids, name, *, minimum=0., integer=False):
    _keys(value, ids, name)
    if any(type(t) is not int for t in value): raise IEEE39InputError(f'{name}: integer period IDs required')
    return {t: (_integer(value[t], name) if integer else _num(value[t], name, minimum)) for t in ids}


def _bounds(value, name, *, allow_none=False):
    if value is None and allow_none: return None
    if not isinstance(value, (tuple,list)) or len(value) != 2:
        raise IEEE39InputError(f'{name}: explicit lower/upper pair required')
    lo, hi = (_num(x,name) for x in value)
    if lo > hi: raise IEEE39InputError(f'{name}: lower exceeds upper')
    return (lo,hi)


def _policies(options, inventories, ids):
    missing, policies, sources = [], {}, {}
    if not isinstance(options.sources, dict) or not set(options.sources) <= set(_POLICY_NAMES):
        raise IEEE39InputError('unknown policy source family')
    sets = dict(thermal_policy=_THERMAL_POLICY, energy_policy=_ENERGY_POLICY,
                battery_policy=_BATTERY_POLICY, pumped_unit_policy=_UNIT_POLICY,
                pumped_plant_policy=_PLANT_POLICY, storage_policy=_STORAGE_POLICY)
    for name in _POLICY_NAMES:
        raw = getattr(options,name)
        inventory = inventories.get(name)
        if inventory == set() and raw is None:
            policies[name] = {}; continue
        if raw is None:
            missing.append(name); continue
        if name not in options.sources:
            raise IEEE39InputError(name+': explicit parameter source required')
        sources[name] = _source(options.sources[name], name+'.source')
        if options.sources[name]['kind'] not in {'research_assumption','frozen_research_archive','uploaded_unverified'}:
            raise IEEE39InputError(name+': these unverified policy values must retain a research/unverified label')
        if inventory is not None:
            _keys(raw, inventory, name)
            for uid, record in raw.items(): _keys(record,sets[name],name+'.'+uid)
        else: _keys(raw,sets[name],name)
        policies[name] = deepcopy(raw)
    return policies, sources, missing


def _source_record(record, physics_source, policy_source, policy_keys):
    record['sources'] = {k: policy_source if k in policy_keys else physics_source for k in record if k != 'units'}
    for key in record:
        if key.endswith('_bid_yuan_per_mwh'):
            record['sources'][key] = ('Zero scalar placeholder owned/replaced by the original full piecewise '
                                      'curve; not a zero-price submission. '+physics_source)
    return record


def map_ieee39_reference_inputs(snapshot, *, options):
    """Map DA96 data without a solver. Incomplete policies stay not-runnable.

    The result is component-build input, not a wired workflow. ReserveSpec,
    ServiceInputs, pricing and stage orchestration are deliberately not inferred.
    """
    if not isinstance(snapshot,FrozenIEEE39Inputs) or not isinstance(options,IEEE39MappingOptions):
        raise IEEE39InputError('typed frozen snapshot and MappingOptions required')
    d = _validate_snapshot(snapshot.payload())
    h = options.horizon
    if not isinstance(h,EnergyHorizon): raise IEEE39InputError('explicit EnergyHorizon required')
    h.validate()
    if h.mode != 'day_ahead_research_96':
        raise IEEE39InputError('only research DA96 is implemented; DA98 requires a complete representative-data adapter and RT requires preparation/executed-boundary contracts')
    if h.ids != tuple(range(96)):
        raise IEEE39InputError('DA96 mapping explicitly uses IDs 0..95 for source periods 1..96')
    base_mva = _num(options.dc_base_mva,'dc_base_mva',positive=True)
    _text(options.dc_base_source,'dc_base_source')
    start, cutoff = _aware(options.delivery_start,'delivery_start'), _aware(options.information_cutoff,'information_cutoff')
    local = start.astimezone(ZoneInfo('Asia/Shanghai'))
    if any((local.hour,local.minute,local.second,local.microsecond)) or cutoff > start:
        raise IEEE39InputError('delivery starts at Shanghai midnight and cutoff is not later')
    initial = d['initial_state']
    if initial['observed_at'] is not None and _aware(initial['observed_at'],'observed_at') > cutoff:
        raise IEEE39InputError('future actual initial state is unavailable at the information cutoff')
    if d['provenance']['initial_state']['kind'] != 'declared_research_initial':
        raise IEEE39InputError('observed/executed initial states need an explicit observation-to-boundary mapping; this DA adapter only supports declared research initials')
    base, units, mode = (d['config'][k] for k in ('base','units','mode'))
    if mode.get('startup_cost_mode') != 'uniform_per_unit':
        raise IEEE39InputError('state-dependent startup bids cannot silently become one hot scalar')
    net, profiles, bids = d['network'], d['profiles'], d['bids']['units']
    ids, buses = h.ids, tuple(str(b) for b in net['buses'])
    series = lambda x: {t:deepcopy(x) for t in ids}
    sources = {k:_source(v,k) for k,v in d['provenance'].items()}
    src = sources['config']+'; '+sources['bids']+'; '+sources['initial_state']
    line_data = {}
    for row in net['branches']:
        rate = float(row['rate_mw'])
        if net['rate_basis'] == 'raw': rate *= base['line_scale'].get(row['branch_id'],1.)*base['line_capacity_multiplier']
        line_data[row['branch_id']] = dict(from_bus=str(row['from_bus']),to_bus=str(row['to_bus']),
            susceptance_mw_per_rad=base_mva/row['x_pu'],lower_mw=series(-rate),upper_mw=series(rate),
            source=sources['network']+'; '+options.dc_base_source)
    load = {(b,t):net['load_share'][b]*profiles['load_da'][t] for b in buses for t in ids}
    renewable = {(b,t):0. for b in buses for t in ids}; tie = dict(renewable)
    offers = dict(renewable_bids=[],battery_bids=[],pumped_plant_bids=[])
    ren_available = {}
    for u in units['renewable']:
        uid = u['id']; values = bids[uid].get('avail_pmax')
        if values is None: values = profiles[uid+'_da']
        if not isinstance(values,list) or len(values) != 96:
            raise IEEE39InputError(uid+': complete declared renewable availability required')
        values = [_num(x,uid+'.availability') for x in values]
        if any(x > u['cap'] for x in values): raise IEEE39InputError(uid+': declared availability exceeds nameplate')
        ren_available[uid] = {t:values[t] for t in ids}
        for t in ids: renewable[str(u['bus']),t] += values[t]
    for u in units['tie']:
        for t in ids: tie[str(u['bus']),t] += profiles[u['id']+'_sched'][t]
    physical_thermal, batteries, plants, unit_rows = {}, {}, {}, {}
    for u in units['thermal']:
        uid = u['id']; bid = bids[uid]; pmin = _num(u['pmin'],uid+'.pmin'); pmax = _num(u['pmax'],uid+'.pmax',positive=True)
        if pmin > pmax: raise IEEE39InputError(uid+': minimum exceeds maximum')
        init = initial['thermal'].get(uid,u.get('init'))
        _keys(init,{'online','p0','hours'},uid+'.initial')
        online = init['online']
        if type(online) is bool: online = int(online)
        if type(online) is not int or online not in (0,1): raise IEEE39InputError(uid+': initial online must be binary')
        p0 = _num(init['p0'],uid+'.p0')
        if (not online and p0 != 0) or (online and not pmin <= p0 <= pmax):
            raise IEEE39InputError(uid+': initial power conflicts with physical state')
        points, prices = _curve_segments(bid['segments'],pmax,'supply',uid)
        intercept = _num(bid.get('noload_cost'),uid+'.noload_cost')
        remaining = []
        for i,(a,b,price) in enumerate(zip(points,points[1:],prices)):
            intercept += max(0.,min(b,pmin)-a)*price
            if b > pmin:
                remaining.append(dict(key=f'segment_{i+1}',width_mw=b-max(a,pmin),price_yuan_per_mwh=series(price)))
        physical_thermal[uid] = dict(bus=str(u['bus']),bid_base_mw=pmin,base_bid_yuan_per_h=series(intercept),
            segments=tuple(remaining),min_mw=series(pmin),max_mw=series(pmax),
            ramp_up_mw_per_h=series(_num(u['ramp'],uid+'.ramp',positive=True)*60),
            ramp_down_mw_per_h=series(float(u['ramp'])*60),startup_yuan=series(_num(bid.get('startup_cost_yuan'),uid+'.startup_cost_yuan')),
            min_up_h=_num(u['ut'],uid+'.ut'),min_down_h=_num(u['dt'],uid+'.dt'),initial_online=online,
            initial_power_mw=p0,initial_state_age_h=_num(init['hours'],uid+'.initial hours'),source=src)
    for u in units['storage']:
        uid = u['id']; bid = bids[uid]; cap = _num(u['ecap'],uid+'.ecap',positive=True)
        eta_c,eta_d = (_num(u[k],uid+'.'+k,positive=True) for k in ('eta_c','eta_d'))
        if eta_c > 1 or eta_d > 1 or eta_c != eta_d:
            raise IEEE39InputError(uid+': symmetric physical efficiency <=1 required; product alone cannot preserve asymmetric dynamics')
        lo = _num(bid.get('soc_min',u['soc_min']),uid+'.soc_min'); hi = _num(bid.get('soc_max',u['soc_max']),uid+'.soc_max')
        physical_lo = _num(u['soc_min'],uid+'.physical_soc_min')
        physical_hi = _num(u['soc_max'],uid+'.physical_soc_max')
        if not 0 <= physical_lo <= lo <= hi <= physical_hi <= 1:
            raise IEEE39InputError(uid+': declared SOC limits conflict with physical bounds')
        e0 = _num(initial['storage'].get(uid,_num(u['soc0'],uid+'.soc0')*cap),uid+'.initial_energy')
        if not lo*cap <= e0 <= hi*cap: raise IEEE39InputError(uid+': initial energy outside limits; no clipping')
        if bid.get('self_schedule') is not None: raise IEEE39InputError(uid+': self-schedule needs a separate explicit boundary adapter')
        batteries[uid] = dict(bus=str(u['bus']),energy_capacity_mwh=cap,roundtrip_efficiency=eta_c*eta_d,initial_energy_mwh=e0,
            energy_min_mwh=series(lo*cap),energy_max_mwh=series(hi*cap),charge_min_mw=series(0.),charge_max_mw=series(float(u['pmax'])),
            discharge_min_mw=series(0.),discharge_max_mw=series(float(u['pmax'])),
            charge_bid_yuan_per_mwh=series(0.),discharge_bid_yuan_per_mwh=series(0.),da_anchor_mwh={},da_direction={})
    physical_ids = {u['id'] for group in _GROUPS for u in units[group]}
    for u in units['pumped']:
        pid = u['id']; n = u['n_units']; total = _num(u['v_total'],pid+'.v_total',positive=True)*10000
        initial_plant = initial['pumped'].get(pid)
        if initial_plant is not None: _keys(initial_plant,{'upper_m3','lower_m3','units'},pid+'.initial')
        upper = _num(initial_plant['upper_m3'] if initial_plant else _num(u['vup0'],pid+'.vup0')*10000,pid+'.upper_initial')
        lower = _num(initial_plant['lower_m3'] if initial_plant else total-upper,pid+'.lower_initial')
        if not math.isclose(upper+lower,total,rel_tol=0,abs_tol=1e-6): raise IEEE39InputError(pid+': initial reservoir total differs from frozen physical total')
        members = {}
        for j in range(1,n+1):
            uid = f'{pid}_U{j}'
            if uid in physical_ids or uid in unit_rows: raise IEEE39InputError('generated physical-unit ID collision')
            if initial_plant:
                _keys(initial_plant['units'],{f'{pid}_U{k}' for k in range(1,n+1)},pid+'.initial.units')
                st = initial_plant['units'][uid]; _keys(st,{'mode','generation_mw','elapsed_h'},uid+'.initial')
            else:
                if u.get('init_mode') not in {'idle','pump'}:
                    raise IEEE39InputError(pid+': generation initial needs explicit per-unit output and age')
                st = dict(mode='off' if u['init_mode']=='idle' else 'pump',generation_mw=0.,elapsed_h=None)
            if st['mode'] not in {'off','pump','generate'}: raise IEEE39InputError(uid+': unsupported initial mode')
            p0 = _num(st['generation_mw'],uid+'.initial_generation')
            if (st['mode']!='generate' and p0 != 0) or (st['mode']=='generate' and not u['unit_gen_min'] <= p0 <= u['unit_gen_max']):
                raise IEEE39InputError(uid+': invalid initial generation')
            rec = dict(bus=str(u['bus']),pump_power_mw=_num(u['unit_pump'],uid+'.pump',positive=True),
                generation_min_mw=series(_num(u['unit_gen_min'],uid+'.gen_min')),
                generation_max_mw=series(_num(u['unit_gen_max'],uid+'.gen_max',positive=True)),
                pump_m3_per_mwh=10000/_num(u['e_pump'],uid+'.e_pump',positive=True),
                generation_m3_per_mwh=10000/_num(u['e_gen'],uid+'.e_gen',positive=True),
                initial_mode=st['mode'],initial_generation_mw=p0,pump_bid_yuan_per_mwh=series(0.),generation_bid_yuan_per_mwh=series(0.))
            if rec['pump_m3_per_mwh'] > rec['generation_m3_per_mwh'] or u['unit_gen_min'] > u['unit_gen_max']:
                raise IEEE39InputError(uid+': invalid water efficiency or generation limits')
            if st['elapsed_h'] is not None: rec['initial_mode_elapsed_h'] = _num(st['elapsed_h'],uid+'.age')
            members[uid] = rec; unit_rows[uid] = rec
        if {r['initial_mode'] for r in members.values()} >= {'generate','pump'}:
            raise IEEE39InputError(pid+': initial same-plant mode conflict')
        p = dict(units=members,upper_initial_m3=upper,lower_initial_m3=lower,
            upper_min_m3=series(_num(u['vup_min'],pid+'.vup_min')*10000),upper_max_m3=series(_num(u['vup_max'],pid+'.vup_max')*10000),
            lower_min_m3=series(_num(u['vdn_min'],pid+'.vdn_min')*10000),lower_max_m3=series(_num(u['vdn_max'],pid+'.vdn_max')*10000),
            da_upper_m3={},da_lower_m3={},upper_band_m3={},lower_band_m3={},band_penalty_yuan_per_m3_step={})
        for side,value in [('upper',upper),('lower',lower)]:
            if not p[side+'_min_m3'][0] <= value <= p[side+'_max_m3'][0]: raise IEEE39InputError(pid+': reservoir initial outside bounds')
        plants[pid] = p
    inventories = dict(thermal_policy=set(physical_thermal),battery_policy=set(batteries),
                       pumped_unit_policy=set(unit_rows),pumped_plant_policy=set(plants))
    policies, policy_sources, missing = _policies(options,inventories,ids)
    thermal = deepcopy(physical_thermal); assembled_bat = deepcopy(batteries); assembled_ps = deepcopy(plants)
    if 'thermal_policy' in policies:
        for uid,p in policies['thermal_policy'].items():
            for key in ('startup_output_limit_mw','shutdown_output_limit_mw','shutdown_yuan'):
                p[key] = _period_series(p[key],ids,uid+'.'+key)
            for key in ('startup_output_limit_mw','shutdown_output_limit_mw'):
                if any(not thermal[uid]['bid_base_mw'] <= p[key][t] <= thermal[uid]['max_mw'][t] for t in ids): raise IEEE39InputError(uid+': transition limit outside stable subset')
            if p['transition_model'] != 'instant_with_output_limits' or p['terminal_policy'] != 'carry_out' or p['initial_state_kind'] != 'stable':
                raise IEEE39InputError('unsupported thermal transition/state/terminal policy')
            for key in ('max_starts','max_stops'):
                if p[key] is not None: _integer(p[key],uid+'.'+key)
            for key in ('status','fixed_power_mw'): _keys(p[key],ids,uid+'.'+key)
            for t in ids:
                if p['status'][t] not in {'free','on','off','unavailable'}: raise IEEE39InputError('invalid thermal status')
                fixed = p['fixed_power_mw'][t]
                if fixed is not None:
                    _num(fixed,'fixed power')
                    if fixed > thermal[uid]['max_mw'][t] or (fixed > 0 and (fixed < thermal[uid]['min_mw'][t] or p['status'][t] in {'off','unavailable'})) or (fixed == 0 and p['status'][t]=='on' and thermal[uid]['min_mw'][t]>0):
                        raise IEEE39InputError('thermal fixed output conflicts with physical/status limits')
            thermal[uid].update(p); thermal[uid]['source'] += '; '+policy_sources['thermal_policy']
    if 'battery_policy' in policies:
        for uid,p in policies['battery_policy'].items():
            r = assembled_bat[uid]; cap = r['energy_capacity_mwh']
            for key in ('cycle_limit','cycle_throughput_used_mwh'): p[key]=_num(p[key],uid+'.'+key)
            if p['cycle_throughput_used_mwh'] > 2*cap*p['cycle_limit']: raise IEEE39InputError('battery cycle budget already exceeded')
            p['final_energy_bounds_mwh'] = _bounds(p['final_energy_bounds_mwh'],uid+'.final',allow_none=True)
            target = bids[uid].get('soc_end')
            if target is not None and p['final_energy_bounds_mwh'] != (_num(target,'soc_end')*cap,)*2:
                raise IEEE39InputError('declared battery terminal target cannot be silently changed')
            if p['final_energy_bounds_mwh'] is not None and not r['energy_min_mwh'][95] <= p['final_energy_bounds_mwh'][0] <= p['final_energy_bounds_mwh'][1] <= r['energy_max_mwh'][95]: raise IEEE39InputError('battery terminal bounds outside physical limits')
            if p['direction_policy']!='hourly_only' or type(p['initial_hour_direction']) is not int or p['initial_hour_direction']!=0 or p['anchor_policy']!={'mode':'hard'}:
                raise IEEE39InputError('DA adapter requires explicit hourly_only, midnight direction=0 and hard anchor policy')
            r.update(p); _source_record(r,src,policy_sources['battery_policy'],_BATTERY_POLICY)
    if 'pumped_unit_policy' in policies:
        for pid,plant in assembled_ps.items():
            for uid,r in plant['units'].items():
                p = policies['pumped_unit_policy'][uid]
                for key in ('initial_mode_elapsed_h','min_generate_h','min_pump_h','min_off_h'): p[key]=_num(p[key],uid+'.'+key)
                if 'initial_mode_elapsed_h' in r and r['initial_mode_elapsed_h'] != p['initial_mode_elapsed_h']: raise IEEE39InputError('physical initial age cannot be overwritten by policy')
                _integer(p['remaining_start_stop_events'],'remaining_start_stop_events')
                for key in ('pump_available','generation_available'):
                    p[key] = _period_series(p[key],ids,uid+'.'+key,integer=True)
                    if any(x>1 for x in p[key].values()): raise IEEE39InputError('availability must be binary')
                r.update(p); _source_record(r,src,policy_sources['pumped_unit_policy'],_UNIT_POLICY)
    if 'pumped_plant_policy' in policies:
        for pid,p in policies['pumped_plant_policy'].items():
            r = assembled_ps[pid]; unit = next(u for u in units['pumped'] if u['id']==pid)
            for key in ('upper_final_bounds_m3','lower_final_bounds_m3'): p[key]=_bounds(p[key],pid+'.'+key)
            target = bids[pid].get('vup_end')
            if target is not None:
                target = _num(target,pid+'.vup_end')*10000; lower=unit['v_total']*10000-target
                if p['upper_final_bounds_m3'] != (target,target) or p['lower_final_bounds_m3'] != (lower,lower): raise IEEE39InputError('declared pumped terminal target cannot be silently changed')
            for side in ('upper','lower'):
                lo,hi=p[side+'_final_bounds_m3']
                if not r[side+'_min_m3'][95] <= lo <= hi <= r[side+'_max_m3'][95]: raise IEEE39InputError('pumped terminal bounds outside physical limits')
            for key in _PLANT_POLICY-{'upper_final_bounds_m3','lower_final_bounds_m3'}:
                p[key]=_period_series(p[key],ids,pid+'.'+key,integer=True)
                if any(x>len(r['units']) for x in p[key].values()): raise IEEE39InputError('plant count exceeds physical unit inventory')
            if any(p['min_online_units'][t]>p['max_online_units'][t] for t in ids): raise IEEE39InputError('plant minimum count exceeds maximum')
            r.update(p); _source_record(r,src,policy_sources['pumped_plant_policy'],_PLANT_POLICY)
    if 'energy_policy' in policies:
        p = policies['energy_policy']
        if p['balance_policy']!='hard' or p['shortfall_cap_policy'] not in {'unbounded_slack','load_capped'}:
            raise IEEE39InputError('DA reference requires hard balance and explicit shortfall-cap policy')
        _keys(p['penalties_yuan_per_mwh'],{'line','section','balance_shortfall','balance_surplus'},'energy penalties')
        for v in p['penalties_yuan_per_mwh'].values(): _num(v,'energy penalty',positive=True)
        _keys(p['renewable_curtailment_penalty_yuan_per_mwh'],renewable,'renewable curtailment penalty')
        for v in p['renewable_curtailment_penalty_yuan_per_mwh'].values(): _num(v,'curtailment penalty')
    core = None
    if all(k in policies for k in ('thermal_policy','energy_policy')):
        p = policies['energy_policy']
        note = policy_sources['energy_policy']
        core = dict(horizon=h,buses=buses,thermal=thermal,load_mw=load,renewable_mw=renewable,physical_tie_mw=tie,
            lines=line_data,sections={},reference_buses=('31',),renewable_mode='dispatchable_bus_aggregate',
            renewable_bid_yuan_per_mwh={k:0. for k in renewable},**deepcopy(p),
            source_notes=dict(thermal=src,transition=policy_sources.get('thermal_policy','no thermal resources'),network=sources['network']+'; '+options.dc_base_source,
                penalties=note,exogenous=sources['profiles']+'; DA columns only; actual kept outside model inputs',
                boundary_basis='frozen raw physical limits before AGC; service adjustment not performed by this data adapter',
                balance_policy=note,shortfall_cap_policy=note,
                renewable_policy='original per-resource convex curves preserved; zero scalar placeholder is owned/replaced by gd_piecewise_bids; no clipping or flattening'))
    if 'storage_policy' in policies:
        p=policies['storage_policy']; cycles=p['cycle_periods']
        if not isinstance(cycles,tuple) or cycles != tuple(t for t in ids if t in cycles) or len(set(cycles))!=len(cycles) or any(type(t) is not int for t in cycles): raise IEEE39InputError('explicit ordered unique cycle_periods required')
        if p['scuc_aggregation_policy'] not in {'single_unit','unit_disaggregated_research'} or (any(len(x['units'])>1 for x in plants.values()) and p['scuc_aggregation_policy']!='unit_disaggregated_research'):
            raise IEEE39InputError('multi-physical-unit plant requires explicit unit_disaggregated_research policy')
        if p['rt_start_stop_budget_policy']!='not_applicable' or p['terminal_dwell_policy']!='forbid_unfinished_new_state': raise IEEE39InputError('unsupported DA storage stage/dwell policy')
        _text(p['reserve_capacity_mapping'],'reserve_capacity_mapping')
        if p['scuc_aggregation_policy']=='unit_disaggregated_research' and 'pumped_unit_policy' in policies:
            for pid,plant in assembled_ps.items():
                members=list(plant['units'].values())
                for key in ('min_generate_h','min_pump_h','min_off_h'):
                    if any(unit[key]!=members[0][key] for unit in members):
                        raise IEEE39InputError(pid+': heterogeneous pumped dwell parameters are unsupported by the selected core')
    storage = None
    if all(k in policies for k in ('battery_policy','pumped_unit_policy','pumped_plant_policy','storage_policy')):
        p=policies['storage_policy']; cycles=p['cycle_periods']
        note=policy_sources['storage_policy']
        storage=dict(batteries=assembled_bat,pumped_plants=assembled_ps,clock_start_h=0.,anchor_periods=(),day_end_period=95,
            cycle_periods=cycles,rt_start_stop_budget_policy=p['rt_start_stop_budget_policy'],scuc_aggregation_policy=p['scuc_aggregation_policy'],
            source_notes={k:note for k in ('clock_start_h','anchor_periods','day_end_period','cycle_periods','terminal_dwell_policy','rt_start_stop_budget_policy','scuc_aggregation_policy')})
        storage['source_notes']['reserve_capacity_mapping']=p['reserve_capacity_mapping']+'; '+note
    def offer(uid,target,kind,key,capacity,available,cost_name):
        direction='demand' if kind in {'battery_charge','pumped_plant_pumping'} else 'supply'
        points,prices=_curve_segments(bids[uid][key],capacity,direction,uid+'.'+key)
        curve=PiecewiseEnergyBidCurve(points,{t:prices for t in ids},direction,'flat' if len(prices)==1 else 'piecewise','MW','yuan/MWh',sources['bids'],sources['bids'])
        return ResourceEnergyBid(uid+':'+kind,uid,target,kind,curve,available,sources['profiles'] if kind=='renewable' else src,
                                 'original resource/plant curve once; physical power inventory retained',cost_name)
    for u in units['renewable']:
        offers['renewable_bids'].append(offer(u['id'],str(u['bus']),'renewable','segments',u['cap'],ren_available[u['id']],'renewable_bid_cost'))
    for u in units['storage']:
        for kind,key in [('battery_charge','ch_segments'),('battery_discharge','dis_segments')]:
            offers['battery_bids'].append(offer(u['id'],u['id'],kind,key,u['pmax'],series(float(u['pmax'])),'gd_storage.battery_bid_cost'))
    for u in units['pumped']:
        for kind,key,power,maximum,available_key in [('pumped_plant_generation','gen_segments','unit_gen_max','max_generating_units','generation_available'),('pumped_plant_pumping','pump_segments','unit_pump','max_pumping_units','pump_available')]:
            available=series(u['n_units']*u[power])
            if 'pumped_unit_policy' in policies and 'pumped_plant_policy' in policies:
                r=assembled_ps[u['id']]
                available={t:u[power]*min(r[maximum][t],r['max_online_units'][t],sum(unit[available_key][t] for unit in r['units'].values())) for t in ids}
            offers['pumped_plant_bids'].append(offer(u['id'],u['id'],kind,key,u['n_units']*u[power],available,'gd_storage.pumped_bid_cost'))
    offers={k:tuple(v) for k,v in offers.items()}
    actual={key:list(values) for key,values in profiles.items() if key.endswith('_actual') and key[:-7] in {'load',*(u['id'] for u in units['renewable'])}}
    physics=dict(buses=buses,lines=line_data,load_mw=load,renewable_available_mw=ren_available,physical_tie_mw=tie,
        thermal=physical_thermal,batteries=batteries,pumped_plants=plants,
        physical_unit_to_plant={uid:pid for pid,p in plants.items() for uid in p['units']},
        original_pumped_research_costs={u['id']:{k:u[k] for k in ('start_cost_gen','start_cost_pump') if k in u} for u in units['pumped']})
    offered={k:[asdict(v) for v in values] for k,values in offers.items()}
    rt_reference={key:list(values) for key,values in profiles.items() if key.endswith('_rt') and key[:-3] in {'load',*(u['id'] for u in units['renewable'])}}
    active_physics={k:v for k,v in physics.items() if k!='original_pumped_research_costs'}
    da_semantic=dict(physics=_semantic(active_physics),offers=_semantic(offered),
                     horizon=_semantic(asdict(h)),selected_policies=policies)
    semantic=dict(da_inputs=da_semantic,actual=actual,offline_rt_reference=rt_reference)
    hashes=dict(snapshot=snapshot.sha256,scenario_content=_hash(semantic),network_limits=_hash(_semantic(line_data)),
        da_problem_inputs=_hash(da_semantic),actual_trajectory=_hash(actual),
        research_cost_ledger=_hash(physics['original_pumped_research_costs']),
        resource_capacity=_hash({g:[{k:v for k,v in u.items() if k in {'id','bus','pmin','pmax','cap','ecap','n_units','unit_gen_min','unit_gen_max','unit_pump'}} for u in units[g]] for g in _GROUPS}))
    required=tuple(missing)+('DA_SCUC_and_DA_SCED_ReserveSpec_with_independent_RU_RD_Rfirst_Rred',
        'ServiceInputs_with_explicit_AGC_prearrangement_and_publication_provenance',
        'SolveSettings_and_runner_with_piecewise_attachment_before_each_solve',
        'explicit_pricing_request_or_explicit_no_pricing_scope')
    report=IEEE39MigrationReport(core is not None and storage is not None,False,required,
        ('DA98_representative_inputs_not_implemented','RT_preparation_and_executed_boundary_adapter_not_implemented',
         'complete_workflow_and_final_settlement_not_assembled'),
        ('resource_nameplate_and_initial_known_state','effective_network_limits_once','original_piecewise_energy_curves','frozen_DA_forecast_and_physical_tie'),
        ('legacy_independent_reserve_auction','legacy_reserve_bids_and_payments','legacy_800_price_clipping',
         'legacy_PS_start_fees_not_in_new_energy_objective','legacy_hourly_PS_counts_not_inferred_as_dwell'),
        deepcopy(policies),hashes,dict(forecast_kind='offline_future_truth_conditioned_or_unverified_archive',
            forecast_source_kind=d['provenance']['profiles']['kind'],future_actual_in_core_inputs=False,
            original_source_profiles_unchanged=True,hydro_present=False,interregional_market=False,
            original_pumped_start_cost_ledger_only=True,production_equivalence_verified=False,
            network_rate_basis=net['rate_basis'],dc_base_mva=base_mva,initial_state_kind=d['provenance']['initial_state']['kind'],
            delivery_start=start.isoformat(),information_cutoff=cutoff.isoformat(),
            source_file_hashes={k:v.get('sha256') for k,v in d['provenance'].items()},
            external_source_hashes_are_caller_declarations=True,snapshot_content_integrity_checked=True,
            original_offers_require_piecewise_attachment=True,zero_scalar_costs_are_placeholders=True,
            policies_are_research_inputs=True,solver_called=False))
    return IEEE39ReferenceInputs(core,storage,offers,physics,actual,report)


def inspect_ieee39_inputs(snapshot, *, options):
    return map_ieee39_reference_inputs(snapshot,options=options).report


def fingerprint_ieee39_scenario(mapped):
    if not isinstance(mapped,IEEE39ReferenceInputs): raise IEEE39InputError('mapped IEEE39ReferenceInputs required')
    return dict(mapped.report.hashes)


def map_ieee39_rt_window(*args, **kwargs):
    raise IEEE39InputError('RT mapping is not implemented: explicit causal/declared forecast selection, actual-to-initial-boundary projection and pumped cold-start preparation contracts are required')
