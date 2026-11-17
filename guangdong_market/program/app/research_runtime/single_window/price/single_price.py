"""Independent eight-quarter PRICE preparation from a real registered near24 pair.
No native call. Explicit research sample only; actual arrival clocks unknown.
"""
from pathlib import Path
from copy import deepcopy
import sys,json,hashlib,importlib.util,re,math
PRICE_ENGINE=None
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def prepare_eight_quarters(*,program_root,session,hooks,near_prepared,near_actual,base_run,selected_DA,output,policy_mode='explicit_research_sample',event_profile=None,loss_profile_path=None,loss_receipt_path=None):
 global PRICE_ENGINE
 need(policy_mode=='explicit_research_sample','explicit research policy required')
 root=Path(program_root).resolve();PRICE_ENGINE=root/'engines/guangdong_short_unified_v6';R=PRICE_ENGINE/'rt';near=Path(near_prepared).resolve();actual=Path(near_actual).resolve();out=Path(output).resolve()
 need(hooks.session is session and str(near)in hooks.prepared,'real registered session/near required');session.verify_sources();record=hooks.prepared[str(near)]
 from price_source_binding import snapshot
 host_binding=snapshot(session)
 from loss_price import verify_registered_artifacts
 verify_registered_artifacts(near,record)
 bundle,bundle_hash=session.export_generated_pair(near,actual,record)
 need(bundle['run_id']==session.registry['run_id'],'same run source');need(Path(base_run).resolve()==Path(session.run).resolve(),'same base run')
 need(not out.exists(),'fresh output only');out.mkdir(parents=True)
 write(out/'HOST_SELECTION.json',host_binding)
 sys.path[:0]=[str(Path(__file__).parent),str(root/'app/gd_live_market_runtime'),str(PRICE_ENGINE/'price')]
 from smm.gd_october_job_codec import loads
 worker=session.program.engine.worker
 n=read(near/'names.json');q=worker.load(read(actual/'SCUC/JOB.json')['request']);u=worker.load(read(actual/'SCUC/COMPLETED.json')['response']);eq=worker.load(read(actual/'SCED/JOB.json')['request']);e=worker.load(read(actual/'SCED/COMPLETED.json')['response']);worker.checks(q,u,n,True);worker.checks(eq,e,n,False)
 assert all(abs(x-round(y))<1e-8 for x,y,v in zip(e['primal'],u['primal'],q['variables'])if v['kind']!='C')
 c=read(near/'CARRY.json');first=c['delivery_tick'];w=first//3-1;need(first%3==0,'aligned stage')
 base=Path(base_run).resolve();da=Path(selected_DA).resolve();casefile=base/'seed/case.json';case=loads(casefile.read_bytes());need(sha(casefile)==c['seed_case_sha256'],'same seed');r=read(base/'seed/request.json');r.pop('rolling_cycle_boundary',None);r.update(run_id=session.registry['run_id'],window=w,delivery_tick=first,cutoff=c['cutoff']);a=dict(run_id=r['run_id'],window=w,SCED_response_sha256=read(actual/'SCED/COMPLETED.json')['response']['raw_sha256'])
 from loss_price import inherit
 loss,loss_trace,loss_pins=inherit(near=near,profile_path=loss_profile_path,receipt_path=loss_receipt_path,scuc=q,sced=eq,case=case,first=first,origin=r['origin'])
 x={}
 for k,v in zip(n['variables'],e['primal']):
  m=re.search(r'(\d+)\]$',k)
  if m:k=k[:m.start(1)]+str(first+int(m[1]))+']'
  x[k]=v
 from incoming_active import bind_registered
 x,incoming_trace,incoming_pins=bind_registered(hooks=hooks,record=record,case=case,carry=c,values=x,program_root=root,event_profile=event_profile)
 for uid,b in c['storage'].items():
  x[f'battery.energy[{uid},{first-1}]']=b['initial_energy_mwh'];x[f'battery.power[{uid},{first-1}]']=b['initial_power_mw']
 for k,v in list(x.items()):
  if k.startswith('battery.charge['):x['battery.power'+k[len('battery.charge'):]]=x['battery.discharge'+k[len('battery.charge'):]]-v
 agc=read(base/'seed/agc.json');local=read(near/'agc.json')
 for g in local:
  for side in ('up_mw','down_mw'):
   for t in range(24):agc[g][side][str(first+t)]=local[g][side][str(t)]
 
 af=out/'agc.json';write(af,agc);r['sources']['AGC'].update(path=str(af),sha256=sha(af));r['effective_agc_path']=str(af)
 from event_gate import supported_fixed_event_state
 state=supported_fixed_event_state(case=case,values=x,first=first,event_profile=event_profile)
 # Independently pinned real stage evidence; not a fabricated old accepted-pair schema.
 pins={str(p):sha(p)for p in [near/'CARRY.json',near/'names.json',near/'derived_case.json',actual/'SCUC/response.json.gz',actual/'SCED/response.json.gz',base/'seed/request.json',actual/'ACCEPTED_PAIR.json',casefile]}
 pins.update(loss_pins)
 pins.update(incoming_pins)
 proof=out/'STAGE_SOURCE.json';write(proof,dict(schema='gd.independent.near24.price_source.v1',pins=pins,SCED_raw_sha256=a['SCED_response_sha256'],interval=[first,first+24],logical_cutoff=c['cutoff'],actual_input_available_at=None,derived_result_available_at=None,mode='explicit_research_replay_unknown_arrival_clocks',event_profile=event_profile,affected_units=state['affected_units'],loss_inheritance=loss_trace,incoming_history=incoming_trace,old_price_values_consumed=False,old_policy_inputs_reused=True))
 sys.modules.pop('contracts',None)
 sys.path.insert(0,str(PRICE_ENGINE/'price'))
 from branch_parameters import sample,resolved
 r['typed_source_path']=str(proof)
 params=out/'PARAMETERS.json';sample(da,base,w,params,'near24_eight_quarter_research','current_sced_research',verified_request=r,accepted_source_pins=host_binding['source_pins'])
 explicit,contract,pars,boundary=resolved(params,case,da,base,w,r);explicit['eligibility']=state['eligibility']
 sys.path.insert(0,str(Path(__file__).parent));spec=importlib.util.spec_from_file_location('one_quarter_domain',Path(__file__).with_name('domain_event.py'));domain=importlib.util.module_from_spec(spec);domain.ENGINE=PRICE_ENGINE;spec.loader.exec_module(domain)
 domain.prepare(proof,casefile,af,da,out/'prepared',research_current_sced=True,boundary='current_sced_research',verified_inputs=(a,r,x),explicit=explicit,thermal_profile=state,selected_profile=event_profile,stage_boundary=c,quarter_node_loss_mw=loss)
 qq=read(out/'prepared/template.json');nn=read(out/'prepared/names.json')
 from wiring import apply_slacks
 from mapping_one import build as mapping
 qq,nn,trace=apply_slacks(qq,nn,mapping(qq,nn),explicit,pars,c['cutoff'])
 from thermal_reference import apply_to_serialized_model,RT_POLICY
 from functools import partial
 from realtime_reference import preserve_current_ramps
 from ramp_anchor import preceding_anchor,apply_first_ramp,POLICY as RP
 from state_adapter import reference_and_ramp
 art=read(da/'ARTIFACTS.json');need(sha(da/art['inputs']['case']['path'])==sha(casefile),'DA and RT case differ: use explicit DA physical parameters, not RT substitutes');ph=read(da/art['stages']['da_sced']['physical_solution']['path'])['physical_cells'];specs={};ramps={};avg=lambda x:math.fsum(x)/len(x)
 for g,d in case['energy_inputs']['thermal'].items():
  for quarter in range(8):
   ts=range(first+3*quarter,first+3*quarter+3);specs[f'P[{g},{quarter}]']=dict(status=state['eligibility'][g][quarter],current_sced=avg([x[f'power[{g},{t}]']for t in ts]),da_sced=avg([ph[t]['thermal'][g]['mean_mw']for t in ts]),current_min=avg([d['min_mw'][t]+agc[g]['down_mw'][str(t)]for t in ts]),current_max=avg([d['max_mw'][t]-agc[g]['up_mw'][str(t)]for t in ts]),da_min=avg([d['min_mw'][t]+ph[t]['thermal'][g]['agc_down_reserved_mw']for t in ts]),da_max=avg([d['max_mw'][t]-ph[t]['thermal'][g]['agc_up_reserved_mw']for t in ts]),delta=explicit['delta'])
  ts=range(first,first+3);ramps[f'P[{g},0]']=dict(DA_anchor_MW=preceding_anchor([cc['thermal'][g]['mean_mw']for cc in ph],first,d['initial_power_mw']),up_budget_MW=min(d['ramp_up_mw_per_h'][t]for t in ts)*.25,down_budget_MW=min(d['ramp_down_mw_per_h'][t]for t in ts)*.25)
 qq,thermal,ramp=reference_and_ramp(qq,nn,specs,ramps,state,partial(apply_to_serialized_model,policy=RT_POLICY),preserve_current_ramps,ramp_policy='current_SCED_incoming_state')
 write(out/'MODEL_EXTENSION.json',dict(slacks=trace,thermal=thermal,ramp=ramp,loss_inheritance=loss_trace));write(out/'PRICE_TEMPLATE.json',qq);write(out/'PRICE_NAMES.json',nn)

 qq['options'].update(time_limit=3600.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
 write(out/'NATIVE_PRICE_TEMPLATE.json',qq)
 result=dict(status='prepared_eight_quarter_research',stage_source_bundle_sha256=bundle_hash,first_tick=first,end_exclusive=first+24,rows=len(qq['rows']),columns=len(qq['variables']),native_calls=0,formal=False,publication=False,execution=False,settlement=False,actual_input_available_at=None,derived_result_available_at=None)
 write(out/'RESULT.json',result)
 write(out/'PREPARED_CONTENT_PINS.json',{str(f):sha(f)for f in [out/'NATIVE_PRICE_TEMPLATE.json',out/'PRICE_NAMES.json',out/'PARAMETERS.json',out/'STAGE_SOURCE.json',out/'RESULT.json',out/'agc.json',out/'MODEL_EXTENSION.json',out/'HOST_SELECTION.json']})
 return result

_prepare_eight_quarters=prepare_eight_quarters
def prepare_eight_quarters(**kwargs):
 """Restore caller's import bindings/path even if pricing preparation fails."""
 path=list(sys.path);mods=dict(sys.modules)
 try:return _prepare_eight_quarters(**kwargs)
 finally:
  sys.path[:]=path
  for k,v in mods.items():
   if sys.modules.get(k)is not v:sys.modules[k]=v
  root=Path(kwargs['program_root']).resolve()
  controlled=(root/'engines/guangdong_short_unified_v6',root/'app/gd_live_market_runtime',Path(__file__).resolve().parent)
  for k in list(set(sys.modules)-set(mods)):
   f=getattr(sys.modules.get(k),'__file__',None)
   if f and any(Path(f).resolve().is_relative_to(b)for b in controlled):sys.modules.pop(k,None)

def execute_eight_quarters(*,session,prepared,output,execute_native=False):
 """Exactly one fresh LP; refuses any existing output, no auto retry."""
 from datetime import datetime,timezone
 import time
 need(execute_native is True,'explicit one PRICE native approval required');session.verify_sources();p=Path(prepared).resolve();out=Path(output).resolve()
 verify_prepared_content(p)
 from price_source_binding import verify
 verify(read(p/'HOST_SELECTION.json'),session)
 result=read(p/'RESULT.json');need(result['status']=='prepared_eight_quarter_research'and result['native_calls']==0,'pure prepared PRICE required')
 src=read(p/'STAGE_SOURCE.json')
 for f,h in src['pins'].items():need(sha(f)==h,'near source drift '+f)
 need(src['interval']==[result['first_tick'],result['end_exclusive']],'quarter source mismatch')
 q=read(p/'NATIVE_PRICE_TEMPLATE.json');n=read(p/'PRICE_NAMES.json');need(all(z['kind']=='C'for z in q['variables']),'PRICE must be LP');need(q['options']['time_limit']==3600. and q['options']['dual_feasibility_tolerance']==1e-10,'explicit native precision/budget')
 need(not out.exists(),'existing PRICE occurrence: inspect, never rerun');out.mkdir(parents=True)
 write(out/'INPUT_PINS.json',{str(f):sha(f)for f in [p/'NATIVE_PRICE_TEMPLATE.json',p/'PRICE_NAMES.json',p/'STAGE_SOURCE.json',p/'PARAMETERS.json',p/'RESULT.json',p/'HOST_SELECTION.json']})
 worker=session.program.engine.worker;start=datetime.now(timezone.utc).isoformat();t=time.perf_counter();write(out/'WALL_START.json',dict(utc=start,time_limit_s=3600,gap=None,scope='worker.native transport plus process'))
 try:qr,rr=worker.native(out,'PRICE',q,worker.V/'lp_worker.py',3600.)
 finally:write(out/'WALL_END.json',dict(started_at=start,ended_at=datetime.now(timezone.utc).isoformat(),duration_s=time.perf_counter()-t))
 t=time.perf_counter();audit=worker.checks(qr,rr,n,False);prices={k:rr['dual'][i]/.25 for i,k in enumerate(n['constraints'])if k.startswith('balance[')};slacks={k:rr['primal'][i]for i,k in enumerate(n['variables'])if k.startswith('PRICE_')}
 z=dict(status='accepted_eight_quarter_research_PRICE',source_stage='near24',PRICE_quarters=8,source_bundle_sha256=result['stage_source_bundle_sha256'],interval=src['interval'],logical_cutoff=src['logical_cutoff'],actual_input_available_at=None,derived_result_available_at=None,prices_yuan_per_mwh=prices,slacks=slacks,audit=audit,validation_duration_s=time.perf_counter()-t,native_calls=1,gap=None,formal=False,publication=False,execution=False,settlement=False)
 z['price_quality']=dict(has_nonzero_relaxation=any(abs(v)>1e-8 for v in slacks.values()),shortage_mw=slacks.get('PRICE_load_shortage[0]',0.),shortage_by_quarter_mw=[slacks.get(f'PRICE_load_shortage[{q}]',0.)for q in range(8)],total_shortage_mwh=.25*sum(v for k,v in slacks.items()if k.startswith('PRICE_load_shortage[')),execution_allowed=False,formal_price_usable=False)
 write(out/'RESULT.json',z);return z

def verify_prepared_content(prepared):
 p=Path(prepared).resolve()
 pins=read(p/'PREPARED_CONTENT_PINS.json')
 for f,h in pins.items():need(sha(f)==h,'prepared PRICE content drift '+f)
 need(all(str(p/f)in pins for f in ('NATIVE_PRICE_TEMPLATE.json','PRICE_NAMES.json','PARAMETERS.json','STAGE_SOURCE.json','RESULT.json','agc.json','MODEL_EXTENSION.json','HOST_SELECTION.json')),'incomplete prepared content receipt')
 return True
