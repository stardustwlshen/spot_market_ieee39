"""Original IEEE39/quotes/400 MW adapter; historical DA status only, never RT.

Two full synthetic days are the stronger physical domain. D+1 final-state
continuation is frozen before feasibility inspection and is never repaired.
"""
from copy import deepcopy
from datetime import datetime,timedelta
from hashlib import sha256
from pathlib import Path
import math, json
from .gd_r14_da_sced import require,digest
from .gd_october_job_codec import loads
from .gd_r13_source_recipe import source_cells
from .gd_october_pricing_network import dc_ptdf,normalize_topology_ptdf
from .gd_da_ieee39 import STATIC_SHA256

DEFAULT_DA=Path('/workspace/scratch/42c5fe32dad0/guangdong_r13_checkpoints_20261111/partial24_v3_restored_numerical/causal_rt_native_v3/reports/source_replay')

def clipped_offer(raw,lo,hi):
    """Exact original breakpoints intersected with physical range; no rebinning."""
    intercept=sum(max(0,min(lo,float(s['p_end']))-float(s['p_start']))*float(s['price'])for s in raw)
    seg=[dict(width_mw=min(hi,float(s['p_end']))-max(lo,float(s['p_start'])),price_yuan_mwh=float(s['price']))for s in raw if min(hi,float(s['p_end']))>max(lo,float(s['p_start']))]
    return seg or [dict(width_mw=1.,price_yuan_mwh=0.)],intercept

