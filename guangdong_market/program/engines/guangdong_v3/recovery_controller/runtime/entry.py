"""Import/review already executed candidate, then prepare downstream. No native launcher."""
from pathlib import Path
import sys,json,argparse,shutil
from context_guard import load_context
CONTEXT=load_context()
BASE=Path(CONTEXT['engine'])/'guangdong_pipeline_20261112'
sys.path.insert(0,str(BASE))
from recovery_contract import *
import derived_da_pipeline as d

def check(run,plan,template,worker,native):
 run=Path(run).resolve();c=candidate_check(run,plan,template,worker,native);d.verify_sources(run);d.checked_prepared(run,'da_sced');d.accepted(run,'da_scuc')
 q=read(Path(native)/'request.json');r=read(Path(native)/'response.json');names=read(run/'da_sced/names.json')
 reduced=d.metrics(q,r,names,angle_tolerance=1e-8)
 full=read(run/'da_sced/full_template.json');fn=read(run/'da_sced/full_names.json');compression=read(run/'da_sced/compression_plan.json');x=d.lift(full,compression,r['primal']);y=[0.]*len(full['rows'])
 for j,v in zip(compression['kept_rows_original_indices'],r['dual']):y[j]=v
 expanded=dict(r,primal=x,dual=y);full_kkt=d.metrics(full,expanded,fn,angle_tolerance=1e-8)
 previous=read(run/'da_scuc/full_primal.json');scuc=read(run/'da_scuc/full_template.json');integers=all(abs(z-round(v))<=1e-8 for z,v,col in zip(x,previous,scuc['variables'])if col['kind']!='C')
 return dict(candidate=c,reduced_KKT=reduced,full_KKT=full_kkt,SCUC_integers_unchanged=integers,passed=reduced['passed']and full_kkt['passed']and integers)

def import_candidate(run,plan,template,worker,native):
 run=Path(run).resolve();result=check(run,plan,template,worker,native);need(result['passed'],'strict reduced/full KKT failed; no acceptance/import')
 need(not(run/'SCED_RECOVERY_REVISION.json').exists()and not(run/'da_sced/ACCEPTANCE.json').exists(),'existing revision or acceptance; no overwrite')
 c=result['candidate'];r=read(run/'RUN.json');files=['RUN.json','da_scuc/ACCEPTANCE.json','da_scuc/full_primal.json','da_sced/PREPARED.json','da_sced/template.json','da_sced/names.json','da_sced/full_template.json','da_sced/full_names.json','da_sced/compression_plan.json','da_sced/native_v2/request.json','da_sced/native_v2/response.json','PRECISION_REVISION.json','TRANSPORT_REVISION.json']
 sources={str(RECOVERY/n):sha(RECOVERY/n)for n in ('entry.py','derived_da_pipeline.py','recovery_contract.py','context_guard.py','context.json')}
 revision=dict(schema='gd.explicit.sced_algorithm_recovery.v1',run_id=r['run_id'],input_identity=r['input_identity'],base_source_identity=r['source_identity'],recovery_source_identity=digest(sources),source_pins=sources,base_evidence={f:sha(run/f)for f in files},candidate_pins=c['candidate_pins'],new_options=c['options'],worker_path=c['worker_path'],native_origin=c['native_path'],strict_review=result,math_changed=False,integer_commitment_changed=False,KKT_threshold_changed=False,automatic_retry=False)
 write(run/'SCED_RECOVERY_REVISION.json',revision)
 out=run/'da_sced/native_recovery_ipm_v1';out.mkdir(exist_ok=False)
 for f in Path(native).iterdir():
  if f.is_file():shutil.copyfile(f,out/f.name)
 prep=d.checked_prepared(run,'da_sced')
 write(out/'IDENTITY.json',dict(stage='da_sced',run_id=r['run_id'],source_identity=r['source_identity'],worker_sha256=sha(worker),request_sha256=sha(out/'request.json'),prepared_sha256=sha(run/'da_sced/PREPARED.json'),recovery_source_identity=revision['recovery_source_identity'],imported_actual_occurrence=True,original_native_path=c['native_path'],automatic_retry=False))
 write(out/'IMPORT_RECEIPT.json',dict(native_calls=0,original_candidate_pins=c['candidate_pins'],copied_raw_bytes_unchanged=True))
 return d.review(run,'da_sced')

def solve_price(run):
 import subprocess,time,os
 run=Path(run).resolve();revision=verify_recovery(run);prep=d.checked_prepared(run,'da_price');n=run/'da_price/native'
 if n.exists():
  need((n/'COMPLETED.json').exists()and(n/'response.json').exists(),'PRICE pending/unknown; no retry')
  return d.review(run,'da_price')
 q=d.expected_request(run,'da_price');worker=d.actual_worker('da_price',run);seconds=q['options']['time_limit'];n.mkdir(exist_ok=False);q['deadline']=time.perf_counter()+seconds
 write(n/'request.json',q);write(n/'IDENTITY.json',dict(stage='da_price',run_id=prep['run_id'],source_identity=prep['source_identity'],worker_sha256=sha(worker),request_sha256=sha(n/'request.json'),prepared_sha256=sha(run/'da_price/PREPARED.json'),recovery_source_identity=revision['recovery_source_identity'],automatic_retry=False))
 from smm import highs_solver as hs
 with(n/'worker.log').open('x')as log:
  c=subprocess.Popen([sys.executable,'-B',str(worker),str(n/'request.json'),str(n/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'})
  write(n/'LAUNCH.json',dict(pid=c.pid,attempt=1,stage='da_price'))
  try:code=c.wait(timeout=seconds+30)
  except subprocess.TimeoutExpired:
   c.terminate()
   try:c.wait(timeout=5)
   except subprocess.TimeoutExpired:c.kill();c.wait()
   write(n/'PENDING.json',dict(automatic_retry=False));raise RuntimeError('PRICE timeout; inspect, no retry')
 need((n/'response.json').exists(),'PRICE response missing; no retry');write(n/'COMPLETED.json',dict(returncode=code,response_sha256=sha(n/'response.json')))
 return d.review(run,'da_price')

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['check','import-candidate','prepare-price','solve-price','artifacts']);p.add_argument('--run',required=True)
 for n in ('plan','template','worker','native'):p.add_argument('--'+n)
 p.add_argument('--execute-native',action='store_true');a=p.parse_args();need(Path(a.run).resolve()==Path(CONTEXT['run']),'context run mismatch')
 if a.action in ('check','import-candidate'):
  need(all((a.plan,a.template,a.worker,a.native)),'explicit candidate paths required');fn=check if a.action=='check'else import_candidate;z=fn(a.run,a.plan,a.template,a.worker,a.native)
 elif a.action=='prepare-price':z=d.prepare_price(a.run)
 elif a.action=='solve-price':
  need(a.execute_native,'explicit --execute-native required');z=solve_price(a.run)
 else:z=d.artifacts(a.run)
 print(json.dumps(z,allow_nan=False))
if __name__=='__main__':main()
