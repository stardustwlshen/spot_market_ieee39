"""New data-only R42 fixed-SCED -> DA98 pricing/publication bridge. No solver.
No R14 native identity or R15 live capability is reused. Formula validators only.
"""
from copy import deepcopy
from datetime import datetime,timedelta
from dataclasses import asdict,is_dataclass
from hashlib import sha256
import json,math
from smm.gd_r14_da_contract import contract_from_lift
from smm.gd_r14_da_lmp import validate as validate_lmp
SCHEMA='gd.research.r53.new98_da_bridge.v1'
SERVICES=('positive','negative','primary','accident')
def require(ok,msg):
    if not ok:raise ValueError(msg)
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x):return sha256(canonical(x)).hexdigest()
def case_digest(case):
    from smm.gd_october_job_codec import dumps
    x=deepcopy(case)
    if is_dataclass(x['energy_inputs']['horizon']):x['energy_inputs']['horizon']=asdict(x['energy_inputs']['horizon'])
    return sha256(dumps(x)).hexdigest()
def stamp(s):
    x=datetime.fromisoformat(s);require(x.utcoffset()==timedelta(hours=8),'Shanghai +08 timestamp required');return x
def close(a,b,msg):require(math.isfinite(a)and math.isfinite(b)and abs(a-b)<=1e-6,msg)
def bit(values,name):
    require(name in values,'fixed discrete witness missing: '+name);v=values[name];require(type(v)in(int,float)and abs(v-round(v))<=1e-6 and round(v)in(0,1),'nonbinary fixed witness');return round(v)
def mean(xs):return math.fsum(xs)/len(xs)

def normalize_reference(value,lo,hi):
    require(all(math.isfinite(z)for z in(value,lo,hi))and lo<=hi,'finite ordered bounds')
    adjusted=min(max(value,lo),hi);require(abs(adjusted-value)<=1e-9,'PRICE reference outside hard bound beyond roundoff budget')
    return adjusted,adjusted-value

def signed_offer(parts,lo,hi):
    # parts contain signed-power [left,right,slope]; C(0)=0. No startup/no-load fees.
    def cost(p):
        if p>=0:return math.fsum(max(0,min(p,b)-max(0,a))*c for a,b,c in parts)
        return -math.fsum(max(0,min(0,b)-max(p,a))*c for a,b,c in parts)
    seg=[dict(width_mw=min(hi,b)-max(lo,a),price_yuan_mwh=c)for a,b,c in parts if min(hi,b)>max(lo,a)]
    if hi==lo:seg=[dict(width_mw=1.,price_yuan_mwh=0.)]
    else:close(sum(z['width_mw']for z in seg),hi-lo,'offer does not cover adjusted range')
    return cost(lo),seg

def validate_receipt(case,report,values,receipt):
    require(receipt['schema']==SCHEMA+'.source_receipt','new R53 receipt required; no old receipt alias')
    require(receipt['stage']=='fixed_SCED'and receipt['status']=='Optimal','successful new fixed SCED required')
    require(receipt['run_id']and receipt['scuc_run_id']and receipt['run_id']!=receipt['scuc_run_id'],'new distinct SCUC/SCED identities')
    require(receipt['case_sha256']==case_digest(case)and receipt['physical_report_sha256']==digest(report)and receipt['expanded_values_sha256']==digest(values),'new source binding mismatch')
    require(type(receipt['SCUC_accepted'])is bool and type(receipt['diagnostic_only'])is bool and type(receipt['publication_allowed'])is bool,'explicit SCUC/diagnostic/publication gates')
    if not receipt['SCUC_accepted']:require(receipt['diagnostic_only']and not receipt['publication_allowed'],'unaccepted SCUC cannot be promoted')
    if receipt['diagnostic_only']:require(not receipt['publication_allowed'],'diagnostic cannot publish')
    require(receipt['source_kind']in('synthetic_pure_test_NOT_native','native_highs_sced'),'source classification required')
    if receipt['source_kind']=='native_highs_sced':
        require(receipt['new_fixed_sced_primal_KKT_verified']is True and receipt['SCUC_fixed_discrete_match_verified']is True,'new SCED certificate/fixed state verification required')
        for k in ('SCUC_request_sha256','SCUC_response_sha256','SCED_request_sha256','SCED_response_sha256','expanded_witness_source_sha256'):
            require(type(receipt.get(k))is str and len(receipt[k])==64 and all(c in '0123456789abcdef'for c in receipt[k]),'new raw artifact digest required: '+k)
    return True

