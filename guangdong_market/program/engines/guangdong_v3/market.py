"""Unified subprocess-isolated entry. Native execution requires explicit flag."""
from pathlib import Path
import sys,subprocess,argparse,json
B=Path(__file__).resolve().parent;P=B/'guangdong_pipeline_20261112';OLD=B/'guangdong_market_delivery_20261112'
def validate_scenario_run_arguments(args):
 """Public CLI must not mix a valid scenario label with different pricing/RT inputs."""
 parser=argparse.ArgumentParser(add_help=False)
 for name in ('bundle','pricing-spec','rt-assets'):parser.add_argument('--'+name,required=True)
 a,_=parser.parse_known_args(args[1:]);bundle=Path(a.bundle).resolve();d=json.loads(bundle.read_bytes())
 if 'scenario_manifest_file'not in d:return None
 from scenario_inputs import validate_scenario
 m=validate_scenario(bundle.parent)
 supplied={'bundle':bundle,'pricing_spec':Path(a.pricing_spec).resolve(),'rt_assets':Path(a.rt_assets).resolve()}
 for key,path in supplied.items():
  if path!=(bundle.parent/m['entries'][key]).resolve():raise ValueError('场景入口不匹配：'+key+'；禁止混用场景说明与不同模型输入')
 if not m.get('name') or not m.get('description'):raise ValueError('场景名称及研究说明不能为空')
 return m['identity']
def main():
 a=sys.argv[1:]
 if not a or a[0]=='help':
  print('Commands: status --run PATH [--ac-plan PATH --ac-result PATH]; energy run|resume [pipeline flags]; recovery preflight|perform --run PATH [--execute-native]; init --run PATH; portable [old CLI flags]; service MODULE INPUT [--output PATH]; prepare-matrix-profile --run PATH [--output PATH]; verify');return 0
 command=a.pop(0)
 if command=='recovery':
  p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','perform']);p.add_argument('--run',required=True);p.add_argument('--execute-native',action='store_true');x=p.parse_args(a)
  sys.path.insert(0,str(B/'recovery_controller'));import controller
  if x.action=='preflight':out=controller.preflight(x.run,B)
  else:
   if not x.execute_native:raise ValueError('数值恢复须显式 --execute-native；默认仅预检')
   controller.prepare(x.run,B);controller.execute(x.run);out=controller.accept(x.run)
  print(json.dumps(out,ensure_ascii=False,allow_nan=False));return 0
 if command=='status':return subprocess.call([sys.executable,'-B',str(B/'result_status.py'),*a])
 if command=='prepare-matrix-profile':return subprocess.call([sys.executable,'-B',str(B/'prepare_matrix_profile.py'),*a])
 if command=='energy':
  args=a[:]
  if args and args[0]=='run':
   for flag,value in [('--bundle',OLD/'examples/ieee39_research/bundle.json'),('--pricing-spec',P/'examples/research_pricing_spec.json'),('--rt-assets',P/'explicit_rt_assets/ASSETS.json')]:
    if not any(z==flag or z.startswith(flag+'=') for z in args):args.extend([flag,str(value)])
   validate_scenario_run_arguments(args)
  return subprocess.call([sys.executable,'-B',str(P/'pipeline_cli_v2.py'),*args])
 if command=='portable':return subprocess.call([sys.executable,'-B',str(OLD/'market.py'),*a])
 if command=='service':return subprocess.call([sys.executable,'-B',str(B/'services_entry.py'),*a])
 if command=='init':
  p=argparse.ArgumentParser();p.add_argument('--run',required=True);x=p.parse_args(a)
  code="import sys,json;sys.path.insert(0,sys.argv[1]);from da_pipeline_v4 import init;print(json.dumps(init(*sys.argv[2:])))"
  return subprocess.call([sys.executable,'-B','-c',code,str(P),str(OLD/'examples/ieee39_research/bundle.json'),str(Path(x.run).resolve()),str(P/'examples/research_pricing_spec.json'),str(P/'explicit_rt_assets/ASSETS.json')])
 if command=='verify':
  import hashlib
  d=json.loads((B/'SOURCE_MAP.json').read_bytes())
  for f,z in d['files'].items():
   if hashlib.sha256((B/f).read_bytes()).hexdigest()!=z['sha256']:raise ValueError('source changed: '+f)
  print(json.dumps({'source_files':len(d['files']),'native_calls':0,'full_new_pipeline_status':'see separately supplied actual evidence; not inferred by package'}));return 0
 raise ValueError('unknown command')
if __name__=='__main__':raise SystemExit(main())
