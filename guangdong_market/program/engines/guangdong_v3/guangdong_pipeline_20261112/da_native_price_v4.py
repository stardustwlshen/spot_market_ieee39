"""Explicit single-attempt native action and recovery check; no silent retry."""
from da_pipeline_v4 import *
import subprocess,time,os
from smm import highs_solver as hs

def execute(run,stage,execute_native=False):
 verify_transport();run=Path(run);p=run/stage;prep=checked_prepared(run,stage)
 if (p/'ACCEPTANCE.json').exists():return accepted(run,stage)
 n=p/native_dir(stage)
 if n.exists():
  # Only known complete actual output may be reviewed. Never resubmit uncertain work.
  need((n/'COMPLETED.json').exists()and(n/'response.json').exists(),'native outcome pending/unknown; inspect process and raw result; no automatic retry')
  return review(run,stage)
 need(execute_native,'explicit execute-native required; no solver called')
 if stage=='da_sced':
  old=read(p/'native/response.json');need(old.get('native_status')=='Optimal' and (p/'FAILED_KKT_DIAGNOSTIC.json').exists(),'new SCED attempt requires preserved known failed strict review')
  revision=read(run/'PRECISION_REVISION.json')
  for name,h in revision['source_pins'].items():need(sha(B/name)==h,'precision revision source changed')
  need(sha(p/'native/response.json')==revision['old_response_sha256'],'old response identity')
 if stage=='da_scuc':
  old=read(p/'native/response.json');need(old.get('optimize_calls')==0 and 'linear model' in old.get('error',''),'only known zero-optimization failed import may use new attempt')
  need({k:v for k,v in read(p/'native/request.json').items()if k!='deadline'}==read(p/'template.json'),'old/new exact payload unchanged')
 q=expected_request(run,stage);worker=actual_worker(stage);seconds=q['options']['time_limit'];n.mkdir();q['deadline']=time.perf_counter()+seconds
 write(n/'request.json',q);write(n/'IDENTITY.json',dict(stage=stage,run_id=prep['run_id'],source_identity=prep['source_identity'],worker_sha256=sha(worker),request_sha256=sha(n/'request.json'),prepared_sha256=sha(p/'PREPARED.json'),automatic_retry=False))
 with(n/'worker.log').open('x')as log:
  child=subprocess.Popen([sys.executable,'-B',str(worker),str(n/'request.json'),str(n/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'})
  write(n/'LAUNCH.json',dict(pid=child.pid,stage=stage,attempt=1))
  try:code=child.wait(timeout=seconds+30)
  except subprocess.TimeoutExpired:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait()
   write(n/'PENDING.json',dict(reason='timeout stopped process; raw outcome requires inspection',automatic_retry=False));raise RuntimeError('native timeout; no automatic retry')
 need((n/'response.json').exists(),'worker did not produce response; outcome retained, no retry')
 write(n/'COMPLETED.json',dict(returncode=code,response_sha256=sha(n/'response.json')))
 return review(run,stage)

def run_da(run,execute_native=False):
 accepted(run,'da_scuc');accepted(run,'da_sced');verify_price_dependencies();prepare_price(run);execute(run,'da_price',execute_native);return artifacts(run)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--execute-native',action='store_true');args=ap.parse_args();print(json.dumps(run_da(args.run,args.execute_native)))
