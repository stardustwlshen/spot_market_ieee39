"""Portable explicit RT-SCUC research program. Default paths never invoke native."""
from pathlib import Path
import sys,os,json,hashlib,shutil,argparse,subprocess,time,math
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,allow_nan=False,sort_keys=True)
def code_pins():return {str(p.relative_to(B)):sha(p)for p in [B/'program.py',B/'DEPENDENCIES.json',*sorted((B/'runtime').glob('*.py'))]}
def bind_dependencies(root):
 root=Path(root).resolve()
 for name,z in read(B/'DEPENDENCIES.json').items():need(sha(root/name)==z['sha256'],'dependency changed: '+name)
 os.environ['GD_RECOMMIT_DEPENDENCY_ROOT']=str(root)
 sys.path[:0]=[str(B/'runtime'),str(root/'guangdong_after_reset_r23_compressed_20261112/repo/src')]
 return root

def seed(da_run,run,dependency_root):
 run=Path(run).resolve();need(not run.exists(),'fresh research run required');dep=bind_dependencies(dependency_root)
 from prepare_research_seed import prepare
 da_run=Path(da_run).resolve();run.mkdir(parents=True);prepare(da_run,run/'seed')
 a=read(da_run/'ARTIFACTS.json');s=a['stages']['da_sced'];names=(da_run/s['full_names']['path']).resolve();response=(da_run/s['expanded_response']['path']).resolve()
 need(sha(names)==s['full_names']['sha256']and sha(response)==s['expanded_response']['sha256'],'seed witness sources')
 write(run/'seed/WARMSTART_SOURCE.json',dict(names=str(names),names_sha256=sha(names),response=str(response),response_sha256=sha(response),basis='accepted DA feasible trajectory only, not new RT optimum'))
 r=read(run/'seed/request.json');write(run/'CONTEXT.json',dict(schema='gd.research.rt_program.context.v1',run_id=r['run_id'],dependency_root=str(dep),code_pins=code_pins(),seed_pins={str(p.relative_to(run)):sha(p)for p in (run/'seed').iterdir()if p.is_file()},formal=False,seed_executed_cells=0,seed_issued_cells=3))
 return dict(run=str(run),run_id=r['run_id'],native_calls=0,next_action='build --window 0',scope='independent hypothetical research issued seed, never telemetry')

def context(run):
 run=Path(run).resolve();c=read(run/'CONTEXT.json');need(c['code_pins']==code_pins(),'program revision changed during run')
 for name,h in c['seed_pins'].items():need(sha(run/name)==h,'seed changed')
 bind_dependencies(c['dependency_root']);return run,c

def worker_profiles(dep):return dict(SCUC=dep/'guangdong_r65_three_state_startup_20261112/standard_counted_worker.py',SCED=dep/'ieee39-cloud/repo/engines/guangdong_v3/guangdong_pipeline_20261112/precision_recovery_v3/lp_worker_1e10.py')

