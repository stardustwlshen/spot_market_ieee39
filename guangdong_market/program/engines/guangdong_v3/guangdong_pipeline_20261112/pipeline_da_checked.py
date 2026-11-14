"""Fresh same-mathematics checked-worker launcher; no old failed attempt required."""
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
 if stage=='da_scuc':
  from run_matrix_profile import activate
  profile=activate(run)
  revision=run/'TRANSPORT_REVISION.json'
  z=dict(schema='gd.pipeline.run_scoped_checked_transport.v3',original_input_identity=prep['input_identity'],original_source_identity=prep['source_identity'],unchanged_template_sha256=sha(p/'template.json'),activation_path='matrix_profile/ACTIVATION.json',activation_sha256=sha(run/'matrix_profile/ACTIVATION.json'),fresh_launcher_sha256=sha(__file__),new_worker_sha256=profile['worker_sha256'],new_native_directory='da_scuc/native_v2',math_changed=False,options_changed=False,threshold_changed=False)
  if revision.exists():need(read(revision)==z,'run transport revision changed')
  else:write(revision,z)
 q=expected_request(run,stage);worker=actual_worker(stage,run);seconds=q['options']['time_limit'];n.mkdir();q['deadline']=time.perf_counter()+seconds+300.
 write(n/'request.json',q);write(n/'IDENTITY.json',dict(stage=stage,run_id=prep['run_id'],source_identity=prep['source_identity'],worker_sha256=sha(worker),request_sha256=sha(n/'request.json'),prepared_sha256=sha(p/'PREPARED.json'),automatic_retry=False))
 with(n/'worker.log').open('x')as log:
  child=subprocess.Popen([sys.executable,'-B',str(worker),str(n/'request.json'),str(n/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'})
  write(n/'LAUNCH.json',dict(pid=child.pid,stage=stage,attempt=1))
  try:code=child.wait(timeout=seconds+300.)
  except subprocess.TimeoutExpired:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait()
   write(n/'PENDING.json',dict(reason='timeout stopped process; raw outcome requires inspection',automatic_retry=False));raise RuntimeError('native timeout; no automatic retry')
 need((n/'response.json').exists(),'worker did not produce response; outcome retained, no retry')
 write(n/'COMPLETED.json',dict(returncode=code,response_sha256=sha(n/'response.json')))
 return review(run,stage)

def run_da(run,execute_native=False):
 run=Path(run);verify_price_dependencies()
 if not(run/'PRECISION_REVISION.json').exists():
  write(run/'PRECISION_REVISION.json',dict(schema='gd.pipeline.fresh_precision_default.v4',SCED_dual_feasibility_tolerance=1e-10,unscaled=0,KKT_threshold=1e-8,no_deliberate_failed_attempt=True,source_pins={p:sha(B/p)for p in ['pipeline_da_checked.py','da_pipeline_v4.py','precision_recovery_v3/lp_worker_1e10.py']}))
 if not(run/'PRICE_DEPENDENCIES.json').exists():write(run/'PRICE_DEPENDENCIES.json',verify_price_dependencies())
 prepare_scuc(run);execute(run,'da_scuc',execute_native);prepare_sced(run);execute(run,'da_sced',execute_native);prepare_price(run);execute(run,'da_price',execute_native);return artifacts(run)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--execute-native',action='store_true');args=ap.parse_args();print(json.dumps(run_da(args.run,args.execute_native)))
