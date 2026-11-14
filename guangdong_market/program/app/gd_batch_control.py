"""UI-only orchestration wrapper. No native execution during plan/status."""
from pathlib import Path
import os,sys,json,time,subprocess,uuid,fcntl
import gd_batch_engine as engine
B=Path(__file__).resolve().parent
def plan(da,run,windows,budget,policy=engine.POLICIES[0]):return engine.plan(engine.load(B),da,run,windows,budget,policy)
def location(run,name):
 import re
 if not re.fullmatch(r'[A-Za-z0-9_\-]{1,50}',name):raise ValueError('批次名仅字母数字下划线连字符')
 return Path(run).resolve().parent/'batches'/name
def status(batch):
 b=Path(batch)
 if not(b/'PLAN.json').exists():return {'status':'not_authorized','native_calls':0}
 p=engine.read(b/'PLAN.json');a=engine.read(b/'AUTHORIZED.json');engine.need(a['plan_sha256']==engine.sha(b/'PLAN.json'),'authorized plan changed')
 engine.need(p['windows']in (4,96)and p['steps']==[list(x)for x in engine.steps(p['windows'])],'unexpected authorized stage sequence')
 engine.need(isinstance(p.get('source_pins'),dict)and bool(p['source_pins']),'missing plan source pins')
 for f,h in p['source_pins'].items():engine.need(Path(f).is_file()and engine.sha(f)==h,'plan source changed; historical status not verified: '+f)
 expected=[b/f'{i:03}_{kind}_{w}'/'DONE.json'for i,(kind,w)in enumerate(p['steps'])]
 observed=set(b.glob('*/DONE.json'));engine.need(observed.issubset(set(expected)),'unexpected extra DONE receipt')
 done=[];gap=False
 for f in expected:
  if not f.exists():gap=True;continue
  engine.need(not gap,'completed receipt after missing stage')
  r=engine.read(f);engine.need(r.get('status')=='verified','DONE is not verified: '+str(f))
  pins=r.get('pins');engine.need(isinstance(pins,dict)and bool(pins),'DONE has no evidence pins: '+str(f))
  for artifact,h in pins.items():engine.need(Path(artifact).is_file()and engine.sha(artifact)==h,'completed artifact changed: '+artifact)
  done.append(f)
 errors=list(b.glob('*/ERROR.json'));launches=sorted((b/'WORKERS').glob('*/START.json'))
 latest=launches[-1].parent if launches else None;exitfile=latest/'EXIT.json'if latest else None
 stopped,_=engine.control_state(b,a['created_epoch'])
 return {'status':'blocked_no_retry'if errors else'complete'if len(done)==len(p['steps'])else'stop_requested'if stopped else'worker_exited'if exitfile and exitfile.exists()else'running_or_unknown'if latest else'external_worker_or_incomplete'if done or list(b.glob('*/LAUNCH_INTENT.json'))else'authorized_not_launched','completed_steps':len(done),'total_steps':len(p['steps']),'windows':p['windows'],'budget_s':p['budget_s'],'worker_exit':engine.read(exitfile)if exitfile and exitfile.exists()else None,'errors':[engine.read(x)for x in errors],'native_calls':0,'verification_scope':'plan/source/verified receipt pins only; not a fresh core replay or safety release','safety_release':False}
def spawn(batch):
 b=Path(batch);w=b/'WORKERS';w.mkdir(exist_ok=True)
 with (b/'UI_LAUNCH_MUTEX').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  s=status(b)
  if s['status']in ('running_or_unknown','external_worker_or_incomplete','complete','blocked_no_retry'):raise ValueError('已有运行、未知或失败状态；不会重复启动，请读取证据')
  d=w/(str(time.time_ns())+'_'+uuid.uuid4().hex);d.mkdir();engine.write(d/'START.json',{'intent_epoch':time.time(),'unknown_is_not_zero_calls':True})
  with(d/'stdout.log').open('ab')as out:
   p=subprocess.Popen([sys.executable,'-B',str(B/'gd_batch_control.py'),'worker',str(b),str(d)],stdout=out,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
  engine.write(d/'PID.json',{'pid':p.pid});return {'status':'worker_started','pid':p.pid,'batch':str(b)}
def start(p,batch,confirmed=False):
 engine.authorize(engine.load(B),p,batch,confirmed=confirmed);return spawn(batch)
def stop(batch):
 engine.need((Path(batch)/'AUTHORIZED.json').exists(),'批次尚未授权');return engine.stop(batch)
def resume(batch,confirmed=False):
 engine.need(confirmed,'必须明确继续同一计划及预算');s=status(batch)
 engine.need(s['status']in ('worker_exited','stop_requested'),'运行/未知/失败不能重新启动')
 # A stop request alone is not proof the worker exited.
 workers=sorted((Path(batch)/'WORKERS').glob('*/START.json'))
 engine.need(not workers or(workers[-1].parent/'EXIT.json').exists(),'停止尚未确认到阶段边界；不重启未知进程')
 if (s.get('worker_exit')or{}).get('result',{}).get('status')=='blocked_or_unknown_no_retry':raise ValueError('原失败/未知保持，不自动重试')
 engine.resume(batch,confirmed=True);return spawn(batch)
def worker(batch,d):
 b=Path(batch);d=Path(d)
 with(b/'UI_WORKER_MUTEX').open('a')as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:z=engine.run_loop(engine.load(B),b);result={'result':z,'exit_epoch':time.time()}
  except Exception as e:result={'result':{'status':'blocked_or_unknown_no_retry','error':str(e)},'exit_epoch':time.time()}
  engine.write(d/'EXIT.json',result)
if __name__=='__main__':
 engine.need(len(sys.argv)==4 and sys.argv[1]=='worker','worker-only internal CLI');worker(sys.argv[2],sys.argv[3])
