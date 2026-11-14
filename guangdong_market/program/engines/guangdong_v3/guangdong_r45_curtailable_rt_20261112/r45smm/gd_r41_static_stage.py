"""Ephemeral same-stage static-source verification, not a capability or mtime cache.

Static inputs are read into sealed memory before a stage. Every entry/exit hashes
all bound files; repeated *within-stage* validation still checks the complete
immutable inventory, but does not reread identical files. Out-of-scope/foreign
permits always perform a full check. No optimization or file writes here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
import json,time
_CURRENT=ContextVar('r41_static_source_stage',default=None)

def require(ok,message):
 if not ok:raise ValueError('R41 static source boundary: '+message)
def _inventory(files):
 require(type(files)is dict and bool(files),'nonempty bound file inventory')
 result=tuple(sorted(files.items()))
 for path,h in result:require(type(path)is str and type(h)is str and len(h)==64,'explicit file/hash pair')
 return result
def _digest(records):return sha256(json.dumps(records,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def _hash_file(path):
 h=sha256();size=0
 with Path(path).open('rb')as f:
  while True:
   b=f.read(1024*1024)
   if not b:break
   h.update(b);size+=len(b)
 return h.hexdigest(),size
def _full_check(inventory):
 total=0;start=time.perf_counter()
 for path,expected in inventory:
  h,size=_hash_file(path);require(h==expected,'bound file changed: '+path);total+=size
 return dict(files=len(inventory),bytes=total,elapsed_s=time.perf_counter()-start)
def verify(permit,files):
 """Call only after ordinary contract/DTO/memory signatures have passed."""
 inventory=_inventory(files);current=_CURRENT.get()
 if current is not None and current['permit'] is permit:
  require(current['active'] and current['inventory']==inventory,'in-scope inventory mutated')
  current['report']['reused_in_stage_checks']+=1
  return
 _full_check(inventory)

@contextmanager
def scope(permit,files,*,run_id,index,stage):
 require(_CURRENT.get()is None,'nested or reused stage scope forbidden')
 require(type(run_id)is str and bool(run_id)and type(index)is int and 0<=index<96 and stage in ('RT','PRICE'),'explicit native stage binding')
 inventory=_inventory(files);before=_full_check(inventory)
 report=dict(schema='gd.research.r41.static_stage_boundaries.v1',run_id=run_id,window_index=index,stage=stage,inventory_sha256=_digest(inventory),entry=before,exit=None,reused_in_stage_checks=0,mtime_cache=False,valid_for_other_stage=False,completed=False)
 state=dict(permit=permit,inventory=inventory,active=True,report=report)
 token=_CURRENT.set(state)
 try:
  yield report
 finally:
  try:
   # A changed inventory or file rejects before caller can accept/issue anything.
   require(_inventory(files)==inventory,'inventory changed during stage')
   report['exit']=_full_check(inventory);report['completed']=True
  finally:
   state['active']=False;_CURRENT.reset(token)
