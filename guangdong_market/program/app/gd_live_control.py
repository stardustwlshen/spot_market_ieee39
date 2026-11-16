"""UI boundary for one same-run complete four-window live session."""
from pathlib import Path
import subprocess,sys,json,hashlib,re,os
from gd_price_quality import from_accepted as price_quality
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def write(p,z):
 payload=json.dumps(z,indent=2,allow_nan=False)
 with Path(p).open('x')as f:f.write(payload)
def sources():
 m=read(B/'LIVE_MARKET_MANIFEST.json')
 for name,h in m.items():need(sha(B/name)==h,'live source changed '+name)
 return {str(B/name):h for name,h in m.items()}
def location(run,name):
 need(re.fullmatch(r'[A-Za-z0-9_-]{1,50}',name),'名称仅字母数字下划线连字符');return Path(run).resolve().parent/'live_market_sessions'/name
def plan(da,run,name,event_simulation=False,counterfactual_continue=False,ac_once=False,event_price_profile=False,stop_request=None,windows=4,nextday_selection=None,nextday_explicit=False,reference_revision=False,post_scuc_planning=False,post_declarations=None,post_scuc_day=False,candidate_loss_once=False,finite_h1=False,finite_hourly=False,pricing_clock_path=None,dispatch_mode="provincial8",pricing_delta=.1,checkpoint_config=None):
 need(not (counterfactual_continue or ac_once or candidate_loss_once),'AC/reactive/counterfactual options are outside active_power_dc scope; start a new DC run without these options')
 from gd_dispatch_mode import supported
 mode_fields=supported(dispatch_mode)
 from gd_live_market_runtime.pricing_delta import normalize,policy
 pricing_delta=normalize(pricing_delta);delta_policy=policy(pricing_delta)
 import gd_unified_backend as api
 api.bound(da,run);api.trusted();pins=sources();r=Path(run).resolve()
 clock_path=str(Path(pricing_clock_path).resolve()) if pricing_clock_path is not None else None
 if clock_path is not None:
  clock_policy=read(clock_path);need(clock_policy.get('schema') in ('gd.price.research_clock_policy.v1','gd.price.input_availability.schedule.v1','gd.price.input_availability.declaration.v1'),'unsupported pricing information clock schema');pins[clock_path]=sha(clock_path)
 p=subprocess.run([sys.executable,'-B',str(B/'gd_live_worker.py'),'validate',str(r),str(Path(da).resolve()),str(int(event_simulation)),str(int(counterfactual_continue)),str(int(ac_once)),str(int(event_price_profile)),json.dumps(stop_request),str(windows),json.dumps(nextday_selection),str(int(nextday_explicit)),str(int(reference_revision)),str(int(post_scuc_planning)),json.dumps(post_declarations),str(int(post_scuc_day)),str(int(candidate_loss_once)),str(int(finite_h1)),str(int(finite_hourly))],capture_output=True,text=True)
 need(p.returncode==0,p.stderr or p.stdout or 'live source validation failed');z=json.loads(p.stdout.strip().splitlines()[-1]);pins.update({str(f):sha(f)for f in [r/'SHORT_CONTEXT.json',r/'seed/case.json']});pins.update(z.get('nextday_preflight',{}).get('source_pins',{}));pins.update((z.get('post_preflight')or{}).get('source_pins',{}));pins.update((z.get('post_day_preflight')or{}).get('source_pins',{}))
 z.update(**mode_fields)
 from gd_live_market_runtime.durable_entry import review as persistence_review
 durable=persistence_review(location(r,name),checkpoint_config)
 if durable.get("config_path"):pins[durable["config_path"]]=durable["config_sha256"]
 z.update(durable_checkpoint=durable,execution_attempt_id=name,logical_source_branch=str(location(r,name)/"pipeline/dispatch"))
 z.update(pricing_delta=pricing_delta,pricing_delta_policy=delta_policy)
 z.update(pricing_clock_path=clock_path,pricing_clock_status='explicit_declaration_selected_pending_actual_input_binding' if clock_path else 'unknown_new_PRICE_native_blocked',pricing_clock_scope='research-only input availability; computation/publication times separate and not inferred')
 return dict(**z,da=str(Path(da).resolve()),run=str(r),output=str(location(r,name)),source_pins=pins)