def build_price_dto(case,report,expanded_values,receipt,spec,*,formal_strict=False):
    require(type(formal_strict)is bool and not formal_strict,'formal blocked: declared-target and representative mapping/economic equivalence missing')
    require(case['contract']['economic_policy']=='market98_hidden_physical_research','explicit new98 objective policy required')
    validate_receipt(case,report,expanded_values,receipt)
    require(spec['renewable_price_policy']=='fixed_verified_SCED_dispatch_nonpricing_research' and spec['nonpricing_assumption_source'], 'explicit nonpricing research choice, never inferred from noquote')
    require(receipt['scenario_model_sha256'] and receipt['actual_AGC_selection_sha256'], 'new scenario and service evidence required')
    cells=report['physical_cells'];require(len(cells)==576 and [z['index']for z in cells]==list(range(576)),'complete new 576-cell trajectory required')
    e=case['energy_inputs'];require(not e.get('groups'),'group mapping unsupported: cannot silently omit');G=e['thermal'];B=case.get('batteries',{});R=set(G)|set(B);day=stamp(case['contract']['day_start']);cutoff=stamp(case['contract']['cutoff']);pub=stamp(case['contract']['publication_at'])
    require(all(set(c['thermal'])==set(G)and set(c['battery'])==set(B)for c in cells),'exact physical resource inventory required')
    require(stamp(receipt['logical_available_at'])<=pub<day,'new SCED logical availability/publication chronology')
    require(spec['mode']=='research'and spec['source']and stamp(spec['available_at'])<=cutoff,'pricing assumptions must be explicit and cutoff available')
    require(0<=spec['delta']<=1 and spec['delta_source'],'explicit delta research parameter')
    reps=spec['representatives'];require(len(reps)==2 and {z['kind']for z in reps}=={'peak','valley'},'explicit peak/valley declarations required')
    require([z['physical_index']for z in reps]==[z['physical_index']for z in case['contract']['representatives']],'representative mapping cannot silently change R42')
    mapping=[list(range(3*q,3*q+3))for q in range(96)];grid=[]
    for q in range(98):
        if q<96:i=3*q;kind='delivery';weight=.25
        else:
            z=reps[q-96];i=z['physical_index'];kind=z['kind'];weight=z['objective_weight_h']
            require(type(i)is int and i%3==0 and 288<=i<=573 and z['mapping_source']and weight>0,'explicit representative weight/time required');mapping.append([i,i+1,i+2])
        a=day+timedelta(minutes=5*i);b=a+timedelta(minutes=15)
        prev=day if not q else stamp(grid[q-1]['end'])
        require(b>prev,'representative chronology must not reset')
        grid.append(dict(start=a.isoformat(),end=b.isoformat(),actual_time=b.isoformat(),power_semantics='interval_mean',kind=kind,objective_weight_h=weight,physical_duration_h=.25,ramp_elapsed_h=.25 if q<96 else(b-prev).total_seconds()/3600,predecessor=None if not q else q-1,settlement=q<96,constraint_scope=spec['source']))
    require(len({j for ids in mapping for j in ids})==294,'overlapping 98 projection')
    x=dict(cells=[dict(start=(day+timedelta(minutes=5*j)).isoformat(),end=(day+timedelta(minutes=5*(j+1))).isoformat())for j in range(576)],market_to_physical=mapping,availability_is_simulated=True,future_RT_used=False,state_source='new R50v2 fixed SCED '+receipt['run_id'],extension_policy='continuous576 physical state retained; DA98 independent pricing objective is a separate research policy',resources={},load_mw={},fixed_injection_mw={},network={},reserve=[])
    resources={};powers={};modes={};events=report['events'];reference_roundoff=[]
    for g,u in G.items():
        rows=[];powers[g]=[];previous=u['initial_power_mw']
        for j,cell in enumerate(cells):
            z=cell['thermal'][g];phase=z['phase'];stable=bit(expanded_values,f'stable[{g},{j}]');off=bit(expanded_values,f'off[{g},{j}]')
            require(z['stable']==bool(stable)and(phase=='stable')==bool(stable)and(phase=='off')==bool(off),'thermal phase/discrete mismatch')
            p=z['mean_mw'];close(p,expanded_values[f'power[{g},{j}]'],'thermal report/primal mismatch');powers[g].append(p)
            up=z['agc_up_reserved_mw'];dn=z['agc_down_reserved_mw']
            require(up==dn==(10. if j<288 else 0.),'R53 verified synthetic AGC reservation, no case-default zero')
            lo=u['min_mw'][j]+dn if stable else p;hi=u['max_mw'][j]-up if stable else p
            require(lo-1e-6<=p<=hi+1e-6,'SCED outside original bounds')
            row=dict(agc_up_reserved_mw=up,agc_down_reserved_mw=dn,phase=phase,min_mw=lo,max_mw=hi,ramp_up_mw_h=u['ramp_up_mw_per_h'][j],ramp_down_mw_h=u['ramp_down_mw_per_h'][j],reserve_eligible=bool(stable),positive_cap_mw=hi-lo if stable else 0.,negative_cap_mw=hi-lo if stable else 0.,primary_cap_mw=u['max_mw'][j]*case['reserve']['primary_fraction'][g]if stable else 0.,accident_cap_mw=min(hi-lo,u['ramp_up_mw_per_h'][j]/6)if stable and case['reserve']['contingency_eligible'][g]else 0.)
            if phase in ('startup','shutdown'):
                ev=[a for a in events if a['unit']==g and a['kind']==phase and a['index']<=j<a['index']+a['curve_cells']];require(len(ev)==1,'unique new event required');ev=ev[0];k=j-ev['index'];curve=case['curves'][g][phase][k]
                require(z['curve_step']==k,'curve cursor mismatch');close(previous,curve['begin'],'curve begin discontinuity');close(p,curve['mean'],'transition mean mismatch');close(z['end_mw'],curve['end'],'transition end mismatch')
                row.update(begin_mw=curve['begin'],mean_mw=p,end_mw=curve['end'],event_id=receipt['scuc_run_id']+':'+g+':'+str(ev['index']),curve_source=case['curves'][g]['source'])
            else:close(z['end_mw'],p,'stable/off endpoint mismatch')
            previous=z['end_mw'];rows.append(row)
        x['resources'][g]=dict(rows=rows);resources[g]=dict(kind='generator',node=u['bus'],initial_power_mw=u['initial_power_mw'],rows=[])
    for b,u in B.items():
        eta=math.sqrt(u['roundtrip_efficiency']);rows=[];powers[b]=[];modes[b]=[];energy=u['initial_energy_mwh'];throughput={}
        for j,cell in enumerate(cells):
            cb=bit(expanded_values,f'battery.charge_mode[{b},{j}]');db=bit(expanded_values,f'battery.discharge_mode[{b},{j}]');require(cb+db<=1,'opposite permission conflict');ib=1-cb-db
            if f'battery.idle_mode[{b},{j}]'in expanded_values:require(bit(expanded_values,f'battery.idle_mode[{b},{j}]')==ib,'idle expression identity mismatch')
            active=[k for k,v in zip(('charge','discharge','idle'),(cb,db,ib))if v];require(len(active)==1,'one-hot storage mode required even at zero power');mode=active[0];modes[b].append(mode)
            z=cell['battery'][b];charge=z['charge_mw'];discharge=z['discharge_mw'];require(charge>=-1e-8 and discharge>=-1e-8,'negative charge/discharge')
            close(charge,expanded_values[f'battery.charge[{b},{j}]'],'charge report mismatch');close(discharge,expanded_values[f'battery.discharge[{b},{j}]'],'discharge report mismatch')
            require((mode=='charge'or abs(charge)<=1e-8)and(mode=='discharge'or abs(discharge)<=1e-8),'mode/power conflict');p=discharge-charge;powers[b].append(p)
            date=(day+timedelta(minutes=5*j)).date().isoformat();throughput[date]=throughput.get(date,0.)+(charge*eta+discharge/eta)/12
            energy+=charge*eta/12-discharge/eta/12;close(energy,z['energy_mwh'],'continuous SOC not satisfied');close(energy,expanded_values[f'battery.energy[{b},{j}]'],'SOC report/primal mismatch')
            require(u['energy_min_mwh']-1e-6<=energy<=u['energy_max_mwh']+1e-6,'SOC hard boundary')
            lo=-u['charge_max_mw']if mode=='charge'else 0.;hi=u['discharge_max_mw']if mode=='discharge'else 0.
            require(lo-1e-6<=p<=hi+1e-6,'storage power hard boundary')
            rows.append(dict(mode=mode,mode_semantics='fixed LP permission, not executed direction',actual_direction='idle'if abs(p)<=1e-8 else('discharge'if p>0 else'charge'),min_mw=lo,max_mw=hi,ramp_up_mw_h=u['ramp_up_mw_per_h'],ramp_down_mw_h=u['ramp_down_mw_per_h'],reserve_eligible=False,**{s+'_cap_mw':0. for s in SERVICES}))
        require(stamp(u['terminal_at'])==day+timedelta(days=1),'D96 target cannot move to representative/end576');close(cells[287]['battery'][b]['energy_mwh'],u['terminal_energy_mwh'],'new SCED D96 target violation')
        budgets={d:dict(limit_internal_mwh=2*u['energy_capacity_mwh']*z['limit_cycles'],used_internal_mwh=z['used_internal_mwh'])for d,z in u['cycle_budget'].items()}
        require(set(throughput)<=set(budgets),'dated cycle budget missing')
        require(all(v+budgets[d]['used_internal_mwh']<=budgets[d]['limit_internal_mwh']+1e-6 for d,v in throughput.items()),'dated cycle budget exceeded')
        x['resources'][b]=dict(rows=rows,cycle_budget=budgets)
        resources[b]=dict(kind='battery',node=u['bus'],initial_power_mw=u['initial_power_mw'],initial_energy_mwh=u['initial_energy_mwh'],eta_charge=eta,eta_discharge=eta,energy_min_mwh=u['energy_min_mwh'],energy_max_mwh=u['energy_max_mwh'],cycle_scope='D96',cycle_throughput_limit_mwh=budgets[day.date().isoformat()]['limit_internal_mwh'],terminal_interpretation='D96_only_research',terminal_targets=[dict(index=95,energy_mwh=u['terminal_energy_mwh'])],rows=[])
    # Every market reference is a projection of NEW fixed-SCED powers, not old DA.
    for r,u in resources.items():
        for q,ids in enumerate(mapping):
            raw=[x['resources'][r]['rows'][j]for j in ids];p=mean([powers[r][j]for j in ids]);lo=mean([z['min_mw']for z in raw]);hi=mean([z['max_mw']for z in raw]);j=ids[0]
            if not lo<=p<=hi:
                adjusted,delta_ref=normalize_reference(p,lo,hi)
                reference_roundoff.append(dict(resource=r,market_index=q,raw_SCED_mean_mw=p,PRICE_reference_mw=adjusted,adjustment_mw=adjusted-p,tolerance_mw=1e-9,physical_lower_mw=lo,physical_upper_mw=hi,raw_physical_and_primal_unchanged=True));p=adjusted
            if r in G:
                require(len({z['phase']for z in raw})==1,'mixed thermal quarter needs an explicit price eligibility mapping');online=raw[0]['phase']!='off';eligible=raw[0]['phase']=='stable'and spec['thermal_price_eligible'][r]
                t=G[r];require(len({t['base_bid_yuan_per_h'][k]for k in ids})==1 and all(len({s['price_yuan_per_mwh'][k]for k in ids})==1 for s in t['segments']),'varying within-quarter bid requires explicit mapping');parts=[(0.,t['bid_base_mw'],t['base_bid_yuan_per_h'][j]/t['bid_base_mw'])];a=t['bid_base_mw']
                for s in t['segments']:parts.append((a,a+s['width_mw'],s['price_yuan_per_mwh'][j]));a+=s['width_mw']
                mode=None
            else:
                active={z['mode']for z in raw}-{'idle'};require(len(active)<=1,'opposite storage directions in projected quarter');mode=next(iter(active),'idle');online=True;eligible=mode!='idle'
                t=B[r];curve=t['charge_curve'if mode=='charge'else'discharge_curve'];bp=curve['breakpoints_mw'];pp=curve['prices_yuan_per_mwh']
                parts=[(-bp[k+1],-bp[k],pp[k])if mode=='charge'else(bp[k],bp[k+1],pp[k])for k in range(len(pp))];parts.sort()
            intercept,segs=signed_offer(parts,lo,hi)
            row=dict(sced_mw=p,min_mw=lo,max_mw=hi,price_eligible=bool(eligible),online=online,eligibility_reason=spec['eligibility_source'],ramp_up_mw_h=min(z['ramp_up_mw_h']for z in raw),ramp_down_mw_h=min(z['ramp_down_mw_h']for z in raw),reserve_eligible=all(z['reserve_eligible']for z in raw),offer_intercept_yuan_h=intercept,segments=segs,**{s+'_cap_mw':mean([z[s+'_cap_mw']for z in raw])for s in SERVICES})
            if mode is not None:row['mode']=mode
            u['rows'].append(row)
    nodes=list(e['buses']);require(set(spec['network'])==set(e['lines'])|set(e.get('sections',{})),'exact network inventory')
    network={}
    for n in nodes:
        x['load_mw'][n]=[e['load_mw'][n,j]for j in range(576)]
        x.setdefault('renewable_availability_mw',{})[n]=[];x.setdefault('renewable_dispatched_mw',{})[n]=[];x.setdefault('renewable_curtailed_mw',{})[n]=[];x['fixed_injection_mw'][n]=[]
        for j in range(576):
            vre=cells[j]['renewable'][n];A=vre['availability_mw'];P=vre['dispatched_mw'];C=vre['curtailed_mw']
            close(A,e['renewable_mw'][n,j],'original availability changed');close(P+C,A,'new VRE conservation');close(P,expanded_values[f'renewable_power[{n},{j}]'],'new VRE dispatch witness');close(C,expanded_values[f'renewable_curtailment[{n},{j}]'],'new VRE curtailment witness')
            require(P>=-1e-6 and C>=-1e-6 and P<=A+1e-6 and C<=A+1e-6,'new VRE hard bounds');require(vre['reserve_credit_mw']==vre['AGC_credit_mw']==0,'no renewable service credit')
            x['renewable_availability_mw'][n].append(A);x['renewable_dispatched_mw'][n].append(P);x['renewable_curtailed_mw'][n].append(C);x['fixed_injection_mw'][n].append(P+e['physical_tie_mw'][n,j])
    for l,z in spec['network'].items():
        require(set(z['ptdf'])==set(nodes)and z['penalty_source']and z['penalty_yuan_mwh']>0,'explicit PTDF/penalty basis required');orig=e['lines'].get(l,e.get('sections',{}).get(l));x['network'][l]={k:[orig[k][j]for j in range(576)]for k in ('lower_mw','upper_mw')}
        for ids in mapping:
            for k in ('lower_mw','upper_mw'):require(len({x['network'][l][k][j]for j in ids})==1,'varying within-quarter line limit needs explicit mapping')
        network[l]=dict(kind=z['kind'],ptdf=deepcopy(z['ptdf']),penalty_yuan_mwh=z['penalty_yuan_mwh'],penalty_source=z['penalty_source'],**{k:[x['network'][l][k][ids[0]]for ids in mapping]for k in ('lower_mw','upper_mw')})
    for j in range(576):x['reserve'].append({s+'_mw':case['reserve']['contingency_mw'if s=='accident'else s+'_mw'][j]for s in SERVICES})
    snap=dict(source_kind=receipt['source_kind'],stage='SCED',status='optimal',power_mw={r:[row['sced_mw']for row in u['rows']]for r,u in resources.items()},online={r:[row['online']for row in u['rows']]for r,u in resources.items()},mode={r:[row['mode']for row in resources[r]['rows']]for r in B},R44_run_id=receipt['run_id'])
    d=dict(schema='gd.r14.da_lmp.v1',delta=spec['delta'],grid=grid,nodes=nodes,resources=resources,network=network,groups=[],reserve=[{s+'_mw':mean([x['reserve'][j][s+'_mw']for j in ids])for s in SERVICES}for ids in mapping],physical_lift=x,sced_snapshot=snap,source_binding=dict(sced_snapshot_sha256=digest(snap),R53_receipt=deepcopy(receipt),R53_spec_sha256=digest(spec)),profile=dict(mode='research',research_profile=SCHEMA,rule_source=spec['rule_source'],delta_source=spec['delta_source'],eligibility_source=spec['eligibility_source'],initial_state_source='new R50v2 case initial state, never old R14 permit',sced_source=receipt['run_id'],parameter_source=spec['source'],storage_interpretation='signed_magnitude_fixed_sced_mode_research',excluded_resource_types=['interregional market','unqualified incremental storage reserves','formal equivalence'],market98_with_hidden_physical_constraints_NOT_official_equivalence=True,renewable_price_policy=spec['renewable_price_policy'],nonpricing_assumption_source=spec['nonpricing_assumption_source'],quantity_only_does_NOT_imply_price_ineligible=True),fixed_state_available_at=receipt['logical_available_at'],planned_publication_at=case['contract']['publication_at'])
    for fam in ('load_mw','fixed_injection_mw','renewable_availability_mw','renewable_dispatched_mw','renewable_curtailed_mw'):d[fam]={n:[mean([x[fam][n][j]for j in ids])for ids in mapping]for n in nodes}
    contract=contract_from_lift(d,scenario_id=receipt['run_id'],information_cutoff=case['contract']['cutoff'],generated_at=receipt['generated_at'],research_assumptions={'economic_mapping':'new continuous48h SCED -> explicit independent DA98 pricing policy; NOT official equivalence','representatives':spec['source'],'storage_terminal':spec['terminal_source']},storage_terminal_state_id='D96',storage_terminal_rule_version='R44_explicit_D96_research',storage_terminal_source=spec['terminal_source'])
    d['contract']=contract.payload();d['contract_sha256']=contract.sha256;d['PRICE_reference_roundoff']=reference_roundoff
    validate_lmp(d)
    return dict(schema=SCHEMA,DTO=d,DTO_sha256=digest(d),source_receipt=deepcopy(receipt),new_native_calls=0,official_equivalence=False,execution_authority=False,legacy_R14_authority_reused=False,diagnostic_only=receipt['diagnostic_only'],publication_allowed=receipt['publication_allowed'],SCUC_accepted=receipt['SCUC_accepted'],terminal_obligations=deepcopy(report['terminal_obligations']))


