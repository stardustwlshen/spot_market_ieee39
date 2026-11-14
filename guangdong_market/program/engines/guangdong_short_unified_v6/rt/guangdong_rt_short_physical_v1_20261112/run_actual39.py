"""One bounded source-pinned joint48 research experiment; exclusive outputs, no retry."""
from pathlib import Path
import sys,json,hashlib,time,subprocess,gzip,os,threading
from copy import deepcopy
B=Path(__file__).resolve().parent;ROOT=B.parent;V=ROOT/'guangdong_rt_recommit_program_v32_20261112';LEG=ROOT/'guangdong_rt_recommit_program_v1_20261112'
sys.path[:0]=[str(LEG/'runtime'),str(LEG/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'),str(V)]
from evidence_io import save,load,write_json
from acceptance import checks
from contracts import fixed_sced
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def native(out,stage,q,worker,seconds):
 d=out/stage;d.mkdir();q=deepcopy(q);q['options']['time_limit']=seconds;q['deadline']=time.perf_counter()+seconds+300.
 qr=save(d/'request.json.gz',q);job=dict(worker=str(worker),worker_sha256=sha(worker),request=qr,response_path=str(d/'response.json.gz'));write_json(d/'JOB.json',job)
 with gzip.open(d/'worker.log.gz','wb')as log:
  proc=subprocess.Popen([sys.executable,str(V/'compressed_worker.py'),str(d/'JOB.json'),str(d/'COMPLETED.json')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  def drain():
   for block in iter(lambda:proc.stdout.read(65536),b''):log.write(block)
  reader=threading.Thread(target=drain,daemon=True);reader.start()
  try:rc=proc.wait(timeout=seconds+300.)
  except subprocess.TimeoutExpired:
   proc.kill();proc.wait();reader.join();write_json(d/'PENDING.json',dict(reason='process watchdog expired',solver_limit_s=seconds,transport_grace_s=300,automatic_retry=False));raise RuntimeError('native outcome pending after watchdog; no retry')
  reader.join()
 assert rc==0 and (d/'COMPLETED.json').exists(),'failed/unknown; no retry'
 r=load(read(d/'COMPLETED.json')['response']);assert r['optimize_calls']==1 and r['transport_solve_entries']==1
 assert all(r['native_settings'][k]==v for k,v in [('threads',1),('mip_rel_gap',q['options']['mip_rel_gap']),('mip_abs_gap',0.)])
 return load(qr),r

def main():
 src=B/'pure39_v2';s=read(src/'SUMMARY.json')
 for f,h in s['source_pins'].items():assert sha(f)==h,('source drift',f)
 out=B/'actual39_v1';out.mkdir(exist_ok=False)
 pins={str(p):sha(p)for p in [Path(__file__),V/'compressed_worker.py',V/'scuc_worker.py',V/'lp_worker.py',V/'evidence_io.py',*sorted((LEG/'runtime').glob('*.py')),*sorted((LEG/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm').glob('*.py'))]}
 write_json(out/'SOURCE.json',dict(source=s,artifact_pins={str(p):sha(p)for p in src.glob('*.json')},code_pins=pins,formal=False,safety_release=False))
 q=read(src/'template.json');n=read(src/'names.json');x=read(src/'WITNESS.json')['primal'];q['warmstart']=True
 for c,v in zip(q['variables'],x):c['start']=v
 save(out/'SCUC_template.json.gz',q)
 uq,u=native(out,'SCUC',q,V/'scuc_worker.py',300.);ua=checks(uq,u,n,True);write_json(out/'SCUC_REVIEW.json',ua)
 lp=fixed_sced(q,u['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);save(out/'SCED_template.json.gz',lp)
 eq,e=native(out,'SCED',lp,V/'lp_worker.py',120.);ea=checks(eq,e,n,False)
 assert all(abs(v-round(w))<=1e-8 for v,w,c in zip(e['primal'],u['primal'],q['variables'])if c['kind']!='C')
 z=dict(status='accepted_research_pair',SCUC_objective=u['objective'],SCUC_gap=u['native_mip_gap'],SCED_objective=e['objective'],SCUC_audit=ua,SCED_audit=ea,native_calls=2,formal=False,safety_release=False,full_network_terminal_certificate=False,domain_start=s['domain_start'],domain_end=s['domain_end'])
 write_json(out/'ACCEPTED_PAIR.json',z);print(json.dumps(z))
if __name__=='__main__':main()
