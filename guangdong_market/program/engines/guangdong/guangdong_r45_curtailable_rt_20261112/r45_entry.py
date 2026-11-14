"""New accepted-DA source assembly and four-window plan, zero native.
No R15/R21 capability can authenticate this new source. No old RT result is read.
"""
import json,hashlib,sys,math
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parent.parent
SRC=ROOT/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src'
sys.path.insert(0,str(SRC))
from smm.gd_r17_midnight_contract import window_spec,quarter_eligibility,TARGETS

def require(ok,msg):
    if not ok:raise ValueError(msg)
def read(p):return json.loads(Path(p).read_text())
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def pin(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))
def close(a,b):require(math.isfinite(a) and math.isfinite(b) and abs(a-b)<=1e-6,'numeric source mismatch')

def validate_publication(release,bridge,physical):
    require(release['physical_schedule_release'] is True and release['raw_PRICE_verified'] is True and release['financial_price_available'] is False,'physical release, not financial price')
    require(release['research_publication_allowed'] is False and release['execution_authority'] is False,'no financial or grid authorization')
    require(bridge['source_receipt']['SCUC_accepted'] is True and bridge['source_receipt']['diagnostic_only'] is False,'accepted physical branch')
    require(bridge['DTO_sha256']==digest(bridge['DTO'])==release['source_DTO_sha256'],'DTO identity')
    require(bridge['source_receipt']['physical_report_sha256']==digest(physical)==release['source_physical_sha256'],'physical binding')
    require(len(physical['physical_cells'])==576 and len(release['rows'])==96,'physical coverage')
    return True

def assemble_slices(publication,bridge,physical,*,window_count,start_index=44):
    """Data assembly only, not live receipt/session minting; validator not native authority."""
    validate_publication(publication,bridge,physical)
    require((window_count,start_index)==(4,44),'explicit first4 or full96 scope only')
    d=bridge['DTO'];lift=d['physical_lift'];day=d['grid'][0]['start']
    require(d['delta']==.1,'original research delta unchanged')
    require(len(lift['cells'])==576,'physical lift coverage')
    cells=physical['physical_cells'];resources=d['resources'];refs=[]
    for j in range(309):
        require(lift['reserve'][j]==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.),'original reserve400/400 unchanged')
        require(cells[j]['index']==j,'physical index')
    for b,target in TARGETS.items():close(cells[287]['battery'][b]['energy_mwh'],target)
    for q in range(103):
        ids=list(range(3*q,3*q+3));rr={}
        for r,u in resources.items():
            rows=[lift['resources'][r]['rows'][j] for j in ids]
            if u['kind']=='battery':
                perms=[cells[j]['battery'][r]['permission'] for j in ids]
                for p in perms:require(all(type(v) is int and v in (0,1) for v in p.values()) and sum(p.values())==1,'binary BESS permission, not inferred direction')
                mode=next(iter({z['mode'] for z in rows}-{'idle'}),'idle')
                permission={'charge':-1,'idle':0,'discharge':1}[mode]
                values=[cells[j]['battery'][r]['discharge_mw']-cells[j]['battery'][r]['charge_mw'] for j in ids]
            else:permission=None;values=[cells[j]['thermal'][r]['mean_mw'] for j in ids]
            qual=quarter_eligibility(rows,u['kind'],permission)
            mean=math.fsum(values)/3
            if q<96:
                pub=publication['rows'][q]
                require(pub['index']==q and pub['start']==d['grid'][q]['start'] and pub['end']==d['grid'][q]['end'],'96 publication chronology')
                close(pub['quantity_mwh'][r],mean*.25)
            rr[r]=dict(**qual,reference_mw=mean,node=u['node'],source_market_index=q if q<96 else None,
                       published_DA_quantity_mwh=publication['rows'][q]['quantity_mwh'][r] if q<96 else None,
                       reference_kind='new_R42_physical_schedule96_NOT_financial_publication' if q<96 else 'new_SCED_chronological_tail_NOT_published_DA')
        refs.append(dict(chronological_q=q,physical_indices=ids,start=lift['cells'][ids[0]]['start'],end=lift['cells'][ids[-1]]['end'],resources=rr))
    windows=[]
    for i in range(start_index,start_index+window_count):
        w=window_spec({'day_start':day},i)
        require(datetime.fromisoformat(publication['available_at'])<=datetime.fromisoformat(w['cutoff']),'publication future to cutoff')
        windows.append(dict(window=w,da_reference_cells=deepcopy(cells[3*i:3*i+24]),references=deepcopy(refs[i:i+8]),
                            resource_rows={r:deepcopy(lift['resources'][r]['rows'][3*i:3*i+24]) for r in resources},
                            actual_initial_state=None,live_receipt_required=True,
                            PRICE_terminal_targets=dated_price_targets(w,day),
                            non_day_end_PRICE_terminal='endogenous research; no rolling terminal equalities',
                            RT_terminal_policy='retain original R21 physical SCED endpoint, no feasibility repair'))
    return dict(schema='gd.research.r45.source_windows.v1',windows=windows,source_DTO_sha256=bridge['DTO_sha256'],
                publication96=deepcopy(publication['rows']),references103=refs,midnight_targets=deepcopy(TARGETS),
                simulated_publication_at=publication['available_at'],actual_preparation_utc=datetime.now(timezone.utc).isoformat(),
                accepted_RT_results=[],native_calls=0,execution_authority=False,formal_bill=None,
                runner_authorization=False,source_only_not_live_state=True)

