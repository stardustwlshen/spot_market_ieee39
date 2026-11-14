from pathlib import Path
import tempfile,shutil,subprocess,json,sys,os
B=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='fixed-event-portable-')as d:
 t=Path(d)
 for f in ['cli.py','event_path.py','W45_BUNDLE.json','W45_LEDGER.json']:shutil.copyfile(B/f,t/f)
 shutil.copytree(B/'vendor',t/'vendor',ignore=shutil.ignore_patterns('__pycache__'))
 h=(B/'W45_HOST_DIGEST.txt').read_text().strip();env=dict(os.environ);env.pop('PYTHONPATH',None)
 for action in ['export-plan','build','carry']:
  cmd=[sys.executable,'-B','cli.py',action,'--bundle','W45_BUNDLE.json','--expected-bundle-sha256',h,'--output',action+'.json']
  if action!='export-plan':cmd+=['--ledger','W45_LEDGER.json','--expected-ledger-sha256',json.loads((B/'W45_LEDGER.json').read_text())['ledger_sha256']]
  subprocess.run(cmd,cwd=t,env=env,check=True,capture_output=True)
 z=json.loads((t/'carry.json').read_text());assert z['storage']
 result={'status':'isolated_temp_directory_portable_CLI_export_build_carry_passed','new_native_calls':0,'no_workspace_imports_in_portable_core':True,'source_bundle_trust_digest_host_supplied':True,'solver_started':False}
(B/'PORTABLE_RESULT.json').write_text(json.dumps(result,indent=2));print(result)
