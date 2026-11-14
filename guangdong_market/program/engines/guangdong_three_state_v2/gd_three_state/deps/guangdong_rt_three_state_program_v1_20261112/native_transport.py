from pathlib import Path
from copy import deepcopy
import sys,os,json,time,subprocess,gzip,threading
from evidence_io import save,load,write_json,sha
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
filehash=lambda p:sha(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def native(r,stage,q,worker):
 p=r/stage;need(not p.exists(),'existing native occurrence forbidden; no retry');p.mkdir()
 q=deepcopy(q);q['deadline']=time.perf_counter()+q['options']['time_limit'];rq=save(p/'request.json.gz',q)
 job=dict(request=rq,worker=str(worker),worker_sha256=filehash(worker),response_path=str(p/'response.json.gz'));write_json(p/'JOB.json',job)
 proc=subprocess.Popen([sys.executable,'-B',str(B/'compressed_worker.py'),str(p/'JOB.json'),str(p/'RESPONSE_REF.json')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'})
 write_json(p/'LAUNCH.json',dict(pid=proc.pid,attempt=1,wrapper_sha256=filehash(B/'compressed_worker.py'),job_sha256=filehash(p/'JOB.json'),automatic_retry=False))
 def pump():
  with (p/'worker.log.gz').open('xb')as f:
   with gzip.GzipFile(fileobj=f,mode='wb',mtime=0)as g:
    while True:
     data=proc.stdout.read(65536)
     if not data:break
     g.write(data)
 t=threading.Thread(target=pump);t.start()
 try:code=proc.wait(timeout=q['options']['time_limit']+30)
 except subprocess.TimeoutExpired:
  proc.terminate();proc.wait(timeout=10);t.join();write_json(p/'PENDING.json',{'retry':False});raise RuntimeError('native outcome pending; no retry')
 t.join();raw=gzip.decompress((p/'worker.log.gz').read_bytes());data=(p/'worker.log.gz').read_bytes();log=dict(schema='gd.evidence.gzip.v2',path=str(p/'worker.log.gz'),codec='gzip',stored_sha256=sha(data),raw_sha256=sha(raw),raw_bytes=len(raw),stored_bytes=len(data))
 need(code==0 and (p/'RESPONSE_REF.json').exists(),'worker response missing');rr=read(p/'RESPONSE_REF.json')['response'];response=load(rr)
 z=dict(returncode=code,request=rq,response=rr,log=log,worker=str(worker),worker_sha256=filehash(worker),wrapper_sha256=filehash(B/'compressed_worker.py'),job_sha256=filehash(p/'JOB.json'));write_json(p/'COMPLETED.json',z)
 return q,response,z
