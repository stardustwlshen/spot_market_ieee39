"""Minimal explicit fresh-input DA stage CLI. No native without flag."""
import argparse,json
from da_pipeline import init,prepare_scuc,artifacts
from da_native import run_da
p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True)
a=s.add_parser('prepare');a.add_argument('--bundle',required=True);a.add_argument('--pricing-spec',required=True);a.add_argument('--rt-assets',required=True);a.add_argument('--run',required=True)
a=s.add_parser('resume');a.add_argument('--run',required=True);a.add_argument('--execute-native',action='store_true')
z=p.parse_args()
if z.command=='prepare':init(z.bundle,z.run,z.pricing_spec,z.rt_assets);r=prepare_scuc(z.run)
else:r=run_da(z.run,z.execute_native)
print(json.dumps(r))
