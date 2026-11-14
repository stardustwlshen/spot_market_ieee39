"""Explicit, two-window R15 preflight and pure RT model construction.

W0 may be prepared from the reverified archived seed. W1 is deliberately
blocked until a future reviewed native adapter accepts a genuinely fresh W0
prefix. There is no adapter, callback escape hatch, or native release in this
pure-code phase. A dry plan is not an accepted RT result or issued dispatch.
"""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import uuid4
from weakref import WeakKeyDictionary

from .gd_r15_da_rt_bridge import (SCOPES, DT, TOL, require, close, digest, stamp,
    integrate_cell, validate_bridge, bridge_sources)

NATIVE_MIN_FREE_BYTES = 1024 ** 3
WINDOW_POLICY = dict(indices=[0, 1], cells_per_window=24, issued_cells=3,
    cutoff_lead_minutes=15, stride_minutes=15,
    storage_terminal='exact R14 physical DA energy at the two-hour horizon end',
    terminal_scope='research target; not official E98 or a full-day continuity certificate',
    freeze_thermal_states=True, freeze_D_day_battery_modes=True,
    positive_reserve_mw=400., accident_reserve_mw=400.,
    wind_solar_unqualified_storage_reserve_mw=0.,
    stop_policy='after W1 delivery starts: 6 RT cells issued, only 3 RT cells executed; optional drain to 00:30 executes6',
    native_release_available=False)
_SESSIONS = WeakKeyDictionary()
_TICKETS = WeakKeyDictionary()


class TwoWindowSession:
    __slots__ = ('__weakref__',)


class PreparedWindow:
    __slots__ = ('__weakref__',)


def window_specs(plan, policy=None):
    policy = deepcopy(WINDOW_POLICY if policy is None else policy)
    require(policy == WINDOW_POLICY, 'exact predeclared two-window policy required')
    day = stamp(plan['day_start'])
    require(day.hour == day.minute == day.second == day.microsecond == 0, 'Shanghai midnight day boundary required')
    return [dict(index=i, cutoff=(day+timedelta(minutes=15*i-15)).isoformat(),
                 start=(day+timedelta(minutes=15*i)).isoformat(),
                 delivery_end=(day+timedelta(minutes=15*(i+1))).isoformat(),
                 horizon_end=(day+timedelta(minutes=15*i+120)).isoformat(),
                 physical_cells=24, issued_cells=3) for i in (0, 1)]


def project_issued_prefix(receipt, commands, *, cutoff, start, technical):
    """Arithmetic only. Inputs do not create an accepted simulation receipt."""
    cutoff, start = stamp(cutoff), stamp(start)
    require(start-cutoff == timedelta(minutes=15), 'T-15 projection required')
    require(stamp(receipt['at']) <= stamp(receipt['available_at']) <= cutoff, 'future receipt or invalid availability forbidden')
    require(receipt['state']['at'] == receipt['at'], 'receipt/state timestamp mismatch')
    require(receipt['is_measurement'] is False, 'this research protocol cannot claim telemetry')
    state = deepcopy(receipt['state'])
    used = []
    require(len({c['id'] for c in commands}) == len(commands), 'duplicate command IDs')
    while stamp(state['at']) < start:
        matches = [c for c in commands if stamp(c['cell']['start']) == stamp(state['at'])
                   and stamp(c['issued_at']) <= cutoff and stamp(c['available_at']) <= cutoff]
        require(len(matches) == 1, 'missing/overlapping cutoff-available issued prefix')
        command = matches[0]
        require(stamp(command['issued_at']) <= stamp(command['available_at']) <= cutoff
                and stamp(command['issued_at']) <= stamp(command['cell']['start'])
                and stamp(command['cell']['end']) <= start, 'command chronology or projection overshoot')
        state = integrate_cell(state, command['cell'], technical)
        used.append(dict(id=command['id'], sha256=digest(command)))
    require(stamp(state['at']) == start, 'projected endpoint mismatch')
    return dict(cutoff=cutoff.isoformat(), start=start.isoformat(), state=state,
                receipt_sha256=digest(receipt), command_refs=used,
                is_measurement=False, future_actual_used=False,
                hypothesis='synthetic zero tracking of already-issued research commands')


