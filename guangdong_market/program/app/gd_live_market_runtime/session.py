"""One live authority and 12-call budget; no JSON-driven restore or fallback."""
from pathlib import Path
import sys,json,subprocess,tempfile,hashlib,re
B=Path(__file__).resolve().parent;ENGINE=B.parent.parent/'engines/guangdong_short_unified_v6'
from source import Snapshot,RollingHost,sha,read,e
from source_ledger import LedgerSource as TypedSource
from hour_scope import hour as hour_scope,plan as scope_plan
POLICY='thermal_DA_DA_previous_quarter_anchor_research'
from state_adapter import PROFILE as EVENT_PROFILE
def require_space(output):
 import shutil
 e.need(shutil.disk_usage(output).free>=256*1024*1024,'low disk space before next native stage; stopped, no automatic retry')

def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2)
def call(args,log):
 p=subprocess.run([sys.executable,'-B',*map(str,args)],capture_output=True,text=True);Path(log).write_text(p.stdout+'\n'+p.stderr);e.need(p.returncode==0,'stage blocked; inspect '+str(log)+'; no retry');return json.loads(p.stdout.strip().splitlines()[-1])
def _first_hour_plan(*,research_event_simulation=False,counterfactual_continue=False,ac_once=False,event_price_profile=False):return dict(event_price_profile=event_price_profile,selected_event_profile=EVENT_PROFILE if event_price_profile else None,ac_once=ac_once,research_event_simulation=research_event_simulation,counterfactual_continue=counterfactual_continue,AC_actual_calls=int(ac_once),AC_actual_points=[{'window':0,'point':'candidate first 5-minute domain point BEFORE handoff (normally origin+15min)','method':'original Q0 / declared proportional-load-Q research snapshot'}]if ac_once else[],AC_automatic_new_run_supported=True,AC_scope='explicit one-call candidate AC before handoff; declared supported five-thermal inventory required; failure/missing checks block default handoff; no hour/day safety certificate',safety_default='stop subsequent computation without explicit counterfactual continuation; never publish',future_tightening_pause_seconds=600,optional_separately_authorized_future_pair_calls=2,optional_pair_AC_PRICE_calls=0,default_native_calls=0,maximum_native_calls=12,sequence=['seed PRICE','W0 pair / q1 PRICE','W1 pair / q2 PRICE','W2 pair / q3 PRICE','W3 pair / H0 arithmetic'],SCUC_calls=4,SCED_calls=4,PRICE_calls=4,SCUC_seconds=3600,SCED_seconds=3600,SCED_precision={'primal_feasibility_tolerance':1e-10,'dual_feasibility_tolerance':1e-10,'ipm_optimality_tolerance':1e-10,'run_crossover':'on','automatic_fallback':False},PRICE_seconds=3600,SCED_solver='ipm_with_crossover_explicit_once',threads=1,mip_gap=.01,solver_time_limit_scope="each_native_solve",process_grace_seconds=300,profile=POLICY,automatic_fallback=False,durable_restart_supported=False,formal=False,publication_allowed=False)
def plan(*,windows=4,reference_revision=False,post_scuc_planning=False,post_scuc_day=False,candidate_loss_once=False,finite_h1=False,finite_hourly=False,research_event_simulation=False,counterfactual_continue=False,ac_once=False,event_price_profile=False):
 from finite_option import validate
 validate(enabled=finite_h1,hourly=finite_hourly,windows=windows,post_scuc_planning=post_scuc_planning,post_scuc_day=post_scuc_day,reference_revision=reference_revision,candidate_loss_once=candidate_loss_once)
 e.need(not post_scuc_day or(windows==96 and not reference_revision and not post_scuc_planning),'post96 requires96 and independent mode');e.need(not post_scuc_planning or (windows==4 and not reference_revision),'post-SCUC planning only4 and incompatible with289 reference revision');e.need(not reference_revision or windows==96,'D1 reference revision requires96');scope=scope_plan(windows);z=_first_hour_plan(research_event_simulation=research_event_simulation,counterfactual_continue=counterfactual_continue,ac_once=ac_once,event_price_profile=event_price_profile)
 z.update(post_scuc_day=post_scuc_day,post_scuc_planning=post_scuc_planning,post_scuc_scope='five-qualified-plant saturated50 planning NOT published awards'if post_scuc_planning else None,reference_revision=reference_revision,reference_SCED_calls=int(reference_revision or post_scuc_day),windows=windows,hours=scope['hours'],maximum_native_calls=scope['maximum_native_calls']+int(reference_revision or post_scuc_day),SCUC_calls=windows,SCED_calls=windows,PRICE_calls=windows,sequence=['selected DA seed PRICE',str(windows)+' causal RT pairs; PRICE after every pair except final; exact12-cell arithmetic at each4th handoff'],full_day_native_validated=False,requires_explicit_nextday_selection=windows==96,extra_budgets_not_included={'optional_AC':int(ac_once),'optional_separately_confirmed_future_pair':2})
 e.need(not candidate_loss_once or ac_once,'loss correction requires explicit initial AC');z.update(finite_h1=bool(finite_h1),finite_hourly=bool(finite_hourly),candidate_loss_once=bool(candidate_loss_once),maximum_native_calls=z['maximum_native_calls']+int(candidate_loss_once),SCED_calls=z['SCED_calls']+int(candidate_loss_once),AC_actual_calls=int(ac_once)+int(candidate_loss_once),loss_correction_budget={'SCED':int(candidate_loss_once),'AC':int(candidate_loss_once),'automatic_retry':False,'scope':'original c10e / AGC50 / reserve400 research only'})
 return z
