"""Portable data-only fixed-curve candidate event path. No optimizer/real dispatch."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import json,gzip,hashlib,math,sys,base64
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
from matrix_audit import audit
from scoped_kkt import metrics
from exact_fixed import derive,aligned_tick,validate_receipts

def need(v,m):
 if not v:raise ValueError(m)
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def read(p):
 p=Path(p);return json.loads(gzip.decompress(p.read_bytes())if p.suffix=='.gz'else p.read_bytes())
def stamp(s):
 x=datetime.fromisoformat(s);need(x.utcoffset()==timedelta(hours=8),'explicit +08 clock');return x
def tick(s,origin):return aligned_tick((stamp(s)-stamp(origin)).total_seconds()/900,'clock')
def pack(x):
 if isinstance(x,bytes):return {'__bytes__':base64.b64encode(x).decode()}
 if isinstance(x,dict):return {'__map__':[[pack(k),pack(v)]for k,v in x.items()]}
 if isinstance(x,tuple):return {'__tuple__':[pack(v)for v in x]}
 if isinstance(x,list):return [pack(v)for v in x]
 return x
def unpack(x):
 if isinstance(x,dict):
  if '__bytes__'in x:return base64.b64decode(x['__bytes__'])
  if '__map__'in x:return {unpack(k):unpack(v)for k,v in x['__map__']}
  if '__tuple__'in x:return tuple(unpack(v)for v in x['__tuple__'])
 if isinstance(x,list):return [unpack(v)for v in x]
 return x


def dual_adapter():
 import importlib.util
 key='_gd_lifecycle_dual_adapter'
 if key not in sys.modules:
  path=Path(__file__).resolve().parent.parent/'guangdong_network_dual_certificate_v1_20261113/acceptance_adapter.py'
  spec=importlib.util.spec_from_file_location(key,path);module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module)
 return sys.modules[key]

def verify_pair(q,u,eq,e,n):
 need(len(n['variables'])==len(q['variables']) and len(n['constraints'])==len(q['rows']),'names dimensions')
 need(len(set(n['variables']))==len(n['variables']),'unique names')
 for r in (u,e):need(r.get('backend')=='highs'and r.get('status')=='optimal'and r.get('native_status')=='Optimal'and r.get('has_primal')is True,'optimal real raw pair required')
 ua=audit(q,u['primal'],u['objective']);need(ua['max_primal_violation']<=1e-6 and ua['max_integer_violation']<=1e-6 and u['native_mip_gap']<=q['options']['mip_rel_gap']+1e-12,'SCUC acceptance')
 need(q['rows']==eq['rows']and q['objective']==eq['objective'],'fixed SCED same mathematics')
 expected=deepcopy(q['variables'])
 for v,x in zip(expected,u['primal']):
  if v['kind']!='C':v.update(kind='C',lb=float(round(x)),ub=float(round(x)),start=None)
 need(expected==eq['variables'],'fixed integers from same SCUC')
 numeric,certificate=dual_adapter().select(eq,e,n)
 kkt=metrics(eq,numeric,n,angle_tolerance=1e-8);need(kkt['passed'],'strict full KKT')
 return {'SCUC':ua,'SCED_KKT':kkt,'SCED_response_kind':certificate['certificate']['kind'],'dual_certificate':certificate['certificate'],'dual_certificate_sha256':certificate['certificate_sha256'],'native_dual_passed':certificate['certificate']['native_KKT']['passed']}

def export_plan(bundle,*,expected_bundle_sha256):
 """Digest must be delivered by trusted host, not read from the bundle itself."""
 need(digest(bundle)==expected_bundle_sha256,'trusted host bundle mismatch')
 need(bundle['schema']=='gd.fixed.host_bundle.v1' and bundle['mode']=='fixed_curve_research','unsupported typed/formal bundle')
 q,u,eq,e,n=[bundle[k]for k in ('SCUC_request','SCUC_response','SCED_request','SCED_response','names')];proof=verify_pair(q,u,eq,e,n)
 case=unpack(bundle['case']);c=case['contract'];start=tick(c['domain_start'],c['origin']);available=tick(bundle['available_at'],c['origin']);need(available>=tick(c['cutoff'],c['origin']),'availability before model cutoff')
 units=case['energy_inputs']['thermal'];count=len(case['energy_inputs']['horizon']['periods']);values=dict(zip(n['variables'],e['primal']));events={}
 for g in units:
  cv=case['curves'][g];need(cv.get('basis','research')=='research'and not cv.get('classes'),'fixed curve only; no invented typed mapping')
  lead=aligned_tick(cv['notice_h']*4,'notice lead');need(lead>=0,'negative lead')
  for j in range(count):
   for phase,stem in [('startup','start'),('shutdown','stop')]:
    value=values[f'{stem}[{g},{j}]'];need(abs(value-round(value))<=1e-6,'binary event')
    if round(value):
     key=f'{g}:{phase}:{start+j}';rows=[{k:r[k]for k in ('begin','mean','end')}for r in cv[phase]]
     events[key]=dict(event_id=key,unit=g,phase=phase,begin_tick=start+j,notice_tick=start+j-lead if phase=='startup'else start+j,rows=rows,curve_parameter_sha256=digest(cv[phase]))
 z=dict(schema='gd.fixed.accepted_plan.v1',run_id=bundle['run_id'],case_identity=bundle['case_identity'],origin=c['origin'],domain_start_tick=start,cells=count,available_tick=available,bundle_sha256=expected_bundle_sha256,events=events,formal=False,native_calls=0,verification=proof)
 z['plan_sha256']=digest(z);return z

def publish(bundle,previous,*,expected_bundle_sha256,cutoff_tick,simulate=False,expected_previous_ledger_sha256=None):
 need(simulate is True,'explicit simulated publication required');plan=export_plan(bundle,expected_bundle_sha256=expected_bundle_sha256)
 need(type(cutoff_tick)is int and cutoff_tick>=plan['available_tick'],'future plan')
 if previous is None:receipts={}
 else:
  verify_ledger(previous);need(previous['ledger_sha256']==expected_previous_ledger_sha256,'host previous ledger selection');need(all(previous[k]==plan[k]for k in ('run_id','case_identity','origin')),'ledger identity mismatch');need(cutoff_tick>=previous['cutoff_tick'],'clock reversal');receipts=deepcopy(previous['receipts'])
 for key,r in receipts.items():
  if plan['domain_start_tick']<=r['begin_tick']<plan['domain_start_tick']+plan['cells']:
   need(key in plan['events'],'accepted plan cancels issued event')
   need(all(r[k]==plan['events'][key][k]for k in ('unit','phase','begin_tick','notice_tick','rows','curve_parameter_sha256')),'accepted plan changes issued event')
 pending={}
 for key,event in plan['events'].items():
  if key in receipts:continue
  if event['phase']=='shutdown':pending[key]={**event,'reason':'explicit shutdown instruction permission workflow required'};continue
  due=event['notice_tick']
  if due>cutoff_tick:pending[key]={**event,'reason':'not_due'};continue
  need(due==cutoff_tick and plan['available_tick']<=due,'missed notice deadline; no backdating')
  r={**event,'issued':True,'issued_tick':cutoff_tick,'source':'explicit accepted-plan simulated research notice; not operator authority','origin':plan['origin'],'run_id':plan['run_id'],'case_identity':plan['case_identity'],'plan_sha256':plan['plan_sha256'],'formal':False};r['receipt_hash']=digest(r);receipts[key]=r
 z=dict(schema='gd.fixed.research_notice_ledger.v1',run_id=plan['run_id'],case_identity=plan['case_identity'],origin=plan['origin'],cutoff_tick=cutoff_tick,receipts=receipts,pending=pending,formal=False,actual_dispatch_authorized=False)
 z['ledger_sha256']=digest(z);return z

def verify_ledger(z):
 need(digest({k:v for k,v in z.items()if k!='ledger_sha256'})==z['ledger_sha256'],'ledger changed')
 for r in z['receipts'].values():
  need(r['receipt_hash']==digest({k:v for k,v in r.items()if k!='receipt_hash'}),'receipt changed')
  need(r['issued'] is True and r['source'] and r['issued_tick']<=z['cutoff_tick'],'unissued/future receipt')
  need(all(r[k]==z[k]for k in ('run_id','case_identity','origin')),'receipt identity')

def consume(bundle,ledger,expected_bundle_sha256,expected_ledger_sha256):
 plan=export_plan(bundle,expected_bundle_sha256=expected_bundle_sha256);verify_ledger(ledger);need(ledger['ledger_sha256']==expected_ledger_sha256,'host ledger selection')
 need(all(ledger[k]==plan[k]for k in ('run_id','case_identity','origin')),'consumer case/run mismatch')
 case=unpack(bundle['case']);cut=tick(case['contract']['cutoff'],plan['origin']);need(ledger['cutoff_tick']<=cut,'ledger unavailable at consumer cutoff');validate_receipts(case,list(ledger['receipts'].values()),cut)
 return case,plan

def build_candidate(bundle,ledger,*,expected_bundle_sha256,expected_ledger_sha256):
 """Append ledger locks to host-verified full matrix; retain every original constraint.
 New physical boundary requires host to rebuild and verify a NEW bundle first.
 """
 case,plan=consume(bundle,ledger,expected_bundle_sha256,expected_ledger_sha256);q=deepcopy(bundle['SCUC_request']);q.pop('deadline',None);n=deepcopy(bundle['names']);ix={s:i for i,s in enumerate(n['variables'])};count=plan['cells'];start=plan['domain_start_tick'];cut=tick(case['contract']['cutoff'],plan['origin']);added=0
 def fix(g,j,f,v,label):
  nonlocal added
  q['rows'].append(dict(lb=v,ub=v,terms=[[ix[f'{f}[{g},{j}]'],1.]]));n['constraints'].append(f'fixed_ledger[{label},{g},{j},{f}]');added+=1
 for g,cv in case['curves'].items():
  lead=aligned_tick(cv['notice_h']*4,'notice lead')
  for j in range(count):
   nt=start+j-lead
   if nt<=cut:
    issued=any(r['unit']==g and r['phase']=='startup'and r['begin_tick']==start+j for r in ledger['receipts'].values());fix(g,j,'start',int(issued),'notice')
 for r in ledger['receipts'].values():
  for k,row in enumerate(r['rows']):
   j=r['begin_tick']+k-start
   if 0<=j<count:
    for f,v in dict(online=1,start=int(k==0 and r['phase']=='startup'),stop=int(k==0 and r['phase']=='shutdown'),power=row['mean']).items():fix(r['unit'],j,f,v,r['event_id'])
 return dict(template=q,names=n,ledger_sha256=ledger['ledger_sha256'],added_rows=added,original_policy_and_AGC_preserved=True,formal=False,native_calls=0)

def carry_candidate(bundle,ledger,*,expected_bundle_sha256,expected_ledger_sha256,executed_cells=1):
 case,plan=consume(bundle,ledger,expected_bundle_sha256,expected_ledger_sha256);need(executed_cells==1,'provincial execution contract exactly one 15-minute cell')
 result=derive(case,unpack(bundle['boundary']),bundle['names'],bundle['SCED_response']['primal'],executed_cells=1,receipts=list(ledger['receipts'].values()))
 result['storage'],result['power_normalization'],result['energy_normalization']=storage_carry(bundle)
 begin=stamp(case['contract']['domain_start']);origin=case['contract']['origin'];cut=tick(begin.isoformat(),origin)
 result.update(cutoff=begin.isoformat(),boundary_at=(begin+timedelta(minutes=15)).isoformat(),executed_end_exclusive=cut,issued_end_exclusive=cut+1,predicted_issued_prefix=True)
 result['ledger_sha256']=ledger['ledger_sha256'];result['notice_values']={g:{str(r['notice_tick']):1 for r in ledger['receipts'].values()if r['unit']==g and r['phase']=='startup'}for g in case['curves']};result['case_identity']=plan['case_identity'];result['run_id']=plan['run_id']
 result['builder_boundary']=dict(thermal=deepcopy(result['boundary_states']),thermal_age_h=deepcopy(result['thermal_age_h']),storage=deepcopy(result['storage']),notice_values=deepcopy(result['notice_values']),boundary_at=result['boundary_at'],cutoff=result['cutoff'],ledger_sha256=ledger['ledger_sha256'],formal=False,safety_release=False)
 return result

def storage_carry(bundle):
 """Provincial one-cell storage/cycle replay; no new physics/default data."""
 case=unpack(bundle['case']);v=dict(zip(bundle['names']['variables'],bundle['SCED_response']['primal']));storage={};corrections=[];energy_corrections=[]
 begin=stamp(case['contract']['domain_start']);delivery=begin+timedelta(minutes=15)
 for u,b in case.get('batteries',{}).items():
  E=b['initial_energy_mwh'];eta=math.sqrt(b['roundtrip_efficiency']);mode=b['initial_mode'];age=b['initial_mode_age_h'];ledger=deepcopy(b['cycle_budget'])
  for i in range(1):
   ch=v[f'battery.charge[{u},{i}]'];dis=v[f'battery.discharge[{u},{i}]'];new='charge'if v[f'battery.charge_mode[{u},{i}]']>.5 else'discharge'if v[f'battery.discharge_mode[{u},{i}]']>.5 else'idle';age=age+1/4 if new==mode else 1/4;mode=new;E+=(eta*ch-dis/eta)/4;need(abs(E-v[f'battery.energy[{u},{i}]'])<=1e-7,'SOC replay');date=(begin+timedelta(minutes=15*i)).date().isoformat();ledger[date]['used_internal_mwh']+=(eta*ch+dis/eta)/4
  oldhour=(delivery-timedelta(minutes=15)).replace(minute=0,second=0,microsecond=0);hour=delivery.replace(minute=0,second=0,microsecond=0);keys=[k for k in v if k.startswith('battery.hour_direction['+u+',')and oldhour.isoformat()in k];need(len(keys)==1,'hour direction')
  power=dis-ch;wrong=(power!=0 if mode=='idle'else power>0 if mode=='charge'else power<0)
  if wrong:need(abs(power)<=1e-10,'material power contradiction');corrections.append({'unit':u,'raw':power,'canonical':0.});power=0.
  if not b['energy_min_mwh']<=E<=b['energy_max_mwh']:
   bound=b['energy_min_mwh']if E<b['energy_min_mwh']else b['energy_max_mwh'];native=v[f'battery.energy[{u},0]'];need(abs(E-bound)<=1e-10 and abs(native-bound)<=1e-10,'material SOC violation');canonical=native if b['energy_min_mwh']<=native<=b['energy_max_mwh']else bound;energy_corrections.append({'unit':u,'raw':E,'canonical':canonical});E=canonical
  storage[u]=dict(initial_energy_mwh=E,initial_power_mw=power,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=(1 if v[keys[0]]>.5 else-1)if oldhour==hour else 0)
 return storage,corrections,energy_corrections

def apply_ledger_to_free_model(model,case,ledger,*,host_case_identity,run_id,expected_ledger_sha256):
 """Host invokes after constructing NEW full free-SCUC with carried boundary.
 This is additive only. Host retains reserve/AGC/protection/storage and source checks.
 """
 import pyomo.environ as p
 from exact_fixed import validate_receipts
 verify_ledger(ledger);need(ledger['ledger_sha256']==expected_ledger_sha256,'host ledger selection')
 need(ledger['case_identity']==host_case_identity and ledger['run_id']==run_id and ledger['origin']==case['contract']['origin'],'host model identity')
 cut=tick(case['contract']['cutoff'],ledger['origin']);start=tick(case['contract']['domain_start'],ledger['origin']);need(ledger['cutoff_tick']<=cut,'future ledger')
 need(set(model.G)==set(case['curves'])and len(model.T)==len(case['energy_inputs']['horizon']['periods']),'model domain')
 leads=validate_receipts(case,list(ledger['receipts'].values()),cut)
 need(not hasattr(model,'fixed_event_ledger_rows'),'ledger already attached');model.fixed_event_ledger_rows=p.ConstraintList()
 for g in model.G:
  for j in model.T:
   if start+j-leads[g]<=cut:
    issued=any(r['unit']==g and r['phase']=='startup'and r['begin_tick']==start+j for r in ledger['receipts'].values());model.fixed_event_ledger_rows.add(model.start[g,j]==int(issued))
 for r in ledger['receipts'].values():
  for k,row in enumerate(r['rows']):
   j=r['begin_tick']+k-start
   if j in model.T:
    for f,v in dict(online=1,start=int(k==0 and r['phase']=='startup'),stop=int(k==0 and r['phase']=='shutdown'),power=row['mean']).items():model.fixed_event_ledger_rows.add(getattr(model,f)[r['unit'],j]==v)
 return dict(rows_added=len(model.fixed_event_ledger_rows),ledger_sha256=expected_ledger_sha256,native_calls=0,original_constraints_removed=False,formal=False)