def prepare(root,da_source=DEFAULT_DA,*,strategy="v1_carry_final_state"):
    require(strategy in ("v1_carry_final_state","v2_forecast_GT_start"),"explicit strategy identity")
    root=Path(root);da_source=Path(da_source)
    names=('source_reconstructed_day_inputs.json','issued_day_research_plan.json','fresh_boundary_report.json')
    blobs={n:(da_source/n).read_bytes()for n in names};original=loads(blobs[names[0]]);plan=loads(blobs[names[1]])
    require(plan['publication_clock_is_simulated'] and not plan['historical_publication_claim'] and plan['accepted_research_plan'],'accepted research DA provenance')
    require(plan['publication_kind']=='initial_day' and not plan['actual_command_history'] and len(plan['physical_cells'])==288,'DA-only input; no RT/tail plan')
    e=original['energy_inputs'];day=datetime.fromisoformat(plan['day_start']);forecast=source_cells(root,count=576);cells=forecast['cells'];N=list(e['buses']);require(forecast['static_sha256']==STATIC_SHA256,'original static identity')
    # Publication is a logical research clock. The fixed D00 boundary is a
    # predeclared hypothesis, not telemetry received before the DA cutoff.
    require(plan['version_available_at']<=plan['day_start'],'prospective simulated plan source')
    from .gd_da_candidate import compile_path
    state_checks={}
    for g,u in e['thermal'].items():
        spec=next(z for z in original['candidates'][g] if z['id']==plan['selected_paths'][g])
        compiled=compile_path(u,spec,tuple(range(288)),original['contract'])
        for j,row in enumerate(compiled['cells']):
            saved=plan['physical_cells'][j]['thermal'][g]
            require(row['phase']==saved['phase'],'DA fixed phase differs from recompiled source path')
            if row['phase'] in ('startup','shutdown'):
                require(max(abs(row[k]-saved[z])for k,z in [('begin','begin_mw'),('mean','mean_mw'),('end','end_mw')])<1e-7,'DA transition curve mismatch')
        state_checks[g]=dict(recompiled=True,minimum_residence_verified=True,notice_verified=True,curve_continuity_verified=True,terminal=compiled['terminal'])
    qloads=[sum(sum(cells[3*q+k]['values']['load_mw'].values())for k in range(3))/3 for q in range(96,192)]
    peak=96+max(range(96),key=lambda q:qloads[q]);valley=96+min(range(96),key=lambda q:qloads[q]);representatives=sorted((peak,valley));selected=list(range(96))+representatives;mapping=[list(range(3*q,3*q+3))for q in selected]
    grid=[]
    for t,q in enumerate(selected):
        a=day+timedelta(minutes=15*q);end=a+timedelta(minutes=15);prior_end=day if t==0 else datetime.fromisoformat(grid[-1]['end']);grid.append(dict(start=a.isoformat(),end=end.isoformat(),actual_time=end.isoformat(),settlement=t<96,kind='delivery'if t<96 else'peak'if q==peak else'valley',objective_weight_h=.25,physical_duration_h=.25,ramp_elapsed_h=(end-prior_end).total_seconds()/3600,predecessor=t-1 if t else None,constraint_scope='full576_physical_lift_research',power_semantics='interval_mean'))
    x=dict(cells=[dict(start=c['start'],end=c['end'])for c in cells],market_to_physical=mapping,availability_is_simulated=True,future_RT_used=False,state_source=sha256(blobs[names[1]]).hexdigest(),extension_policy='D+1 all thermal carry D-end stable/off state without events; all BESS idle continuously; full day synthetic prior and 400MW constraints; no reset',resources={},network={},load_mw={n:[c['values']['load_mw'][n]for c in cells]for n in N},fixed_injection_mw={n:[sum(c['values'][f][n]for f in ('wind_mw','solar_mw','boundary_tie_mw'))for c in cells]for n in N},reserve=[dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.)for _ in cells])
    d=dict(schema='gd.r14.da_sced.v1',stage='fixed_known_state_SCED',fixed_state_available_at=plan['version_available_at'],planned_publication_at=plan['publication_at'],grid=grid,nodes=N,resources={},network={},groups=[],reserve=[deepcopy(x['reserve'][ids[0]])for ids in mapping],physical_lift=x,source_binding=dict(original_static_sha256=STATIC_SHA256,artifacts=[dict(role='historical_DA_only',path=str((da_source/n).resolve()),sha256=sha256(blobs[n]).hexdigest())for n in names]),profile=dict(mode='research',research_profile='DA98_projection_full576_fixed_R13_DA_state_v1',rule_source='regional2026V1 2.5.4/2.5.5',delta_source='explicit research sensitivity parameter, not official value',eligibility_source='stable thermal and BESS engineering research assumption',initial_state_source='predeclared prepared D00 hypothesis; coal48h/gas8h at23:45 plus3 physical preparation steps; not original all-gas-off reachability',sced_source='fresh fixed-known-state SCED',parameter_source='six original IEEE39 statics; seeded synthetic prior',storage_interpretation='signed_magnitude_fixed_sced_mode_research',excluded_resource_types=['PS fixed off with original inventory retained','no interprovincial market variables','fixed wind/solar/tie no flexible reserves'],storage_terminal_rule_version='early_Guangdong_D96_research_not_regional_E98',official_unknowns=['delta','representative chronology/weight/transition','storage printed band','E_T mapping']),storage_inventory=deepcopy(original['storage_inventory']),source_availability=dict(logical_only=True,historical_publication_claim=False,available_at=plan['version_available_at'],RT_used=False))
    for fam in ('load_mw','fixed_injection_mw'):d[fam]={n:[sum(x[fam][n][j]for j in ids)/3 for ids in mapping]for n in N}
    startup=None
    if strategy=='v2_forecast_GT_start':
        caps=sum(u['max_mw'][0] for g,u in e['thermal'].items() if plan['physical_cells'][-1]['thermal'][g]['phase']=='stable')
        deficits=[j for j in range(288,576) if caps-sum(x['load_mw'][n][j]-x['fixed_injection_mw'][n][j] for n in N)<400.]
        require(deficits,'v2 requires forecast-based need, not discretionary restart')
        curve_doc=json.loads(original['contract']['sources']['curves']['artifact']);count=curve_doc['durations_minutes']['GT']['startup']//5
        end=min(deficits);begin=end-count;minimum=e['thermal']['GT']['min_mw'][0]
        require(begin>=288 and plan['physical_cells'][-1]['thermal']['GT']['phase']=='off','v2 expects inherited off GT')
        age=plan['physical_cells'][-1]['thermal']['GT']['age_end_h']+(begin-288)/12
        require(age>=e['thermal']['GT']['min_down_h'],'minimum down must elapse before startup')
        require((576-end)/12>=e['thermal']['GT']['min_up_h'],'minimum up must be carried after completion')
        at=day+timedelta(minutes=5*begin);notice=at-timedelta(minutes=30)
        require(datetime.fromisoformat(plan['publication_at'])<=notice,'notice cannot precede strategy availability')
        startup=dict(strategy_id=strategy,trigger='first forecast capacity gap under v1, independent of solver',first_capacity_gap_index=end,start_index=begin,completion_index=end,effective_at=at.isoformat(),notification_at=notice.isoformat(),available_at=plan['publication_at'],inherited_off_age_h_at_start=age,minimum_down_h=e['thermal']['GT']['min_down_h'],minimum_up_h=e['thermal']['GT']['min_up_h'],curve_source_sha256=sha256(original['contract']['sources']['curves']['artifact']).hexdigest(),steps=[dict(begin_mw=minimum*k/count,mean_mw=minimum*(k+.5)/count,end_mw=minimum*(k+1)/count)for k in range(count)],research_only=True,no_future_RT=True)
        x['extension_policy']='v2 forecast-driven single GT start using original30min curve and30min notice; other thermal carries; BESS idle; no reset'
        x['prospective_startup']=startup
    d['strategy_id']=strategy;d['fixed_state_validation']=state_checks
    for g,u in e['thermal'].items():
        physical=[];rawbid=original['static']['bids']['units'][g]['segments']
        for j,c in enumerate(cells):
            z=deepcopy(plan['physical_cells'][min(j,287)]['thermal'][g])
            if startup and g=='GT' and j>=startup['start_index']:
                if j<startup['completion_index']:
                    z.update(startup['steps'][j-startup['start_index']],phase='startup',curve_step=j-startup['start_index'])
                else:z.update(phase='stable',curve_step=None)
            stable=z['phase']=='stable';phase=z['phase'];require(j<288 or phase in ('stable','off')or(startup and g=='GT' and startup['start_index']<=j<startup['completion_index']),'cannot extend unfinished thermal event without curve')
            physical.append(dict(phase=phase,min_mw=u['min_mw'][0]if stable else z['mean_mw'],max_mw=u['max_mw'][0]if stable else z['mean_mw'],mean_mw=z['mean_mw'],begin_mw=z['begin_mw'],end_mw=z['end_mw'],ramp_up_mw_h=u['ramp_up_mw_per_h'][0],ramp_down_mw_h=u['ramp_down_mw_per_h'][0],reserve_eligible=stable,positive_cap_mw=u['max_mw'][0]-u['min_mw'][0]if stable else 0.,negative_cap_mw=u['max_mw'][0]-u['min_mw'][0]if stable else 0.,primary_cap_mw=u['max_mw'][0]*original['reserve']['primary_fraction'][g]if stable else 0.,accident_cap_mw=u['ramp_up_mw_per_h'][0]/6 if stable and original['reserve']['contingency_eligible'][g]else 0.,event_id=f'{g}:{j-z["curve_step"]}'if z['curve_step']is not None else None,curve_source=(startup['curve_source_sha256']if startup and j>=288 else digest(plan['events']))if z['curve_step']is not None else None))
        rows=[]
        for ids in mapping:
            rr=[physical[j]for j in ids];lo=sum(z['min_mw']for z in rr)/3;hi=sum(z['max_mw']for z in rr)/3;stable=all(z['phase']=='stable'for z in rr);online=any(z['phase']!='off'for z in rr);seg,intercept=clipped_offer(rawbid,lo,hi)
            rows.append(dict(min_mw=lo,max_mw=hi,sced_mw=lo,online=online,price_eligible=stable,eligibility_reason='stable thermal assumed eligible'if stable else'fixed physical transition/off',reserve_eligible=stable,positive_cap_mw=hi-lo if stable else 0.,negative_cap_mw=hi-lo if stable else 0.,primary_cap_mw=sum(z['primary_cap_mw']for z in rr)/3 if stable else 0.,accident_cap_mw=sum(z['accident_cap_mw']for z in rr)/3 if stable else 0.,ramp_up_mw_h=u['ramp_up_mw_per_h'][0],ramp_down_mw_h=u['ramp_down_mw_per_h'][0],segments=seg,offer_intercept_yuan_h=intercept))
        d['resources'][g]=dict(kind='generator',node=u['bus'],initial_power_mw=u['initial_power_mw'],rows=rows,original_offer=deepcopy(rawbid));x['resources'][g]=dict(rows=physical)
    for b,u in original['batteries'].items():
        physical=[]
        for j,c in enumerate(cells):
            z=plan['physical_cells'][j]['batteries'][b]if j<288 else dict(mode='idle');mode=z['mode'];physical.append(dict(mode=mode,min_mw=-u['charge_max_mw']if mode=='charge'else 0.,max_mw=u['discharge_max_mw']if mode=='discharge'else 0.,ramp_up_mw_h=u['ramp_up_mw_per_h'],ramp_down_mw_h=u['ramp_down_mw_per_h'],reserve_eligible=False,positive_cap_mw=0.,negative_cap_mw=0.,accident_cap_mw=0.,primary_cap_mw=0.))
        rows=[]
        for ids in mapping:
            rr=[physical[j]for j in ids];modes={z['mode']for z in rr};require(not {'charge','discharge'}<=modes,'opposite storage directions');mode=next(iter(modes-{'idle'}),'idle');lo=sum(z['min_mw']for z in rr)/3;hi=sum(z['max_mw']for z in rr)/3;curve=u['charge_curve']if mode=='charge'else u['discharge_curve'];bp=curve['breakpoints_mw'];prices=curve['prices_yuan_per_mwh'];rawbid=[dict(p_start=a,p_end=z,price=v)for a,z,v in zip(bp,bp[1:],prices)]
            if mode=='charge':
                seg=[dict(width_mw=float(min(-lo,z)-a),price_yuan_mwh=float(v))for a,z,v in reversed(list(zip(bp,bp[1:],prices)))if min(-lo,z)>a];intercept=-sum(max(0,min(-lo,z)-a)*v for a,z,v in zip(bp,bp[1:],prices))
            else:seg,intercept=clipped_offer(rawbid,lo,hi)
            rows.append(dict(min_mw=lo,max_mw=hi,sced_mw=0.,online=True,mode=mode,price_eligible=mode!='idle',eligibility_reason='fixed mode signed-magnitude research',reserve_eligible=False,positive_cap_mw=0.,negative_cap_mw=0.,primary_cap_mw=0.,accident_cap_mw=0.,ramp_up_mw_h=u['ramp_up_mw_per_h'],ramp_down_mw_h=u['ramp_down_mw_per_h'],segments=seg,offer_intercept_yuan_h=intercept))
        eta=math.sqrt(u['roundtrip_efficiency']);d['resources'][b]=dict(kind='battery',node=u['bus'],initial_power_mw=u['initial_power_mw'],initial_energy_mwh=u['initial_energy_mwh'],energy_min_mwh=u['energy_min_mwh'],energy_max_mwh=u['energy_max_mwh'],eta_charge=eta,eta_discharge=eta,rows=rows,cycle_throughput_limit_mwh=2*u['energy_capacity_mwh'],cycle_scope='D96',terminal_interpretation='D96_only_research',terminal_targets=[dict(index=95,energy_mwh=u['terminal_energy_mwh'])]);d['resources'][b]['original_curves']={direction:[dict(p_start=a,p_end=z,price=v)for a,z,v in zip(u[direction+'_curve']['breakpoints_mw'],u[direction+'_curve']['breakpoints_mw'][1:],u[direction+'_curve']['prices_yuan_per_mwh'])]for direction in ('charge','discharge')};x['resources'][b]=dict(rows=physical,cycle_budget={str((day+timedelta(days=k)).date()):dict(used_internal_mwh=0.,limit_internal_mwh=2*u['energy_capacity_mwh'])for k in range(2)})
    lines={l:dict(from_node=z['from_bus'],to_node=z['to_bus'],susceptance_mw_per_rad=z['susceptance_mw_per_rad'])for l,z in e['lines'].items()};factors=dc_ptdf(tuple(N),lines,e['reference_buses'][0]);factors,pa=normalize_topology_ptdf(tuple(N),lines,e['reference_buses'][0],factors)
    for l,z in e['lines'].items():d['network'][l]=dict(kind='line',ptdf=factors[l],lower_mw=[z['lower_mw'][0]]*98,upper_mw=[z['upper_mw'][0]]*98,penalty_yuan_mwh=1000.,penalty_source='regional2026V1 appendix network pricing default');x['network'][l]=dict(lower_mw=[z['lower_mw'][0]]*576,upper_mw=[z['upper_mw'][0]]*576)
    require(not e['sections'],'no silently omitted sections')
    from .gd_r14_da_contract import contract_from_lift,validate_physical_projection
    contract=contract_from_lift(d,scenario_id='IEEE39_'+strategy,information_cutoff=plan['publication_at'],generated_at=datetime.now().astimezone().isoformat(),research_assumptions=dict(choices='; '.join(['98 market objective only; hidden physical bridge has no economic objective','representative interval means and0.25h objective weights are research assumptions','physical network hard bounds restrict pricing M1-prime slacks','D96 storage target selected explicitly; regional E98 unresolved','R13 DA state is fixed, not official or free SCUC','all initial/future availability clocks are research assumptions'])),storage_terminal_state_id='D96',storage_terminal_rule_version='early_Guangdong_D96_research',storage_terminal_source='original IEEE39 midnight storage target retained, regional E_T interpretation unresolved')
    d['contract']=contract.payload();d['contract_sha256']=contract.sha256;d['physical_projection_audit']=validate_physical_projection(contract,x);d['precheck']=capacity_precheck(d);return d

