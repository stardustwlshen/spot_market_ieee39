"""Explicit opt-in simulated notices from pinned accepted plans; no live edits."""
from pathlib import Path
from copy import deepcopy
import json,gzip,hashlib,importlib.util
from carry import advance,to_short_boundary,need
B=Path(__file__).resolve().parent;R=B.parents[1]/'rt'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def read(p):
 p=Path(p);return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes())
def load_plan(pair_path,expected_sha256,context,parameters):
 """Caller pins acceptance identity. Known fixed validators, no receipt-loaded code.
 Named-column adapter currently supports R68 typed-event schema only.
 """
 need(sha(pair_path)==expected_sha256,'accepted pair identity drift');a=read(pair_path)
 for p,h in a['pins'].items():need(sha(p)==h,'native source drift '+p)
 need(context['basis']=='research' and context['publication_policy']=='exact_notice_tick_no_late_backfill','explicit simulated publication policy')
 need(type(context['available_tick']) is int and context['run_id'] and context['origin'],'plan availability/run required')
 p=a['paths'];q,u,eq,e,n=[read(p[k]) for k in ('SCUC_request','SCUC_response','SCED_request','SCED_response','names')]
 dto=read(p['parameters']);need(dto['dt_minutes']==5 and parameters['parameter_sha256']==sha(p['parameters']),'typed curve source binding')
 for k in ('hot_upper_ticks','warm_upper_ticks','classification_boundary_policy'):need(parameters[k]==dto[k],'classification declaration mismatch')
 for kind,c in dto['curves'].items():
  expected=dict(rows=[dict(begin=b,mean=m,end=e) for b,m,e in zip(c['begin_mw'],c['mean_mw'],c['end_mw'])],notice_ticks=c['notice_lead_ticks'],parameter_sha256=digest(c))
  need(parameters['classes'][kind]==expected,'typed class curve/lead mismatch')
 c=dto['shutdown'];need(parameters['shutdown']==dict(rows=[dict(begin=b,mean=m,end=e) for b,m,e in zip(c['begin_mw'],c['mean_mw'],c['end_mw'])],parameter_sha256=digest(c)),'shutdown source mismatch')
 need(context['adapter']=='R68_toy_zero_min' and parameters['min_up_ticks']==parameters['min_down_ticks']==0,'current toy source adapter does not certify other minimum times')
 s=importlib.util.spec_from_file_location('_fixed_short_native_loader',R/'guangdong_rt_short_physical_v1_20261112/run_actual39.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 m.checks(q,u,n,True);m.checks(eq,e,n,False);fixed=m.fixed_sced(q,u['primal'])
 for k in ('variables','rows','objective'):need(fixed[k]==eq[k],'SCED must fix accepted SCUC integers')
 vals=dict(zip(n['variables'],e['primal']));unit=context['unit'];offset=context['domain_start_tick'];count=context['cells'];events={}
 for t in range(count):
  for phase,stem in [('startup','start'),('shutdown','stop')]:
   if vals[f'{stem}[{unit},{t}]']<.5:continue
   kind=next((k for k in parameters['classes'] if vals[f'three_state[{unit}].events.kind[{t},{k}]']>.5),None) if phase=='startup' else None
   if phase=='startup':need(kind is not None,'missing startup category')
   curve=parameters['classes'][kind] if kind else parameters['shutdown'];lead=curve['notice_ticks'] if kind else 0;notice=offset+t-lead
   if kind:need(vals[f'three_state[{unit}].events.notice[{t-lead},{kind}]']>.5,'notice decision absent from native')
   key=f'{unit}:{phase}:{offset+t}';events[key]=dict(event_id=key,unit=unit,phase=phase,begin_tick=offset+t,notice_tick=notice,kind=kind,curve_parameter_sha256=curve['parameter_sha256'],rows=deepcopy(curve['rows']))
 z=dict(schema='gd.research.accepted_thermal_plan.v2',context=deepcopy(context),events=events,parameters=deepcopy(parameters),parameters_sha256=digest(parameters),accepted_pair_path=str(Path(pair_path).resolve()),accepted_pair_sha256=expected_sha256,SCUC_response_sha256=sha(p['SCUC_response']),SCED_response_sha256=sha(p['SCED_response']),source_pins=deepcopy(a['pins']),native_calls=0,formal=False)
 z['plan_sha256']=digest(z);return z

def publish_due(plan,previous,*,cutoff_tick,previous_sha256=None,simulate_publish=False):
 """Exact simulated notice tick only: no implicit opt-in, late backdating or stop permit."""
 need(simulate_publish is True,'explicit simulated publication opt-in required')
 need(digest({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256'],'plan changed')
 verified=load_plan(plan['accepted_pair_path'],plan['accepted_pair_sha256'],plan['context'],plan['parameters'])
 need(verified==plan,'plan events not derived from accepted native pair')
 need(sha(plan['accepted_pair_path'])==plan['accepted_pair_sha256'],'accepted source drift')
 for p,h in plan['source_pins'].items():need(sha(p)==h,'plan source drift')
 return _publish_verified(plan,previous,cutoff_tick=cutoff_tick,previous_sha256=previous_sha256)

def _publish_verified(plan,previous,*,cutoff_tick,previous_sha256=None):
 # Pure ledger transition. Internal only: public publish_due first derives and
 # revalidates the plan from the accepted native pair. Old pending is deliberately
 # not carried: it is a forecast and may be cancelled/replaced by the new plan.
 c=plan['context'];need(type(cutoff_tick)is int and cutoff_tick>=c['available_tick'],'future accepted plan')
 if previous is None:
  need(previous_sha256 is None,'missing previous ledger');receipts={}
 else:
  need(digest(previous)==previous_sha256,'prior ledger identity mismatch');need(previous['run_id']==c['run_id'] and previous['origin']==c['origin'],'previous run/clock mismatch');need(cutoff_tick>=previous['cutoff_tick'],'clock reversal');receipts=deepcopy(previous['receipts'])
 for key,old in receipts.items():
  need(digest({k:v for k,v in old.items() if k!='receipt_hash'})==old['receipt_hash'],'issued receipt changed')
  if c['domain_start_tick']<=old['begin_tick']<c['domain_start_tick']+c['cells']:
   new=plan['events'].get(key);need(new is not None,'accepted plan cancels existing issued obligation')
   for k in ('unit','phase','begin_tick','notice_tick','kind','curve_parameter_sha256','rows'):need(old[k]==new[k],'accepted plan changes issued obligation')
 pending={};created=[]
 for key,event in plan['events'].items():
  if key in receipts:continue
  if event['phase']=='shutdown':pending[key]={**event,'reason':'requires_explicit_permission_aware_shutdown_instruction'};continue
  due=event['notice_tick']
  if due>cutoff_tick:pending[key]={**event,'reason':'notice_tick_not_reached'};continue
  need(due==cutoff_tick,'missed notice tick; no retroactive simulated publication')
  need(c['available_tick']<=due,'plan was unavailable at notice tick')
  rec={**deepcopy(event),'issued':True,'issued_tick':cutoff_tick,'published_at_tick':cutoff_tick,'source':'explicit research simulated publication, not operator instruction','run_id':c['run_id'],'origin':c['origin'],'plan_sha256':plan['plan_sha256'],'accepted_pair_sha256':plan['accepted_pair_sha256'],'SCUC_response_sha256':plan['SCUC_response_sha256'],'SCED_response_sha256':plan['SCED_response_sha256'],'publication_policy':c['publication_policy'],'formal':False}
  rec['receipt_hash']=digest(rec);receipts[key]=rec;created.append(key)
 return dict(schema='gd.research.simulated_notice_ledger.v2',run_id=c['run_id'],origin=c['origin'],cutoff_tick=cutoff_tick,receipts=receipts,pending=pending,created=created,previous_ledger_sha256=previous_sha256,formal=False,actual_notice_claimed=False,DA_protection_changed=False,safety_permission_created=False)

def connect_boundary(initial,parameters,ledger,cells,*,cutoff_tick,executed_cells,origin,source):
 need(ledger['origin']==origin and ledger['cutoff_tick']<=cutoff_tick,'ledger available clock')
 for e in ledger['receipts'].values():need(e['run_id']==ledger['run_id'] and e['origin']==origin and e['receipt_hash']==digest({k:v for k,v in e.items() if k!='receipt_hash'}),'receipt drift')
 result=advance(initial,parameters,ledger['receipts'],cells,cutoff_tick=cutoff_tick,executed_cells=executed_cells,origin=origin)
 payload=to_short_boundary(result,predicted=True,available_at=result['executed_at'],source=source)
 payload['notice_values']={initial['unit']:{str(e['notice_tick']):1 for e in ledger['receipts'].values() if e['unit']==initial['unit'] and e['phase']=='startup'}}
 payload['notice_ledger_sha256']=digest(ledger);payload['run_id']=ledger['run_id'];payload['DA_protection_changed']=False
 return dict(carry=result,short_boundary=payload)
