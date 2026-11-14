"""Disclosed synthetic control, NOT IEEE39 and NOT an official market replay.

One fresh fixed-state hard-network SCED, then independent soft-network price
LP and two price probes. Native calls occur only through explicit run_control.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from hashlib import sha256
import json,sys,time
import pyomo.environ as p
from . import highs_solver as hs
from .gd_r14_da_lmp import canonical,digest,require,audit_kkt,solve_native,audit_probe_envelope,OPTIONS,SCHEMA

def control_input():
    start=datetime(2026,11,12,tzinfo=timezone(timedelta(hours=8)))
    grid=[]
    for t in range(98):
        at=start+timedelta(minutes=15*(t+1)) if t<96 else start+timedelta(days=1,hours=12 if t==96 else 18)
        grid.append(dict(start=(at-timedelta(minutes=15)).isoformat(),end=at.isoformat(),actual_time=at.isoformat(),settlement=t<96,kind='delivery' if t<96 else ('peak' if t==96 else 'valley'),objective_weight_h=.25 if t<96 else 1.,physical_duration_h=.25 if t<96 else (12. if t==96 else 6.),ramp_elapsed_h=.25 if t<96 else (12. if t==96 else 6.),predecessor=t-1 if t else None,constraint_scope='synthetic constant dispatch no storage; explicit sparse bridge'))
    resources={}
    for name,node,price,initial in [('cheap','A',100.,300.),('expensive','B',1500.,400.)]:
        row=dict(min_mw=0.,max_mw=1000.,online=True,price_eligible=True,eligibility_reason='synthetic explicit pricing qualification',reserve_eligible=True,positive_cap_mw=1000.,negative_cap_mw=1000.,primary_cap_mw=0.,accident_cap_mw=1000.,ramp_up_mw_h=10000.,ramp_down_mw_h=10000.,segments=[dict(width_mw=1000.,price_yuan_mwh=price)],offer_intercept_yuan_h=0.)
        resources[name]=dict(node=node,kind='generator',initial_power_mw=initial,rows=[deepcopy(row) for _ in range(98)])
    return dict(control_case='two_node_M1_prime',schema=SCHEMA,profile=dict(mode='research',research_profile='synthetic_M1_prime_control_v1',rule_source='regional2026V1 table network price penalty default1000',delta_source='explicit synthetic 10 percent, not official value',eligibility_source='synthetic qualified thermal',initial_state_source='synthetic online stable',sced_source='native synthetic control',parameter_source='all case quantities authored for diagnostic',storage_interpretation='signed_magnitude_fixed_sced_mode_research',excluded_resource_types=['battery','renewable','hydro','pumped_storage']),delta=.1,grid=grid,nodes=['A','B'],load_mw={'A':[0.]*98,'B':[700.]*98},fixed_injection_mw={'A':[0.]*98,'B':[0.]*98},resources=resources,reserve=[dict(positive_mw=400.,accident_mw=400.,negative_mw=0.,primary_mw=0.) for _ in range(98)],network={'AB':dict(kind='line',ptdf={'A':1.,'B':0.},lower_mw=[-300.]*98,upper_mw=[300.]*98,penalty_yuan_mwh=1000.,penalty_source='regional2026V1 appendix network default; explicit synthetic adoption')},groups=[])

def build_sced(data):
    """Small SCED from scratch. No pricing model, delta or SCED reference used."""
    d=deepcopy(data);require(d['control_case']=='two_node_M1_prime','control scope');m=p.ConcreteModel('synthetic_fixed_state_SCED');R=list(d['resources']);T=range(98);N=d['nodes'];L=list(d['network'])
    m.power=p.Var(R,T);m.rows=p.ConstraintList();m.up=p.Var(R,T,domain=p.NonNegativeReals);m.accident=p.Var(R,T,domain=p.NonNegativeReals)
    cost=0
    for r,u in d['resources'].items():
        for t,row in enumerate(u['rows']):
            require(row['online'] and row['min_mw']==0 and len(row['segments'])==1,'single segment online control')
            m.power[r,t].setlb(row['min_mw']);m.power[r,t].setub(row['max_mw']);cost+=d['grid'][t]['objective_weight_h']*row['segments'][0]['price_yuan_mwh']*m.power[r,t]
            for var,key in ((m.up,'positive_cap_mw'),(m.accident,'accident_cap_mw')):
                var[r,t].setub(row[key]);m.rows.add(var[r,t]<=row['max_mw']-m.power[r,t])
            g=d['grid'][t];prev=u['initial_power_mw'] if g['predecessor'] is None else m.power[r,g['predecessor']]
            m.rows.add(m.power[r,t]-prev<=row['ramp_up_mw_h']*g['ramp_elapsed_h']);m.rows.add(prev-m.power[r,t]<=row['ramp_down_mw_h']*g['ramp_elapsed_h'])
    m.injection=p.Expression(N,T,rule=lambda _,n,t:sum(m.power[r,t] for r in R if d['resources'][r]['node']==n)+d['fixed_injection_mw'][n][t]-d['load_mw'][n][t])
    m.balance=p.Constraint(T,rule=lambda _,t:sum(m.injection[n,t] for n in N)==0)
    m.flow=p.Expression(L,T,rule=lambda _,l,t:sum(d['network'][l]['ptdf'][n]*m.injection[n,t] for n in N))
    m.line_upper=p.Constraint(L,T,rule=lambda _,l,t:m.flow[l,t]<=d['network'][l]['upper_mw'][t]);m.line_lower=p.Constraint(L,T,rule=lambda _,l,t:-m.flow[l,t]<=-d['network'][l]['lower_mw'][t])
    for t,q in enumerate(d['reserve']):
        require(q['negative_mw']==q['primary_mw']==0,'control zero negative/primary')
        m.rows.add(sum(m.up[r,t] for r in R)>=q['positive_mw']);m.rows.add(sum(m.accident[r,t] for r in R)>=q['accident_mw'])
    m.objective=p.Objective(expr=cost);m.dual=p.Suffix(direction=p.Suffix.IMPORT)
    return m,d,dict(synthetic=True)

def solve_sced(d,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);require(not(out/'request.json').exists(),'do not overwrite control evidence')
    m,d,_=build_sced(d);payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));payload.update(deadline=time.perf_counter()+OPTIONS['time_limit'],preparation_elapsed_s=0.)
    (out/'input.json').write_bytes(canonical(d));(out/'request.json').write_bytes(canonical(payload));(out/'names.json').write_bytes(canonical(dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])))
    run=hs.run_child([sys.executable,str(Path(hs.__file__).with_name('highs_worker.py')),str(out/'request.json'),str(out/'response.json')],env=hs.child_environment(),timeout=OPTIONS['time_limit'])
    require(run.returncode==0 and(out/'response.json').exists(),'control SCED worker failed');raw=json.loads((out/'response.json').read_text());require(raw.get('status')=='optimal' and raw.get('dual_valid'),'control SCED optimal LP required');values=hs.validate_solution(payload,raw);kkt=audit_kkt(payload,raw)
    for v,x in zip(vs,values):v.set_value(x)
    snapshot=dict(stage='SCED',status='optimal',source_kind='native_highs_sced',power_mw={r:[p.value(m.power[r,t]) for t in range(98)] for r in d['resources']},online={r:[True]*98 for r in d['resources']},mode={})
    priced=deepcopy(d);priced['sced_snapshot']=snapshot;priced['source_binding']=dict(sced_snapshot_sha256=digest(snapshot),artifacts=[dict(role='sced_'+role,path=str((out/name).resolve()),sha256=sha256((out/name).read_bytes()).hexdigest()) for role,name in [('input','input.json'),('request','request.json'),('response','response.json'),('names','names.json')]])
    for r,u in priced['resources'].items():
        for t,row in enumerate(u['rows']):row['sced_mw']=snapshot['power_mw'][r][t]
    (out/'pricing_input.json').write_bytes(canonical(priced));(out/'audit.json').write_bytes(canonical(dict(kkt=kkt,synthetic=True)))
    return priced

def run_control(out):
    out=Path(out);d=solve_sced(control_input(),out/'sced');base=solve_native(d,out/'lmp_base');left=solve_native(d,out/'lmp_left',dict(node='B',index=0,increment_mw=-.01));right=solve_native(d,out/'lmp_right',dict(node='B',index=0,increment_mw=.01));envelope=audit_probe_envelope(base,left,right,'B',0,.01,.25)
    raw=json.loads((out/'lmp_base'/'response.json').read_text());names=json.loads((out/'lmp_base'/'names.json').read_text());primal=dict(zip(names['variables'],raw['primal']));slack=primal['slack_up[AB,0]'];require(slack>1e-5,'network penalty was not activated')
    result=dict(synthetic_control=True,native_solve_count=4,M1_prime_yuan_mwh=1000.,network_overflow_mw=slack,probe=envelope,all_native_kkt_passed=True,not_IEEE39=True)
    (out/'control_result.json').write_bytes(canonical(result));return result
