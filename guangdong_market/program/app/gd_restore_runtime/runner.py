"""Reviewed narrow import Q03 -> new Q04/Q05. Six native calls, two cloud barriers."""
from pathlib import Path
import json,sys,hashlib,traceback,time,importlib.util,os
A=Path(__file__).resolve().parent
config_path=Path(sys.argv[1]).resolve();expected_config_sha=sys.argv[2]
config_bytes=config_path.read_bytes()
if hashlib.sha256(config_bytes).hexdigest()!=expected_config_sha:raise ValueError('config changed')
config=json.loads(config_bytes);B=Path(config['output_root']).resolve();P=Path(config['runtime_program_root']).resolve()
sys.path[:0]=[str(A),str(P/'app'),str(P/'app/gd_live_market_runtime'),str(A.parent)]
from historical_store import HistoricalStore
from import_prefix import sha,need
import highspy
need(highspy.Highs().version()=='1.15.1','solver version')
from gd_restore_runtime.preflight import check,verify_runtime_module,verify_runner_owner
gate=check(config_path,expected_config_sha)
need(len(sys.argv)==5,'runner requires explicit preflight receipt')
lock=Path(config['output_root'])/(config['execution_attempt_id']+'.lock')
verify_runner_owner(lock,expected_config_sha,sys.argv[3],sys.argv[4],gate,os.getppid())
config_sha=sha(config_bytes);descriptor=config['historical_import'];store=HistoricalStore(descriptor['manifest'],descriptor['manifest_sha256']);old=store.data['logical_original_output'];plan=store.json(old+'/PLAN.json');forbidden=[]
need(store.data['first_unexecuted_window']==4,'Q03 import only')
need(plan['rt_clock_mode']=='provincial_15min_execution'and plan['single_day_scope']=='truncate_at_24h','clock/scope')
need(not any(plan.get(k)for k in ('post_scuc_day','post_scuc_planning','reference_revision','stop_request','ac_once','candidate_loss_once')),'unsupported mode')
store.verify_pins(plan['source_pins']);installed_pins=store.json(plan['run']+'/SHORT_CONTEXT.json')['code_pins'];store.verify_pins(installed_pins)
for path,digest in installed_pins.items():need(sha(Path(path).read_bytes())==digest,'installed code differs from archived pin '+path)
for path in store.data['map']:
 if path.startswith(old+'/pipeline/dispatch/W'):
  import re
  match=re.match(re.escape(old)+r'/pipeline/dispatch/W(\d+)/',path)
  need(match is None or int(match[1])<4,'future executed history must not be imported')
old_real=os.path.realpath(old)
def audit(event,args):
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
  target=os.path.realpath(os.fsdecode(args[0]))
  if target==old_real or target.startswith(old_real+os.sep):
   forbidden.append(target);raise RuntimeError('forbidden original run read '+target)
sys.addaudithook(audit)
from gd_resume_runtime.engine import PARAMS
from session import Pipeline
from source_ledger import LedgerSource
from adopt_frames import adopt,verify_historical_accounting,verify_rebased_agc
from lifecycle_runtime.gd_sced_precision_runtime.bridge import install
from lifecycle_runtime.worker_proof_memo import install_for_pipeline
from gd_resume_runtime.same_period import derive
for module_name in ('gd_resume_runtime.engine','session','source_ledger','gd_resume_runtime.same_period'):
 verify_runtime_module(sys.modules[module_name].__file__,P,{**installed_pins,**plan['source_pins']})
spec=importlib.util.spec_from_file_location('gd_resume_runtime.isolated_durable',A/'isolated_durable.py');durable=importlib.util.module_from_spec(spec);spec.loader.exec_module(durable)
out=B/config['execution_attempt_id'];need(not out.exists(),'attempt already exists; inspect not retry');out.mkdir()
baseline_path=Path(config['baseline_manifest']);need(sha(baseline_path.read_bytes())==config['baseline_manifest_sha256'],'baseline changed');baseline=json.loads(baseline_path.read_bytes())
need('sitecustomize' in sys.modules,'all-process Python guard missing')
need(Path(sys.modules['sitecustomize'].__file__).resolve()==A/'child_guard/sitecustomize.py','wrong startup guard')
codepins={str(f.resolve()):sha(f.read_bytes())for f in [A/'runner.py',A/'adopt_frames.py',A/'historical_store.py',A/'import_prefix.py',A/'isolated_durable.py',A/'child_guard/sitecustomize.py',A/'preflight.py',A/'__main__.py',A/'__init__.py',Path(descriptor['manifest'])]}
for path,digest in codepins.items():need(baseline['files'].get(path)==digest,'adapter source not backed in baseline '+path)
codepins[str(config_path)]=config_sha;codepins[str(baseline_path)]=config['baseline_manifest_sha256']
(out/'PLAN.json').write_text(json.dumps({**plan,'output':str(out),'execution_attempt_id':out.name,'source_pins':{**plan['source_pins'],**codepins},'isolated_import':descriptor,'historical_executed_windows':[0,1,2,3],'new_execution_windows':[4,5],'new_native_budget':6},indent=2))
started=time.time();completed=[]
def status(stage):
 (out/'STATUS.json').write_text(json.dumps({'stage':stage,'completed_new_windows':completed,'new_native_budget':6,'forbidden_original_reads':forbidden,'elapsed_s':time.time()-started},indent=2))
