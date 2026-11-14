"""R13 source-only recipe. No generated R12 inputs or historical profiles read.

Simulation availability is a scenario clock, never actual publication history.
Native solves are explicit caller actions; construction and comparison are pure.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math

from .gd_da_ieee39 import STATIC_SHA256
from .gd_da_candidate import digest, require, stamp
from .gd_da_prepared_boundary import prepare_boundary_problem
from .gd_da_battery_ieee39 import prepare_ieee39_battery_da
from .gd_october_ieee39_scenario import _load_static
from .gd_october_research_forecast import prior_value, METHOD, PRIOR_SCHEMA

SCHEMA = 'gd.research.r13.source_recipe.v1'
TAIL_POLICY = dict(schema='gd.research.r13.tail_policy.v1',
    duration_minutes=105, gt_restart_minutes=(15,30,45),
    thermal_policy='carry final state; stable units remain stable; off gas may stay off or restart on frozen grid',
    curve_policy='unchanged original R12 synthetic duration and original startup fee',
    battery_terminal_policy='research closure SOC0.5 at D+1 01:45, not official new-day target; midnight original target retained',
    battery_cycle_policy='carry dated ledger; new date begins with zero throughput, never erase prior date',
    exogenous_policy='same seeded harmonic prior, exact 15-minute knots and linear 5-minute means',
    reserve_policy='original positive400 contingency400 negative0 primary0; battery/renewable/PS incremental zero',
    infeasibility_policy='stop and retain failed attempt; no exogenous repairs, target changes or invented reserves',
    predeclared_before_native=True)


def source_cells(root=None, *, static_raw=None, day_start='2026-11-11T00:00:00+08:00', seed=20261111, count=309):
    """All delivery and tail exogenous data directly from six original statics."""
    require(type(count) is int and count>0 and count%3==0, 'whole quarters required')
    if static_raw is None:
        sr,s=_load_static(root)
    else:
        from .gd_da_battery_ieee39 import _static_from_bytes
        sr=static_raw;s=_static_from_bytes(sr)
    require({k:sha256(v).hexdigest() for k,v in sr.items()}==STATIC_SHA256,'original static bytes required')
    day=stamp(day_start);declared=day-timedelta(days=1);issue=day-timedelta(hours=4.5)
    u=s['units']; buses=tuple(str(x['bus']) for x in s['buses'])
    resources={'load_mw':{f'load:{x["bus"]}':dict(bus=str(x['bus']),scale_mw=float(x['load_mw'])*float(s['base']['load_scale'])) for x in s['buses']},
        'wind_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='wind'},
        'solar_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='pv'},
        'boundary_tie_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['pmax'])) for x in u['tie']}}
    priors={};vintages={}
    for family,rs in resources.items():
        prior=dict(schema_version=PRIOR_SCHEMA,source_kind='research_assumption',method=METHOD,seed=seed,epoch=day.isoformat(),family=family,resources=rs,published_at=declared.isoformat(),available_at=declared.isoformat(),information_end=declared.isoformat(),uses_future_actual=False,original_refs=deepcopy(STATIC_SHA256))
        priors[family]=prior
        vintages[family]=dict(issued_at=issue.isoformat(),available_at=issue.isoformat(),
            knots=[dict(at=(day+timedelta(minutes=15*j)).isoformat(),values={r:prior_value(prior,r,day+timedelta(minutes=15*j)) for r in rs}) for j in range(count//3+1)])
    cells=[]
    for i in range(count):
        j,k=divmod(i,3);v={f:{b:0. for b in buses} for f in resources}
        for family,rs in resources.items():
            a,z=vintages[family]['knots'][j:j+2]
            for r,p in rs.items():v[family][p['bus']]+=a['values'][r]+(z['values'][r]-a['values'][r])*(k+.5)/3
        cells.append(dict(start=(day+timedelta(minutes=5*i)).isoformat(),end=(day+timedelta(minutes=5*(i+1))).isoformat(),values=v))
    return dict(schema=SCHEMA,day_start=day.isoformat(),seed=seed,static_sha256=deepcopy(STATIC_SHA256),
        actual_generated_at_utc=datetime.now(timezone.utc).isoformat(),cutoff=(day-timedelta(hours=4)).isoformat(),
        publication_at=(day-timedelta(hours=3.5)).isoformat(),availability_is_simulated=True,
        historical_publication_claim=False,priors=priors,vintages=vintages,cells=cells,tail_policy=deepcopy(TAIL_POLICY))


def reconstruct_boundary(root, *, day_start='2026-11-11T00:00:00+08:00', seed=20261111, generated_at_utc=None):
    return prepare_boundary_problem(root,day_start=day_start,seed=seed,
        generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat())


def reconstruct_day(root, boundary_report, *, generated_at_utc=None):
    """Mathematical reconstruction only; live builder separately demands permit."""
    p=boundary_report['problem'];h=p['hypothesis']
    return prepare_ieee39_battery_da(root,boundary_kind='prepared_D00_research',day_start=h['day_start'],seed=p['seed'],
        generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat(),prepared_boundary=boundary_report)


def mathematical_payload(payload):
    """Solver runtime settings/timings are deliberately excluded, not matched."""
    return {k:payload[k] for k in ('version','variables','rows','objective')}


def compare_math(a,b, *, tolerance=1e-9):
    """Independent recursive numeric comparison, not hash-only reproducibility."""
    count=0;worst=0.;mismatches=[]
    def walk(x,y,path):
        nonlocal count,worst
        if type(x) in (int,float) and type(y) in (int,float):
            count+=1; delta=abs(x-y);worst=max(worst,delta)
            if not math.isfinite(delta) or delta>tolerance:
                if len(mismatches)<20:mismatches.append(dict(path=path,left=x,right=y,absolute_delta=delta))
        elif isinstance(x,dict) and isinstance(y,dict):
            if set(x)!=set(y):mismatches.append(dict(path=path,reason='key mismatch'));return
            for k in x:walk(x[k],y[k],path+'/'+str(k))
        elif isinstance(x,(list,tuple)) and isinstance(y,(list,tuple)):
            if len(x)!=len(y):mismatches.append(dict(path=path,reason='length mismatch'));return
            for i,(v,w) in enumerate(zip(x,y)):walk(v,w,path+'/'+str(i))
        elif x!=y:
            if len(mismatches)<20:mismatches.append(dict(path=path,left=x,right=y))
    walk(a,b,'')
    return dict(passed=not mismatches,numeric_comparisons=count,max_absolute_delta=worst,mismatches=mismatches,tolerance=tolerance,hash_only=False,runtime_metadata_compared=False)


def independent_primal_check(matrix, primal, *, tolerance=1e-6):
    require(len(primal)==len(matrix['variables']),'full primal vector required')
    worst=0.;integer=0
    def check(x,lo,hi):
        nonlocal worst
        require(math.isfinite(x),'nonfinite primal')
        worst=max(worst,0. if lo is None else lo-x,0. if hi is None else x-hi)
    for x,v in zip(primal,matrix['variables']):
        check(x,v['lb'],v['ub'])
        if v['kind']!='C':integer+=1;worst=max(worst,abs(x-round(x)))
    for r in matrix['rows']:check(math.fsum(primal[i]*a for i,a in r['terms']),r['lb'],r['ub'])
    obj=matrix['objective'];objective=obj['constant']+math.fsum(primal[i]*a for i,a in obj['terms'])
    return dict(passed=worst<=tolerance,max_primal_violation=worst,objective_yuan=objective,rows=len(matrix['rows']),variables=len(primal),integer_variables=integer,tolerance=tolerance)
