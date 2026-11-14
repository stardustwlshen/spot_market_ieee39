"""One actual fixed-commitment SCED sourced from the accepted free SCUC."""
from pathlib import Path
import json,time,hashlib,subprocess,sys,os,signal,resource
B=Path(__file__).resolve().parent;R=B.parent;P=B/'actual_review_v1/sced_prepared_v1';O=B/'sced_native_v1';S=R/'guangdong_after_reset_r23_compressed_20261112/repo/src';sys.path.insert(0,str(S))
from smm import highs_solver as hs
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def enc(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def write(p,v):
 with p.open('xb')as f:f.write(enc(v))
def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,128*1024**2))
assert sha(P/'PREPARATION.json')=='5c36724a29dbef730dd8a50721a179fcd9f3fb070cf2e24f9104bd2a0cdb9a5f';m=json.loads((P/'PREPARATION.json').read_bytes());assert m['SCUC_accepted'] and m['native_calls']==0 and m['native_calls_allowed']==1
for n,h in m['artifact_hashes'].items():assert sha(P/n)==h
for item in m['SCUC_event_pins'].values():assert sha(item['path'])==item['sha256']
assert sha(m['SCUC_review_path'])==m['SCUC_review_sha256']
agc_path=B/'actual_review_v1/ACTUAL_AGC_SELECTION_TO_SCED.json';assert sha(agc_path)=='988d6820ab2cba17124475366dd163cd40f3767f3360256845505813db7f86f0';agc=json.loads(agc_path.read_bytes())
q=json.loads((P/'template.json').read_bytes());assert q['dual'] and not q['warmstart'] and all(v['kind']=='C'for v in q['variables']);assert q['options']['time_limit']==60 and q['preparation_elapsed_s']==0
w=S/'smm/highs_worker.py';assert sha(w)=='dc439a81e2cb2eb2a3595d24f0c8ead08ffa633387a259a868f1d0d9fcdc46ea';body=enc(q);O.mkdir(exist_ok=False);deadline=time.perf_counter()+60.;(O/'request.json').write_bytes(b'{"deadline":'+repr(deadline).encode()+b','+body[1:]);write(O/'IDENTITY.json',{'schema':'gd.research.r42.curtailable_D_AGC_fixed_SCED.native.v1','request_sha256':sha(O/'request.json'),'template_sha256':sha(P/'template.json'),'preparation_sha256':sha(P/'PREPARATION.json'),'worker_sha256':sha(w),'SCUC_accepted':True,'SCUC_actual_event_pins':m['SCUC_event_pins'],'automatic_retry':False,'formal_publication_allowed':False,'AGC_selection_sha256':sha(agc_path),'AGC_selection':agc,'curtailment_penalty_research_yuan_mwh':1000})
start=time.monotonic()
with(O/'worker.log').open('xb')as log:
 p=subprocess.Popen([sys.executable,'-B',str(w),str(O/'request.json'),str(O/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True,preexec_fn=limits);write(O/'launch.json',{'pid':p.pid,'attempt':1,'worker_sha256':sha(w)})
 while p.poll()is None:
  if (B/'CANCEL_REQUESTED').exists():os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=5);write(O/'CANCELLED.json',{'pid':p.pid,'accepted':False});raise SystemExit(2)
  if time.perf_counter()>deadline+5:os.killpg(p.pid,signal.SIGKILL);p.wait();write(O/'OUTER_STOP.json',{'pid':p.pid,'accepted':False});raise TimeoutError('outer deadline')
  time.sleep(.25)
assert p.returncode==0;r=json.loads((O/'response.json').read_bytes())
if 'error'in r:write(O/'FAILED_RESULT.json',r);raise RuntimeError(r['error'])
assert r['backend']=='highs'and r['solver_version']=='1.15.1';v={k:r.get(k)for k in('native_status','status','has_primal','dual_valid','objective','bound','native_settings','time_settings')};v.update(pid=p.pid,returncode=p.returncode,measured_worker_elapsed_s=time.monotonic()-start,response_sha256=sha(O/'response.json'),SCUC_accepted=True,formal_publication_allowed=False,automatic_retry=False);write(O/'CONFIRMED_NATIVE_RESULT.json',v);print(json.dumps(v))
