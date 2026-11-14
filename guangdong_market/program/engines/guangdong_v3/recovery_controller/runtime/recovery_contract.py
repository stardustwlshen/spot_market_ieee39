"""Explicit source-pinned import of an already executed candidate; never launches native."""
from pathlib import Path
import json,hashlib
RECOVERY=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
digest=lambda z:hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def verify_recovery(run):
 run=Path(run).resolve();z=read(run/'SCED_RECOVERY_REVISION.json');r=read(run/'RUN.json')
 need(z['run_id']==r['run_id']and z['base_source_identity']==r['source_identity']and z['input_identity']==r['input_identity'],'recovery run identity')
 need(z['recovery_source_identity']==digest(z['source_pins']),'recovery source identity')
 for p,h in z['source_pins'].items():need(sha(p)==h,'recovery implementation changed '+p)
 for p,h in z['base_evidence'].items():need(sha(run/p)==h,'base accepted/failed evidence changed '+p)
 for p,h in z['candidate_pins'].items():need(sha(p)==h,'candidate evidence changed '+p)
 return z

def candidate_check(run,plan,template,worker,native):
 run=Path(run).resolve();plan=Path(plan).resolve();template=Path(template).resolve();worker=Path(worker).resolve();native=Path(native).resolve()
 z=read(plan);q=read(template);actual=read(native/'request.json');r=read(native/'response.json');old=read(run/'da_sced/native_v2/request.json')
 need(Path(z['base_run']).resolve()==run and z['base_SCuc_acceptance_sha256']==sha(run/'da_scuc/ACCEPTANCE.json'),'candidate base SCUC identity')
 need(z['failed_sced_request_sha256']==sha(run/'da_sced/native_v2/request.json')and z['failed_sced_response_sha256']==sha(run/'da_sced/native_v2/response.json'),'base failed SCED identity')
 need(z['worker_sha256']==sha(worker)and z['template_sha256']==sha(template),'candidate worker/template source hash')
 need({k:v for k,v in actual.items()if k!='deadline'}==q,'candidate actual request mismatch')
 need({k:v for k,v in old.items()if k not in ('options','deadline')}=={k:v for k,v in q.items()if k!='options'},'candidate changed mathematics or other input')
 allowed={'solver','primal_feasibility_tolerance','ipm_optimality_tolerance','run_crossover','output_flag'}
 diff={k:dict(old=old['options'].get(k),new=q['options'].get(k))for k in set(old['options'])|set(q['options'])if old['options'].get(k)!=q['options'].get(k)}
 need(set(diff)<=allowed and diff==z['algorithm_only_options_diff'],'algorithm-only option delta differs')
 expected=dict(threads=1,mip_rel_gap=.001,mip_abs_gap=0.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,primal_feasibility_tolerance=1e-10,ipm_optimality_tolerance=1e-12,solver='ipm')
 need(all(q['options'].get(k)==v for k,v in expected.items())and q['options']['run_crossover']in('on','off'),'candidate exact options')
 need(r.get('status')=='optimal'and r.get('native_status')=='Optimal'and r.get('has_primal')and r.get('dual_valid'),'candidate not optimal LP')
 need(r.get('backend')=='highs'and r.get('solver_version')=='1.15.1','candidate solver')
 need(all(r['native_settings'].get(k)==v for k,v in q['options'].items()if k not in ('time_limit','solve_relaxation')),'native option readback')
 need(q['options'].get('solve_relaxation')is False and all(v['kind']=='C'for v in q['variables']),'fixed LP only; no relaxation change')
 done=read(native/'COMPLETED.json');need(done['returncode']==0 and done['response_sha256']==sha(native/'response.json'),'candidate completion')
 pins={str(p):sha(p)for p in [plan,template,worker,*[f for f in native.iterdir()if f.is_file()]]}
 return dict(options=q['options'],worker_path=str(worker),template_path=str(template),native_path=str(native),candidate_pins=pins)
