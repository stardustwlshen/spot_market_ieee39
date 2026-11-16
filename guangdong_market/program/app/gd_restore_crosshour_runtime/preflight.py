"""Mandatory bounded-recovery gate. No solver or trust in self-reported flags."""
from pathlib import Path
import sys,json,hashlib,gzip,tarfile
from .historical_store import HistoricalStore

def sha(raw):return hashlib.sha256(raw).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def read_pinned(p,h):
 raw=Path(p).read_bytes();need(sha(raw)==h,'pinned file changed: '+str(p));return json.loads(raw)
def check(config_path,expected):
 need(sys.platform=='linux','recovery currently supports Linux only')
 import highspy
 need(highspy.Highs().version()=='1.15.1','only verified HiGHS 1.15.1 supported')
 c=read_pinned(config_path,expected);need(c.get('schema')=='gd.restore.q07_one_hour.v1','unsupported recovery config schema')
 allowed={'schema','runtime_program_root','historical_import','quarter_archives','baseline_manifest','baseline_manifest_sha256','execution_attempt_id','output_root','policy','cadence','persistent_root','previous_execution_ledger','require_download_ack','timeout_s','transport','coordinator_operated'}
 need(set(c)<=allowed,'unknown config/override rejected');need(c['policy']=='cloud_required'and c['cadence']=='quarter'and c['require_download_ack']is True,'independent quarter ACK required')
 need(c.get('transport')=='dot_library_coordinator' and c.get('coordinator_operated')is True,'external coordinator required')
 need(c['timeout_s']==3600,'unsupported ACK timeout');attempt=c['execution_attempt_id'];need(isinstance(attempt,str)and attempt.replace('_','').replace('-','').isalnum(),'invalid attempt name')
 out=Path(c['output_root']).resolve()/attempt;need(out.is_relative_to(Path(c['persistent_root']).resolve()),'output outside persistent root');need(not out.exists(),'attempt already exists; never retry in place')
 d=c['historical_import'];s=HistoricalStore(d['manifest'],d['manifest_sha256']);need(s.data['schema']=='gd.isolated_archive_import.v1','unsupported import schema');need(s.data['first_unexecuted_window']==8,'only Q07 -> Q08/Q11 supported')
 old=s.data['logical_original_output']
 for versions in s.data['map'].values():
  for record in versions.values():need(not Path(record['path']).resolve().is_relative_to(Path(old).resolve()),'historical objects must be outside original run')
 need(old.startswith('/') and '\\' not in old,'only Linux identities supported');need(not out.is_relative_to(Path(old).resolve()),'output overlaps original run')
 baseline=read_pinned(c['baseline_manifest'],c['baseline_manifest_sha256']);need(baseline.get('library_dependencies'),'external baseline dependencies required')
 roots=Path(c['runtime_program_root']).resolve();need((roots/'app/gd_live_market_runtime/session.py').is_file(),'runtime installation missing')
 # Re-read independently downloaded archives, not importer status flags.
 need(len(c['quarter_archives'])==8,'eight contiguous quarter archives required')
 for w,item in enumerate(c['quarter_archives']):
  ack=read_pinned(item['receipt'],item['receipt_sha256']);need(ack.get('schema')=='gd.durable.download_verified.v1' and bool(ack.get('library_file_id')),'unverified archive receipt')
  raw=Path(item['archive']).read_bytes();need(sha(raw)==ack['archive_sha256'] and len(raw)==ack['archive_bytes'],'archive changed')
  with tarfile.open(item['archive'])as t:
   members=t.getmembers();need(all(m.isfile()for m in members),'archive must contain regular files only');names=[m.name for m in members];need(len(set(names))==len(names),'duplicate archive member')
   rb=t.extractfile('READY.json').read();need(sha(rb)==ack['manifest_sha256'],'READY changed');r=json.loads(rb)
   need(r['window']==w and r['executed_end_exclusive']==w+1 and r['output']==old and r['rt_clock_mode']=='provincial_15min_execution' and r['grid_minutes']==15,'mixed archive scope/clock')
   need(len(r['files'])==ack['payload_files'] and set(names)=={'READY.json'}|{x['member']for x in r['files']},'archive inventory mismatch')
   for f in r['files']:
    b=t.extractfile(f['member']).read();need(sha(b)==f['sha256'] and len(b)==f['size'],'archive payload changed');need(s.bytes(f['restore_path'],f['sha256'])==b,'import differs from original archive')
 plan=s.json(old+'/PLAN.json');need(plan['output']==old,'PLAN identity');need(plan['rt_clock_mode']=='provincial_15min_execution'and plan['single_day_scope']=='truncate_at_24h','unsupported clock/scope')
 need(plan.get('horizon_periods')==8 and plan.get('grid_minutes')==15 and plan.get('maximum_rolling_window_cells')==8,'unsupported rolling horizon')
 need(plan.get('mip_gap')==.01 and plan.get('threads')==1 and not plan.get('automatic_fallback') and all(plan.get(k)==3600 for k in ('SCUC_seconds','SCED_seconds','PRICE_seconds')),'unsupported native policy')
 need(plan.get('hours')==24 and plan.get('windows')==96,'only original full-day96 task supported')
 forbidden=('post_scuc_day','post_scuc_planning','reference_revision','stop_request','ac_once','candidate_loss_once','finite_h1','finite_hourly')
 need(not any(plan.get(k)for k in forbidden),'unsupported event/legacy mode')
 ctx=s.json(plan['run']+'/SHORT_CONTEXT.json');s.verify_pins(ctx['code_pins']);s.verify_pins(plan['source_pins'])
 for p,h in {**ctx['code_pins'],**{p:h for p,h in plan['source_pins'].items() if Path(p).is_relative_to(roots)}}.items():
  need(Path(p).resolve().is_relative_to(roots),'different runtime installation; migration not supported');need(sha(Path(p).read_bytes())==h,'installed source drift')
 # Closed dependency graph: baseline source files are opaque leaves only under
 # the explicitly declared installation root. All live JSON pins recurse.
 seen=set();queue=[(p,h)for p,vs in s.data['map'].items() if p.startswith(old+'/') or p.startswith(plan['run']+'/') for h in vs]
 def pins(v):
  if isinstance(v,dict):
   for k,z in v.items():
    if k in ('source_pins','code_pins')and isinstance(z,dict):yield from z.items()
    else:yield from pins(z)
  elif isinstance(v,list):
   for z in v:yield from pins(z)
 while queue:
  p,h=queue.pop()
  if(p,h)in seen:continue
  seen.add((p,h));raw=s.bytes(p,h)
  if Path(p).is_relative_to(roots):need(baseline['files'].get(p)==h,'source leaf absent from baseline');continue
  if not p.endswith(('.json','.json.gz')):continue
  v=json.loads(gzip.decompress(raw)if p.endswith('.gz')else raw)
  queue.extend(pins(v))
  from .isolated_durable import scan_pins
  queue.extend(scan_pins(v))
 branch=None;previous=None
 for w in range(8):
  folder=old+f'/pipeline/dispatch/W{w}';result=s.json(folder+'/RESULT.json');carry=s.json(folder+'/EXACT_CARRY.json');incoming=s.json(folder+'/prepared/CARRY.json');pair=s.json(folder+'/actual/ACCEPTED_PAIR.json')
  need(result['window']==w and result['executed_end_exclusive']==w+1 and carry['delivery_tick']==w+1,'execution boundary')
  need(branch is None or branch==result['branch_id'],'mixed branches');branch=result['branch_id'];need(carry['seed_case_sha256']==sha(s.bytes(plan['run']+'/seed/case.json')),'different case')
  need(all(z['phase']in ('stable','off')and not z.get('remaining_curve')and z.get('event_id')is None for z in carry['thermal'].values()),'actual thermal event unsupported')
  if previous:
   for k in ('thermal','thermal_age_h','storage','pumped_storage','notice_values','cutoff_tick','delivery_tick'):need(incoming[k]==previous[k],'state discontinuity '+k)
  previous=carry
  for stage in ('SCUC','SCED'):
   request=s.json(folder+f'/actual/{stage}/request.json.gz');response=s.json(folder+f'/actual/{stage}/response.json.gz',pair[stage+'_response_sha256']);o=request['options'];need(o['time_limit']==3600 and o['threads']==1 and response['optimize_calls']==1,'unsupported native policy')
   if stage=='SCUC':need(o['mip_rel_gap']==.01,'unsupported MIP gap')
 for p in s.data['map']:
  import re
  m=re.match(re.escape(old)+r'/pipeline/dispatch/W(\d+)/',p)
  need(m is None or int(m[1])<8,'future execution imported')
  m=re.match(re.escape(old)+r'/pipeline/price/q(\d+)(?:/|_)',p);need(m is None or int(m[1])<8,'future price imported')
 for hour in (0,1):
  for name in (f'HOUR{hour}.json',f'SETTLEMENT_H{hour}.json',f'EXECUTION_H{hour}.json'):s.bytes(old+'/pipeline/price/'+name)
 ledger=s.json(old+'/DURABLE_QUARTERS/Q07/EXECUTION_LEDGER.json');need(ledger['settled_hour_ids']==[0,1],'historical hour inventory')
 need(read_pinned(c['previous_execution_ledger']['path'],c['previous_execution_ledger']['sha256'])==ledger,'wrong prior ledger')
 return {'schema':'gd.restore.preflight.v1','config_sha256':expected,'import_sha256':s.digest,'original_output':old,'branch_id':branch,'new_output':str(out),'native_budget':12,'supported_windows':[8,9,10,11],'checked_dependency_versions':len(seen),'installed_code_pins':len(ctx['code_pins']),'formal':False}

def verify_runtime_module(path,root,pins):
 p=Path(path).resolve();need(p.is_relative_to(Path(root).resolve()),'runtime module outside verified installation');need(str(p)in pins,'runtime module not pinned');need(sha(p.read_bytes())==pins[str(p)],'actual runtime module changed')

def verify_runner_owner(lock,config_sha,preflight_path,preflight_sha,gate,parent_pid):
 receipt=read_pinned(preflight_path,preflight_sha);need(receipt==gate,'preflight receipt mismatch')
 owner=json.loads((Path(lock)/'OWNER.json').read_bytes());need(owner.get('pid')==parent_pid and owner.get('config_sha256')==config_sha and owner.get('preflight_sha256')==preflight_sha,'CLI owner/lock mismatch')
