"""Explicit actual launcher. Import/help never starts optimizer; main controls invocation."""
from pathlib import Path
import json,hashlib,time,subprocess,sys,os,signal,argparse
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm import highs_solver as hs
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,sort_keys=True,separators=(',',':'),allow_nan=False)
def run(plan_path,expected_sha,output):
 plan_path=Path(plan_path);assert sha(plan_path)==expected_sha;plan=read(plan_path);assert plan['schema']=='gd.research.r69.PRICE_only_C_batch.v1'
 items=plan['items'];assert 0<len(items)<=96 and len({z['window']for z in items})==len(items) and all(0<=z['window']<=95 for z in items)
 for name,h in read(B/'RUNNER_MANIFEST.json').items():assert sha(B/name)==h
 pins={}
 for item in items:
  p=Path(item['prepared']);assert sha(p/'PREPARATION.json')==item['preparation_sha256'];m=read(p/'PREPARATION.json')
  for f,h in m['artifacts'].items():assert sha(p/f)==h
  for group in ('source_pins','source_code_pins'):
   for path,h in m[group].items():
    assert path not in pins or pins[path]==h
    pins[path]=h
 for path,h in pins.items():assert sha(path)==h
 worker=R/'guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_worker.py';assert sha(worker)=='dc439a81e2cb2eb2a3595d24f0c8ead08ffa633387a259a868f1d0d9fcdc46ea'
 o=Path(output);o.mkdir(parents=True,exist_ok=False);results=[]
 for item in items:
  if (o/'CANCEL_REQUESTED').exists() or (B/'CANCEL_REQUESTED').exists():raise RuntimeError('cancelled before next call')
  p=Path(item['prepared']);assert sha(p/'PREPARATION.json')==item['preparation_sha256'];m=read(p/'PREPARATION.json');assert sha(p/'template.json')==m['artifacts']['template.json'];q=read(p/'template.json');opt=q['options'];assert q['dual']is True and not q['warmstart'] and all(z['kind']=='C'for z in q['variables'])
  assert opt['threads']==1 and opt['time_limit']==60 and opt['mip_rel_gap']==.001 and opt['mip_abs_gap']==0
  d=o/('W'+str(item['window']));d.mkdir();q['deadline']=time.perf_counter()+60;write(d/'request.json',q)
  write(d/'IDENTITY.json',dict(schema='gd.research.r69.PRICE_only.native.v1',window=item['window'],request_sha256=sha(d/'request.json'),preparation_sha256=sha(p/'PREPARATION.json'),worker_sha256=sha(worker),batch_plan_sha256=sha(plan_path),research_only=True,execution_authority=False,market_price=None,settlement_price=None,automatic_retry=False))
  with (d/'worker.log').open('xb')as log:
   child=subprocess.Popen([sys.executable,'-B',str(worker),str(d/'request.json'),str(d/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True);write(d/'launch.json',dict(pid=child.pid,attempt=1,worker_sha256=sha(worker)))
   while child.poll()is None:
    if (o/'CANCEL_REQUESTED').exists() or (B/'CANCEL_REQUESTED').exists() or time.perf_counter()>q['deadline']+5:
     os.killpg(child.pid,signal.SIGTERM)
     try:child.wait(timeout=5)
     except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
     write(d/'STOPPED.json',dict(accepted=False,retry_allowed=False));raise RuntimeError('bounded stop, inspect partial evidence')
    time.sleep(.1)
  assert child.returncode==0;r=read(d/'response.json');assert 'error'not in r and r['backend']=='highs'and r['solver_version']=='1.15.1'
  done=dict(window=item['window'],pid=child.pid,returncode=child.returncode,response_sha256=sha(d/'response.json'),status=r['status'],native_status=r['native_status'],market_price=None,settlement_price=None)
  write(d/'CONFIRMED_NATIVE_RESULT.json',done);results.append(done);print(json.dumps(done),flush=True)
 for path,h in pins.items():assert sha(path)==h
 write(o/'COMPLETED_BATCH.json',dict(native_calls=len(results),results=results))
 subprocess.run([sys.executable,'-B',str(B/'review_batch.py'),'--plan',str(plan_path),'--native',str(o),'--output',str(o/'review_v1')],check=True)
if __name__=='__main__':
 ap=argparse.ArgumentParser();[ap.add_argument('--'+k,required=True)for k in ('plan','plan-sha','output')];a=ap.parse_args();run(a.plan,a.plan_sha,a.output)
