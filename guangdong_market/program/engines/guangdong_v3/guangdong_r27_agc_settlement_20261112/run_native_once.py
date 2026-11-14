"""One real HiGHS synthetic AGC LP. Research allocation, not official payment."""
from pathlib import Path
import json,time,subprocess,hashlib,sys,os
B=Path(__file__).resolve().parent;R=B.parent;P=B/'prepared_synthetic_v1';O=B/'native_synthetic_once_v1';W=R/'guangdong_r25_strong_scuc_20261112/r25_highs_worker.py';sys.path.insert(0,str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm import highs_solver as hs
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(n,v):(O/n).write_text(json.dumps(v,sort_keys=True,allow_nan=False))
q=json.loads((P/'template.json').read_bytes());assert len(q['variables'])==6 and len(q['rows'])==9 and q['dual'] and all(v['kind']=='C'for v in q['variables']);assert q['options']['mip_rel_gap']==.001;O.mkdir(exist_ok=False);q.update(deadline=time.perf_counter()+60.,preparation_elapsed_s=0.);write('request.json',q);write('IDENTITY.json',{'request_sha256':sha(O/'request.json'),'template_sha256':sha(P/'template.json'),'input_sha256':sha(P/'input.json'),'worker_sha256':sha(W),'synthetic':True,'official_rule_equivalent':False,'new_native_calls':1})
start=time.monotonic()
with(O/'worker.log').open('xb')as log:
 p=subprocess.Popen([sys.executable,'-B',str(W),str(O/'request.json'),str(O/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'});write('launch.json',{'pid':p.pid,'attempt':1,'worker_sha256':sha(W)});code=p.wait(timeout=65)
assert code==0;r=json.loads((O/'response.json').read_bytes());assert r['status']=='optimal' and r['dual_valid'];write('CONFIRMED_NATIVE_RESULT.json',{'pid':p.pid,'returncode':code,'elapsed_s':time.monotonic()-start,'native_status':r['native_status'],'objective':r['objective'],'response_sha256':sha(O/'response.json'),'research_only':True,'formal_payment':None});print(json.dumps({'status':r['status'],'objective':r['objective'],'primal':r['primal'],'native_time_s':r['native_run_wall_time_s']}))
