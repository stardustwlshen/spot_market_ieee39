"""Real matrix/primal/KKT acceptance; no native launcher and no boolean-only certificates."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,sys,importlib.util,math
from contracts import need,digest,fixed_sced
from runtime_config import dependency_root
ROOT=dependency_root()
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,allow_nan=False,sort_keys=True)
def checks(q,r,n,mip):
 sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
 from smm.gd_r23_equivalent_compression import audit
 need(r.get('solver_version')=='1.15.1'and r.get('backend')=='highs'and r.get('native_status')=='Optimal'and r.get('status')=='optimal'and r.get('has_primal')is True,'native optimal required')
 need(len(r['primal'])==len(q['variables'])and all(math.isfinite(v)for v in r['primal']),'finite full primal')
 a=audit(q,r['primal'],r['objective']);need(a['max_primal_violation']<=1e-6 and a['max_integer_violation']<=1e-6,'full primal/integer rejected')
 if mip:need(r['native_mip_gap']<=.001+1e-12,'MIP gap')
 else:
  path=ROOT/'guangdong_pipeline_20261112/da_math/scoped_kkt.py';spec=importlib.util.spec_from_file_location('_generic_strict_KKT',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);a['KKT']=m.metrics(q,r,n,angle_tolerance=1e-8);need(a['KKT']['passed'],'strict full SCED KKT')
 return a

def native_evidence(template,native,worker):
 native=Path(native);q=read(native/'request.json');r=read(native/'response.json');identity=read(native/'IDENTITY.json');complete=read(native/'COMPLETED.json')
 need({k:v for k,v in q.items()if k!='deadline'}==template,'actual mathematical/options mismatch')
 need(identity['worker_sha256']==sha(worker)and identity['request_sha256']==sha(native/'request.json'),'native worker/request identity')
 need(complete['returncode']==0 and complete['response_sha256']==sha(native/'response.json'),'native completion')
 need(all(r['native_settings'][k]==v for k,v in [('threads',1),('mip_rel_gap',.001),('mip_abs_gap',0.)]),'solver settings')
 return q,r

def prepare_sced(prepared,scuc_native,scuc_worker,output):
 p=Path(prepared);out=Path(output);ready=read(p/'READY.json')
 for f,h in ready['artifact_pins'].items():need(sha(p/f)==h,'prepared source changed')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'builder source changed')
 q,r=native_evidence(read(p/'template.json'),scuc_native,scuc_worker);n=read(p/'names.json');a=checks(q,r,n,True);lp=fixed_sced(read(p/'template.json'),r['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,time_limit=120.)
 out.mkdir(parents=True,exist_ok=False);write(out/'template.json',lp);write(out/'names.json',n);write(out/'SCUC_REVIEW.json',a);write(out/'SOURCE.json',dict(prepared=str(p.resolve()),prepared_sha256=sha(p/'READY.json'),SCUC_native=str(Path(scuc_native).resolve()),SCUC_worker=str(Path(scuc_worker).resolve()),SCUC_response_sha256=sha(Path(scuc_native)/'response.json'),fixed_new_RT_integers=True,native_calls=0))
 return out

def accept_pair(prepared,scuc_native,scuc_worker,sced_prepared,sced_native,sced_worker,output):
 p=Path(prepared);sp=Path(sced_prepared);request=read(p/'request.json');names=read(p/'names.json');q,u=native_evidence(read(p/'template.json'),scuc_native,scuc_worker);uc=checks(q,u,names,True)
 expected=fixed_sced(read(p/'template.json'),u['primal']);expected['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,time_limit=120.);need(read(sp/'template.json')==expected,'SCED not fixed accepted current SCUC')
 lp,ed=native_evidence(expected,sced_native,sced_worker);review=checks(lp,ed,names,False)
 need(all(abs(v-round(z))<=1e-8 for v,z,col in zip(ed['primal'],u['primal'],q['variables'])if col['kind']!='C'),'SCED discrete drift')
 paths=[p/'READY.json',p/'request.json',p/'template.json',p/'names.json',Path(scuc_worker),Path(sced_worker),Path(__file__)]+[Path(n)/f for n in (scuc_native,sced_native)for f in ('request.json','response.json','IDENTITY.json','COMPLETED.json')]
 z=dict(schema='gd.research.generic_rt.accepted_pair.v1',run_id=request['run_id'],window=request['window'],request_sha256=digest(request),SCUC_response_sha256=sha(Path(scuc_native)/'response.json'),SCED_response_sha256=sha(Path(sced_native)/'response.json'),SCUC_primal_integer_gap_passed=True,SCED_full_KKT_passed=True,SCUC_audit=uc,SCED_audit=review,request_path=str((p/'request.json').resolve()),names_path=str((p/'names.json').resolve()),SCED_response_path=str((Path(sced_native)/'response.json').resolve()),pins={str(f.resolve()):sha(f)for f in paths},formal=False,native_calls_this_review=0)
 write(output,z);return z

def load_verified(path,request):
 z=read(path);need(z['schema']=='gd.research.generic_rt.accepted_pair.v1'and z['run_id']==request['run_id']and z['window']==request['window']and z['request_sha256']==digest(request),'actual acceptance same run/window/request')
 for f,h in z['pins'].items():need(sha(f)==h,'acceptance evidence changed')
 need(z['pins'].get(str(Path(__file__).resolve()))==sha(__file__),'review implementation pin')
 need(z['SCUC_primal_integer_gap_passed']is True and z['SCED_full_KKT_passed']is True,'full acceptance required')
 n=read(z['names_path']);r=read(z['SCED_response_path']);need(sha(z['SCED_response_path'])==z['SCED_response_sha256'],'SCED response binding')
 return z,dict(zip(n['variables'],r['primal']))
