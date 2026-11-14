"""Validate fresh pipeline model import/storage once; explicit zero optimization."""
from pathlib import Path
import json,hashlib
import highspy as hs
from matrix_warning_guard import verify,math_digest
B=Path(__file__).resolve().parent;read=lambda p:json.loads(p.read_bytes());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
d=read(Path('/workspace/scratch/42c5fe32dad0/guangdong_pipeline_20261112/pure_fresh_run_v1/da_scuc/template.json'));policy=read(B/'STORAGE_POLICY.json');h=hs.Highs();model=hs.HighsLp();n=len(d['variables']);rows=d['rows'];obj=d['objective'];model.num_col_=n;model.num_row_=len(rows);cost=[0.]*n
for j,c in obj['terms']:cost[j]+=c
model.col_cost_=cost;model.col_lower_=[-hs.kHighsInf if z['lb']is None else z['lb']for z in d['variables']];model.col_upper_=[hs.kHighsInf if z['ub']is None else z['ub']for z in d['variables']];model.row_lower_=[-hs.kHighsInf if z['lb']is None else z['lb']for z in rows];model.row_upper_=[hs.kHighsInf if z['ub']is None else z['ub']for z in rows];model.offset_=obj['constant'];model.sense_=hs.ObjSense.kMinimize if obj['minimize']else hs.ObjSense.kMaximize;model.integrality_=[hs.HighsVarType.kContinuous if z['kind']=='C'else hs.HighsVarType.kInteger for z in d['variables']];model.a_matrix_.format_=hs.MatrixFormat.kRowwise;starts=[0];ix=[];val=[]
for row in rows:
 for j,c in row['terms']:ix.append(j);val.append(c)
 starts.append(len(ix))
model.a_matrix_.start_=starts;model.a_matrix_.index_=ix;model.a_matrix_.value_=val
messages=[];cb=lambda e:messages.append(e.message);h.cbLogging.subscribe(cb);s=h.passModel(model);h.cbLogging.unsubscribe(cb)
r=verify(d,h,hs,s,messages,policy);r.update(optimize_calls=0,model_import_only=True,source_template_sha256=sha(Path('/workspace/scratch/42c5fe32dad0/guangdong_pipeline_20261112/pure_fresh_run_v1/da_scuc/template.json')),policy_sha256=sha(B/'STORAGE_POLICY.json'),guard_sha256=sha(B/'matrix_warning_guard.py'))
(B/'ACTUAL_STORAGE_IMPORT_VERIFICATION.json').write_text(json.dumps(r,sort_keys=True,separators=(',',':')));print(json.dumps(r))
