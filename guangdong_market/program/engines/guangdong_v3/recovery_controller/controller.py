"""Explicit one-candidate numerical recovery; no implicit fallback or retries."""
from pathlib import Path
import json,hashlib,sys,subprocess,shutil,time,os,argparse
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def gates(run_meta,acceptance,completion,response,diagnostic,*,sced_accepted=False,candidate_exists=False):
 need(not sced_accepted,'SCED already accepted')
 need(not candidate_exists,'candidate already exists; no retry')
 need(acceptance.get('status')=='accepted'and acceptance.get('stage')=='da_scuc','accepted SCUC required')
 need(all(acceptance.get(k)==run_meta.get(k)and run_meta.get(k)for k in ('run_id','input_identity','source_identity')),'SCUC/run identity mismatch')
 need(completion.get('returncode')==0 and response.get('native_status')=='Optimal'and response.get('status')=='optimal'and response.get('has_primal')is True and response.get('dual_valid')is True,'unknown or nonOptimal base outcome not eligible')
 need(diagnostic.get('passed')is False,'only recomputed original strict KKT failure is eligible')

def preflight(run,engine):
 run=Path(run).resolve();engine=Path(engine).resolve();p=engine/'guangdong_pipeline_20261112'
 need(not(run/'da_sced/ACCEPTANCE.json').exists(),'SCED already accepted')
 need(not(run/'recovery_controller_v4').exists(),'candidate already exists; no retry')
 for name,h in read(B/'SUPPORTED_ENGINE.json').items():need(sha(engine/name)==h,'unsupported engine profile; explicit adaptation required')
 m=read(run/'RUN.json');a=read(run/'da_scuc/ACCEPTANCE.json');n=run/'da_sced/native_v2'
 need((n/'COMPLETED.json').exists(),'incomplete/unknown native outcome')
 response=read(n/'response.json');done=read(n/'COMPLETED.json');need(done['response_sha256']==sha(n/'response.json'),'base completed response hash')
 for path,h in m['source_pins'].items():need(sha(path)==h,'base run source changed')
 for z in a['artifacts'].values():need(sha(run/z['path'])==z['sha256'],'SCUC accepted evidence changed')
 # Isolated interpreter keeps imported model generations separate. Recompute original strict KKT.
 code="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import da_pipeline_v4 as d;r=Path(sys.argv[2]);d.checked_prepared(r,'da_sced');q=d.read(r/'da_sced/native_v2/request.json');x=d.read(r/'da_sced/native_v2/response.json');assert {k:v for k,v in q.items()if k!='deadline'}==d.expected_request(r,'da_sced');print(json.dumps(d.metrics(q,x,d.read(r/'da_sced/names.json'),angle_tolerance=1e-8)))"
 c=subprocess.run([sys.executable,'-B','-c',code,str(p),str(run)],capture_output=True,text=True)
 need(c.returncode==0,'base strict review could not be recomputed: '+c.stderr[-2000:]);diagnostic=json.loads(c.stdout)
 gates(m,a,done,response,diagnostic,sced_accepted=(run/'da_sced/ACCEPTANCE.json').exists(),candidate_exists=(run/'recovery_controller_v4').exists())
 return dict(run=str(run),engine=str(engine),original_strict_KKT=diagnostic,optimization_calls=0)

def prepare(run,engine):
 z=preflight(run,engine);run=Path(z['run']);engine=Path(z['engine']);out=run/'recovery_controller_v4';out.mkdir(exist_ok=False);runtime=out/'runtime';shutil.copytree(B/'runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','context.json'))
 pins={str(p.relative_to(runtime)):sha(p)for p in runtime.glob('*.py')}
 ep=['guangdong_pipeline_20261112/'+n for n in ('da_pipeline_v4.py','run_protocol.py','da_math/scoped_kkt.py','da_math/physical_geometry.py','price_dependencies_v4/MANIFEST.json')]
 write(runtime/'context.json',dict(run=str(run),engine=str(engine),run_sha256=sha(run/'RUN.json'),runtime_pins=pins,engine_pins={n:sha(engine/n)for n in ep}))
 q=read(run/'da_sced/native_v2/request.json');q.pop('deadline',None);old=dict(q['options']);q['options'].update(solver='ipm',run_crossover='off',primal_feasibility_tolerance=1e-10,ipm_optimality_tolerance=1e-12)
 need(q['options']['dual_feasibility_tolerance']==1e-10 and q['options']['simplex_scale_strategy']==0 and all(v['kind']=='C'for v in q['variables']),'strict fixed LP required')
 write(out/'template.json',q)
 plan=dict(schema='gd.sced.precision_recovery.plan.v1',base_run=str(run),base_SCuc_acceptance_sha256=sha(run/'da_scuc/ACCEPTANCE.json'),failed_sced_request_sha256=sha(run/'da_sced/native_v2/request.json'),failed_sced_response_sha256=sha(run/'da_sced/native_v2/response.json'),worker_sha256=sha(runtime/'frozen_ipm_worker.py'),template_sha256=sha(out/'template.json'),algorithm_only_options_diff={k:dict(old=old.get(k),new=v)for k,v in q['options'].items()if old.get(k)!=v},SCUC_not_repeated=True,acceptance_threshold_unchanged=True)
 write(out/'PLAN.json',plan);write(out/'PREFLIGHT.json',z);write(out/'PREPARED.json',dict(context_sha256=sha(runtime/'context.json'),plan_sha256=sha(out/'PLAN.json'),controller_sha256=sha(__file__),supported_engine_sha256=sha(B/'SUPPORTED_ENGINE.json'),native_calls=0));return dict(prepared=str(out),native_calls=0)

