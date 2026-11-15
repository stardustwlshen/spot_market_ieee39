"""One in-memory event/AGC host. No durable restore or implicit solver permission."""
from pathlib import Path
from copy import deepcopy
import sys,json,uuid
from datetime import timedelta
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_event_host_candidate_v1_20261113'))
from v6_runtime import V6Session,RuntimeHooks
from v6_host import accepted_bundle,read,write,sha,e
from native_runtime import execute_pair

class UnifiedSession(V6Session):
 def export_generated_pair(self,p,a,record):
  if str(p)not in self.compatibility:return super().export_generated_pair(p,a,record)
  b,h,original_actual=self.compatibility[str(p)]
  e.need(a==original_actual,'compatibility source substitution')
  for f,v in record['artifact_pins'].items():e.need(sha(f)==v,'candidate drift')
  return b,h

class RollingHost:
 def __init__(self,run,output,*,mode='pure',research=False,same_period_execution=False):
  e.need(research is True,'explicit research switch');e.need(mode in ('pure','native'),'unknown mode')
  self.output=Path(output).resolve();e.need(not self.output.exists(),'durable restart unsupported; inspect existing evidence, never resume/retry')
  self.output.mkdir(parents=True);self.branch_id='unified-first-hour-'+uuid.uuid4().hex;self.mode=mode;self.session=UnifiedSession(run,research_fixed_curve_events=True);self.session.compatibility={};self.hooks=RuntimeHooks(self.session);self.accepted={};self.window=0;self.pending=None
  for p in [*R.rglob('*.py'),B/'agc_runtime/RESEARCH_INPUT.json']:self.session.registry['source_pins'][str(p)]=sha(p)
  from agc_lifecycle import AgcLifecycle
  self.agc=AgcLifecycle(self.session.run,self.output/'agc',accepted_source=self.accepted_source)
  self.agc.produce(0,cutoff=(self.agc.origin-timedelta(minutes=30)).isoformat())
  cp=self.session.program.current_carry(self.session.run,self.session.context,0);c=read(cp)
  e.need(c['cutoff_tick']==0 and c['delivery_tick']==(0 if self.session.context.get('schema')=='gd.provincial.short.context.v1' else 3),'initial source clock')
  self.same_period_execution=bool(same_period_execution)
  self.rt_clock_mode='provincial_15min_execution' if self.same_period_execution else 'legacy_issued_prefix'
  if self.same_period_execution and self.session.context.get('schema')!='gd.provincial.short.context.v1':
   from gd_live_market_runtime.initial_state import from_declared
   from smm.gd_october_job_codec import loads
   c=from_declared(loads((self.session.run/'seed/case.json').read_bytes()),c)
   cp=self.output/'INITIAL_RT_CARRY.json';write(cp,c)
  self.initial_carry_path=cp
  e.need(all(t['phase'] in ('stable','offline','off') and not t['remaining_curve'] for t in c['thermal'].values()),'initial active event requires verified incoming issued ledger adapter')
  e.need(not any(v for d in c['notice_values'].values()for v in d.values()),'initial issued notices require verified importer')
  l=dict(schema='gd.fixed.research_notice_ledger.v1',run_id=self.session.registry['run_id'],case_identity=self.session.registry['case_identity'],origin=c['origin'],cutoff_tick=0,receipts={},pending={},formal=False,actual_dispatch_authorized=False);l['ledger_sha256']=e.digest(l)
  self.hooks.carries[str(cp)]=sha(cp);self.hooks.ledgers[l['ledger_sha256']]=l;self.cp=cp;self.lid=l['ledger_sha256'];self.results=[]
 def accepted_source(self,w):
  e.need(w in self.accepted,'source not yet accepted in this lifecycle')
  p,a,pins=self.accepted[w]
  for f,h in pins.items():e.need(sha(f)==h,'registered raw source drift')
  return p,a
 def prepare(self):
  e.need(self.pending is None,'pending window must be resolved without retry');e.need(self.window<4,'bounded first hour only')
  if self.window==2:self.agc.produce(1,cutoff=read(self.cp)['cutoff'])
  from gd_provincial15_model import thermal_physical
  old=thermal_physical.build_free_scuc;bindings=[]
  def hook(**kw):
   _,binding=self.agc.apply_before_scuc(kw,read(self.cp));bindings.append(binding);return old(**kw)
  thermal_physical.build_free_scuc=hook
  p=self.output/f'W{self.window}'/'prepared';p.parent.mkdir(exist_ok=False)
  try:z=self.hooks.build(self.cp,p,ledger_id=self.lid)
  finally:thermal_physical.build_free_scuc=old
  e.need(len(bindings)==1,'single AGC/full freeSCUC application');bindings[0]['host_branch_id']=self.branch_id;write(p.parent/'AGC_BINDING.json',bindings)
  options=read(p/'template.json')['options'];e.need(options['mip_rel_gap']==.01 and options['threads']==1 and options['time_limit']==3600.,'native budget/options drift')
  self.pending=p;self.checkpoint('prepared');return z
 def adopt_compatible_old_raw(self):
  e.need(self.mode=='pure' and self.pending is not None,'pure pending only')
  p=self.pending;w=self.window;_,r,_,b,h=accepted_bundle(self.session.run,w);a=r/f'W{w}'/'actual';q=read(p/'template.json');n=read(p/'names.json');oq=b['SCUC_request'];on=b['names'];newrows=dict(zip(n['constraints'],q['rows']));oldrows=dict(zip(on['constraints'],oq['rows']))
  e.need(n['variables']==on['variables'] and q['objective']==oq['objective'],'columns/objective changed')
  e.need([{k:v for k,v in x.items()if k!='start'}for x in q['variables']]==[{k:v for k,v in x.items()if k!='start'}for x in oq['variables']],'bounds changed')
  e.need(all(newrows.get(k)==v for k,v in oldrows.items()),'original row changed')
  eq=self.session.program.fixed_sced(q,b['SCUC_response']['primal']);witness=deepcopy(b['SCED_response']);d=dict(zip(on['constraints'],witness['dual']));witness['dual']=[d.get(k,0.)for k in n['constraints']]
  from scoped_kkt import metrics
  proof=metrics(eq,witness,n,angle_tolerance=1e-8);e.need(proof['passed'],'new constraints incompatible with old raw')
  incoming=read(self.cp);oldcarry=e.unpack(b['incoming_carry'])
  for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):e.need(incoming[k]==oldcarry[k],'physical carry mismatch '+k)
  self.session.compatibility[str(p.resolve())]=(b,h,a.resolve())
  # Register original source honestly; no fake actual/accepted file in candidate.
  return self._finish(r/f'W{w}'/'prepared',a,dict(mode='old_raw_compatibility_only',KKT=proof,native_calls=0,new_awards_solved=False))
 def run(self,*,execute_native=False):
  e.need(self.mode=='native' and execute_native is True,'native requires separate explicit authorization; default0')
  e.need(self.pending is not None,'prepare required');self.checkpoint('native_requested_no_retry')
  z=execute_pair(self.hooks,self.pending,execute_native=True)
  gate=getattr(self,'before_handoff_gate',None)
  if gate is not None:gate._generated_after_verified_pair(self.pending,self.pending.parent/'actual',z)
  return self._finish(self.pending,self.pending.parent/'actual',z)
 def _finish(self,source_prepared,actual,proof):
  p=self.pending;w=self.window
  gate=getattr(self,'before_handoff_gate',None)
  decision=gate.before_handoff(p,actual,proof) if gate is not None else dict(status='LEGACY_RESEARCH_SIMULATION_NO_PREHANDOFF_AC_GATE',safety_release=False,dispatch_authorized=False,formal=False)
  nxt=self.hooks.handoff_accepted(p,actual,p.parent/'EXACT_CARRY.json',simulate_no_deviation=True)
  pins={str(f):sha(f)for f in [source_prepared/'SUMMARY.json',source_prepared/'template.json',source_prepared/'names.json',actual/'ACCEPTED_PAIR.json',*[actual/s/f for s in ('SCUC','SCED')for f in ('request.json.gz','response.json.gz','JOB.json','COMPLETED.json')]]}
  controller=getattr(self.hooks,'post_scuc',None)
  if controller is not None:controller.commit_after_handoff(p,actual,decision,nxt,grant=controller._receipt_commit_grant)
  self.accepted[w]=(source_prepared,actual,pins);self.cp=Path(nxt['carry_path']);self.lid=nxt['ledger_id'];self.window+=1;self.pending=None
  carry=read(self.cp)
  proof=dict(proof,before_handoff=decision)
  from safety_adapter import model_verified_safety
  safety=model_verified_safety(read(p/'names.json'),source_kind=proof.get('mode','new_accepted_raw_pair'));safety['numerical_pair_accepted']=True
  z=dict(branch_id=self.branch_id,window=w,proof=proof,handoff=nxt,executed_end_exclusive=carry['executed_end_exclusive'],issued_end_exclusive=carry['issued_end_exclusive'],execution_basis='research exact no-deviation replay, not observed operation',safety=safety,formal=False,safety_release=False)
  if decision.get('model_scope')=='active_power_dc':
   safety.update(status='DC_MODEL_CHECKS_COMPLETE',model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,execution_allowed=True,execution_scope='model simulation only')
   safety['checks']['network_sections_ac']='not_in_model_scope'
   z.update(model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,execution_allowed=True,execution_scope='model simulation only',publication_allowed=False)
  self.results.append(z);write(p.parent/'RESULT.json',z);self.checkpoint('accepted');return z
 def checkpoint(self,status):
  f=self.output/'RESULT.next.json';write(f,dict(self.read(),host_status=status));f.replace(self.output/'RESULT.json')
 def read(self):return dict(rt_clock_mode=self.rt_clock_mode,branch_id=self.branch_id,base_run=str(self.session.run),mode=self.mode,completed_windows=self.window,pending=str(self.pending)if self.pending else None,results=self.results,durable_restart_supported=False,safety_release=False,publication_allowed=False,dispatch_authorized=False,formal=False)

