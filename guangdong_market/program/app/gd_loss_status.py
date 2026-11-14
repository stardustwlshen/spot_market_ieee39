"""Display-only source-bound correction evidence. Never grants execution authority."""
from pathlib import Path
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def inspect(window,branch_id,index):
 root=Path(window)/'loss_correction_1'
 if not root.exists():return None
 ctx=read(root/'CONTEXT.json');c=ctx['context'];need(c['branch_id']==branch_id and c['window']==index,'loss candidate branch/window mismatch')
 original=Path(window)/'actual'
 for name in ('request','response'):need(sha(original/'SCED'/f'{name}.json.gz')==c[f'SCED_{name}_sha256'],'loss original raw changed')
 pair=root/'actual/ACCEPTED_PAIR.json';accepted=read(pair)if pair.exists()else None
 if accepted:
  need(accepted['status']=='accepted_research_pair' and accepted['safety_release']is False,'loss numerical acceptance invalid')
  need(sha(root/'actual/SCED/response.json.gz')==accepted['SCED_response_sha256'],'loss corrected raw changed')
 ac=read(root/'AC_RESULT.json')if(root/'AC_RESULT.json').exists()else None
 if ac:
  from candidate_safety import digest
  inp=read(root/'AC_INPUT.json');need(ac['input_sha256']==digest(inp['mapping']['ac_input']),'loss second AC input changed')
  for item in inp['source']['sources']:need(sha(item['path'])==item['sha256'],'loss corrected AC source drift')
 result=read(root/'RESULT.json')if(root/'RESULT.json').exists()else None
 if result:need(result['handoff_allowed']is False and result['safety_release']is False,'correction cannot authorize handoff')
 return dict(label='同候选损耗纠正诊断',status=result['status']if result else 'incomplete_or_unknown_no_retry',context=ctx,corrected_numerical_acceptance=accepted,second_AC=ac,result=result,extra_SCED_attempted=int((root/'STARTED.json').exists()),extra_AC_attempted=int((root/'AC_STARTED.json').exists()),handoff_allowed=False,read_only_not_live_authority=True)
