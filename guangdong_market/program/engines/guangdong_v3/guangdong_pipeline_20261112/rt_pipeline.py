"""Fresh DA receipt -> fixed-DA RT preparation. No old DA/live result fallback.
Pure constructors and recovery journal; native execution is caller-explicit.
"""
from pathlib import Path
from copy import deepcopy
import json,hashlib,math,sys
from datetime import datetime
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent

def need(ok,message):
 if not ok:raise ValueError(message)
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def write(p,x):
 with Path(p).open('xb')as f:f.write(canonical(x))
def checked_ref(run,ref):
 need(isinstance(ref,dict)and set(ref)>={'path','sha256'},'file reference required')
 relative=Path(ref['path']);need(not relative.is_absolute()and '..'not in relative.parts,'run-relative artifact required')
 p=(Path(run).resolve()/relative).resolve();need(p.is_relative_to(Path(run).resolve()),'artifact escapes run')
 need(p.is_file()and sha(p)==ref['sha256'],'artifact missing or changed: '+str(relative));return p

def accepted_da(run):
 root=Path(run).resolve();a=read(root/'ARTIFACTS.json')
 need(a.get('schema')=='gd.pipeline.da_artifacts.v1'and a.get('status')=='accepted','fresh all-three-stage accepted DA receipt required')
 for k in ('run_id','input_identity','source_identity'):need(bool(a.get(k)),'missing DA '+k)
 refs={}
 for group,values in [('inputs',a['inputs'])]+[(name,z)for name,z in a['stages'].items()]:
  for key,ref in values.items():
   if isinstance(ref,dict)and 'path'in ref:refs[group+'.'+key]=checked_ref(root,ref)
 required=['inputs.case','inputs.grid','inputs.policy','inputs.agc','inputs.pricing_spec']
 required+=['da_scuc.'+x for x in ('acceptance','request','response','full_primal')]
 required+=['da_sced.'+x for x in ('physical_solution','expanded_response','full_names','acceptance','request','response')]
 required+=['da_price.'+x for x in ('input','base_bridge','raw_prices','template','names','acceptance','request','response')]
 need(all(k in refs for k in required),'incomplete fresh DA artifact inventory')
 for stage in ('da_scuc','da_sced','da_price'):
  r=read(refs[stage+'.response']);need(r.get('native_status')=='Optimal'and r.get('status')=='optimal'and r.get('has_primal')is True,'DA native stage not Optimal '+stage)
  accept=read(refs[stage+'.acceptance']);need(accept.get('status')=='accepted'and accept.get('stage')==stage and all(accept.get(k)==a[k]for k in ('run_id','input_identity','source_identity')),'stage acceptance not bound to current run')
  for key in ('request','response'):need(accept['artifacts'][key]==a['stages'][stage][key],'accepted native artifact binding changed')
 from run_protocol import verify_sources
 verify_sources(root)
 return a,refs

