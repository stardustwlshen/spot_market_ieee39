"""One genuine new curtailable-renewable and synthetic AGC MIP; 600s bounded, interruptible by a cancel file."""
from pathlib import Path
import json,hashlib,time,subprocess,sys,os,signal,resource
BASE=Path(__file__).resolve().parent;ROOT=BASE.parent;PREP=BASE/'prepared_v1';SRC=ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src';sys.path.insert(0,str(SRC))
from smm import highs_solver as hs

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def encoded(d):return json.dumps(d,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def write(p,d):
 with p.open('xb')as f:f.write(encoded(d))
def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,128*1024**2))
ready_path=BASE/'READY.json';assert sha(ready_path)=='15b6f069a4ef54fd1257a6d921411284d3e3526f0ef86539abf7dd5d905f6e53';ready=json.loads(ready_path.read_text());assert ready['optimizer_calls']==0 and ready['HiGHS_import_only_calls']==1
for n,h in ready['pins'].items():assert sha(n)==h
meta=json.loads((PREP/'PREPARATION.json').read_text());assert meta['native_calls']==0 and meta['worker_time_limit_s']==600 and not meta['SCUC_accepted']
for n,h in meta['source_pins'].items():assert sha(n)==h
for n,h in meta['artifact_hashes'].items():assert sha(PREP/n)==h
worker=BASE/'checked_worker_v1/r42_checked_worker.py';assert sha(worker)=='12a9456c6f597c827ec9511598ce69802d9c2e907e665607e9e8576cbb2efed1';q=json.loads((PREP/'template.json').read_text());assert not q['warmstart'] and sum(v['kind']!='C'for v in q['variables'])==2160 and q['options']==dict(threads=1,time_limit=600.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True,presolve='choose',solve_relaxation=False,mip_heuristic_effort=0.,mip_heuristic_run_rens=False,mip_heuristic_run_rins=False,mip_heuristic_run_root_reduced_cost=False)
q.pop('deadline',None);q['preparation_elapsed_s']=0.;body=encoded(q);out=BASE/'native_once_v1';out.mkdir(exist_ok=False)
deadline=time.perf_counter()+600.;payload=b'{"deadline":'+repr(deadline).encode()+b','+body[1:]
with(out/'request.json').open('xb')as f:f.write(payload)
write(out/'IDENTITY.json',{'schema':'gd.research.r42.curtailable_renewable_D_AGC_global_mip.native.v1','request_sha256':sha(out/'request.json'),'preparation_sha256':sha(PREP/'PREPARATION.json'),'template_sha256':sha(PREP/'template.json'),'worker_sha256':sha(worker),'ready_sha256':sha(ready_path),'new_scenario_not_old_fixed_renewable_case':True,'unchanged_original_case':True,'new_physical_forecasts':False,'synthetic_AGC_qualification_and_plant_data':True,'AGC_MW_D_day':50,'research_curtailment_penalty_yuan_mwh':1000,'network_implementation':'new 20-injection affine elimination with quantified floating error; final new-full validation required','no_warmstart':True,'gap':.001,'automatic_retry':False,'source_meta':meta,'worker_ready':ready})
started=time.monotonic();cancelled=False
with(out/'worker.log').open('xb')as log:
 p=subprocess.Popen([sys.executable,'-B',str(worker),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True,preexec_fn=limits);write(out/'launch.json',{'pid':p.pid,'worker_sha256':sha(worker),'attempt':1})
 while p.poll()is None:
  if(BASE/'CANCEL_REQUESTED').exists():
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
   cancelled=True;write(out/'CANCELLED.json',{'pid':p.pid,'returncode':p.returncode,'native_result_not_accepted':True,'automatic_retry':False});break
  if time.perf_counter()>deadline+5:
   os.killpg(p.pid,signal.SIGKILL);p.wait();write(out/'UNCERTAIN_STOP.json',{'pid':p.pid,'returncode':p.returncode,'automatic_retry':False});raise TimeoutError('outer deadline exceeded')
  time.sleep(.25)
if cancelled:print('Cancelled, no result accepted.');raise SystemExit(2)
code=p.returncode;elapsed=time.monotonic()-started;assert code==0 and(out/'response.json').exists();r=json.loads((out/'response.json').read_text());
if 'error'in r:write(out/'FAILED_RESULT.json',{'returncode':code,'response_sha256':sha(out/'response.json'),'error':r['error'],'optimize_calls':r.get('optimize_calls'),'automatic_retry':False});raise RuntimeError(r['error'])
assert r['backend']=='highs'and r['solver_version']=='1.15.1'and r['optimize_calls']==1
s={k:r.get(k)for k in ('native_status','status','has_primal','primal_witness_available','native_mip_gap','objective','bound','native_counters','native_run_wall_time_s','native_settings','time_settings','optimize_calls','matrix_storage_verification')};s.update(pid=p.pid,returncode=code,measured_worker_elapsed_s=elapsed,response_sha256=sha(out/'response.json'),full_original_lift_review_required=True,formal_publication_allowed=False,automatic_retry=False);write(out/'CONFIRMED_NATIVE_RESULT.json',s);print(json.dumps(s))
