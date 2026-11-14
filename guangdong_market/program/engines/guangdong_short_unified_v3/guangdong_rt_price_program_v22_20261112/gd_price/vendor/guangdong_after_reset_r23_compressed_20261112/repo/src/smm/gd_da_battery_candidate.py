"""R12 finite-path DA research SCUC, never an official DA98 or execution permit.

Free integers select predeclared thermal paths and physical battery modes. Network,
energy segments and physical assets are retained from energy_core. Instant
transition equations are explicitly replaced, not silently relaxed. No solver
is called by construction; accepted selections are minted only by solve_scuc.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from weakref import WeakKeyDictionary
from uuid import uuid4
from zoneinfo import ZoneInfo
import pyomo.environ as pyo
from .gd_energy_core import build_energy_core, EnergyInputError, assert_linear_energy_model
from .gd_horizon import EnergyHorizon, EnergyPeriod

DT = 1 / 12
_BUILT = WeakKeyDictionary()
_ACCEPTED = WeakKeyDictionary()
_ACCEPTED_SCED = WeakKeyDictionary()
_FAILED = WeakKeyDictionary()
_LIVE_BOUNDARIES = WeakKeyDictionary()
_ATTEMPTS = WeakKeyDictionary()


def require(ok, message):
    if not ok:
        raise EnergyInputError(message)


def number(x, name, minimum=0):
    require(type(x) in (int, float) and math.isfinite(x) and x >= minimum, name)
    return float(x)


def stamp(s):
    x = datetime.fromisoformat(s)
    require(x.utcoffset() is not None, 'timezone-aware timestamp required')
    return x


def digest(value):
    def clean(x):
        if isinstance(x, dict):
            return [[repr(k), clean(v)] for k, v in sorted(x.items(), key=lambda kv: repr(kv[0]))]
        if isinstance(x, (tuple, list)):
            return [clean(v) for v in x]
        if isinstance(x, bytes):
            return {'bytes_sha256': sha256(x).hexdigest()}
        if isinstance(x, EnergyHorizon):
            return x.audit()
        return x
    return sha256(json.dumps(clean(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_contract(c):
    require(set(c) == {'schema', 'experiment_id', 'boundary_kind', 'boundary_at',
        'day_start', 'cutoff', 'planned_publication_at', 'generated_at_utc',
        'sources', 'storage', 'cost_policy', 'terminal_policy'}, 'DA contract keys')
    require(c['schema'] in ('gd.research.da_candidate_battery.v1', 'gd.research.da_candidate_battery.diagnostic.v1') and c['experiment_id'].strip(), 'DA identity')
    cutoff, publication, boundary, day = map(stamp, (c['cutoff'], c['planned_publication_at'], c['boundary_at'], c['day_start']))
    stamp(c['generated_at_utc'])  # Actual wall clock is not simulated availability.
    require(cutoff <= publication <= boundary and (boundary <= day or c['schema'].endswith('.diagnostic.v1')), 'DA decision/publication/boundary chronology')
    require(c['boundary_kind'] in ('original_D00_research', 'synthetic_lead_in_research', 'prepared_D00_research', 'local_diagnostic_research'), 'explicit boundary identity')
    require((boundary == day) == (c['boundary_kind'] in ('original_D00_research','prepared_D00_research')) or (c['schema'].endswith('.diagnostic.v1') and c['boundary_kind']=='local_diagnostic_research'), 'explicit boundary must not move')
    require((c['boundary_kind']=='local_diagnostic_research') == c['schema'].endswith('.diagnostic.v1'), 'diagnostic boundary/schema separation')
    require(boundary.second == boundary.microsecond == 0 and boundary.minute % 5 == 0, 'five-minute boundary')
    require(c['cost_policy'] == 'stable_bid_plus_transition_hourly_plus_uniform_event', 'explicit cost policy')
    require(c['terminal_policy'] == 'complete_curves_carry_residence', 'explicit tail policy')
    require(set(c['storage']) == {'policy','inventory_sha256','battery_parameters_source_id','pumped_storage'}, 'battery storage contract keys')
    require(c['storage']['policy'] == 'battery_energy_only_zero_incremental_reserve', 'battery incremental reserve/AGC unsupported')
    ps=c['storage']['pumped_storage']
    require(set(ps)=={'policy','inventory'} and ps['policy']=='off_fixed_inventory_zero_credit' and bool(ps['inventory']), 'retained off pumped storage inventory required')
    require(bool(c['storage']['inventory_sha256']), 'retained storage inventory required')
    require(bool(c['sources']), 'explicit byte sources required')
    for key, s in c['sources'].items():
        require(set(s) == {'artifact', 'sha256', 'available_at', 'kind'}, 'source keys')
        require(type(s['artifact']) is bytes and sha256(s['artifact']).hexdigest() == s['sha256'], 'source bytes/hash mismatch')
        require(stamp(s['available_at']) <= cutoff, 'future DA source: ' + key)
        require(s['kind'] in ('retained_static', 'synthetic_exogenous_prior', 'synthetic_da_vintage', 'research_curve_assumption', 'research_boundary_assumption', 'research_storage_assumption'), 'unsupported or offline future truth source')
    return c


def research_horizon(count=288):
    require(type(count) is int and count >= 288 and count % 3 == 0, 'DA physical grid requires whole quarters and full delivery day')
    return EnergyHorizon(tuple(EnergyPeriod(t, DT, DT, 'delivery', True) for t in range(count)),
        'day_ahead_research_5min', 'R12 finite candidate-path research; not official 98 points')


def compile_path(d, spec, periods, contract):
    """Regenerate all phases from event templates; caller cannot supply phases.

    Templates carry five-minute (start, mean, end, hourly_cost) tuples. Event
    costs are taken from the original unit bid at the event time, not templates.
    """
    require(set(spec) == {'id', 'events'}, 'candidate path keys')
    require(isinstance(spec['id'], str) and spec['id'].strip(), 'candidate ID')
    cells = []
    online = d['initial_online']; age = d['initial_state_age_h']
    starts = stops = 0; active = {}; last_end = -1
    for event in spec['events']:
        require(set(event) == {'kind', 'at', 'steps', 'notice_h', 'notification_at', 'source_id', 'curve_rate_mw_per_min'}, 'candidate event keys')
        kind, at, steps = event['kind'], event['at'], event['steps']
        require(kind in ('startup', 'shutdown') and type(at) is int and 0 <= at < len(periods), 'event kind/start')
        require(at >= last_end and isinstance(steps, tuple) and bool(steps) and at + len(steps) <= len(periods), 'overlap or unfinished curve tail')
        require(event['source_id'] in contract['sources'] and contract['sources'][event['source_id']]['kind'] == 'research_curve_assumption', 'curve source missing')
        rate = number(event['curve_rate_mw_per_min'], 'curve rate')
        notice = number(event['notice_h'], 'notice')
        if kind == 'startup':
            notification = stamp(event['notification_at'])
            require(stamp(contract['planned_publication_at']) <= notification and notification + timedelta(hours=notice) <= stamp(contract['boundary_at']) + timedelta(minutes=5*at), 'prospective research notice/lead time')
        else:
            require(event['notification_at'] is None and notice == 0, 'shutdown notice unsupported')
        minimum = d['min_mw'][periods[at]]
        prior = 0. if kind == 'startup' else minimum
        for j, row in enumerate(steps):
            require(type(row) is tuple and len(row) == 4, 'curve step shape')
            a, mean, b, cost = (number(x, 'curve nonnegative finite value') for x in row)
            require(abs(a-prior) < 1e-7 and 0 <= min(a,b) <= mean <= max(a,b) <= minimum and abs(mean-(a+b)/2) < 1e-7, 'curve endpoint/linear mean continuity')
            require(abs(b-a) <= 5*rate+1e-7 and (b-a)*(1 if kind == 'startup' else -1) >= 0, 'curve rate/direction')
            require(d['min_mw'][periods[at+j]] == minimum, 'time-varying transition Pmin unsupported')
            active[at+j] = (event, j, row)
            prior = b
        require(abs(prior-(minimum if kind == 'startup' else 0)) < 1e-7, 'curve final endpoint')
        last_end = at + len(steps)
    fee = 0.
    for i, t in enumerate(periods):
        if i in active:
            e, j, row = active[i]; kind = e['kind']
            if j == 0:
                require(online == int(kind == 'shutdown'), 'invalid event state sequence')
                require(age+1e-9 >= (d['min_down_h'] if kind == 'startup' else d['min_up_h']), 'minimum residence violated')
                if kind == 'startup':
                    off_since=stamp(contract['boundary_at'])+timedelta(hours=i*DT-age)
                    require(stamp(e['notification_at'])>=off_since, 'notice predates preceding off occurrence')
                    starts += 1; age = 0; online = 1; fee += d['startup_yuan'][t]
                else:
                    stops += 1; fee += d['shutdown_yuan'][t]
            a, mean, b, cost = row
            cells.append(dict(phase=kind, stable=0, connected=1, start=int(kind=='startup' and j==0), stop=int(kind=='shutdown' and j==0), mean=mean, begin=a, end=b, cost=cost))
            age += DT
            if j == len(e['steps'])-1 and kind == 'shutdown':
                online = 0; age = 0.
        else:
            cells.append(dict(phase='stable' if online else 'off', stable=online, connected=online, start=0, stop=0, mean=0., begin=0., end=0., cost=0.))
            age += DT
        required = d['status'][t]
        require(required == 'free' or cells[-1]['connected'] == int(required == 'on'), 'candidate conflicts with unit availability')
    require(d['max_starts'] is None or starts <= d['max_starts'], 'candidate start count')
    require(d['max_stops'] is None or stops <= d['max_stops'], 'candidate stop count')
    return dict(cells=cells, fee=fee, terminal=dict(online=online, age_h=age,
        residence_remaining_h=max(0., (d['min_up_h'] if online else d['min_down_h'])-age)))


def build_candidate_scuc(*, contract, candidates, reserve, energy_inputs, batteries, boundary_permit=None):
    validate_contract(contract)
    live_boundary = _validate_prepared_link(contract, energy_inputs, batteries, boundary_permit)
    h = energy_inputs['horizon']; h.validate()
    diagnostic = contract['schema'].endswith('.diagnostic.v1')
    require(h.mode == ('component' if diagnostic else 'day_ahead_research_5min'), 'named DA or separate local diagnostic horizon required')
    require(all(abs(p.duration_h-DT)<1e-12 and abs(p.ramp_elapsed_h-DT)<1e-12 and p.role=='delivery' for p in h.periods), 'physical five-minute grid required')
    boundary, day = stamp(contract['boundary_at']), stamp(contract['day_start'])
    local_day=day.astimezone(ZoneInfo('Asia/Shanghai'))
    require(local_day.hour == local_day.minute == local_day.second == local_day.microsecond == 0, 'Shanghai midnight delivery day required')
    if not diagnostic:
        require(boundary.minute % 15 == 0, 'quarter-aligned full-day boundary required')
        require(boundary+timedelta(minutes=5*len(h.ids)) == day+timedelta(days=1), 'physical horizon must cover complete delivery day after lead-in, no unsourced tail')
    require(energy_inputs['balance_policy'] == 'hard' and energy_inputs['renewable_mode'] == 'fixed_exogenous', 'hard balance and fixed renewables required')
    require(set(reserve) == {'positive_mw', 'negative_mw', 'primary_mw', 'contingency_mw', 'primary_fraction', 'contingency_eligible'}, 'explicit reserve contract')
    battery_data, battery_clock = validate_batteries(batteries, contract, h, energy_inputs['buses'])
    m = build_energy_core(**energy_inputs)
    _attach_batteries(m, battery_data, battery_clock, contract)
    ds = m._energy_inputs['thermal']; ts = tuple(m.T)
    require(set(candidates) == set(ds), 'candidate inventory mismatch')
    require(set(reserve['primary_fraction']) == set(ds) == set(reserve['contingency_eligible']), 'reserve eligibility inventory')
    for name in ('positive_mw', 'negative_mw', 'primary_mw', 'contingency_mw'):
        require(set(reserve[name]) == set(ts), 'reserve time coverage')
        for v in reserve[name].values(): number(v, 'reserve value')
    paths = {}
    for g, d in ds.items():
        require(bool(candidates[g]), 'empty candidate menu')
        require(0 <= number(reserve['primary_fraction'][g], 'primary fraction') <= 1 and type(reserve['contingency_eligible'][g]) is bool, 'qualification values')
        for spec in candidates[g]:
            key = (g, spec['id'])
            require(key not in paths, 'duplicate path ID')
            paths[key] = compile_path(d, spec, ts, contract)
    # Replace the complete instant-thermal block. Network and fixed-output
    # constraints stay active. Availability/min-residence/counts were validated
    # on every path before any integer decision exists.
    for name in ('c_commitment','c_transition_exclusive','c_segment_capacity','c_power_sum','c_power_min','c_power_max','c_ramp_up','c_ramp_down','c_min_up','c_min_down','c_initial_min_duration','c_status','c_max_starts','c_max_stops'):
        getattr(m, name).deactivate()
    for component in (m.online, m.start, m.stop):
        for v in component.values(): v.fix(0.)
    m.PATH = pyo.Set(dimen=2, initialize=tuple(paths))
    m.choose = pyo.Var(m.PATH, domain=pyo.Binary, initialize=0)
    by_g = {g: [k for k in paths if k[0] == g] for g in ds}
    m.c_choose = pyo.Constraint(m.G, rule=lambda _,g: sum(m.choose[k] for k in by_g[g]) == 1)
    idx = {t:i for i,t in enumerate(ts)}
    def weighted(g,t,field):
        return sum(paths[k]['cells'][idx[t]][field]*m.choose[k] for k in by_g[g])
    m.path_state = pyo.ConstraintList()
    for g in m.G:
        for t in m.T:
            for component,field in ((m.online,'connected'),(m.start,'start'),(m.stop,'stop')):
                component[g,t].unfix(); component[g,t].domain=pyo.UnitInterval
                m.path_state.add(component[g,t]==weighted(g,t,field))
    m.stable = pyo.Expression(m.G,m.T,rule=lambda _,g,t:weighted(g,t,'stable'))
    m.fixed_transition = pyo.Expression(m.G,m.T,rule=lambda _,g,t:weighted(g,t,'mean'))
    m.da_min = pyo.Expression(m.G,m.T,rule=lambda _,g,t: ds[g]['min_mw'][t]*m.stable[g,t]+m.fixed_transition[g,t])
    m.da_max = pyo.Expression(m.G,m.T,rule=lambda _,g,t: ds[g]['max_mw'][t]*m.stable[g,t]+m.fixed_transition[g,t])
    m.da_segment = pyo.Constraint(m.GK,m.T,rule=lambda _,g,k,t:m.segment_power[g,k,t]<=ds[g]['segments'][k]['width_mw']*m.stable[g,t])
    m.da_power = pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]==ds[g]['bid_base_mw']*m.stable[g,t]+sum(m.segment_power[g,k,t] for k in ds[g]['segments'])+m.fixed_transition[g,t])
    m.da_lower = pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]>=m.da_min[g,t])
    m.da_upper = pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]<=m.da_max[g,t])
    m.end_power = pyo.Expression(m.G,m.T,rule=lambda _,g,t:m.power[g,t]-m.fixed_transition[g,t]+weighted(g,t,'end'))
    m.ramps = pyo.ConstraintList()
    for g,d in ds.items():
        big = max(d['max_mw'].values())
        for i,t in enumerate(ts):
            prev = d['initial_power_mw'] if not i else m.end_power[g,ts[i-1]]
            # Stable interval's end power obeys five-minute ramp from the true
            # preceding boundary, not the preceding transition mean.
            m.ramps.add(m.power[g,t]-prev <= d['ramp_up_mw_per_h'][t]*DT+big*(1-m.stable[g,t]))
            m.ramps.add(prev-m.power[g,t] <= d['ramp_down_mw_per_h'][t]*DT+big*(1-m.stable[g,t]))
            for key in by_g[g]:
                cell = paths[key]['cells'][i]
                if cell['phase'] in ('startup','shutdown'):
                    m.ramps.add(prev-cell['begin'] <= big*(1-m.choose[key]))
                    m.ramps.add(cell['begin']-prev <= big*(1-m.choose[key]))
    m.primary = pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.contingency = pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.reserve_constraints = pyo.ConstraintList()
    for t in ts:
        demand = sum(m.demand_mw[b,t] for b in m.B)
        fixed = sum(m.renewable_power[b,t]+m.physical_tie[b,t]+m.other_injection[b,t] for b in m.B)
        m.reserve_constraints.add(sum(m.da_max[g,t] for g in m.G)+fixed >= demand+reserve['positive_mw'][t])
        m.reserve_constraints.add(sum(m.da_min[g,t] for g in m.G)+fixed <= demand-reserve['negative_mw'][t])
        for g,d in ds.items():
            for var in (m.primary,m.contingency):
                m.reserve_constraints.add(var[g,t] <= m.da_max[g,t]-m.power[g,t])
            m.reserve_constraints.add(m.primary[g,t] <= reserve['primary_fraction'][g]*d['max_mw'][t]*m.stable[g,t])
            m.reserve_constraints.add(m.contingency[g,t] <= int(reserve['contingency_eligible'][g])*d['ramp_up_mw_per_h'][t]/6*m.stable[g,t])
        m.reserve_constraints.add(sum(m.primary[g,t] for g in m.G) >= reserve['primary_mw'][t])
        m.reserve_constraints.add(sum(m.contingency[g,t] for g in m.G) >= reserve['contingency_mw'][t])
    # Primary and contingency can share headroom, matching explicit existing
    # October representation. Neither is an award or a compensation payment.
    m.da_thermal_cost = pyo.Expression(expr=sum(DT*(d['base_bid_yuan_per_h'][t]*m.stable[g,t]+sum(s['price_yuan_per_mwh'][t]*m.segment_power[g,k,t] for k,s in d['segments'].items())+weighted(g,t,'cost')) for g,d in ds.items() for t in ts)+sum(p['fee']*m.choose[k] for k,p in paths.items()))
    m.total_cost.set_value(m.da_thermal_cost+m.renewable_bid_cost+m.renewable_curtailment_cost+m.network_penalty_cost+m.balance_penalty_cost+m.other_cost)
    for g in m.G:
        for t in m.T:
            m.online_min[g,t].set_value(m.da_min[g,t])
            m.online_max[g,t].set_value(m.da_max[g,t])
    m._energy_scope.update(transition_model='finite_research_candidate_paths',startup_cost_model='uniform_event_plus_explicit_curve',terminal_policy='complete_curves_carry_residence',mode='SCUC')
    m._da_inputs = deepcopy(dict(contract=contract,candidates=candidates,reserve=reserve,energy_inputs=energy_inputs,batteries=batteries))
    m._da_input_hash = digest(m._da_inputs)
    m._da_paths = paths
    m._da_scope = dict(kind='finite_candidate_path_DA_research', official_DA98=False,
        live_prepared_boundary_verified=live_boundary is not None, prepared_boundary_source_sha256=None if live_boundary is None else contract['sources']['prepared_boundary_certificate']['sha256'],
        storage_activity=True, battery_incremental_reserve_mw=0., battery_agc_mw=0.,
        storage_preparation='explicit_research_idle_preparation_and_notice', storage_minimum_mode='complete_residence_within_horizon',
        battery_terminal_slack=False, local_diagnostic=diagnostic, certified_96_point_storage_declaration=False,
        storage_technical_readiness_verified=False, network_policy='penalized_slack_reported_separately', agc_awards='explicit_zero_subset', execution_authority=False,
        curve_source='synthetic_research', requested_mip_rel_gap=.001)
    assert_linear_energy_model(m)
    m._da_structure_hash = _fingerprint(m, include_values=False)
    _BUILT[m] = m._da_structure_hash
    if live_boundary is not None: _LIVE_BOUNDARIES[m] = live_boundary
    return m


def primal_check(m, tolerance=1e-6):
    worst = 0.
    for v in m.component_data_objects(pyo.Var):
        x = pyo.value(v); require(math.isfinite(x), 'nonfinite primal')
        residuals = [0., (v.lb-x) if v.has_lb() else 0., (x-v.ub) if v.has_ub() else 0.]
        if v.is_integer(): residuals.append(abs(x-round(x)))
        worst = max(worst, *residuals)
    for c in m.component_data_objects(pyo.Constraint, active=True):
        x = pyo.value(c.body)
        require(math.isfinite(x), 'nonfinite constraint primal')
        worst = max(worst, pyo.value(c.lower)-x if c.has_lb() else 0., x-pyo.value(c.upper) if c.has_ub() else 0.)
    require(worst <= tolerance, 'DA primal bound/constraint/integer violation')
    return worst


def _fingerprint(m, include_values=True):
    # Includes values as well as mathematics to reject post-solve mutation.
    from pyomo.repn import generate_standard_repn
    rows = []
    for c in m.component_data_objects(pyo.Constraint, active=True):
        r = generate_standard_repn(c.body)
        rows.append((c.name,str(c.lower),str(c.upper),r.constant,str(r.nonlinear_expr),[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    r = generate_standard_repn(m.objective.expr)
    rows.append(('objective',r.constant,str(r.nonlinear_expr),[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    return digest((m._da_inputs,m._da_input_hash,m._da_paths,m._da_scope,m._battery_inputs,m._battery_clock,rows,[(v.name,v.value if include_values or v.fixed else None,v.fixed,str(v.domain),v.lb,v.ub) for v in m.component_data_objects(pyo.Var)]))


def solve_scuc(m, *, time_limit_s, threads=1):
    from .gd_solve import solve_dispatch
    require(hasattr(m,'_da_inputs') and digest(m._da_inputs)==m._da_input_hash, 'DA inputs modified')
    require(m in _BUILT and _fingerprint(m, include_values=False)==_BUILT[m], 'DA model structure modified before solve')
    if m._da_inputs['contract']['boundary_kind']=='prepared_D00_research':
        require(m in _LIVE_BOUNDARIES, 'live prepared boundary construction provenance absent')
        _validate_prepared_link(m._da_inputs['contract'],m._da_inputs['energy_inputs'],m._da_inputs['batteries'],_LIVE_BOUNDARIES[m][1])
    _native_solver_identity(solve_dispatch)
    _FAILED.pop(m,None); _ACCEPTED.pop(m,None); _ATTEMPTS.pop(m,None)
    attempt_id=str(uuid4()); attempted_at=datetime.now(timezone.utc).isoformat()
    from .highs_solver import HighsNoIncumbentError
    try:
        result = solve_dispatch(m,stage='R12_DA_CANDIDATE_SCUC',time_limit_s=time_limit_s,threads=threads,mip_rel_gap=.001)
    except HighsNoIncumbentError as exc:
        record=exc.failure_record
        require(record['native_path_intact'] is True, 'native failure provenance is not intact')
        _FAILED[m]=dict(schema='gd.research.da_candidate_battery.failure.v1',attempt_id=attempt_id,attempted_at_utc=attempted_at,
            input_sha256=m._da_input_hash,physical_model_sha256=_BUILT[m],
            experiment_id=m._da_inputs['contract']['experiment_id'],
            worker_failure=record,accepted_primal=False,execution_authority=False,
            effective_plan_issued=False,official_price=None,settlement_cash=None,
            full_market_completed=False)
        _ATTEMPTS[m]=dict(schema='gd.research.da_candidate_battery.attempt.v1',attempt_id=attempt_id,attempted_at_utc=attempted_at,input_sha256=m._da_input_hash,physical_model_sha256=_BUILT[m],experiment_id=m._da_inputs['contract']['experiment_id'],metrics=None,worker_failure=deepcopy(record),accepted_at_attempt=False,primal_verified=False,acceptance_rejection='no_native_incumbent',execution_authority=False,official_price=None,settlement_cash=None)
        raise
    # Preserve a genuine native incumbent even when acceptance later rejects
    # its gap or primal residual. This never mints an accepted selection.
    _native_metrics(result, m, lp=False, enforce_gap=False)
    result.metrics.update(attempt_id=attempt_id,attempted_at_utc=attempted_at)
    _ATTEMPTS[m]=_attempt_record(m,result.metrics,accepted=False)
    try:
        result.metrics['primal_max_violation'] = primal_check(m)
    except EnergyInputError:
        _ATTEMPTS[m]['acceptance_rejection']='primal_check_failed'
        raise
    result.metrics['network_slack_max_mw'] = max([0.]+[pyo.value(v) for block in (m.line_over,m.line_under,m.section_over,m.section_under) for v in block.values()])
    result.metrics['hard_network_feasible'] = result.metrics['network_slack_max_mw'] <= 1e-6
    result.metrics['physical_and_network_verified'] = result.metrics['hard_network_feasible']
    _ATTEMPTS[m]=_attempt_record(m,result.metrics,accepted=False)
    if not result.metrics['requested_gap_met'] or result.metrics['canonical_relative_gap']>.001+1e-9:
        _ATTEMPTS[m]['acceptance_rejection']='requested_gap_not_met'
        raise EnergyInputError('SCUC gap target not met; verified native attempt retained')
    _native_metrics(result,m,lp=False)
    _ACCEPTED[m] = (_fingerprint(m), deepcopy(result.metrics))
    _ATTEMPTS[m]=_attempt_record(m,result.metrics,accepted=True)
    return result


def build_fixed_selection_sced(m):
    require(m in _ACCEPTED, 'live internally accepted SCUC required; report or caller choices insufficient')
    fingerprint, metrics = _ACCEPTED[m]
    require(_fingerprint(m)==fingerprint, 'accepted SCUC changed')
    clone = m.clone()
    for original in m.component_data_objects(pyo.Var):
        if original.is_integer(): clone.find_component(original.name).fix(round(pyo.value(original)))
    clone._da_scuc_metrics = deepcopy(metrics)
    clone._da_scope['kind'] = 'fixed_accepted_candidate_DA_SCED_research'
    assert_linear_energy_model(clone,require_lp=True)
    return clone


def solve_fixed_selection_sced(m, *, time_limit_s, threads=1):
    """Internally freeze this live accepted SCUC selection, then solve the LP."""
    from .gd_solve import solve_dispatch
    fixed = build_fixed_selection_sced(m)
    _native_solver_identity(solve_dispatch)
    result = solve_dispatch(fixed, stage='R12_DA_FIXED_SELECTION_SCED',
        time_limit_s=time_limit_s, threads=threads, mip_rel_gap=.001)
    _native_metrics(result, fixed, lp=True)
    primal_check(fixed)
    # Keep original integer bound/gap; LP optimum never upgrades the MIP claim.
    result.metrics['original_scuc_metrics'] = deepcopy(fixed._da_scuc_metrics)
    result.metrics['executable_plan_certified'] = False
    result.metrics['network_slack_max_mw'] = max([0.]+[pyo.value(v) for block in
        (fixed.line_over,fixed.line_under,fixed.section_over,fixed.section_under) for v in block.values()])
    result.metrics['hard_network_feasible'] = result.metrics['network_slack_max_mw'] <= 1e-6
    result.metrics['physical_and_network_verified'] = result.metrics['hard_network_feasible']
    result.metrics['primal_max_violation'] = primal_check(fixed)
    _ACCEPTED_SCED[fixed] = (_fingerprint(fixed), deepcopy(result.metrics))
    return result



def _native_solver_identity(solver):
    from .gd_solve import _ORIGINAL_SOLVE_DISPATCH
    from .highs_solver import native_solver_path_intact
    require(solver is _ORIGINAL_SOLVE_DISPATCH and native_solver_path_intact(), 'original native solve_dispatch identity required')


def _native_metrics(result, model, *, lp, enforce_gap=True):
    from .gd_solve import DispatchSolution
    require(type(result) is DispatchSolution and result.model is model, 'native typed result/model mismatch')
    a=result.metrics
    require(a['backend']=='highs' and a['termination'] in (('optimal',) if lp else ('optimal','maxTimeLimit')), 'native HiGHS termination required')
    require(a['requested_mip_rel_gap']==.001 and math.isfinite(a['canonical_relative_gap']) and
        0 <= a['canonical_relative_gap'] and a['objective_offset_yuan']==0., 'native canonical gap provenance required')
    if enforce_gap:
        require(a['requested_gap_met'] and a['canonical_relative_gap']<=.001+1e-9, 'native canonical gap required')
    require(a['solver_version'] is not None and a['native_status'] is not None and a['solver_settings'] is not None, 'native solver provenance missing')
    require(math.isfinite(a['objective_yuan']) and abs(a['objective_yuan']-pyo.value(model.objective)) <= 1e-4+1e-9*max(1.,abs(a['objective_yuan'])), 'native objective/primal mismatch')
    if lp: require(a['integer_variables']==0, 'fixed selection must be continuous LP')


def native_failure_report(m):
    """Preserve actual verified worker failure; never parse exception strings."""
    require(m in _FAILED and m in _BUILT, 'recorded native failure required')
    require(_fingerprint(m,include_values=False)==_BUILT[m], 'failed model mathematics changed')
    return deepcopy(_FAILED[m])


BATTERY_KEYS = {
    'bus','energy_capacity_mwh','roundtrip_efficiency','initial_energy_mwh',
    'energy_min_mwh','energy_max_mwh','terminal_energy_mwh','terminal_at',
    'charge_max_mw','discharge_max_mw','initial_power_mw','initial_mode',
    'initial_mode_age_h','initial_hour_direction','ramp_up_mw_per_h',
    'ramp_down_mw_per_h','preparation_h','minimum_mode_h','availability',
    'charge_curve','discharge_curve','cycle_budget','notice_available_at',
}
SHANGHAI = ZoneInfo('Asia/Shanghai')


def local_diagnostic_horizon(count):
    """Separate component contract, NEVER a shortened full-day DA contract."""
    require(type(count) is int and count > 0, 'positive diagnostic cell count')
    return EnergyHorizon(tuple(EnergyPeriod(t,DT,DT,'delivery',True) for t in range(count)),
        'component','R12 battery local diagnostic five-minute research')


def _json_equal(a, b):
    # Normalize only JSON containers, preserving all supplied decimal values.
    return json.dumps(json.loads(json.dumps(a,allow_nan=False)),sort_keys=True,allow_nan=False) == json.dumps(json.loads(json.dumps(b,allow_nan=False)),sort_keys=True,allow_nan=False)


def validate_batteries(batteries, contract, horizon, buses):
    require(type(batteries) is dict and bool(batteries), 'explicit battery inventory required')
    source_id=contract['storage']['battery_parameters_source_id']
    require(source_id in contract['sources'], 'battery parameter source absent')
    source=contract['sources'][source_id]
    require(source['kind']=='research_storage_assumption', 'battery assumptions must be labeled research')
    try:
        bound=json.loads(source['artifact'])
    except (UnicodeDecodeError,ValueError,TypeError) as exc:
        raise EnergyInputError('battery parameter source must contain JSON') from exc
    require(_json_equal(bound,dict(batteries=batteries,pumped_storage=contract['storage']['pumped_storage'])), 'battery parameters/source bytes mismatch')
    ts=horizon.ids; beginning=stamp(contract['boundary_at']).astimezone(SHANGHAI)
    times={t:beginning+timedelta(minutes=5*i) for i,t in enumerate(ts)}
    dates={t:x.date().isoformat() for t,x in times.items()}
    hours={t:x.replace(minute=0,second=0,microsecond=0).isoformat() for t,x in times.items()}
    clock=dict(times={t:x.isoformat() for t,x in times.items()},dates=dates,hours=hours)
    result=deepcopy(batteries)
    for uid,r in result.items():
        require(type(uid) is str and bool(uid.strip()) and set(r)==BATTERY_KEYS, 'battery identity/keys')
        require(r['bus'] in buses, 'battery bus missing')
        for key in ('energy_capacity_mwh','roundtrip_efficiency','initial_energy_mwh','energy_min_mwh','energy_max_mwh','terminal_energy_mwh','charge_max_mw','discharge_max_mw','initial_mode_age_h','ramp_up_mw_per_h','ramp_down_mw_per_h'):
            number(r[key], 'battery '+key)
        require(r['energy_capacity_mwh']>0 and 0<r['roundtrip_efficiency']<=1, 'battery capacity/efficiency')
        require(0<=r['energy_min_mwh']<=r['initial_energy_mwh']<=r['energy_max_mwh']<=r['energy_capacity_mwh'], 'initial battery energy hard bounds')
        require(r['energy_min_mwh']<=r['terminal_energy_mwh']<=r['energy_max_mwh'], 'terminal battery energy hard bounds')
        require(r['charge_max_mw']>0 and r['discharge_max_mw']>0, 'positive battery power ratings')
        p=number(r['initial_power_mw'], 'signed initial battery power', minimum=-math.inf)
        require(r['initial_mode'] in ('idle','charge','discharge'), 'explicit battery initial mode')
        require(-r['charge_max_mw']<=p<=r['discharge_max_mw'], 'initial battery power capacity')
        require((p==0 if r['initial_mode']=='idle' else p<=0 if r['initial_mode']=='charge' else p>=0), 'initial mode/power contradiction')
        q=r['initial_hour_direction']
        require(type(q) is int and q in (-1,0,1), 'initial same-hour direction')
        require(beginning.minute!=0 or q==0, 'new clock hour cannot inherit direction')
        if beginning.minute and r['initial_mode']!='idle':
            require(q==(-1 if r['initial_mode']=='charge' else 1), 'active same-hour boundary direction missing')
        require(set(r['preparation_h'])=={'charge','discharge'} and set(r['minimum_mode_h'])=={'idle','charge','discharge'}, 'explicit preparation/minimum-mode assumptions')
        for x in (*r['preparation_h'].values(),*r['minimum_mode_h'].values()): number(x,'preparation/residence hours')
        notice=stamp(r['notice_available_at'])
        require(stamp(contract['planned_publication_at'])<=notice<=beginning, 'battery notice cannot predate publication or follow boundary')
        require(set(r['availability'])==set(ts) and all(type(t) is int and type(v) is bool for t,v in r['availability'].items()), 'battery availability exact Boolean cells')
        terminal=stamp(r['terminal_at'])
        ends={t:x+timedelta(minutes=5) for t,x in times.items()}
        require(terminal in ends.values(), 'battery terminal target must be an actual included boundary')
        if not contract['schema'].endswith('.diagnostic.v1'):
            require(terminal==stamp(contract['day_start'])+timedelta(days=1), 'battery terminal target must be actual delivery-day end')
        r['_terminal_period']=next(t for t,x in ends.items() if x==terminal)
        r['_eta']=math.sqrt(r['roundtrip_efficiency'])
        require(type(r['cycle_budget']) is dict and set(dates.values())<=set(r['cycle_budget']), 'dated battery throughput coverage')
        for date,budget in r['cycle_budget'].items():
            require(datetime.fromisoformat(date).date().isoformat()==date and set(budget)=={'limit_cycles','used_internal_mwh'}, 'dated cycle budget keys')
            number(budget['limit_cycles'],'declared cycle assumption'); number(budget['used_internal_mwh'],'prior daily throughput')
            require(budget['used_internal_mwh']<=2*r['energy_capacity_mwh']*budget['limit_cycles']+1e-8, 'prior daily throughput exhausts budget')
            require(date<=beginning.date().isoformat() or budget['used_internal_mwh']==0, 'future operating-date throughput cannot preexist boundary')
        for direction in ('charge','discharge'):
            curve=r[direction+'_curve']
            require(set(curve)=={'breakpoints_mw','prices_yuan_per_mwh'}, 'battery exact curve keys')
            points,prices=curve['breakpoints_mw'],curve['prices_yuan_per_mwh']
            require(type(points) in (tuple,list) and type(prices) in (tuple,list) and len(points)==len(prices)+1 and 1<=len(prices)<=10, 'battery curve shape/research ten-segment cap')
            for x in points: number(x,'battery breakpoint')
            for x in prices: number(x,'battery signed bid price',minimum=-math.inf)
            require(points[0]==0 and points[-1]==r[direction+'_max_mw'] and all(b>a for a,b in zip(points,points[1:])), 'battery continuous exact capacity endpoints')
            require(all((b-a)*(1 if direction=='discharge' else -1)>=0 for a,b in zip(prices,prices[1:])), 'battery convex signed-axis prices')
    return result,clock


def _attach_batteries(m, data, clock, contract):
    """Attach once before the new builder seals any mathematics."""
    ts=tuple(m.T); idx={t:i for i,t in enumerate(ts)}
    b=m.battery=pyo.Block()
    b.BAT=pyo.Set(initialize=tuple(data));b.T=pyo.Set(initialize=ts)
    b.HOUR=pyo.Set(initialize=tuple(dict.fromkeys(clock['hours'].values())))
    b.charge=pyo.Var(b.BAT,b.T,domain=pyo.NonNegativeReals,initialize=0)
    b.discharge=pyo.Var(b.BAT,b.T,domain=pyo.NonNegativeReals,initialize=0)
    b.energy=pyo.Var(b.BAT,b.T,domain=pyo.NonNegativeReals,initialize=lambda _,u,t:data[u]['initial_energy_mwh'],bounds=lambda _,u,t:(data[u]['energy_min_mwh'],data[u]['energy_max_mwh']))
    b.charge_mode=pyo.Var(b.BAT,b.T,domain=pyo.Binary,initialize=0)
    b.discharge_mode=pyo.Var(b.BAT,b.T,domain=pyo.Binary,initialize=0)
    b.hour_direction=pyo.Var(b.BAT,b.HOUR,domain=pyo.Binary,initialize=0)
    b.idle_mode=pyo.Expression(b.BAT,b.T,rule=lambda _,u,t:1-b.charge_mode[u,t]-b.discharge_mode[u,t])
    b.power=pyo.Expression(b.BAT,b.T,rule=lambda _,u,t:b.discharge[u,t]-b.charge[u,t])
    b.energy_begin=pyo.Expression(b.BAT,b.T,rule=lambda _,u,t:data[u]['initial_energy_mwh'] if idx[t]==0 else b.energy[u,ts[idx[t]-1]])
    b.internal_throughput=pyo.Expression(b.BAT,b.T,rule=lambda _,u,t:DT*(data[u]['_eta']*b.charge[u,t]+b.discharge[u,t]/data[u]['_eta']))
    b.c_power=pyo.ConstraintList();b.c_soc=pyo.ConstraintList();b.c_ramp=pyo.ConstraintList()
    b.c_hour=pyo.ConstraintList();b.c_cycle=pyo.ConstraintList();b.c_terminal=pyo.ConstraintList();b.c_readiness=pyo.ConstraintList()
    for uid,r in data.items():
        mode=lambda name,t:getattr(b,name+'_mode')[uid,t]
        for i,t in enumerate(ts):
            b.c_power.add(b.charge[uid,t]<=r['charge_max_mw']*b.charge_mode[uid,t])
            b.c_power.add(b.discharge[uid,t]<=r['discharge_max_mw']*b.discharge_mode[uid,t])
            b.c_power.add(b.charge_mode[uid,t]+b.discharge_mode[uid,t]<=1)
            if not r['availability'][t]:
                b.charge_mode[uid,t].fix(0);b.discharge_mode[uid,t].fix(0)
            b.c_soc.add(b.energy[uid,t]==b.energy_begin[uid,t]+DT*(r['_eta']*b.charge[uid,t]-b.discharge[uid,t]/r['_eta']))
            previous=r['initial_power_mw'] if i==0 else b.power[uid,ts[i-1]]
            b.c_ramp.add(b.power[uid,t]-previous<=DT*r['ramp_up_mw_per_h'])
            b.c_ramp.add(previous-b.power[uid,t]<=DT*r['ramp_down_mw_per_h'])
            hour=clock['hours'][t]
            b.c_hour.add(b.charge_mode[uid,t]<=1-b.hour_direction[uid,hour])
            b.c_hour.add(b.discharge_mode[uid,t]<=b.hour_direction[uid,hour])
            for name in ('idle','charge','discharge'):
                prev=int(r['initial_mode']==name) if i==0 else mode(name,ts[i-1])
                entry=mode(name,t)-prev
                n=int(math.ceil(r['minimum_mode_h'][name]/DT-1e-10))
                if n>len(ts)-i:
                    b.c_readiness.add(entry<=0)
                else:
                    for j in range(i+1,i+n): b.c_readiness.add(mode(name,ts[j])>=entry)
                if r['initial_mode']==name and i*DT<r['minimum_mode_h'][name]-r['initial_mode_age_h']-1e-10:
                    b.c_readiness.add(mode(name,t)==1)
                if name!='idle':
                    prep=r['preparation_h'][name]
                    nprep=int(math.ceil(prep/DT-1e-10))
                    # An entry needs consecutive idle preparation, including
                    # inherited idle age when preparation began before boundary.
                    for j in range(max(0,i-nprep),i):
                        b.c_readiness.add(b.idle_mode[uid,ts[j]]>=entry)
                    if i*DT+1e-10<prep and (r['initial_mode']!='idle' or r['initial_mode_age_h']+i*DT+1e-10<prep):
                        b.c_readiness.add(entry<=0)
                    if stamp(clock['times'][t])<stamp(r['notice_available_at'])+timedelta(hours=prep):
                        b.c_readiness.add(entry<=0)
        if r['initial_hour_direction']:
            b.hour_direction[uid,clock['hours'][ts[0]]].fix(int(r['initial_hour_direction']>0))
        for date in dict.fromkeys(clock['dates'].values()):
            budget=r['cycle_budget'][date]
            b.c_cycle.add(budget['used_internal_mwh']+sum(b.internal_throughput[uid,t] for t in ts if clock['dates'][t]==date)<=2*r['energy_capacity_mwh']*budget['limit_cycles'])
        b.c_terminal.add(b.energy[uid,r['_terminal_period']]==r['terminal_energy_mwh'])
    keys=[(u,d,k) for u,r in data.items() for d in ('charge','discharge') for k in range(len(r[d+'_curve']['prices_yuan_per_mwh']))]
    b.SEG=pyo.Set(dimen=3,initialize=keys)
    b.segment=pyo.Var(b.SEG,b.T,domain=pyo.NonNegativeReals,initialize=0)
    b.c_segment=pyo.ConstraintList();b.c_bid_power=pyo.ConstraintList()
    for u,d,k in keys:
        points=data[u][d+'_curve']['breakpoints_mw'];width=points[k+1]-points[k]
        for t in ts:b.c_segment.add(b.segment[u,d,k,t]<=width*getattr(b,d+'_mode')[u,t])
    for u in data:
        for d in ('charge','discharge'):
            for t in ts:b.c_bid_power.add(getattr(b,d)[u,t]==sum(b.segment[u,d,k,t] for uu,dd,k in keys if uu==u and dd==d))
    b.charge_credit=pyo.Expression(expr=sum(DT*data[u]['charge_curve']['prices_yuan_per_mwh'][k]*b.segment[u,d,k,t] for u,d,k in keys if d=='charge' for t in ts))
    b.discharge_cost=pyo.Expression(expr=sum(DT*data[u]['discharge_curve']['prices_yuan_per_mwh'][k]*b.segment[u,d,k,t] for u,d,k in keys if d=='discharge' for t in ts))
    b.bid_cost=pyo.Expression(expr=b.discharge_cost-b.charge_credit)
    for bus in m.B:
        for t in ts:
            m.other_injection[bus,t].set_value(sum(b.power[u,t] for u,r in data.items() if r['bus']==bus))
    m.other_cost.set_value(b.bid_cost)
    m._battery_inputs=deepcopy(data);m._battery_clock=deepcopy(clock)


def accepted_da_report(solution):
    """Authoritative accepted SCED snapshot, not a publication/execution permit."""
    m=solution.model
    require(m in _ACCEPTED_SCED, 'live internally solved fixed-selection SCED required')
    fingerprint,metrics=_ACCEPTED_SCED[m]
    require(_fingerprint(m)==fingerprint and solution.metrics==metrics, 'accepted SCED/metrics changed')
    selected={g:next(k[1] for k in m.PATH if k[0]==g and round(pyo.value(m.choose[k]))==1) for g in m.G}
    ts=tuple(m.T);c=m._da_inputs['contract'];beginning=stamp(c['boundary_at']);day=stamp(c['day_start'])
    b=m.battery;data=m._battery_inputs;clock=m._battery_clock
    used={u:{date:q['used_internal_mwh'] for date,q in r['cycle_budget'].items()} for u,r in data.items()}
    ages={u:r['initial_mode_age_h'] for u,r in data.items()};modes={u:r['initial_mode'] for u,r in data.items()}
    directions={u:r['initial_hour_direction'] for u,r in data.items()}
    prior_hour=clock['hours'][ts[0]]
    cells=[]
    for i,t in enumerate(ts):
        if clock['hours'][t]!=prior_hour:
            directions={u:0 for u in data};prior_hour=clock['hours'][t]
        at=beginning+timedelta(minutes=5*i);storage={}
        for u,r in data.items():
            mode='charge' if round(pyo.value(b.charge_mode[u,t])) else 'discharge' if round(pyo.value(b.discharge_mode[u,t])) else 'idle'
            if mode!='idle': directions[u]=-1 if mode=='charge' else 1
            entered=mode!=modes[u];age_begin=0. if entered else ages[u]
            ages[u]=age_begin+DT;modes[u]=mode
            date=clock['dates'][t];used[u][date]+=pyo.value(b.internal_throughput[u,t])
            storage[u]=dict(mode=mode,charge_mw=pyo.value(b.charge[u,t]),discharge_mw=pyo.value(b.discharge[u,t]),power_mw=pyo.value(b.power[u,t]),energy_begin_mwh=pyo.value(b.energy_begin[u,t]),energy_end_mwh=pyo.value(b.energy[u,t]),soc_end=pyo.value(b.energy[u,t])/r['energy_capacity_mwh'],calendar_hour=clock['hours'][t],hour_direction=directions[u],selected_hour_direction_permission=1 if round(pyo.value(b.hour_direction[u,clock['hours'][t]])) else -1,mode_entered=entered,mode_age_begin_h=age_begin,mode_age_end_h=ages[u],notice_available_at=r['notice_available_at'],preparation_h=0. if mode=='idle' else r['preparation_h'][mode],cycle_date=date,internal_throughput_mwh=pyo.value(b.internal_throughput[u,t]),daily_used_internal_mwh=used[u][date],incremental_operating_up_mw=0.,incremental_operating_down_mw=0.,primary_mw=0.,contingency_mw=0.,agc_mw=0.,reserve_signed_baseline_mw=pyo.value(b.power[u,t]))
        fixed=sum(pyo.value(m.renewable_power[bus,t]+m.physical_tie[bus,t]) for bus in m.B)
        baseline=sum(x['power_mw'] for x in storage.values());demand=sum(pyo.value(m.demand_mw[bus,t]) for bus in m.B)
        cells.append(dict(start=at.isoformat(),end=(at+timedelta(minutes=5)).isoformat(),thermal={g:dict(phase=m._da_paths[g,selected[g]]['cells'][i]['phase'],mean_mw=pyo.value(m.power[g,t]),end_mw=pyo.value(m.end_power[g,t]),connected=int(round(pyo.value(m.online[g,t]))),stable=int(round(pyo.value(m.stable[g,t])))) for g in m.G},batteries=storage,reserve=dict(demand_mw=demand,fixed_nonbattery_mw=fixed,battery_signed_baseline_mw=baseline,battery_incremental_credit_mw=0.,positive_lhs_mw=sum(pyo.value(m.da_max[g,t]) for g in m.G)+fixed+baseline,positive_rhs_mw=demand+m._da_inputs['reserve']['positive_mw'][t],negative_lhs_mw=sum(pyo.value(m.da_min[g,t]) for g in m.G)+fixed+baseline,negative_rhs_mw=demand-m._da_inputs['reserve']['negative_mw'][t],primary_mw=sum(pyo.value(m.primary[g,t]) for g in m.G),contingency_mw=sum(pyo.value(m.contingency[g,t]) for g in m.G)),pumped_storage_power_mw=0.))
    delivery=[r for r in cells if day<=stamp(r['start'])<day+timedelta(days=1)]
    if not m._da_scope['local_diagnostic']:require(len(delivery)==288,'DA report requires288 delivery cells')
    quarters=[]
    if not m._da_scope['local_diagnostic']:
        quarters=[dict(start=delivery[i]['start'],thermal_mean_mw={g:sum(delivery[i+j]['thermal'][g]['mean_mw'] for j in range(3))/3 for g in m.G},batteries={u:dict(charge_mean_mw=sum(delivery[i+j]['batteries'][u]['charge_mw'] for j in range(3))/3,discharge_mean_mw=sum(delivery[i+j]['batteries'][u]['discharge_mw'] for j in range(3))/3,power_mean_mw=sum(delivery[i+j]['batteries'][u]['power_mw'] for j in range(3))/3,energy_end_mwh=delivery[i+2]['batteries'][u]['energy_end_mwh']) for u in data}) for i in range(0,288,3)]
    costs={name:pyo.value(expr) for name,expr in dict(thermal=m.da_thermal_cost,renewable_bid=m.renewable_bid_cost,renewable_curtailment=m.renewable_curtailment_cost,network_penalty=m.network_penalty_cost,balance_penalty=m.balance_penalty_cost,battery_discharge=b.discharge_cost,battery_charge_credit=b.charge_credit,battery_net=b.bid_cost,total=m.total_cost).items()}
    decomposition=sum(costs[k] for k in ('thermal','renewable_bid','renewable_curtailment','network_penalty','balance_penalty','battery_net'))
    require(abs(costs['total']-decomposition)<1e-6+1e-10*abs(costs['total']),'battery objective decomposition failed')
    return dict(schema='gd.research.da_candidate_battery.report.v1',input_sha256=m._da_input_hash,experiment_id=c['experiment_id'],boundary_kind=c['boundary_kind'],selected_paths=selected,physical_cells=cells,delivery_quarters=quarters,terminal={g:dict(m._da_paths[g,selected[g]]['terminal'],power_mw=pyo.value(m.end_power[g,ts[-1]])) for g in m.G},battery_terminal={u:dict(target_at=r['terminal_at'],target_mwh=r['terminal_energy_mwh'],actual_mwh=pyo.value(b.energy[u,r['_terminal_period']]),daily_used_internal_mwh=used[u]) for u,r in data.items()},cost_components_yuan=costs,metrics=deepcopy(metrics),source_sha256={k:v['sha256'] for k,v in c['sources'].items()},scope=deepcopy(m._da_scope),official_DA98=False,issued_effective_plan=False,execution_authority=False,official_price=None,settlement_cash=None,full_market_completed=False)


def _validate_prepared_link(contract, energy_inputs, batteries, permit):
    """Prepared construction needs a live native predecessor, never a report flag."""
    if contract['boundary_kind']!='prepared_D00_research':
        require(permit is None, 'boundary permit supplied to nonprepared case')
        return None
    require(permit is not None, 'live prepared boundary permit required for construction')
    source=contract['sources'].get('prepared_boundary_certificate')
    require(source is not None and source['kind']=='research_boundary_assumption', 'live prepared boundary source required')
    from .gd_october_job_codec import loads
    from .gd_da_prepared_boundary import validate_live_boundary
    report=loads(source['artifact'])
    states=validate_live_boundary(report,permit)
    require(stamp(report['output_at'])==stamp(contract['boundary_at']), 'live prepared boundary output clock mismatch')
    hypothesis=report['problem']['hypothesis']
    require(hypothesis['boundary_kind']==contract['boundary_kind'], 'live prepared boundary kind mismatch')
    for field in ('day_start','cutoff','planned_publication_at'):
        require(stamp(hypothesis[field])==stamp(contract[field]), 'live prepared boundary clock/source mismatch: '+field)
    require(set(states)==set(energy_inputs['thermal']), 'live prepared thermal inventory mismatch')
    for g,state in states.items():
        for field in ('initial_online','initial_power_mw','initial_state_age_h','initial_state_kind'):
            require(energy_inputs['thermal'][g][field]==state[field], 'live prepared thermal state mismatch: '+g+'.'+field)
    inventory=report['storage_inventory']
    require(set(inventory['batteries'])==set(batteries), 'live prepared battery inventory mismatch')
    for u,r in batteries.items():
        old=inventory['batteries'][u]
        require(r['initial_mode']==old['mode'] and r['initial_power_mw']==old['power_mw'] and r['initial_energy_mwh']==old['energy_mwh'], 'live prepared battery boundary mismatch: '+u)
    require(digest(inventory['pumped'])==digest(contract['storage']['pumped_storage']['inventory']), 'live prepared pumped inventory mismatch')
    for key,old in report['problem']['sources'].items():
        if key.startswith(('static:','prior:')):
            require(key in contract['sources'] and old['sha256']==contract['sources'][key]['sha256'] and old['artifact']==contract['sources'][key]['artifact'], 'live prepared shared source mismatch: '+key)
        # The independently reconstructed preparation and delivery vintages
        # cover different clocks. Their shared underlying prior, not unequal
        # per-horizon forecast bytes, must match exactly.
    return (deepcopy(report),permit)


def _attempt_record(model, metrics, *, accepted):
    return dict(schema='gd.research.da_candidate_battery.attempt.v1',
        attempt_id=metrics['attempt_id'],attempted_at_utc=metrics['attempted_at_utc'],
        input_sha256=model._da_input_hash,physical_model_sha256=_BUILT[model],
        experiment_id=model._da_inputs['contract']['experiment_id'],metrics=deepcopy(metrics),
        worker_failure=None,accepted_at_attempt=accepted,
        primal_verified='primal_max_violation' in metrics,acceptance_rejection=None,
        execution_authority=False,official_price=None,settlement_cash=None)


def native_attempt_report(model):
    """Historical genuine attempt metrics, never a live accepted plan permit.

    An incumbent outside the requested gap is still evidence; neither LP
    polishing nor acceptance is allowed merely because these metrics exist.
    """
    require(model in _ATTEMPTS and model in _BUILT, 'recorded native attempt required')
    require(_fingerprint(model,include_values=False)==_BUILT[model], 'attempted model mathematics changed')
    return deepcopy(_ATTEMPTS[model])
