"""One explicit portable PRICE occurrence; no retries, no silent price fill."""
from pathlib import Path
import sys,subprocess,time,os,json,argparse
from domain import read,write,sha,need
from evidence import optimal
from scoped_kkt import metrics
from review import nodal_prices
B=Path(__file__).resolve().parent

def run(prepared,execute=False):
 p=Path(prepared).resolve();ready=read(p/'READY.json')
 for f,h in ready['artifact_pins'].items():need(sha(p/f)==h,'prepared changed')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'source changed')
 if not execute:return dict(status='dryrun',native_calls=0,columns=ready['columns'])
 out=p/'native_once';need(not out.exists(),'occurrence exists; review rather than retry');out.mkdir();q=read(p/'template.json');need(q['options']['threads']==1 and q['options']['time_limit']==3600. and q['options']['mip_rel_gap']==.01 and q['options']['mip_abs_gap']==0,'bounded HiGHS settings');q['deadline']=time.perf_counter()+3900.;write(out/'request.json',q)
 worker=B/'price_worker.py';write(out/'IDENTITY.json',dict(worker_sha256=sha(worker),request_sha256=sha(out/'request.json'),native_entry_sha256=sha(__file__),ready_sha256=sha(p/'READY.json'),automatic_retry=False))
 with (out/'worker.log').open('x') as log:
  proc=subprocess.Popen([sys.executable,'-B',str(worker),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'});write(out/'LAUNCH.json',dict(pid=proc.pid,attempt=1))
  try:rc=proc.wait(timeout=3900)
  except subprocess.TimeoutExpired:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
   write(out/'PENDING.json',dict(automatic_retry=False));raise RuntimeError('unknown occurrence; no retry')
 need(rc==0 and (out/'response.json').exists(),'native failed; no retry');r=read(out/'response.json');write(out/'COMPLETED.json',dict(returncode=rc,response_sha256=sha(out/'response.json')))
 ctx=read(p/'CONTEXT.json');z=dict(context=ctx,raw_prices=None,formal_bill=None,payable=False,safety_release=False,native_calls=1,native_status=r.get('native_status'),source_pins={str(f):sha(f) for f in [p/'READY.json',p/'names.json',p/'CONTEXT.json',out/'request.json',out/'response.json',out/'IDENTITY.json',out/'COMPLETED.json',Path(__file__),worker]})
 if r.get('native_status')=='Optimal':
  names=read(p/'names.json');optimal(q,r,names);need(r.get('optimize_calls')==1,'one native optimize');z['KKT']=metrics(q,r,names,angle_tolerance=1e-8);z['raw_prices'],z['price_sensitivity']=nodal_prices(q,r,names);need(len(z['raw_prices'])==39,'all39 actual prices');z['slacks']={s:v for s,v in zip(names['variables'],r['primal']) if s.startswith('PRICE_')};z['objective']=r['objective']
 write(p/'PRICE_RESULT.json',z);return dict(native_status=z['native_status'],native_calls=1,priced=z['raw_prices'] is not None)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--prepared',required=True);a.add_argument('--execute-native',action='store_true');args=a.parse_args();print(json.dumps(run(args.prepared,args.execute_native)))
