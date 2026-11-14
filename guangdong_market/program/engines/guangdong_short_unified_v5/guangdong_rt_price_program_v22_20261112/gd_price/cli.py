"""Single installed CLI. Every native action requires explicit opt-in."""
from pathlib import Path
import sys,argparse,json
B=Path(__file__).resolve().parent
# Retained source modules are loaded only from installed package, never receipts.
sys.path.insert(0,str(B))
from domain import read,write,sha,need,digest

def main():
 p=argparse.ArgumentParser(description='广东省内研究独立定价；不是正式发布/账单')
 sub=p.add_subparsers(dest='command',required=True)
 q=sub.add_parser('prepare');g=q.add_mutually_exclusive_group(required=True);g.add_argument('--pair');g.add_argument('--seed-request')
 for k in ['case','agc','da-run','parameters','output']:q.add_argument('--'+k,required=True)
 q.add_argument('--boundary',choices=['DA_reference_R61_compatible_research','current_sced_research'],default='DA_reference_R61_compatible_research')
 q=sub.add_parser('native');q.add_argument('--prepared',required=True);q.add_argument('--execute-native',action='store_true')
 q=sub.add_parser('accept');q.add_argument('--prepared',required=True);q.add_argument('--output',required=True)
 q=sub.add_parser('research-parameters');q.add_argument('--output',required=True)
 q=sub.add_parser('hour-manifest');q.add_argument('--run',required=True);q.add_argument('--da-run',required=True);q.add_argument('--prices',nargs=4,required=True);q.add_argument('--output',required=True);q.add_argument('--hour-index',type=int,default=0)
 q=sub.add_parser('execution-view');q.add_argument('--run',required=True);q.add_argument('--da-run',required=True);q.add_argument('--hour-index',type=int,required=True);q.add_argument('--execution-window',type=int);q.add_argument('--output',required=True)
 q=sub.add_parser('hour');q.add_argument('--run',required=True);q.add_argument('--da-run',required=True);q.add_argument('--price-manifest',required=True);q.add_argument('--output',required=True);q.add_argument('--hour-index',type=int,default=0)
 a=p.parse_args()
 if a.command=='prepare':
  from program import build_price
  z=build_price(a.pair,a.case,a.agc,a.da_run,a.parameters,a.output,seed_request=a.seed_request,boundary=a.boundary)
 elif a.command=='native':
  from native import run
  z=run(a.prepared,a.execute_native)
 elif a.command=='accept':
  from review import review
  z=review(a.prepared,a.output)
 elif a.command=='research-parameters':
  out=Path(a.output);need(not out.exists(),'fresh output only');out.mkdir(parents=True);text=(B/'RESEARCH_PARAMETERS.txt').read_text();(out/'assumptions.txt').write_text(text);z=read(B/'RESEARCH_PARAMETERS.json')
  for v in z['factors'].values():v['source'].update(document=str((out/'assumptions.txt').resolve()),sha256=sha(out/'assumptions.txt'))
  write(out/'parameters.json',z);z={'parameters':str(out/'parameters.json'),'native_calls':0,'mode':'research'}
 elif a.command=='execution-view':
  from hour_generic import materialize
  z={'execution_view':str(materialize(a.run,a.da_run,a.hour_index,a.output,a.execution_window)),'native_calls':0}
 elif a.command=='hour-manifest':
  paths=[Path(x).resolve() for x in a.prices];run=Path(a.run);z=dict(hour_index=a.hour_index,run_id=read(run/'seed/request.json')['run_id'],da_artifacts_sha256=sha(Path(a.da_run)/'ARTIFACTS.json'),quarters=[dict(day_quarter=a.hour_index*4+i,path=str(f),sha256=sha(f)) for i,f in enumerate(paths)])
  need(not Path(a.output).exists(),'fresh manifest only');write(a.output,z)
  from hour_binding import bind
  bind(a.run,a.da_run,a.output,hour_index=a.hour_index);z={'manifest':a.output,'native_calls':0,'hour_index':a.hour_index}
 else:
  from hour_accounting_v2 import compute
  need(not Path(a.output).exists(),'fresh accounting output only');z=compute(a.run,a.da_run,a.output,price_manifest=a.price_manifest,hour_index=a.hour_index)
 print(json.dumps(z,ensure_ascii=False))
if __name__=='__main__':main()
