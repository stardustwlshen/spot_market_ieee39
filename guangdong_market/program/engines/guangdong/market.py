"""Unified subprocess-isolated entry. Native execution requires explicit flag."""
from pathlib import Path
import sys,subprocess,argparse,json
B=Path(__file__).resolve().parent;P=B/'guangdong_pipeline_20261112';OLD=B/'guangdong_market_delivery_20261112'
def main():
 a=sys.argv[1:]
 if not a or a[0]=='help':
  print('Commands: status --run PATH [--ac-plan PATH --ac-result PATH]; energy run|resume [pipeline flags]; init --run PATH; portable [old CLI flags]; service MODULE INPUT [--output PATH]; prepare-matrix-profile --run PATH [--output PATH]; verify');return 0
 command=a.pop(0)
 if command=='status':return subprocess.call([sys.executable,'-B',str(B/'result_status.py'),*a])
 if command=='prepare-matrix-profile':return subprocess.call([sys.executable,'-B',str(B/'prepare_matrix_profile.py'),*a])
 if command=='energy':
  args=a[:]
  if args and args[0]=='run':
   for flag,value in [('--bundle',OLD/'examples/ieee39_research/bundle.json'),('--pricing-spec',P/'examples/research_pricing_spec.json'),('--rt-assets',P/'explicit_rt_assets/ASSETS.json')]:
    if flag not in args:args.extend([flag,str(value)])
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
