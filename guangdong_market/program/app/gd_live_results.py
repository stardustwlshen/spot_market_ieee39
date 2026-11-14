"""Read-only live/resume display adapter. No solver or restoration authority."""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
import json,hashlib,re,math

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def price_root(root):
 root=Path(root)
 return root/'pipeline/price' if (root/'pipeline/price').is_dir() else root/'price' if (root/'price').is_dir() else root

def supported(root):
 r=Path(root)
 return (r/'DISPLAY_MANIFEST.json').is_file() or (r/'pipeline/price').is_dir() or (r/'price').is_dir() or any(r.glob('HOUR[0-9]*.json'))

def portable(root):
 root=Path(root);m=read(root/'DISPLAY_MANIFEST.json')
 if m.get('schema')!='gd.display.snapshot.v1' or m.get('restore_checkpoint') is not False:raise ValueError('not a display manifest')
 if sha(root/'DISPLAY_VIEW.json')!=m['display_sha256']:raise ValueError('display snapshot digest mismatch')
 z=read(root/'DISPLAY_VIEW.json')
 if z.get('run_id')!=m['run_id']:raise ValueError('display run mismatch')
 return z

def accepted_price(q):
 if q.get('schema')=='gd.independent_PRICE.accepted.v2':return True
 return q.get('schema')=='gd.PRICE.reaccepted_preserved_raw.v1' and all(q.get(k)is True for k in ('strict_full_model_equal','prices_exactly_equal_original','KKT_exactly_equal_original')) and q.get('original_records_modified')is False and q.get('new_native_calls')==0

def load(root):
 root=Path(root)
 if (root/'DISPLAY_MANIFEST.json').is_file():return portable(root)
 p=price_root(root);pins={};errors=[];run_id=None;origin=None;seen=set();hours=[];executions=[];qpaths={}
 def verify(path,expected=None):
  path=Path(path);digest=sha(path)
  if expected is not None and digest!=expected:raise ValueError('source changed: '+str(path))
  pins[str(path.resolve())]=digest
  return read(path)
 def checked_sources(z):
  for path,digest in z.get('source_pins',{}).items():
   if pins.get(str(Path(path).resolve()))!=digest:
    if sha(path)!=digest:raise ValueError('source changed: '+path)
    pins[str(Path(path).resolve())]=digest
 for hp in sorted(p.glob('HOUR[0-9]*.json'),key=lambda x:int(x.stem[4:])):
  try:
   h=verify(hp)
   if h.get('schema')!='gd.live_host.PRICE.hour.v3' or h.get('executed_cells')!=12:raise ValueError('unrecognized complete hour')
   checked_sources(h);idx=int(hp.stem[4:]);binding=h['input_binding']
   if binding['hour']!=idx:raise ValueError('hour index mismatch')
   ep=[Path(x)for x,v in h['source_pins'].items()if Path(x).name==f'EXECUTION_H{idx}.json' and v==binding['execution_sha256']]
   if len(ep)!=1:raise ValueError('missing unique bound execution')
   e=verify(ep[0],binding['execution_sha256'])
   from gd_unit_economics import executed_events
   executed_events(h,e) # validates run, exact hour, contiguous executed cells, no issued overlap
   if run_id is not None and (h['run_id']!=run_id or e['origin']!=origin):raise ValueError('mixed run/origin')
   if set(e['cells']) & seen:raise ValueError('duplicate executed cells')
   if sum((Decimal(r['energy_amount_CNY'])for r in h['rows']),Decimal(0))!=Decimal(h['total_energy_component_CNY']):raise ValueError('hour monetary sum mismatch')
   local_q={}
   for name,digest in h['source_pins'].items():
    qp=Path(name)
    if qp.name=='ACCEPTED.json':
     accepted=verify(qp,digest);quarter=accepted['context']['day_quarter']
     if quarter in local_q:raise ValueError('duplicate bound quarter')
     local_q[quarter]=(qp,digest)
   if set(local_q)!=set(range(idx*4,idx*4+4)):raise ValueError('four bound price quarters required')
   for quarter,(qp,digest) in local_q.items():
    q=verify(qp,digest);checked_sources(q);ctx=q['context']
    if not accepted_price(q) or ctx['run_id']!=h['run_id'] or ctx['day_quarter']!=quarter or ctx.get('RT_dual_used') is not False or q['KKT'].get('passed')is not True:raise ValueError('hour price source/acceptance mismatch')
   # No partial hour append before every binding check succeeds.
   run_id=h['run_id'];origin=e['origin'];seen.update(e['cells']);hours.extend(h['rows']);executions.append(e);qpaths.update(local_q)
  except (OSError,ValueError,KeyError,TypeError) as exc:errors.append({'file':hp.name,'reason':str(exc)})
 units=[];storage=[];vre=[];prices=[]
 for e in executions:
  origin_dt=datetime.fromisoformat(e['origin']);cells=set(e['cells']);groups={}
  for key,value in e['values'].items():
   m=re.fullmatch(r'([^\[]+)\[([^,]+),(\d+)\]',key)
   if not m or int(m[3])not in cells:continue
   kind,res,tick=m[1],m[2].strip("'\""),int(m[3]);n=float(value)
   if not math.isfinite(n):raise ValueError('nonfinite execution value')
   groups.setdefault((res,tick),{})[kind]=n
  for (res,tick),v in groups.items():
   start=(origin_dt+timedelta(minutes=5*tick)).isoformat();end=(origin_dt+timedelta(minutes=5*(tick+1))).isoformat()
   if 'power'in v:units.append(dict(start=start,end=end,resource=res,P_mw=v['power'],kind='simulated_5min_execution'))
   if any(k.startswith('battery.') for k in v):
    storage.append(dict(start=start,end=end,resource=res,charge_mw=v.get('battery.charge'),discharge_mw=v.get('battery.discharge'),SOC_MWh=v.get('battery.energy')))
   if 'renewable_power'in v or 'renewable_curtailment'in v:
    a=v.get('renewable_power');c=v.get('renewable_curtailment')
    vre.append(dict(start=start,end=end,resource='VRE:'+res,availability_mw=a+c if a is not None and c is not None else None,dispatched_mw=a,curtailed_mw=c))
 for quarter,(qp,digest) in sorted(qpaths.items()):
  try:
   q=verify(qp,digest);checked_sources(q);ctx=q['context']
   if not accepted_price(q) or ctx['run_id']!=run_id or ctx['day_quarter']!=quarter or ctx.get('RT_dual_used') is not False or q['KKT'].get('passed')is not True:raise ValueError('independent price binding/acceptance mismatch')
   for node,value in q['raw_prices'].items():
    if not math.isfinite(float(value)):raise ValueError('nonfinite price')
    from gd_price_timing import evaluate
    clock=evaluate(q,source_sha256=digest)
    prices.append(dict(window=quarter,node=node,raw_research_LMP=value,formal_price=None,information_time=clock))
  except (OSError,ValueError,KeyError,TypeError)as exc:errors.append({'file':str(qp),'reason':str(exc)})
 progress_record={}
 cp=root/'CHECKPOINT.json'
 if cp.is_file():
  try:
   checkpoint=verify(cp);progress_record=checkpoint.get('persistence_progress')or{}
  except (OSError,ValueError,KeyError,TypeError)as exc:errors.append({'file':'CHECKPOINT.json','reason':str(exc)})
 view=dict(progress_record=progress_record,progress_scope='read-time saved checkpoint record; not a new restore test',schema='gd.live.display_view.v1',run_id=run_id,execution_receipt=dict(executed=len(seen),executed_ticks=sorted(seen),origin=origin,complete_hours=len(executions),scope='only complete bound hours; partial next hour not represented',restore_checkpoint=False),issued_not_verified_executed=[],executed_5min=units,storage_5min=storage,VRE_5min=vre,independent_PRICE_15min=prices,settlement_hour_rows=hours,display_errors=errors,source_manifest=pins,formal_bill=None,safety_release=False,native_calls=0)
 attach_persistence(view,root)
 return view

