"""Fresh in-process R62 source/permit reused across windows. No native launcher."""
from prepare import B,R,source,session,models,hs,OPTIONS,attach,POLICY,write,digest,file_sha,require
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary
import time,sys
from r62smm.gd_r15_two_window import project_issued_prefix
from r62smm.gd_r15_da_rt_bridge import integrate_cell
from r62smm.gd_r46_native_support import audit_lp_certificate,reconstruct_solution_cells
sys.path.insert(0,str(R/'guangdong_r35_accepted_reduced_da_chain_20261112'))
from r35_pipeline import load_event
_TICKETS=WeakKeyDictionary()
class Ticket:__slots__=('__weakref__',)
def identity():
 roots=[B,R/'guangdong_r52_minimal_source_view_20261112/r52smm',R/'guangdong_r46_curtailable_full_day_20261112/r46smm',R/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm']
 pins={str(p):file_sha(p) for root in roots for p in root.rglob('*.py')};return dict(pins=pins,sha256=digest(pins))
def model(inp):
 m,_=models.build_rt_template(inp)
 if datetime.fromisoformat(inp['window']['horizon_end'])!=datetime.fromisoformat(inp['day_start'])+timedelta(days=1):
  for row in m.rolling_terminal.values():row.deactivate()
 g=attach(m,inp);q,vs,cs=hs.serialize_model(m,OPTIONS);q.update(dual=True,preparation_elapsed_s=0.)
 return q,dict(variables=[v.name for v in vs],constraints=[z.name for z in cs]),g
class FreshRun:
 def __init__(self,output):
  started=time.perf_counter();self.output=Path(output).resolve();self.output.mkdir(parents=True,exist_ok=False)
  self.code=identity();self.plan,self.permit=source.load_contract(B/'SOURCE_CONFIG.json',policy=source.POLICY)
  self.id='R62-'+uuid4().hex;self.next=0;self.pending=None;self.accepted=[];self.native_calls=0;self.drained=False
  prep=self.plan['initial_preparation'];seed=deepcopy(prep['seed_state'])
  self.receipt=dict(id=self.id+'-SEED',at=seed['at'],available_at=seed['at'],state=seed,is_measurement=False,kind='new_research_preparation_not_telemetry')
  self.commands=[dict(id=self.id+'-PREP-'+str(j),issued_at=seed['at'],available_at=seed['at'],cell=deepcopy(c),kind='original_research_preparation_facts') for j,c in enumerate(prep['cells'])]
  write(self.output/'SOURCE_CONTRACT.json',self.plan);write(self.output/'SOURCE_IDENTITY.json',self.code)
  write(self.output/'INITIALIZATION.json',dict(branch_id=self.id,source_load_count=1,elapsed_s=time.perf_counter()-started,old_live_permit_reused=False,new_native_calls=0,scope='actual new R50v2/R53 source; physical publication clock explicitly simulated'))
  self._seal()
 def _state(self):return digest(dict(id=self.id,next=self.next,receipt=self.receipt,commands=self.commands,accepted=self.accepted,native_calls=self.native_calls,drained=self.drained))
 def _seal(self):self.seal=self._state()
 def _check(self):
  require(self.seal==self._state(),'R62 live state changed outside transaction');require(identity()==self.code,'frozen runtime/dependencies changed');source.validate_contract(self.plan,self.permit)
 def prepare(self,index):
  self._check();require(index==self.next and 0<=index<96 and self.pending is None,'one sequential fresh ticket')
  spec=source.window_spec(self.plan,index)
  projection=project_issued_prefix(self.receipt,self.commands,cutoff=spec['cutoff'],start=spec['start'],technical=self.plan['technical'])
  provenance=dict(run_id=self.id,window=index,live_state_seal=self.seal,receipt_sha256=digest(self.receipt),issued_commands_sha256=digest(self.commands),source_contract_sha256=digest(self.plan),source_identity_sha256=self.code['sha256'])
  projection.update(origin='same_live_R62_cutoff_receipt_and_issued_RT_prefix',session_id=self.id,run_id=self.id,live_provenance_sha256=digest(provenance))
  with source.native_stage_scope(self.plan,self.permit,run_id=self.id,index=index,stage='RT'):
   inp=session.assemble_window_input(self.plan,self.permit,index,projection,transaction_id=self.id+'-W'+str(index),construction_only=False)
   require(all(u['bid_proxy_yuan_mwh']==-50 and u['M2_dispatch_research']==1000 for u in inp['renewable'].values()),'same-source noquote policy')
   q,n,g=model(inp)
  token=Ticket();tid=self.id+'-TICKET-'+uuid4().hex
  q.update(r62_ticket_id=tid,r62_source_identity=self.code['sha256'],r62_contract_sha256=digest(self.plan),r62_policy_sha256=digest(POLICY),r62_window=index)
  out=self.output/('W'+str(index))/'prepared';out.mkdir(parents=True,exist_ok=False)
  for f,z in [('input.json',inp),('template.json',q),('names.json',n),('GUARD.json',g)]:write(out/f,z)
  record=dict(schema='gd.research.r62.live_prepared.v1',ticket_id=tid,branch_id=self.id,window=index,source_identity_sha256=self.code['sha256'],source_contract_sha256=digest(self.plan),artifact_hashes={f:file_sha(out/f) for f in ('input.json','template.json','names.json','GUARD.json')},noquote_bid=-50,dispatch_M2_research=1000,old_live_permit_reused=False,execution_authority=False)
  write(out/'TICKET.json',record);_TICKETS[token]=dict(owner=self,input=inp,request=q,names=n,path=out,record=record,consumed=False)
  _TICKETS[token]['memory_sha256']=digest({k:_TICKETS[token][k] for k in ('input','request','names','record')})
  self.pending=token;return token,out,record
 def accept(self,ticket,event_paths):
  self._check();require(ticket is self.pending and ticket in _TICKETS,'same fresh R62 ticket');t=_TICKETS[ticket]
  require(t['owner'] is self and not t['consumed'],'ticket used');require(digest({k:t[k] for k in ('input','request','names','record')})==t['memory_sha256'],'ticket memory DTO mutated');require(all(file_sha(t['path']/f)==h for f,h in t['record']['artifact_hashes'].items()),'prepared artifact mutation')
  with source.native_stage_scope(self.plan,self.permit,run_id=self.id,index=self.next,stage='RT'):
   ev=load_event(event_paths);require({k:v for k,v in ev['request'].items() if k!='deadline'}==t['request'],'actual event is not exact new R62 request')
   cert=audit_lp_certificate(ev['request'],ev['response'],t['names']);cells,terminal=reconstruct_solution_cells(t['input'],t['names'],ev['response']['primal'])
  out=t['path'].parent/'accepted';out.mkdir(exist_ok=False)
  audit=dict(schema='gd.research.r62.accepted_RT.v1',window=self.next,event_pins=ev['pins'],event_id=ev['event_id'],KKT=cert,full_horizon_reintegrated_cells=24,issued_cells=3,source_contract_sha256=digest(self.plan),source_identity_sha256=self.code['sha256'],execution_authority=False)
  state=deepcopy(t['input']['projection']['state']);at=state['at'];cutoff=t['input']['window']['cutoff']
  commands=[dict(id=self.id+'-W'+str(self.next)+'-'+str(j),issued_at=cutoff,available_at=cutoff,cell=c,kind='new_same_source_R62_RT_synthetic_command',accepted_event=ev['event_id']) for j,c in enumerate(cells[:3])]
  write(out/'ACCEPTANCE.json',audit);write(out/'issued_commands.json',commands);write(out/'full_horizon_cells.json',cells)
  self.receipt=dict(id=self.id+'-RECEIPT-'+str(self.next),at=at,available_at=at,state=state,is_measurement=False,kind='zero_tracking_previously_issued_commands_not_telemetry')
  self.commands=commands;self.accepted.append(self.next);self.next+=1;self.native_calls+=1;t['consumed']=True;self.pending=None;self._seal()
  report=self.report();write(out/'STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json',dict(report=report,receipt=self.receipt,commands=self.commands));return report
 def report(self):
  n=len(self.accepted);return dict(branch_id=self.id,accepted_RT_windows=n,new_accepted_RT_native_calls=self.native_calls,issued_RT_cells=3*n,executed_RT_cells=3*n if self.drained else max(0,3*(n-1)),preparation_cells_executed=3 if n else 0,receipt_at=self.receipt['at'],same_new_DA_source=True,noquote_bid=-50,source_load_count=1,prices_complete=False,financial_settlement_complete=False,full_market_completed=False)
 def drain(self):
  self._check();require(self.next==96 and self.accepted==list(range(96)) and self.pending is None and not self.drained,'only complete suffix final drain')
  state=deepcopy(self.receipt['state'])
  for cmd in self.commands:
   require(cmd['available_at']<=self.receipt['at'],'not yet available final command');state=integrate_cell(state,cmd['cell'],self.plan['technical'])
  end=(datetime.fromisoformat(self.plan['day_start'])+timedelta(days=1)).isoformat();require(state['at']==end,'D96 exact time')
  for b,F in self.plan['midnight_targets'].items():
   require(abs(state['batteries'][b]['energy_mwh']-F)<=1e-6,'D96 original target')
   for date,U in state['batteries'][b]['daily_used_internal_mwh'].items():
    if date in self.plan['technical']['batteries'][b]['cycle_budget']:require(U<=self.plan['technical']['batteries'][b]['cycle_budget'][date]['limit_internal_mwh']+1e-6,'daily budget')
  self.receipt=dict(at=end,available_at=end,state=state,is_measurement=False,kind='final_simulated_execution_not_telemetry');self.commands=[];self.drained=True;self._seal()
  report=self.report();require(report['issued_RT_cells']==report['executed_RT_cells']==288,'count closure');write(self.output/'D96_DRAIN.json',dict(report=report,final_state=state));return report