def start(p,confirmed=False):
 need(confirmed is True,'explicit complete displayed stage budget required');need(p==plan(p['da'],p['run'],Path(p['output']).name,p['research_event_simulation'],p['counterfactual_continue'],p['ac_once'],p['event_price_profile'],p.get('stop_request'),p.get('windows',4),p.get('nextday_selection'),p.get('nextday_explicit',False),p.get('reference_revision',False),p.get('post_scuc_planning',False),p.get('post_declarations'),p.get('post_scuc_day',False),p.get('candidate_loss_once',False),p.get('finite_h1',False),p.get('finite_hourly',False),p.get('pricing_clock_path'),p.get('dispatch_mode','provincial8'),p.get('pricing_delta',.1),(p.get('durable_checkpoint')or{}).get('config_path')),'reviewed plan changed');out=Path(p['output']);out.parent.mkdir(parents=True,exist_ok=True);out.mkdir();write(out/'PLAN.json',p);write(out/'INTENT.json',dict(unknown_is_not_zero_calls=True))
 from gd_resume_control import acquire
 lock=acquire(out)if os.name=='posix'else None
 try:
  with(out/'stdout.log').open('ab')as f:
   if os.name=='nt':
    from gd_process_guard import launch_windows
    proc=launch_windows([sys.executable,'-B',str(B/'gd_live_worker.py'),'run',str(out)],out,out/'PROCESS.guard',f,env={'PYTHONDONTWRITEBYTECODE':'1'})
   else:
    proc=subprocess.Popen([sys.executable,'-B',str(B/'gd_live_worker.py'),'run',str(out)],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(lock.fileno(),),env={**os.environ,'GD_PIPELINE_LOCK_FD':str(lock.fileno()),'PYTHONDONTWRITEBYTECODE':'1'})
 except Exception as exc:
  # Windows handshake can fail after launch; a recorded launch is NOT zero calls.
  uncertain=os.name=='nt' or not isinstance(exc,OSError) or (out/'LAUNCH_PID.json').exists()
  from gd_process_guard import atomic_marker
  try:atomic_marker(out/('LAUNCH_ERROR.json' if uncertain else 'EXIT.json'),dict(status='launch_unknown' if uncertain else 'launch_failed',error=str(exc),native_calls=None if uncertain else 0,automatic_retry=False))
  except OSError:pass  # Preserve the original launch exception, never invent a save.
  if uncertain:return dict(status='launch_unknown',output=str(out),warning='启动状态未确认，保留本次任务以免重复启动：'+str(exc),publication_allowed=False)
  raise
 finally:
  if lock:lock.close()  # Child retains inherited ownership after a successful Popen.
 from gd_process_guard import process_namespace
 try:write(out/'PID.json',dict(pid=proc.pid,process_namespace=process_namespace()))
 except OSError as exc:
  # A live child now exists: return its output so UI retains the running attempt.
  # Do not emit EXIT or invite another launch after a bookkeeping-only failure.
  return dict(status='started_record_warning',output=str(out),pid=proc.pid,warning='进程已启动，但PID记录保存失败：'+str(exc),publication_allowed=False)
 return dict(status='started',output=str(out),publication_allowed=False)

def status(out):
 out=Path(out)
 if not out.exists():return dict(status='not_started',native_calls=0)
 p=read(out/'PLAN.json');need(p['output']==str(out.resolve()),'output binding');sources()
 for f,h in p['source_pins'].items():need(sha(f)==h,'input/source changed')
 dispatch=read(out/'pipeline/dispatch/RESULT.json')if(out/'pipeline/dispatch/RESULT.json').exists()else{}
 prices=[]
 for q in range(p.get('windows',4)):
  f=out/f'pipeline/price/q{q}/ACCEPTED.json'
  if f.exists():
   z=read(f);need(z['context']['short_run']==str((out/'pipeline/dispatch').resolve())and (z['context']['boundary_mode']==p['profile']or(p.get('event_price_profile')and z['context']['boundary_mode']==p['selected_event_profile']and z['context']['price_producer']=='gd.live_host.PRICE.v4_event')),'quarter from another branch/profile');prices.append(dict(quarter=q,price_quality=price_quality(f),raw_prices=z['raw_prices'],zero_vre_reference_normalization=z['context'].get('zero_vre_reference_normalization'),safety_release=False))
 end=read(out/'EXIT.json')if(out/'EXIT.json').exists()else None
 from gd_process_guard import observe_run
 worker_state=dict(state='exited') if end else observe_run(out)
 hour=read(out/'pipeline/price/HOUR0.json')if(out/'pipeline/price/HOUR0.json').exists()else None
 if hour:need(hour['branch']==str((out/'pipeline/dispatch').resolve()),'hour from another branch')
 hours=[read(f)for f in sorted((out/'pipeline/price').glob('HOUR*.json'),key=lambda p:int(p.stem[4:]))]
 for h in hours:need(h['branch']==str((out/'pipeline/dispatch').resolve()),'hour from another branch')
 safety=[read(f)for f in sorted((out/'pipeline').glob('SAFETY_W*.json'))]
 eventfile=out/'pipeline/EVENTS.blocked.json'if(out/'pipeline/EVENTS.blocked.json').exists()else out/'pipeline/EVENTS.json'
 events=read(eventfile)if eventfile.exists()else {}
 numerical=[dict(window=row.get('window'),SCED_response_kind=row.get('proof',{}).get('SCED_response_kind','legacy/source-specific evidence'),native_dual_passed=row.get('proof',{}).get('native_dual_passed'),dual_certificate=row.get('proof',{}).get('dual_certificate'),safety_release=False)for row in dispatch.get('results',[])]
 from gd_agc_results import scan as scan_agc
 agc_results=scan_agc(out) if dispatch else None
 from gd_candidate_status import read_blocks
 candidate_blocks=read_blocks(out,dispatch) if dispatch else []
 from gd_live_market_runtime.durable_entry import progress as persistence_progress
 persistence=persistence_progress(out,p.get('durable_checkpoint'))
 return dict(worker_state=worker_state,persistence=persistence,model_scope=p.get('model_scope','historical_unspecified'),ac_assessment=p.get('ac_assessment','historical_unspecified'),reactive_power_modeled=p.get('reactive_power_modeled'),candidate_blocks=candidate_blocks,agc_results=agc_results,numerical=numerical,safety=safety,events=events,status='worker_exited' if end else ('worker_interrupted' if worker_state['state']=='interrupted' else 'running_or_unknown'),exit=end,completed_windows=dispatch.get('completed_windows',0),quarters=prices,hour=hour,hours=hours,hour_exports=[dict(path=str(f.resolve()),sha256=sha(f))for f in sorted((out/'pipeline/price').glob('HOUR*.json'),key=lambda p:int(p.stem[4:]))],verification_scope='same output branch/source-pinned live worker receipts; not a new independent raw/safety certification',publication_allowed=False,dispatch_authorized=False,durable_restart_supported=False)