def validate(run):
 run=Path(run).resolve();out=run/'recovery_controller_v4';z=read(out/'PREPARED.json')
 need(z['supported_engine_sha256']==sha(B/'SUPPORTED_ENGINE.json')and z['controller_sha256']==sha(__file__)and z['context_sha256']==sha(out/'runtime/context.json')and z['plan_sha256']==sha(out/'PLAN.json'),'controller preparation changed')
 c=read(out/'runtime/context.json');need(c['run']==str(run)and c['run_sha256']==sha(run/'RUN.json'),'run context mismatch')
 for name,h in c['runtime_pins'].items():need(sha(out/'runtime'/name)==h,'runtime changed')
 for name,h in c['engine_pins'].items():need(sha(Path(c['engine'])/name)==h,'engine changed')
 plan=read(out/'PLAN.json')
 for name,h in read(run/'RUN.json')['source_pins'].items():need(sha(name)==h,'base source changed after preparation')
 need(sha(run/'da_scuc/ACCEPTANCE.json')==plan['base_SCuc_acceptance_sha256']and sha(run/'da_sced/native_v2/request.json')==plan['failed_sced_request_sha256']and sha(run/'da_sced/native_v2/response.json')==plan['failed_sced_response_sha256'],'base accepted/failed evidence changed')
 need(plan['template_sha256']==sha(out/'template.json')and plan['worker_sha256']==sha(out/'runtime/frozen_ipm_worker.py'),'candidate changed')
 return out,plan

def execute(run):
 out,plan=validate(run);n=out/'native_once';need(not n.exists(),'already started or unknown; no automatic retry');need(not(Path(run)/'da_sced/ACCEPTANCE.json').exists(),'SCED already accepted');n.mkdir(exist_ok=False)
 q=read(out/'template.json');q['deadline']=time.perf_counter()+q['options']['time_limit'];write(n/'request.json',q)
 with(n/'worker.log').open('x')as log:
  c=subprocess.Popen([sys.executable,'-B',str(out/'runtime/frozen_ipm_worker.py'),str(n/'request.json'),str(n/'response.json')],stdout=log,stderr=log,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'})
  write(n/'LAUNCH.json',dict(pid=c.pid,worker_sha256=plan['worker_sha256'],request_sha256=sha(n/'request.json'),automatic_retry=False))
  try:code=c.wait(timeout=q['options']['time_limit']+30)
  except subprocess.TimeoutExpired:
   c.terminate()
   try:c.wait(timeout=5)
   except subprocess.TimeoutExpired:c.kill();c.wait()
   write(n/'PENDING.json',dict(automatic_retry=False));raise RuntimeError('unknown outcome; inspect without retry')
 need((n/'response.json').exists(),'missing response; no retry');write(n/'COMPLETED.json',dict(returncode=code,response_sha256=sha(n/'response.json')))
 return dict(native=str(n),accepted=False,next_action='accept requires independent full/reduced KKT and geometry')

def accept(run):
 out,plan=validate(run);n=out/'native_once';r=read(n/'response.json');l=read(n/'LAUNCH.json')
 need(r.get('optimize_calls')==1 and l['worker_sha256']==plan['worker_sha256']and l['request_sha256']==sha(n/'request.json'),'actual launch binding')
 cmd=[sys.executable,'-B',str(out/'runtime/entry.py'),'import-candidate','--run',str(Path(run).resolve()),'--plan',str(out/'PLAN.json'),'--template',str(out/'template.json'),'--worker',str(out/'runtime/frozen_ipm_worker.py'),'--native',str(n)]
 # Import refuses failed KKT, existing revision or acceptance. No resubmit, no native.
 c=subprocess.run(cmd,capture_output=True,text=True);write(out/'IMPORT_RESULT.json',dict(returncode=c.returncode,stdout=c.stdout,stderr=c.stderr))
 need(c.returncode==0,'strict recovery acceptance failed; retained evidence')
 c=subprocess.run([sys.executable,'-B',str(out/'runtime/entry.py'),'prepare-price','--run',str(Path(run).resolve())],capture_output=True,text=True);write(out/'PRICE_PREPARATION_RESULT.json',dict(returncode=c.returncode,stdout=c.stdout,stderr=c.stderr));need(c.returncode==0,'PRICE preparation pending, do not ordinary resume before repair')
 return dict(status='SCED_recovery_accepted_PRICE_prepared',next_action='ordinary engine energy resume',native_calls=0)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','prepare','execute','accept']);p.add_argument('--run',required=True);p.add_argument('--engine');p.add_argument('--execute-native',action='store_true');a=p.parse_args()
 if a.action in ('preflight','prepare'):
  need(a.engine,'explicit --engine required');z=(preflight if a.action=='preflight'else prepare)(a.run,a.engine)
 elif a.action=='execute':need(a.execute_native,'explicit native authorization required');z=execute(a.run)
 else:z=accept(a.run)
 print(json.dumps(z,allow_nan=False))
if __name__=='__main__':main()