def dependencies():
 sys.path[:0]=[str(ROOT/'guangdong_r67_complete_tail_20261112'),str(ROOT/'guangdong_r62_same_source_da_rt_20261112'),str(ROOT/'guangdong_r46_curtailable_full_day_20261112'),str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'),str(ROOT/'guangdong_r53_same_source_da_chain_20261112')]
 import common67,full_input,full_model,cache,runtime67
 from r46_entry import assemble_slices
 return common67,full_input,full_model,cache,runtime67,assemble_slices

def release_from_da(run):
 """R62 publication/slices arithmetic; receipt paths replace its fixed R53 root."""
 a,refs=accepted_da(run);_,_,_,_,_,assemble=dependencies()
 wrapper=read(refs['da_price.input']);bridge=read(refs['da_price.base_bridge']);physical=read(refs['da_sced.physical_solution']);d=wrapper['base_DTO'];policy=wrapper['same_bid_policy']
 need(policy['noquote_energy_bid']['yuan_per_mwh']==-50 and policy['curtailment_penalty']['yuan_per_mwh']==1000,'supported preserved bid policy')
 need(digest(d)==bridge['DTO_sha256'],'fresh DTO/bridge mismatch')
 need(bridge['source_receipt']['physical_report_sha256']==digest(physical),'fresh physical report/bridge mismatch')
 raw=read(refs['da_price.raw_prices']);prices=raw.get('raw_unclipped_LMP',raw)
 need(len(prices)==len(d['nodes'])if 'nodes'in d else len(prices)==39,'complete raw DA nodes')
 need(all(len(v)==98 for v in prices.values()),'98 raw prices required')
 rows=[]
 for q,g in enumerate(d['grid'][:96]):
  qty={uid:math.fsum(physical['physical_cells'][j]['thermal'][uid]['mean_mw']if u['kind']!='battery'else physical['physical_cells'][j]['battery'][uid]['discharge_mw']-physical['physical_cells'][j]['battery'][uid]['charge_mw']for j in range(3*q,3*q+3))/12 for uid,u in d['resources'].items()}
  rows.append(dict(index=q,start=g['start'],end=g['end'],quantity_mwh=qty))
 release=dict(schema='gd.pipeline.rt.physical_release.v1',rows=rows,available_at=d['planned_publication_at'],source_DTO_sha256=digest(d),source_SCED_event_id=a['run_id']+':da_sced',source_physical_sha256=digest(physical),physical_schedule_release=True,raw_PRICE_verified=True,financial_price_available=False,research_publication_allowed=False,formal_publication_allowed=False,execution_authority=False)
 slices=assemble(release,bridge,physical,window_count=96,start_index=0)
 for ref in slices['references103']:
  ids=ref['physical_indices'];ref['renewable']={b:dict(SCED_reference_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['dispatched_mw']for j in ids)/3,availability_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['availability_mw']for j in ids)/3,curtailed_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['curtailed_mw']for j in ids)/3,physical_indices=ids,price_eligible=True,eligibility_basis='explicit research; no financial entitlement')for b in wrapper['eligible_VRE']['nodes']}
 return dict(da=a,refs=refs,wrapper=wrapper,bridge=bridge,physical=physical,release=release,slices=slices,raw_prices=prices)

class OccurrenceJournal:
 """On-disk exactly-once boundary: never relaunch an attempted unknown window."""
 def __init__(self,root,run_id,da_sha):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.binding=dict(schema='gd.pipeline.rt.journal.v1',run_id=run_id,da_receipt_sha256=da_sha)
  p=self.root/'BINDING.json'
  if p.exists():need(read(p)==self.binding,'different DA/run journal')
  else:write(p,self.binding)
 def begin(self,index,prepared_ref):
  need(type(index)is int and 0<=index<96,'window0..95');d=self.root/f'W{index}';d.mkdir(exist_ok=True)
  need(not(d/'ATTEMPT.json').exists(),'already attempted: inspect existing evidence, never automatic relaunch')
  if index:need((self.root/f'W{index-1}'/'ACCEPTED.json').exists(),'previous acceptance missing')
  write(d/'ATTEMPT.json',dict(window=index,prepared=prepared_ref,**self.binding));return d
 def accept(self,index,evidence):
  d=self.root/f'W{index}';need((d/'ATTEMPT.json').exists(),'no attempted occurrence');need(not(d/'ACCEPTED.json').exists(),'already accepted')
  for ref in evidence.values():checked_ref(self.root,ref)
  write(d/'ACCEPTED.json',dict(window=index,evidence=evidence,attempt_sha256=sha(d/'ATTEMPT.json'),**self.binding))
 def recovery(self):
  accepted=[]
  for i in range(96):
   d=self.root/f'W{i}'
   if(d/'ACCEPTED.json').exists():
    a=read(d/'ACCEPTED.json');need(a['run_id']==self.binding['run_id']and a['da_receipt_sha256']==self.binding['da_receipt_sha256']and a['attempt_sha256']==sha(d/'ATTEMPT.json'),'journal changed')
    for ref in a['evidence'].values():checked_ref(self.root,ref)
    accepted.append(i);continue
   need(not any((self.root/f'W{j}'/'ATTEMPT.json').exists()for j in range(i+1,96)),'noncontiguous future journal occurrence')
   return dict(accepted=accepted,next_index=i,state='pending_uncertain_native'if(d/'ATTEMPT.json').exists()else'ready_unattempted',automatic_retry=False)
  return dict(accepted=accepted,next_index=96,state='all_accepted_drain_required',automatic_retry=False)

