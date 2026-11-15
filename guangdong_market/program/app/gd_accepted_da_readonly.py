"""Read-only historic accepted DA authentication; creates no acceptance wrapper."""
from pathlib import Path
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify(da,base_run):
 root=Path(da).resolve();base=Path(base_run).resolve();art=read(root/'ARTIFACTS.json');ctx=read(base/'SHORT_CONTEXT.json')
 if art.get('schema')!='gd.pipeline.da_artifacts.v1' or ctx.get('schema')!='gd.short.user.context.v2':raise ValueError('不支持的日前/基础运行schema')
 if Path(ctx['selected_DA']).resolve()!=root or ctx['selected_DA_run_id']!=art['run_id']:raise ValueError('基础运行绑定的是另一日前来源')
 def check(rec):
  p=(root/rec['path']).resolve()
  if not p.is_relative_to(root) or sha(p)!=rec['sha256']:raise ValueError('日前源文件已变化：'+rec['path'])
  return p
 for rec in art['inputs'].values():check(rec)
 for stage in ('da_scuc','da_sced','da_price'):
  refs=art['stages'][stage];accepted=read(check(refs['acceptance']))
  if accepted.get('schema')!='gd.pipeline.acceptance.v1' or accepted.get('stage')!=stage or accepted.get('status')!='accepted':raise ValueError('日前阶段未接受')
  for field in ('run_id','input_identity','source_identity'):
   if accepted.get(field)!=art.get(field):raise ValueError('日前接受身份不一致')
  for rec in refs.values():check(rec)
  for rec in accepted['artifacts'].values():check(rec)
 if 'physical_solution' not in art['stages']['da_sced']:raise ValueError('缺日前物理解')
 for path,h in ctx['seed_pins'].items():
  if sha(path)!=h:raise ValueError('基础运行输入已变化：'+path)
 return dict(run=str(root),base_run=str(base),ARTIFACTS_sha256=sha(root/'ARTIFACTS.json'),context_sha256=sha(base/'SHORT_CONTEXT.json'),run_id=art['run_id'],route='readonly_legacy_accepted',formal=False)