class PriceSession:
 def __init__(self,host,output,*,event_price_profile=False,windows=4):
  self.windows=scope_plan(windows)['windows']
  self.selected_profile=EVENT_PROFILE if event_price_profile else None
  self.source=TypedSource(host,windows=self.windows);self.host=host;self.output=Path(output).resolve();e.need(not self.output.exists(),'new price session required');self.output.mkdir(parents=True);self.prepared={};self.accepted={};self.execution={};self.reference_overlay=None
 def prepare(self,w):
  e.need(w not in self.prepared,'already prepared; no overwrite');snap=self.source.snapshot(w);z=snap.verify();sf=self.output/f'q{w+1}_SOURCE.json'
  if w==-1 and getattr(self.host.hooks,'post_scuc',None) is not None:
   z['AGC_forecast_semantics']={'seed':'original selected-DA forecast; only H0 initial issued prefix; future planning is NOT published AGC'}
  if w==94 and self.reference_overlay is not None:
   sys.path.insert(0,str(B.parent/'gd_reference_runtime'));from reference_session import ReferenceOverlay
   e.need(type(self.reference_overlay)is ReferenceOverlay,'registered reference capability required, not JSON/duck type')
   ref=self.reference_overlay.verify(self.host);z['reference_revision']=ref;z['source_pins'].update(ref['source_pins'])
  write(sf,z);out=self.output/f'q{w+1}'
  result=call([B/'math_prepare.py',sf,sha(sf),out,self.selected_profile or 'NONE'],self.output/f'q{w+1}_prepare.log');p=out/'prepared';pins={str(f):sha(f)for f in [sf,*p.glob('*.json')]};self.prepared[w]=(snap,p,pins);return result
 def execute(self,w,*,execute_native=False):
  e.need(execute_native is True,'explicit PRICE permission required');e.need(w in self.prepared and w not in self.accepted,'prepared unaccepted quarter required');snap,p,pins=self.prepared[w];snap.verify()
  for f,h in pins.items():e.need(sha(f)==h,'prepared live source drift')
  e.need(not(p/'native_once').exists(),'prior/unknown native occurrence; never retry')
  call([ENGINE/'price/program.py','native','--prepared',p,'--execute-native'],p.parent/'native.log');a=p.parent/'ACCEPTED.json';call([ENGINE/'price/program.py','accept','--prepared',p,'--output',a],p.parent/'accept.log');z=read(a);e.need(z['context']['run_id']==self.host.branch_id and z['context']['boundary_mode']==read(p/'CONTEXT.json')['boundary_mode'],'PRICE source/profile changed');self.accepted[w]=(a,sha(a),snap);return dict(quarter=w+1,status='accepted',native_calls=1,source=str(a),safety_release=False)
 def adopt_readonly(self,w,accepted):
  """Read verified original prices only for an explicitly read-only historical host.
  Does not write ACCEPTED or claim a newly solved/accepted run.
  """
  e.need(getattr(self.host,'source_kind','')=='historical_raw_replay_no_new_acceptance','history adapter only');e.need(w not in self.accepted,'duplicate quarter');snap=self.source.snapshot(w);d=snap.verify();a=Path(accepted).resolve();z=read(a);ctx=z['context'];e.need(ctx['short_run']==d['branch']and ctx['run_id']==d['branch_id']and ctx['selected_DA']==d['selected_DA']and ctx['SCED_response_sha256']==d['accepted']['SCED_response_sha256']and ctx['day_quarter']==w+1,'old result from wrong live source');e.need(ctx['boundary_mode']==POLICY,'different profile rejected')
  call([ENGINE/'price/program.py','read','--accepted',a],self.output/f'q{w+1}_historical_read.log');self.accepted[w]=(a,sha(a),snap);return dict(readonly_original=True,native_calls=0,new_acceptance=False)
 def hour(self,index=0):
  scope=hour_scope(index,self.windows);needed=set(scope['source_windows'])
  e.need(needed.issubset(self.accepted),'four explicit accepted quarters; missing prices never filled');snap=self.source.snapshot(scope['handoff_window']);z=snap.verify();e.need(z['executed_end']==scope['executed_end'] and z['issued_end']==scope['issued_end'],'incomplete declared hour execution');vals={k:v for k,v in z['values'].items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])in scope['cells']};e.need(index not in self.execution,'hour already produced; read evidence only')
  quarters=[]
  for w in scope['source_windows']:
   a,h,s=self.accepted[w];s.verify();e.need(sha(a)==h,'accepted price changed');quarters.append(a)
  v=dict(schema='gd.live_host.execution_view.v3',run_id=z['branch_id'],run=z['branch'],base_run=z['base_run'],selected_DA=z['selected_DA'],hour=index,origin=z['request']['origin'],executed_end=scope['executed_end'],issued_end=scope['issued_end'],cells=scope['cells'],values=vals,SCED_sources={str(r['window']):r['SCED_response_sha256']for r in z['reports']},source_pins=z['source_pins'],formal_bill=None,safety_release=False,excluded_issued_ticks=scope['excluded_issued_ticks']);f=self.output/f'EXECUTION_H{index}.json';write(f,v);out=self.output/f'HOUR{index}.json'
  result=call([B/'math_hour.py',z['selected_DA'],f,*quarters,out],self.output/f'hour{index}.log');self.execution[index]=(out,sha(out));write(self.output/f'SETTLEMENT_H{index}.json',self.settlement(index));return result
 def hour_receipt(self,index=0):
  e.need(index in self.execution,'no live completed hour receipt')
  out,digest=self.execution[index];e.need(sha(out)==digest,'hour export drift')
  scope=hour_scope(index,self.windows);z=self.source.snapshot(scope['handoff_window']).verify()
  e.need(z['branch_id']==self.host.branch_id,'live hour source identity changed')
  for w in scope['source_windows']:
   a,h,s=self.accepted[w];s.verify();e.need(sha(a)==h,'live quarter changed')
  result=read(out);e.need(result['run_id']==self.host.branch_id,'hour branch mismatch')
  return dict(path=str(out),sha256=digest,run_id=self.host.branch_id,hour=index,formal_bill=None)
 def settlement(self,index=0,fields=None):
  sys.path.insert(0,str(B.parent));from gd_settlement_bridge import from_live,components
  return components(from_live(self,index),fields)
