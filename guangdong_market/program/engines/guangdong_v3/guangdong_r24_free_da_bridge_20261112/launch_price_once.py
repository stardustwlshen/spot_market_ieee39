"""One new independent DA98 PRICE solve; diagnostic commitment only."""
import pathlib,json,hashlib,time,subprocess,sys,os,signal,resource
BASE=pathlib.Path(__file__).resolve().parent;ROOT=BASE.parent;PREP=BASE/'prepared_actual_v2';SRC=ROOT/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src';sys.path.insert(0,str(SRC))
from smm import highs_solver as hs

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def write(p,d):
 with p.open('x')as f:json.dump(d,f,sort_keys=True,separators=(',',':'),allow_nan=False)
def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,128*1024**2))
pins=json.loads((PREP/'PINS.json').read_text())
for n,h in pins.items():assert sha(PREP/n)==h
meta=json.loads((PREP/'PURE_PREPARATION.json').read_text());assert not meta['SCUC_accepted']and meta['diagnostic_only']and not meta['publication_allowed']
q=json.loads((PREP/'template.json').read_text());assert all(v['kind']=='C'for v in q['variables'])and q['dual']and not q['warmstart']and q['options']['time_limit']==60
out=BASE/'native_price_once_v1';out.mkdir(exist_ok=False);q['deadline']=time.perf_counter()+60.;q['preparation_elapsed_s']=0.
write(out/'request.json',q);worker=SRC/'smm/highs_worker.py';assert sha(worker)=='dc439a81e2cb2eb2a3595d24f0c8ead08ffa633387a259a868f1d0d9fcdc46ea'
write(out/'IDENTITY.json',{'stage':'independent_DA98_PRICE','request_sha256':sha(out/'request.json'),'pins_sha256':sha(PREP/'PINS.json'),'source_receipt':meta['source_receipt'],'SCUC_accepted':False,'formal_publication_allowed':False,'native_calls_allowed':1,'SCED_duals_used':False,'automatic_retry':False})
started=time.monotonic()
with(out/'worker.log').open('xb')as log:
 p=subprocess.Popen([sys.executable,'-B',str(worker),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True,preexec_fn=limits);write(out/'launch.json',{'pid':p.pid,'worker_sha256':sha(worker),'attempt':1})
 try:code=p.wait(timeout=max(.01,q['deadline']-time.perf_counter()+5))
 except subprocess.TimeoutExpired:
  os.killpg(p.pid,signal.SIGKILL);p.wait();write(out/'UNCERTAIN_STOP.json',{'pid':p.pid,'returncode':p.returncode,'automatic_retry':False});raise
elapsed=time.monotonic()-started;assert code==0 and(out/'response.json').exists();r=json.loads((out/'response.json').read_text());assert r['backend']=='highs'and r['solver_version']=='1.15.1'
s={k:r.get(k)for k in ('native_status','status','has_primal','dual_valid','objective','bound','native_settings','time_settings')};s.update(pid=p.pid,returncode=code,measured_worker_elapsed_s=elapsed,response_sha256=sha(out/'response.json'),SCUC_accepted=False,formal_publication_allowed=False,diagnostic_only=True,automatic_retry=False);write(out/'CONFIRMED_NATIVE_RESULT.json',s)
for n,h in pins.items():assert sha(PREP/n)==h
print(json.dumps(s))
