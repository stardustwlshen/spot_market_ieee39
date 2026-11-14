"""Run-scoped exact import activation. Imports a model; NEVER calls Highs.run."""
from pathlib import Path
import json,hashlib,sys,subprocess,importlib.util
B=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def verify(run):
 run=Path(run).resolve();out=run/'matrix_profile';z=read(out/'ACTIVATION.json');r=read(run/'RUN.json')
 need(z['run_id']==r['run_id']and z['input_identity']==r['input_identity']and z['DA_source_identity']==r['source_identity'],'matrix profile belongs to another run')
 for p,h in z['source_pins'].items():need(sha(p)==h,'profile generator source changed')
 for f,h in z['input_pins'].items():need(sha(run/'da_scuc'/f)==h,'run template/names/network changed')
 for f,h in z['profile_pins'].items():need(sha(out/f)==h,'activated profile changed')
 v=read(out/'ACTUAL_STORAGE_IMPORT_VERIFICATION.json')
 need(v['solver_version']=='1.15.1'and v['model_import_only']is True and v['optimize_calls']==0 and v['all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked']is True,'exact import acceptance absent')
 need(v['source_template_sha256']==sha(run/'da_scuc/template.json')and v['policy_sha256']==sha(out/'STORAGE_POLICY.json')and v['guard_sha256']==sha(out/'matrix_warning_guard.py'),'import verification binding')
 return z

def activate(run):
 run=Path(run).resolve();out=run/'matrix_profile'
 if(out/'ACTIVATION.json').exists():return verify(run)
 need(not out.exists(),'partial profile/import retained; no automatic overwrite/retry')
 r=read(run/'RUN.json');prep=read(run/'da_scuc/PREPARED.json')
 need(prep['stage']=='da_scuc'and all(prep[k]==r[k]for k in ('run_id','input_identity','source_identity')),'SCUC preparation/run mismatch')
 for name in ('template','names','network_plan'):
  ref=prep['artifacts'][name];need((run/ref['path']).resolve()==run/'da_scuc'/f'{name}.json'and sha(run/ref['path'])==ref['sha256'],'prepared input changed')
 spec=importlib.util.spec_from_file_location('_run_profile_generator',B.parent/'prepare_matrix_profile.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.prepare(run,out)
 with(out/'IMPORT_ONLY.log').open('x')as log:
  done=subprocess.run([sys.executable,'-B',str(out/'validate_actual_import.py')],stdout=log,stderr=log,timeout=180)
 need(done.returncode==0,'exact import rejected; evidence retained, no optimization')
 v=read(out/'ACTUAL_STORAGE_IMPORT_VERIFICATION.json')
 need(v.get('all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked')is True and v.get('optimize_calls')==0,'invalid import result')
 sources=[Path(__file__),B.parent/'prepare_matrix_profile.py',B/'da_math/mip_worker.py']+[B/'transport_recovery_v2'/n for n in ('matrix_warning_guard.py','prepare_transport.py','validate_actual_import.py')]
 z=dict(schema='gd.pipeline.run_scoped_matrix_activation.v1',run_id=r['run_id'],input_identity=r['input_identity'],DA_source_identity=r['source_identity'],source_pins={str(p):sha(p)for p in sources},input_pins={n:sha(run/'da_scuc'/n)for n in ('template.json','names.json','network_plan.json')},profile_pins={p.name:sha(p)for p in out.iterdir()if p.is_file()},worker='checked_mip_worker.py',worker_sha256=sha(out/'checked_mip_worker.py'),optimization_calls=0,threshold_changed=False,options_changed=False)
 write(out/'ACTIVATION.json',z);return verify(run)

def worker(run):
 z=verify(run);p=Path(run).resolve()/'matrix_profile'/z['worker'];need(sha(p)==z['worker_sha256'],'worker hash');return p
