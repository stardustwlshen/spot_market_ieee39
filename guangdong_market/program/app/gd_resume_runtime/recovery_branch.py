"""Derive only logical identity from immutable bound window records.
Missing historical aggregate remains missing. This is not a restored live object,
new mathematical acceptance, or safety authorization.
"""
from pathlib import Path
import json,os
from content_hash import sha

def need(ok,message):
 if not ok:raise ValueError(message)

def pinned_value(pins,path):
 """Original lexical keys may now resolve through verified directory aliases."""
 target=Path(path).resolve();values={v for k,v in pins.items()if Path(k).resolve()==target}
 need(len(values)<=1,'同一来源路径有冲突摘要')
 return next(iter(values))if values else None

def identify(source,pins):
 source=Path(source).resolve();bound={}
 for path,value in pins.items():
  key=str(Path(path).resolve())
  need(key not in bound or bound[key]==value,'历史来源别名摘要冲突')
  bound[key]=value
 records=[];branches=set()
 for w in range(96):
  p=source/'pipeline/dispatch'/f'W{w}/RESULT.json'
  if not p.is_file():break
  key=str(p.resolve());need(bound.get(key)==sha(p),'历史窗口结果未绑定或已改变')
  z=json.loads(p.read_bytes());need(z.get('window')==w,'历史窗口索引不连续')
  b=z.get('branch_id');need(isinstance(b,str)and bool(b),'历史窗口缺逻辑分支')
  branches.add(b);records.append(dict(window=w,path=key,sha256=bound[key]))
 need(bool(records)and len(branches)==1,'不能从同一连续已绑定前缀确定分支')
 branch=next(iter(branches));aggregate=source/'pipeline/dispatch/RESULT.json';aggregate_record=None
 if aggregate.is_file():
  key=str(aggregate.resolve());need(bound.get(key)==sha(aggregate),'历史聚合结果未绑定或已改变')
  z=json.loads(aggregate.read_bytes());need(z.get('branch_id')==branch,'聚合与固定窗口分支不一致')
  aggregate_record=dict(path=key,sha256=bound[key])
 return dict(schema='gd.resume.immutable_branch_index.v1',branch_id=branch,
  source_output=str(source),historical_aggregate_available=aggregate_record is not None,
  historical_aggregate=aggregate_record,window_records=records,
  derived_from_bound_immutable_windows=True,old_files_modified=False,
  new_native_calls=0,new_math_acceptance=False,safety_release=False,formal=False)

def record_index(output,proof,attempt):
 p=Path(output)/'RECOVERY_BRANCH_INDEX.json';z={**proof,'execution_attempt_id':attempt}
 with p.open('x')as f:json.dump(z,f,indent=2);f.flush();os.fsync(f.fileno())
 return p
