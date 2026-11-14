"""Subprocess-isolated orchestration revision; never mixes SMM generations."""
from pathlib import Path
import argparse,json,hashlib,subprocess,sys,uuid,os
B=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def exclusive(p,z):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def revision(root):
 p=root/'ORCHESTRATOR_REVISION_v2.json'
 z=dict(schema='gd.pipeline.orchestrator.v2',stage_process_isolation=True,old_orchestrator_sha256=sha(root/'ORCHESTRATOR.json')if(root/'ORCHESTRATOR.json').exists()else None,source_pins={str(B/n):sha(B/n)for n in ('pipeline_cli_v2.py','pipeline_stage_entry_v2.py','pipeline_cli.py')},automatic_retry=False)
 if p.exists():
  if read(p)!=z:raise ValueError('orchestrator revision changed; retain old revision')
 else:exclusive(p,z)
 return z

def run(args):
 root=Path(args.run).resolve()
 if not 1<=args.rt_windows<=96:raise ValueError('rt-windows1..96')
 if not args.execute_native:return dict(status='dryrun_no_native_no_initialization',isolated_stages=['DA','RT_prepare','RT','PRICE','settle_if96_explicit'])
 if(root/'RUN.json').exists()and args.command!='resume':raise ValueError('existing run requires resume')
 if not(root/'RUN.json').exists()and args.command!='run':raise ValueError('missing run requires explicit run inputs')
 # Fresh init is DA-owned; do not create run directory before init.
 control=root.parent/(root.name+'_orchestration_v2');control.mkdir(exist_ok=True)
 attempt=control/uuid.uuid4().hex;attempt.mkdir();results={}
 for stage in ['DA','RT_prepare','RT','PRICE']+(['settle']if args.rt_windows==96 and args.simulation_settlement else []):
  if(root/'RUN.json').exists():revision(root)
  output=attempt/(stage+'.json');command=[sys.executable,'-B',str(B/'pipeline_stage_entry_v2.py'),'--stage',stage,'--run',str(root),'--result',str(output),'--windows',str(args.rt_windows),'--execute-native']
  for key in ('rt_prepared','bundle','pricing_spec','rt_assets'):
   value=getattr(args,key,None)
   if value:command+=['--'+key.replace('_','-'),str(Path(value).resolve())]
  exclusive(attempt/(stage+'_LAUNCH.json'),dict(command=command,stage=stage,automatic_retry=False))
  with(attempt/(stage+'.log')).open('x')as log:code=subprocess.call(command,stdout=log,stderr=log)
  exclusive(attempt/(stage+'_EXIT.json'),dict(returncode=code,result_present=output.exists()))
  if code or not output.exists():
   exclusive(attempt/'PENDING.json',dict(stage=stage,returncode=code,reason='stage failure/unknown; inspect raw receipt before resume',automatic_retry=False));raise RuntimeError(f'{stage} pending; see {attempt}')
  results[stage]=read(output)
 revision(root)
 full=args.rt_windows==96 and args.simulation_settlement
 out=dict(status='research_end_to_end_complete'if full else 'accepted_requested_prefix_or_day_without_settlement',requested_windows=args.rt_windows,results=results,formal_bill=None,official_compliance=False,stage_process_isolation=True)
 exclusive(attempt/'RESULT.json',out);return out

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['run','resume']);p.add_argument('--run',required=True);p.add_argument('--rt-windows',type=int,default=4);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulation-settlement',action='store_true')
 for f in ('bundle','pricing-spec','rt-assets','rt-prepared'):p.add_argument('--'+f)
 print(json.dumps(run(p.parse_args()),allow_nan=False))
if __name__=='__main__':main()