def initialize_two_windows(plan, permit, *, policy=None):
    specs = window_specs(plan, policy)
    validate_bridge(plan, permit, cutoff=specs[0]['cutoff'], recheck_files=True)
    prep = plan['initial_preparation']
    seed = prep['seed_state']
    require(seed['at'] == specs[0]['cutoff'], 'fresh chain must start at exact preparation seed cutoff')
    sid = 'R15-RT-' + uuid4().hex
    receipt = dict(id=sid+'-SEED', at=seed['at'], available_at=seed['at'],
        state=deepcopy(seed), is_measurement=False,
        kind='new_synthetic_seed_from_reverified_archived_preparation',
        publication_transaction_id=plan['transaction_id'])
    commands = [dict(id=sid+'-PREP-'+str(i), issued_at=seed['at'], available_at=seed['at'],
        cell=deepcopy(cell), kind='new_synthetic_preparation_command',
        source_bindings_sha256=digest(plan['preparation_source_bindings'])) for i, cell in enumerate(prep['cells'])]
    projected = project_issued_prefix(receipt, commands, cutoff=specs[0]['cutoff'], start=specs[0]['start'], technical=plan['technical'])
    require(digest(projected['state']) == digest(prep['midnight_state']), 'new preparation projection differs from verified midnight state')
    session = TwoWindowSession()
    _SESSIONS[session] = dict(id=sid, plan=deepcopy(plan), permit=permit,
        specs=specs, receipt=receipt, commands=commands, clock=specs[0]['cutoff'],
        accepted_windows=[], next_window=0, prepared=None)
    return session


def _session(session):
    require(type(session) is TwoWindowSession and session in _SESSIONS, 'fresh R15 two-window session required')
    entry = _SESSIONS[session]
    validate_bridge(entry['plan'], entry['permit'])
    return entry


def session_report(session):
    s = _session(session)
    return dict(transaction_id=s['id'], publication_transaction_id=s['plan']['transaction_id'],
        clock=s['clock'], next_window=s['next_window'],
        intended_windows=deepcopy(s['specs']), accepted_RT_windows=len(s['accepted_windows']),
        preparation_commands=len(s['commands']), issued_RT_cells=0, executed_RT_cells=0,
        new_native_calls=0, research_only=True, native_release_available=False)


def _forecast(source, spec):
    """Fresh labeled vintage from frozen prior, never future RT/actual input."""
    from .gd_october_research_forecast import prior_value
    cutoff, start = stamp(spec['cutoff']), stamp(spec['start'])
    nodes = source['energy_inputs']['buses']
    families, evidence = {}, {}
    for family in ('load_mw', 'wind_mw', 'solar_mw', 'boundary_tie_mw'):
        saved = source['contract']['sources']['prior:'+family]
        require(sha256(saved['artifact']).hexdigest() == saved['sha256'], 'forecast prior bytes changed')
        prior = json.loads(saved['artifact'])
        require(prior['uses_future_actual'] is False and stamp(saved['available_at']) <= cutoff
                and stamp(prior['available_at']) <= cutoff
                and stamp(prior['information_end']) <= cutoff, 'future prior or information end forbidden')
        knots = [{r: prior_value(prior, r, start+timedelta(minutes=15*j)) for r in prior['resources']} for j in range(9)]
        values = {n: [0.]*24 for n in nodes}
        for t in range(24):
            q, k = divmod(t, 3)
            for r, info in prior['resources'].items():
                values[info['bus']][t] += knots[q][r]+(knots[q+1][r]-knots[q][r])*(k+.5)/3
        families[family] = values
        evidence[family] = dict(prior_sha256=saved['sha256'], issued_at=spec['cutoff'],
            available_at=spec['cutoff'], source_available_at=saved['available_at'],
            information_end=prior['information_end'], uses_future_actual=False,
            is_measurement=False, method='frozen prior, linear15-minute knots to5-minute interval means')
    return families, evidence