try:
 durable.preflight_config(out,config_path,config_sha,normal_runtime=True)
 x=Pipeline(plan['run'],out/'pipeline',research=True,**{k:plan[k]for k in PARAMS if k in plan})
 h=x.host;h.branch_id=store.json(old+'/pipeline/dispatch/W0/RESULT.json')['branch_id'];h.source_branch=Path(old+'/pipeline/dispatch');x.execution_attempt_id=out.name;h.execution_attempt_id=x.execution_attempt_id;x.prices.source=LedgerSource(h,windows=96)
 from pricing_delta import declare as declare_delta
 restored_delta=declare_delta(x.prices.pricing_delta,branch_id=h.branch_id,base_run=h.session.run,selected_DA=h.session.context['selected_DA'])
 restored_delta['restoration']={'execution_attempt_id':out.name,'historical_logical_branch':h.branch_id,'import_sha256':descriptor['manifest_sha256'],'new_declaration':True}
 x.prices.delta_path=x.prices.output/'RESTORED_DELTA_DECLARATION.json';x.prices.delta_path.write_text(json.dumps(restored_delta,indent=2));x.prices.delta_sha256=sha(x.prices.delta_path.read_bytes())
 h.sced_precision_policy=install(h.session.program);h.session.registry['source_pins'].update(h.sced_precision_policy.pins);h.session.registry['source_pins'].update(codepins);install_for_pipeline(x)
 adopt(x,store,out/'historical');verify_historical_accounting(x,store,out)
 # Original raw frames remain immutable; new carries explicitly identify their derivation.
 for w in (4,5):
  status('prepare W'+str(w));h.prepare()
  if w==4:verify_rebased_agc(x,store,out)
  status('native pair W'+str(w));h.run(execute_native=True);x.after_pair(w)
  status('PRICE q'+str(w));x.prices.prepare(w);x.prices.execute(w,execute_native=True)
  projection=out/f'EXECUTED_QUARTER_W{w}.json';projection.write_text(json.dumps(derive(out,w),indent=2))
  status('sealing Q'+str(w));directory=durable.seal_quarter(out,w,config_path,config_sha,executed_state_path=projection);durable.pack(directory)
  status('waiting independent ACK Q'+str(w));durable.wait_for_upload(directory,timeout_s=config['timeout_s']);completed.append(w);status('independent ACK Q'+str(w))
 import gzip
 native_records=[]
 expected_done=set()
 for w in (4,5):
  for stage in ('SCUC','SCED'):
   done=json.loads((out/f'pipeline/dispatch/W{w}/actual/{stage}/COMPLETED.json').read_bytes());need(done['solve_entries']==1,'unexpected native count')
   directory=out/f'pipeline/dispatch/W{w}/actual/{stage}';expected_done.add(directory/'COMPLETED.json')
   request=json.loads(gzip.decompress((directory/'request.json.gz').read_bytes()));response=json.loads(gzip.decompress((directory/'response.json.gz').read_bytes()))
   need(response['optimize_calls']==1 and request['options']['time_limit']==3600,'native settings/count')
   if stage=='SCUC':need(request['options']['mip_rel_gap']==.01,'SCUC gap')
   native_records.append({'window':w,'stage':stage,'calls':response['optimize_calls'],'options':request['options']})
  directory=out/f'pipeline/price/q{w}/prepared/native_once';expected_done.add(directory/'COMPLETED.json')
  request=json.loads((directory/'request.json').read_bytes());response=json.loads((directory/'response.json').read_bytes())
  need(response['optimize_calls']==1 and request['options']['time_limit']==3600,'PRICE native settings/count')
  native_records.append({'window':w,'stage':'PRICE','calls':response['optimize_calls'],'options':request['options']})
  ack=json.loads((out/f'DURABLE_QUARTERS/Q{w:02d}/DOWNLOAD_VERIFIED.json').read_bytes());need(ack['payload_files']>0,'missing independent ACK')
  ledger=json.loads((out/f'DURABLE_QUARTERS/Q{w:02d}/EXECUTION_LEDGER.json').read_bytes());previous=store.json(old+'/DURABLE_QUARTERS/Q03/EXECUTION_LEDGER.json')
  need(ledger['settled_hour_ids']==[0] and ledger['research_energy_total_CNY']==previous['research_energy_total_CNY'],'H0 changed or applied twice')
 need(set((out/'pipeline').rglob('COMPLETED.json'))==expected_done,'unexpected extra native completion')
 guard_rows=[json.loads(line)for line in Path(os.environ['RESTORE_GUARD_LOG']).read_text().splitlines()]
 starts={r['pid']for r in guard_rows if r['kind']=='started'};children={r['child_pid']for r in guard_rows if r['kind']=='spawn'};exits={r['pid']for r in guard_rows if r['kind']=='exited'}
 need(children.issubset(starts) and children.issubset(exits) and not any(r['kind']=='blocked'for r in guard_rows),'child guard coverage incomplete')
 (out/'CHILD_GUARD_AUDIT.json').write_text(json.dumps({'method':'Python audit hooks, not OS syscall trace','started_pids':sorted(starts),'child_pids':sorted(children),'exited_child_pids':sorted(exits),'all_children_guarded':True,'blocked_reads':0},indent=2))
 actual_calls=sum(r['calls'] for r in native_records);need(actual_calls==6,'native budget')
 (out/'NATIVE_AUDIT.json').write_text(json.dumps(native_records,indent=2))
 result={'status':'bounded_independent_restore_complete','historical_windows':[0,1,2,3],'new_executed_windows':completed,'new_native_calls':actual_calls,'old_windows_reoptimized':False,'forbidden_original_reads':forbidden,'executed_end_tick':6,'new_complete_hours':0,'historical_hour_count':1,'wall_elapsed_including_backups_s':time.time()-started}
except BaseException as exc:result={'status':'blocked_no_retry','error':str(exc),'traceback':traceback.format_exc(),'completed_new_windows':completed,'forbidden_original_reads':forbidden,'native_calls':'inspect exact native records; do not assume zero'}
(out/'EXIT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
