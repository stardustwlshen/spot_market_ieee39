"""R11 finite-path DA research SCUC, never an official DA98 or execution permit.

The sole free integers select predeclared complete thermal event paths. Network,
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
import pyomo.environ as pyo
from .gd_energy_core import build_energy_core, EnergyInputError, assert_linear_energy_model
from .gd_horizon import EnergyHorizon, EnergyPeriod

DT = 1 / 12
_BUILT = WeakKeyDictionary()
_ACCEPTED = WeakKeyDictionary()
_ACCEPTED_SCED = WeakKeyDictionary()
_FAILED = WeakKeyDictionary()


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
    require(c['schema'] == 'gd.research.da_candidate.v1' and c['experiment_id'].strip(), 'DA identity')
    cutoff, publication, boundary, day = map(stamp, (c['cutoff'], c['planned_publication_at'], c['boundary_at'], c['day_start']))
    stamp(c['generated_at_utc'])  # Actual wall clock is not simulated availability.
    require(cutoff <= publication <= boundary <= day, 'DA decision/publication/boundary chronology')
    require(c['boundary_kind'] in ('original_D00_research', 'synthetic_lead_in_research'), 'explicit boundary identity')
    require((boundary == day) == (c['boundary_kind'] == 'original_D00_research'), 'original boundary must not move')
    require(boundary.second == boundary.microsecond == 0 and boundary.minute % 5 == 0, 'five-minute boundary')
    require(c['cost_policy'] == 'stable_bid_plus_transition_hourly_plus_uniform_event', 'explicit cost policy')
    require(c['terminal_policy'] == 'complete_curves_carry_residence', 'explicit tail policy')
    require(c['storage']['policy'] == 'all_idle_off_fixed_inventory_zero_credit', 'storage freedom unsupported')
    require(bool(c['storage']['inventory_sha256']), 'retained storage inventory required')
    require(bool(c['sources']), 'explicit byte sources required')
    for key, s in c['sources'].items():
        require(set(s) == {'artifact', 'sha256', 'available_at', 'kind'}, 'source keys')
        require(type(s['artifact']) is bytes and sha256(s['artifact']).hexdigest() == s['sha256'], 'source bytes/hash mismatch')
        require(stamp(s['available_at']) <= cutoff, 'future DA source: ' + key)
        require(s['kind'] in ('retained_static', 'synthetic_exogenous_prior', 'synthetic_da_vintage', 'research_curve_assumption', 'research_boundary_assumption'), 'unsupported or offline future truth source')
    return c


def research_horizon(count=288):
    require(type(count) is int and count >= 288 and count % 3 == 0, 'DA physical grid requires whole quarters and full delivery day')
    return EnergyHorizon(tuple(EnergyPeriod(t, DT, DT, 'delivery', True) for t in range(count)),
        'day_ahead_research_5min', 'R11 finite candidate-path research; not official 98 points')


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


def build_candidate_scuc(*, contract, candidates, reserve, energy_inputs):
    validate_contract(contract)
    h = energy_inputs['horizon']; h.validate()
    require(h.mode == 'day_ahead_research_5min', 'named DA physical research horizon required')
    boundary, day = stamp(contract['boundary_at']), stamp(contract['day_start'])
    require(boundary.minute % 15 == 0 and day.hour == day.minute == day.second == day.microsecond == 0, 'quarter-aligned boundary and midnight delivery day required')
    require(boundary+timedelta(minutes=5*len(h.ids)) >= day+timedelta(days=1), 'physical horizon must cover complete delivery day after lead-in')
    require(energy_inputs['balance_policy'] == 'hard' and energy_inputs['renewable_mode'] == 'fixed_exogenous', 'hard balance and fixed renewables required')
    require(set(reserve) == {'positive_mw', 'negative_mw', 'primary_mw', 'contingency_mw', 'primary_fraction', 'contingency_eligible'}, 'explicit reserve contract')
    m = build_energy_core(**energy_inputs)
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
        fixed = sum(m.renewable_power[b,t]+m.physical_tie[b,t] for b in m.B)
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
    m.total_cost.set_value(m.da_thermal_cost+m.renewable_bid_cost+m.renewable_curtailment_cost+m.network_penalty_cost+m.balance_penalty_cost)
    for g in m.G:
        for t in m.T:
            m.online_min[g,t].set_value(m.da_min[g,t])
            m.online_max[g,t].set_value(m.da_max[g,t])
    m._energy_scope.update(transition_model='finite_research_candidate_paths',startup_cost_model='uniform_event_plus_explicit_curve',terminal_policy='complete_curves_carry_residence',mode='SCUC')
    m._da_inputs = deepcopy(dict(contract=contract,candidates=candidates,reserve=reserve,energy_inputs=energy_inputs))
    m._da_input_hash = digest(m._da_inputs)
    m._da_paths = paths
    m._da_scope = dict(kind='finite_candidate_path_DA_research', official_DA98=False,
        storage_activity=False, agc_awards='explicit_zero_subset', execution_authority=False,
        curve_source='synthetic_research', requested_mip_rel_gap=.001)
    assert_linear_energy_model(m)
    m._da_structure_hash = _fingerprint(m, include_values=False)
    _BUILT[m] = m._da_structure_hash
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
        rows.append((c.name,str(c.lower),str(c.upper),r.constant,[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    r = generate_standard_repn(m.objective.expr)
    rows.append(('objective',r.constant,[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    return digest((m._da_inputs,m._da_input_hash,m._da_paths,m._da_scope,rows,[(v.name,v.value if include_values or v.fixed else None,v.fixed,str(v.domain),v.lb,v.ub) for v in m.component_data_objects(pyo.Var)]))


def solve_scuc(m, *, time_limit_s, threads=1):
    from .gd_solve import solve_dispatch
    require(hasattr(m,'_da_inputs') and digest(m._da_inputs)==m._da_input_hash, 'DA inputs modified')
    require(m in _BUILT and _fingerprint(m, include_values=False)==_BUILT[m], 'DA model structure modified before solve')
    _native_solver_identity(solve_dispatch)
    _FAILED.pop(m,None); _ACCEPTED.pop(m,None)
    attempt_id=str(uuid4()); attempted_at=datetime.now(timezone.utc).isoformat()
    from .highs_solver import HighsNoIncumbentError
    try:
        result = solve_dispatch(m,stage='R11_DA_CANDIDATE_SCUC',time_limit_s=time_limit_s,threads=threads,mip_rel_gap=.001)
    except HighsNoIncumbentError as exc:
        record=exc.failure_record
        require(record['native_path_intact'] is True, 'native failure provenance is not intact')
        _FAILED[m]=dict(schema='gd.research.da_candidate.failure.v1',attempt_id=attempt_id,attempted_at_utc=attempted_at,
            input_sha256=m._da_input_hash,physical_model_sha256=_BUILT[m],
            experiment_id=m._da_inputs['contract']['experiment_id'],
            worker_failure=record,accepted_primal=False,execution_authority=False,
            effective_plan_issued=False,official_price=None,settlement_cash=None,
            full_market_completed=False)
        raise
    _native_metrics(result, m, lp=False)
    result.metrics.update(attempt_id=attempt_id,attempted_at_utc=attempted_at)
    require(result.metrics['requested_gap_met'], 'SCUC gap target not met')
    primal_check(m)
    _ACCEPTED[m] = (_fingerprint(m), deepcopy(result.metrics))
    return result


def build_fixed_selection_sced(m):
    require(m in _ACCEPTED, 'live internally accepted SCUC required; report or caller choices insufficient')
    fingerprint, metrics = _ACCEPTED[m]
    require(_fingerprint(m)==fingerprint, 'accepted SCUC changed')
    clone = m.clone()
    for key in clone.PATH: clone.choose[key].fix(round(pyo.value(m.choose[key])))
    clone._da_scuc_metrics = deepcopy(metrics)
    clone._da_scope['kind'] = 'fixed_accepted_candidate_DA_SCED_research'
    assert_linear_energy_model(clone,require_lp=True)
    return clone


def solve_fixed_selection_sced(m, *, time_limit_s, threads=1):
    """Internally freeze this live accepted SCUC selection, then solve the LP."""
    from .gd_solve import solve_dispatch
    fixed = build_fixed_selection_sced(m)
    _native_solver_identity(solve_dispatch)
    result = solve_dispatch(fixed, stage='R11_DA_FIXED_SELECTION_SCED',
        time_limit_s=time_limit_s, threads=threads, mip_rel_gap=.001)
    _native_metrics(result, fixed, lp=True)
    primal_check(fixed)
    # Keep original integer bound/gap; LP optimum never upgrades the MIP claim.
    result.metrics['original_scuc_metrics'] = deepcopy(fixed._da_scuc_metrics)
    result.metrics['executable_plan_certified'] = False
    result.metrics['network_slack_max_mw'] = max([0.]+[pyo.value(v) for block in
        (fixed.line_over,fixed.line_under,fixed.section_over,fixed.section_under) for v in block.values()])
    _ACCEPTED_SCED[fixed] = (_fingerprint(fixed), deepcopy(result.metrics))
    return result


def accepted_da_report(solution):
    """Read-only report, not an effective plan, command, observation or receipt."""
    m=solution.model
    require(m in _ACCEPTED_SCED, 'live internally solved fixed-selection SCED required')
    fingerprint, metrics = _ACCEPTED_SCED[m]
    require(_fingerprint(m)==fingerprint and solution.metrics==metrics, 'accepted SCED/metrics changed')
    selected={g:next(k[1] for k in m.PATH if k[0]==g and round(pyo.value(m.choose[k]))==1) for g in m.G}
    ts=tuple(m.T); c=m._da_inputs['contract']; beginning=stamp(c['boundary_at']); day=stamp(c['day_start'])
    cells=[]
    for i,t in enumerate(ts):
        at=beginning+timedelta(minutes=5*i)
        cells.append(dict(start=at.isoformat(),end=(at+timedelta(minutes=5)).isoformat(),
            thermal={g:dict(phase=m._da_paths[g,selected[g]]['cells'][i]['phase'],
                mean_mw=pyo.value(m.power[g,t]),end_mw=pyo.value(m.end_power[g,t]),
                connected=int(round(pyo.value(m.online[g,t]))),stable=int(round(pyo.value(m.stable[g,t])))) for g in m.G}))
    delivery=[r for r in cells if day<=stamp(r['start'])<day+timedelta(days=1)]
    require(len(delivery)==288, 'DA report requires288 delivery cells')
    quarters=[dict(start=delivery[i]['start'],thermal_mean_mw={g:sum(delivery[i+j]['thermal'][g]['mean_mw'] for j in range(3))/3 for g in m.G}) for i in range(0,288,3)]
    terminal={g:dict(m._da_paths[g,selected[g]]['terminal'],power_mw=pyo.value(m.end_power[g,ts[-1]])) for g in m.G}
    return dict(schema='gd.research.da_candidate.report.v1',input_sha256=m._da_input_hash,
        experiment_id=c['experiment_id'],selected_paths=selected,physical_cells=cells,
        delivery_quarters=quarters,terminal=terminal,metrics=deepcopy(metrics),
        official_DA98=False,issued_effective_plan=False,execution_authority=False,
        official_price=None,settlement_cash=None,full_market_completed=False)


def _native_solver_identity(solver):
    from .gd_solve import _ORIGINAL_SOLVE_DISPATCH
    from .highs_solver import native_solver_path_intact
    require(solver is _ORIGINAL_SOLVE_DISPATCH and native_solver_path_intact(), 'original native solve_dispatch identity required')


def _native_metrics(result, model, *, lp):
    from .gd_solve import DispatchSolution
    require(type(result) is DispatchSolution and result.model is model, 'native typed result/model mismatch')
    a=result.metrics
    require(a['backend']=='highs' and a['termination'] in (('optimal',) if lp else ('optimal','maxTimeLimit')), 'native HiGHS termination required')
    require(a['requested_mip_rel_gap']==.001 and a['requested_gap_met'] and
        0 <= a['canonical_relative_gap'] <= .001+1e-9 and a['objective_offset_yuan']==0., 'native canonical gap required')
    require(a['solver_version'] is not None and a['native_status'] is not None and a['solver_settings'] is not None, 'native solver provenance missing')
    require(math.isfinite(a['objective_yuan']) and abs(a['objective_yuan']-pyo.value(model.objective)) <= 1e-4+1e-9*max(1.,abs(a['objective_yuan'])), 'native objective/primal mismatch')
    if lp: require(a['integer_variables']==0, 'fixed selection must be continuous LP')


def native_failure_report(m):
    """Preserve actual verified worker failure; never parse exception strings."""
    require(m in _FAILED and m in _BUILT, 'recorded native failure required')
    require(_fingerprint(m,include_values=False)==_BUILT[m], 'failed model mathematics changed')
    return deepcopy(_FAILED[m])
