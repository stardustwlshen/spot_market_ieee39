"""Portable pure Guangdong research interfaces; optional execution backend is not bundled."""
from pathlib import Path
import argparse,json,subprocess,sys,hashlib
HOME=Path(__file__).resolve().parent
ROUTES={'thermal':('guangdong_r73_agc_parameter_interface_20261112','adapter.py','gd.agc.r73.v1'),'storage':('guangdong_r78_storage_agc_competition_20261112','storage.py','gd.agc.storage.r78.v1'),'mixed':('guangdong_r79_mixed_agc_projection_20261112','mixed.py','gd.agc.mixed.r79.v1'),'safety':('guangdong_r77_agc_safety_gate_20261112','gate.py','gd.agc.safety.r77.v1')}
def verify():
 for n,r in json.loads((HOME/'SOURCE_MAP.json').read_bytes()).items():
  if hashlib.sha256((HOME/n).read_bytes()).hexdigest()!=r['sha256']:raise ValueError('vendor source changed: '+n)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['readiness','validate','run','evidence','backend-readiness','prepare-small','prepare-scuc','solve-prepared','prepare-rt-small','run-rt-small','rt-small-fixture']);p.add_argument('--module',choices=ROUTES);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path);p.add_argument('--mode',choices=['research','formal'],default='research');p.add_argument('--backend-root',type=Path);p.add_argument('--prepared',type=Path);p.add_argument('--time-limit',type=float);p.add_argument('--execute-native',action='store_true');a=p.parse_args()
 if a.mode=='formal':print(json.dumps(dict(status='missing_formal_inputs_and_operator_authority',formal_supported=False,native_calls=0)));return 2
 verify()
 if a.action in ('prepare-rt-small','run-rt-small','rt-small-fixture'):
  cmd=[sys.executable,'-B',str(HOME/'r80.py'),{'prepare-rt-small':'prepare','run-rt-small':'run','rt-small-fixture':'fixture'}[a.action]]
  if a.output:cmd+=['--output',str(a.output.resolve())]
  if a.execute_native:cmd+=['--execute-native']
  return subprocess.run(cmd,check=False).returncode
 if a.action in ('prepare-small','prepare-scuc','solve-prepared'):
  import core
  if a.output is None:p.error('--output required')
  if a.action=='prepare-scuc'and a.input is None:p.error('--input explicit bundle required')
  if a.action=='solve-prepared':
   if a.prepared is None or a.time_limit is None:p.error('--prepared and --time-limit required')
   r=core.solve(a.prepared,a.output,a.time_limit,a.execute_native)
  else:
   if a.execute_native:p.error('preparation never executes native')
   r=core.prepare(a.output,a.input if a.action=='prepare-scuc'else None)
  print(json.dumps(r,indent=2));return 2 if r.get('status','').startswith('pending')else 0
 if a.action=='readiness':
  print(json.dumps(dict(status='portable_pure_interfaces_ready',python='3.11+',modules=list(ROUTES),native_solver_worker_bundled=True,native_binary_requires_optional_dependencies=True,core_builder='R68 small and R56 explicit SCUC; no native run in this packaging task',complete_market_pipeline_bundled=False,current_AGC50='research_no_increase_not_certified',existing_BESS_qualification='not supplied',Fm_in_settlement=False,native_calls=0),indent=2));return 0
 if a.action=='evidence':print((HOME/'EVIDENCE_INDEX.json').read_text());return 0
 if a.action=='backend-readiness':
  root=a.backend_root.resolve()if a.backend_root else None
  found=bool(root and(root/'guangdong_market_program_v25_20261112/market.py').is_file())
  print(json.dumps(dict(status='candidate_backend_requires_original_validation'if found else'missing_execution_backend',configured_root=str(root)if root else None,automatic_execution=False,R80_slot='unconfigured; schema/runner not yet integrated',native_calls=0),indent=2));return 0 if found else 2
 if a.module is None or a.input is None:p.error('--module and --input required')
 inp=json.loads(a.input.read_bytes());folder,file,schema=ROUTES[a.module]
 if inp.get('schema')!=schema:raise ValueError('expected schema '+schema)
 if inp.get('mode')!='research':raise ValueError('input research mode required; formal not supported')
 if a.output and(a.action=='validate'or a.output.exists()):raise ValueError('output only for run and must be fresh')
 # Subprocess isolation avoids module-name collisions; paths anchored to this package, never original cwd.
 r=subprocess.run([sys.executable,'-B',str(HOME/'vendor'/folder/file),str(a.input.resolve())],capture_output=True,text=True,check=False)
 try:result=json.loads(r.stdout)
 except json.JSONDecodeError:result=dict(status='missing_or_invalid_input',detail=r.stderr[-2000:],native_calls=0)
 if a.action=='validate':result=dict(status='valid_pure_input'if r.returncode==0 else'invalid_or_unsupported_input',module=a.module,underlying_status=result.get('status'),error=result.get('error',result.get('detail')),native_calls=0,validation_runs_pure_arithmetic_only=True)
 text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
 if a.output:
  with a.output.open('x')as f:f.write(text)
 else:print(text,end='')
 return 0 if r.returncode==0 else 2
if __name__=='__main__':
 try:raise SystemExit(main())
 except (ValueError,KeyError,TypeError,OSError)as e:print(json.dumps(dict(status='missing_or_invalid_input',error=str(e),native_calls=0)));raise SystemExit(2)