def validate_window_input(inp):
    require(inp['schema'] == 'gd.research.r15.two_window_input.v1', 'R15 input schema')
    require(inp['policy'] == WINDOW_POLICY and inp['scopes'] == SCOPES, 'window policy/scope changed')
    spec = inp['window']
    require(type(spec['index']) is int and spec['index'] in (0, 1), 'only intended first two windows')
    start, cutoff = stamp(spec['start']), stamp(spec['cutoff'])
    day = stamp(inp['day_start'])
    require(start == day+timedelta(minutes=15*spec['index']) and cutoff == start-timedelta(minutes=15), 'window index/start/cutoff mismatch')
    require(stamp(spec['horizon_end']) == start+timedelta(hours=2)
            and stamp(spec['delivery_end']) == start+timedelta(minutes=15)
            and spec['physical_cells'] == 24 and spec['issued_cells'] == 3, '24-cell horizon/3-cell delivery required')
    require(inp['projection']['state']['at'] == spec['start'] and inp['projection']['cutoff'] == spec['cutoff'], 'initial projection clock mismatch')
    require(len(inp['da_cells']) == 24, 'missing 24-cell physical coverage')
    for j, row in enumerate(inp['da_cells']):
        require(stamp(row['start']) == start+timedelta(minutes=5*j)
                and stamp(row['end']) == start+timedelta(minutes=5*(j+1)), 'noncontiguous physical window')
    require(stamp(inp['publication_at']) <= cutoff, 'future publication')
    for v in inp['vintages'].values():
        require(v['uses_future_actual'] is False and v['is_measurement'] is False
                and stamp(v['source_available_at']) <= stamp(v['issued_at']) <= cutoff
                and stamp(v['available_at']) <= cutoff and stamp(v['information_end']) <= cutoff,
                'future forecast vintage')
    for r, u in inp['resources'].items():
        require(len(u['rows']) == 24, 'resource row shape')
        for row in u['rows']:
            if u['kind'] == 'battery':
                require(row['reserve_eligible'] is False and all(row[k+'_cap_mw'] == 0 for k in ('positive', 'negative', 'primary', 'accident')), 'unqualified storage reserve')
        if u['kind'] == 'battery':
            initial = inp['projection']['state']['batteries'][r]
            require(u['energy_min_mwh'] <= initial['energy_mwh'] <= u['energy_max_mwh'], 'initial battery SOC outside bounds')
            close(u['terminal_energy_mwh'], inp['da_cells'][-1]['batteries'][r]['energy_end_mwh'], 'window terminal target not bound to new R14 DA')
    for req in inp['reserve']:
        require(req['positive_mw'] == req['accident_mw'] == 400.
                and req['negative_mw'] == req['primary_mw'] == 0., 'original reserve stress changed')
    require(len(inp['reserve']) == 24, 'reserve shape')
    for family in ('load_mw', 'fixed_injection_mw'):
        require(set(inp[family]) == set(inp['nodes']) and all(len(v) == 24 for v in inp[family].values()), 'network injection shape')
    return True


def prepare_window(session, index=0):
    s = _session(session)
    require(type(index) is int and index in (0, 1), 'only two intended windows may be prepared')
    require(index == s['next_window'], 'W1 pending genuinely fresh accepted W0 issued prefix; no DA/old result substitution')
    require(s['prepared'] is None, 'window already prepared; no duplicate transaction')
    plan, permit, spec = s['plan'], s['permit'], s['specs'][index]
    validate_bridge(plan, permit, cutoff=spec['cutoff'], recheck_files=True)
    require(s['clock'] == spec['cutoff'], 'window cannot be backdated or prepared at future clock')
    projection = project_issued_prefix(s['receipt'], s['commands'], cutoff=spec['cutoff'], start=spec['start'], technical=plan['technical'])
    source, data = bridge_sources(plan, permit)
    families, vintages = _forecast(source, spec)
    first = 3*index
    cells = deepcopy(plan['physical_cells'][first:first+24])
    resources = {}
    for r, u in data['resources'].items():
        resources[r] = {k: deepcopy(v) for k, v in u.items() if k != 'rows'}
        resources[r]['rows'] = deepcopy(data['physical_lift']['resources'][r]['rows'][first:first+24])
        if u['kind'] == 'battery':
            resources[r]['terminal_energy_mwh'] = cells[-1]['batteries'][r]['energy_end_mwh']
            resources[r]['cycle_budget'] = deepcopy(data['physical_lift']['resources'][r]['cycle_budget'])
    inp = dict(schema='gd.research.r15.two_window_input.v1', transaction_id=s['id']+'-W'+str(index),
        publication_transaction_id=plan['transaction_id'], publication_sha256=digest(plan),
        day_start=plan['day_start'], publication_at=plan['publication_at'], window=deepcopy(spec),
        policy=deepcopy(WINDOW_POLICY), scopes=deepcopy(SCOPES), projection=projection,
        da_cells=cells, technical=deepcopy(plan['technical']), vintages=vintages,
        nodes=deepcopy(data['nodes']), resources=resources,
        reserve=deepcopy(data['physical_lift']['reserve'][first:first+24]),
        load_mw=families['load_mw'], fixed_injection_mw={n: [sum(families[f][n][t] for f in ('wind_mw','solar_mw','boundary_tie_mw')) for t in range(24)] for n in data['nodes']},
        network={l: dict(ptdf=deepcopy(row['ptdf']),
            lower_mw=data['physical_lift']['network'][l]['lower_mw'][first:first+24],
            upper_mw=data['physical_lift']['network'][l]['upper_mw'][first:first+24]) for l, row in data['network'].items()},
        notes=['physical fixed ties retained; no interregional market',
               'dispatch from future optimizer only, never DA replay',
               'fixed startup/noload costs are constants excluded from this research LP objective'])
    validate_window_input(inp)
    ticket = PreparedWindow()
    _TICKETS[ticket] = dict(session=session, input=deepcopy(inp), input_sha256=digest(inp), model=None, consumed=False)
    s['prepared'] = ticket
    return inp, ticket


