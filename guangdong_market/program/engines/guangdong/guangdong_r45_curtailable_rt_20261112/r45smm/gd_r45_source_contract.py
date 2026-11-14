"""Fresh R35-bound research source contract. Never reuse archived native permit."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from weakref import WeakKeyDictionary
from contextlib import contextmanager
from . import gd_r41_static_stage as static_stage_checks
import json,math
from hashlib import sha256
from .gd_r15_da_rt_bridge import require,close,digest,stamp,integrate_cell,_technical,SCOPES
from .gd_r17_midnight_contract import window_spec,derive_price_quarters,quarter_eligibility,TARGETS,POLICY as OLD_POLICY
from .gd_october_job_codec import loads,dumps
SCHEMA='gd.research.r45.new_da_source_contract.v1'
DA_KIND='new_R42_physical_schedule96_NOT_financial_publication'
TAIL_KIND='new_accepted_R35_SCED_chronological_tail_NOT_published_DA'
POLICY=deepcopy(OLD_POLICY)
POLICY.update(schema=SCHEMA+'.policy',tail_reference=TAIL_KIND,source='fresh accepted R35 chain; archived preparation facts only',native_release_available=False,native_calls_authorized=0)
_CONTRACTS=WeakKeyDictionary()
class ContractPermit:__slots__=('__weakref__',)
def _read(p):return json.loads(Path(p).read_bytes())
def _sha(p):return sha256(Path(p).read_bytes()).hexdigest()
def _source_digest(x):return sha256(dumps(x)).hexdigest()
def _preparation(case,data,archived):
    prep=deepcopy(archived['initial_preparation'])
    require(archived['day_start']==case['contract']['day_start']==data['grid'][0]['start'],'same prepared research date')
    require(prep['historical_telemetry'] is False and len(prep['cells'])==3,'three archived preparation facts only')
    tech=_technical(case,data);oldtech=archived['technical']
    require(set(tech['thermal'])==set(oldtech['thermal']) and set(tech['batteries'])==set(oldtech['batteries']),'prepared inventory changed')
    for g,t in tech['thermal'].items():
        for k,v in t.items():close(v,oldtech['thermal'][g][k],'thermal technical mismatch')
    for b,t in tech['batteries'].items():
        for k,v in t.items():
            if k=='cycle_budget':continue
            if isinstance(v,dict):require(v==oldtech['batteries'][b][k],'battery technical dict mismatch')
            else:close(v,oldtech['batteries'][b][k],'battery technical mismatch')
    state=deepcopy(prep['seed_state'])
    require(stamp(state['at'])==stamp(case['contract']['day_start'])-timedelta(minutes=15),'prep T-15')
    for cell in prep['cells']:state=integrate_cell(state,cell,tech)
    require(digest(state)==digest(prep['midnight_state']),'prep reintegration mismatch')
    for g,u in case['energy_inputs']['thermal'].items():
        z=state['thermal'][g]
        require(z['phase']==('stable' if u['initial_online'] else 'off') and z['online']==u['initial_online'] and z['curve_step'] is None,'initial phase differs')
        close(z['power_mw'],u['initial_power_mw'],'initial power differs');close(z['state_age_h'],u['initial_state_age_h'],'initial age differs')
    for b,u in case['batteries'].items():
        z=state['batteries'][b]
        for sk,uk in [('energy_mwh','initial_energy_mwh'),('power_mw','initial_power_mw'),('mode_age_h','initial_mode_age_h')]:close(z[sk],u[uk],'initial battery differs')
        require(z['mode']==u['initial_mode'] and z['hour_direction']==u['initial_hour_direction'],'initial battery direction differs')
        close(sum(z['daily_used_internal_mwh'].values()),z['cumulative_internal_mwh'],'usage history mismatch')
        for date,budget in tech['batteries'][b]['cycle_budget'].items():close(z['daily_used_internal_mwh'].get(date,0.),budget['used_internal_mwh'],'dated initial usage differs')
    prep['reuse_scope']='archived preparation facts only; fresh synthetic R45 session, not actual telemetry or prior-day feasibility'
    return prep,tech

def _physical_cells(case,data,physical,prep,tech,published_at):
    state=deepcopy(prep['midnight_state']);cells=[];events=[];raw_events=physical['events']
    for event in raw_events:
        require(event['kind'] in ('startup','shutdown'),'supported transition')
        g=event['unit'];curve=case['curves'][g];at=stamp(event['at'])
        require(at==stamp(case['contract']['day_start'])+timedelta(minutes=5*event['index']) and event['curve_cells']==len(curve[event['kind']]),'event index/curve identity')
        if event['kind']=='startup':require(stamp(published_at)<=at-timedelta(hours=curve['notice_h']),'startup schedule lead unavailable')
        require(stamp(curve['available_at'])<=stamp(published_at),'curve declaration unavailable')
        events.append(dict(unit=g,kind=event['kind'],effective_at=event['at'],notification_at=published_at,available_at=published_at,curve_cells=event['curve_cells'],source_event_index=event['index'],notice_semantics='new DA simulated advance schedule, not real operator notification'))
    for j,raw in enumerate(physical['physical_cells'][:309]):
        clock=data['physical_lift']['cells'][j];require(raw['index']==j,'physical index')
        cell=dict(start=clock['start'],end=clock['end'],thermal={},batteries={},reserve_requirement_mw=deepcopy(data['physical_lift']['reserve'][j]))
        require(cell['reserve_requirement_mw']==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.),'original reserve unchanged')
        for g,z in raw['thermal'].items():
            phase=z['phase'];begin=state['thermal'][g]['power_mw'];step=z['curve_step'];length=None
            if phase in ('startup','shutdown'):
                cv=case['curves'][g][phase];length=len(cv);require(type(step)is int and 0<=step<length,'curve cursor')
                close(begin,cv[step]['begin'],'curve begin');close(z['mean_mw'],cv[step]['mean'],'curve mean');close(z['end_mw'],cv[step]['end'],'curve end')
                require(len([a for a in raw_events if a['unit']==g and a['kind']==phase and a['index']==j-step])==1,'transition event missing')
            else:require(step is None and phase in ('stable','off'),'stable/off cursor')
            cell['thermal'][g]=dict(phase=phase,mean_mw=z['mean_mw'],begin_mw=begin,end_mw=z['end_mw'],curve_step=step,curve_length=length,stable=int(phase=='stable'),connected=int(phase!='off'),age_begin_h=state['thermal'][g]['state_age_h'])
        for b,z in raw['battery'].items():
            p=z['permission'];require(set(p)=={'charge','idle','discharge'} and all(type(v)is int and v in (0,1)for v in p.values()) and sum(p.values())==1,'binary BESS permission')
            mode=next(k for k,v in p.items()if v);row=data['physical_lift']['resources'][b]['rows'][j];require(row['mode']==mode,'permission mismatch')
            cell['batteries'][b]=dict(mode=mode,power_mw=z['discharge_mw']-z['charge_mw'],charge_mw=z['charge_mw'],discharge_mw=z['discharge_mw'],selected_hour_direction_permission={'charge':-1,'idle':0,'discharge':1}[mode],energy_begin_mwh=state['batteries'][b]['energy_mwh'],energy_end_mwh=z['energy_mwh'])
        cell['renewable']=deepcopy(raw['renewable'])
        for g,z in raw['thermal'].items():
            for k in ('agc_up_reserved_mw','agc_down_reserved_mw'):cell['thermal'][g][k]=z[k]
        validate_physical_cell(cell,j,data,case)
        state=integrate_cell(state,cell,tech)
        for g,z in cell['thermal'].items():z.update(age_end_h=state['thermal'][g]['state_age_h'],residence_remaining_h=state['thermal'][g]['residence_remaining_h'])
        for b,z in cell['batteries'].items():
            close(z['energy_end_mwh'],state['batteries'][b]['energy_mwh'],'SOC reintegration')
            z.update(mode_age_end_h=state['batteries'][b]['mode_age_h'],hour_direction=state['batteries'][b]['hour_direction'],daily_used_internal_mwh=deepcopy(state['batteries'][b]['daily_used_internal_mwh']))
        cells.append(cell)
    require(len(cells)==309,'309 cells')
    for b,target in TARGETS.items():close(cells[287]['batteries'][b]['energy_end_mwh'],target,'D96 source target')
    return cells,state,events

def _inventory(case):
    candidates=[(p,h)for p,h in case['contract']['source_bindings'].items()if p.endswith('/ieee39_native_v4b/reports/sced/input.json')]
    require(len(candidates)==1,'explicit static inventory source');p,h=candidates[0];require(_sha(p)==h,'static inventory pin')
    inventory=deepcopy(_read(p)['storage_inventory'])
    require(inventory['pumped']==case['battery_contract']['storage']['pumped_storage']['inventory'],'pumped inventory changed')
    require(set(inventory['batteries'])==set(case['batteries']),'battery inventory')
    for b,u in case['batteries'].items():
        z=inventory['batteries'][b];close(z['initial_energy_mwh'],u['initial_energy_mwh'],'inventory initial SOC');close(z['final_energy_mwh'],u['terminal_energy_mwh'],'inventory D96')
        close(z['parameters']['ecap'],u['energy_capacity_mwh'],'inventory capacity');require(str(z['parameters']['bus'])==u['bus'] and z['reserve_credit_mw']==0,'inventory bus/reserve')
    return inventory,{str(Path(p).resolve()):h}
def _collect_files(value,files):
    if isinstance(value,dict):
        for v in value.values():_collect_files(v,files)
    elif isinstance(value,(list,tuple)):
        for v in value:_collect_files(v,files)
    elif isinstance(value,str):
        try:
            p=Path(value)
            if p.is_file():files[str(p.resolve())]=_sha(p)
        except (OSError,ValueError):pass

def load_contract(config_path,*,preparation_root=None,policy):
    require(policy==POLICY,'exact new R45 policy');require(preparation_root is None,'explicit preparation source in config')
    cfg=_read(config_path)
    from r45_entry import prepare_from_actual
    args={k:cfg[k]for k in ('price_prepared','price_review','physical_path','price_native_paths')}
    slices=prepare_from_actual(**args,window_count=4)
    from r44_bridge import case_digest
    case=loads(Path(cfg['case_path']).read_bytes());bridge=_read(Path(cfg['price_prepared'])/'base_bridge.json');data=deepcopy(_read(Path(cfg['price_prepared'])/'input.json')['base_DTO'])
    require(case_digest(case)==bridge['source_receipt']['case_sha256'],'actual new case binding')
    physical=_read(cfg['physical_path']);archived=_read(cfg['archived_preparation_publication']);prep,tech=_preparation(case,data,archived)
    publication=slices['physical_schedule_release'];pub=publication['available_at']
    cells,end,events=_physical_cells(case,data,physical,prep,tech,pub)
    inventory,static_pins=_inventory(case);data['storage_inventory']=inventory
    quote_binding=_attach_original_offers(case,data)
    refs=deepcopy(slices['references103'])
    for ref in refs:
        q=ref['chronological_q'];ids=ref['physical_indices']
        ref['source_available_at']=pub;ref['clock']='simulated research DA source availability'
        for r,z in ref['resources'].items():
            rows=[data['physical_lift']['resources'][r]['rows'][j]for j in ids]
            raw_ref=z['reference_mw'];reference,correction=normalize_reference_roundoff(raw_ref,z['min_mw'],z['max_mw'])
            if correction is not None:z['reference_roundoff']=dict(resource=r,quarter=q,**correction)
            z['reference_mw']=reference
            z['physical_rows_sha256']=digest(rows);z['reference_kind']=DA_KIND if q<96 else TAIL_KIND;z['published_DA_award']=z['reference_mw'] if q<96 else None
            z['official_Dplus1_DA_publication']=False
    pub96=dict(power_mw={r:[publication['rows'][q]['quantity_mwh'][r]/.25 for q in range(96)]for r in data['resources']},lmp_yuan_mwh=None,raw_DA_price98=slices['raw_DA_price98'],financial_price_available=False,official=False,execution_authority=False,source_DTO_sha256=bridge['DTO_sha256'])
    source,prior_pins=_forecast_source(case)
    require(all('prior:'+f in source['contract']['sources']for f in ('load_mw','wind_mw','solar_mw','boundary_tie_mw')),'frozen prior inventory')
    files={str(Path(config_path).resolve()):_sha(config_path),**static_pins,**prior_pins};_collect_files(cfg,files);_collect_files(slices['finalizer_arguments'],files)
    for v in slices['new_source_pins'].values():files[v['path']]=v['sha256']
    for name in ('input.json','template.json','base_bridge.json','PREPARATION.json','FINAL_PRICE_POLICY.json'):
        p=Path(cfg['price_prepared'])/name;files[str(p.resolve())]=_sha(p)
    prep_path=str(Path(cfg['archived_preparation_publication']).resolve())
    c=dict(schema=SCHEMA,original_quote_binding=quote_binding,policy=deepcopy(POLICY),source_bindings=files,preparation_source_bindings={prep_path:_sha(prep_path)},day_start=data['grid'][0]['start'],publication_at=pub,source_available_at=pub,publication_clock_is_simulated=True,availability_is_simulated=True,future_RT_used=False,publication96=pub96,representatives=[dict(market_index=q,physical_indices=deepcopy(data['physical_lift']['market_to_physical'][q]),grid=deepcopy(data['grid'][q]))for q in (96,97)],physical_cells=cells,references=refs,midnight_targets=deepcopy(TARGETS),initial_preparation=prep,technical=tech,archived_DA_terminal_state=end,coverage_end=cells[-1]['end'],events=events,scopes=deepcopy(SCOPES),new_native_calls=0,native_release_available=False,upstream_mode_evidence={'source':'new accepted SCUC/SCED permission onehots, not inferred from power','source_DTO_sha256':bridge['DTO_sha256']},meaning='new accepted R42 physical plan + R44 raw PRICE evidence; counterfactual DA-prefix local simulation, no financial price/grid authority')
    c['archived_midnight_preparation']=deepcopy(prep)
    seed=build_da_prefix_seed(c,data,case)
    c.update(start_index=44,seed_proof=seed,physical_schedule_release=deepcopy(publication),raw_DA_price98=slices['raw_DA_price98'],financial_DA_price=None,financial_price_available=False)
    c['initial_preparation']=dict(seed_state=seed['seed_state'],cells=seed['prefix_cells'],midnight_state=seed['projected_start_state'],historical_telemetry=False,initial_prefix_kind='counterfactual_new_DA_prefix_1045_to_1100_NOT_midnight')
    p=ContractPermit();_CONTRACTS[p]=dict(sha=digest(c),contract=deepcopy(c),source=source,data=data,source_sha=_source_digest(source),data_sha=digest(data),files=deepcopy(files))
    validate_contract(c,p,recheck_files=True);return c,p

def validate_contract(c,p,*,official_strict=False,recheck_files=False):
    require(type(p)is ContractPermit and p in _CONTRACTS,'fresh R45 permit required, no old permit')
    e=_CONTRACTS[p];require(digest(c)==e['sha'] and c['policy']==POLICY,'contract mutated')
    require(_source_digest(e['source'])==e['source_sha'] and digest(e['data'])==e['data_sha'],'prior/DTO mutated')
    require(not official_strict,'official declarations/mappings/qualifications incomplete')
    require(len(c['physical_cells'])==309 and len(c['references'])==103,'source coverage')
    if recheck_files:static_stage_checks.verify(p,e['files'])
    return True

@contextmanager
def native_stage_scope(c,p,*,run_id,index,stage):
    # This optimization never bypasses mutable-memory signatures.
    validate_contract(c,p)
    e=_CONTRACTS[p]
    with static_stage_checks.scope(p,e['files'],run_id=run_id,index=index,stage=stage) as receipt:
        try:
            yield receipt
        finally:
            validate_contract(c,p)


def template_window(c,p,index):
    validate_contract(c,p)
    from .gd_r15_two_window import project_issued_prefix
    spec=window_spec(c,index)
    if index==c['start_index']:
        prep=c['initial_preparation'];seed=prep['seed_state'];receipt=dict(at=seed['at'],available_at=seed['at'],state=deepcopy(seed),is_measurement=False)
        commands=[dict(id='R45-PURE-PREP-'+str(i),issued_at=seed['at'],available_at=seed['at'],cell=deepcopy(z))for i,z in enumerate(prep['cells'])]
        projection=project_issued_prefix(receipt,commands,cutoff=spec['cutoff'],start=spec['start'],technical=c['technical'])
    else:
        state=deepcopy(c['archived_midnight_preparation']['midnight_state'])
        for z in c['physical_cells'][:3*index]:state=integrate_cell(state,z,c['technical'])
        projection=dict(state=state,start=spec['start'],cutoff=spec['cutoff'],is_measurement=False,future_actual_used=False,origin='new_DA_pure_shape_not_live_receipt',native_acceptance=False)
    return {'projection':projection},None


def _forecast_source(case):
    # New case pins the original exogenous prior container; battery_contract only
    # holds battery/static inputs, not forecast vintages. No old DA result read.
    candidates=[(p,h)for p,h in case['contract']['source_bindings'].items()if p.endswith('/input/historical_da_only/source_reconstructed_day_inputs.json')]
    require(len(candidates)==1,'new case must bind original exogenous prior source')
    p,h=candidates[0];require(_sha(p)==h,'exogenous source pin changed')
    prior_source=loads(Path(p).read_bytes());source=deepcopy(case)
    source['contract']=deepcopy(case['battery_contract'])
    for family in ('load_mw','wind_mw','solar_mw','boundary_tie_mw'):
        key='prior:'+family;saved=prior_source['contract']['sources'][key]
        require(sha256(saved['artifact']).hexdigest()==saved['sha256'],'prior bytes changed')
        prior=json.loads(saved['artifact'])
        require(prior['uses_future_actual'] is False,'future actual prior forbidden')
        require(stamp(saved['available_at'])<=stamp(case['contract']['cutoff']),'prior not cutoff available')
        source['contract']['sources'][key]=deepcopy(saved)
    return source,{str(Path(p).resolve()):h}

def _attach_original_offers(case,data):
    """Read only immutable static bids from the source pinned by this new case.
    Never copy its SCED values, flags, results, permit, or clipped market segments.
    """
    candidates=[(p,h)for p,h in case['contract']['source_bindings'].items()if p.endswith('/ieee39_native_v4b/reports/sced/input.json')]
    require(len(candidates)==1,'single pinned static bid container')
    path,h=candidates[0];require(_sha(path)==h,'original static bid SHA changed')
    original=_read(path)['resources']
    require(set(data['resources'])==set(original)==set(case['energy_inputs']['thermal'])|set(case['batteries']),'original quote inventory')
    for r,u in data['resources'].items():
        if u['kind']=='battery':
            curves=deepcopy(original[r]['original_curves']);require(set(curves)=={'charge','discharge'},'complete BESS original curves')
            for mode in ('charge','discharge'):
                current=case['batteries'][r][mode+'_curve'];bp=current['breakpoints_mw'];prices=current['prices_yuan_per_mwh']
                require(len(curves[mode])==len(prices)==len(bp)-1,'BESS segment count')
                for k,z in enumerate(curves[mode]):
                    close(z['p_start'],bp[k],'BESS original start');close(z['p_end'],bp[k+1],'BESS original end');close(z['price'],prices[k],'BESS original price')
            u['original_curves']=curves
        else:
            offer=deepcopy(original[r]['original_offer']);current=case['energy_inputs']['thermal'][r];base=current['bid_base_mw']
            require(len(offer)==len(current['segments'])+1,'complete thermal base plus incremental quotes')
            close(offer[0]['p_start'],0,'base start');close(offer[0]['p_end'],base,'base MW')
            for v in current['base_bid_yuan_per_h'].values():close(v,base*offer[0]['price'],'full base energy offer differs')
            previous=base
            for z,segment in zip(offer[1:],current['segments']):
                close(z['p_start'],previous,'thermal segment start');close(z['p_end']-z['p_start'],segment['width_mw'],'thermal width')
                for price in segment['price_yuan_per_mwh'].values():close(z['price'],price,'thermal current source price')
                previous=z['p_end']
            u['original_offer']=offer
    return {'source_path':str(Path(path).resolve()),'source_sha256':h,'fields_only':['original_offer','original_curves'],'crosschecked_against_new_case':True,'old_dispatch_reused':False}

def validate_physical_cell(cell,j,data,case):
    import math
    x=data['physical_lift'];e=case['energy_inputs'];inj={}
    for b in data['nodes']:
        z=cell['renewable'][b];a=z['availability_mw'];p=z['dispatched_mw'];c=z['curtailed_mw']
        require(all(math.isfinite(k) for k in (a,p,c)) and min(a,p,c)>=-1e-6 and max(p,c)<=a+1e-6,'physical VRE domain')
        close(p+c,a,'VRE conservation');close(a,e['renewable_mw'][b,j],'original availability')
        for key,value in [('availability',a),('dispatched',p),('curtailed',c)]:close(value,x['renewable_'+key+'_mw'][b][j],'DTO VRE identity')
        require(z['reserve_credit_mw']==z['AGC_credit_mw']==0,'zero VRE service credit')
        close(x['fixed_injection_mw'][b][j],p+e['physical_tie_mw'][b,j],'SCED P not A')
        inj[b]=p+e['physical_tie_mw'][b,j]-e['load_mw'][b,j]
        inj[b]+=math.fsum(z['mean_mw'] for r,z in cell['thermal'].items() if data['resources'][r]['node']==b)
        inj[b]+=math.fsum(z['power_mw'] for r,z in cell['batteries'].items() if data['resources'][r]['node']==b)
    require(abs(math.fsum(inj.values()))<=1e-6,'source physical nodal balance')
    for lid,line in data['network'].items():
        f=math.fsum(line['ptdf'][b]*inj[b] for b in data['nodes'])
        require(x['network'][lid]['lower_mw'][j]-1e-6<=f<=x['network'][lid]['upper_mw'][j]+1e-6,'source physical PTDF limit')
    for g,z in cell['thermal'].items():
        row=x['resources'][g]['rows'][j];award=10. if j<288 else 0.
        require(z['agc_up_reserved_mw']==z['agc_down_reserved_mw']==row['agc_up_reserved_mw']==row['agc_down_reserved_mw']==award,'AGC source identity')
        if z['phase']=='stable':
            close(row['min_mw'],e['thermal'][g]['min_mw'][j]+award,'AGC lower deducted once');close(row['max_mw'],e['thermal'][g]['max_mw'][j]-award,'AGC upper deducted once')
    return dict(index=j,node_injection_mw=inj,global_residual_mw=math.fsum(inj.values()))

def build_da_prefix_seed(plan,data,case):
    state=deepcopy(plan['initial_preparation']['midnight_state']);proof=[]
    for j,c in enumerate(plan['physical_cells'][:129]):
        proof.append(validate_physical_cell(c,j,data,case));state=integrate_cell(state,c,plan['technical'])
    spec=window_spec(plan,44);require(state['at']==spec['cutoff'],'noon seed cutoff')
    prefix=deepcopy(plan['physical_cells'][129:132]);projected=deepcopy(state)
    for j,c in enumerate(prefix,129):proof.append(validate_physical_cell(c,j,data,case));projected=integrate_cell(projected,c,plan['technical'])
    require(projected['at']==spec['start'],'noon seed start')
    return dict(schema='gd.research.r45.DA_prefix_seed.v1',start_index=44,seed_state=state,prefix_cells=prefix,projected_start_state=projected,replayed_DA_prefix_cells=129,replay_source_sha256=digest(plan['physical_cells'][:129]),initial_preparation_sha256=digest(plan['initial_preparation']),electrical_proof=proof,state_basis='counterfactual_execution_of_new_accepted_DA_prefix',prior_rolling_RT_windows=0,actual_metered_cells=0,creates_historical_RT_capability=False)


def normalize_reference_roundoff(raw,lo,hi):
    import math
    require(all(math.isfinite(x) for x in (raw,lo,hi)) and lo<=hi,'finite reference bounds')
    ref=min(hi,max(lo,raw));delta=ref-raw
    require(abs(delta)<=1e-9,'reference outside physical bounds beyond numerical budget')
    return ref,(dict(raw_mw=raw,reference_mw=ref,delta_mw=delta,physical_lower_mw=lo,physical_upper_mw=hi,tolerance_mw=1e-9,scope='PRICE reference only; raw SCED power/primal unchanged') if delta else None)
