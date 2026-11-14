"""Explicit relocation proof, never a live permission or rewritten old source."""
from pathlib import Path
from copy import deepcopy
from .engine import read,sha,verify,need

def compare(old,new,*,solver_policy=None):
 old,new=Path(old),Path(new);pins={}
 old_seed={f.name for f in (old/'seed').glob('*.json')};new_seed={f.name for f in (new/'seed').glob('*.json')}
 need(old_seed==new_seed,'完整种子文件清单改变')
 for rel in ['seed/'+name for name in sorted(old_seed)]:
  a,b=old/rel,new/rel;need(a.read_bytes()==b.read_bytes(),'新上下文数学输入不同：'+rel)
  pins.update({str(a):sha(a),str(b):sha(b)})
 for rel,excluded in [('SHORT_CONTEXT.json',('code_pins','seed_pins')),('INITIAL_CARRY.json',('source_pins','day_declaration_path'))]:
  a,b=read(old/rel),read(new/rel)
  for obj in (a,b):
   for k in excluded:
    if k.endswith('pins'):verify(obj[k]);pins.update(obj[k])
  need({k:v for k,v in a.items()if k not in excluded}=={k:v for k,v in b.items()if k not in excluded},'新上下文状态不同：'+rel)
  if rel=='SHORT_CONTEXT.json':
   def relative_sources(obj):return {str(p).split('/engines/',1)[1]:h for p,h in obj['code_pins'].items()}
   aa,bb=relative_sources(a),relative_sources(b)
   from .solver_policy_compat import permits
   need(set(aa)==set(bb)and all(aa[k]==bb[k]or permits(solver_policy,'engines/'+k,aa[k],bb[k])for k in aa),'短域计算源码改变')
  else:
   for obj in (a,b):need(sha(obj['day_declaration_path'])==obj['day_declaration_sha256'],'日声明绑定失效')
   need(read(a['day_declaration_path'])==read(b['day_declaration_path']),'日声明数值改变')
  pins.update({str(old/rel):sha(old/rel),str(new/rel):sha(new/rel)})
 return dict(old_run=str(old),new_run=str(new),mathematical_inputs_and_initial_state_equal=True,metadata_relocation_only=True,source_pins=pins)

def d1(old,new,oldrun,newrun):
 a,b=read(old),read(new);need(a['run']==str(oldrun) and b['run']==str(newrun),'D1上下文错误')
 excluded=('run','declaration','declaration_sha256')
 need({k:v for k,v in a.items()if k not in excluded}=={k:v for k,v in b.items()if k not in excluded},'D1选择语义改变')
 values=[];pins={str(old):sha(old),str(new):sha(new)}
 for obj in (a,b):
  p=obj['declaration'];need(sha(p)==obj['declaration_sha256'],'D1声明失效');v=deepcopy(read(p));r=v['research_extension'];need(sha(r['source'])==r['source_sha256'],'D1研究来源失效');pins.update({p:sha(p),r['source']:sha(r['source'])});r.pop('source');values.append(v)
 need(values[0]==values[1],'D1完整声明数值改变')
 return dict(source_pins=pins,metadata_relocation_only=True)