def _ticket(inp, ticket):
    require(type(ticket) is PreparedWindow and ticket in _TICKETS, 'fresh internally prepared R15 window required')
    entry = _TICKETS[ticket]
    require(digest(inp) == entry['input_sha256'], 'window input changed after preparation')
    s = _session(entry['session'])
    require(s['prepared'] is ticket and not entry['consumed'], 'stale or consumed window ticket')
    validate_bridge(s['plan'], s['permit'], cutoff=inp['window']['cutoff'], recheck_files=True)
    validate_window_input(inp)
    return entry


def build_rt_model(inp, ticket):
    """Construct a fresh 24-cell continuous fixed-state RT LP; no solve call."""
    entry = _ticket(inp, ticket)
    require(entry['model'] is None, 'one model per prepared transaction')
    import pyomo.environ as p
    m = p.ConcreteModel('R15_two_window_research_RT')
    R = list(inp['resources']); B = [r for r in R if inp['resources'][r]['kind']=='battery']; G = [r for r in R if r not in B]
    N = inp['nodes']; L = list(inp['network']); J = range(24)
    m.zero = p.Var(bounds=(0., 0.)); m.zero.fix(0.)
    m.power = p.Var(R, J); m.energy = p.Var(B, range(25))
    m.reserve = p.Var(G, J, ('positive','negative','primary','accident'), domain=p.NonNegativeReals)
    m.rows = p.ConstraintList()
    for r, u in inp['resources'].items():
        initial = inp['projection']['state']['batteries' if r in B else 'thermal'][r]
        previous = initial['power_mw']
        if r in B:
            m.energy[r,0].fix(initial['energy_mwh'])
        for j, row in enumerate(u['rows']):
            v = m.power[r,j]; v.setlb(row['min_mw']); v.setub(row['max_mw'])
            if r in G and row['phase'] in ('startup','shutdown'):
                require(row['event_id'] and row['curve_source'], 'transition evidence missing')
                close(row['mean_mw'], inp['da_cells'][j]['thermal'][r]['mean_mw'], 'bound transition mean changed')
                m.rows.add(previous+m.zero == row['begin_mw']); previous = row['end_mw']
            else:
                m.rows.add(v-previous <= row['ramp_up_mw_h']*DT)
                m.rows.add(previous-v <= row['ramp_down_mw_h']*DT); previous = v
            if r in G:
                for service in ('positive','negative','primary','accident'):
                    cap = row[service+'_cap_mw']
                    require(row['reserve_eligible'] or cap == 0, 'qualified thermal reserves only')
                    reserve = m.reserve[r,j,service]; reserve.setub(cap)
                    m.rows.add(reserve <= v-row['min_mw'] if service=='negative' else reserve <= row['max_mw']-v)
            else:
                mode = row['mode']
                require(mode == inp['da_cells'][j]['batteries'][r]['mode'], 'published storage mode mismatch')
                coef = u['eta_charge'] if mode=='charge' else 1/u['eta_discharge']
                m.energy[r,j+1].setlb(u['energy_min_mwh']); m.energy[r,j+1].setub(u['energy_max_mwh'])
                m.rows.add(m.energy[r,j+1] == m.energy[r,j]-coef*v*DT)
        if r in B:
            m.rows.add(m.energy[r,24] == u['terminal_energy_mwh'])
            for date in {c['start'][:10] for c in inp['da_cells']}:
                budget = u['cycle_budget'][date]
                used = initial['daily_used_internal_mwh'].get(date, 0.)
                terms = [(-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge'])*m.power[r,j]*DT for j in J if inp['da_cells'][j]['start'][:10]==date]
                m.rows.add(m.zero+sum(terms) <= budget['limit_internal_mwh']-used)
    m.injection = p.Expression(N, J, rule=lambda _,n,j: m.zero+sum(m.power[r,j] for r in R if inp['resources'][r]['node']==n)+inp['fixed_injection_mw'][n][j]-inp['load_mw'][n][j])
    m.balance = p.Constraint(J, rule=lambda _,j: sum(m.injection[n,j] for n in N)==0)
    m.flow = p.Expression(L, J, rule=lambda _,l,j: sum(inp['network'][l]['ptdf'][n]*m.injection[n,j] for n in N))
    m.line_upper = p.Constraint(L, J, rule=lambda _,l,j: m.flow[l,j] <= inp['network'][l]['upper_mw'][j])
    m.line_lower = p.Constraint(L, J, rule=lambda _,l,j: m.flow[l,j] >= inp['network'][l]['lower_mw'][j])
    for j, req in enumerate(inp['reserve']):
        for service in ('positive','negative','primary','accident'):
            m.rows.add(m.zero+sum(m.reserve[g,j,service] for g in G) >= req[service+'_mw'])
    # The unchanged convex source offers are evaluated at every physical cell.
    keys, quotes = [], {}
    for r, u in inp['resources'].items():
        for j, row in enumerate(u['rows']):
            mode = row.get('mode')
            curve = u['original_offer'] if r in G else u['original_curves']['charge' if mode=='charge' else 'discharge']
            quotes[r,j] = curve
            keys.extend((r,j,k) for k in range(len(curve)))
    m.segment = p.Var(keys, domain=p.NonNegativeReals)
    cost = 0.
    for (r,j), curve in quotes.items():
        sign = -1 if inp['resources'][r]['rows'][j].get('mode')=='charge' else 1
        m.rows.add(sign*m.power[r,j] == sum(m.segment[r,j,k] for k in range(len(curve))))
        for k, row in enumerate(curve):
            m.segment[r,j,k].setub(row['p_end']-row['p_start'])
            cost += sign*row['price']*m.segment[r,j,k]*DT
    m.objective = p.Objective(expr=cost)
    entry['model'] = m
    return m, dict(RT_input_prepared=True, RT_model_built=True, RT_solved=False,
        variables=sum(1 for _ in m.component_data_objects(p.Var)),
        constraints=sum(1 for _ in m.component_data_objects(p.Constraint, active=True)),
        new_native_calls=0, integer_variables=0, official_market=False)


def native_preflight(inp, ticket, output_directory):
    """Inspectable blockers; neither enough space nor a caller flag releases."""
    _ticket(inp, ticket)
    path = Path(output_directory).resolve()
    require(path.is_dir(), 'existing dedicated output directory required')
    free = shutil.disk_usage(path).free
    blockers = ['native execution disabled in R15 pure-code phase',
                'future fresh-native acceptance adapter and independent release review required']
    if free < NATIVE_MIN_FREE_BYTES:
        blockers.append('free space below unchanged 1 GiB native floor')
    return dict(allowed=False, blockers=blockers, free_bytes=free,
                minimum_free_bytes=NATIVE_MIN_FREE_BYTES, planned_RT_windows=2,
                planned_RT_LP_calls=2, optional_independent_price_calls=2,
                no_preparation_or_DA_rerun=True, new_native_calls=0)


def execute_native(*args, **kwargs):
    """No release route exists in this phase, even with forged boolean flags."""
    raise RuntimeError('Native release unavailable: pure-code/pure-test phase; no optimizer was called')
