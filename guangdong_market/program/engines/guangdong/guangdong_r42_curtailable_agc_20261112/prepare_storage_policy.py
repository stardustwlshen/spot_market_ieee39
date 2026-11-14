from prepare import *
from fractions import Fraction as F
import importlib.util
B=BASE/'checked_worker_v1';P=BASE/'prepared_v1'
spec=importlib.util.spec_from_file_location('storageguard',B/'matrix_warning_guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
q=read(P/'template.json');names=read(P/'names.json');plan=read(P/'network_elimination_plan.json');a=plan['network_affine'];ix={v:i for i,v in enumerate(plan['kept_original_compact_columns'])};box={}
for indices,bounds in zip(a['injection_indices'],a['box_upper_by_cell']):
 for j,b in zip(indices,bounds):box[ix[j]]=b
profiles={}
for cutoff in (1e-9,1e-12):
 dropped=affected=0;largest=0.;worst=F(0);label=None
 for i,row in enumerate(q['rows']):
  loss=F(0);here=0
  for j,c in row['terms']:
   if abs(c)<=cutoff:
    need(j in box,'tiny outside bounded injection');loss+=abs(F(c))*F(box[j]);dropped+=1;here+=1;largest=max(largest,abs(c))
  affected+=bool(here)
  if loss>worst:worst=loss;label=names['constraints'][i]
 need(float(worst)<=1e-6,'error envelope')
 profiles[str(cutoff)]=dict(affected_rows=affected,exact_worst_row_activity_loss=str(worst),largest_potentially_ignored_abs_coefficient=largest,potentially_ignored_coefficients=dropped,worst_row_activity_loss_mw=float(worst),worst_row_name=label)
policy=dict(schema='gd.research.r42.storage_warning_policy.v1',request_math_sha256=g.math_digest(q),source_template_sha256=sha(P/'template.json'),source_plan_sha256=sha(P/'network_elimination_plan.json'),injection_box_by_reduced_column=box,threshold_profiles=profiles)
write(B/'STORAGE_POLICY.json',policy)
old=ROOT/'guangdong_r34_network_elimination_20261112/warning_checked_worker_v2/r34_checked_worker.py';s=old.read_text().replace('9efaf779e8098a2ffff2d0fe4a06c2dabdb51b7459c00b421fe770894532a539',sha(B/'STORAGE_POLICY.json')).replace('R34 storage','R42 storage');(B/'r42_checked_worker.py').write_text(s)
print(profiles);print('policy_sha256',sha(B/'STORAGE_POLICY.json'))
