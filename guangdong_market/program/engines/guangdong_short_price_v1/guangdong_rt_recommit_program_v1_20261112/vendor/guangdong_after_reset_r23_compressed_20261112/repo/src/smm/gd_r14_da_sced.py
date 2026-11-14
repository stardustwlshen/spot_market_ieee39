"""Fixed-known-state DA98 SCED and continuous five-minute research lift.

This is not free SCUC. Every construction creates new Pyomo components.
No solver is imported/started by construction. Physical bridge is an explicit
stronger research domain, not claimed to be the published DA98 equations.
"""
from copy import deepcopy
from hashlib import sha256
import json, math, sys, time
from pathlib import Path
import pyomo.environ as p
from . import highs_solver as hs

OPTIONS=dict(time_limit=180.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.)
def require(ok,msg):
    if not ok: raise ValueError(msg)
def canonical(d):return json.dumps(d,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(d):return sha256(canonical(d)).hexdigest()

def attach_physical_lift(m,d,probe=None):
    """Fresh physical variables shared as a formula library, never model clone.

All physical balance rows are retained. Therefore a market-load probe also
perturbs the three mapped physical loads. Pricing must include both sets of
balance/network dual derivatives, not just the redundant market rows.
"""
    from .gd_r14_da_contract import validate_lift_contract_binding
    validate_lift_contract_binding(d)
    x=d['physical_lift'];cells=x['cells'];J=range(len(cells));R=list(d['resources']);N=d['nodes'];L=list(d['network']);mapping=x['market_to_physical'];B=[r for r in R if d['resources'][r]['kind']=='battery']
    require(len(mapping)==98 and len(cells)>=294,'complete lift and 98 mappings required')
    require(len(set(j for ids in mapping for j in ids))==294 and all(len(ids)==3 for ids in mapping),'nonoverlapping three-cell market mappings required')
    from datetime import datetime,timedelta
    start=datetime.fromisoformat(d['grid'][0]['start'])
    for j,c in enumerate(cells):
        require(datetime.fromisoformat(c['start'])==start+timedelta(minutes=5*j) and datetime.fromisoformat(c['end'])==start+timedelta(minutes=5*(j+1)),'continuous physical clock required')
    for t,ids in enumerate(mapping):
        require(ids==list(range(ids[0],ids[0]+3)) and 0<=ids[0] and ids[-1]<len(cells),'contiguous in-domain market mapping')
        require(d['grid'][t]['actual_time']==d['grid'][t]['end'] and d['grid'][t]['power_semantics']=='interval_mean','explicit mean-window end label required')
        require(cells[ids[0]]['start']==d['grid'][t]['start'] and cells[ids[-1]]['end']==d['grid'][t]['end'],'market/physical timestamps mismatch')
    require(mapping[:96]==[list(range(3*t,3*t+3)) for t in range(96)],'D96 mapping required')
    require(x['availability_is_simulated'] is True and x['future_RT_used'] is False,'explicit prospective research provenance required')
    require(x['state_source'] and x['extension_policy'],'fixed state evidence required')
    m.physical_power=p.Var(R,J);m.physical_energy=p.Var(B,range(len(cells)+1));m.physical_rows=p.ConstraintList();m.physical_accident=p.Var(R,J,domain=p.NonNegativeReals);m.physical_primary=p.Var(R,J,domain=p.NonNegativeReals);m.physical_up=p.Var(R,J,domain=p.NonNegativeReals);m.physical_down=p.Var(R,J,domain=p.NonNegativeReals)
    for r,u in d['resources'].items():
        rows=x['resources'][r]['rows'];require(len(rows)==len(cells),'full physical resource path required');prev=u['initial_power_mw']
        if r in B:m.physical_energy[r,0].fix(u['initial_energy_mwh'])
        for j,row in enumerate(rows):
            v=m.physical_power[r,j];lo=row['min_mw'];hi=row['max_mw'];require(math.isfinite(lo) and lo<=hi,'physical bounds');v.setlb(lo);v.setub(hi)
            if r not in B and row['phase'] in ('startup','shutdown'):
                require(row['curve_source'] and row['event_id'],'transition curve evidence required')
                require(abs(row['mean_mw']-(row['begin_mw']+row['end_mw'])/2)<1e-7,'linear curve integration mismatch')
                require(lo==hi==row['mean_mw'],'transition curve mean must be fixed')
                m.physical_rows.add(prev+m.zero==row['begin_mw']);prev=row['end_mw']
            else:
                m.physical_rows.add(v-prev<=row['ramp_up_mw_h']/12);m.physical_rows.add(prev-v<=row['ramp_down_mw_h']/12);prev=v
            for var,key in ((m.physical_accident,'accident_cap_mw'),(m.physical_primary,'primary_cap_mw')):
                var[r,j].setub(row[key]);m.physical_rows.add(var[r,j]<=hi-v)
            for var,key in ((m.physical_up,'positive_cap_mw'),(m.physical_down,'negative_cap_mw')):
                require(row[key]>=0,'invalid reserve cap');var[r,j].setub(row[key])
                if not row['reserve_eligible']:require(row[key]==0,'unqualified positive/negative reserve')
            m.physical_rows.add(m.physical_up[r,j]<=hi-v);m.physical_rows.add(m.physical_down[r,j]<=v-lo)
            if r in B:
                require(row['accident_cap_mw']==row['primary_cap_mw']==row['positive_cap_mw']==row['negative_cap_mw']==0,'battery reserve forbidden')
                mode=row['mode'];require(mode in ('charge','discharge','idle'),'physical storage mode')
                require((mode=='charge' and hi<=0)or(mode=='discharge' and lo>=0)or(mode=='idle' and lo==hi==0),'storage sign')
                coef=u['eta_charge'] if mode=='charge' else 1/u['eta_discharge']
                m.physical_energy[r,j+1].setlb(u['energy_min_mwh']);m.physical_energy[r,j+1].setub(u['energy_max_mwh'])
                m.physical_rows.add(m.physical_energy[r,j+1]==m.physical_energy[r,j]-coef*v/12)
        for t,ids in enumerate(mapping):m.physical_rows.add(3*m.power[r,t]==sum(m.physical_power[r,j] for j in ids))
        if r in B:
            for h in range((len(cells)+11)//12):require(not {'charge','discharge'}<={z['mode']for z in rows[12*h:12*h+12]},'opposite storage directions within hour')
            for target in u['terminal_targets']:
                m.physical_rows.add(m.physical_energy[r,max(mapping[target['index']])+1]==target['energy_mwh'])
            extra_targets=x['resources'][r].get('terminal_targets',[])
            seen={max(mapping[z['index']])+1 for z in u['terminal_targets']}
            for target in extra_targets:
                require(set(target)=={'boundary_index','energy_mwh','source'},'explicit physical terminal fields required')
                index=target['boundary_index'];energy=target['energy_mwh']
                require(type(index)is int and 0<index<=len(cells) and index not in seen,'unique in-range physical terminal boundary required');seen.add(index)
                require(type(energy)in(int,float) and math.isfinite(energy) and u['energy_min_mwh']<=energy<=u['energy_max_mwh'],'physical terminal outside original SOC bounds')
                require(isinstance(target['source'],str) and 'research' in target['source'],'additional terminal must state research source')
                m.physical_rows.add(m.physical_energy[r,index]==energy)
            for day,budget in x['resources'][r]['cycle_budget'].items():
                ids=[j for j,c in enumerate(cells)if c['start'][:10]==day]
                terms=[(-u['eta_charge'] if rows[j]['mode']=='charge' else 1/u['eta_discharge'])*m.physical_power[r,j]/12 for j in ids]
                require(budget['used_internal_mwh']>=0 and budget['limit_internal_mwh']>=budget['used_internal_mwh'],'cycle ledger invalid')
                m.physical_rows.add(m.zero+sum(terms)<=budget['limit_internal_mwh']-budget['used_internal_mwh'])
    loads=deepcopy(x['load_mw'])
    if probe:
        for j in mapping[probe['index']]:loads[probe['node']][j]+=probe['increment_mw']
    m.physical_injection=p.Expression(N,J,rule=lambda _,n,j:m.zero+sum(m.physical_power[r,j]for r in R if d['resources'][r]['node']==n)+x['fixed_injection_mw'][n][j]-loads[n][j])
    m.physical_balance=p.Constraint(J,rule=lambda _,j:sum(m.physical_injection[n,j]for n in N)==0)
    m.physical_flow=p.Expression(L,J,rule=lambda _,l,j:sum(d['network'][l]['ptdf'][n]*m.physical_injection[n,j]for n in N))
    m.physical_line_upper=p.Constraint(L,J,rule=lambda _,l,j:m.physical_flow[l,j]<=x['network'][l]['upper_mw'][j])
    m.physical_line_lower=p.Constraint(L,J,rule=lambda _,l,j:-m.physical_flow[l,j]<=-x['network'][l]['lower_mw'][j])
    for j,req in enumerate(x['reserve']):
        eligible=[r for r in R if x['resources'][r]['rows'][j]['reserve_eligible']]
        m.physical_rows.add(m.zero+sum(m.physical_up[r,j]for r in R)>=req['positive_mw'])
        m.physical_rows.add(m.zero+sum(m.physical_down[r,j]for r in R)>=req['negative_mw'])
        m.physical_rows.add(m.zero+sum(m.physical_accident[r,j]for r in R)>=req['accident_mw'])
        m.physical_rows.add(m.zero+sum(m.physical_primary[r,j]for r in R)>=req['primary_mw'])
    return dict(physical_cells=len(cells),market_points=98,hidden_cells=len(cells)-294,no_inventory_reset=True,all_physical_balances=True,extension_policy=x['extension_policy'])

def build_model(data):
    d=json.loads(canonical(data));require(d['stage']=='fixed_known_state_SCED','SCED stage declaration');require(d['profile']['mode']=='research','strict unsupported');require(len(d['grid'])==98,'98 points required');require(d['fixed_state_available_at']<=d['planned_publication_at'],'fixed status must be available before publication')
    from .gd_r14_da_contract import validate_lift_contract_binding
    validate_lift_contract_binding(d)
    upstream_ids=('v3_native_BESS_modes_on_v2_thermal','v4_native_BESS_modes_original_bounds_tail')
    upstream_trace=any(k.startswith(('upstream_', 'native_upstream_'))for k in d)or any(str(a.get('role','')).startswith('upstream_BESS_')for a in d.get('source_binding',{}).get('artifacts',[]))
    if d.get('strategy_id')in upstream_ids or upstream_trace:
        from .gd_r14_da_bess_prepare import validate_frozen_binding
        validate_frozen_binding(d)
    m=p.ConcreteModel('DA98_fixed_known_state_SCED');T=range(98);R=list(d['resources']);N=d['nodes'];L=list(d['network']);m.zero=p.Var(bounds=(0,0));m.zero.fix(0);m.power=p.Var(R,T);m.rows=p.ConstraintList()
    keys=[(r,t,k)for r in R for t in T for k in range(len(d['resources'][r]['rows'][t]['segments']))];m.seg=p.Var(keys,domain=p.NonNegativeReals);cost=0
    for r,u in d['resources'].items():
        for t,row in enumerate(u['rows']):
            require(row['min_mw']<=row['max_mw'],'invalid bounds');m.power[r,t].setlb(row['min_mw']);m.power[r,t].setub(row['max_mw'])
            m.rows.add(m.power[r,t]==row['min_mw']+sum(m.seg[r,t,k]for k in range(len(row['segments']))))
            cost+=d['grid'][t]['objective_weight_h']*row['offer_intercept_yuan_h']
            for k,s in enumerate(row['segments']):m.seg[r,t,k].setub(s['width_mw']);cost+=d['grid'][t]['objective_weight_h']*s['price_yuan_mwh']*m.seg[r,t,k]
    m.injection=p.Expression(N,T,rule=lambda _,n,t:m.zero+sum(m.power[r,t]for r in R if d['resources'][r]['node']==n)+d['fixed_injection_mw'][n][t]-d['load_mw'][n][t]);m.balance=p.Constraint(T,rule=lambda _,t:sum(m.injection[n,t]for n in N)==0)
    m.flow=p.Expression(L,T,rule=lambda _,l,t:sum(d['network'][l]['ptdf'][n]*m.injection[n,t]for n in N));m.line_upper=p.Constraint(L,T,rule=lambda _,l,t:m.flow[l,t]<=d['network'][l]['upper_mw'][t]);m.line_lower=p.Constraint(L,T,rule=lambda _,l,t:-m.flow[l,t]<=-d['network'][l]['lower_mw'][t])
    for group in d['groups']:
        e=sum(.25*m.power[r,t]for r in group['members']for t in range(96));m.rows.add(p.inequality(group['daily_min_mwh'],m.zero+e,group['daily_max_mwh']))
    lift=attach_physical_lift(m,d);m.objective=p.Objective(expr=cost);m.dual=p.Suffix(direction=p.Suffix.IMPORT)
    payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS))
    return m,d,dict(stage='fixed_known_state_SCED',free_SCUC_completed=False,official_equivalence=False,original_all_gas_off_reachable=False,input_sha256=digest(d),matrix_sha256=digest(payload),variables=len(vs),rows=len(cs),integer_variables=sum(v['kind']!='C'for v in payload['variables']),physical_lift=lift)

