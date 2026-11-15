"""Explicit one-shot SCED precision policy. No fallback, no changes to SCUC/PRICE.
Install only for a registered host's future occurrences; original modules remain intact.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,json
B=Path(__file__).resolve().parent
WORKER=B/'precision_worker.py'
SETTINGS=dict(presolve='off',solver='ipm',run_crossover='on',primal_feasibility_tolerance=1e-8,dual_feasibility_tolerance=1e-10,ipm_optimality_tolerance=1e-10,simplex_scale_strategy=0,threads=1,time_limit=3600.)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def request(q):
 z=deepcopy(q);z['options'].update(SETTINGS)
 assert all(v['kind']=='C' for v in z['variables']), 'fixed integer continuous SCED required'
 assert all(z[k]==q[k] for k in q if k!='options')
 return z
class Proxy:
 def __init__(self,base):self._base=base
 def __getattr__(self,k):return getattr(self._base,k)
class Policy:
 def __init__(self,program):
  self.original=program;self.pins={str(p):sha(p) for p in (Path(__file__).resolve(),WORKER,B/'stage_status.py')}
 def verify(self):
  assert all(sha(p)==h for p,h in self.pins.items()),'precision source drift'
 def execute(self,out,stage,q,worker,seconds):
  self.verify()
  if stage!='SCED':return self.original.engine.worker.native(out,stage,q,worker,seconds)
  assert seconds==3600. and Path(worker).resolve()==(self.original.B/'lp_ipm_worker.py').resolve(),'unknown SCED protocol'
  assert not (Path(out)/stage).exists(),'existing/unknown occurrence; no retry'
  return self.original.engine.worker.native(out,stage,request(q),WORKER,3600.)
 def stage(self,p,name,q,n,mip):
  self.verify();d=Path(p)/'actual'/name
  if mip or name!='SCED' or not d.exists():return self.original.stage(p,name,q,n,mip)
  read=self.original.read;need=self.original.need
  need((d/'JOB.json').exists() and (d/'COMPLETED.json').exists(),'unknown precision occurrence; no retry')
  job=read(d/'JOB.json');done=read(d/'COMPLETED.json')
  # Older occurrences are never relabeled under this numerical policy.
  if Path(job['worker']).resolve()!=WORKER.resolve():return self.original.stage(p,name,q,n,mip)
  need(job['worker_sha256']==sha(WORKER)==done['worker_sha256'],'precision worker binding')
  need(Path(job['request']['path']).resolve()==(d/'request.json.gz').resolve() and Path(job['response_path']).resolve()==(d/'response.json.gz').resolve() and Path(done['response']['path']).resolve()==(d/'response.json.gz').resolve(),'precision canonical paths')
  actual=self.original.load(job['request']);r=self.original.load(done['response']);expected=request(q)
  need({k:v for k,v in actual.items() if k!='deadline'}=={k:v for k,v in expected.items() if k!='deadline'},'precision full request mismatch')
  from .stage_status import require_optimal_sced
  require_optimal_sced(r,actual,done)
  need(done['solve_entries']==r['optimize_calls']==r['transport_solve_entries']==1,'precision single call count')
  need(all(r['native_settings'][k]==v for k,v in SETTINGS.items() if k!='time_limit'),'explicit precision readback mismatch')
  need(r['time_settings']['wall_time_limit_s']==3600.,'precision time budget')
  self.original.checks(actual,r,n,False)
  return actual,r
 def program_proxy(self):
  p=Proxy(self.original);p.engine=Proxy(self.original.engine);p.engine.worker=Proxy(self.original.engine.worker)
  p.engine.worker.native=self.execute;p.stage=self.stage
  return p

def install(host):
 """Instance-level explicit producer proxy; never mutates global program modules."""
 host.session.verify_sources()
 assert not hasattr(host,'sced_precision_policy'),'already installed'
 policy=Policy(host.session.program)
 host.session.registry['source_pins'].update(policy.pins)
 host.session.program=policy.program_proxy();host.sced_precision_policy=policy
 host.session.verify_sources()
 return policy