def normalize_record_paths(value):
    if isinstance(value,dict):
        return {k:(str(Path(v).resolve()) if k=='path' and isinstance(v,str) else normalize_record_paths(v)) for k,v in value.items()}
    if isinstance(value,list):return [normalize_record_paths(v) for v in value]
    return value

def dated_price_targets(window,day_start):
    """R22 corrected semantics: include exact timestamp anchor, including local k=24."""
    day=datetime.fromisoformat(day_start);start=datetime.fromisoformat(window['start']);end=datetime.fromisoformat(window['horizon_end'])
    target_at=day+timedelta(days=1)
    if not start<=target_at<=end:return []
    k=(target_at-start).total_seconds()/300
    require(k.is_integer(),'target must align to physical 5min boundary')
    return [dict(resource=b,local_energy_index=int(k),at=target_at.isoformat(),energy_mwh=v) for b,v in TARGETS.items()]

def prepare_from_actual(*,price_prepared,price_review,physical_path,price_native_paths,window_count=4,start_index=44):
    import importlib.util
    require((start_index,window_count)==(44,4),'bounded separate noon experiment')
    p=Path(price_prepared).resolve();r=Path(price_review).resolve();r44=ROOT/'guangdong_r44_curtailable_da_price_bridge_20261112'
    require(sha(p/'PREPARATION.json')=='a058436f22a0a51ea3ac706d5c806182b5e6d00381f3eeffe3bd6b298dfa7b0b','final prepared source')
    require(sha(r/'PRICE_REVIEW.json')=='387f034083a44b978ecb9661b10669851e5988ce9119a90375fce66c0715f478','actual raw PRICE review')
    src=r44/'prepare.py';require(sha(src)=='a391b203d6cb72de0c53978e6ff9548517f6d4235e30c7feb1f83c480b80e8cf','pinned source verifier')
    meta=read(p/'PREPARATION.json');pins={}
    for f,h in meta['artifact_hashes'].items():require(sha(p/f)==h,'artifact changed');pins[str(p/f)]=h
    for group in ('source_pins','source_code_pins'):
        for path,h in meta[group].items():require(sha(path)==h,'bound source changed');pins[path]=h
    sys.path.insert(0,str(r44));spec=importlib.util.spec_from_file_location('_r45_pinned_r44_source',src);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    case,physical,values,receipt,source_spec,source_pins=mod.source()
    bridge=read(p/'base_bridge.json');wrapper=read(p/'input.json');d=wrapper['base_DTO'];policy=read(p/'FINAL_PRICE_POLICY.json')
    require(digest(d)==bridge['DTO_sha256']==meta['base_DTO_sha256'] and digest(wrapper['eligible_VRE'])==meta['eligible_VRE_sha256'],'DTO/VRE binding')
    require(policy['final_policy']=='eligible_noquote_VRE_bid0_proxy_with_explicit_curtailment' and policy['same_R42_bid_proxy'] is True,'explicit new price policy')
    require(read(physical_path)==physical and sha(physical_path)==receipt['physical_report_sha256'],'same physical source')
    for key in ('run_id','scuc_run_id','case_sha256','physical_report_sha256','expanded_values_sha256','SCUC_request_sha256','SCUC_response_sha256','SCED_request_sha256','SCED_response_sha256','scenario_model_sha256','actual_AGC_selection_sha256'):
        require(bridge['source_receipt'][key]==receipt[key],'source receipt mismatch '+key)
    event=mod.load_event(price_native_paths);report=read(r/'PRICE_REVIEW.json');raw=read(r/'RAW_UNCLIPPED_LMPS98.json')
    require(event['event_id'] not in (receipt['run_id'],receipt['scuc_run_id']),'independent PRICE')
    require({k:v for k,v in event['request'].items() if k!='deadline'}==read(p/'template.json'),'exact request')
    for role,z in event['pins'].items():require(z['sha256']==report['event_pins'][role]['sha256'],'same event');pins[z['path']]=z['sha256']
    rr=event['response'];require(rr['status']=='optimal' and rr['native_status']=='Optimal' and rr['has_primal'] is True and rr['dual_valid'] is True,'actual PRICE optimal')
    require(report['status']=='pass_new_eligible_VRE_PRICE' and report['source_input_sha256']==sha(p/'input.json') and report['raw_unclipped_LMP_sha256']==sha(r/'RAW_UNCLIPPED_LMPS98.json'),'review identity')
    require(report['market_price'] is None and report['settlement_price'] is None and report['research_publication_allowed'] is False and raw['market_price'] is None and raw['settlement_price'] is None,'raw prices cannot authorize settlement')
    prices=raw['raw_unclipped_LMP'];require(set(prices)==set(d['nodes']) and all(len(v)==98 and all(math.isfinite(a) for a in v) for v in prices.values()),'raw price coverage')
    rows=[]
    for q,g in enumerate(d['grid'][:96]):
        quantities={}
        for uid,u in d['resources'].items():
            quantities[uid]=math.fsum(physical['physical_cells'][j]['thermal'][uid]['mean_mw'] if u['kind']!='battery' else physical['physical_cells'][j]['battery'][uid]['discharge_mw']-physical['physical_cells'][j]['battery'][uid]['charge_mw'] for j in range(3*q,3*q+3))/12
        renewable={b:dict(availability_mwh=math.fsum(physical['physical_cells'][j]['renewable'][b]['availability_mw'] for j in range(3*q,3*q+3))/12,dispatched_mwh=math.fsum(physical['physical_cells'][j]['renewable'][b]['dispatched_mw'] for j in range(3*q,3*q+3))/12,curtailed_mwh=math.fsum(physical['physical_cells'][j]['renewable'][b]['curtailed_mw'] for j in range(3*q,3*q+3))/12,financial_DA_participation='unknown',financial_DA_entitlement=False) for b in wrapper['eligible_VRE']['nodes']}
        rows.append(dict(index=q,start=g['start'],end=g['end'],quantity_mwh=quantities,renewable=renewable,prices=None))
    release=dict(schema='gd.research.r45.physical_schedule_release.v1',rows=rows,available_at=d['planned_publication_at'],source_DTO_sha256=digest(d),source_SCED_event_id=receipt['run_id'],source_physical_sha256=sha(physical_path),physical_schedule_release=True,raw_PRICE_verified=True,financial_price_available=False,research_publication_allowed=False,formal_publication_allowed=False,execution_authority=False)
    result=assemble_slices(release,bridge,physical,window_count=window_count,start_index=start_index)
    for ref in result['references103']:
        ids=ref['physical_indices'];ref['renewable']={b:dict(SCED_reference_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['dispatched_mw'] for j in ids)/3,availability_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['availability_mw'] for j in ids)/3,curtailed_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['curtailed_mw'] for j in ids)/3,physical_indices=ids,price_eligible=True,eligibility_basis='explicit research; no real financial qualification') for b in wrapper['eligible_VRE']['nodes']}
    for w in result['windows']:i=w['window']['index'];w['references']=deepcopy(result['references103'][i:i+8])
    pins.update(source_pins)
    for path in (p/'PREPARATION.json',r/'PRICE_REVIEW.json',r/'RAW_UNCLIPPED_LMPS98.json',Path(physical_path)):pins[str(path)]=sha(path)
    result.update(start_index=start_index,physical_schedule_release=release,raw_DA_price98=prices,financial_DA_price=None,actual_new_source_rechecked=True,new_source_pins={str(i):dict(path=str(Path(f).resolve()),sha256=h) for i,(f,h) in enumerate(sorted(pins.items()))},finalizer_arguments=dict(price_prepared=str(p),price_review=str(r),physical_path=str(physical_path),price_native_paths=price_native_paths,window_count=window_count,start_index=start_index))
    return result