def validate_worker_response(raw):
    """Fail once on worker/protocol errors without inventing solver status.

    An error-only response does not establish optimization or infeasibility.
    The caller retains raw artifacts and never retries automatically.
    """
    if not isinstance(raw,dict):
        raise RuntimeError('SCED worker protocol error: response must be an object; evidence retained; no retry')
    if 'error' in raw:
        raise RuntimeError('SCED worker error before accepted solve: '+str(raw['error'])+'; solver status not established; evidence retained; no retry')
    if 'status' not in raw:
        raise RuntimeError('SCED worker protocol error: missing status; solver outcome not established; evidence retained; no retry')
    if raw.get('status')!='optimal' or raw.get('dual_valid') is not True:
        raise RuntimeError('SCED optimal LP with duals required; received status='+str(raw.get('status'))+'; evidence retained; no retry or input repair')
    return raw

def solve_native(data,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);require(not(out/'request.json').exists(),'do not overwrite native evidence')
    m,d,audit=build_model(data);payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));payload['deadline']=time.perf_counter()+OPTIONS['time_limit'];payload['preparation_elapsed_s']=0.
    (out/'request.json').write_bytes(canonical(payload));(out/'input.json').write_bytes(canonical(d));(out/'names.json').write_bytes(canonical(dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])))
    result=hs.run_child([sys.executable,str(Path(hs.__file__).with_name('highs_worker.py')),str(out/'request.json'),str(out/'response.json')],env=hs.child_environment(),timeout=OPTIONS['time_limit'],tee=False)
    require(result.returncode==0 and(out/'response.json').exists(),'SCED native failed; evidence retained');raw=json.loads((out/'response.json').read_text());validate_worker_response(raw);values=hs.validate_solution(payload,raw)
    for v,z in zip(vs,values):v.set_value(z)
    require(raw.get('backend')=='highs' and raw.get('solver_version')=='1.15.1' and raw['native_settings']['threads']==1 and raw['native_settings']['mip_rel_gap']==.001,'required native version/settings')
    from .gd_r14_da_lmp import audit_kkt
    kkt=audit_kkt(payload,raw)
    residuals=write_full_residuals(payload,values,vs,cs,out/'all_primal_residuals.json.gz')
    require(residuals['maximum_violation']<=1e-6,'SCED raw residual gate')
    accounting=cost_accounting(m,d)
    snapshot=dict(stage='SCED',status='optimal',source_kind='native_highs_sced',power_mw={r:[p.value(m.power[r,t])for t in range(98)]for r in d['resources']},online={r:[row['online']for row in u['rows']]for r,u in d['resources'].items()},mode={r:[row['mode']for row in u['rows']]for r,u in d['resources'].items()if u['kind']=='battery'})
    audit.update(kkt=kkt,residual_audit=residuals,cost_accounting=accounting,artifacts=[dict(role='sced_'+role,path=str((out/name).resolve()),sha256=sha256((out/name).read_bytes()).hexdigest())for role,name in [('request','request.json'),('response','response.json'),('names','names.json'),('input','input.json')]],snapshot=snapshot,objective_yuan=raw['objective'],source_unchanged=digest(data)==digest(d),solver_version=raw.get('solver_version'),native_settings=raw.get('native_settings'),stage_counts=dict(free_SCUC=0,fixed_SCED=1,independent_LMP=0))
    (out/'result.json').write_bytes(canonical(audit));return audit

