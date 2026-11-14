"""Single-process complete-tail controller. No native launch; main owns execution."""
from common67 import *
from cache import TemplateCache
from uuid import uuid4
from weakref import WeakKeyDictionary
from r62smm.gd_r46_native_support import audit_lp_certificate,reconstruct_solution_cells
sys.path.insert(0,str(R/'guangdong_r35_accepted_reduced_da_chain_20261112'))
from r35_pipeline import load_event
_TICKETS=WeakKeyDictionary()
class Ticket:__slots__=('__weakref__',)
WORKER=R/'safe_workers/rt_precision.py'
WORKER_SHA='1c04da260127cc0745b8ef19fcaf1c12b8a92276f86f2700958879609c0022f2'
CACHE=B/'prepared_v1';WITNESS=R/'guangdong_r67_independent_source_witness_20261112/witness_v1'
def identity():
 roots=[B,R/'guangdong_r62_same_source_da_rt_20261112',R/'guangdong_r52_minimal_source_view_20261112/r52smm',R/'guangdong_r46_curtailable_full_day_20261112/r46smm',R/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm']
 pins={str(p):file_sha(p) for root in roots for p in root.rglob('*.py')}
 require(file_sha(WORKER)==WORKER_SHA,'verified precision worker original changed')
 for p in [WORKER,CACHE/'PREPARATION.json',WITNESS/'PRIMAL_REVIEW.json',WITNESS/'constructed_primal.json']:pins[str(p)]=file_sha(p)
 return dict(pins=pins,sha256=digest(pins))
def compact_certificate(cert):
 # Full auditor has already executed. Preserve aggregates and worst actual items;
 # raw request/response retain every coefficient and primal/dual for reproduction.
 result={k:v for k,v in cert.items() if k not in ('rows','columns')}
 for group in ('rows','columns'):
  items=cert.get(group,[])
  if items:
   result['worst_'+group+'_primal']=max(items,key=lambda x:x.get('violation',0.))
   result['worst_'+group+'_complementarity']=max(items,key=lambda x:x.get('complementarity',0.))
 result['detailed_rows_and_columns_not_duplicated']=True
 result['full_raw_request_response_preserved']=True
 return result
class FreshRun:
 def __init__(self,output):
  self.output=Path(output).resolve();self.output.mkdir(parents=True,exist_ok=False);self.code=identity()
  self.plan,self.permit=source.load_contract(CONFIG,policy=source.POLICY);meta=read(CACHE/'PREPARATION.json')
  require(meta['source_contract_sha256']==digest(self.plan),'cached/source identity');self.cache_pins={str(CACHE/f):h for f,h in meta['artifact_hashes'].items()}
  for p,h in self.cache_pins.items():require(file_sha(p)==h,'cached artifact changed')
  self.full_input=read(CACHE/'FULL_INPUT.json');self.full_input_hash=digest(self.full_input);self.cache=TemplateCache(read(CACHE/'full_template.json'),read(CACHE/'names.json'))
  witness=read(WITNESS/'PRIMAL_REVIEW.json');vector=read(WITNESS/'constructed_primal.json');require(witness['status']=='pass_new_matrix_primal_only' and witness['certificate']['passed'],'initial full-tail feasible witness required')
  require(vector['template_sha256']==self.cache.base_hash and vector['names_sha256']==self.cache.names_hash and file_sha(WITNESS/'constructed_primal.json')==witness['constructed_primal_sha256'],'witness exact template/names')
  self.previous_primal=vector['primal'];self.previous_certificate=dict(kind='constructed_DA_source_feasibility_only_not_optimum',path=str(WITNESS/'PRIMAL_REVIEW.json'),sha256=file_sha(WITNESS/'PRIMAL_REVIEW.json'))
  self.id='R67-'+uuid4().hex;self.next=0;self.pending=None;self.accepted=[];self.native_calls=0;self.drained=False
  prep=self.plan['initial_preparation'];seed=deepcopy(prep['seed_state']);self.receipt=dict(id=self.id+'-SEED',at=seed['at'],available_at=seed['at'],state=seed,is_measurement=False,kind='same-source research preparation not telemetry')
  self.commands=[dict(id=self.id+'-PREP-'+str(j),issued_at=seed['at'],available_at=seed['at'],cell=deepcopy(c),kind='original preparation facts')for j,c in enumerate(prep['cells'])]
  write(self.output/'SOURCE_CONTRACT.json',self.plan);write(self.output/'SOURCE_IDENTITY.json',self.code);write(self.output/'CACHED_TEMPLATE_BINDING.json',dict(cache_pins=self.cache_pins,base_template_sha256=self.cache.base_hash,full_names_sha256=self.cache.names_hash,initial_primal_feasibility=self.previous_certificate,source_loads=1,model_builds_this_run=0,cache_used=True,forecast_frozen=True,zero_tracking_error=True,fixed_UC_modes_qualifications=True,unconditional_feasibility_claim=False))
  self._seal()
 def _state(self):return digest(dict(id=self.id,next=self.next,receipt=self.receipt,commands=self.commands,accepted=self.accepted,native_calls=self.native_calls,drained=self.drained,committed_end=self.cache.committed_end,committed_columns=self.cache.fixed,numeric_boundary_records=self.cache.numeric_boundary_records,previous_primal=self.previous_primal,previous_certificate=self.previous_certificate))
 def _seal(self):self.seal=self._state()
 def _check(self):
  require(self._state()==self.seal,'live state/prefix changed');require(identity()==self.code,'runtime source changed');require(digest(self.cache.template)==self.cache.base_hash and digest(self.cache.names)==self.cache.names_hash and digest(self.full_input)==self.full_input_hash,'cached mutable math/input changed')
  source.validate_contract(self.plan,self.permit)
 def prepare(self,index):
  self._check();require(index==self.next and 0<=index<96 and self.pending is None,'one sequential unconsumed ticket')
  spec=source.window_spec(self.plan,index);projection=project_issued_prefix(self.receipt,self.commands,cutoff=spec['cutoff'],start=spec['start'],technical=self.plan['technical'])
  provenance=dict(run_id=self.id,window=index,state_seal=self.seal,receipt_sha256=digest(self.receipt),issued_prefix_sha256=digest(self.commands),source_contract_sha256=digest(self.plan))
  projection.update(origin='same_live_R62_cutoff_receipt_and_issued_RT_prefix',session_id=self.id,run_id=self.id,live_provenance_sha256=digest(provenance))
  with source.native_stage_scope(self.plan,self.permit,run_id=self.id,index=index,stage='RT'):
   local=session.assemble_window_input(self.plan,self.permit,index,projection,transaction_id=self.id+'-W'+str(index),construction_only=False)
   offset=3*index
   for n in local['nodes']:
    require(local['load_mw'][n]==self.full_input['load_mw'][n][offset:offset+24] and local['fixed_injection_mw'][n]==self.full_input['fixed_injection_mw'][n][offset:offset+24],'forecast changed: recursive certificate invalid')
   for r,u in local['resources'].items():require(u['rows']==self.full_input['resources'][r]['rows'][offset:offset+24],'mode/qualification changed')
   for b,u in local['renewable'].items():require(u['availability_mw']==self.full_input['renewable'][b]['availability_mw'][offset:offset+24] and u['bid_proxy_yuan_mwh']==-50 and u['M2_dispatch_research']==1000,'VRE change')
   for l,z in local['network'].items():
    require(z['ptdf']==self.full_input['network'][l]['ptdf'] and all(z[k]==self.full_input['network'][l][k][offset:offset+24]for k in ('lower_mw','upper_mw')),'network change')
   q=self.cache.request(index)
  # Rows/domains stay fixed except prefix columns equal the last feasible primal.
  worst=0.;original_domain=0.;row_worst=0.;worst_row=None
  for j,v in enumerate(q['variables']):
   x=self.previous_primal[j];base=self.cache.template['variables'][j]
   worst=max(worst,0 if v['lb']is None else v['lb']-x,0 if v['ub']is None else x-v['ub'])
   original_domain=max(original_domain,0 if base['lb']is None else base['lb']-x,0 if base['ub']is None else x-base['ub'])
  for k,row in enumerate(q['rows']):
   value=math.fsum(c*self.previous_primal[j] for j,c in row['terms']);violation=max(0.,0. if row['lb']is None else row['lb']-value,0. if row['ub']is None else value-row['ub'])
   if violation>row_worst:row_worst=violation;worst_row=k
  require(worst<=1e-6 and original_domain<=1e-7 and row_worst<=1e-6,'inherited extension no longer satisfies complete updated model')
  feasible=dict(previous_full_row_certificate=self.previous_certificate,immutable_rows_sha256=self.cache.rows_hash,previous_primal_sha256=digest(self.previous_primal),max_updated_domain_violation=worst,max_original_domain_violation=original_domain,max_all_row_violation=row_worst,worst_row=worst_row,all_rows_checked=len(q['rows']),all_columns_checked=len(q['variables']),numeric_boundary_records=self.cache.numeric_boundary_records,raw_issued_values_unchanged=True,objective_change_does_not_affect_feasibility=True,new_optimality_claim=False,conditions='frozen forecasts/modes/network; same issued-prefix primal; zero tracking error; explicit numerical tolerances')
  token=Ticket();tid=self.id+'-TICKET-'+uuid4().hex;q.update(r67_ticket_id=tid,r67_source_identity=self.code['sha256'],r67_contract_sha256=digest(self.plan))
  out=self.output/('W'+str(index))/'prepared';out.mkdir(parents=True,exist_ok=False)
  for f,x in [('input.json',local),('local_RT_input.json',local),('template.json',q),('names.json',self.cache.names),('FEASIBLE_EXTENSION.json',feasible)]:write(out/f,x)
  record=dict(schema='gd.research.r67.live_prepared.v1',ticket_id=tid,branch_id=self.id,window=index,global_offset=offset,global_physical_cells=309,local_physical_cells=24,current_objective_cells=[offset,offset+24],tail_cost_zero=True,source_identity_sha256=self.code['sha256'],source_contract_sha256=digest(self.plan),artifact_hashes={f:file_sha(out/f)for f in ('input.json','local_RT_input.json','template.json','names.json','FEASIBLE_EXTENSION.json')},execution_authority=False,tail_LP_duals_are_not_independent_PRICE=True,numerical_transport=dict(worker_path=str(WORKER),worker_sha256=WORKER_SHA,dual_feasibility_tolerance=1e-9,simplex_scale_strategy=0,KKT_tolerance_not_relaxed=True))
  write(out/'TICKET.json',record);data=dict(owner=self,input=local,request=q,names=self.cache.names,record=record,path=out,consumed=False)
  data['memory_sha256']=digest({k:data[k]for k in ('input','request','names','record')});_TICKETS[token]=data;self.pending=token;return token,out,record
 def accept(self,ticket,event_paths):
  self._check();require(ticket is self.pending and ticket in _TICKETS,'same fresh R67 ticket');t=_TICKETS[ticket]
  require(t['owner'] is self and not t['consumed'] and digest({k:t[k]for k in ('input','request','names','record')})==t['memory_sha256'],'ticket memory mutated/consumed')
  for f,h in t['record']['artifact_hashes'].items():require(file_sha(t['path']/f)==h,'prepared disk changed')
  with source.native_stage_scope(self.plan,self.permit,run_id=self.id,index=self.next,stage='RT'):
   ev=load_event(event_paths);require({k:v for k,v in ev['request'].items()if k!='deadline'}==t['request'],'exact actual new full-tail request required')
   require(ev['response']['native_settings']['dual_feasibility_tolerance']==1e-9 and ev['response']['native_settings']['simplex_scale_strategy']==0,'actual numerical settings readback')
   require(read(ev['pins']['identity']['path'])['worker_sha256']==WORKER_SHA,'actual verified worker identity')
   cert=audit_lp_certificate(ev['request'],ev['response'],t['names'])
   # Reintegrate every new global lookahead cell; these are not all issued/executed.
   full_cells,full_terminal=reconstruct_solution_cells(self.full_input,t['names'],ev['response']['primal'])
   names,values=self.cache.local_view(self.next,ev['response']['primal']);cells,terminal=reconstruct_solution_cells(t['input'],names,values)
   require(len(full_cells)==309 and len(cells)==24,'complete physical views')
  out=t['path'].parent/'accepted';out.mkdir(exist_ok=False);offset=self.next*3
  audit=dict(schema='gd.research.r67.accepted_RT.v1',window=self.next,global_offset=offset,event_pins=ev['pins'],event_id=ev['event_id'],KKT=compact_certificate(cert),global_lookahead_cells_reintegrated=309,local_lookahead_cells_reintegrated=24,issued_cells=3,source_contract_sha256=digest(self.plan),source_identity_sha256=self.code['sha256'],local_RT_input_sha256=digest(t['input']),tail_LP_duals_are_not_independent_PRICE=True,execution_authority=False)
  commands=[dict(id=self.id+'-W'+str(self.next)+'-'+str(j),issued_at=t['input']['window']['cutoff'],available_at=t['input']['window']['cutoff'],cell=c,kind='same-source R67 first-three-only synthetic RT command',accepted_event=ev['event_id'],global_cell_index=offset+j)for j,c in enumerate(cells[:3])]
  write(out/'ACCEPTANCE.json',audit);write(out/'issued_commands.json',commands);write(out/'local_24_physical_cells.json',cells)
  write(out/'GLOBAL_EXTENSION_FACTS.json',dict(global_lookahead_cells=309,lookahead_is_not_executed=True,global_terminal_state=full_terminal,first_three_issued_global_indices=list(range(offset,offset+3)),local_RT_input_sha256=digest(t['input']),acceptance_sha256=file_sha(out/'ACCEPTANCE.json')))
  self.cache.commit_first_three(self.next,ev['response']['primal']);self.previous_primal=ev['response']['primal'];self.previous_certificate=dict(kind='actual full-matrix primal certificate only, not next-window optimality',path=str((out/'ACCEPTANCE.json').resolve()),sha256=file_sha(out/'ACCEPTANCE.json'),event_id=ev['event_id'])
  state=deepcopy(t['input']['projection']['state']);at=state['at'];self.receipt=dict(id=self.id+'-RECEIPT-'+str(self.next),at=at,available_at=at,state=state,is_measurement=False,kind='zero-tracking previously issued prefix not telemetry');self.commands=commands;self.accepted.append(self.next);self.next+=1;self.native_calls+=1;t['consumed']=True;self.pending=None;self._seal()
  report=self.report();write(out/'STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json',dict(report=report,receipt=self.receipt,commands=self.commands));return report
 def report(self):
  n=len(self.accepted);return dict(branch_id=self.id,accepted_RT_windows=n,new_accepted_RT_native_calls=self.native_calls,issued_RT_cells=3*n,executed_RT_cells=3*n if self.drained else max(0,3*(n-1)),preparation_cells_executed=3 if n else 0,receipt_at=self.receipt['at'],source_load_count=1,full_template_load_count=1,model_builds_this_run=0,global_horizon309=True,same_controller_full96_complete=self.drained and n==96,noquote_bid=-50,tail_duals_are_not_PRICE=True,financial_settlement_complete=False,full_market_completed=False)
 def drain(self):
  self._check();require(self.next==96 and self.accepted==list(range(96)) and self.pending is None and not self.drained,'all96 accepted before final drain');state=deepcopy(self.receipt['state'])
  for cmd in self.commands:require(cmd['available_at']<=self.receipt['at'],'issued command availability');state=integrate_cell(state,cmd['cell'],self.plan['technical'])
  end=(datetime.fromisoformat(self.plan['day_start'])+timedelta(days=1)).isoformat();require(state['at']==end,'D96 exact time')
  for r,F in self.plan['midnight_targets'].items():
   require(abs(state['batteries'][r]['energy_mwh']-F)<=1e-6,'original D96 target')
   for date,U in state['batteries'][r]['daily_used_internal_mwh'].items():
    if date in self.plan['technical']['batteries'][r]['cycle_budget']:require(U<=self.plan['technical']['batteries'][r]['cycle_budget'][date]['limit_internal_mwh']+1e-6,'original dated budget')
  self.receipt=dict(at=end,available_at=end,state=state,is_measurement=False,kind='final synthetic execution not telemetry');self.commands=[];self.drained=True;self._seal();report=self.report();require(report['issued_RT_cells']==report['executed_RT_cells']==288,'issued/executed closure');write(self.output/'D96_DRAIN.json',dict(report=report,final_state=state));return report
