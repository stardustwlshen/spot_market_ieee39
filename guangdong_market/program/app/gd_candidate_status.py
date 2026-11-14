"""Read candidate quarantine evidence after ordinary same-run source gates."""
from pathlib import Path
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def read_blocks(out,dispatch):
 root=Path(out)/'pipeline/dispatch';rows=[]
 for f in sorted(root.glob('W*/BEFORE_HANDOFF.json')):
  w=int(f.parent.name[1:]);z=read(f);need(z['branch_id']==dispatch['branch_id']and z['window']==w,'candidate blocker branch/window differs')
  for p,h in z['source_pins'].items():need(sha(p)==h,'candidate blocker raw/source drift')
  a=read(f.parent/'actual/ACCEPTED_PAIR.json');need(a['status']=='accepted_research_pair','candidate numerical acceptance missing')
  need(z['safety_release']is False and z['publication_allowed']is False,'candidate is not a safety release')
  acdir=f.parent/'CANDIDATE_AC';started=(acdir/'STARTED.json').exists();ac=read(acdir/'AC_RESULT.json')if(acdir/'AC_RESULT.json').exists()else None
  if ac:
   from candidate_safety import digest
   mapping=read(acdir/'MAPPING.json');need(ac['input_sha256']==digest(mapping['ac_input']),'candidate AC input mismatch')
   for item in read(acdir/'SOURCE.json')['sources']:need(sha(item['path'])==item['sha256'],'candidate AC mapping source drift')
  from gd_loss_status import inspect
  correction=inspect(f.parent,dispatch['branch_id'],w)
  rows.append(dict(loss_correction=correction,**z,numerical_pair_accepted=True,handoff_exists=(f.parent/'EXACT_CARRY.json').exists(),AC_calls_consumed=int(started)+(correction['extra_AC_attempted']if correction else 0),AC_result=ac,classification='safety_quarantine_NOT_numerical_failure',raw_and_EXIT_preserved=True))
 return rows