def pricing_inputs(data,sced_result,delta=.1):
    d=deepcopy(data);require(sced_result['source_unchanged'] and sced_result['input_sha256']==digest(data),'SCED input binding');d['schema']='gd.r14.da_lmp.v1';d['sced_snapshot']=deepcopy(sced_result['snapshot']);d['source_binding']['sced_snapshot_sha256']=digest(d['sced_snapshot']);d['delta']=delta;d['source_binding']['artifacts'].extend(sced_result.get('artifacts',[]))
    for r,u in d['resources'].items():
        for t,row in enumerate(u['rows']):row['sced_mw']=d['sced_snapshot']['power_mw'][r][t]
    return d


def write_full_residuals(payload,values,vs,cs,path):
    import gzip
    records=[];worst=0.
    for v,z,name in zip(payload['variables'],values,vs):
        violation=max(0.,0. if v['lb'] is None else v['lb']-z,0. if v['ub'] is None else z-v['ub']);worst=max(worst,violation);records.append(dict(kind='variable',name=name.name,value=z,lower=v['lb'],upper=v['ub'],violation=violation))
    for row,c in zip(payload['rows'],cs):
        value=math.fsum(values[i]*a for i,a in row['terms']);violation=max(0.,0. if row['lb'] is None else row['lb']-value,0. if row['ub'] is None else value-row['ub']);worst=max(worst,violation);records.append(dict(kind='constraint',name=c.name,value=value,lower=row['lb'],upper=row['ub'],violation=violation))
    with gzip.open(path,'wb')as f:f.write(canonical(records))
    return dict(maximum_violation=worst,variables=len(vs),rows=len(cs),all_rows_and_bounds_file=str(path),sha256=sha256(Path(path).read_bytes()).hexdigest())

