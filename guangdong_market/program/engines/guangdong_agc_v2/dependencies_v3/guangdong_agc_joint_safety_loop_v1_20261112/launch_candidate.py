"""Main-thread explicit one-shot native launcher; no automatic retry."""
from loop import *
import argparse,subprocess,time,os

def write(p,z):
 with p.open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--candidate',required=True);ap.add_argument('--output',required=True);ap.add_argument('--execute-native',action='store_true');a=ap.parse_args();need(a.execute_native,'explicit native authorization flag');C=Path(a.candidate);N=Path(a.output);ready=read(C/'READY.json')
 for ref in ready['artifacts'].values():resolve(ref)
 physical=read(C/'PHYSICAL_REQUEST.json')
 for path,h in physical['producer_pins'].items():need(sha(path)==h,'producer changed')
 need(not N.exists(),'attempt exists: inspect outcome; no automatic retry');N.mkdir();worker=Path(physical['native_worker']['path']);need(sha(worker)==physical['native_worker']['sha256'],'worker identity');q=read(C/'template.json');q['deadline']=time.perf_counter()+q['options']['time_limit'];write(N/'request.json',q);write(N/'IDENTITY.json',dict(binding=physical['binding'],physical_request_sha256=digest(physical),worker_sha256=sha(worker),launcher_sha256=sha(__file__),template_sha256=sha(C/'template.json'),request_sha256=sha(N/'request.json'),automatic_retry=False))
 with(N/'worker.log').open('x')as f:
  child=subprocess.Popen([sys.executable,'-B',str(worker),str(N/'request.json'),str(N/'response.json')],stdout=f,stderr=f,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'});write(N/'LAUNCH.json',dict(pid=child.pid,request_sha256=sha(N/'request.json'),worker_sha256=sha(worker),launcher_sha256=sha(__file__)))
  try:code=child.wait(timeout=q['options']['time_limit']+30)
  except subprocess.TimeoutExpired:
   child.terminate();write(N/'PENDING.json',dict(reason='native timeout, inspect outcome; no automatic retry'));raise
 need((N/'response.json').exists(),'native response missing, unknown');write(N/'COMPLETED.json',dict(returncode=code,pid=child.pid,request_sha256=sha(N/'request.json'),response_sha256=sha(N/'response.json'),worker_sha256=sha(worker)));print(N)
if __name__=='__main__':main()