def save_display(view,directory):
 """Portable, hash-bound visual data; NEVER a raw result/restore checkpoint."""
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
 (directory/'DISPLAY_VIEW.json').write_text(json.dumps(view,ensure_ascii=False,allow_nan=False))
 m=dict(schema='gd.display.snapshot.v1',run_id=view['run_id'],display_sha256=sha(directory/'DISPLAY_VIEW.json'),restore_checkpoint=False,raw_solver_results_included=False,source_manifest=view['source_manifest'])
 (directory/'DISPLAY_MANIFEST.json').write_text(json.dumps(m,ensure_ascii=False))
 return m

def status(root):
 z=load(root)
 return dict(ac={'status':'not_verified'},run_id=z['run_id'],execution_receipt=z['execution_receipt'],persistence_progress=z.get('progress_record',{}),display_errors=z.get('display_errors',[]),scope='source-bound display only; not new mathematical acceptance',native_calls=0)


def attach_persistence(view,root):
 """Append verified save evidence, retaining stale original checkpoint/EXIT unchanged."""
 root=Path(root);view['original_checkpoint_progress']=dict(view.get('progress_record',{}))
 if (root/'EXIT.json').is_file():
  view['original_exit']=read(root/'EXIT.json');view['source_manifest'][str((root/'EXIT.json').resolve())]=sha(root/'EXIT.json')
 records=[]
 for ready in sorted((root/'DURABLE_QUARTERS').glob('Q*/READY.json')):
  q=ready.parent
  if not(q/'UPLOADED.json').is_file() or not(q/'DOWNLOAD_VERIFIED.json').is_file():continue
  try:
   from gd_resume_runtime.hourly_durable import validate_receipt
   validate_receipt(q,local_archive=False)
   receipt=read(q/'UPLOADED.json');verification=read(q/'DOWNLOAD_VERIFIED.json');download=Path(verification['path'])
   if verification.get('actual_download_verified')is not True or verification['sha256']!=receipt['archive_sha256'] or verification['bytes']!=receipt['archive_bytes'] or download.stat().st_size!=verification['bytes'] or sha(download)!=verification['sha256']:raise ValueError('download verification mismatch')
   if receipt['checkpoint_kind']!='quarter' or type(receipt['window'])is not int:raise ValueError('not a quarter receipt')
   for f in [ready,q/'UPLOADED.json',q/'DOWNLOAD_VERIFIED.json',download]:view['source_manifest'][str(f.resolve())]=sha(f)
   records.append(receipt['window'])
  except (OSError,ValueError,KeyError,TypeError)as exc:view['display_errors'].append({'file':str(q),'reason':'persistence evidence: '+str(exc)})
 if records:
  latest=max(records);prior=dict(view.get('progress_record',{}))
  for key in ('computed','validated','local_checkpoint','external_saved'):
   old=prior.get(key);prior[key]=max(old,latest)if type(old)is int else latest
  view['progress_record']=prior;view['progress_scope']='read-time validated upload AND actual downloaded archive; original checkpoint and EXIT retained separately; not a restore execution test'
 return view