def capacity_precheck(d):
    x=d['physical_lift'];margins=[];reachable=[];previous={r:u['initial_power_mw']for r,u in d['resources'].items()};startup_trace=[]
    for j,c in enumerate(x['cells']):
        demand=sum(x['load_mw'][n][j]-x['fixed_injection_mw'][n][j]for n in d['nodes']);maxpower=sum(u['rows'][j]['max_mw']for u in x['resources'].values());margin=maxpower-demand-x['reserve'][j]['positive_mw'];margins.append(margin)
        caps={}
        for r,u in x['resources'].items():
            z=u['rows'][j]
            if z.get('phase') in ('startup','shutdown'):caps[r]=z['mean_mw'];previous[r]=z['end_mw']
            else:caps[r]=min(z['max_mw'],previous[r]+z['ramp_up_mw_h']/12);previous[r]=caps[r]
        reachable.append(sum(caps.values())-demand-x['reserve'][j]['positive_mw'])
        if x.get('prospective_startup') and x['prospective_startup']['start_index']-2<=j<=x['prospective_startup']['completion_index']+3:startup_trace.append(dict(index=j,start=c['start'],GT_forward_upper_mw=caps['GT'],reachable_generation_minus_demand_and400=reachable[-1]))
    return dict(no_solver_called=True,min_capacity_minus_load_plus400_mw=min(margins),min_Dplus1_margin_mw=min(margins[288:]),negative_capacity_cells=[j for j,v in enumerate(margins)if v< -1e-6],forward_ramp_envelope_min_margin_mw=min(reachable),negative_forward_envelope_cells=[j for j,v in enumerate(reachable)if v< -1e-6],startup_forward_trace=startup_trace,necessary_not_sufficient=True,envelope_caveat='independent upper reachability envelope; not joint network/SOC/operating-reserve certification',GT_Dplus1_final_phase=x['resources']['GT']['rows'][-1]['phase'],repair_performed=False)