def contract_from_da(run):
 """Mint fresh source permit from this run's accepted facts and explicit input assets."""
 x=release_from_da(run);a,refs=x['da'],x['refs'];common,*_=dependencies();source=common.source
 from r62smm.gd_october_job_codec import loads
 from r62smm.minimal_source import make_view,validate_view
 need('inputs.rt_assets'in refs,'initial input identity must include explicit rt_assets')
 am=read(refs['inputs.rt_assets']);need(am['schema']=='gd.pipeline.rt_assets.v1','rt assets schema')
 assets={k:checked_ref(refs['inputs.rt_assets'].parent,v)for k,v in am['files'].items()}
 case=loads(refs['inputs.case'].read_bytes());data=deepcopy(x['wrapper']['base_DTO']);physical=x['physical']
 need(am['day_start']==case['contract']['day_start'],'input asset day mismatch')
 archived=read(assets['preparation']);prep,tech=source._preparation(case,data,archived)
 pub=x['release']['available_at'];cells,end,events=source._physical_cells(case,data,physical,prep,tech,pub)
 static=read(assets['static']);inventory=deepcopy(static['storage_inventory']);need(set(inventory['batteries'])==set(case['batteries']),'storage inventory')
 for b,u in case['batteries'].items():
  z=inventory['batteries'][b]
  for left,right in [('initial_energy_mwh','initial_energy_mwh'),('final_energy_mwh','terminal_energy_mwh')]:source.close(z[left],u[right],'inventory SOC')
  source.close(z['parameters']['ecap'],u['energy_capacity_mwh'],'inventory capacity');need(str(z['parameters']['bus'])==u['bus']and z['reserve_credit_mw']==0,'inventory bus/reserve')
 need(inventory['pumped']==case['battery_contract']['storage']['pumped_storage']['inventory'],'pumped inventory')
 data['storage_inventory']=inventory;original=static['resources'];need(set(original)==set(data['resources']),'original quote inventory')
 for r,u in data['resources'].items():
  if u['kind']=='battery':
   curves=deepcopy(original[r]['original_curves']);need(set(curves)=={'charge','discharge'},'BESS curves')
   for mode in curves:
    c=case['batteries'][r][mode+'_curve'];need(len(curves[mode])==len(c['prices_yuan_per_mwh'])==len(c['breakpoints_mw'])-1,'curve dimensions')
    for k,z in enumerate(curves[mode]):
     for left,value in [('p_start',c['breakpoints_mw'][k]),('p_end',c['breakpoints_mw'][k+1]),('price',c['prices_yuan_per_mwh'][k])]:source.close(z[left],value,'original quote')
   u['original_curves']=curves
  else:
   offer=deepcopy(original[r]['original_offer']);cur=case['energy_inputs']['thermal'][r];need(len(offer)==len(cur['segments'])+1,'full thermal offer')
   source.close(offer[0]['p_start'],0,'base start');source.close(offer[0]['p_end'],cur['bid_base_mw'],'base end')
   for v in cur['base_bid_yuan_per_h'].values():source.close(v,cur['bid_base_mw']*offer[0]['price'],'base quote')
   prev=cur['bid_base_mw']
   for z,seg in zip(offer[1:],cur['segments']):
    source.close(z['p_start'],prev,'offer start');source.close(z['p_end']-z['p_start'],seg['width_mw'],'offer width')
    for price in seg['price_yuan_per_mwh'].values():source.close(z['price'],price,'offer price')
    prev=z['p_end']
   u['original_offer']=offer
 view=make_view(case);prior=loads(assets['priors'].read_bytes())
 for f in ('load_mw','wind_mw','solar_mw','boundary_tie_mw'):
  key='prior:'+f;saved=prior['contract']['sources'][key];need(hashlib.sha256(saved['artifact']).hexdigest()==saved['sha256'],'prior bytes')
  payload=json.loads(saved['artifact']);need(payload['uses_future_actual']is False and datetime.fromisoformat(saved['available_at'])<=datetime.fromisoformat(case['contract']['cutoff']),'prior causality')
  view['contract']['sources'][key]=deepcopy(saved)
 validate_view(view);references=deepcopy(x['slices']['references103'])
 for ref in references:
  q=ref['chronological_q'];ids=ref['physical_indices'];ref.update(source_available_at=pub,clock='fresh simulated research DA publication')
  for r,z in ref['resources'].items():
   rows=[data['physical_lift']['resources'][r]['rows'][j]for j in ids];v,fix=source.normalize_reference_roundoff(z['reference_mw'],z['min_mw'],z['max_mw']);z['reference_mw']=v
   if fix is not None:z['reference_roundoff']=dict(resource=r,quarter=q,**fix)
   z.update(physical_rows_sha256=digest(rows),reference_kind=source.DA_KIND if q<96 else source.TAIL_KIND,published_DA_award=v if q<96 else None,official_Dplus1_DA_publication=False)
 files={str(p):sha(p)for p in list(refs.values())+list(assets.values())+[Path(run).resolve()/'ARTIFACTS.json',Path(__file__).resolve()]}
 pub96=dict(power_mw={r:[x['release']['rows'][q]['quantity_mwh'][r]/.25 for q in range(96)]for r in data['resources']},lmp_yuan_mwh=None,raw_DA_price98=x['raw_prices'],financial_price_available=False,official=False,execution_authority=False,source_DTO_sha256=x['bridge']['DTO_sha256'])
 c=dict(schema=source.SCHEMA,original_quote_binding=dict(source_path=str(assets['static']),source_sha256=sha(assets['static']),fields_only=['original_offer','original_curves'],crosschecked_against_new_case=True,old_dispatch_reused=False),policy=deepcopy(source.POLICY),source_bindings=files,preparation_source_bindings={str(assets['preparation']):sha(assets['preparation'])},day_start=data['grid'][0]['start'],publication_at=pub,source_available_at=pub,publication_clock_is_simulated=True,availability_is_simulated=True,future_RT_used=False,publication96=pub96,representatives=[dict(market_index=q,physical_indices=deepcopy(data['physical_lift']['market_to_physical'][q]),grid=deepcopy(data['grid'][q]))for q in (96,97)],physical_cells=cells,references=references,midnight_targets=deepcopy(source.TARGETS),initial_preparation=prep,technical=tech,archived_DA_terminal_state=end,coverage_end=cells[-1]['end'],events=events,scopes=deepcopy(source.SCOPES),new_native_calls=0,native_release_available=False,upstream_mode_evidence=dict(source='this run accepted DA SCUC/SCED onehots',source_DTO_sha256=x['bridge']['DTO_sha256']),meaning='fresh accepted pipeline DA; fixedDA research RT; no R80 or financial publication',archived_midnight_preparation=deepcopy(prep))
 seed=source.build_midnight_seed(c);c.update(start_index=0,seed_proof=seed,physical_schedule_release=deepcopy(x['release']),raw_DA_price98=x['raw_prices'],financial_DA_price=None,financial_price_available=False,DA_pipeline_run_id=a['run_id'],DA_receipt_sha256=sha(Path(run)/'ARTIFACTS.json'))
 c['initial_preparation']=dict(seed_state=seed['seed_state'],cells=seed['prefix_cells'],midnight_state=seed['projected_start_state'],historical_telemetry=False,initial_prefix_kind='explicit_input_reverified_midnight_preparation')
 permit=source.ContractPermit();source._CONTRACTS[permit]=dict(sha=digest(c),contract=deepcopy(c),source=view,data=data,source_sha=source._source_digest(view),data_sha=digest(data),files=deepcopy(files));source.validate_contract(c,permit,recheck_files=True)
 return c,permit,x