def build_window(run,window,time_limit=300.):
 run,c=context(run);need(type(window)is int and 0<=window<96,'window0..95');need(time_limit in(120.,180.,300.),'bounded explicit native budget')
 from contracts import validate,digest
 from smm.gd_october_job_codec import loads
 from builder import build
 from smm.highs_solver import serialize_model,validate_options
 from smm.gd_r23_equivalent_compression import audit
 rp=run/'seed/request.json'if window==0 else run/f'handoff/W{window}/request.json';r=validate(read(rp));need(r['run_id']==c['run_id']and r['window']==window,'request window/run mismatch')
 if window>0:
  h=read(rp.parent/'HANDOFF.json');need(h['context_sha256']==sha(run/'CONTEXT.json') and h['window']==window,'handoff context/window mismatch')
  for file,hsh in h['pins'].items():need(sha(file)==hsh,'handoff receipt/acceptance/request changed')
  receipt=read(rp.parent/'RECEIPT.json');need(digest(receipt)==r['state']['receipt_sha256'] and receipt['run_id']==c['run_id'],'handoff receipt chain')
 out=run/f'W{window}/prepared';need(not out.exists(),'prepared output exists; do not rebuild')
 casefile=run/'seed/case.json';agcfile=run/'seed/agc.json';policyfile=run/'seed/policy.json';need(sha(casefile)==r['case_sha256']and sha(agcfile)==r['sources']['AGC']['sha256']and sha(policyfile)==r['policy_sha256'],'case/AGC/policy mismatch')
 case=loads(casefile.read_bytes());agc=read(agcfile);policy=read(policyfile)
 for z in agc.values():
  for key in ('up_mw','down_mw'):z[key]={int(k):v for k,v in z[key].items()}
 model=build(case,r,agc,policy);q,vs,cs=serialize_model(model,validate_options(dict(time_limit=float(time_limit),threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;names=dict(variables=[v.name for v in vs],constraints=[v.name for v in cs])
 if window==0:
  src=read(run/'seed/WARMSTART_SOURCE.json');need(sha(src['names'])==src['names_sha256']and sha(src['response'])==src['response_sha256'],'seed warmstart source changed');values=dict(zip(read(src['names'])['variables'],read(src['response'])['primal']))
 else:
  from acceptance import load_verified
  prev=run/f'W{window-1}/ACCEPTED_PAIR.json';prev_request=read(run/f'W{window-1}/prepared/request.json');_,values=load_verified(prev,prev_request)
 x=[]
 for name in names['variables']:
  if name in values:value=values[name]
  elif name.startswith('rt_notice['):
   g,t=name[len('rt_notice['):-1].split(',');g=g.strip("'");j=int(t)+int(round(case['curves'][g]['notice_h']*12));keys=[f'start[{g},{j}]',f"start['{g}',{j}]"]
   if j>=576:value=0.
   else:
    found=[values[k]for k in keys if k in values];need(len(found)==1,'notice source missing');value=found[0]
  else:raise ValueError('witness variable missing: '+name)
  x.append(value)
 obj=q['objective']['constant']+math.fsum(a*x[j]for j,a in q['objective']['terms']);witness=audit(q,x,obj);need(witness['max_primal_violation']<=1e-6 and witness['max_integer_violation']<=1e-6,'previous trajectory not feasible for this new window; no fabricated start')
 for col,v in zip(q['variables'],x):col['start']=v
 q['warmstart']=True;out.mkdir(parents=True)
 for name,z in [('template',q),('names',names),('request',r),('FEASIBLE_WARMSTART',witness)]:write(out/(name+'.json'),z)
 deps=Path(c['dependency_root']);sources={str((B/n).resolve()):h for n,h in c['code_pins'].items()};sources.update({str(deps/n):z['sha256']for n,z in read(B/'DEPENDENCIES.json').items()})
 ready=dict(request_sha256=digest(r),context_sha256=sha(run/'CONTEXT.json'),source_pins=sources,artifact_pins={p.name:sha(p)for p in out.iterdir()},columns=len(vs),rows=len(cs),integers=sum(v['kind']!='C'for v in q['variables']),native_calls=0,formal=False,warmstart='verified full feasible prior; not new optimal result')
 write(out/'READY.json',ready);return {k:ready[k]for k in('columns','rows','integers','native_calls')}

def native_once(prepared,output,worker,stage):
 prepared=Path(prepared);out=Path(output)
 if out.exists():
  need((out/'COMPLETED.json').exists()and(out/'response.json').exists(),'unknown/incomplete native occurrence; do not retry')
  done=read(out/'COMPLETED.json');need(done['response_sha256']==sha(out/'response.json'),'completed response changed');return dict(status='existing_completed_requires_review',native_calls=0)
 q=read(prepared/'template.json');need(q['options']['threads']==1 and q['options']['mip_rel_gap']==.001 and q['options']['mip_abs_gap']==0,'solver settings')
 seconds=q['options']['time_limit'];need(0<seconds<=300,'budget');out.mkdir(parents=True,exist_ok=False);q['deadline']=time.perf_counter()+seconds;write(out/'request.json',q);write(out/'IDENTITY.json',dict(stage=stage,worker_sha256=sha(worker),request_sha256=sha(out/'request.json'),template_sha256=sha(prepared/'template.json'),program_sha256=sha(__file__),automatic_retry=False))
 with(out/'worker.log').open('x')as log:
  proc=subprocess.Popen([sys.executable,'-B',str(worker),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'})
  write(out/'LAUNCH.json',dict(pid=proc.pid,attempt=1,worker_sha256=sha(worker),request_sha256=sha(out/'request.json')))
  try:code=proc.wait(timeout=seconds+30)
  except subprocess.TimeoutExpired:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
   write(out/'PENDING.json',dict(automatic_retry=False));raise RuntimeError('native outcome pending; no retry')
 need((out/'response.json').exists(),'missing response; no retry');write(out/'COMPLETED.json',dict(returncode=code,response_sha256=sha(out/'response.json')))
 return dict(status=read(out/'response.json').get('native_status'),native_calls=1,mathematically_accepted=False)

def verify_prepared(run,window):
 p=run/f'W{window}/prepared';r=read(p/'READY.json');need(r['context_sha256']==sha(run/'CONTEXT.json'),'prepared/context mismatch')
 for name,h in r['artifact_pins'].items():need(sha(p/name)==h,'prepared artifact changed')
 for name,h in r['source_pins'].items():need(sha(name)==h,'prepared source changed')
 return p

def accept_window(run,window):
 run,c=context(run);p=verify_prepared(run,window);base=run/f'W{window}';workers=worker_profiles(Path(c['dependency_root']))
 from acceptance import accept_pair,load_verified
 output=base/'ACCEPTED_PAIR.json'
 if output.exists():return load_verified(output,read(p/'request.json'))[0]
 return accept_pair(p,base/'scuc_native',workers['SCUC'],base/'sced_prepared',base/'sced_native',workers['SCED'],output)

def run_one(run,window,execute_native=False):
 run,c=context(run);p=verify_prepared(run,window);base=run/f'W{window}';workers=worker_profiles(Path(c['dependency_root']))
 if not execute_native:return dict(status='dryrun_only',window=window,native_calls=0,stages=['SCUC','fixed_new_integer_SCED','strict_full_acceptance'])
 from acceptance import prepare_sced
 native_once(p,base/'scuc_native',workers['SCUC'],'SCUC')
 sp=base/'sced_prepared'
 if not sp.exists():prepare_sced(p,base/'scuc_native',workers['SCUC'],sp)
 else:
  source=read(sp/'SOURCE.json');need(source['prepared_sha256']==sha(p/'READY.json')and source['SCUC_response_sha256']==sha(base/'scuc_native/response.json'),'SCED source mismatch')
 native_once(sp,base/'sced_native',workers['SCED'],'SCED');a=accept_window(run,window)
 return dict(status='actual_SCUC_SCED_pair_accepted',window=window,acceptance=str(base/'ACCEPTED_PAIR.json'),request_sha256=a['request_sha256'],independent_PRICE_pending=True,formal=False,safety_release=False,AGC_AC_clearance='not_verified')

def handoff(run,window,simulate=False):
 run,c=context(run);base=run/f'W{window}';output=run/f'handoff/W{window+1}'
 if not simulate:return dict(status='simulation_confirmation_required',native_calls=0,meaning='counterfactual research execution without AGC/AC clearance; never operational release')
 from advance_simulated import advance
 result=advance(base/'prepared/request.json',base/'ACCEPTED_PAIR.json',output)
 write(output/'HANDOFF.json',dict(context_sha256=sha(run/'CONTEXT.json'),window=window+1,pins={str(f.resolve()):sha(f)for f in [output/'request.json',output/'RECEIPT.json',base/'prepared/request.json',base/'ACCEPTED_PAIR.json']},safety_release=False,scope='counterfactual research execution without AGC/AC clearance'))
 return {**result,'safety_release':False,'AGC_AC_clearance':'not_verified','formal':False}

def main():
 p=argparse.ArgumentParser(description='Explicit research RT recommitment; default never launches native');p.add_argument('action',choices=['seed','build','run-one-window','accept','handoff','verify']);p.add_argument('--mode',choices=['research','formal'],default='research');p.add_argument('--run');p.add_argument('--da-run');p.add_argument('--dependency-root',default=str(B/'vendor'));p.add_argument('--window',type=int,default=0);p.add_argument('--time-limit',type=float,default=300.);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');a=p.parse_args()
 need(a.mode=='research','formal mode unsupported: missing AGC/AC clearance and operational inputs')
 if a.action=='verify':bind_dependencies(a.dependency_root);z=dict(status='source_dependencies_verified',native_calls=0)
 else:
  need(a.run,'--run required')
  if a.action=='seed':need(a.da_run,'--da-run required');z=seed(a.da_run,a.run,a.dependency_root)
  elif a.action=='build':z=build_window(a.run,a.window,a.time_limit)
  elif a.action=='run-one-window':z=run_one(a.run,a.window,a.execute_native)
  elif a.action=='accept':z=accept_window(a.run,a.window)
  else:z=handoff(a.run,a.window,a.simulate_no_deviation)
 print(json.dumps(z,allow_nan=False))
if __name__=='__main__':main()
