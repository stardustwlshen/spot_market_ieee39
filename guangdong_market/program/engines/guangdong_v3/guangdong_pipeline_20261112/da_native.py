"""Explicit single-attempt native action and recovery check; no silent retry."""
from da_pipeline import *
import subprocess,time,os
from smm import highs_solver as hs

def execute(run,stage,execute_native=False):
 run=Path(run);p=run/stage;prep=checked_prepared(run,stage)
 if (p/'ACCEPTANCE.json').exists():return accepted(run,stage)
 n=p/'native'
 if n.exists():
  # Only known complete actual output may be reviewed. Never resubmit uncertain work.
  need((n/'COMPLETED.json').exists()and(n/'response.json').exists(),'native outcome pending/unknown; inspect process and raw result; no automatic retry')
  return review(run,stage)
 need(execute_native,'explicit execute-native required; no solver called')
 q=read(p/'template.json');worker=B/'da_math'/('mip_worker.py'if stage=='da_scuc'else'lp_worker.py');seconds=q['options']['time_limit'];n.mkdir();q['deadline']=time.perf_counter()+seconds
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
 prepare_scuc(run);execute(run,'da_scuc',execute_native);prepare_sced(run);execute(run,'da_sced',execute_native);prepare_price(run);execute(run,'da_price',execute_native);return artifacts(run)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--execute-native',action='store_true');args=ap.parse_args();print(json.dumps(run_da(args.run,args.execute_native)))