def plan():return dict(flow=['same accepted DA/H0','W0 prepare/pair/exact carry','W1 prepare/pair/exact carry','cutoff00:30 H1 from W1','W2 prepare/pair/exact carry','W3 prepare/pair/exact carry'],default_native_calls=0,proposed_native_calls=8,SCUC_seconds=3600,SCED_seconds=3600,SCED_solver='simplex',automatic_fallback=False,mip_gap=.01,threads=1,restart='blocked; inspect unknown occurrences',unchanged=['400 MW','50 MW AGC','qualification','DA protection','SOC/cycle'])
if __name__=='__main__':
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['plan','pure','native','read']);ap.add_argument('--run');ap.add_argument('--output');ap.add_argument('--research',action='store_true');ap.add_argument('--execute-native',action='store_true');a=ap.parse_args()
 if a.action=='plan':print(json.dumps(plan()))
 elif a.action=='read':print((Path(a.output)/'RESULT.json').read_text())
 else:
  e.need(a.action!='native' or a.execute_native,'native requires --execute-native; default zero calls')
  host=RollingHost(a.run,a.output,mode=a.action,research=a.research)
  for w in range(4):
   host.prepare()
   host.run(execute_native=True) if a.action=='native' else host.adopt_compatible_old_raw()
   print(a.action+' accepted W'+str(w),flush=True)
  host.checkpoint('complete');print('complete '+a.action+' first-hour lifecycle',flush=True)
