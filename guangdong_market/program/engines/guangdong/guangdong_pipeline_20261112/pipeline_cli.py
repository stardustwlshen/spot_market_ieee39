"""One-run orchestration only; separate identity, never mutates DA source seal."""
from pathlib import Path
import argparse,json,hashlib,sys,os,datetime
HERE=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def save(p,value):
 from rt_price_settlement import json_safe
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);raw=json.dumps(json_safe(value),sort_keys=True,separators=(',',':'),allow_nan=False)
 if p.exists():need(read(p)==json.loads(raw),'existing derived file differs: '+str(p));return
 with p.open('x')as f:f.write(raw)
def status(root,value):
 from rt_price_settlement import json_safe
 p=root/'PIPELINE_STATUS.json';t=root/'PIPELINE_STATUS.tmp';t.write_text(json.dumps(json_safe(value),sort_keys=True,separators=(',',':'),allow_nan=False));os.replace(t,p)
def orchestrator(root):
 p=root/'ORCHESTRATOR.json';z=dict(schema='gd.pipeline.orchestrator.v1',entry=str(Path(__file__).resolve()),entry_sha256=sha(__file__),fresh_checked_launcher_sha256=sha(HERE/'pipeline_da_checked.py'),scope='current declared IEEE39 uniform-event fixed-DA research chain; R80 not integrated',automatic_retry=False)
 save(p,z);return z

def settle(root,price_manifest):
 import rt_price_settlement as S
 directory=root/'settlement';result=directory/'RESULT.json';receipt=directory/'ACCEPTANCE.json'
 if receipt.exists():
  a=read(receipt)
  from pipeline_rt_price_native import verify_manifest
  verify_manifest(a['stage_manifest']);need(a['status']=='accepted_simulation_only','settlement receipt');need(sha(result)==a['result_sha256'],'settlement result changed')
  for path,h in a['source_pins'].items():need(sha(path)==h,'settlement source changed')
  return a
 need(not result.exists(),'partial settlement output requires explicit reconciliation, not overwrite')
 from pipeline_rt_price_native import source_manifest,verify_manifest
 stage_manifest=source_manifest();verify_manifest(stage_manifest)
 execution=S.load_execution(root,root/'rt',root/'ARTIFACTS.json')
 specfile=HERE.parent/'guangdong_r74_price_summary_20261112/summarize.py';mod=S.module('_pipeline_public_price_baseline',specfile)
 prices,trace,pins=S.load_prices(root,root/'ARTIFACTS.json',price_manifest,execution,price_limit_spec=mod.spec())
 amount=S.compute_settlement(execution['source'],execution['cells'],execution['da'],execution['inputs'],execution['receipt'],prices,explicit_synthetic_contract_assumptions=True)
 allpins={**execution['source_pins'],**pins,str(specfile):sha(specfile),str(root/'ARTIFACTS.json'):sha(root/'ARTIFACTS.json'),str(price_manifest):sha(price_manifest),str(HERE/'rt_price_settlement.py'):sha(HERE/'rt_price_settlement.py')}
 # Include retained arithmetic source rather than pretending the CLI alone is its dependency.
 for folder in ('guangdong_r75_daily_simulated_settlement_20261112','guangdong_r36_energy_settlement_20261112','guangdong_r47_price_settlement_contract_20261112'):
  for p in (HERE.parent/folder).glob('*.py'):allpins[str(p)]=sha(p)
 for path,h in allpins.items():need(sha(path)==h,'settlement input/source changed')
 save(directory/'RESULT.json',amount);save(directory/'PRICE_TRACE.json',trace)
 a=dict(schema='gd.pipeline.settlement_acceptance.v1',status='accepted_simulation_only',run_id=read(root/'RUN.json')['run_id'],result_sha256=sha(result),source_pins=allpins,stage_manifest=stage_manifest,formal_bill=None,payable=False,native_calls=0)
 save(receipt,a);return a

def pipeline(args):
 root=Path(args.run).resolve();need(type(args.rt_windows)is int and 1<=args.rt_windows<=96,'rt-windows1..96')
 if not args.execute_native:return dict(status='dryrun_no_native_no_initialization',run=str(root),rt_windows=args.rt_windows,stages=['DA_SCUC','DA_SCED','DA_PRICE','RT','independent_RT_PRICE','simulation_settlement_if96'],explicit_native_flag_required=True,simulation_settlement=args.simulation_settlement)
 import da_pipeline as D
 # A recorded transport revision selects its pinned reviewer, never rewrites DA mathematics.
 if (root/'PRECISION_REVISION.json').exists()and(root/'da_sced/ACCEPTANCE.json').exists():
  from da_native_price_v4 import run_da

 else:
  from pipeline_da_checked import run_da
 if not(root/'RUN.json').exists():
  need(args.command=='run'and args.bundle and args.pricing_spec and args.rt_assets,'fresh run requires explicit bundle/pricing-spec/RT-assets')
  D.init(args.bundle,root,args.pricing_spec,args.rt_assets)
 else:need(args.command=='resume','existing run: use resume, do not silently reinitialize')
 orchestrator(root);phase='DA'
 try:
  da=run_da(root,execute_native=True)
  phase='RT_prepare';import rt_pipeline as RT
  prepared=root/'rt_prepared'
  if not(prepared/'PREPARATION.json').exists():
   need(not prepared.exists(),'partial RT preparation pending; no silent overwrite');RT.prepare_rt(root,prepared)
  phase='RT';rt=RT.run_rt(root,prepared,root/'rt',windows=args.rt_windows,execute_native=True)
  need(rt.get('status')!='paused_at_accepted_boundary','RT paused; retain checkpoint')
  phase='RT_PRICE'
  from pipeline_rt_price_native import run_prices
  price=run_prices(root,root/'rt',root/'rt_price',windows=range(args.rt_windows),execute_native=True)
  # A partial PRICE batch cannot be promoted to full-day money.
  if args.rt_windows<96:
   out=dict(status='accepted_prefix_only',run_id=da['run_id'],RT=rt,PRICE=price,requested_RT_windows=args.rt_windows,full_day=False,formal_bill=None)
  elif not args.simulation_settlement:
   out=dict(status='actual_physics_and_PRICE_complete_settlement_assumptions_not_enabled',run_id=da['run_id'],RT=rt,PRICE=price,formal_bill=None)
  else:
   phase='simulation_settlement';manifest=root/'rt_price/PRICE_MANIFEST.json';need(manifest.exists(),'full96 independent PRICE manifest pending');a=settle(root,manifest)
   out=dict(status='research_end_to_end_complete',run_id=da['run_id'],DA_artifacts_sha256=sha(root/'ARTIFACTS.json'),RT=rt,PRICE=price,settlement=a,formal_bill=None,official_compliance=False)
  status(root,out);return out
 except BaseException as e:
  status(root,dict(status='pending_or_blocked',phase=phase,error=type(e).__name__+': '+str(e),automatic_retry=False,existing_native_results_retained=True,full_day_complete=False));raise

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 for name in ('run','resume'):
  s=sub.add_parser(name);s.add_argument('--run',required=True);s.add_argument('--rt-windows',type=int,default=4);s.add_argument('--execute-native',action='store_true');s.add_argument('--simulation-settlement',action='store_true',help='explicit research participation/empty contracts; no real bill')
  for field in ('bundle','pricing-spec','rt-assets'):s.add_argument('--'+field)
 a=p.parse_args();from rt_price_settlement import dumps_exact;print(dumps_exact(pipeline(a)))
if __name__=='__main__':main()
