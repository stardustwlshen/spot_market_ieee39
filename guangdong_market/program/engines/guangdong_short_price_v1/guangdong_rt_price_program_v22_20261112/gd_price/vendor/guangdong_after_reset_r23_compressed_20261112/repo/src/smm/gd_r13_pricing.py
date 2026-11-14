"""Fresh 8x15 research price LP; never an average of ED duals or execution plan.

The 24-cell thermal/storage lift is a stricter research feasibility domain, not
an assertion of exact official eight-quarter equivalence. Original files frozen.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from weakref import WeakKeyDictionary
import pyomo.environ as pyo
from . import highs_solver
from .gd_october_pricing_network import dc_ptdf, normalize_topology_ptdf
from .gd_october_pricing_solve import _audit_optimum

_SCHEMA='gd.r13.independent_price.v1'
_MODELS=WeakKeyDictionary()
_SOLVES=WeakKeyDictionary()
_OPTIONS=dict(time_limit=60.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.)


def require(ok, message):
    if not ok: raise ValueError(message)


def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def stamp(x):
    d=datetime.fromisoformat(x)
    require(d.utcoffset() is not None,'timezone-aware timestamp required')
    return d


def number(x,label):
    require(type(x) in (int,float) and math.isfinite(x),label+': finite numeric input required')
    return float(x)


def mean(xs): return math.fsum(xs)/len(xs)


def widths(curve, charge=False):
    """Retain exact authored decimal endpoints; do not evenly repartition them."""
    ps=[Decimal(str(x)) for x in curve['breakpoints_mw']]
    prices=[number(x,'bid price') for x in curve['prices_yuan_per_mwh']]
    require(len(ps)==len(prices)+1 and ps[0]==0 and all(a<b for a,b in zip(ps,ps[1:])),'ordered zero-origin quote endpoints required')
    signed=[-p if charge else p for p in prices]
    require(signed==sorted(signed),'convex signed bid slopes required; no sorting permitted')
    return [(float(b-a),p) for a,b,p in zip(ps,ps[1:],prices)]


def relative_band(reference,lo,hi,delta,eligible):
    for x in (reference,lo,hi,delta): number(x,'relative band')
    require(type(eligible) is bool and 0<=delta<=1 and lo-1e-7<=reference<=hi+1e-7,'invalid relative-domain input')
    if not eligible: return reference,reference
    # Signed magnitude is a named research choice, not the contradictory print.
    radius=abs(reference)*delta
    return max(lo,reference-radius),min(hi,reference+radius)


def _signature(m):
    payload,vs,cs=highs_solver.serialize_model(m,_OPTIONS)
    return sha256(canonical(dict(payload=payload,domains=[(v.name,str(v.domain),v.fixed)for v in vs],rows=[c.name for c in cs]))).hexdigest()


@dataclass(frozen=True,eq=False)
class PricingLP:
    model: object
    input_bytes: bytes
    matrix_sha256: str
    audit_bytes: bytes
    @property
    def audit(self): return json.loads(self.audit_bytes)


@dataclass(frozen=True,eq=False)
class PriceSolution:
    report_bytes: bytes
    @property
    def report(self): return json.loads(self.report_bytes)


def validate_inputs(data):
    """Complete normalized DTO; a DTO alone does not establish native lineage."""
    d=deepcopy(data)
    require(d['schema']==_SCHEMA and d['scope']=='guangdong_provincial_research','explicit provincial research scope required')
    require(d['reference_choice']=='selected_RT_mean_research_not_official_DA_reference','explicit unresolved printed-stage choice required')
    require(d['storage_band_choice']=='signed_magnitude_research_printed_conflict_unresolved','explicit storage interpretation required')
    require(d['source_kind'] in ('issued_native_RT','diagnostic_fixture'),'declared source origin required')
    for k in ('source_id','eligibility_source','policy_source'): require(isinstance(d[k],str) and d[k].strip(),k+' required')
    start=stamp(d['start']); require(start.minute%15==start.second==start.microsecond==0,'quarter-aligned delivery required')
    require(stamp(d['source_available_at'])<=stamp(d['price_available_at'])<=start,'price must be available before first delivery')
    delta=number(d['delta'],'delta');require(0<=delta<=1,'delta domain')
    require(len(d['cells'])==24,'24 source cells required')
    for j,c in enumerate(d['cells']):
        require(stamp(c['start'])==start+j*timedelta(minutes=5) and stamp(c['end'])==start+(j+1)*timedelta(minutes=5),'exact contiguous 24-cell grid required')
    nodes=d['nodes'];require(nodes and len(nodes)==len(set(nodes)) and d['reference_node'] in nodes,'node inventory')
    require(set(d['load_mw'])==set(d['fixed_mw'])==set(nodes),'complete nodal load/fixed inventory required')
    for fam in ('load_mw','fixed_mw'):
        for vals in d[fam].values():
            require(len(vals)==24,'full source exogenous grid required')
            for v in vals:number(v,'source exogenous')
    for g,u in d['thermal'].items():
        require(u['node'] in nodes and len(u['rows'])==24,'complete thermal source required')
        number(u['initial_power_mw'],'thermal initial')
        widths(u['curve'])
        for q in range(8):
            rows=u['rows'][q*3:q*3+3];phase=rows[0]['phase']
            require(phase in ('stable','off','startup','shutdown') and {r['phase'] for r in rows}=={phase},g+': mixed-phase quarter unsupported')
            require(len({r['price_eligible'] for r in rows})==1,'mixed price eligibility unsupported')
            for r in rows:
                require(type(r['price_eligible']) is bool,'explicit price eligibility required')
                for k in ('reference_mw','end_mw','begin_mw','min_mw','max_mw','ramp_up_mw_h','ramp_down_mw_h','transition_cost_yuan_h','primary_fraction','accident_max_mw'):number(r[k],k)
                require(r['min_mw']>=0 and r['reference_mw']>=r['min_mw']-1e-7 and r['reference_mw']<=r['max_mw']+1e-7,'thermal reference outside physical range')
                require(not r['price_eligible'] or phase=='stable','only stable thermal eligible')
                require(0<=r['primary_fraction']<=1 and r['accident_max_mw']>=0,'thermal reserve domain')
                if phase!='stable':require(r['primary_fraction']==r['accident_max_mw']==0,'transition/off flexible reserve forbidden')
                if phase=='off':require(r['reference_mw']==r['end_mw']==0,'off unit must be zero')
                if phase in ('startup','shutdown'):require(r['event_id'] and r['curve_source'],'source event/curve evidence required')
            if phase in ('startup','shutdown'):require(len({r['event_id'] for r in rows})==1,'mixed curve event unsupported')
    # V3 pre-native intake consistency: computed floats may carry roundoff.
    # Preserve their exact values; static declarations and LP bounds stay strict.
    for b,u in d['batteries'].items():
        require(u['node'] in nodes and len(u['rows'])==24,'complete battery inventory required')
        require(u['soc_source'] and stamp(u['soc_at'])==start,'interval initial SOC source required')
        for k in ('initial_energy_mwh','initial_power_mw','energy_min_mwh','energy_max_mwh','capacity_mwh','eta_charge','eta_discharge'):number(u[k],k)
        require(0<u['eta_charge']<=1 and 0<u['eta_discharge']<=1 and 0<=u['energy_min_mwh']<=u['energy_max_mwh']<=u['capacity_mwh'] and u['energy_min_mwh']-1e-7<=u['initial_energy_mwh']<=u['energy_max_mwh']+1e-7,'battery efficiency/energy domain')
        require(u['incremental_reserve']=={'positive':0,'negative':0,'primary':0,'accident':0},'unqualified battery reserve must be explicitly zero')
        widths(u['charge_curve'],True);widths(u['discharge_curve'])
        hours={}
        for j,r in enumerate(u['rows']):
            mode=r['mode'];require(mode in ('charge','discharge','idle') and r['hour_permission'] in ('charge','discharge','idle'),'storage mode/permission')
            require(mode=='idle' or mode==r['hour_permission'],'mode violates selected hour permission')
            hk=stamp(d['cells'][j]['start']).replace(minute=0,second=0,microsecond=0).isoformat()
            hours.setdefault(hk,set()).add(r['hour_permission']);require(len(hours[hk])==1,'opposite storage directions within hour')
            require(type(r['price_eligible']) is bool,'battery explicit price eligibility')
            for k in ('reference_mw','charge_max_mw','discharge_max_mw','ramp_up_mw_h','ramp_down_mw_h'):number(r[k],k)
            require(r['charge_max_mw']>=0 and r['discharge_max_mw']>=0,'nonnegative battery magnitude limits')
            require((-r['charge_max_mw']-1e-7<=r['reference_mw']<=1e-7 if mode=='charge' else -1e-7<=r['reference_mw']<=r['discharge_max_mw']+1e-7 if mode=='discharge' else abs(r['reference_mw'])<=1e-7),'battery mode/reference sign')
        for q in range(8):
            rows=u['rows'][q*3:q*3+3]
            require(len({r['hour_permission'] for r in rows})==1,'mixed battery direction quarter unsupported')
            require(len({r['price_eligible'] for r in rows})==1,'mixed battery price eligibility unsupported')
        for day,v in u['cycle_budget'].items():
            number(v['used_internal_mwh'],'computed throughput');number(v['limit_internal_mwh'],'declared throughput limit')
            require(v['limit_internal_mwh']>=0 and -1e-7<=v['used_internal_mwh']<=v['limit_internal_mwh']+1e-7,'dated cycle budget')
        if u['terminal'] is not None:
            require(start<stamp(u['terminal']['at'])<=start+timedelta(hours=2),'terminal must be source point within window')
    for q,r in enumerate(d['reserve_rows']):
        for k in ('positive_mw','negative_mw','primary_mw','accident_mw'):require(number(r[k],k)>=0,'reserve negative')
    require(len(d['reserve_rows'])==8,'eight independent reserve rows required')
    require(d['fixed_renewable_incremental_reserve']==0,'unqualified wind/solar flexible reserve prohibited')
    return d


def build_pricing_lp(data, *, probe=None):
    d=validate_inputs(data)
    if d['source_kind']=='issued_native_RT':
        key=sha256(canonical(d)).hexdigest();require(key in _LIVE_INPUTS,'live native RT source binding required; JSON is not authority')
        from .gd_r13_causal_rt import validate_rt_solution
        validate_rt_solution(_LIVE_INPUTS[key][0])
    m=pyo.ConcreteModel();G=list(d['thermal']);B=list(d['batteries']);N=list(d['nodes']);Q=range(8);J=range(24)
    m.T=pyo.Set(initialize=Q,ordered=True);m.J=pyo.Set(initialize=J,ordered=True);m.N=pyo.Set(initialize=N);m.G=pyo.Set(initialize=G);m.B=pyo.Set(initialize=B)
    m.zero=pyo.Var(bounds=(0,0));m.zero.fix(0)
    load={n:[mean(d['load_mw'][n][q*3:q*3+3])for q in Q]for n in N};fixed={n:[mean(d['fixed_mw'][n][q*3:q*3+3])for q in Q]for n in N}
    if probe:
        require(set(probe)=={'node','quarter','increment_mw'} and probe['node'] in N and probe['quarter'] in Q,'explicit energy-only finite-difference probe')
        load[probe['node']][probe['quarter']]+=number(probe['increment_mw'],'probe increment')
    m.tx=pyo.Var(m.G,m.J);m.tp=pyo.Var(m.G,m.T);m.c_thermal=pyo.ConstraintList();thermal_cost=0.
    keys=[];quotes={};lower={};upper={}
    for g,u in d['thermal'].items():
        quote=widths(u['curve']);quotes[g]=quote
        for q in Q:
            rr=u['rows'][q*3:q*3+3];ref=mean([r['reference_mw'] for r in rr]);lo=mean([r['min_mw']for r in rr]);hi=mean([r['max_mw']for r in rr]);eligible=rr[0]['price_eligible']
            lower[g,q],upper[g,q]=lo,hi
            a,b=relative_band(ref,lo,hi,d['delta'],eligible);m.tp[g,q].setlb(a);m.tp[g,q].setub(b)
            m.c_thermal.add(3*m.tp[g,q]==sum(m.tx[g,j] for j in range(q*3,q*3+3)))
            if rr[0]['phase']=='stable':keys.extend((g,q,k)for k in range(len(quote)))
            else:thermal_cost+=.25*mean([r['transition_cost_yuan_h']for r in rr])
        previous=u['initial_power_mw']
        for j,r in enumerate(u['rows']):
            x=m.tx[g,j];x.setlb(r['min_mw']);x.setub(r['max_mw'])
            if r['phase']!='stable' or not r['price_eligible']:x.fix(r['reference_mw'])
            if r['phase']=='stable':
                m.c_thermal.add(x-previous<=r['ramp_up_mw_h']/12);m.c_thermal.add(previous-x<=r['ramp_down_mw_h']/12)
                previous=x
            elif r['phase'] in ('startup','shutdown'):
                m.c_thermal.add(previous+m.zero==r['begin_mw']);previous=r['end_mw']
            else:
                m.c_thermal.add(x-previous<=r['ramp_up_mw_h']/12);m.c_thermal.add(previous-x<=r['ramp_down_mw_h']/12);previous=0.
    m.K=pyo.Set(initialize=keys,dimen=3);m.seg=pyo.Var(m.K,domain=pyo.NonNegativeReals,bounds=lambda _,g,q,k:(0,quotes[g][k][0]))
    for g,q in {(g,q)for g,q,k in keys}:m.c_thermal.add(m.tp[g,q]==sum(m.seg[g,q,k]for k in range(len(quotes[g]))))
    thermal_cost+=sum(.25*quotes[g][k][1]*m.seg[g,q,k]for g,q,k in keys)
    thermal_cost+=sum(r.get('event_fee_yuan',0.)for u in d['thermal'].values()for r in u['rows'])
    m.bc=pyo.Var(m.B,m.J,domain=pyo.NonNegativeReals);m.bd=pyo.Var(m.B,m.J,domain=pyo.NonNegativeReals);m.bp=pyo.Var(m.B,m.T);m.be=pyo.Var(m.B,range(25));m.c_battery=pyo.ConstraintList();storage_cost=0.;bkeys=[];bquotes={}
    for b,u in d['batteries'].items():
        m.be[b,0].fix(u['initial_energy_mwh']);prev=u['initial_power_mw'];by_day={}
        for j,r in enumerate(u['rows']):
            m.bc[b,j].setub(r['charge_max_mw']if r['mode']=='charge' else 0);m.bd[b,j].setub(r['discharge_max_mw']if r['mode']=='discharge' else 0)
            p=m.bd[b,j]-m.bc[b,j]
            if not r['price_eligible']:m.c_battery.add(p==r['reference_mw'])
            m.c_battery.add(p-prev<=r['ramp_up_mw_h']/12);m.c_battery.add(prev-p<=r['ramp_down_mw_h']/12);prev=p
            m.be[b,j+1].setlb(u['energy_min_mwh']);m.be[b,j+1].setub(u['energy_max_mwh'])
            m.c_battery.add(m.be[b,j+1]==m.be[b,j]+(u['eta_charge']*m.bc[b,j]-m.bd[b,j]/u['eta_discharge'])/12)
            day=stamp(d['cells'][j]['start']).date().isoformat();by_day.setdefault(day,[]).append((u['eta_charge']*m.bc[b,j]+m.bd[b,j]/u['eta_discharge'])/12)
        for day,terms in by_day.items():
            require(day in u['cycle_budget'],'missing dated cycle budget');v=u['cycle_budget'][day];m.c_battery.add(sum(terms)<=v['limit_internal_mwh']-v['used_internal_mwh'])
        if u['terminal'] is not None:
            pos=next((j+1 for j,c in enumerate(d['cells'])if stamp(c['end'])==stamp(u['terminal']['at'])),None);require(pos is not None,'terminal off cell grid');m.be[b,pos].fix(u['terminal']['energy_mwh'])
        for target in u.get('interior_energy_targets',[]):
            pos=next((j+1 for j,c in enumerate(d['cells'])if stamp(c['end'])==stamp(target['at'])),None);require(pos is not None,'interior source SOC target off-grid');m.be[b,pos].fix(target['energy_mwh'])
        for q in Q:
            rr=u['rows'][3*q:3*q+3];mode=rr[0]['hour_permission'];ref=mean([r['reference_mw']for r in rr]);lo=-mean([r['charge_max_mw'] if r['mode']=='charge' else 0. for r in rr])if mode=='charge'else 0.;hi=mean([r['discharge_max_mw'] if r['mode']=='discharge' else 0. for r in rr])if mode=='discharge'else 0.
            a,z=relative_band(ref,lo,hi,d['delta'],rr[0]['price_eligible']);m.bp[b,q].setlb(a);m.bp[b,q].setub(z);m.c_battery.add(3*m.bp[b,q]==sum(m.bd[b,j]-m.bc[b,j]for j in range(q*3,q*3+3)))
            quote=widths(u['charge_curve'],True)if mode=='charge'else widths(u['discharge_curve']);bquotes[b,q]=[(w,-p if mode=='charge'else p)for w,p in quote];bkeys.extend((b,q,k)for k in range(len(quote)))
    m.BK=pyo.Set(initialize=bkeys,dimen=3);m.bseg=pyo.Var(m.BK,domain=pyo.NonNegativeReals,bounds=lambda _,b,q,k:(0,bquotes[b,q][k][0]))
    for b in B:
        for q in Q:
            sign=-1 if d['batteries'][b]['rows'][3*q]['hour_permission']=='charge'else 1
            m.c_battery.add(sign*m.bp[b,q]==sum(m.bseg[b,q,k]for k in range(len(bquotes[b,q]))))
    storage_cost=sum(.25*bquotes[b,q][k][1]*m.bseg[b,q,k]for b,q,k in bkeys)
    m.inj=pyo.Expression(m.N,m.T,rule=lambda _,n,q:m.zero+sum(m.tp[g,q]for g in G if d['thermal'][g]['node']==n)+sum(m.bp[b,q]for b in B if d['batteries'][b]['node']==n)+fixed[n][q]-load[n][q])
    m.c_balance=pyo.Constraint(m.T,rule=lambda _,q:sum(m.inj[n,q]for n in N)==0)
    # Balance RHS representation must be generation = demand so raw dual has positive energy sign.
    line_factors=dc_ptdf(tuple(N),d['lines'],d['reference_node']);line_factors,ptdf_audit=normalize_topology_ptdf(tuple(N),d['lines'],d['reference_node'],line_factors)
    section_factors={s:{n:sum(w*line_factors[l][n]for l,w in r['line_weights'].items())for n in N}for s,r in d['sections'].items()}
    m.L=pyo.Set(initialize=list(d['lines']));m.S=pyo.Set(initialize=list(d['sections']))
    m.flow=pyo.Expression(m.L,m.T,rule=lambda _,l,q:sum(line_factors[l][n]*m.inj[n,q]for n in N));m.section_flow=pyo.Expression(m.S,m.T,rule=lambda _,s,q:sum(section_factors[s][n]*m.inj[n,q]for n in N))
    m.c_line_upper=pyo.Constraint(m.L,m.T,rule=lambda _,l,q:m.flow[l,q]<=d['lines'][l]['upper_mw'][q]);m.c_line_lower=pyo.Constraint(m.L,m.T,rule=lambda _,l,q:-m.flow[l,q]<=-d['lines'][l]['lower_mw'][q])
    m.c_section_upper=pyo.Constraint(m.S,m.T,rule=lambda _,s,q:m.section_flow[s,q]<=d['sections'][s]['upper_mw'][q]);m.c_section_lower=pyo.Constraint(m.S,m.T,rule=lambda _,s,q:-m.section_flow[s,q]<=-d['sections'][s]['lower_mw'][q])
    m.primary=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals);m.accident=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals);m.c_reserve=pyo.ConstraintList()
    for q,r in enumerate(d['reserve_rows']):
        # Fixed RE/tie plus signed battery injection is energy baseline, NOT flexible credit.
        fixed_total=sum(fixed[n][q]for n in N)+sum(m.bp[b,q]for b in B)
        demand=sum(mean(d['load_mw'][n][q*3:q*3+3])for n in N)
        m.c_reserve.add(m.zero+sum(upper[g,q]for g in G)+fixed_total>=demand+r['positive_mw'])
        m.c_reserve.add(m.zero+sum(lower[g,q]for g in G)+fixed_total<=demand-r['negative_mw'])
        for g in G:
            rr=d['thermal'][g]['rows'][q*3:q*3+3]
            cap=mean([x['max_mw']*x['primary_fraction']for x in rr]);acc=mean([x['accident_max_mw']for x in rr])
            m.primary[g,q].setub(cap);m.accident[g,q].setub(acc)
            m.c_reserve.add(m.primary[g,q]+m.tp[g,q]<=upper[g,q]);m.c_reserve.add(m.accident[g,q]+m.tp[g,q]<=upper[g,q])
        m.c_reserve.add(m.zero+sum(m.primary[g,q]for g in G)>=r['primary_mw']);m.c_reserve.add(m.zero+sum(m.accident[g,q]for g in G)>=r['accident_mw'])
    m.objective=pyo.Objective(expr=thermal_cost+storage_cost,sense=pyo.minimize);m.dual=pyo.Suffix(direction=pyo.Suffix.IMPORT)
    audit=dict(kind='independent_eight_quarter_research_price_LP',source_id=d['source_id'],source_kind=d['source_kind'],start=d['start'],price_available_at=d['price_available_at'],quarter_count=8,duration_h=.25,physical_lift_cells=24,official_model_equivalence=False,dispatch_executable=False,ED_duals_used=False,objective_is_settlement=False,line_ptdf=line_factors,section_ptdf=section_factors,ptdf_audit=ptdf_audit,probe=probe,scope=d['scope'],network_slacks='hard_zero_research_restriction',formula_source='regional PDF137 section3.4.4.18, provincial projection only',reference_choice=d['reference_choice'],storage_band_choice=d['storage_band_choice'])
    audit['original_RT_cost_yuan']=d.get('original_RT_cost_yuan')
    audit['selected_mean_price_objective_yuan']=_selected_mean_cost(d)
    audit['price_ED_cost_interpretation']='separate original ED cost, cost of quarter means, independent optimum; none is settlement'
    audit['derivative_scope']='nodal energy-only demand perturbation; reserve demand/requirements held fixed; no reserve dual adder'
    audit['hourly_price_rule']='arithmetic mean of independently solved quarter prices, not mean ED five-minute duals'
    audit['price_clipped']=False
    audit['price_dual_unique']=False
    result=PricingLP(m,canonical(d),_signature(m),canonical(audit));_MODELS[result]=(result.input_bytes,result.matrix_sha256,result.audit_bytes);return result


def assert_price_model(p):
    require(type(p)is PricingLP and p in _MODELS,'issued fresh price model required');raw,sig,audit=_MODELS[p]
    require((p.input_bytes,p.matrix_sha256,p.audit_bytes)==(raw,sig,audit) and _signature(p.model)==sig,'price model/source mutated')
    d=json.loads(raw)
    if d['source_kind']=='issued_native_RT':
        key=sha256(raw).hexdigest();require(key in _LIVE_INPUTS,'native lineage absent')
        from .gd_r13_causal_rt import validate_rt_solution
        validate_rt_solution(_LIVE_INPUTS[key][0])
    return p.audit


def solve_pricing_lp(p,*,time_limit_s=60.):
    audit=assert_price_model(p);options=highs_solver.validate_options(dict(_OPTIONS,time_limit=float(time_limit_s)));solver=highs_solver.HighsSolver(options);m=p.model
    report=dict(audit,status='unavailable',prices=None,official_price=None,published=False,whole_day=False,model_sha256=p.matrix_sha256,actual_started_at_utc=datetime.now(timezone.utc).isoformat(),clock_basis='synthetic_logical_availability_distinct_from_actual_runtime',timely_operational_execution_verified=False)
    try:
        require(highs_solver.native_solver_path_intact(),'native solver path replaced');solver.solve(m);assert_price_model(p)
        require(solver.status=='optimal' and solver.primal_available and solver.dual_available and highs_solver.native_solver_path_intact(),'genuine optimal native LP with duals required')
        kkt=_audit_optimum(m,options);prices={n:[]for n in m.N};raw=[]
        for q in m.T:
            balance=number(m.dual[m.c_balance[q]],'balance dual')/.25;rec=dict(quarter=q,balance_raw=m.dual[m.c_balance[q]],lines={},sections={})
            for family,ids,up,lo in [('lines',m.L,m.c_line_upper,m.c_line_lower),('sections',m.S,m.c_section_upper,m.c_section_lower)]:
                for key in ids:
                    a=number(m.dual[up[key,q]],'network dual');b=number(m.dual[lo[key,q]],'network dual');require(a<=1e-7 and b<=1e-7,'wrong <= dual signs');rec[family][key]=dict(upper_raw=a,lower_raw=b,tau_upper=-a/.25,tau_lower=-b/.25)
            for n in m.N:
                price=balance-sum((r['tau_upper']-r['tau_lower'])*audit['line_ptdf'][l][n]for l,r in rec['lines'].items())-sum((r['tau_upper']-r['tau_lower'])*audit['section_ptdf'][s][n]for s,r in rec['sections'].items())
                prices[n].append(number(price,'LMP'))
            raw.append(rec)
        report.update(status='optimal_research',prices=prices,raw_duals=raw,kkt=kkt,objective_yuan=pyo.value(m.objective),solver_version=solver.solver_version,native_settings=solver.native_settings,price_primals=dict(thermal={g:[pyo.value(m.tp[g,q])for q in m.T]for g in m.G},battery={b:[pyo.value(m.bp[b,q])for q in m.T]for b in m.B}))
    except (ValueError,RuntimeError,KeyError,TypeError)as exc:report['error']=str(exc)
    report['actual_completed_at_utc']=datetime.now(timezone.utc).isoformat()
    result=PriceSolution(canonical(report));_SOLVES[result]=(p,result.report_bytes,_solution_snapshot(m));return result


def _solution_snapshot(m):
    return canonical(dict(primal=[(v.name,v.value)for v in m.component_data_objects(pyo.Var)],dual=[(r.name,m.dual.get(r))for r in m.component_data_objects(pyo.Constraint,active=True)]))


def accepted_price_report(solution):
    require(type(solution)is PriceSolution and solution in _SOLVES,'live independent native price receipt required');p,raw,snapshot=_SOLVES[solution];assert_price_model(p)
    require(solution.report_bytes==raw and snapshot==_solution_snapshot(p.model),'price receipt/primal changed');require(solution.report['status']=='optimal_research','unavailable price');return solution.report

# Live RT capabilities are kept apart from portable diagnostic DTOs. Archived
# hashes or JSON cannot recreate this selected-source authorization.
_LIVE_INPUTS={}


def build_price_inputs(rt_solution, *, policy):
    from .gd_r13_causal_rt import accepted_rt_report, pricing_source, validate_rt_solution
    validate_rt_solution(rt_solution);source=pricing_source(rt_solution);report=accepted_rt_report(rt_solution)
    require(set(policy)=={'delta','source','eligibility_source','available_at','thermal_eligibility','battery_eligibility'},'explicit research policy fields required')
    require(stamp(policy['available_at'])<=stamp(source['cutoff']),'price policy must preexist RT cutoff')
    e=source['energy_inputs'];cells=report['physical_cells'];reserve=source['reserve']
    require(set(policy['thermal_eligibility'])==set(e['thermal']) and set(policy['battery_eligibility'])==set(source['batteries']),'complete authored price eligibility inventory')
    for family in ('thermal_eligibility','battery_eligibility'):
        require(all(type(v)is bool for v in policy[family].values()),'explicit Boolean eligibility required')
    d=dict(schema=_SCHEMA,scope='guangdong_provincial_research',source_kind='issued_native_RT',source_id=report['physical_model_sha256'],
        start=source['start'],source_available_at=source['cutoff'],price_available_at=source['cutoff'],
        reference_choice='selected_RT_mean_research_not_official_DA_reference',storage_band_choice='signed_magnitude_research_printed_conflict_unresolved',
        policy_source=policy['source'],eligibility_source=policy['eligibility_source'],source_policy=deepcopy(policy),original_RT_cost_yuan=report['cost_components_yuan'],delta=policy['delta'],cells=[dict(start=c['start'],end=c['end'])for c in cells],
        nodes=list(e['buses']),reference_node=e['reference_buses'][0],thermal={},batteries={},lines={},sections={},
        load_mw={n:[e['load_mw'][n,j]for j in range(24)]for n in e['buses']},
        fixed_mw={n:[e['renewable_mw'][n,j]+e['physical_tie_mw'][n,j]for j in range(24)]for n in e['buses']},fixed_renewable_incremental_reserve=0,
        reserve_rows=[{name:mean([reserve[key][j]for j in range(q*3,q*3+3)])for name,key in [('positive_mw','positive_mw'),('negative_mw','negative_mw'),('primary_mw','primary_mw'),('accident_mw','contingency_mw')]}for q in range(8)])
    for g,u in e['thermal'].items():
        base=u['bid_base_mw'];require(base>0,'R13 original thermal source requires positive Pmin bid base')
        def constant(vals,label):
            require(len(set(vals))==1,label+': source coefficient varies over pricing horizon unsupported');return vals[0]
        points=[Decimal('0'),Decimal(str(base))];prices=[constant(list(u['base_bid_yuan_per_h'].values()),'base cost')/base]
        for seg in u['segments']:
            points.append(points[-1]+Decimal(str(seg['width_mw'])));prices.append(constant(list(seg['price_yuan_per_mwh'].values()),'thermal bid'))
        rows=[]
        for j,c in enumerate(cells):
            z=c['thermal'][g];s=source['thermal_schedule'][j][g];stable=z['phase']=='stable'
            rows.append(dict(phase=z['phase'],reference_mw=z['mean_mw'],begin_mw=z['begin_mw'],end_mw=z['end_mw'],min_mw=u['min_mw'][j]if stable else z['mean_mw'],max_mw=u['max_mw'][j]if stable else z['mean_mw'],ramp_up_mw_h=u['ramp_up_mw_per_h'][j],ramp_down_mw_h=u['ramp_down_mw_per_h'][j],transition_cost_yuan_h=s['cost'],event_fee_yuan=u['startup_yuan'][j]*s['start']+u['shutdown_yuan'][j]*s['stop'],price_eligible=policy['thermal_eligibility'][g]and stable,primary_fraction=reserve['primary_fraction'][g]if stable else 0.,accident_max_mw=int(reserve['contingency_eligible'][g])*u['ramp_up_mw_per_h'][j]/6 if stable else 0.,event_id=(g+':'+z['phase']+':'+str(j-z['curve_step']))if z['curve_step']is not None else None,curve_source=report['plan_sha256']if z['curve_step']is not None else None))
        d['thermal'][g]=dict(node=u['bus'],initial_power_mw=u['initial_power_mw'],curve=dict(breakpoints_mw=[str(x)for x in points],prices_yuan_per_mwh=prices),rows=rows)
    for b,u in source['batteries'].items():
        rows=[]
        for j,c in enumerate(cells):
            z=c['batteries'][b];rows.append(dict(mode=z['mode'],hour_permission='charge'if z['selected_hour_direction_permission']==-1 else 'discharge',reference_mw=z['power_mw'],charge_max_mw=u['charge_max_mw']if u['availability'][j]else 0.,discharge_max_mw=u['discharge_max_mw']if u['availability'][j]else 0.,ramp_up_mw_h=u['ramp_up_mw_per_h'],ramp_down_mw_h=u['ramp_down_mw_per_h'],price_eligible=policy['battery_eligibility'][b]))
        d['batteries'][b]=dict(node=u['bus'],initial_energy_mwh=u['initial_energy_mwh'],initial_power_mw=u['initial_power_mw'],energy_min_mwh=u['energy_min_mwh'],energy_max_mwh=u['energy_max_mwh'],capacity_mwh=u['energy_capacity_mwh'],eta_charge=u['_eta'],eta_discharge=u['_eta'],soc_source=source['projection']['source_receipt_id'] if 'source_receipt_id'in source['projection']else 'validated causal RT projection '+report['input_sha256'],soc_at=source['start'],charge_curve=deepcopy(u['charge_curve']),discharge_curve=deepcopy(u['discharge_curve']),rows=rows,incremental_reserve=dict(positive=0,negative=0,primary=0,accident=0),cycle_budget={day:dict(used_internal_mwh=v['used_internal_mwh'],limit_internal_mwh=2*u['energy_capacity_mwh']*v['limit_cycles'])for day,v in u['cycle_budget'].items()},terminal=dict(at=u['terminal_at'],energy_mwh=u['terminal_energy_mwh']))
    for l,r in e['lines'].items():
        for key in ('lower_mw','upper_mw'):
            for q in range(8):require(len({r[key][j]for j in range(q*3,q*3+3)})==1,'varying intra-quarter network limit unsupported')
        d['lines'][l]=dict(from_node=r['from_bus'],to_node=r['to_bus'],susceptance_mw_per_rad=r['susceptance_mw_per_rad'],lower_mw=[mean([r['lower_mw'][j]for j in range(q*3,q*3+3)])for q in range(8)],upper_mw=[mean([r['upper_mw'][j]for j in range(q*3,q*3+3)])for q in range(8)])
    require(not e['sections'],'unmapped section inventory forbidden')
    # Retain DA midnight boundary inside cross-day windows, not just window end.
    for b,u in d['batteries'].items():
        u['interior_energy_targets']=[dict(at=c['end'],energy_mwh=source['da_cells'][j]['batteries'][b]['energy_end_mwh'])for j,c in enumerate(cells[:-1])if stamp(c['end']).hour==stamp(c['end']).minute==0]
    validated=validate_inputs(d);key=sha256(canonical(validated)).hexdigest();_LIVE_INPUTS[key]=(rt_solution,deepcopy(policy));return validated


def run_window_pricing(solution,output,policy,*,time_limit_s=60.):
    """Retain reviewable source DTO, actual matrix, primal, duals and report."""
    from pathlib import Path
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    data=build_price_inputs(solution,policy=policy);p=build_pricing_lp(data)
    matrix,vs,rows=highs_solver.serialize_model(p.model,_OPTIONS)
    (out/'price_inputs.json').write_bytes(canonical(data));(out/'price_matrix.json').write_bytes(canonical(matrix));(out/'price_model_audit.json').write_bytes(p.audit_bytes)
    solved=solve_pricing_lp(p,time_limit_s=time_limit_s);(out/'price_result.json').write_bytes(solved.report_bytes)
    report=accepted_price_report(solved)
    (out/'price_primal_dual.json').write_bytes(canonical(dict(primal=[dict(name=v.name,value=v.value)for v in vs],dual=[dict(name=r.name,value=p.model.dual.get(r))for r in rows])))
    p.model.write(str(out/'price_model.lp'),io_options={'symbolic_solver_labels':True})
    return solved


def _selected_mean_cost(d):
    def integral(q,curve,charge=False):
        result=0.
        for width,price in widths(curve,charge):
            take=min(max(q,0.),width);result+=take*price;q-=take
        require(q<=1e-5,'bid coverage below selected mean')
        return -result if charge else result
    result=0.
    for u in d['thermal'].values():
        for q in range(8):
            rs=u['rows'][3*q:3*q+3];p=mean([r['reference_mw']for r in rs])
            result+=.25*(integral(p,u['curve'])if rs[0]['phase']=='stable'else mean([r['transition_cost_yuan_h']for r in rs]))
        result+=sum(r.get('event_fee_yuan',0.)for r in u['rows'])
    for u in d['batteries'].values():
        for q in range(8):
            rs=u['rows'][3*q:3*q+3];p=mean([r['reference_mw']for r in rs]);charge=rs[0]['hour_permission']=='charge'
            result+=.25*integral(abs(p),u['charge_curve']if charge else u['discharge_curve'],charge)
    return result