def future_state(out):
 import time
 out=Path(out);f=out/'pipeline/FUTURE_CAP/STATE.json'
 if not f.exists():return None
 z=read(f)
 z['interactive']=z['status']in ('waiting_proposal','waiting_confirmation','waiting_execution_confirmation')and time.time()<z['expires_at_epoch']and not(out/'EXIT.json').exists()
 return z

def future_command(out,kind,*,request=None,confirmation_digest=None,confirmed=False):
 raise ValueError('Legacy AC/cap-correction commands are disabled in active_power_dc scope; historical files remain read-only')
 import time,os
 out=Path(out);sources();z=future_state(out)
 need(z and z['interactive'],'worker exited, expired or unknown; no live restore')
 # A stale file alone cannot establish a live worker. Process identity is checked;
 # only the still-running worker can validate/act upon the nonce-bound message.
 pid=read(out/'PID.json')['pid']
 try:os.kill(pid,0)
 except OSError:raise ValueError('worker is not live; no restart')
 if kind=='proposal':
  need(z['status']=='waiting_proposal','proposal already used');q=dict(nonce=z['nonce'],request=request)
 elif kind=='confirm':
  need(confirmed is True and z['status']=='waiting_confirmation','explicit later confirmation required')
  need(confirmation_digest==z['registration_plan']['confirmation_digest'],'reviewed declaration changed');q=dict(nonce=z['nonce'],confirmation_digest=confirmation_digest)
 elif kind=='execute':
  need(confirmed is True and z['status']=='waiting_execution_confirmation','separate explicit two-call approval required')
  need(confirmation_digest==z['registration_plan']['confirmation_digest'],'wrong candidate');q=dict(nonce=z['nonce'],confirmation_digest=confirmation_digest,execute_native=True)
 else:raise ValueError('unsupported live command')
 target=out/'pipeline/FUTURE_CAP'/f'{kind}.json'
 # exclusive file creation means no duplicate/replacement commands
 write(target,q);return dict(status='request_sent_not_yet_processed',native_calls=0)


def stop_choices(da,run,windows=4):
 import gd_unified_backend as api
 api.bound(da,run);sources()
 p=subprocess.run([sys.executable,'-B',str(B/'gd_live_worker.py'),'stops',str(Path(run).resolve()),str(Path(da).resolve()),str(windows)],capture_output=True,text=True)
 need(p.returncode==0,p.stderr or p.stdout or 'stop source inspection failed');return json.loads(p.stdout.strip().splitlines()[-1])


def create_nextday(da,run,name,available_at,*,confirmed=False):
 need(confirmed is True,'explicit independent D+1 declaration confirmation required');sources()
 p=subprocess.run([sys.executable,'-B',str(B/'gd_live_worker.py'),'declare_nextday',str(Path(run).resolve()),str(Path(da).resolve()),name,available_at,'--confirmed-research'],capture_output=True,text=True)
 need(p.returncode==0,p.stderr or p.stdout or 'declaration failed');return json.loads(p.stdout.strip().splitlines()[-1])

