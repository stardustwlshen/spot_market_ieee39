"""Independent DA98 price LP. Explicit research interpretations, never SCED duals.

Only the serializer/validated native worker are reused. No inherited model cloned.
DTO and assembled LP are fingerprinted; no native library is imported at build.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import time
import pyomo.environ as p
from . import highs_solver as hs

SCHEMA = 'gd.r14.da_lmp.v1'
OPTIONS = dict(time_limit=120., threads=1, mip_rel_gap=.001, mip_abs_gap=0.)

def require(ok, text):
    if not ok: raise ValueError(text)

def canonical(d): return json.dumps(d, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def digest(d): return sha256(canonical(d)).hexdigest()
def numeric(v):
    require(type(v) in (int,float) and math.isfinite(v), 'finite numeric input required')
    return v

def band(reference, lo, hi, delta, eligible, online=True):
    for v in (reference,lo,hi,delta): numeric(v)
    require(type(eligible) is bool and type(online) is bool and 0<=delta<=1 and lo<=reference<=hi,'invalid pricing band')
    if not online:
        require(reference==0, 'offline SCED must be zero')
        return 0.,0.
    if not eligible: return reference,reference
    return max(lo,reference-abs(reference)*delta),min(hi,reference+abs(reference)*delta)

def validate(data):
    d=deepcopy(data)
    if d.get('physical_lift'):
        from .gd_r14_da_contract import validate_lift_contract_binding
        validate_lift_contract_binding(d)
    require(d['schema']==SCHEMA,'schema mismatch')
    require(d['profile']['mode'] in ('strict','research'),'explicit profile mode')
    # No complete strict profile is certified by this implementation.
    require(d['profile']['mode']=='research','strict certification blocked: representative-point and storage printed ambiguities unresolved')
    for k in ('research_profile','rule_source','delta_source','eligibility_source','initial_state_source','sced_source','parameter_source'):
        require(bool(d['profile'].get(k)), 'missing provenance '+k)
    numeric(d['delta']); require(0<=d['delta']<=1,'delta domain')
    require(d['profile'].get('storage_interpretation')=='signed_magnitude_fixed_sced_mode_research','storage interpretation must be explicit')
    require(d['profile'].get('excluded_resource_types') is not None,'unsupported resource inventory must be explicit')
    require(len(d['grid'])==98,'DA98 requires 96+2')
    start=datetime.fromisoformat(d['grid'][0]['start'])
    require(start.utcoffset()==timedelta(hours=8) and start.hour==start.minute==start.second==0,'China standard time midnight required')
    for t,g in enumerate(d['grid']):
        for k in ('objective_weight_h','physical_duration_h','ramp_elapsed_h'): require(numeric(g[k])>0,'positive explicit '+k)
        require(g['predecessor'] is None or type(g['predecessor']) is int and 0<=g['predecessor']<t,'acyclic predecessor required')
        require(type(g['settlement']) is bool and g['settlement']==(t<96),'settlement mask must select D96 only')
        if t<96:
            require(datetime.fromisoformat(g['start'])==start+timedelta(minutes=15*t),'D contiguous quarters')
            require(datetime.fromisoformat(g['end'])==start+timedelta(minutes=15*(t+1)),'D quarter end')
            require(g['objective_weight_h']==g['physical_duration_h']==g['ramp_elapsed_h']==.25 and g['predecessor']==(t-1 if t else None),'D quarter semantics')
        else:
            require(g['predecessor'] is not None,'representative state cannot reset to initial state')
            actual=datetime.fromisoformat(g['actual_time']);prev=d['grid'][g['predecessor']]
            previous=datetime.fromisoformat(prev['end'] if g['predecessor']<96 else prev['actual_time'])
            require(abs((actual-previous).total_seconds()/3600-g['ramp_elapsed_h'])<1e-9,'representative elapsed must match actual timestamps')
            require(g['kind'] in ('peak','valley') and datetime.fromisoformat(g['actual_time']).date()==(start+timedelta(days=1)).date() and g['constraint_scope'],'D+1 representative semantics required')
    require({x['kind'] for x in d['grid'][96:]}=={'peak','valley'},'two distinct representatives')
    nodes=d['nodes'];require(nodes and len(nodes)==len(set(nodes)),'nodes')
    for name in ('load_mw','fixed_injection_mw'):
        require(set(d[name])==set(nodes),'nodal inventory')
        for arr in d[name].values(): require(len(arr)==98 and all(math.isfinite(numeric(v)) for v in arr),'nodal series')
    require(len(d['reserve'])==98,'reserve series')
    for r in d['reserve']:
        for k in ('positive_mw','negative_mw','primary_mw','accident_mw'):require(numeric(r[k])>=0,'reserve requirement')
    for key,u in d['resources'].items():
        require(u['node'] in nodes and u['kind'] in ('generator','battery') and len(u['rows'])==98,'resource inventory')
        numeric(u['initial_power_mw'])
        for t,r in enumerate(u['rows']):
            band(r['sced_mw'],r['min_mw'],r['max_mw'],d['delta'],r['price_eligible'],r['online'])
            require(r['eligibility_reason'],'eligibility reason required')
            for k in ('ramp_up_mw_h','ramp_down_mw_h','positive_cap_mw','negative_cap_mw','primary_cap_mw','accident_cap_mw'):require(numeric(r[k])>=0,k)
            require(type(r['reserve_eligible']) is bool,'reserve qualification explicit')
            if not r['reserve_eligible']: require(all(r[k]==0 for k in ('positive_cap_mw','negative_cap_mw','primary_cap_mw','accident_cap_mw')),'unqualified reserve credit')
            if not r['online']: require(all(r[k]==0 for k in ('positive_cap_mw','negative_cap_mw','primary_cap_mw','accident_cap_mw')),'offline reserve credit')
            curve=r['segments'];require(curve and all(numeric(s['width_mw'])>0 for s in curve),'positive segment widths')
            prices=[numeric(s['price_yuan_mwh']) for s in curve];require(prices==sorted(prices),'convex signed segment prices required')
            if u['kind']=='generator':require(r['min_mw']>=0,'generator signed power')
            else:
                require(r['mode'] in ('charge','discharge','idle'),'fixed SCED mode')
                require((r['mode']=='charge' and r['max_mw']<=0) or (r['mode']=='discharge' and r['min_mw']>=0) or (r['mode']=='idle' and r['min_mw']==r['max_mw']==0),'mode physical bounds')
        if u['kind']=='battery':
            for h in range(24):require(not {'charge','discharge'} <= {r['mode'] for r in u['rows'][h*4:h*4+4]},'hourly opposite storage modes')
            for k in ('eta_charge','eta_discharge'):require(0<numeric(u[k])<=1,'efficiency')
            require(0<=u['energy_min_mwh']<=u['initial_energy_mwh']<=u['energy_max_mwh'],'initial SOC')
            require(numeric(u['cycle_throughput_limit_mwh'])>=0 and u['cycle_scope'] in ('D96','all98'),'explicit cycle scope')
            require(u['terminal_interpretation'] in ('D96_only_research','optimization_end_only_research','both_explicit_research'),'terminal conflict interpretation')
            expected={'D96_only_research':{95},'optimization_end_only_research':{97},'both_explicit_research':{95,97}}[u['terminal_interpretation']]
            require({x['index'] for x in u['terminal_targets']}==expected,'terminal targets must match interpretation')
    for n,line in d['network'].items():
        require(line['kind'] in ('line','section') and set(line['ptdf'])==set(nodes),'network factors')
        require(len(line['lower_mw'])==len(line['upper_mw'])==98,'network series')
        require(numeric(line['penalty_yuan_mwh'])>0 and line['penalty_source'],'M1 prime source')
        for lo,hi in zip(line['lower_mw'],line['upper_mw']):require(numeric(lo)<=numeric(hi),'network bounds')
    require(d['sced_snapshot'].get('source_kind') in ('synthetic_pure_test_NOT_native','native_highs_sced'),'explicit SCED source kind')
    require(d['source_binding']['sced_snapshot_sha256']==digest(d['sced_snapshot']),'SCED snapshot hash mismatch')
    snapshot=d['sced_snapshot']
    require(snapshot['stage']=='SCED' and snapshot['status']=='optimal','solved SCED snapshot required')
    for k,u in d['resources'].items():
        require(snapshot['power_mw'][k]==[r['sced_mw'] for r in u['rows']],'SCED powers differ from snapshot')
        require(snapshot['online'][k]==[r['online'] for r in u['rows']],'SCUC state differs from snapshot')
        if u['kind']=='battery':require(snapshot['mode'][k]==[r['mode'] for r in u['rows']],'SCED storage mode mismatch')
    canonical(d)
    return d

def build_model(data, probe=None):
    d=validate(data); m=p.ConcreteModel('independent_DA98_LMP'); T=range(98);R=list(d['resources']);N=d['nodes'];L=list(d['network'])
    m.zero=p.Var(bounds=(0,0));m.zero.fix(0)
    m.accident=p.Var(R,T,domain=p.NonNegativeReals);m.power=p.Var(R,T);m.rows=p.ConstraintList();m.primary=p.Var(R,T,domain=p.NonNegativeReals);m.up=p.Var(R,T,domain=p.NonNegativeReals);m.down=p.Var(R,T,domain=p.NonNegativeReals)
    sk=[(r,t,k) for r in R for t in T for k in range(len(d['resources'][r]['rows'][t]['segments']))]
    m.seg=p.Var(sk,domain=p.NonNegativeReals);cost=0
    batteries=[r for r in R if d['resources'][r]['kind']=='battery'];m.energy=p.Var(batteries,T)
    for r,u in d['resources'].items():
        for t,row in enumerate(u['rows']):
            lo,hi=band(row['sced_mw'],row['min_mw'],row['max_mw'],d['delta'],row['price_eligible'],row['online']);m.power[r,t].setlb(lo);m.power[r,t].setub(hi)
            # Signed convex incremental curve starts at min_mw. Constant offer
            # intercept is explicit and retained; no startup/min-output fee.
            m.rows.add(m.power[r,t]==row['min_mw']+sum(m.seg[r,t,k] for k in range(len(row['segments']))))
            cost+=d['grid'][t]['objective_weight_h']*numeric(row['offer_intercept_yuan_h'])
            for k,s in enumerate(row['segments']):
                m.seg[r,t,k].setub(s['width_mw']);cost+=d['grid'][t]['objective_weight_h']*s['price_yuan_mwh']*m.seg[r,t,k]
            g=d['grid'][t];prev=u['initial_power_mw'] if g['predecessor'] is None else m.power[r,g['predecessor']]
            if not d.get('physical_lift'):
                m.rows.add(m.power[r,t]-prev<=row['ramp_up_mw_h']*g['ramp_elapsed_h']);m.rows.add(prev-m.power[r,t]<=row['ramp_down_mw_h']*g['ramp_elapsed_h'])
            for var,cap in ((m.up,'positive_cap_mw'),(m.down,'negative_cap_mw'),(m.primary,'primary_cap_mw'),(m.accident,'accident_cap_mw')):var[r,t].setub(row[cap])
            m.rows.add(m.up[r,t]<=row['max_mw']-m.power[r,t]);m.rows.add(m.down[r,t]<=m.power[r,t]-row['min_mw']);m.rows.add(m.primary[r,t]<=row['max_mw']-m.power[r,t]);m.rows.add(m.accident[r,t]<=row['max_mw']-m.power[r,t])
            if r in batteries and not d.get('physical_lift'):
                m.energy[r,t].setlb(u['energy_min_mwh']);m.energy[r,t].setub(u['energy_max_mwh'])
                ep=u['initial_energy_mwh'] if g['predecessor'] is None else m.energy[r,g['predecessor']]
                coef=u['eta_charge'] if row['mode']=='charge' else 1/u['eta_discharge']
                m.rows.add(m.energy[r,t]==ep-coef*m.power[r,t]*g['physical_duration_h'])
        if r in batteries and not d.get('physical_lift'):
            for target in u['terminal_targets']:m.rows.add(m.energy[r,target['index']]==target['energy_mwh'])
            terms=[]
            for t in (range(96) if u['cycle_scope']=='D96' else T):
                row=u['rows'][t];coef=-u['eta_charge'] if row['mode']=='charge' else 1/u['eta_discharge']
                terms.append(coef*m.power[r,t]*d['grid'][t]['physical_duration_h'])
            m.rows.add(sum(terms)<=u['cycle_throughput_limit_mwh'])
    for group in d['groups']:
        require(set(group['members'])<=set(R),'unknown group member')
        if group.get('power_bounds') is not None:
            require(len(group['power_bounds'])==98,'group power grid')
            for t,b in enumerate(group['power_bounds']):m.rows.add(p.inequality(b[0],m.zero+sum(m.power[r,t] for r in group['members']),b[1]))
        e=sum(.25*m.power[r,t] for r in group['members'] for t in range(96));m.rows.add(p.inequality(group['daily_min_mwh'],m.zero+e,group['daily_max_mwh']))
    for t,v in enumerate(d['reserve']):
        m.rows.add(m.zero+sum(m.up[r,t] for r in R)>=v['positive_mw']);m.rows.add(m.zero+sum(m.down[r,t] for r in R)>=v['negative_mw']);m.rows.add(m.zero+sum(m.primary[r,t] for r in R)>=v['primary_mw']);m.rows.add(m.zero+sum(m.accident[r,t] for r in R)>=v['accident_mw'])
    load=deepcopy(d['load_mw'])
    if probe:load[probe['node']][probe['index']]+=probe['increment_mw']
    m.injection=p.Expression(N,T,rule=lambda _,n,t:m.zero+sum(m.power[r,t] for r in R if d['resources'][r]['node']==n)+d['fixed_injection_mw'][n][t]-load[n][t])
    m.balance=p.Constraint(T,rule=lambda _,t:sum(m.injection[n,t] for n in N)==0)
    m.slack_up=p.Var(L,T,domain=p.NonNegativeReals);m.slack_down=p.Var(L,T,domain=p.NonNegativeReals)
    m.flow=p.Expression(L,T,rule=lambda _,l,t:sum(d['network'][l]['ptdf'][n]*m.injection[n,t] for n in N))
    m.line_upper=p.Constraint(L,T,rule=lambda _,l,t:m.flow[l,t]-m.slack_up[l,t]<=d['network'][l]['upper_mw'][t])
    m.line_lower=p.Constraint(L,T,rule=lambda _,l,t:-m.flow[l,t]-m.slack_down[l,t]<=-d['network'][l]['lower_mw'][t])
    cost+=sum(d['grid'][t]['objective_weight_h']*d['network'][l]['penalty_yuan_mwh']*(m.slack_up[l,t]+m.slack_down[l,t]) for l in L for t in T)
    if d.get('physical_lift'):
        from .gd_r14_da_sced import attach_physical_lift
        attach_physical_lift(m,d,probe=probe)
    m.objective=p.Objective(expr=cost);m.dual=p.Suffix(direction=p.Suffix.IMPORT)
    audit=dict(schema=SCHEMA,input_sha256=digest(d),source_binding=d['source_binding'],independent_variables=True,sced_duals_used=False,official_equivalence=False,dispatch_executable=False,price_unit='yuan/MWh',reserve_requirements_held_fixed_in_price_probe=True,retained=['balance','positive_reserve','negative_reserve','primary_reserve','effective_physical_bounds','SCED_delta_band','ramp_elapsed','PTDF_lines_sections','group_power','D96_group_energy','fixed_mode_SOC','storage_capacity','storage_terminal_interpretation','cycle_throughput','convex_offer_segments'],excluded=['SCUC_integer_optimization','startup_cost','minimum_output_cost','interprovincial_market_variables'],unsupported_declared=d['profile']['excluded_resource_types'],physical_lift=bool(d.get('physical_lift')),physical_network_hard_research_restriction=bool(d.get('physical_lift')),source_kind=d['sced_snapshot']['source_kind'],probe=probe)
    return m,d,audit

def serialize(model): return hs.serialize_model(model,hs.validate_options(OPTIONS))

def audit_kkt(payload, raw):
    """Independent LP reduced-cost/complementarity and strong-duality check."""
    require(payload['objective']['minimize'] is True and all(v['kind']=='C' for v in payload['variables']),'KKT requires continuous minimization LP')
    require(len(raw['primal'])==len(payload['variables']) and len(raw['dual'])==len(payload['rows']),'KKT complete primal/dual vectors required')
    require(all(math.isfinite(numeric(z)) for z in raw['primal']+raw['dual']+[raw['objective']]),'KKT finite witness required')
    x=raw['primal'];y=raw['dual'];rc=[0.]*len(x);dual_objective=payload['objective']['constant'];residual=0.
    for i,c in payload['objective']['terms']:rc[i]+=c
    for row,z in zip(payload['rows'],y):
        ax=sum(c*x[i] for i,c in row['terms'])
        for i,c in row['terms']:rc[i]-=z*c
        if abs(z)>1e-9:
            bound=row['lb'] if z>0 else row['ub'];require(bound is not None,'row dual sign on absent bound')
            dual_objective+=z*bound;residual=max(residual,abs(z*(ax-bound)))
    for i,z in enumerate(rc):
        if abs(z)>1e-8:
            bound=payload['variables'][i]['lb'] if z>0 else payload['variables'][i]['ub'];require(bound is not None,'reduced cost sign on absent bound')
            dual_objective+=z*bound;residual=max(residual,abs(z*(x[i]-bound)))
    gap=abs(raw['objective']-dual_objective);tol=1e-5+1e-7*max(1.,abs(raw['objective']))
    require(gap<=tol and residual<=tol,'LP KKT/strong duality check failed')
    return dict(primal_objective=raw['objective'],dual_objective=dual_objective,strong_duality_gap=gap,max_complementarity_residual=residual,tolerance=tol)

def audit_probe_envelope(base, left, right, node, index, step_mw, weight_h, tolerance=1e-4):
    require(step_mw>0 and weight_h>0,'positive probe scales')
    require(base['input_sha256']==left['input_sha256']==right['input_sha256'],'probe must preserve identical SCED and parameters')
    require(base['probe'] is None and left['probe']==dict(node=node,index=index,increment_mw=-step_mw) and right['probe']==dict(node=node,index=index,increment_mw=step_mw),'probe perturbation path mismatch')
    lower=(base['objective_yuan']-left['objective_yuan'])/(step_mw*weight_h)
    upper=(right['objective_yuan']-base['objective_yuan'])/(step_mw*weight_h)
    price=base['lmp98_yuan_mwh'][node][index]
    require(lower-tolerance<=price<=upper+tolerance,'LMP outside convex one-sided derivative envelope')
    return dict(left_slope_yuan_mwh=lower,right_slope_yuan_mwh=upper,lmp_yuan_mwh=price,within_envelope=True,step_mw=step_mw)

def solve_native(data, output_directory, probe=None):
    """Explicit invocation only. Preserve exact worker request/response and names."""
    out=Path(output_directory);out.mkdir(parents=True,exist_ok=True)
    require(not (out/'request.json').exists(),'refuse overwrite of native evidence')
    m,d,audit=build_model(data,probe);payload,vs,cs=serialize(m)
    require(d['sced_snapshot']['source_kind']=='native_highs_sced','native pricing requires native SCED evidence; synthetic fixtures build only')
    artifacts=d['source_binding'].get('artifacts',[])
    require(artifacts and {'sced_request','sced_response','sced_names','sced_input'} <= {a['role'] for a in artifacts},'SCED request/response binding required')
    for a in artifacts:
        require(sha256(Path(a['path']).read_bytes()).hexdigest()==a['sha256'],'SCED artifact hash mismatch: '+a['role'])
    source_response=json.loads(Path(next(a['path'] for a in artifacts if a['role']=='sced_response')).read_text())
    require(source_response.get('status')=='optimal' and source_response.get('backend')=='highs','SCED source is not native optimal HiGHS')
    source_files={a['role']:Path(a['path']) for a in artifacts}
    source_request=json.loads(source_files['sced_request'].read_text());source_names=json.loads(source_files['sced_names'].read_text());source_input=json.loads(source_files['sced_input'].read_text())
    if source_input.get('control_case')=='two_node_M1_prime':
        from .gd_r14_da_lmp_control import build_sced as rebuild_source
    else:
        from .gd_r14_da_sced import build_model as rebuild_source
    rebuilt,_,_=rebuild_source(source_input)
    rebuilt_payload,rebuilt_vs,rebuilt_cs=hs.serialize_model(rebuilt,source_request['options'])
    for key in ('variables','rows','objective'):
        require(rebuilt_payload[key]==source_request[key],'SCED input/request matrix mismatch: '+key)
    require(source_names==dict(variables=[v.name for v in rebuilt_vs],constraints=[c.name for c in rebuilt_cs]),'SCED input/names mismatch')
    require(source_response.get('dual_valid') is True,'SCED LP optimal dual witness required')
    source_kkt=audit_kkt(source_request,source_response)
    for key in ('grid','nodes','load_mw','fixed_injection_mw','network','groups','reserve'):
        require(source_input[key]==d[key],'SCED/LMP fixed input changed: '+key)
    require(set(source_input['resources'])==set(d['resources']),'SCED resource inventory changed')
    for r,u in d['resources'].items():
        original=deepcopy(source_input['resources'][r]);current=deepcopy(u)
        for row in original['rows']:row.pop('sced_mw',None)
        for row in current['rows']:row.pop('sced_mw',None)
        require(original==current,'SCED resource physical/offer/qualification input changed: '+r)
    source_values=hs.validate_solution(source_request,source_response)
    require(len(source_names['variables'])==len(source_values),'SCED names/primal mismatch')
    source_power=dict(zip(source_names['variables'],source_values))
    for r,u in d['resources'].items():
        for t,row in enumerate(u['rows']):
            name=f'power[{r},{t}]'
            require(name in source_power and abs(source_power[name]-row['sced_mw'])<=1e-7,'SCED snapshot does not match original primal column '+name)
            original=source_input['resources'][r]['rows'][t]
            require(original['online']==row['online'],'SCUC state differs from original SCED input')
            if u['kind']=='battery':require(original['mode']==row['mode'],'SCED mode differs from original input')
    require(source_input.get('physical_lift')==d.get('physical_lift'),'physical source trajectory differs from SCED input')

    payload['deadline']=time.perf_counter()+OPTIONS['time_limit'];payload['preparation_elapsed_s']=0.
    req=out/'request.json';resp=out/'response.json';req.write_bytes(canonical(payload));(out/'input.json').write_bytes(canonical(d))
    (out/'names.json').write_bytes(canonical(dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])))
    result=hs.run_child([sys.executable,str(Path(hs.__file__).with_name('highs_worker.py')),str(req),str(resp)],env=hs.child_environment(),timeout=OPTIONS['time_limit'],tee=False)
    require(result.returncode==0 and resp.exists(),'native worker failure; raw evidence retained')
    raw=json.loads(resp.read_text());require(raw.get('status')=='optimal' and raw.get('dual_valid') is True and raw.get('backend')=='highs','optimal native LP duals required')
    values=hs.validate_solution(payload,raw);require(len(raw['dual'])==len(cs) and all(math.isfinite(numeric(x)) for x in raw['dual']),'complete finite dual vector required')
    require(raw['native_settings']['threads']==1 and raw['native_settings']['mip_rel_gap']==.001,'native settings mismatch')
    for v,x in zip(vs,values):v.set_value(x)
    dual=dict(zip([c.name for c in cs],raw['dual']))
    prices={n:[] for n in d['nodes']}
    for n in d['nodes']:
        for t in range(98):
            # HiGHS <= rows have nonpositive duals. d flow/d nodal load=-PTDF.
            val=dual[m.balance[t].name]+sum((dual[m.line_upper[l,t].name]-dual[m.line_lower[l,t].name])*d['network'][l]['ptdf'][n] for l in d['network'])
            if d.get('physical_lift'):
                for j in d['physical_lift']['market_to_physical'][t]:
                    val+=dual[m.physical_balance[j].name]+sum((dual[m.physical_line_upper[l,j].name]-dual[m.physical_line_lower[l,j].name])*d['network'][l]['ptdf'][n] for l in d['network'])
            prices[n].append(val/d['grid'][t]['objective_weight_h'])
    audit.update(source_sced_kkt=source_kkt,kkt=audit_kkt(payload,raw),objective_yuan=raw['objective'],status=raw['status'],raw_request_sha256=sha256(req.read_bytes()).hexdigest(),raw_response_sha256=sha256(resp.read_bytes()).hexdigest(),lmp98_yuan_mwh=prices,lmp96_yuan_mwh={n:v[:96] for n,v in prices.items()},hourly_arithmetic_mean_yuan_mwh={n:[sum(v[4*h:4*h+4])/4 for h in range(24)] for n,v in prices.items()},pricing_power_mw={r:[p.value(m.power[r,t]) for t in range(98)] for r in d['resources']},source_unchanged=digest(data)==digest(d))
    (out/'result.json').write_bytes(canonical(audit));return audit
