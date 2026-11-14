from pathlib import Path
import json,hashlib,importlib.util
from fractions import Fraction as F
B=Path(__file__).resolve().parent;P=B.parent/'pure_fresh_run_v1/da_scuc';read=lambda p:json.loads(p.read_bytes());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
from matrix_warning_guard import math_digest
q=read(P/'template.json');names=read(P/'names.json');plan=read(P/'network_plan.json');a=plan['network_affine'];ix={v:i for i,v in enumerate(plan['kept_original_compact_columns'])};box={}
for indices,bounds in zip(a['injection_indices'],a['box_upper_by_cell']):
 for j,b in zip(indices,bounds):box[ix[j]]=b
profiles={}
for cutoff in (1e-9,1e-12):
 dropped=affected=0;largest=0.;worst=F(0);label=None
 for i,row in enumerate(q['rows']):
  loss=F(0);here=0
  for j,c in row['terms']:
   if abs(c)<=cutoff:
    assert j in box;loss+=abs(F(c))*F(box[j]);dropped+=1;here+=1;largest=max(largest,abs(c))
  affected+=bool(here)
  if loss>worst:worst=loss;label=names['constraints'][i]
 assert float(worst)<=1e-6
 profiles[str(cutoff)]=dict(affected_rows=affected,exact_worst_row_activity_loss=str(worst),largest_potentially_ignored_abs_coefficient=largest,potentially_ignored_coefficients=dropped,worst_row_activity_loss_mw=float(worst),worst_row_name=label)
policy=dict(schema='gd.pipeline.checked_storage_policy.v1',request_math_sha256=math_digest(q),source_template_sha256=sha(P/'template.json'),source_plan_sha256=sha(P/'network_plan.json'),injection_box_by_reduced_column=box,threshold_profiles=profiles)
(B/'STORAGE_POLICY.json').write_text(json.dumps(policy,sort_keys=True,separators=(',',':')))
s=(B.parent/'da_math/mip_worker.py').read_text();s=s.replace('import time\n','import time\nfrom pathlib import Path\nfrom hashlib import sha256\nfrom matrix_warning_guard import verify as verify_stored_model\n')
block="""    policy_path=Path(__file__).with_name('STORAGE_POLICY.json')
    if sha256(policy_path.read_bytes()).hexdigest()!='POLICY_SHA':raise RuntimeError('fresh storage policy hash changed')
    policy=json.loads(policy_path.read_bytes());messages=[]
    def capture_log(event):messages.append(event.message)
    h.cbLogging.subscribe(capture_log)
    try:pass_status=h.passModel(model)
    finally:h.cbLogging.unsubscribe(capture_log)
    storage=verify_stored_model(data,h,hs,pass_status,messages,policy)""".replace('POLICY_SHA',sha(B/'STORAGE_POLICY.json'))
s=s.replace("    checked(h.passModel(model),'linear model')",block).replace('native_settings=applied)','native_settings=applied,matrix_storage_verification=storage)');(B/'checked_mip_worker.py').write_text(s)
print(json.dumps(profiles))