from collections import Counter
def construct_fresh_witness(inp,q,n,refs):
 pp=refs['da_sced.physical_solution'];ep=refs['da_sced.expanded_response'];np=refs['da_sced.full_names']
 physical=read(pp);expanded=read(ep);on=read(np);ov=dict(zip([z.replace("'",'')for z in on['variables']],expanded['primal']))
 need(len(ov)==len(expanded['primal'])==len(on['variables']),'fresh expanded SCED dimension')
 assert len(inp['da_cells'])==309 and inp['physical_horizon_cells']==309 and not inp['future_actual_used']
 values={};methods=Counter();max_segment_remainder=0.;segment_adjustments=[]
 for resource,u in inp['resources'].items():
  battery=u['kind']=='battery'
  for j in range(309):
   cell=physical['physical_cells'][j];assert cell['start']==inp['da_cells'][j]['start'] and cell['end']==inp['da_cells'][j]['end']
   z=cell['battery'if battery else'thermal'][resource];P=z['discharge_mw']-z['charge_mw']if battery else z['mean_mw'];values[f'power[{resource},{j}]']=P
   if battery:
    assert inp['resources'][resource]['rows'][j]['mode']==next(k for k,v in z['permission'].items()if v)
    values[f'energy[{resource},{j+1}]']=z['energy_mwh']
    mode=u['rows'][j]['mode'];curve=u['original_curves']['charge'if mode=='charge'else'discharge'];sign=-1 if mode=='charge'else 1
   else:
    assert u['rows'][j]['phase']==z['phase'];curve=u['original_offer'];sign=1
    accident=ov[f'contingency[{resource},{j}]']
    # Independent positive and accident services use the same feasible thermal headroom.
    # This does not credit storage/VRE or impose800MW of simultaneous service.
    for service in ('positive','accident'):values[f'reserve[{resource},{j},{service}]']=accident
    for service in ('primary','negative'):values[f'reserve[{resource},{j},{service}]']=0.
   left=sign*P
   for k,s in enumerate(curve):
    width=s['p_end']-s['p_start'];v=min(max(left,0.),width);values[f'segment[{resource},{j},{k}]']=v;left-=v
   max_segment_remainder=max(max_segment_remainder,abs(left))
   if left:segment_adjustments.append(dict(resource=resource,index=j,unrepresented_signed_power_mw=left,raw_physical_power_unchanged=True))
   assert abs(left)<=1e-6,(resource,j,left)
  if battery:values[f'energy[{resource},0]']=inp['projection']['state']['batteries'][resource]['energy_mwh']
 for b in inp['renewable']:
  for j in range(309):
   z=physical['physical_cells'][j]['renewable'][b];assert abs(inp['renewable'][b]['availability_mw'][j]-z['availability_mw'])<=1e-9
   values[f'vre_power[{b},{j}]']=z['dispatched_mw'];values[f'vre_curtailment[{b},{j}]']=z['curtailed_mw']
 values['zero']=0.;x=[]
 for name,col in zip(n['variables'],q['variables']):
  name=name.replace("'",'');assert name in values,('unmapped column',name)
  x.append(float(values[name]));methods[name.split('[')[0]]+=1
 assert len(x)==len(q['variables'])==len(n['variables']) and all(math.isfinite(v)for v in x)
 return x,dict(mapping_counts=dict(methods),source_pins={str(p):sha(p)for p in (pp,ep,np)},max_auxiliary_segment_roundoff_mw=max_segment_remainder,segment_roundoff_count=len(segment_adjustments),segment_roundoff=segment_adjustments,physical_P_SOC_VRE_unmodified=True,primary_and_negative_zero=True,positive_and_accident_each_from_actual_contingency=True)