def cost_accounting(m,d):
    market=[]
    for t in range(98):
        cost=0.
        for r,u in d['resources'].items():
            row=u['rows'][t];cost+=row['offer_intercept_yuan_h']+sum(s['price_yuan_mwh']*p.value(m.seg[r,t,k])for k,s in enumerate(row['segments']))
        market.append(cost*d['grid'][t]['objective_weight_h'])
    def integral(power,curve):
        return sum(max(0.,min(power,s['p_end'])-s['p_start'])*s['price']for s in curve)
    physical=[]
    for j in range(len(d['physical_lift']['cells'])):
        cost=0.
        for r,u in d['resources'].items():
            power=p.value(m.physical_power[r,j])
            if u['kind']=='generator' and 'original_offer'in u:cost+=integral(power,u['original_offer'])
            elif u['kind']=='battery' and 'original_curves'in u:
                curve=u['original_curves']['charge'if power<0 else'discharge'];cost+=(-1 if power<0 else 1)*integral(abs(power),curve)
        physical.append(cost/12)
    used=set(j for ids in d['physical_lift']['market_to_physical']for j in ids)
    return dict(D96_market_energy_offer_yuan=sum(market[:96]),representatives_weighted_offer_yuan=sum(market[96:]),market_objective_yuan=sum(market),D96_physical_energy_offer_evaluation_yuan=sum(physical[:288]),hidden_bridge_energy_offer_evaluation_yuan=sum(v for j,v in enumerate(physical)if j not in used),physical48h_energy_offer_evaluation_yuan=sum(physical),hidden_cost_in_objective=False,physical48h_economic_optimum_claim=False,startup_and_no_load_fees_included=False,not_settlement=True)
