"""Stage-boundary, resumable orchestration. No mathematical code or native retries."""
from pathlib import Path
import json,os,sys,hashlib,time,importlib.util,contextlib,argparse

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,ensure_ascii=False)
def need(ok,msg):
 if not ok:raise ValueError(msg)
def load(app):
 app=Path(app).resolve();sys.path.insert(0,str(app));spec=importlib.util.spec_from_file_location('gd_unified_backend',app/'gd_unified_backend.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
@contextlib.contextmanager
def lock(run):
 # OS releases lock on process death; persistent evidence is not deleted.
 need(os.name=='posix','批处理依赖POSIX文件锁；Windows批功能暂不支持')
 import fcntl
 with (Path(run)/'BATCH_MUTEX').open('a')as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('another batch tick owns this run')
  try:yield
  finally:fcntl.flock(f,fcntl.LOCK_UN)
def steps(windows):
 out=[('price',-1)]
 for w in range(windows):
  out.extend([('rt',w),('handoff',w)])
  if w<=windows-2:out.append(('price',w))
  if w%4==3:out.append(('hour',w//4))
 return out
ASSUMPTIONS=['Research only, not operational dispatch, real meters, certified prices or payable bill.','Selected accepted DA plan conservatively protected; declared D/D+1 targets and synthetic publication remain explicit research assumptions.','Counterfactual no-deviation handoff; not actual execution or AC/AGC clearance.','5min RT, independent8-quarter PRICE publishing first15min, hourly energy-component accounting only.','Default PRICE: thermal DA bands + DA preceding complete15min first-ramp anchor (explicit unconfirmed research interpretation); storage current, delta0.1 and VRE eligibility remain research; no fallback.','PRICE uses same installed named research parameter generator; regional network default1000 is not authenticated Guangdong active override.','New named sample: VRE offer -50CNY/MWh, load/curtail1000, network regional default1000, accident200; delta0.1; allocation node39. Existing explicit parameter files are separately pinned in this plan.', 'Thermal pricing_stable eligibility subject to accepted-state checks; original400MW reserve remains. Primary/negative/group constraints follow explicit selected-case declarations, not invented qualification.', 'HiGHS installed limits and0.1% MIP gap unchanged; known failed/unknown native never retried automatically.']
POLICIES=('thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current','legacy_all_current_comparison','legacy_DA_storage_comparison')
def plan(api,da,run,windows,budget_s,policy=POLICIES[0]):
 need(os.name=='posix','批处理依赖POSIX文件锁；Windows批功能暂不支持')
 need(policy in POLICIES,'explicit supported PRICE policy required')
 need(windows in (4,96),'scope exactly4 or96 windows');need(60<=budget_s<=172800,'budget60..172800 seconds');run=api.bound(da,run);api.trusted()
 p=dict(schema='gd.unified.batch.plan.v1',da=str(Path(da).resolve()),run=str(run),windows=windows,budget_s=budget_s,steps=steps(windows),assumptions=ASSUMPTIONS,formal=False,once_native_authorization_required=True,source_pins={str(p):sha(p)for p in [api.CONFIG,api.B/'gd_unified_backend.py',api.B/'gd_unified_supervisor.py',run/'SHORT_CONTEXT.json',Path(da)/'ARTIFACTS.json',Path(__file__)]},time_budget_meaning='Wall-clock launch budget. Stop/pause only between stages; already launched supervisor may finish beyond budget. No completion-time guarantee.',native_stage_budget_upper_bound_s=windows*420+windows*60,requires_prepared_existing_run=True)
 p['PRICE_profile']=policy
 p['assumptions']=[*ASSUMPTIONS,'Selected PRICE profile: '+policy+'; no fallback or silent parameter replacement.']
 for f in (api.B/'gd_thermal_price_runtime').iterdir():
  if f.is_file()and f.suffix in ('.py','.json'):p['source_pins'][str(f)]=sha(f)
 p['existing_PRICE_parameters']={}
 for w in range(-1,windows-1):
  f=api.price_paths(run,w)['parameters'] if 'parameters' in api.price_paths(run,w) else api.price_paths(run,w)['root']/'PARAMETERS.json'
  if f.exists():
   p['source_pins'][str(f)]=sha(f);p['existing_PRICE_parameters'][str(w)]=read(f)
   cp=api.price_paths(run,w)['prepared']/'CONTEXT.json';ready=cp.parent/'READY.json'
   need(cp.exists()and ready.exists(),'existing parameters without complete prepared context; inspect, no automatic rebuild')
   ctx=read(cp);actual=ctx.get('thermal_reference_extension',{}).get('policy')
   if actual is None:actual='legacy_DA_storage_comparison'if read(f)['domain']=='DA_reference_R61_compatible_research'else'legacy_all_current_comparison'
   need(actual==policy,'existing PRICE policy differs; no silent reprice/mixed-profile batch')
   p['source_pins'][str(cp)]=sha(cp);p['source_pins'][str(ready)]=sha(ready)
 return p
def verify(api,p):
 need(p.get('PRICE_profile')in POLICIES,'batch plan lacks explicit PRICE profile');api.trusted();api.bound(p['da'],p['run'])
 for f,h in p['source_pins'].items():need(sha(f)==h,'DA/install/context/orchestrator changed: '+f)
def authorize(api,p,batch,confirmed=False):
 need(confirmed,'explicit once-only acceptance of complete plan required');verify(api,p);b=Path(batch).resolve();need(not b.exists(),'batch exists; resume, never recreate')
 with lock(p['run']):
  b.mkdir(parents=True);write(b/'PLAN.json',p);write(b/'AUTHORIZED.json',dict(plan_sha256=sha(b/'PLAN.json'),created_epoch=time.time(),native_retry=False));return dict(status='authorized',batch=str(b))
def control_event(batch,kind,confirmed=False):
 import uuid
 if kind=='resume':need(confirmed,'explicit resume of same authorized scope/budget required')
 p=Path(batch)/'CONTROL';p.mkdir(exist_ok=True);write(p/(str(time.time_ns())+'_'+uuid.uuid4().hex+'.json'),dict(kind=kind,epoch=time.time(),safe_boundary_only=True))
 return dict(status=kind+'_requested_no_process_killed')
def stop(batch):return control_event(batch,'stop')
def resume(batch,confirmed=False):return control_event(batch,'resume',confirmed)
def control_state(b,initial_epoch):
 events=sorted((b/'CONTROL').glob('*.json'))
 last=read(events[-1])if events else dict(kind='start',epoch=initial_epoch)
 return last['kind']=='stop',last['epoch']
def verify_handoff(api,run,w):
 # Same installed bridge validates entire predecessor carry, including midnight.
 entry=api.trusted();script=entry.parent/'price/trusted_rt_bridge.py'
 import subprocess
 q=subprocess.run([sys.executable,'-B',str(script),str(run),str(w+1),'carry'],capture_output=True,text=True,timeout=120)
 need(q.returncode==0,'handoff core replay failed: '+q.stderr[-500:]);return json.loads(q.stdout.strip().splitlines()[-1])
def tick(api,batch):
 b=Path(batch);p=read(b/'PLAN.json');a=read(b/'AUTHORIZED.json');need(a['plan_sha256']==sha(b/'PLAN.json'),'plan changed after authorization');verify(api,p)
 with lock(p['run']):
  for i,(kind,w)in enumerate(p['steps']):
   d=b/f'{i:03}_{kind}_{w}';done=d/'DONE.json'
   if done.exists():
    record=read(done)
    for f,h in record.get('pins',{}).items():need(sha(f)==h,'completed artifact changed: '+f)
    continue
   stopped,epoch=control_state(b,a['created_epoch'])
   if stopped:return dict(status='paused_safe_boundary',stage=[kind,w],inflight_may_finish=True)
   if time.time()>epoch+p['budget_s']:return dict(status='budget_paused_safe_boundary',stage=[kind,w],inflight_may_finish=True)
   d.mkdir(exist_ok=True);run=Path(p['run']);da=p['da'];intent=d/'INTENT.json'
   try:
    if kind in ('rt','price'):
     control=run/'ui_unified_control'/f'{kind}_{w}'
     if control.exists() and not(control/'EXIT.json').exists():
      # Strict core readers deliberately reject incomplete native receipts. Do not
      # invoke them while the original supervisor is still writing those receipts.
      launch=control/'LAUNCH.json';need(launch.exists(),'unknown supervisor launch outcome; inspect retained receipts')
      pid=read(launch)['pid']
      try:os.kill(pid,0)
      except ProcessLookupError:raise ValueError('supervisor no longer live without EXIT; unknown outcome, no retry')
      return dict(status='pending_or_unknown_native_no_retry',stage=[kind,w],control=str(control),supervisor_pid=pid)
    if kind=='rt':
     # Pure build is never repeated after an interrupted mutation.
     if not(run/f'W{w}').exists():
      need(not intent.exists(),'unknown interrupted RT build, inspect evidence');write(intent,dict(action='build'));api.build(da,run,w)
     proof=api.rt_proof(da,run,w);state=proof['core']
     if state['status']=='accepted':result=proof;pins=[run/f'W{w}/actual/ACCEPTED_PAIR.json']
     elif not state['pending_stages']:
      result=api.call(['rt','accept','--run',run,'--window',w]);need(api.rt_proof(da,run,w)['core']['status']=='accepted','strict RT accept required');pins=[run/f'W{w}/actual/ACCEPTED_PAIR.json']
     else:return native(api,p,d,kind,w,proof)
    elif kind=='price':
     paths=api.price_paths(run,w)
     if not paths['root'].exists():
      need(not intent.exists(),'unknown interrupted PRICE prepare');write(intent,dict(action='prepare_price'));api.prepare_price(da,run,w,domain=p['PRICE_profile'],confirmed=True)
     state=api.price_status(run,w)
     if state['status']=='completed':result=api.accept_price(run,w);pins=[paths['accepted']]
     else:return native(api,p,d,kind,w,None)
    elif kind=='handoff':
     h=run/f'handoff/W{w+1}'
     if not h.exists():
      need(not intent.exists(),'unknown interrupted handoff');write(intent,dict(action='handoff'));api.handoff(da,run,w,confirmed=True)
     result=verify_handoff(api,run,w);pins=[h/'HANDOFF.json',h/'CARRY.json']
    else:
     result=api.hour(da,run,w);pins=[run.parent/f'hours/H{w}/COMPLETED.json']
    write(done,dict(status='verified',result=result,pins={str(f):sha(f)for f in pins}));return dict(status='advanced',stage=[kind,w],completed_steps=i+1,total_steps=len(p['steps']))
   except Exception as e:
    err=d/'ERROR.json'
    if not err.exists():write(err,dict(error=str(e),automatic_retry=False))
    return dict(status='blocked_or_unknown_no_retry',stage=[kind,w],error=str(e))
  return dict(status='completed_research_batch',windows=p['windows'],formal=False,native_calls='inspect individual native receipts')
def native(api,p,d,kind,w,proof):
 run=Path(p['run']);control=run/'ui_unified_control'/f'{kind}_{w}';attempt=d/'LAUNCH_INTENT.json'
 if control.exists():
  if (control/'EXIT.json').exists():return dict(status='blocked_or_unknown_no_retry',stage=[kind,w],reason='Supervisor exited without strict completed state; inspect receipts')
  return dict(status='pending_or_unknown_native_no_retry',stage=[kind,w],control=str(control))
 need(not attempt.exists(),'launch intent exists without reliable outcome; no retry')
 write(attempt,dict(stage=[kind,w],authorized_plan_sha256=sha(d.parent/'PLAN.json')))
 result=api.launch(kind,p['da'],run,w,confirmed=True,proof=proof);write(d/'LAUNCH_RETURN.json',result)
 return dict(status='pending_or_unknown_native_no_retry',stage=[kind,w],launch=result)
def run_loop(api,batch,poll_s=3):
 while True:
  z=tick(api,batch);print(json.dumps(z),flush=True)
  if z['status']=='advanced':continue
  if z['status']=='pending_or_unknown_native_no_retry':
   # A lost supervisor is not deemed complete or restarted; poll stays budget-bounded.
   time.sleep(poll_s);continue
  return z
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['plan','start','tick','run','stop','resume']);a.add_argument('--app');a.add_argument('--da');a.add_argument('--rt-run');a.add_argument('--windows',type=int,choices=[4,96]);a.add_argument('--budget-seconds',type=int,default=7200);a.add_argument('--policy',choices=POLICIES,default=POLICIES[0]);a.add_argument('--plan');a.add_argument('--batch',required=True);a.add_argument('--confirm-research-batch',action='store_true');x=a.parse_args()
 if x.action=='stop':z=stop(x.batch)
 elif x.action=='resume':z=resume(x.batch,x.confirm_research_batch)
 else:
  api=load(x.app)
  if x.action=='plan':z=plan(api,x.da,x.rt_run,x.windows,x.budget_seconds,x.policy);write(x.plan,z)
  elif x.action=='start':z=authorize(api,read(x.plan),x.batch,x.confirm_research_batch)
  elif x.action=='tick':z=tick(api,x.batch)
  else:z=run_loop(api,x.batch)
 print(json.dumps(z,ensure_ascii=False))
