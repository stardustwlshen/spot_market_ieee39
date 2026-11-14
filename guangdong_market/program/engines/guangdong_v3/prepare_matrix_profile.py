"""Candidate generation; explicit --activate-import-only checks storage without optimization."""
from pathlib import Path
import argparse,hashlib,json,runpy,shutil
B=Path(__file__).resolve().parent
P=B/'guangdong_pipeline_20261112'
T=P/'transport_recovery_v2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def replace_once(s,old,new):
 if s.count(old)!=1:raise ValueError('upstream helper changed: '+old)
 return s.replace(old,new)
def prepare(run,output=None):
 run=Path(run).resolve();stage=run/'da_scuc'
 for name in ('template.json','names.json','network_plan.json'):
  if not (stage/name).is_file():raise ValueError('prepare SCUC first; missing '+str(stage/name))
 out=Path(output).resolve() if output else run/'matrix_profile_candidate'
 if out.exists():raise ValueError('new output required; never overwrite active profile')
 out.mkdir(parents=True)
 source=(T/'prepare_transport.py').read_text()
 source=replace_once(source,"P=B.parent/'pure_fresh_run_v1/da_scuc'",'P=Path('+repr(str(stage))+')')
 source=replace_once(source,"(B.parent/'da_math/mip_worker.py')",'Path('+repr(str(P/'da_math/mip_worker.py'))+')')
 (out/'generate_profile.py').write_text(source)
 shutil.copyfile(T/'matrix_warning_guard.py',out/'matrix_warning_guard.py')
 import sys
 sys.path.insert(0,str(out))
 try:runpy.run_path(str(out/'generate_profile.py'),run_name='__profile_generation__')
 finally:sys.path.pop(0)
 v=(T/'validate_actual_import.py').read_text()
 v=v.replace("B.parent/'pure_fresh_run_v1/da_scuc/template.json'",'Path('+repr(str(stage/'template.json'))+')')
 if "B.parent/'pure_fresh_run_v1" in v:raise ValueError('unreplaced historical input path')
 v=v.replace("r=verify(d,h,hs,s,messages,policy);","assert h.version()=='1.15.1','expected HiGHS1.15.1';r=verify(d,h,hs,s,messages,policy);r['solver_version']=h.version();")
 (out/'validate_actual_import.py').write_text(v)
 record=dict(schema='gd.delivery.matrix_profile_candidate.v1',run=str(run),input_pins={str(stage/n):sha(stage/n) for n in ('template.json','names.json','network_plan.json')},generator_source_pins={str(T/n):sha(T/n) for n in ('prepare_transport.py','matrix_warning_guard.py','validate_actual_import.py')},artifacts={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()},optimization_calls=0,model_import_calls=0,active_launcher_modified=False,activation_allowed=False,next_step='Explicit import-only validation, then a separately pinned transport revision; generation is not activation or cross-platform validation.')
 (out/'PROFILE_PREPARATION.json').write_text(json.dumps(record,indent=2,sort_keys=True))
 return record
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--output');ap.add_argument('--activate-import-only',action='store_true');a=ap.parse_args()
 if a.activate_import_only:
  if a.output:raise ValueError('activation uses run/matrix_profile exclusively')
  import sys
  sys.path.insert(0,str(P))
  from run_matrix_profile import activate
  print(json.dumps(activate(a.run),indent=2))
 else:print(json.dumps(prepare(a.run,a.output),indent=2))