def publish_candidate(bridge,*,price_result=None,available_at):
    require(bridge['schema']==SCHEMA and bridge['DTO_sha256']==digest(bridge['DTO']),'bridge mutated')
    require(bridge['publication_allowed']and bridge['SCUC_accepted']and not bridge['diagnostic_only'],'publication blocked for unaccepted SCUC diagnostic')
    d=bridge['DTO'];pub=stamp(available_at);require(pub==stamp(d['planned_publication_at'])and stamp(d['fixed_state_available_at'])<=pub<stamp(d['grid'][0]['start']),'logical publication must follow SCED and precede delivery')
    rows=[dict(index=q,start=g['start'],end=g['end'],quantity_mwh={r:u['rows'][q]['sced_mw']*.25 for r,u in d['resources'].items()},prices=None)for q,g in enumerate(d['grid'][:96])]
    if price_result is not None:
        require(price_result['DTO_sha256']==bridge['DTO_sha256']and price_result['run_id']!=bridge['source_receipt']['run_id']and price_result['status']=='Optimal'and price_result['independent_price_KKT_verified']is True,'new independent pricing identity/certificate required')
        require(stamp(price_result['logical_available_at'])<=pub,'future prices cannot publish earlier')
        require(set(price_result['prices'])==set(d['nodes'])and all(len(v)==98 and all(math.isfinite(z)for z in v)for v in price_result['prices'].values()),'complete independent98 price inventory')
        for q,row in enumerate(rows):row['prices']={n:v[q]for n,v in price_result['prices'].items()}
    return dict(schema=SCHEMA+'.publication_candidate',available_at=available_at,rows=rows,representatives_publishable=False,price_status='pending_independent_PRICE'if price_result is None else'research_verified_input',official=False,execution_authority=False,causal_clock='explicit simulated publication, not backdated actual completion',source_DTO_sha256=bridge['DTO_sha256'])
