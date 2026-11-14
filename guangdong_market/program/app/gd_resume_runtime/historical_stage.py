"""Read completed workers from a pinned earlier installation, never execute aliases.
Absolute executable relocation is permitted only for identical worker bytes.
All request/model/readback/counter and normal mathematical checks remain mandatory.
"""
from pathlib import Path
from copy import deepcopy
from .engine import need,sha,read,verify

def install(host,source_pins):
 program=host.session.program;original=program.stage
 from lifecycle_runtime.gd_sced_precision_runtime.policy import WORKER,SETTINGS,request
 from lifecycle_runtime.gd_sced_precision_runtime.stage_status import require_optimal_sced
 allowed={str(Path(p).resolve())for p in source_pins if Path(p).name=='JOB.json'}
 memo={}
 def stage(parent,name,q,n,mip):
  d=Path(parent)/'actual'/name;jobpath=d/'JOB.json'
  if str(jobpath.resolve())not in allowed:return original(parent,name,q,n,mip)
  for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz'):
   p=(d/f).resolve();need(str(p)in source_pins and sha(p)==source_pins[str(p)],'历史原生字节未绑定')
  job=read(jobpath);done=read(d/'COMPLETED.json');worker=program.engine.worker.V/'scuc_worker.py'if mip else WORKER
  need(sha(job['worker'])==job['worker_sha256']==done['worker_sha256']==sha(worker),'迁移worker字节不等')
  need(Path(job['request']['path']).resolve()==(d/'request.json.gz').resolve()and Path(job['response_path']).resolve()==(d/'response.json.gz').resolve()and Path(done['response']['path']).resolve()==(d/'response.json.gz').resolve(),'原生来源路径不等')
  actual=program.load(job['request']);r=program.load(done['response']);expected=deepcopy(q)if mip else request(q)
  if mip:expected['options']['time_limit']=300.
  need({k:v for k,v in actual.items()if k!='deadline'}=={k:v for k,v in expected.items()if k!='deadline'},'历史解对应完整模型不等')
  need(done['solve_entries']==r['optimize_calls']==r['transport_solve_entries']==1,'历史调用数不等1')
  if mip:need(all(r['native_settings'][k]==v for k,v in [('threads',1),('mip_rel_gap',.001),('mip_abs_gap',0.)]),'历史SCUC设置改变')
  else:
   require_optimal_sced(r,actual,done);need(all(r['native_settings'][k]==v for k,v in SETTINGS.items()if k!='time_limit')and r['time_settings']['wall_time_limit_s']==120.,'历史SCED设置改变')
  from gd_post_scuc_runtime.phase_runtime import digest
  key=(str(jobpath.resolve()),digest(actual),digest(n))
  if key not in memo:program.checks(actual,r,n,mip);memo[key]=True
  return actual,r
 program.stage=stage
 return dict(scope='pinned historical completed-stage read only; identical worker bytes',memo=memo)