def prepare_rt(run,output):
 """Explicit pure large constructor; not called by import/tests. No native."""
 out=Path(output).resolve();need(not out.exists(),'fresh RT preparation directory required')
 plan,permit,x=contract_from_da(run);common,fullinput,fullmodel,_,_,_=dependencies()
 inp,local=fullinput.build_full_input(plan,permit);m,_=fullmodel.build_full_template(inp)
 q,vs,cs=common.hs.serialize_model(m,common.OPTIONS);q.update(dual=True,preparation_elapsed_s=0.);names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])
 vector,mapping=construct_fresh_witness(inp,q,names,x['refs'])
 import importlib.util
 audit_path=ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src/smm/gd_r23_equivalent_compression.py'
 spec=importlib.util.spec_from_file_location('pipeline_rt_sparse_audit',audit_path);audit_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit_module)
 certificate=audit_module.audit(q,vector)
 out.mkdir()
 for n,z in [('SOURCE_CONTRACT.json',plan),('FULL_INPUT.json',inp),('full_template.json',q),('names.json',names),('constructed_primal.json',dict(primal=vector,template_sha256=digest(q),names_sha256=digest(names))),('PRIMAL_REVIEW.json',dict(status='pass_new_matrix_primal_only',certificate=dict(passed=True,**certificate),mapping=mapping,source='this run accepted SCED actual facts; not optimum'))]:write(out/n,z)
 meta=dict(schema='gd.pipeline.rt_prepared.v1',DA_run=str(Path(run).resolve()),DA_receipt_sha256=sha(Path(run)/'ARTIFACTS.json'),source_contract_sha256=digest(plan),artifact_hashes={p.name:sha(p)for p in out.iterdir()},source_code_sha256=sha(__file__),RT_source_identity=source_identity(),native_calls=0,model_builds=1)
 write(out/'PREPARATION.json',meta);return meta

