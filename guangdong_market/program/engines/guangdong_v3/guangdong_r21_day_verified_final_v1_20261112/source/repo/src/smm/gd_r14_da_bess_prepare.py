"""Partial upstream D+1 BESS-mode research MIP, NOT full official SCUC.

D-day modes and all thermal states remain fixed. Full physical-grid energy-offer
objective differs deliberately from subsequent DA98 SCED. No native on import.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json,math,sys,time
import pyomo.environ as p
from . import highs_solver as hs
from .gd_r14_da_sced import canonical,digest,require,write_full_residuals
from .gd_r14_da_contract import validate_lift_contract_binding
from .gd_october_job_codec import loads

SCHEMA='gd.r14.partial_Dplus1_BESS_prepare.v1'
OPTIONS=dict(time_limit=180.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.)
MODES=('charge','discharge','idle')

def policy_record(selection):
    require(selection in ('closed50','original_bounds_only'),'explicit closed50/original_bounds_only terminal policy required')
    return dict(id='v3_prospective_hourly_BESS_MIP'if selection=='closed50'else'v4_prospective_hourly_BESS_MIP_open_tail',terminal_Dplus1='research_Dplus2_midnight_50percent_not_original_mandate'if selection=='closed50'else'original_bounds_only',terminal_soc_fraction=.5 if selection=='closed50'else None,objective='576_physical_cells_original_energy_offers_excludes_fixed_startup_and_noload',cycle='separate_natural_dates_original_one_cycle_each_no_inventory_reset',partial_SCUC_only=True,full_official_SCUC=False)


def is_closed(data):
    policy=data.get('policy');require(isinstance(policy,dict),'explicit terminal policy record required')
    name=policy.get('terminal_Dplus1')
    require(name in ('research_Dplus2_midnight_50percent_not_original_mandate','original_bounds_only'),'missing or unsupported terminal selection')
    closed=name!='original_bounds_only';require(policy==policy_record('closed50'if closed else'original_bounds_only'),'terminal policy fields altered')
    return closed


def prepare(base,technical_source,*,base_source,terminal_policy):
    """Bind original R13 source parameters by the already-frozen artifact hash."""
    base=deepcopy(base);validate_lift_contract_binding(base)
    basepath=Path(base_source).resolve();baseblob=basepath.read_bytes();require(digest(json.loads(baseblob))==digest(base),'frozen base file mismatch')
    require(base['strategy_id']=='v2_forecast_GT_start','v2 thermal strategy required')
    path=Path(technical_source).resolve();blob=path.read_bytes();h=sha256(blob).hexdigest()
    refs=[a for a in base['source_binding']['artifacts']if Path(a['path']).name=='source_reconstructed_day_inputs.json']
    require(len(refs)==1 and refs[0]['sha256']==h,'original technical source artifact identity mismatch')
    source=loads(blob);technical=deepcopy(source['batteries'])
    return validate(dict(schema=SCHEMA,base=base,base_sha256=digest(base),base_source=dict(path=str(basepath),sha256=sha256(baseblob).hexdigest()),technical_source=dict(path=str(path),sha256=h),technical=technical,policy=policy_record(terminal_policy)))

def validate(data):
    d=json.loads(canonical(data));require(d['schema']==SCHEMA,'BESS preparation schema')
    b=d['base'];require(digest(b)==d['base_sha256'],'base changed');validate_lift_contract_binding(b)
    rawbase=Path(d['base_source']['path']).read_bytes();require(sha256(rawbase).hexdigest()==d['base_source']['sha256'] and digest(json.loads(rawbase))==digest(b),'frozen base artifact changed')
    require(b['strategy_id']=='v2_forecast_GT_start' and len(b['physical_lift']['cells'])==576,'fixed v2 full physical domain required')
    source=d['technical_source'];refs=[a for a in b['source_binding']['artifacts']if Path(a['path']).name=='source_reconstructed_day_inputs.json'];require(len(refs)==1 and refs[0]['sha256']==source['sha256'],'technical source no longer bound to original base artifact');blob=Path(source['path']).read_bytes();require(sha256(blob).hexdigest()==source['sha256'],'technical source changed')
    original=loads(blob);require(canonical(json.loads(canonical(original['batteries'])))==canonical(d['technical']),'technical fields differ from original source')
    is_closed(d)
    require(not b['groups'],'upstream nonempty resource-group constraints not implemented; refuse omission')
    B=[r for r,u in b['resources'].items()if u['kind']=='battery'];require(set(B)==set(d['technical']),'battery inventory mismatch')
    for r in B:
        u=d['technical'][r];v=b['resources'][r];x=b['physical_lift']['resources'][r]
        for key in ('charge_min_mw','discharge_min_mw','minimum_charge_mw','minimum_discharge_mw'):
            require(u.get(key,0)==0,'nonzero minimum charging/discharging power unsupported')
        for direction in ('charge','discharge'):
            curve=u[direction+'_curve'];points=curve['breakpoints_mw'];prices=curve['prices_yuan_per_mwh']
            require(len(points)==len(prices)+1 and points[0]==0 and points[-1]==u[direction+'_max_mw'] and all(a<z for a,z in zip(points,points[1:])),'original zero-origin/full-capacity bid endpoints required')
            normalized=[dict(p_start=a,p_end=z,price=q)for a,z,q in zip(points,points[1:],prices)]
            require(v['original_curves'][direction]==normalized,'original BESS quotes changed')
        for h in range(24):require(not {'charge','discharge'}<={z['mode']for z in x['rows'][h*12:h*12+12]},'D-day fixed modes violate hourly direction')
        require(all(z==0 for z in u['minimum_mode_h'].values()) and all(z==0 for z in u['preparation_h'].values()),'nonzero minimum mode/preparation not supported faithfully')
        require(len(u['availability'])==288 and all(z is True for z in u['availability'].values()),'nontrivial availability pattern unsupported; do not extend silently')
        require(u['initial_mode']=='idle' and u['initial_hour_direction']==0,'unsupported initial storage mode/direction')
        for k in ('initial_energy_mwh','initial_power_mw','energy_min_mwh','energy_max_mwh'):require(u[k]==v[k],r+' original '+k+' changed')
        original_unit=next(z for z in original['static']['units']['storage']if z['id']==r)
        require(v['eta_charge']==float(original_unit['eta_c']) and v['eta_discharge']==float(original_unit['eta_d']) and abs(v['eta_charge']*v['eta_discharge']-u['roundtrip_efficiency'])<1e-12,'original directional efficiencies changed')
        require(u['charge_max_mw']==u['discharge_max_mw']==float(original_unit['pmax']),'original storage rated power changed')
        require(v['node']==u['bus'] and v['terminal_targets']==[dict(index=95,energy_mwh=u['terminal_energy_mwh'])],'D96 original target/node changed')
        require(len(x['cycle_budget'])==2 and all(z['limit_internal_mwh']==2*u['energy_capacity_mwh'] and z['used_internal_mwh']==0 for z in x['cycle_budget'].values()),'original per-date cycle ledger required')
        require(all(z['limit_cycles']==1. and z['used_internal_mwh']==0 for z in u['cycle_budget'].values()),'nonzero or nonoriginal inherited cycle budget unsupported')
        for row in x['rows']:
            mode=row['mode'];require(row['min_mw']==(-u['charge_max_mw']if mode=='charge'else 0.) and row['max_mw']==(u['discharge_max_mw']if mode=='discharge'else 0.),'base physical mode/rated bounds changed')
            require(not row['reserve_eligible'] and all(row[k]==0 for k in ('positive_cap_mw','negative_cap_mw','primary_cap_mw','accident_cap_mw')),'battery cannot supply reserves')
            require(row['ramp_up_mw_h']==u['ramp_up_mw_per_h'] and row['ramp_down_mw_h']==u['ramp_down_mw_per_h'],'storage ramp changed')
        require(u['energy_min_mwh']<=.5*u['energy_capacity_mwh']<=u['energy_max_mwh'],'research terminal outside original bounds')
    return d

def build_model(data):
    d=validate(data);b=d['base'];x=b['physical_lift'];R=list(b['resources']);B=list(d['technical']);G=[r for r in R if r not in B];N=b['nodes'];L=list(b['network']);J=range(576)
    m=p.ConcreteModel('partial_upstream_Dplus1_BESS_modes');m.zero=p.Var(bounds=(0,0));m.zero.fix(0);m.power=p.Var(R,J);m.rows=p.ConstraintList();m.energy=p.Var(B,range(577));m.charge=p.Var(B,J,domain=p.NonNegativeReals);m.discharge=p.Var(B,J,domain=p.NonNegativeReals);m.mode=p.Var(B,range(24,48),MODES,domain=p.Binary)
    m.reserve=p.Var(G,J,('positive','negative','primary','accident'),domain=p.NonNegativeReals)
    for r in G:
        u=b['resources'][r];prev=u['initial_power_mw']
        for j,row in enumerate(x['resources'][r]['rows']):
            v=m.power[r,j];v.setlb(row['min_mw']);v.setub(row['max_mw'])
            if row['phase']in('startup','shutdown'):
                require(row['event_id'] and row['curve_source'] and row['min_mw']==row['max_mw']==row['mean_mw'],'unaltered fixed thermal curve required')
                m.rows.add(prev+m.zero==row['begin_mw']);prev=row['end_mw']
            else:m.rows.add(v-prev<=row['ramp_up_mw_h']/12);m.rows.add(prev-v<=row['ramp_down_mw_h']/12);prev=v
            for service in ('positive','negative','primary','accident'):
                cap=row[service+'_cap_mw'];require(cap>=0 and (row['reserve_eligible']or cap==0),'qualified thermal reserve only');m.reserve[r,j,service].setub(cap)
                m.rows.add(m.reserve[r,j,service]<=v-row['min_mw']if service=='negative'else m.reserve[r,j,service]<=row['max_mw']-v)
    for r in B:
        u=d['technical'][r];v=b['resources'][r];m.energy[r,0].fix(u['initial_energy_mwh']);prev=u['initial_power_mw']
        for h in range(24,48):m.rows.add(sum(m.mode[r,h,k]for k in MODES)==1)
        for j in J:
            c=m.charge[r,j];z=m.discharge[r,j];c.setub(u['charge_max_mw']);z.setub(u['discharge_max_mw'])
            if j<288:
                mode=x['resources'][r]['rows'][j]['mode'];c.setub(u['charge_max_mw']if mode=='charge'else 0.);z.setub(u['discharge_max_mw']if mode=='discharge'else 0.)
                m.power[r,j].setlb(x['resources'][r]['rows'][j]['min_mw']);m.power[r,j].setub(x['resources'][r]['rows'][j]['max_mw'])
            else:
                m.rows.add(c<=u['charge_max_mw']*m.mode[r,j//12,'charge']);m.rows.add(z<=u['discharge_max_mw']*m.mode[r,j//12,'discharge'])
                m.power[r,j].setlb(-u['charge_max_mw']);m.power[r,j].setub(u['discharge_max_mw'])
            m.rows.add(m.power[r,j]==z-c);m.rows.add(m.power[r,j]-prev<=u['ramp_up_mw_per_h']/12);m.rows.add(prev-m.power[r,j]<=u['ramp_down_mw_per_h']/12);prev=m.power[r,j]
            m.energy[r,j+1].setlb(u['energy_min_mwh']);m.energy[r,j+1].setub(u['energy_max_mwh']);m.rows.add(m.energy[r,j+1]==m.energy[r,j]+(v['eta_charge']*c-z/v['eta_discharge'])/12)
        m.rows.add(m.energy[r,288]==u['terminal_energy_mwh'])
        if is_closed(d):m.rows.add(m.energy[r,576]==.5*u['energy_capacity_mwh'])
        for day,ledger in x['resources'][r]['cycle_budget'].items():
            ids=[j for j,cell in enumerate(x['cells'])if cell['start'][:10]==day];require(len(ids)==288,'full natural day cycle scope')
            m.rows.add(sum((v['eta_charge']*m.charge[r,j]+m.discharge[r,j]/v['eta_discharge'])/12 for j in ids)<=ledger['limit_internal_mwh']-ledger['used_internal_mwh'])
    m.injection=p.Expression(N,J,rule=lambda _,n,j:m.zero+sum(m.power[r,j]for r in R if b['resources'][r]['node']==n)+x['fixed_injection_mw'][n][j]-x['load_mw'][n][j]);m.balance=p.Constraint(J,rule=lambda _,j:sum(m.injection[n,j]for n in N)==0)
    m.flow=p.Expression(L,J,rule=lambda _,l,j:sum(b['network'][l]['ptdf'][n]*m.injection[n,j]for n in N));m.line_upper=p.Constraint(L,J,rule=lambda _,l,j:m.flow[l,j]<=x['network'][l]['upper_mw'][j]);m.line_lower=p.Constraint(L,J,rule=lambda _,l,j:-m.flow[l,j]<=-x['network'][l]['lower_mw'][j])
    for j,req in enumerate(x['reserve']):
        for service in ('positive','negative','primary','accident'):m.rows.add(m.zero+sum(m.reserve[r,j,service]for r in G)>=req[service+'_mw'])
    # Exact authored segment endpoints, at every physical cell. No old market
    # idle zero bounds or dummy width-one segments enter this model.
    quotes={};keys=[]
    for r in R:
        for direction in (('energy',)if r in G else('charge','discharge')):
            curve=b['resources'][r]['original_offer']if r in G else b['resources'][r]['original_curves'][direction]
            quotes[r,direction]=curve;keys.extend((r,direction,j,k)for j in J for k in range(len(curve)))
    m.segment=p.Var(keys,domain=p.NonNegativeReals);cost=0.
    for (r,direction),curve in quotes.items():
        for j in J:
            amount=m.power[r,j]if r in G else m.charge[r,j]if direction=='charge'else m.discharge[r,j]
            m.rows.add(amount==sum(m.segment[r,direction,j,k]for k in range(len(curve))))
            for k,s in enumerate(curve):
                m.segment[r,direction,j,k].setub(s['p_end']-s['p_start']);cost+=(-1 if direction=='charge'else 1)*s['price']*m.segment[r,direction,j,k]/12
    m.objective=p.Objective(expr=cost)
    payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS))
    return m,d,dict(schema=SCHEMA,input_sha256=digest(d),matrix_sha256=digest(payload),variables=len(vs),rows=len(cs),integer_variables=sum(v['kind']!='C'for v in payload['variables']),fixed_D_day_modes=True,D_day_dispatch_fixed=False,thermal_states_fixed=True,full_SCUC=False,official_equivalence=False,objective=d['policy']['objective'],native_calls=0)

def _freeze_modes(data,modes):
    """Private algebraic conversion. Public native entry validates binary evidence."""
    d=validate(data);b=deepcopy(d['base']);x=b['physical_lift'];B=list(d['technical']);require(set(modes)==set(B),'mode inventory')
    for r in B:
        u=d['technical'][r];require(len(modes[r])==24 and all(k in MODES for k in modes[r]),'24 native selected hourly modes')
        for j in range(288,576):
            mode=modes[r][j//12-24];row=x['resources'][r]['rows'][j];row.update(mode=mode,min_mw=-u['charge_max_mw']if mode=='charge'else 0.,max_mw=u['discharge_max_mw']if mode=='discharge'else 0.)
        if is_closed(d):x['resources'][r]['terminal_targets']=[dict(boundary_index=576,energy_mwh=.5*u['energy_capacity_mwh'],source='new v3 research Dplus2 midnight 50percent closure; not original mandated target')]
        else:require('terminal_targets'not in x['resources'][r],'open-tail base must not carry extra terminal equality')
        for t in range(96,98):
            ids=x['market_to_physical'][t];mode=modes[r][ids[0]//12-24];row=b['resources'][r]['rows'][t];lo=-u['charge_max_mw']if mode=='charge'else 0.;hi=u['discharge_max_mw']if mode=='discharge'else 0.;curve=b['resources'][r]['original_curves']['charge'if mode=='charge'else'discharge']
            if mode=='charge':segments=[dict(width_mw=s['p_end']-s['p_start'],price_yuan_mwh=s['price'])for s in reversed(curve)];intercept=-sum((s['p_end']-s['p_start'])*s['price']for s in curve)
            elif mode=='discharge':segments=[dict(width_mw=s['p_end']-s['p_start'],price_yuan_mwh=s['price'])for s in curve];intercept=0.
            else:segments=[dict(width_mw=1.,price_yuan_mwh=0.)];intercept=0.
            row.update(mode=mode,min_mw=lo,max_mw=hi,sced_mw=0.,online=True,price_eligible=mode!='idle',eligibility_reason='native upstream BESS selected hourly mode, research pricing assumption',segments=segments,offer_intercept_yuan_h=intercept)
    b['strategy_id']='v3_native_BESS_modes_on_v2_thermal'if is_closed(d)else'v4_native_BESS_modes_original_bounds_tail';x['extension_policy']='native partial upstream Dplus1 BESS hourly modes; v2 thermal unchanged; new Dplus2 midnight50percent SOC research closure'if is_closed(d)else'native partial upstream Dplus1 BESS hourly modes; v2 thermal unchanged; E576 original SOC bounds only, open research tail not official E98 or continuity certificate';b['upstream_policy']=d['policy'];b['upstream_input_sha256']=digest(d)
    # Existing contract is unchanged: representatives are the same means and
    # D96 remains the official-ambiguity terminal interpretation. The D+2
    # endpoint is an explicitly additional physical research constraint.
    validate_lift_contract_binding(b);return b

def validate_mip_evidence(raw):
    require(isinstance(raw,dict) and raw.get('status')=='optimal' and raw.get('has_primal')is True and raw.get('backend')=='highs' and raw.get('solver_version')=='1.15.1','optimal actual native MIP evidence required')
    settings=raw.get('native_settings',{});require(settings.get('threads')==1 and settings.get('mip_rel_gap')==.001 and settings.get('mip_abs_gap')==0.,'MIP native settings mismatch')
    for key in ('objective','bound','native_mip_gap'):require(type(raw.get(key))in(int,float) and math.isfinite(raw[key]),'finite MIP objective/bound/gap required')
    objective=raw['objective'];bound=raw['bound'];gap=raw['native_mip_gap'];require(0<=gap<=.001+1e-10 and bound<=objective+1e-6,'MIP gap/bound domain')
    actual=abs(objective-bound)/abs(objective) if objective!=0 else (0. if bound==0 else math.inf)
    require(math.isfinite(actual) and actual<=.001+1e-10 and abs(actual-gap)<=1e-7,'native bound/objective inconsistent with reported relative gap')
    return dict(recomputed_relative_gap=actual,reported_relative_gap=gap)

def solve_native(data,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);require(not(out/'request.json').exists(),'no evidence overwrite')
    m,d,audit=build_model(data);payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));payload['deadline']=time.perf_counter()+OPTIONS['time_limit'];payload['preparation_elapsed_s']=0.
    for name,obj in [('request',payload),('input',d),('names',dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]))]:(out/(name+'.json')).write_bytes(canonical(obj))
    done=hs.run_child([sys.executable,str(Path(hs.__file__).with_name('highs_worker.py')),str(out/'request.json'),str(out/'response.json')],env=hs.child_environment(),timeout=OPTIONS['time_limit'],tee=False)
    require(done.returncode==0 and(out/'response.json').exists(),'upstream worker failed; retain evidence, no retry');raw=json.loads((out/'response.json').read_text())
    if raw.get('error') or 'status'not in raw:raise RuntimeError('upstream worker error/protocol failure: '+str(raw)+'; no retry')
    require(raw['status']=='optimal' and raw['has_primal'] and raw['backend']=='highs' and raw['solver_version']=='1.15.1','optimal actual HiGHS1.15.1 MIP required')
    require(raw['native_settings']['threads']==1 and raw['native_settings']['mip_rel_gap']==.001 and raw['native_settings']['mip_abs_gap']==0.,'native settings changed')
    require(raw['native_mip_gap']is not None and raw['native_mip_gap']<=.001+1e-10,'MIP gap certificate')
    validate_mip_evidence(raw);values=hs.validate_solution(payload,raw)
    for v,z in zip(vs,values):v.set_value(z)
    audit['residuals']=write_full_residuals(payload,values,vs,cs,out/'all_primal_residuals.json.gz');require(audit['residuals']['maximum_violation']<=1e-6,'raw primal residual gate')
    modes={r:[next(k for k in MODES if p.value(m.mode[r,h,k])>.5)for h in range(24,48)]for r in d['technical']};audit.update(status='optimal',objective_yuan=raw['objective'],bound_yuan=raw['bound'],mip_gap=raw['native_mip_gap'],native_calls=1,selected_modes=modes)
    (out/'result.json').write_bytes(canonical(audit));return freeze_native_output(d,out)

def freeze_native_output(data,out):
    """Reconstruct and bind actual MIP matrix/primal before downstream use."""
    out=Path(out);saved=json.loads((out/'input.json').read_text());require(digest(data)==digest(saved),'upstream input binding')
    m,d,_=build_model(saved);payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));actual=json.loads((out/'request.json').read_text());raw=json.loads((out/'response.json').read_text());names=json.loads((out/'names.json').read_text())
    for k in ('variables','rows','objective','options'):require(payload[k]==actual[k],'upstream matrix mismatch '+k)
    require(names==dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]),'upstream names mismatch')
    require(raw.get('status')=='optimal' and raw.get('backend')=='highs' and raw.get('solver_version')=='1.15.1' and raw.get('native_mip_gap')is not None and raw['native_mip_gap']<=.001+1e-10,'native optimal source required')
    validate_mip_evidence(raw);values=hs.validate_solution(actual,raw);lookup=dict(zip(names['variables'],values));modes={}
    for r in d['technical']:
        modes[r]=[]
        for h in range(24,48):
            z=[lookup[f'mode[{r},{h},{k}]']for k in MODES];require(abs(sum(z)-1)<=1e-6 and all(abs(v-round(v))<=1e-6 for v in z),'native binary one-hot mode proof');modes[r].append(MODES[z.index(max(z))])
    frozen=_freeze_modes(d,modes);frozen['source_binding']['artifacts'].extend(dict(role='upstream_BESS_'+k,path=str((out/(k+'.json')).resolve()),sha256=sha256((out/(k+'.json')).read_bytes()).hexdigest())for k in ('input','request','response','names'))
    frozen['native_upstream_modes']=modes;witness=project_sced_witness(frozen,actual,raw,names);(out/'projected_sced_witness_audit.json').write_bytes(canonical(witness));(out/'frozen_sced_input.json').write_bytes(canonical(frozen));return frozen


def validate_frozen_binding(frozen):
    """Validate native binary values and full projection at every SCED use."""
    require(frozen.get('strategy_id')in('v3_native_BESS_modes_on_v2_thermal','v4_native_BESS_modes_original_bounds_tail'),'upstream evidence requires legitimate native strategy id')
    artifacts={a['role']:a for a in frozen['source_binding']['artifacts']if a['role'].startswith('upstream_BESS_')}
    require(set(artifacts)=={'upstream_BESS_'+k for k in ('input','request','response','names')},'complete native upstream artifacts required')
    records={}
    for role,a in artifacts.items():
        blob=Path(a['path']).read_bytes();require(sha256(blob).hexdigest()==a['sha256'],'upstream evidence changed');records[role.removeprefix('upstream_BESS_')]=json.loads(blob)
    data=records['input'];m,d,_=build_model(data);payload,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));actual=records['request'];raw=records['response'];names=records['names']
    for k in ('variables','rows','objective','options'):require(payload[k]==actual[k],'upstream source matrix mismatch')
    require(names==dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]),'upstream names mismatch')
    require(raw.get('status')=='optimal' and raw.get('backend')=='highs' and raw.get('solver_version')=='1.15.1' and raw.get('native_mip_gap')is not None and raw['native_mip_gap']<=.001+1e-10,'accepted native upstream required')
    validate_mip_evidence(raw);values=hs.validate_solution(actual,raw);lookup=dict(zip(names['variables'],values));modes={}
    for r in d['technical']:
        modes[r]=[]
        for h in range(24,48):
            z=[lookup[f'mode[{r},{h},{k}]']for k in MODES];require(abs(sum(z)-1)<=1e-6 and all(abs(v-round(v))<=1e-6 for v in z),'upstream mode integrality');modes[r].append(MODES[z.index(max(z))])
    expected=_freeze_modes(d,modes);expected['source_binding']['artifacts'].extend(artifacts['upstream_BESS_'+k]for k in ('input','request','response','names'));expected['native_upstream_modes']=modes
    require(digest(expected)==digest(frozen),'frozen SCED differs from native mode projection')
    return dict(validated=True,upstream_input_sha256=digest(d),mode_source='actual_native_binary_not_power_sign')


def project_sced_witness(frozen,request,response,names):
    """Check every downstream SCED row using upstream primal, without solving."""
    from .gd_r14_da_sced import build_model as build_sced
    validate_mip_evidence(response);values=hs.validate_solution(request,response);source=dict(zip(names['variables'],values))
    m,d,_=build_sced(frozen);x=d['physical_lift'];m.zero.set_value(0.)
    for r,u in d['resources'].items():
        for j in range(576):
            m.physical_power[r,j].set_value(source[f'power[{r},{j}]'])
            for service,component in [('positive',m.physical_up),('negative',m.physical_down),('primary',m.physical_primary),('accident',m.physical_accident)]:component[r,j].set_value(source[f'reserve[{r},{j},{service}]']if u['kind']=='generator'else 0.)
        if u['kind']=='battery':
            for j in range(577):m.physical_energy[r,j].set_value(source[f'energy[{r},{j}]'])
        for t,ids in enumerate(x['market_to_physical']):
            power=math.fsum(source[f'power[{r},{j}]']for j in ids)/3;m.power[r,t].set_value(power);remaining=power-u['rows'][t]['min_mw']
            for k,s in enumerate(u['rows'][t]['segments']):take=max(0.,min(remaining,s['width_mw']));m.seg[r,t,k].set_value(take);remaining-=take
            require(abs(remaining)<=1e-6,'market segment coverage cannot represent projected physical power')
    matrix,vs,cs=hs.serialize_model(m,hs.validate_options(OPTIONS));primal=[v.value for v in vs];require(all(z is not None and math.isfinite(z)for z in primal),'complete projected witness required')
    worst=0.;worst_name=None
    for i,v in enumerate(matrix['variables']):
        violation=max(0.,0. if v['lb']is None else v['lb']-primal[i],0. if v['ub']is None else primal[i]-v['ub'])
        if violation>worst:worst=violation;worst_name=vs[i].name
    for row,c in zip(matrix['rows'],cs):
        value=math.fsum(primal[i]*coef for i,coef in row['terms']);violation=max(0.,0. if row['lb']is None else row['lb']-value,0. if row['ub']is None else value-row['ub'])
        if violation>worst:worst=violation;worst_name=c.name
    require(worst<=1e-6,'upstream primal does not witness downstream fixed-mode SCED')
    return dict(passed=True,variables=len(vs),rows=len(cs),maximum_violation=worst,worst_name=worst_name,upstream_primal_sha256=digest(primal),downstream_matrix_sha256=digest(matrix),SCED_optimization_performed=False,primal_projection_only=True)
