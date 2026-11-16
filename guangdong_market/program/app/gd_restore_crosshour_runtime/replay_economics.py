"""Zero-solve, append-only economic replay for the verified Q07 -> Q11 experiment.

This is not arbitrary-run recovery. It never overwrites dispatch or settlement.
External archive verification is a separate completion gate.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, sys, os, subprocess, tempfile

def digest(b): return hashlib.sha256(b).hexdigest()
def require(ok, message):
 if not ok: raise ValueError(message)
def read(path, expected):
 b=Path(path).read_bytes();require(digest(b)==expected,'SHA mismatch: '+str(path));return b

def verify_runtime_closure(app,pins):
 app=Path(app).resolve()
 required={str(p.resolve()) for p in app.rglob('*.py')}
 require(required and str(app/'gd_settlement_bridge.py') in required,'runtime installation absent')
 require(required.issubset(pins),'runtime dependency pin absent')
 for path in required:read(path,pins[path])

def replay(config_path, config_sha):
 c=json.loads(read(config_path,config_sha))
 require(isinstance(c,dict) and c.get('schema')=='gd.restore.q07_h2_economics_replay.v1','unsupported schema')
 require(c['reader_sha256']=='92c3f4e0a7fe61d3992991aa7fb90a948755fb9d71282bef4a370fe1024baefa','unreviewed economic reader')
 require(c['hour']==2 and c['cells']==[8,9,10,11],'only reviewed H2 supported')
 pins=c['source_pins'];require(isinstance(pins,dict) and pins,'source pins absent')
 for p,h in pins.items():read(p,h)
 hour=Path(c['hour_path']);original=Path(c['settlement_path']);execution=Path(c['execution_path'])
 for p in (hour,original,execution,Path(c['reader_path'])):require(str(p) in pins,'required pin absent')
 require(pins[c['reader_path']]==c['reader_sha256'],'reader pin conflict')
 raw=hour.read_bytes();h=json.loads(raw);e=json.loads(execution.read_bytes())
 require(e['hour']==2 and e['cells']==[8,9,10,11] and e['rt_clock_mode']=='provincial_15min_execution','execution scope')
 require(h['run_id']==e['run_id'],'run mismatch')
 for w in (8,9,10,11):
  binding=hour.parents[1]/f'dispatch/W{w}/AGC_BINDING.json'
  require(str(binding) in pins,'current AGC binding unpinned')
  for item in json.loads(binding.read_bytes()):
   for receipt,expected in item['receipt_files'].items():
    require(pins.get(receipt)==expected,'current AGC receipt unpinned')
 output=Path(c['output']);require(not output.exists(),'output already exists')
 # All source code is explicitly pinned; the only overlay is the reviewed reader.
 app=Path(c['runtime_app']).resolve()
 verify_runtime_closure(app,pins)
 # Avoid accepting stale/forged bytecode instead of the pinned Python source.
 sys.dont_write_bytecode=True
 sys.pycache_prefix=str(Path(tempfile.gettempdir())/('no_cache_'+os.urandom(16).hex()))
 sys.path.insert(0,str(app))
 import highspy
 def forbidden(*a,**kw):raise RuntimeError('economic replay cannot invoke solver')
 highspy.Highs.run=forbidden
 spec=importlib.util.spec_from_file_location('gd_service_estimates',c['reader_path'])
 mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
 from gd_settlement_bridge import from_export,components
 result=components(from_export(hour,h['run_id'],digest(raw)))
 econ=result['unit_economics'];require(not econ.get('automatic_input_diagnostics'),'incomplete economic sources')
 rows=econ['rows'];require(len(rows)==51 and len({r['resource'] for r in rows})==51,'resource set incomplete')
 require(all(r['agc_estimate'] is not None and r['estimated_spot_margin_before_service_allocation_CNY'] is not None for r in rows),'research estimates missing')
 for p,d in pins.items():read(p,d)
 output.mkdir(parents=True,exist_ok=False)
 derived=output/'DERIVED_SETTLEMENT_H2.json';derived.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 proof={'replay_code_pins':{str(p.resolve()):digest(p.read_bytes()) for p in Path(__file__).parent.rglob('*.py')},'config_path':str(Path(config_path).resolve()),'schema':'gd.restore.derived_economics.v1','config_sha256':config_sha,'source_pins':pins,'derived_sha256':digest(derived.read_bytes()),'new_native_calls':0,'source_files_unchanged':True,'formal_profit_verified':False,'research_economics_derived':True,'economics_derived_verified':False,'reason':'independent archive download ACK required'}
 (output/'DERIVATION.json').write_text(json.dumps(proof,indent=2));return proof

def main():
 p=argparse.ArgumentParser(description='仅对已验证 Q07→Q11 运行做 H2 经济派生，不调用求解器')
 p.add_argument('--config',required=True);p.add_argument('--config-sha',required=True);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
 c=json.loads(read(a.config,a.config_sha));guard=Path(__file__).parent/'child_guard'
 if a.worker:
  require('sitecustomize' in sys.modules and Path(sys.modules['sitecustomize'].__file__).resolve()==(guard/'sitecustomize.py').resolve(),'startup guard absent')
  require(os.environ.get('RESTORE_FORBIDDEN_ROOT')==c['forbidden_original_output'],'wrong original guard')
  print(json.dumps(replay(a.config,a.config_sha)));return
 log=Path(c['output']).with_suffix('.guard.jsonl');require(not log.exists(),'guard log already exists')
 env=dict(os.environ);env['RESTORE_FORBIDDEN_ROOT']=c['forbidden_original_output'];env['RESTORE_GUARD_LOG']=str(log.resolve());env['PYTHONPATH']=str(guard.resolve())+os.pathsep+str(Path(__file__).parents[1].resolve())
 subprocess.run([sys.executable,'-B','-m','gd_restore_crosshour_runtime.replay_economics','--worker','--config',str(Path(a.config).resolve()),'--config-sha',a.config_sha],env=env,check=True)
 rows=[json.loads(line) for line in log.read_text().splitlines()]
 require(len(rows)==2 and rows[0]['kind']=='started' and rows[1]['kind']=='exited' and rows[0]['pid']==rows[1]['pid'],'zero-child guard incomplete')
 (Path(c['output'])/'PYTHON_GUARD.jsonl').write_bytes(log.read_bytes())
if __name__=='__main__':main()
