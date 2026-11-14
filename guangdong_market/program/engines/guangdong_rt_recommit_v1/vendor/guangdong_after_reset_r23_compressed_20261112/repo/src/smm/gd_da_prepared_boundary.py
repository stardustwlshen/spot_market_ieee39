"""Independently hypothesized, physically checked R12 preparation boundaries.

No source describes the pre-existing gas state. This module NEVER proves a
prehistory from the original all-gas-off boundary. Preparation and reconstruction
are pure; native calls are explicit. Portable primal/dual proof is mathematical
evidence, not authentication of a historical dispatch, plan or native process.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from weakref import WeakKeyDictionary
import csv
import io
import json
import math

import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn
import yaml

from .gd_da_candidate import digest, require, stamp, _native_solver_identity, _native_metrics
from .gd_da_ieee39 import STATIC_SHA256, raw
from .gd_october_ieee39_scenario import _load_static
from .gd_october_research_forecast import prior_value, METHOD, PRIOR_SCHEMA
from .highs_solver import serialize_model

PROBLEM_SCHEMA = 'gd.research.prepared_boundary.problem.v1'
EQUILIBRIUM_SCHEMA = 'gd.research.prepared_boundary.equilibrium.v1'
REPORT_SCHEMA = 'gd.research.prepared_boundary.report.v1'
IDS = ('C1', 'C2', 'C3', 'CCGT', 'GT')
PRIMAL_TOL = 1e-6
DUAL_TOL = 1e-6
LEX_TOL = 1e-6
BUILT = WeakKeyDictionary()
ACCEPTED = WeakKeyDictionary()
FAILED = WeakKeyDictionary()
_LIVE_PERMITS = WeakKeyDictionary()


class _LivePreparedPermit:
    __slots__ = ('__weakref__',)


def frozen_prepared_hypothesis(day_start='2026-11-11T00:00:00+08:00', *, boundary_kind='prepared_D00_research'):
    day = stamp(day_start)
    require(day.utcoffset() == timedelta(hours=8) and not any((day.hour, day.minute, day.second, day.microsecond)), 'Shanghai midnight required')
    require(boundary_kind in ('prepared_D00_research', 'local_0045_research'), 'separate prepared hypothesis identity required')
    full = boundary_kind == 'prepared_D00_research'
    beginning = day - timedelta(minutes=15) if full else day + timedelta(minutes=45)
    delivery = beginning + timedelta(minutes=15)
    online = {g: int(full or g != 'GT') for g in IDS}
    ages = {g: (48. if g.startswith('C') and g != 'CCGT' else 8. if online[g] else 12.) for g in IDS}
    return dict(schema='gd.research.prepared_boundary.hypothesis.v1', boundary_kind=boundary_kind,
        day_start=day.isoformat(), preparation_boundary_at=beginning.isoformat(), delivery_boundary_at=delivery.isoformat(),
        declared_at=(day-timedelta(days=1)).isoformat(), forecast_issued_at=(day-timedelta(hours=4.5)).isoformat(),
        cutoff=(day-timedelta(hours=4)).isoformat(), planned_publication_at=(day-timedelta(hours=3.5)).isoformat(),
        online=online, age_h=ages, preparation_cells=3, duration_h=1/12,
        independent_hypothesis=True, original_all_gas_off_predecessor=False, historical_preparation_proven=False,
        prior_startup_outside_scope=True, actual_telemetry=False,
        equilibrium_objective='original_stable_energy_bid_integral_one_hour_no_noload',
        equilibrium_lexicographic_order=[g for g in IDS if online[g]],
        cost_tolerance_absolute_yuan=1e-4, cost_tolerance_relative=1e-9,
        lexicographic_pin='exact_previous_optimum', primal_tolerance=PRIMAL_TOL, dual_tolerance=DUAL_TOL,
        preparation_objective='original_stable_energy_bid_integral_three_five_minute_cells_no_noload',
        preparation_policy='all_initial_phases_unchanged_true_boundary_ramp_hard_network',
        storage_policy='original_inventory_idle_off_zero_energy_zero_incremental_reserve',
        reserves=dict(positive_mw=400., negative_mw=0., primary_mw=0., contingency_mw=400.,
            sharing='primary_and_contingency_may_share_headroom', wind_credit=0., solar_credit=0., storage_credit=0., agc=0.),
        missing_evidence=['actual_preexisting_state','historical_startup','manufacturer_certified_curves','operator_qualification','N_minus_1_deliverability'])


def _parse_static(sources):
    sr = {key[7:]:value['artifact'] for key,value in sources.items() if key.startswith('static:')}
    require(set(sr) == set(STATIC_SHA256), 'static inventory')
    require({key:sha256(value).hexdigest() for key,value in sr.items()} == STATIC_SHA256, 'retained original static source bytes changed')
    return sr, dict(base=yaml.safe_load(sr['config/base.yaml']), modes=yaml.safe_load(sr['config/modes.yaml']),
        units=yaml.safe_load(sr['config/units.yaml']), buses=list(csv.DictReader(io.StringIO(sr['data/case39/buses.csv'].decode()))),
        branches=list(csv.DictReader(io.StringIO(sr['data/case39/branches.csv'].decode()))), bids=json.loads(sr['resolved_bids.json']))


def prepare_boundary_problem(root, *, boundary_kind='prepared_D00_research', day_start='2026-11-11T00:00:00+08:00', seed=20261111, generated_at_utc=None):
    """Freeze inputs/objectives without solving or selecting a dispatch."""
    sr, static = _load_static(root)
    return _prepare(sr, static, boundary_kind, day_start, seed, generated_at_utc or datetime.now(timezone.utc).isoformat())


def _prepare(sr, static, kind, day_start, seed, generated):
    require({key:sha256(value).hexdigest() for key,value in sr.items()} == STATIC_SHA256, 'retained original static source bytes changed')
    require(type(seed) is int, 'integer seed required')
    stamp(generated)
    h = frozen_prepared_hypothesis(day_start, boundary_kind=kind)
    sources = {}
    def bind(key, artifact, kind, at):
        sources[key] = dict(artifact=artifact, sha256=sha256(artifact).hexdigest(), kind=kind, available_at=at)
    for key,value in sr.items(): bind('static:'+key, value, 'retained_static', h['declared_at'])
    bind('hypothesis', raw(h), 'research_boundary_assumption', h['declared_at'])
    u = static['units']; buses = tuple(str(b['bus']) for b in static['buses'])
    resources = {'load_mw':{f'load:{b["bus"]}':dict(bus=str(b['bus']), scale_mw=float(b['load_mw'])*float(static['base']['load_scale'])) for b in static['buses']},
        'wind_mw':{x['id']:dict(bus=str(x['bus']), scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='wind'},
        'solar_mw':{x['id']:dict(bus=str(x['bus']), scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='pv'},
        'boundary_tie_mw':{x['id']:dict(bus=str(x['bus']), scale_mw=float(x['pmax'])) for x in u['tie']}}
    beginning = stamp(h['preparation_boundary_at']); priors = {}; forecasts = {}; snapshot = {}; cells = []
    for family,rs in resources.items():
        prior = dict(schema_version=PRIOR_SCHEMA, source_kind='research_assumption', method=METHOD, seed=seed,
            epoch=day_start, family=family, resources=rs, published_at=h['declared_at'], available_at=h['declared_at'],
            information_end=h['declared_at'], uses_future_actual=False, original_refs=STATIC_SHA256)
        priors[family] = prior; bind('prior:'+family, raw(prior), 'synthetic_exogenous_prior', h['declared_at'])
        knots = [dict(at=(beginning+timedelta(minutes=15*j)).isoformat(), values={r:prior_value(prior,r,beginning+timedelta(minutes=15*j)) for r in rs}) for j in (0,1)]
        forecasts[family] = dict(schema='gd.research.prepared_boundary.vintage.v1', issued_at=h['forecast_issued_at'],
            available_at=h['forecast_issued_at'], prior_sha256=sources['prior:'+family]['sha256'], knots=knots,
            value_semantics='instantaneous_knots_exact_linear_five_minute_means', uses_future_actual=False)
        bind('vintage:'+family, raw(forecasts[family]), 'synthetic_da_vintage', h['forecast_issued_at'])
        snapshot[family] = {b:0. for b in buses}
        for r,p in rs.items(): snapshot[family][p['bus']] += knots[0]['values'][r]
    for i in range(3):
        values = {family:{b:0. for b in buses} for family in resources}
        for family,rs in resources.items():
            a,b = forecasts[family]['knots']
            for r,p in rs.items(): values[family][p['bus']] += a['values'][r]+(b['values'][r]-a['values'][r])*(i+.5)/3
        cells.append(dict(at=(beginning+timedelta(minutes=5*i)).isoformat(), end=(beginning+timedelta(minutes=5*(i+1))).isoformat(), values=values))
    thermal = {}
    for x in u['thermal']:
        g=x['id']; b=static['bids']['units'][g]
        thermal[g] = dict(bus=str(x['bus']), pmin=float(x['pmin']), pmax=float(x['pmax']), ramp_mw_per_min=float(x['ramp']),
            min_up_h=float(x['ut']), min_down_h=float(x['dt']), online=h['online'][g], age_h=h['age_h'][g],
            primary_fraction=.06 if x['type']=='coal' else .04, contingency_eligible=True,
            segments=[dict(start=float(s['p_start']),end=float(s['p_end']),price=float(s['price'])) for s in b['segments']])
    lines={}
    for l in static['branches']:
        lines[l['branch_id']] = dict(from_bus=str(l['from_bus']),to_bus=str(l['to_bus']),susceptance=100/float(l['x_pu']),
            limit=float(l['rate_mw'])*float(static['base']['line_capacity_multiplier'])*float(static['base'].get('line_scale',{}).get(l['branch_id'],1)))
    inventory=dict(batteries={x['id']:dict(mode='idle',power_mw=0.,energy_mwh=float(x['ecap'])*float(x['soc0']),reserve_credit_mw=0.,parameters=x) for x in u['storage']},
        pumped={x['id']:dict(modes={f'{x["id"]}_{j+1}':'off' for j in range(x['n_units'])},net_power_mw=0.,
            upper_m3=float(x['vup0'])*10000,lower_m3=(float(x['v_total'])-float(x['vup0']))*10000,reserve_credit_mw=0.,parameters=x) for x in u['pumped']})
    return dict(schema=PROBLEM_SCHEMA,hypothesis=h,seed=seed,generated_at_utc=generated,sources=sources,buses=buses,
        thermal=thermal,lines=lines,snapshot=snapshot,preparation_cells=cells,storage_inventory=inventory)


def validate_boundary_problem(problem):
    h=problem['hypothesis']; sources=problem['sources']; cutoff=stamp(h['cutoff'])
    for key,s in sources.items():
        require(set(s)=={'artifact','sha256','kind','available_at'}, 'source keys')
        require(type(s['artifact']) is bytes and sha256(s['artifact']).hexdigest()==s['sha256'], 'source bytes/hash mismatch')
        require(stamp(s['available_at'])<=cutoff, 'future boundary source: '+key)
    sr,static=_parse_static(sources)
    expected=_prepare(sr,static,h['boundary_kind'],h['day_start'],problem['seed'],problem['generated_at_utc'])
    require(digest(problem)==digest(expected), 'boundary reconstruction mismatch: source/clock/forecast/hypothesis/model changed')
    require(stamp(h['declared_at'])<=stamp(h['forecast_issued_at'])<=cutoff<=stamp(h['planned_publication_at'])<=stamp(h['preparation_boundary_at']), 'boundary availability chronology')
    for g,d in problem['thermal'].items():
        require(d['age_h'] >= (d['min_up_h'] if d['online'] else d['min_down_h']), 'pre-existing residence requirement')
    return True


def _build_physics(problem, *, preparation=False, initial_power=None):
    """Hard lossless DC feasibility, original bids, eligible thermal reserve only."""
    m=pyo.ConcreteModel(); ts=range(3) if preparation else range(1)
    m.T=pyo.Set(initialize=ts,ordered=True); m.G=pyo.Set(initialize=IDS,ordered=True)
    m.B=pyo.Set(initialize=problem['buses'],ordered=True); m.L=pyo.Set(initialize=tuple(problem['lines']),ordered=True)
    ds=problem['thermal']; ls=problem['lines']; segs={}; bases={}
    for g,d in ds.items():
        bases[g]=sum(max(0,min(d['pmin'],s['end'])-s['start'])*s['price'] for s in d['segments'])
        for j,s in enumerate(d['segments']):
            width=s['end']-max(s['start'],d['pmin'])
            if width>0: segs[g,j]=(width,s['price'])
    m.GK=pyo.Set(initialize=tuple(segs),dimen=2,ordered=True)
    m.power=pyo.Var(m.G,m.T,domain=pyo.Reals,initialize=0,bounds=lambda _,g,t:(ds[g]['pmin']*ds[g]['online'],ds[g]['pmax']*ds[g]['online']))
    m.segment=pyo.Var(m.GK,m.T,domain=pyo.NonNegativeReals,initialize=0,bounds=lambda _,g,k,t:(0,segs[g,k][0]*ds[g]['online']))
    m.angle=pyo.Var(m.B,m.T,domain=pyo.Reals,initialize=0)
    m.flow=pyo.Var(m.L,m.T,domain=pyo.Reals,initialize=0,bounds=lambda _,l,t:(-ls[l]['limit'],ls[l]['limit']))
    m.primary=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.contingency=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.power_sum=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]==ds[g]['pmin']*ds[g]['online']+sum(m.segment[a,k,t] for a,k in segs if a==g))
    m.reference=pyo.Constraint(m.T,rule=lambda _,t:m.angle['39',t]==0)
    m.dc_flow=pyo.Constraint(m.L,m.T,rule=lambda _,l,t:m.flow[l,t]==ls[l]['susceptance']*(m.angle[ls[l]['from_bus'],t]-m.angle[ls[l]['to_bus'],t]))
    m.nodal=pyo.ConstraintList(); m.reserves=pyo.ConstraintList(); m.ramps=pyo.ConstraintList()
    for t in ts:
        v=problem['preparation_cells'][t]['values'] if preparation else problem['snapshot']
        for b in m.B:
            m.nodal.add(sum(m.power[g,t] for g,d in ds.items() if d['bus']==b)+v['wind_mw'][b]+v['solar_mw'][b]+v['boundary_tie_mw'][b]-v['load_mw'][b]
                ==sum(m.flow[l,t] for l,d in ls.items() if d['from_bus']==b)-sum(m.flow[l,t] for l,d in ls.items() if d['to_bus']==b))
        net=sum(v['load_mw'][b]-v['wind_mw'][b]-v['solar_mw'][b]-v['boundary_tie_mw'][b] for b in m.B)
        # Explicit variable-free margins are encoded against a fixed zero anchor
        # rather than allowing Pyomo to collapse an infeasible Boolean row.
        m.reserves.add(0*m.angle['39',t] <= sum(d['pmax']*d['online'] for d in ds.values())-net-400.)
        m.reserves.add(0*m.angle['39',t] <= net-sum(d['pmin']*d['online'] for d in ds.values()))
        for g,d in ds.items():
            m.reserves.add(m.primary[g,t] <= d['pmax']*d['online']-m.power[g,t])
            m.reserves.add(m.contingency[g,t] <= d['pmax']*d['online']-m.power[g,t])
            m.reserves.add(m.primary[g,t] <= d['primary_fraction']*d['pmax']*d['online'])
            m.reserves.add(m.contingency[g,t] <= 10*d['ramp_mw_per_min']*d['online']*d['contingency_eligible'])
            if preparation:
                previous=initial_power[g] if t==0 else m.power[g,t-1]
                m.ramps.add(m.power[g,t]-previous <= 5*d['ramp_mw_per_min'])
                m.ramps.add(previous-m.power[g,t] <= 5*d['ramp_mw_per_min'])
        m.reserves.add(sum(m.primary[g,t] for g in m.G)>=0.)
        m.reserves.add(sum(m.contingency[g,t] for g in m.G)>=400.)
    factor=1/12 if preparation else 1.
    m.energy_cost=pyo.Expression(expr=factor*sum(bases[g]*ds[g]['online']+sum(segs[a,k][1]*m.segment[a,k,t] for a,k in segs if a==g) for g in m.G for t in ts))
    m.objective=pyo.Objective(expr=m.energy_cost)
    m.lex_pins=pyo.ConstraintList(); m.dual=pyo.Suffix(direction=pyo.Suffix.IMPORT)
    m._boundary_problem=deepcopy(problem)
    return m


def _payload(m):
    p,vs,cs=serialize_model(m,dict(threads=1,time_limit=1,mip_rel_gap=.001))
    return {k:p[k] for k in ('version','variables','rows','objective')},vs,cs


def _fingerprint(m):
    payload,_,_=_payload(m)
    return digest((m._boundary_problem,payload))


def build_boundary_equilibrium(problem):
    validate_boundary_problem(problem)
    m=_build_physics(problem); BUILT[m]=_fingerprint(m)
    return m


def _certificate(m,metrics):
    payload,vs,cs=_payload(m)
    c=dict(model_sha256=digest(payload),primal=[pyo.value(v) for v in vs],row_dual=[float(m.dual[c]) for c in cs],
        objective_value=float(pyo.value(m.objective)),metrics=deepcopy(metrics))
    c['validation']=_verify_certificate(m,c)
    return c


def _verify_certificate(m,certificate):
    """Independent primal/dual bound proof, never a self-declared solved flag."""
    payload,vs,cs=_payload(m)
    require(certificate['model_sha256']==digest(payload), 'certificate mathematics mismatch')
    x=certificate['primal']; y=certificate['row_dual']
    require(len(x)==len(vs) and len(y)==len(cs), 'certificate vector coverage')
    require(all(type(v) in (int,float) and math.isfinite(v) for v in x+y), 'certificate nonfinite')
    primal=0.; dualbad=0.; reduced=[0.]*len(vs)
    objective=payload['objective']; primal_obj=objective['constant']; dual_obj=objective['constant']
    for i,a in objective['terms']: reduced[i]+=a; primal_obj+=a*x[i]
    def residual(value,lo,hi): return max(0.,lo-value if lo is not None else 0.,value-hi if hi is not None else 0.)
    for value,record in zip(x,payload['variables']): primal=max(primal,residual(value,record['lb'],record['ub']))
    for yy,row in zip(y,payload['rows']):
        value=math.fsum(a*x[i] for i,a in row['terms'])
        primal=max(primal,residual(value,row['lb'],row['ub']))
        for i,a in row['terms']: reduced[i]-=yy*a
        bound=row['lb'] if yy>=0 else row['ub']
        if bound is None: dualbad=max(dualbad,abs(yy))
        else: dual_obj+=yy*bound
    for rr,record in zip(reduced,payload['variables']):
        bound=record['lb'] if rr>=0 else record['ub']
        if bound is None: dualbad=max(dualbad,abs(rr))
        else: dual_obj+=rr*bound
    cost_stage=certificate['metrics']['stage'].endswith(('ENERGY','PREPARATION'))
    tolerance=1e-4+1e-9*abs(primal_obj) if cost_stage else LEX_TOL
    gap=primal_obj-dual_obj
    require(primal<=PRIMAL_TOL, 'certificate primal physical violation')
    require(dualbad<=DUAL_TOL, 'certificate dual feasibility violation')
    require(-tolerance<=gap<=tolerance, 'certificate primal-dual optimality gap')
    require(abs(certificate['objective_value']-primal_obj)<=tolerance, 'certificate objective mismatch')
    return dict(max_primal_violation=primal,max_dual_violation=dualbad,primal_objective=primal_obj,dual_objective=dual_obj,gap=gap,tolerance=tolerance)


def _native_solve(m,stage,time_limit_s,threads):
    from .gd_solve import solve_dispatch
    from .highs_solver import HighsNoIncumbentError
    _native_solver_identity(solve_dispatch)
    try:
        result=solve_dispatch(m,stage=stage,time_limit_s=time_limit_s,threads=threads,mip_rel_gap=.001)
    except HighsNoIncumbentError as exc:
        FAILED[m]=dict(stage=stage,worker_failure=exc.failure_record,accepted_primal=False,execution_authority=False)
        raise
    _native_metrics(result,m,lp=True)
    return _certificate(m,result.metrics)


def solve_boundary_equilibrium(m, *, time_limit_s=60., threads=1):
    """One energy LP plus one LP per stable unit, frozen lexical order."""
    require(m in BUILT and _fingerprint(m)==BUILT[m], 'boundary model modified or not built here')
    ACCEPTED.pop(m,None); FAILED.pop(m,None)
    problem=deepcopy(m._boundary_problem); h=problem['hypothesis']; certificates=[]
    first=_native_solve(m,'R12_BOUNDARY_ENERGY',time_limit_s,threads); certificates.append(first)
    m._boundary_certificates=deepcopy(certificates)
    optimum=first['objective_value']; tolerance=1e-4+1e-9*abs(optimum)
    m.cost_cap=pyo.Constraint(expr=m.energy_cost<=optimum+tolerance)
    for g in h['equilibrium_lexicographic_order']:
        m.objective.set_value(m.power[g,0]); m.dual.clear()
        cert=_native_solve(m,'R12_BOUNDARY_LEX_'+g,time_limit_s,threads); certificates.append(cert)
        m._boundary_certificates=deepcopy(certificates)
        m.lex_pins.add(m.power[g,0]==cert['objective_value'])
    # Last pin is redundant but is not part of the last solve; keep witness
    # representation equal to the exact last solved LP for revalidation.
    del m.lex_pins[len(m.lex_pins)]
    report=dict(schema=EQUILIBRIUM_SCHEMA,problem=problem,certificates=certificates,
        at=h['preparation_boundary_at'],thermal_power_mw={g:float(pyo.value(m.power[g,0])) for g in IDS},
        stage1_energy_yuan_per_h=optimum,lex_energy_yuan_per_h=float(pyo.value(m.energy_cost)),cost_tolerance_yuan=tolerance,
        equilibrium_only=True,finite_preparation_reachability=False,original_predecessor_reachable=False,execution_authority=False)
    validate_equilibrium_report(report)
    ACCEPTED[m]=deepcopy(report)
    return deepcopy(report)


def validate_equilibrium_report(report):
    require(set(report)=={'schema','problem','certificates','at','thermal_power_mw','stage1_energy_yuan_per_h','lex_energy_yuan_per_h','cost_tolerance_yuan','equilibrium_only','finite_preparation_reachability','original_predecessor_reachable','execution_authority'}, 'equilibrium report keys')
    require(report['schema']==EQUILIBRIUM_SCHEMA, 'equilibrium report schema')
    problem=report['problem']; validate_boundary_problem(problem); h=problem['hypothesis']
    require(report['at']==h['preparation_boundary_at'] and report['equilibrium_only'] is True and
        report['finite_preparation_reachability'] is False and report['original_predecessor_reachable'] is False and report['execution_authority'] is False, 'equilibrium scope/clock mismatch')
    certificates=report['certificates']; order=h['equilibrium_lexicographic_order']
    require(len(certificates)==len(order)+1, 'all objective certificates required')
    m=_build_physics(problem)
    first=certificates[0]; require(first['metrics']['stage']=='R12_BOUNDARY_ENERGY', 'energy stage identity')
    _verify_certificate(m,first); optimum=first['objective_value']; tolerance=1e-4+1e-9*abs(optimum)
    m.cost_cap=pyo.Constraint(expr=m.energy_cost<=optimum+tolerance)
    for i,g in enumerate(order):
        m.objective.set_value(m.power[g,0]); cert=certificates[i+1]
        require(cert['metrics']['stage']=='R12_BOUNDARY_LEX_'+g, 'lex stage identity/order')
        _verify_certificate(m,cert)
        if i+1<len(order): m.lex_pins.add(m.power[g,0]==cert['objective_value'])
    _,vs,_=_payload(m)
    for v,x in zip(vs,certificates[-1]['primal']): v.set_value(x)
    expected={g:float(pyo.value(m.power[g,0])) for g in IDS}
    require(report['thermal_power_mw']==expected, 'boundary p0 differs from final lex certificate')
    require(abs(report['stage1_energy_yuan_per_h']-optimum)<=1e-9 and report['cost_tolerance_yuan']==tolerance and
        abs(report['lex_energy_yuan_per_h']-pyo.value(m.energy_cost))<=1e-9, 'objective ledger mismatch')
    return deepcopy(expected)


def build_preparation_segment(equilibrium_report):
    initial=validate_equilibrium_report(equilibrium_report)
    m=_build_physics(equilibrium_report['problem'],preparation=True,initial_power=initial)
    m._equilibrium_report=deepcopy(equilibrium_report); BUILT[m]=_fingerprint(m)
    return m


def _cell_rows(m):
    problem=m._boundary_problem; ds=problem['thermal']; rows=[]
    for t,cell in enumerate(problem['preparation_cells']):
        values=cell['values']; net=sum(values['load_mw'][b]-values['wind_mw'][b]-values['solar_mw'][b]-values['boundary_tie_mw'][b] for b in problem['buses'])
        rows.append(dict(at=cell['at'],end=cell['end'],power_mw={g:float(pyo.value(m.power[g,t])) for g in IDS},
            contingency_mw={g:float(pyo.value(m.contingency[g,t])) for g in IDS},
            primary_mw={g:float(pyo.value(m.primary[g,t])) for g in IDS},
            positive_margin_mw=sum(d['pmax']*d['online'] for d in ds.values())-net-400.,
            negative_margin_mw=net-sum(d['pmin']*d['online'] for d in ds.values()),
            contingency_margin_mw=sum(pyo.value(m.contingency[g,t]) for g in IDS)-400.,
            flow_mw={l:float(pyo.value(m.flow[l,t])) for l in m.L},
            angle_rad={b:float(pyo.value(m.angle[b,t])) for b in m.B}))
    return rows


def _physical_summary(m):
    rows=_cell_rows(m); ds=m._boundary_problem['thermal']; lines=m._boundary_problem['lines']
    initial=m._equilibrium_report['thermal_power_mw']
    ramp=0.; network=0.; balance=0.; reserve=0.
    for t,row in enumerate(rows):
        v=m._boundary_problem['preparation_cells'][t]['values']
        for g,d in ds.items():
            previous=initial[g] if t==0 else rows[t-1]['power_mw'][g]
            ramp=max(ramp,abs(row['power_mw'][g]-previous)-5*d['ramp_mw_per_min'])
            reserve=max(reserve,row['contingency_mw'][g]-(d['pmax']*d['online']-row['power_mw'][g]),row['contingency_mw'][g]-10*d['ramp_mw_per_min']*d['online'])
        for l,d in lines.items(): network=max(network,abs(row['flow_mw'][l])-d['limit'])
        for bus in m.B:
            injection=sum(row['power_mw'][g] for g,d in ds.items() if d['bus']==bus)+v['wind_mw'][bus]+v['solar_mw'][bus]+v['boundary_tie_mw'][bus]-v['load_mw'][bus]
            outgoing=sum(row['flow_mw'][l] for l,d in lines.items() if d['from_bus']==bus)-sum(row['flow_mw'][l] for l,d in lines.items() if d['to_bus']==bus)
            balance=max(balance,abs(injection-outgoing))
    return dict(max_nodal_balance_residual_mw=balance,max_network_overload_mw=network,max_ramp_violation_mw=ramp,max_contingency_provider_violation_mw=reserve,minimum_positive_margin_mw=min(r['positive_margin_mw'] for r in rows),minimum_negative_margin_mw=min(r['negative_margin_mw'] for r in rows),minimum_contingency_margin_mw=min(r['contingency_margin_mw'] for r in rows))


def _terminal_thermal(problem,powers):
    return {g:dict(initial_online=d['online'],initial_power_mw=powers[g],initial_state_age_h=d['age_h']+.25,
        initial_state_kind='stable',phase='stable' if d['online'] else 'off',
        residence_remaining_h=max(0.,(d['min_up_h'] if d['online'] else d['min_down_h'])-d['age_h']-.25)) for g,d in problem['thermal'].items()}


def solve_preparation_segment(m, *, time_limit_s=60., threads=1):
    require(m in BUILT and _fingerprint(m)==BUILT[m], 'preparation model modified or not built here')
    ACCEPTED.pop(m,None); FAILED.pop(m,None)
    # Recheck the immutable inherited boundary, not a caller's replacement p0.
    initial=validate_equilibrium_report(m._equilibrium_report)
    expected=_build_physics(m._boundary_problem,preparation=True,initial_power=initial)
    require(_fingerprint(m)==_fingerprint(expected), 'preparation boundary linkage changed')
    cert=_native_solve(m,'R12_BOUNDARY_PREPARATION',time_limit_s,threads)
    rows=_cell_rows(m); problem=deepcopy(m._boundary_problem)
    report=dict(schema=REPORT_SCHEMA,problem=problem,equilibrium=deepcopy(m._equilibrium_report),preparation_certificate=cert,
        cells=rows,thermal=_terminal_thermal(problem,rows[-1]['power_mw']),storage_inventory=deepcopy(problem['storage_inventory']),
        output_at=problem['hypothesis']['delivery_boundary_at'],preparation_cost_yuan=float(pyo.value(m.energy_cost)),
        equilibrium_verified=True,finite_preparation_reachability=True,original_predecessor_reachable=False,
        historical_telemetry=False,execution_authority=False,effective_plan_issued=False,full_day_feasibility_proven=False,
        validation=deepcopy(cert['validation']),max_network_overload_mw=_physical_summary(m)['max_network_overload_mw'],physical_summary=_physical_summary(m),
        provenance='independent hypothetical equilibrium plus exactly three continuous five-minute cells; not authenticated history',
        evidence_kind='portable_independently_reconstructed_optimality_and_finite_feasibility',native_execution_authenticated=False)
    validate_boundary_report(report)
    ACCEPTED[m]=deepcopy(report)
    return deepcopy(report)


def validate_boundary_report(report):
    """Pure portable mathematical verification. Does not mint live authority."""
    require(report.get('schema')==REPORT_SCHEMA, 'complete preparation report required; equilibrium alone is insufficient')
    require(set(report)=={'schema','problem','equilibrium','preparation_certificate','cells','thermal','storage_inventory','output_at','preparation_cost_yuan','equilibrium_verified','finite_preparation_reachability','original_predecessor_reachable','historical_telemetry','execution_authority','effective_plan_issued','full_day_feasibility_proven','validation','max_network_overload_mw','provenance','evidence_kind','native_execution_authenticated','physical_summary'}, 'preparation report keys')
    require(report['evidence_kind']=='portable_independently_reconstructed_optimality_and_finite_feasibility' and report['native_execution_authenticated'] is False, 'portable report cannot authenticate native execution')
    problem=report['problem']; validate_boundary_problem(problem)
    require(digest(problem)==digest(report['equilibrium']['problem']), 'equilibrium/preparation source mismatch')
    initial=validate_equilibrium_report(report['equilibrium'])
    m=_build_physics(problem,preparation=True,initial_power=initial)
    cert=report['preparation_certificate']; require(cert['metrics']['stage']=='R12_BOUNDARY_PREPARATION','preparation stage identity')
    check=_verify_certificate(m,cert)
    _,vs,_=_payload(m)
    for v,x in zip(vs,cert['primal']): v.set_value(x)
    m._equilibrium_report=deepcopy(report['equilibrium'])
    rows=_cell_rows(m)
    require(digest(report['physical_summary'])==digest(_physical_summary(m)), 'physical residual ledger mismatch')
    require(digest(report['cells'])==digest(rows), 'preparation cells differ from physical certificate')
    require(digest(report['thermal'])==digest(_terminal_thermal(problem,rows[-1]['power_mw'])), 'terminal power/phase/age mismatch')
    require(digest(report['storage_inventory'])==digest(problem['storage_inventory']), 'preparation inventory mutated')
    require(report['output_at']==problem['hypothesis']['delivery_boundary_at'], 'preparation export clock mismatch')
    require(report['equilibrium_verified'] is True and report['finite_preparation_reachability'] is True and all(report[k] is False for k in
        ('original_predecessor_reachable','historical_telemetry','execution_authority','effective_plan_issued','full_day_feasibility_proven')), 'prepared research scope overclaim')
    require(abs(report['preparation_cost_yuan']-pyo.value(m.energy_cost))<=1e-9 and report['max_network_overload_mw']==_physical_summary(m)['max_network_overload_mw'] and report['max_network_overload_mw']<=PRIMAL_TOL, 'preparation cost/network ledger')
    require(digest(report['validation'])==digest(check), 'certificate residual ledger mismatch')
    return deepcopy(report['thermal'])


def accepted_boundary_report(model):
    """Live model acceptance only; setting variables or rehashing cannot mint it."""
    require(model in ACCEPTED, 'accepted native boundary model required')
    report=ACCEPTED[model]
    require(digest(model._boundary_problem)==digest(report['problem']), 'accepted model source problem mutated')
    if report['schema']==REPORT_SCHEMA:
        require(digest(model._equilibrium_report)==digest(report['equilibrium']), 'accepted predecessor evidence mutated')
    if report['schema']==REPORT_SCHEMA:
        validate_boundary_report(report); cert=report['preparation_certificate']
    else:
        validate_equilibrium_report(report); cert=report['certificates'][-1]
    payload,vs,_=_payload(model)
    require(digest(payload)==cert['model_sha256'] and [pyo.value(v) for v in vs]==cert['primal'], 'accepted model mutated after solve')
    return deepcopy(report)


def solve_prepared_boundary(problem, *, time_limit_s=60., threads=1):
    """Explicit native entry: energy + stable-unit lex LPs + three-cell LP."""
    eq=build_boundary_equilibrium(problem)
    equilibrium=solve_boundary_equilibrium(eq,time_limit_s=time_limit_s,threads=threads)
    prep=build_preparation_segment(equilibrium)
    solve_preparation_segment(prep,time_limit_s=time_limit_s,threads=threads)
    return accepted_boundary_report(prep)


def native_failure_report(model):
    require(model in FAILED, 'recorded native failure required')
    return deepcopy(FAILED[model])


def solve_prepared_boundary_live(problem, *, time_limit_s=60., threads=1):
    """Fresh seven-LP replay and an opaque in-process permit, never serialized."""
    eq=build_boundary_equilibrium(problem)
    equilibrium=solve_boundary_equilibrium(eq,time_limit_s=time_limit_s,threads=threads)
    prep=build_preparation_segment(equilibrium)
    solve_preparation_segment(prep,time_limit_s=time_limit_s,threads=threads)
    report=accepted_boundary_report(prep)
    permit=_LivePreparedPermit()
    _LIVE_PERMITS[permit]=(prep,digest(report),digest(problem))
    return report,permit


def validate_live_boundary(report, permit):
    """Require the exact live accepted preparation, not raw report/hash flags."""
    require(type(permit) is _LivePreparedPermit and permit in _LIVE_PERMITS, 'live prepared native permit required')
    model,report_hash,problem_hash=_LIVE_PERMITS[permit]
    require(digest(report)==report_hash and digest(report['problem'])==problem_hash, 'live prepared report/input linkage changed')
    live=accepted_boundary_report(model)
    require(digest(live)==report_hash, 'live prepared model/report changed')
    return deepcopy(report['thermal'])
