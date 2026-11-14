"""Independent, optimizer-free arithmetic checks. No candidate audit is reused."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path


def demand(ok, why):
    if not ok:
        raise ValueError(why)


def number(z):
    demand(type(z) in (float, int) and math.isfinite(z), 'finite real number required')
    return z


def bound(z):
    return None if z is None else number(z)


def file_hash(path):
    with Path(path).open('rb') as stream:
        h = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            demand(k not in out, 'duplicate JSON key')
            out[k] = v
        return out
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda z: demand(False, 'nonfinite JSON'))


def audit_frozen_files(accepted, candidate, restore_record):
    manifest = read_json(restore_record)['manifest']['files']
    demand(len(manifest) == 878, 'expected 878 inherited files')
    checks = []
    for item in manifest:
        rel = item['path']
        for root in (Path(accepted), Path(candidate)):
            path = root / rel
            demand(path.is_file() and path.resolve().is_relative_to(root.resolve()), 'missing/outside inherited file: ' + rel)
            demand(path.stat().st_size == item['bytes'] and file_hash(path) == item['sha256'], 'changed inherited file: ' + str(path))
        checks.append(rel)
    expected = set(checks)
    added = sorted(str(p.relative_to(candidate)) for p in Path(candidate).rglob('*') if p.is_file() and str(p.relative_to(candidate)) not in expected)
    return dict(inherited_files_verified=len(checks), inherited_bytes=sum(f['bytes'] for f in manifest), unchanged=True, added_files=added)


def audit_lp(request, response, names):
    """All bounds, rows, objective, dual feasibility, complementarity and gap."""
    demand(response.get('backend') == 'highs' and response.get('solver_version') == '1.15.1', 'native solver identity')
    demand(response.get('status') == 'optimal' and response.get('native_status') == 'Optimal', 'optimal native status required')
    demand(response.get('has_primal') is True and response.get('dual_valid') is True, 'complete optimal LP witness required')
    opts = request['options']; settings = response['native_settings']
    demand(opts['threads'] == settings['threads'] == 1 and opts['mip_rel_gap'] == settings['mip_rel_gap'] == .001 and opts['mip_abs_gap'] == settings['mip_abs_gap'] == 0., 'native parameters changed')
    demand(opts['solve_relaxation'] is False and request['warmstart'] is False, 'no relaxation or imported warm start')
    for key in ('output_flag', 'presolve'):
        demand(settings[key] == opts[key], 'applied option mismatch: ' + key)
    demand(set(names) == {'variables', 'constraints'}, 'names schema')
    variables = request['variables']; rows = request['rows']; obj = request['objective']
    x = response['primal']; dual = response['dual']
    demand(obj['minimize'] is True and all(v['kind'] == 'C' for v in variables), 'continuous minimization required')
    demand(len(x) == len(variables) == len(names['variables']) == len(set(names['variables'])), 'full unique primal/variable names')
    demand(len(dual) == len(rows) == len(names['constraints']) == len(set(names['constraints'])), 'full unique dual/constraint names')
    demand(all(type(n) is str and n for n in names['variables'] + names['constraints']), 'nonempty names')
    for z in x + dual:
        number(z)
    costs = [0.] * len(x)
    def terms(ts):
        for i, c in ts:
            demand(type(i) is int and 0 <= i < len(x), 'sparse index out of range')
            number(c)
        return ts
    for i, c in terms(obj['terms']):
        costs[i] += c
    objective = number(obj['constant']) + math.fsum(c * x[i] for i, c in obj['terms'])
    reported = number(response['objective']); reported_bound = number(response['bound'])
    demand(abs(objective - reported) <= 1e-6, 'objective does not match complete primal')
    demand(abs(reported_bound - reported) <= 1e-6, 'LP reported bound differs')
    primal_max = 0.; complementarity = 0.; dual_objective = obj['constant']; reduced = costs[:]
    records = []
    def limits(value, lo, hi, name, kind):
        nonlocal primal_max
        bound(lo); bound(hi)
        demand(lo is None or hi is None or lo <= hi, 'inverted bounds')
        violation = max(0., 0. if lo is None else lo - value, 0. if hi is None else value - hi)
        primal_max = max(primal_max, violation)
        records.append(dict(kind=kind, name=name, value=value, lower=lo, upper=hi, violation=violation))
    for z, v, name in zip(x, variables, names['variables']):
        limits(z, v['lb'], v['ub'], name, 'variable')
    for y, row, name in zip(dual, rows, names['constraints']):
        ax = math.fsum(c * x[i] for i, c in terms(row['terms']))
        limits(ax, row['lb'], row['ub'], name, 'constraint')
        for i, c in row['terms']:
            reduced[i] -= y * c
        if abs(y) > 1e-9:
            active = row['lb'] if y > 0 else row['ub']
            demand(active is not None, 'dual sign uses absent row bound')
            dual_objective += y * active
            complementarity = max(complementarity, abs(y * (ax - active)))
    for z, r, v in zip(x, reduced, variables):
        if abs(r) > 1e-8:
            active = v['lb'] if r > 0 else v['ub']
            demand(active is not None, 'reduced cost sign uses absent variable bound')
            dual_objective += r * active
            complementarity = max(complementarity, abs(r * (z - active)))
    gap = abs(objective - dual_objective)
    tol = 1e-5 + 1e-7 * max(1., abs(objective))
    demand(primal_max <= 1e-6, 'full primal residual exceeds absolute 1e-6')
    demand(gap <= tol and complementarity <= tol, 'dual optimality/strong-duality failure')
    return dict(passed=True, variables=len(variables), rows=len(rows), objective=objective,
                maximum_primal_violation=primal_max, dual_objective=dual_objective,
                strong_duality_gap=gap, maximum_complementarity=complementarity, optimality_tolerance=tol,
                residual_records=records)


def audit_physics(inp, response, names):
    """Independent physical/economic evaluation from named native primal only.

    DA physical cells supply fixed phases/modes and target, never fresh output.
    No optimization, receipt, command issuance, or source mutation occurs here.
    """
    from datetime import datetime, timedelta
    values = dict(zip(names['variables'], response['primal']))
    demand(len(values) == len(response['primal']), 'complete unique named primal')
    R = inp['resources']; J = range(24); dt = 1/12; tolerance = 1e-6
    initial = inp['projection']['state']; powers = {}; battery_ends = {}; reserves = {}
    max_balance = 0.; max_flow = 0.; offer_cost = 0.; reconstructed = []
    def close(a,b,reason):
        demand(abs(number(a)-number(b)) <= tolerance, reason)
    for r,u in R.items():
        powers[r] = [number(values[f'power[{r},{j}]']) for j in J]
        old = initial['batteries' if u['kind']=='battery' else 'thermal'][r]
        prev = old['power_mw']
        if u['kind']=='battery':
            energy = number(values[f'energy[{r},0]'])
            close(energy,old['energy_mwh'],'physical initial SOC')
            used = deepcopy(old['daily_used_internal_mwh']); battery_ends[r] = [energy]
        for j,row in enumerate(u['rows']):
            p = powers[r][j]
            demand(row['min_mw']-tolerance <= p <= row['max_mw']+tolerance, 'physical source power bounds')
            if u['kind'] != 'battery' and row['phase'] in ('startup','shutdown'):
                close(p,row['mean_mw'],'transition mean fixed by original curve')
                close(prev,row['begin_mw'],'transition true boundary')
                prev=row['end_mw']
            else:
                demand(-row['ramp_down_mw_h']*dt-tolerance <= p-prev <= row['ramp_up_mw_h']*dt+tolerance, 'physical signed ramp')
                prev=p
            if u['kind']=='battery':
                mode=row['mode']; demand(mode==inp['da_cells'][j]['batteries'][r]['mode'],'original fixed battery mode')
                demand((mode=='idle' and abs(p)<=tolerance) or (mode=='charge' and p<=tolerance) or (mode=='discharge' and p>=-tolerance),'battery signed mode')
                demand(row['reserve_eligible'] is False and all(row[s+'_cap_mw']==0 for s in ('positive','negative','primary','accident')),'battery reserve forbidden')
                coeff = u['eta_charge'] if mode=='charge' else 1/u['eta_discharge']
                energy -= coeff*p*dt
                close(energy, values[f'energy[{r},{j+1}]'],'physical SOC recursion')
                demand(u['energy_min_mwh']-tolerance <= energy <= u['energy_max_mwh']+tolerance,'physical SOC bounds')
                battery_ends[r].append(energy)
                date=inp['da_cells'][j]['start'][:10]
                used[date]=used.get(date,0.)+(-u['eta_charge'] if mode=='charge' else 1/u['eta_discharge'])*p*dt
                demand(used[date]<=u['cycle_budget'][date]['limit_internal_mwh']+tolerance,'physical dated throughput')
                curve=u['original_curves']['charge' if mode=='charge' else 'discharge'];sign=-1 if mode=='charge' else 1
            else:
                curve=u['original_offer'];sign=1
                reserves.setdefault(r,{s:[] for s in ('positive','negative','primary','accident')})
                for s in reserves[r]:
                    q=number(values[f'reserve[{r},{j},{s}]']);reserves[r][s].append(q)
                    demand(-tolerance<=q<=row[s+'_cap_mw']+tolerance,'physical reserve cap')
                    demand(row['reserve_eligible'] or abs(q)<=tolerance,'unqualified thermal reserve')
                    room=p-row['min_mw'] if s=='negative' else row['max_mw']-p
                    demand(q<=room+tolerance,'reserve headroom')
            offer_cost += sign*math.fsum(max(0.,min(abs(p),c['p_end'])-c['p_start'])*c['price'] for c in curve)*dt
        if u['kind']=='battery':
            close(energy,u['terminal_energy_mwh'],'hard DA two-hour terminal target')
            close(energy,inp['da_cells'][-1]['batteries'][r]['energy_end_mwh'],'original DA physical terminal energy')
    for j in J:
        req=inp['reserve'][j]
        demand(req['positive_mw']==req['accident_mw']==400. and req['negative_mw']==req['primary_mw']==0.,'original reserve stress')
        for s in ('positive','negative','primary','accident'):
            demand(math.fsum(reserves[g][s][j] for g in reserves)+tolerance>=req[s+'_mw'],'system reserve insufficient')
        injection={n:math.fsum(powers[r][j] for r in R if R[r]['node']==n)+inp['fixed_injection_mw'][n][j]-inp['load_mw'][n][j] for n in inp['nodes']}
        imbalance=abs(math.fsum(injection.values()));max_balance=max(max_balance,imbalance)
        demand(imbalance<=tolerance,'physical nodal balance')
        for l,line in inp['network'].items():
            flow=math.fsum(line['ptdf'][n]*injection[n] for n in inp['nodes'])
            violation=max(0.,line['lower_mw'][j]-flow,flow-line['upper_mw'][j]);max_flow=max(max_flow,violation)
            demand(violation<=tolerance,'physical PTDF line limit')
        cell=deepcopy(inp['da_cells'][j])
        for r,u in R.items():
            p=powers[r][j]
            if u['kind']=='battery':
                old=cell['batteries'][r];old.update(power_mw=p, charge_mw=max(0.,-p),discharge_mw=max(0.,p),energy_begin_mwh=battery_ends[r][j],energy_end_mwh=battery_ends[r][j+1])
            else:
                old=cell['thermal'][r]
                if old['phase'] not in ('startup','shutdown'):
                    old.update(begin_mw=initial['thermal'][r]['power_mw'] if j==0 else reconstructed[-1]['thermal'][r]['end_mw'],mean_mw=p,end_mw=p)
        reconstructed.append(cell)
    close(offer_cost,response['objective'],'objective versus direct original offer integration')
    return dict(passed=True, cells=24, power_variables=len(R)*24, max_balance_residual=max_balance,
                max_PTDF_violation=max_flow, direct_original_offer_cost=offer_cost,
                reconstructed_cells=reconstructed, reconstructed_first_three=reconstructed[:3],
                original_reserve_mw=dict(positive=400.,accident=400.),price_claim=False)