class Pipeline:
 def __init__(self,run,output,*,research=False,research_event_simulation=False,counterfactual_continue=False,ac_once=False,event_price_profile=False,stop_request=None,windows=4,nextday_selection=None,nextday_explicit=False,reference_revision=False,post_scuc_planning=False,post_declarations=None,post_scuc_day=False,candidate_loss_once=False,finite_h1=False,finite_hourly=False):
  e.need(not post_scuc_day or(windows==96 and not reference_revision and not post_scuc_planning and not post_declarations),'post96 independent scope');self.post_scuc_day=post_scuc_day
  self.windows=scope_plan(windows)['windows'];e.need(not reference_revision or windows==96,'reference revision only96');self.reference_revision=reference_revision;self.reference=None;self.post_scuc_planning=post_scuc_planning;self.post_controller=None
  self.event_price_profile=event_price_profile
  e.need(research is True,'explicit hypotheses required');self.output=Path(output).resolve();e.need(not self.output.exists(),'fresh complete pipeline only');self.output.mkdir(parents=True);self.event_simulation=research_event_simulation;self.counterfactual_continue=counterfactual_continue;self.ac_once=ac_once;self.candidate_loss_once=bool(candidate_loss_once);self.finite_h1=bool(finite_h1);self.finite_hourly=bool(finite_hourly)
  sys.path.insert(0,str(B.parent/'lifecycle_runtime/guangdong_clocked_general_host_v1_20261113'));from clocked_host import ClockedStopPermissionHost
  from v6_runtime import V6Session
  from day_selection import preflight
  from post_selection import preflight as post_preflight
  post_selected=post_preflight(V6Session(run,research_fixed_curve_events=True),enabled=post_scuc_planning,declarations=post_declarations,windows=windows,reference_revision=reference_revision)
  selected=preflight(V6Session(run,research_fixed_curve_events=True),nextday_selection,explicit=nextday_explicit,reference_revision=reference_revision,post_scuc_day=post_scuc_day)if windows==96 else None
  if post_scuc_day:
   from gd_post_scuc_runtime.day_calendar import prepare as calendar_prepare
   inventory=calendar_prepare(run,self.output/'POST_DAY_DECLARATIONS',nextday_path=selected['declaration_path'],research=True)
   post_selected=dict(declarations=inventory['declarations'],source_pins=inventory['source_pins'],inventory_path=inventory['inventory_path'])
  import importlib.util
  safety_dir=B.parent/'lifecycle_runtime/guangdong_safety_session_v1_20261113';sys.path.insert(0,str(safety_dir));spec=importlib.util.spec_from_file_location('_gd_safety_session',safety_dir/'safety_session.py');safety_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(safety_module);SafetySession=safety_module.SafetySession
  from v6_runtime import V6Session
  from finite_option import preflight as finite_preflight
  finite_preflight(V6Session(run,research_fixed_curve_events=True),enabled=finite_h1,hourly=finite_hourly,windows=windows,post_scuc_planning=post_scuc_planning,post_scuc_day=post_scuc_day,reference_revision=reference_revision,candidate_loss_once=candidate_loss_once)
  from loss_option import preflight as loss_preflight
  loss_preflight(V6Session(run,research_fixed_curve_events=True),enabled=candidate_loss_once,ac_once=ac_once)
  write(self.output/'PLAN.json',plan(finite_h1=finite_h1,finite_hourly=finite_hourly,candidate_loss_once=candidate_loss_once,windows=windows,reference_revision=reference_revision,post_scuc_planning=post_scuc_planning,post_scuc_day=post_scuc_day,research_event_simulation=research_event_simulation,counterfactual_continue=counterfactual_continue,ac_once=ac_once,event_price_profile=event_price_profile));self.host=ClockedStopPermissionHost(run,self.output/'dispatch',mode='native',research=True,research_event_simulation=research_event_simulation,windows=windows,nextday_agc_declaration=selected['declaration_path']if selected else None,nextday_scenario=selected['scenario']if selected else None);self.host.session.registry['source_pins'].update({str(f):sha(f)for f in B.glob('*.py')});self.prices=PriceSession(self.host,self.output/'price',event_price_profile=event_price_profile,windows=windows);self.safety=SafetySession(self.host)
  from before_handoff_gate import BeforeHandoffGate
  self.host.before_handoff_gate=BeforeHandoffGate(self.host,counterfactual_research=counterfactual_continue)
  self.host.candidate_safety=self.safety;self.host.candidate_ac_once=bool(ac_once);self.host.candidate_loss_once=bool(candidate_loss_once)
  if post_selected:
   from gd_post_scuc_runtime.controller import Controller
   self.post_controller=Controller(self.host,post_selected['declarations'],research=True,saturated_planning=True,day_calendar=post_selected.get('inventory_path'),finite_h1=finite_h1,finite_hourly=finite_hourly);self.host.session.registry['source_pins'].update(post_selected['source_pins'])
  from stop_choices import inspect,validate_request
  inventory=inspect(self.host.session,windows=windows);
  if selected:self.host.session.registry['source_pins'].update(selected['source_pins'])
  request=validate_request(inventory,stop_request);write(self.output/'STOP_CHOICES.json',inventory)
  if request is not None:self.host.stop_permissions.register_from_selected_da(request['unit'],request['absolute_tick'],declaration_name=request['declaration_name'],research_stop_override=True,official=False)
  write(self.output/'STOP_PERMISSION.json',self.host.stop_permissions.read())
  if counterfactual_continue:self.safety.allow_counterfactual_research(explicit=True)
 def plan_reclear(self,request):
  e.need(request['branch_id']==self.host.branch_id,'reclear wrong host')
  return dict(status='blocked',reason='BLOCKED_MISSING_MODEL_ADAPTER',branch_id=self.host.branch_id,candidate_id=request['candidate_id'],actual_native_calls=0,actual_AC_calls=0,proposed_native_budget=0,required=['same-run material input adapter','new accepted raw model and new source-bound safety verification'],publication_allowed=False)
 def after_pair(self,w):
  if getattr(self.host,'before_handoff_gate',None) is not None:
   self.safety.attach_candidate_ac_after_handoff(w)
  elif self.ac_once and w==0:self.safety.run_ac(w,self.output/'AC_W0',execute_ac=True)
  report=self.safety.inspect(w)
  if w in self.safety.failures:report['reclear']=self.safety.propose_reclear(w,{},declaration_path=None)
  write(self.output/f'SAFETY_W{w}.json',report)
  events=self.host.read()['event_workflow'];tmp=self.output/'EVENTS.next.json';write(tmp,events);tmp.replace(self.output/'EVENTS.json')
  if not(report.get('publication_allowed')is True or self.counterfactual_continue):
   from pause_control import wait_for_future_candidate
   wait_for_future_candidate(self,w,report)
   raise ValueError('system safety blocked; live pause ended, no automatic restart or execution')
  return report
 def run(self,*,execute_native=False):
  if not execute_native:return plan(finite_h1=self.finite_h1,finite_hourly=self.finite_hourly,candidate_loss_once=self.candidate_loss_once,windows=self.windows,reference_revision=self.reference_revision,post_scuc_planning=self.post_scuc_planning,post_scuc_day=self.post_scuc_day,research_event_simulation=self.event_simulation,counterfactual_continue=self.counterfactual_continue,ac_once=self.ac_once,event_price_profile=self.event_price_profile)
  from lifecycle_runtime.gd_sced_precision_runtime.bridge import install as precision_install
  self.host.sced_precision_policy=precision_install(self.host.session.program)
  self.host.session.registry['source_pins'].update(self.host.sced_precision_policy.pins)
  from lifecycle_runtime.worker_proof_memo import install_for_pipeline
  install_for_pipeline(self)
  require_space(self.output);self.prices.prepare(-1);self.prices.execute(-1,execute_native=True)
  hours=[]
  for w in range(self.windows):
   if w==94 and self.reference_revision:
    sys.path.insert(0,str(B.parent/'gd_reference_runtime'));from reference_session import ReferenceSession
    require_space(self.output);self.reference=ReferenceSession(self.host,self.output/'D1_REFERENCE',research_reference_revision=True);self.reference.prepare();self.prices.reference_overlay=self.reference.execute(execute_native=True)
   require_space(self.output);self.host.prepare();self.host.run(execute_native=True)
   self.after_pair(w)
   if w%4==3:hours.append(self.prices.hour(w//4))
   if w==94 and self.post_scuc_day:
    sys.path.insert(0,str(B.parent/'gd_reference_runtime'));from post_reference import PostReferenceSession
    require_space(self.output);self.reference=PostReferenceSession(self.host,self.output/'POST_D1_REFERENCE',research_reference_revision=True);self.reference.prepare();self.prices.reference_overlay=self.reference.execute(execute_native=True)
   if w<self.windows-1:require_space(self.output);self.prices.prepare(w);self.prices.execute(w,execute_native=True)
  z=hours[0]if self.windows==4 else dict(hours=hours,completed_hours=len(hours),native_calls=3*self.windows+int(self.reference_revision or self.post_scuc_day),not_formal=True)
  write(self.output/'COMPLETE.json',dict(status='complete_research',native_calls=3*self.windows+int(self.reference_revision or self.post_scuc_day),windows=self.windows,AC_calls=self.safety.ac_calls,branch_id=self.host.branch_id,hour=z,formal=False,safety_release=False));return z
