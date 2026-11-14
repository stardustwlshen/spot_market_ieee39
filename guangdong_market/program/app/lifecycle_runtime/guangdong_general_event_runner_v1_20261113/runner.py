"""Default runner integration via existing handoff callback; explicit research switch."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import sys
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_normalized_event_handoff_v1_20261113'))
from hooks import NormalizedHooks,RuntimeHooks,verify_bundle_storage,read,write,sha,e,event
from control import AcceptedInstructions
from prediction import predict
class GeneralEventHooks(NormalizedHooks):
 @classmethod
 def attach(cls,hooks,*,research_event_simulation=False):
  e.need(type(research_event_simulation)is bool,'explicit boolean research flag')
  e.need(type(hooks)in (RuntimeHooks,NormalizedHooks),'registered live hooks only');hooks.session.verify_sources();out=cls(hooks.session)
  out.ledgers=hooks.ledgers;out.carries=hooks.carries;out.prepared=hooks.prepared;out.research=research_event_simulation;out.accepted_plans={};out._plan_views={};out._stage_pins={};out._scheduled={};out._phase_grant=object();out._phase=None
  for f in [*B.glob('*.py'),*(R/'guangdong_normalized_event_handoff_v1_20261113').glob('*.py'),R/'guangdong_shutdown_instruction_candidate_v1_20261113/instructions.py']:out.session.registry['source_pins'][str(f)]=sha(f)
  return out
 def build(self,carry_path,output,*,ledger_id):
  # Existing event locks remain; append only conservative availability rows.
  from availability import window_plan
  import pyomo.environ as pyo
  serialize=self.session.builder.serialize_model;proof=[];scheduled=[]
  for pins in self._stage_pins.values():
   for f,h in pins.items():e.need(sha(f)==h,'old accepted scheduling source drift')
  def guarded(model,options):
   rows,commitments=window_plan(model._r23['inputs'],self.ledgers[ledger_id],self._plan_views,cells=len(model.T),research=self.research);scheduled.extend(commitments);model.notice_availability_gate=pyo.ConstraintList()
   for row in rows:model.notice_availability_gate.add(model.start[row['unit'],row['local_tick']]==row['value'])
   proof.extend(rows);return serialize(model,options)
  self.session.builder.serialize_model=guarded
  try:result=super().build(carry_path,output,ledger_id=ledger_id)
  finally:self.session.builder.serialize_model=serialize
  target=Path(output).resolve()/'NOTICE_AVAILABILITY.json';write(target,dict(locks=proof,scheduled_research_commitments=scheduled,bounded_intermediate_tick_scheduler_supported=True,no_future_receipts_inserted=True,native_calls=0));self.prepared[str(Path(output).resolve())]['artifact_pins'][str(target)]=sha(target);self.prepared[str(Path(output).resolve())]['research_commitments']=deepcopy(scheduled)
  return dict(result,availability_locks=proof)
 def verify_phase(self):
  self.session.verify_sources();e.need(self._phase is not None,'no accepted-control phase')
  rec=self._phase['record']
  for f,h in rec['artifact_pins'].items():e.need(sha(f)==h,'accepted phase prepared drift')
  e.need(sha(rec['carry_path'])==rec['carry_sha256'],'model carry drift')
  for f,h in self._phase['raw_pins'].items():e.need(sha(f)==h,'accepted raw drift')
 def issue_and_predict(self,bundle,bundle_hash,ledger,record,actual):
  plan=e.export_plan(bundle,expected_bundle_sha256=bundle_hash);case=e.unpack(bundle['case']);start=e.tick(case['contract']['domain_start'],plan['origin']);cut=e.tick(case['contract']['cutoff'],plan['origin'])
  incoming=read(record['carry_path']);e.need(start==incoming['delivery_tick']and cut==incoming['cutoff_tick'],'registered model boundary clocks');e.need(plan['available_tick']<=start,'future accepted plan')
  if not self.accepted_plans:e.need(all(s['phase']in ('off','offline','stable')and not s['remaining_curve']for s in incoming['thermal'].values()),'active initial event requires sourced importer; unsupported')
  raw_pins=self.session.accepted_phase_pins(actual)if hasattr(self.session,'accepted_phase_pins')else{str(actual/s/f):sha(actual/s/f)for s in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')}
  self._phase=dict(record=record,raw_pins=raw_pins);self.verify_phase()
  stage=len(self.accepted_plans);self.accepted_plans[stage]=(deepcopy(bundle),bundle_hash);self._plan_views[stage]=dict(deepcopy(plan),host_source_window=cut//3);self._issue_tick=start;self._stage_pins[stage]={**self._phase['raw_pins'],**record['artifact_pins'],record['carry_path']:record['carry_sha256']}
  # Replay only commitments fixed before this new plan existed, at exact due ticks.
  from schedule import issue_scheduled
  ledger,transitions=issue_scheduled(ledger,record.get('research_commitments',[]),self.accepted_plans,self._stage_pins,cut=cut,available=start,current_stage=stage,research=self.research)
  # Startup notices: only exact existing due tick, never backfill missing lead.
  newledger=e.publish(bundle,ledger,expected_bundle_sha256=bundle_hash,cutoff_tick=start,simulate=True,expected_previous_ledger_sha256=ledger['ledger_sha256'])
  if set(newledger['receipts'])-set(ledger['receipts']):e.need(self.research,'explicit research event simulation required')
  self.ledgers[newledger['ledger_sha256']]=newledger;control=AcceptedInstructions(self,self._phase_grant,newledger['ledger_sha256'],start)
  for key,ev in plan['events'].items():
   if ev['phase']=='shutdown'and start<=ev['begin_tick']<start+3 and key not in newledger['receipts']:
    e.need(self.research,'explicit research event simulation required');token=control.register(stage,key,as_of_tick=start,research=True);control.issue(token,as_of_tick=start,research=True)
  newledger=self.ledgers[control.host.lid];e.verify_ledger(newledger)
  # Validated at accepted issue tick. The original optimization cutoff is unchanged.
  new=predict(case,e.unpack(bundle['boundary']),bundle['names'],bundle['SCED_response']['primal'],issue_tick=start,executed_cells=3,receipts=list(newledger['receipts'].values()))
  normalized,norm=verify_bundle_storage(bundle);new['storage']=normalized
  begin=e.stamp(case['contract']['domain_start']);new.update(cutoff=begin.isoformat(),boundary_at=(begin+timedelta(minutes=15)).isoformat(),executed_end_exclusive=start,issued_end_exclusive=start+3)
  phase=dict(intermediate_notice_transitions=transitions,model_cutoff_tick=cut,accepted_plan_available_tick=plan['available_tick'],research_instruction_issue_tick=start,predicted_prefix_start_tick=start,predicted_prefix_end_tick=start+3,executed_clock_end_tick=start,bundle_sha256=bundle_hash,source='host-verified accepted raw pair; fixed declared curves only',research_event_simulation=self.research,formal=False,safety_release=False,actual_dispatch_authorized=False)
  return new,newledger,norm,phase
 def handoff_accepted(self,prepared,actual,output,*,simulate_no_deviation=False):
  """Once normal runner has REAL accepted SCUC+SCED, replay without stable-only code.
 Independent normalization adapter; no new native call or real dispatch permission.
 """
  assert simulate_no_deviation is True,'explicit simulated handoff required'
  s=self.session;s.verify_sources();p=Path(prepared).resolve();a=Path(actual).resolve();record=self.prepared[str(p)]
  for f,h in record['artifact_pins'].items():assert sha(f)==h,'generated input changed'
  assert sha(record['carry_path'])==record['carry_sha256'],'boundary changed'
  from host_export import export
  summary=read(p/'SUMMARY.json')
  if hasattr(s,'export_generated_pair'):bundle,h=s.export_generated_pair(p,a,record)
  else:bundle,h=export(p,a,producer_root=s.registry['accepted_producer_root'],expected_summary_sha256=record['summary_sha256'],case_identity=s.registry['case_identity'],run_id=s.registry['run_id'],available_at=summary['domain_start'])
  ledger=self.ledgers[record['ledger_id']];new,nextledger,norm,phase=self.issue_and_predict(bundle,h,ledger,record,a)
  incoming=read(record['carry_path']);carry=deepcopy(incoming);carry.update(thermal=new['boundary_states'],thermal_age_h=new['thermal_age_h'],storage=new['storage'],notice_requirements=new['notice_requirements'],fixed_curve_state=new['fixed_curve_state'],cutoff=new['cutoff'],boundary_at=new['boundary_at'],executed_end_exclusive=new['executed_end_exclusive'],issued_end_exclusive=new['issued_end_exclusive'],cutoff_tick=new['executed_end_exclusive'],delivery_tick=new['issued_end_exclusive'],formal=False,safety_release=False)
  phasepath=Path(output).resolve().with_suffix('.ACCEPTED_PHASE.json');write(phasepath,phase);carry['accepted_control_phase']={'path':str(phasepath),'sha256':sha(phasepath)}
  normpath=Path(output).resolve().with_suffix('.NORMALIZATION.json');write(normpath,norm)
  carry['source_pins']={**s.registry['source_pins'],str(Path(__file__).resolve()):sha(__file__),**record['artifact_pins'],str(a/'ACCEPTED_PAIR.json'):sha(a/'ACCEPTED_PAIR.json')}
  carry['boundary_numeric_normalization']=norm['power'];carry['energy_boundary_numeric_normalization']=norm['energy']
  carry['boundary_normalization_receipt']={'path':str(normpath),'sha256':sha(normpath),'receipt_sha256':norm['receipt_sha256']}
  carry['source_pins'].update({str(normpath):sha(normpath),str(phasepath):sha(phasepath),**norm['source_pins']})
  # Publish only newly due research notices, then bind exactly the same ledger
  # into the next builder's legacy notice map and additive hook.
  # Already issued at authenticated accepted-plan clock, before prefix prediction.
  carry['notice_values']={g:{str(r['notice_tick']):1 for r in nextledger['receipts'].values()if r['unit']==g and r['phase']=='startup'}for g in carry['thermal']}
  carry['event_ledger_sha256']=nextledger['ledger_sha256']
  output=Path(output).resolve();write(output,carry);self.carries[str(output)]=sha(output)
  self.ledgers[nextledger['ledger_sha256']]=nextledger
  return dict(carry_path=str(output),carry_sha256=sha(output),ledger_id=nextledger['ledger_sha256'],accepted_control_phase=phase,native_calls=0,formal=False,safety_release=False)

 def event_status(self):
  events=[]
  for w,plan in self._plan_views.items():
   for key,ev in plan['events'].items():
    receipts=[l['receipts'][key]for l in self.ledgers.values()if key in l['receipts']]
    receipt=receipts[-1]if receipts else None
    covering=[(v['available_tick'],k,v)for k,v in self._plan_views.items()if v['available_tick']<=self._issue_tick and v['domain_start_tick']<=ev['begin_tick']<v['domain_start_tick']+v['cells']]
    latest=max(covering,key=lambda x:(x[0],x[1]))if covering else None
    stale=latest is not None and latest[1]!=w
    commitments=[c for r in getattr(self,'prepared',{}).values()for c in r.get('research_commitments',[])if c['event']['event_id']==key and c['source_plan_sha256']==plan['plan_sha256']]
    status='research_issued'if receipt else'scheduled_research_commitment'if commitments else'superseded_not_actionable'if stale else'available_not_issued'
    reason='not operational dispatch; safety blocked'if receipt else'old source fixed before new solution; issue only at exact promised5min tick'if commitments else'newer available covering source supersedes this historical proposal'if stale else'future event or instruction permission required'
    events.append(dict(event_id=key,unit=ev['unit'],phase=ev['phase'],source_window=plan.get('host_source_window',w),source_available_tick=plan['available_tick'],instruction_clock=None if receipt is None else receipt['issued_tick'],status=status,reason=reason,begin_tick=ev['begin_tick']))
  return dict(events=events,research_event_simulation=self.research,bounded_intermediate_tick_scheduler_supported=True,availability_policy='two intermediate ticks: precommit latest already-available old plan; issue at due tick; unknown new starts blocked; never use new solution for past notice',safety_release=False,actual_dispatch_authorized=False,formal=False)

sys.path.insert(0,str(R/'guangdong_unified_event_agc_host_v1_20261113'))
from unified import RollingHost
class GeneralRollingHost(RollingHost):
 def __init__(self,*args,research_event_simulation=False,**kwargs):
  super().__init__(*args,**kwargs);self.hooks=GeneralEventHooks.attach(self.hooks,research_event_simulation=research_event_simulation)
 def read(self):return dict(super().read(),event_workflow=self.hooks.event_status())