def source_identity():
 common,_,_,_,runtime,_=dependencies()
 roots=[ROOT/'guangdong_r67_complete_tail_20261112',ROOT/'guangdong_r62_same_source_da_rt_20261112',ROOT/'guangdong_r52_minimal_source_view_20261112/r52smm',ROOT/'guangdong_r46_curtailable_full_day_20261112/r46smm',ROOT/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm']
 pins={str(p.resolve()):sha(p)for root in roots for p in root.rglob('*.py')};pins[str(Path(__file__).resolve())]=sha(__file__);audit_path=ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src/smm/gd_r23_equivalent_compression.py';pins[str(audit_path)]=sha(audit_path)
 need(sha(runtime.WORKER)==runtime.WORKER_SHA,'precision worker changed');pins[str(runtime.WORKER)]=sha(runtime.WORKER)
 return dict(pins=pins,sha256=digest(pins))

def open_rt(run,prepared,output):
 """New controller or replay of verified accepted facts; never restores a live ticket."""
 common,_,_,cache,runtime,_=dependencies()
 class PipelineRun(runtime.FreshRun):
  def __init__(self):
   self.output=Path(output).resolve();prepdir=Path(prepared).resolve();meta=read(prepdir/'PREPARATION.json')
   need(meta['schema']=='gd.pipeline.rt_prepared.v1'and meta['DA_receipt_sha256']==sha(Path(run)/'ARTIFACTS.json')and meta['source_code_sha256']==sha(__file__),'fresh RT preparation receipt')
   self.plan,self.permit,_=contract_from_da(run);need(meta['source_contract_sha256']==digest(self.plan),'fresh contract mismatch')
   self.cache_pins={str(prepdir/f):h for f,h in meta['artifact_hashes'].items()}
   for p,h in self.cache_pins.items():need(sha(p)==h,'RT preparation changed')
   self.full_input=read(prepdir/'FULL_INPUT.json');self.full_input_hash=digest(self.full_input);self.cache=cache.TemplateCache(read(prepdir/'full_template.json'),read(prepdir/'names.json'))
   witness=read(prepdir/'PRIMAL_REVIEW.json');v=read(prepdir/'constructed_primal.json');need(witness['certificate']['passed']and v['template_sha256']==self.cache.base_hash and v['names_sha256']==self.cache.names_hash,'initial witness binding')
   self.previous_primal=v['primal'];self.previous_certificate=dict(kind='fresh_DA_source_feasibility_only',path=str(prepdir/'PRIMAL_REVIEW.json'),sha256=sha(prepdir/'PRIMAL_REVIEW.json'))
   self.code=source_identity();need(meta['RT_source_identity']==self.code,'prepared RT source dependency set changed');self.id=self.plan['DA_pipeline_run_id']+'-RT';self.next=0;self.pending=None;self.accepted=[];self.native_calls=0;self.drained=False
   initial=self.plan['initial_preparation'];seed=deepcopy(initial['seed_state']);self.receipt=dict(id=self.id+'-SEED',at=seed['at'],available_at=seed['at'],state=seed,is_measurement=False,kind='fresh research preparation not telemetry');self.commands=[dict(id=self.id+'-PREP-'+str(j),issued_at=seed['at'],available_at=seed['at'],cell=deepcopy(c),kind='declared initial preparation')for j,c in enumerate(initial['cells'])]
   if self.output.exists():
    need(read(self.output/'SOURCE_CONTRACT.json')==self.plan and read(self.output/'SOURCE_IDENTITY.json')==self.code,'resume source identity changed')
    for i in range(96):
     w=self.output/f'W{i}';acc=w/'accepted';marker=w/'NATIVE_ATTEMPT.json'
     if not(acc/'COMPLETE.json').exists():
      if marker.exists()or(acc/'ACCEPTANCE.json').exists():raise ValueError('pending occurrence W'+str(i)+': inspect/reconcile actual evidence; never relaunch')
      need(not(w/'prepared').exists(),'unlaunched prepared ticket requires explicit recovery, no silent overwrite');break
     done=read(acc/'COMPLETE.json')
     for name,h in done['files'].items():need(sha(w/name)==h,'accepted recovery evidence changed')
     r=read(w/'native/response.json');facts=read(acc/'STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json')
     self.cache.commit_first_three(i,r['primal']);self.previous_primal=r['primal'];self.previous_certificate=dict(kind='restored_verified_actual_full_matrix_facts_not_live_capability',path=str(acc/'ACCEPTANCE.json'),sha256=sha(acc/'ACCEPTANCE.json'));self.receipt=facts['receipt'];self.commands=facts['commands'];self.accepted.append(i);self.next=i+1;self.native_calls+=1
    if(self.output/'D96_DRAIN.json').exists():
     drain=read(self.output/'D96_DRAIN.json');need(self.next==96,'drain before96');self.receipt=dict(at=drain['final_state']['at'],available_at=drain['final_state']['at'],state=drain['final_state'],is_measurement=False,kind='verified final synthetic execution');self.commands=[];self.drained=True
   else:
    self.output.mkdir(parents=True);write(self.output/'SOURCE_CONTRACT.json',self.plan);write(self.output/'SOURCE_IDENTITY.json',self.code)
   self._seal()
  def _check(self):
   need(self._state()==self.seal,'live prefix mutated');need(source_identity()==self.code,'source code changed');need(digest(self.cache.template)==self.cache.base_hash and digest(self.full_input)==self.full_input_hash,'cached math changed');common.source.validate_contract(self.plan,self.permit,recheck_files=True)
  def accept(self,ticket,paths):
   index=self.next;report=super().accept(ticket,paths);w=self.output/f'W{index}';files={str(p.relative_to(w)):sha(p)for base in ('prepared','native','accepted')for p in(w/base).iterdir()if p.is_file()and p.name!='COMPLETE.json'};write(w/'accepted/COMPLETE.json',dict(schema='gd.pipeline.rt.completed_occurrence.v1',window=index,files=files,DA_receipt_sha256=self.plan['DA_receipt_sha256']));return report
 return PipelineRun()

def launch_rt(prepared,output,identity):
 """New exact request occurrence. Reuses frozen precision worker, not old permits."""
 import subprocess,time,os,signal
 common,_,_,_,runtime,_=dependencies();p=Path(prepared);out=Path(output);ticket=read(p/'TICKET.json')
 need(ticket['source_identity_sha256']==identity['sha256']and ticket['schema']=='gd.research.r67.live_prepared.v1','fresh runtime identity')
 for n,h in ticket['artifact_hashes'].items():need(sha(p/n)==h,'prepared artifact changed')
 q=read(p/'template.json');need(q['dual']and not q['warmstart']and all(v['kind']=='C'for v in q['variables']),'RT continuous only')
 need(q['options']['threads']==1 and q['options']['mip_rel_gap']==.001 and q['options']['mip_abs_gap']==0 and q['options']['time_limit']==120.,'verified RT solver settings')
 need(sha(runtime.WORKER)==runtime.WORKER_SHA,'precision worker source')
 attempt=p.parent/'NATIVE_ATTEMPT.json';need(not attempt.exists(),'occurrence already attempted; no retry');write(attempt,dict(ticket_sha256=sha(p/'TICKET.json'),output=str(out),automatic_retry=False));out.mkdir(exist_ok=False)
 q['deadline']=time.perf_counter()+120;write(out/'request.json',q);write(out/'IDENTITY.json',dict(schema='gd.research.r67.complete_tail_RT.native.v1',request_sha256=sha(out/'request.json'),template_sha256=sha(p/'template.json'),ticket_sha256=sha(p/'TICKET.json'),worker_sha256=runtime.WORKER_SHA,branch_id=ticket['branch_id'],window=ticket['window'],automatic_retry=False,execution_authority=False))
 with(out/'worker.log').open('xb')as log:
  proc=subprocess.Popen([sys.executable,'-B',str(runtime.WORKER),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**common.hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True)
  write(out/'launch.json',dict(pid=proc.pid,attempt=1,worker_sha256=runtime.WORKER_SHA))
  while proc.poll()is None:
   if(p.parents[1]/'CANCEL_REQUESTED').exists()or time.perf_counter()>q['deadline']+5:
    os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise RuntimeError('stopped native occurrence; inspect evidence, no retry')
   time.sleep(.1)
 need(proc.returncode==0,'worker failure');r=read(out/'response.json');need(not r.get('error'),'worker error')
 write(out/'TRANSPORT_RECEIPT.json',dict(status='optimal_pending_original_KKT' if r.get('status')=='optimal' else 'nonoptimal_not_mathematically_accepted',native_status=r.get('native_status'),mathematical_acceptance=False,response_sha256=sha(out/'response.json'),worker_sha256=runtime.WORKER_SHA))
 confirmed={k:r.get(k)for k in ('native_status','status','has_primal','dual_valid','objective','bound','native_settings','time_settings')};confirmed.update(pid=proc.pid,returncode=proc.returncode,response_sha256=sha(out/'response.json'),automatic_retry=False);write(out/'CONFIRMED_NATIVE_RESULT.json',confirmed)
 need(r.get('native_status')=='Optimal'and r.get('status')=='optimal'and r.get('has_primal')and r.get('dual_valid'),'nonoptimal RT is pending')
 return {k:out/n for k,n in [('request','request.json'),('response','response.json'),('identity','IDENTITY.json'),('launch','launch.json'),('confirmed','CONFIRMED_NATIVE_RESULT.json')]}

def run_rt(run,prepared,output,windows=96,execute_native=False):
 need(1<=windows<=96,'1..96 windows')
 if not execute_native:return dict(status='dryrun_only',DA_run=str(run),prepared=str(prepared),output=str(output),windows=windows,native_calls=0,fixed_DA=True,R80=False)
 controller=open_rt(run,prepared,output)
 while controller.next<windows:
  if(Path(output)/'CANCEL_REQUESTED').exists():return dict(status='paused_at_accepted_boundary',report=controller.report())
  ticket,path,_=controller.prepare(controller.next);events=launch_rt(path,path.parent/'native',controller.code);controller.accept(ticket,events)
 report=controller.drain()if windows==96 and not controller.drained else controller.report()
 result=dict(report=report,native_launch_attempts=controller.native_calls,independent_PRICE_pending=True,full_market_completed=False)
 result['RT_source_identity_sha256']=controller.code['sha256']
 result['DA_source_identity']=accepted_da(run)[0]['source_identity']
 path=Path(output)/('RUN_RESULT.json'if windows==96 else 'PARTIAL_W'+str(windows)+'.json')
 if path.exists():need(read(path)==result,'existing final result differs')
 else:write(path,result)
 return result

def main():
 import argparse
 p=argparse.ArgumentParser(description='Isolated fresh RT stage; import never launches native')
 p.add_argument('action',choices=['prepare','run']);p.add_argument('--run',required=True);p.add_argument('--prepared');p.add_argument('--output',required=True);p.add_argument('--windows',type=int,default=4);p.add_argument('--execute-native',action='store_true');a=p.parse_args()
 if a.action=='prepare':
  need(not a.execute_native,'pure preparation cannot execute native');r=prepare_rt(a.run,a.output)
 else:
  need(a.prepared is not None,'prepared path required');r=run_rt(a.run,a.prepared,a.output,a.windows,a.execute_native)
 print(json.dumps(r));return 0
if __name__=='__main__':raise SystemExit(main())
