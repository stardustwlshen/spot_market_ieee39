"""Registered completed-chain sources. Discovery is never 'latest file wins'."""
from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def registered(source):
 r=read(B/'RESUME_CHAIN_REGISTRATION.json');entry=r.get(str(Path(source).resolve()))
 if entry is None:return None
 path=(B/entry['manifest']).resolve()
 if not path.is_relative_to(B.parent)or sha(path)!=entry['sha256']:raise ValueError('已完成接续清单来源改变')
 z=read(path)
 if z['source_output']!=str(Path(source).resolve())or z['new_authority_from_JSON']is not False:raise ValueError('接续清单身份不匹配')
 for f,h in z['source_pins'].items():
  if sha(f)!=h:raise ValueError('已完成接续来源缺失或改变，禁止回退重算：'+f)
 return z
