"""Bounded research AGC supervisor; no native calls or authority to publish/pay."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import json,hashlib,sys,importlib.util,math
B=Path(__file__).resolve().parent;ROOT=B.parent;THERM={'C1','C2','C3','CCGT','GT'}
def need(x,m):
 if not x:raise ValueError(m)
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def stamp(x):
 d=datetime.fromisoformat(x);need(d.tzinfo is not None,'aware timestamp');return d
def file_ref(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))
def resolve(r):
 p=Path(r['path']);need(p.is_file()and sha(p)==r['sha256'],'evidence file hash mismatch');return p
def module(name,p):
 s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def algorithms():
 sys.path.insert(0,str(ROOT/'guangdong_r73_agc_parameter_interface_20261112'))
 return module('joint_r73',ROOT/'guangdong_r73_agc_parameter_interface_20261112/adapter.py'),module('joint_r77',ROOT/'guangdong_r77_agc_safety_gate_20261112/gate.py')
def context_from_run(run,hour,zone,as_of,*,parent_window=None):
 run=Path(run).resolve();r=read(run/'RUN.json');a=read(run/'ARTIFACTS.json');need(a['status']=='accepted'and a['run_id']==r['run_id'],'accepted parent DA')
 need(isinstance(zone,str)and zone.strip(),'zone');h=stamp(hour);t=stamp(as_of);need(h.minute==h.second==h.microsecond==0 and t<=h-timedelta(minutes=30),'hour and T-30 cutoff')
 pins={'RUN':file_ref(run/'RUN.json'),'ARTIFACTS':file_ref(run/'ARTIFACTS.json')}
 for stage in ('da_scuc','da_sced','da_price'):
  refs=a['stages'][stage]
  for key in ('acceptance','request','response'):
   ref=refs[key];p=(run/ref['path']).resolve();need(p.is_relative_to(run)and sha(p)==ref['sha256'],'parent stage artifact identity');pins[stage+'.'+key]=file_ref(p)
  z=read(resolve(pins[stage+'.acceptance']));need(z['status']=='accepted'and z['run_id']==r['run_id']and z['stage']==stage,'same parent stage')
 for key in ('case','agc'):
  ref=a['inputs'][key];p=run/ref['path'];need(sha(p)==ref['sha256'],'explicit input');pins[key]=file_ref(p)
 ref=a['stages']['da_sced']['physical_solution'];p=run/ref['path'];need(sha(p)==ref['sha256'],'parent physical');pins['physical']=file_ref(p)
 if(run/'rt/SOURCE_CONTRACT.json').exists():pins['source_contract']=file_ref(run/'rt/SOURCE_CONTRACT.json')
 agc=read(resolve(pins['agc']));need(set(agc)==THERM and all(u['qualification']=='explicit_synthetic_qualified'for u in agc.values()),'only five explicitly research-qualified thermals')
 day=stamp(read(resolve(pins['physical']))['physical_cells'][0]['start']);idx=(h-day).total_seconds()/900;need(idx.is_integer()and 0<=idx<=92,'parent D-hour range')
 pw=int((t-day).total_seconds()//900)+1 if parent_window is None else parent_window;need(type(pw)is int and 0<=pw<=95,'parent window')
 parent=run/f'rt/W{pw}/prepared';local=read(parent/'local_RT_input.json');available=stamp(local['window']['cutoff']);need(available<=t,'future parent physical state/template beyond clearing as_of')
 need(stamp(local['window']['start'])<=h and stamp(local['window']['horizon_end'])>=h+timedelta(hours=1),'parent physical coverage does not cover whole AGC hour')
 template=read(parent/'template.json');frontier=template['r67_committed_prefix_end'];need(frontier==3*pw,'known issued frontier source')
 need(local['projection']['cutoff']==local['window']['cutoff'],'parent actual cutoff mismatch')
 for key,name in [('parent_template','template.json'),('parent_names','names.json'),('parent_local_input','local_RT_input.json')]:pins[key]=file_ref(parent/name)
 c=dict(schema='gd.agc.joint.context.v1',parent_run_id=r['run_id'],parent_window=pw,source_available_at=available.isoformat(),fixed_issued_frontier=frontier,fixed_issued_frontier_at=(day+timedelta(minutes=5*frontier)).isoformat(),logical_clock_is_simulated=True,parent_new_solution_or_commands_not_used=True,hour_start=hour,hour_end=(h+timedelta(hours=1)).isoformat(),zone=zone,as_of=as_of,source_identity=a['source_identity'],commitment_sha256=pins['da_scuc.response']['sha256'],parent_refs=pins,Qzone_mw=50.,procurement_target_mw=50.,positive_reserve_mw=400.,accident_reserve_mw=400.,eligibility_scope=sorted(THERM),new_BESS_VRE_qualification=False,scope='offline independent candidate branch; old actual receipts are parent provenance, not acceptance of new awards',formal=False)
 c['identity']=digest(c);return c
def verify_context(c):
 need(c['identity']==digest({k:v for k,v in c.items()if k!='identity'}),'context mutated')
 for r in c['parent_refs'].values():resolve(r)
 return c
def binding(c,iteration,parent):return dict(context_identity=c['identity'],parent_run_id=c['parent_run_id'],hour_start=c['hour_start'],hour_end=c['hour_end'],zone=c['zone'],source_identity=c['source_identity'],commitment_sha256=c['commitment_sha256'],parent_window=c['parent_window'],source_available_at=c['source_available_at'],fixed_issued_frontier=c['fixed_issued_frontier'],iteration=iteration,parent_iteration_sha256=parent)
def physical_request(c,clear,b):
 """Future branch margins from original raw capability, never double-deduct AGC."""
 sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
 from smm.gd_october_job_codec import loads
 case=loads(resolve(c['parent_refs']['case']).read_bytes());physical=read(resolve(c['parent_refs']['physical']));start=stamp(case['contract']['day_start']);offset=(stamp(c['hour_start'])-start).total_seconds()/300;need(offset.is_integer()and 0<=offset<=276,'D hour only')
 rows=[];phase=[]
 for t in range(int(offset),int(offset)+12):
  for g in sorted(THERM):
   u=case['energy_inputs']['thermal'][g];award=clear['awards_mw'].get(g,0.);lo=u['min_mw'][t];hi=u['max_mw'][t];z=physical['physical_cells'][t]['thermal'][g]
   if award and z['phase']!='stable':phase.append(dict(unit=g,index=t,phase=z['phase']))
   rows.append(dict(unit=g,global_cell=t,raw_pmin_mw=lo,raw_pmax_mw=hi,agc_up_mw=award,agc_down_mw=award,dispatch_min_mw=lo+award,dispatch_max_mw=hi-award))
 worker=ROOT/'guangdong_r53_same_source_da_chain_20261112/dual_precision_v3/highs_worker_unscaled_dual_1e9.py'
 producers=[B/'proof.py',B/'review_and_ac.py',B/'launch_candidate.py',worker,ROOT/'guangdong_r30_ac_security_20261112/ac_security.py',ROOT/'guangdong_fresh_run_ac_bridge_20261112/bridge.py']
 return dict(producer_pins={str(p):sha(p)for p in producers},native_worker=file_ref(worker),schema='gd.agc.joint.physical_request.v1',binding=b,parent_refs=deepcopy(c['parent_refs']),clearing_result_sha256=digest(clear),margin_rows=rows,unresolved_phase=phase,positive_reserve_mw=400.,accident_reserve_mw=400.,requirements=['same commitment','fixed executed+issued prefix','full remaining physical tail','original network/ramp/SOC/date-throughput/D96','no BESS/VRE ancillary credit','new independent PRICE after changed dispatch','candidate-specific full-row KKT and AC'],old_dispatch_acceptance_reusable=False,execution_authority=False,native_calls=0)
class Loop:
 def __init__(self,context,max_rounds=4):
  self.context=deepcopy(verify_context(context));need(type(max_rounds)is int and 1<=max_rounds<=8,'finite iteration bound');self.max_rounds=max_rounds;self.rounds=[];self.excluded=set();self.status='awaiting_sourced_bids';self._seal()
 def _seal(self):self.seal=digest(dict(context=self.context,rounds=self.rounds,excluded=sorted(self.excluded),status=self.status,max_rounds=self.max_rounds))
 def _check(self):
  need(self.seal==digest(dict(context=self.context,rounds=self.rounds,excluded=sorted(self.excluded),status=self.status,max_rounds=self.max_rounds)),'controller state mutated');verify_context(self.context)
  for row in self.rounds:
   for p,h in row['source_pins'].items():need(sha(p)==h,'stage source changed')
 def clear(self,thermal_input):
  self._check();need(self.status in ('awaiting_sourced_bids','reclear_required'),'not ready to clear');need(len(self.rounds)<self.max_rounds,'round limit reached')
  d=deepcopy(thermal_input);c=self.context;need(d['hour_start']==c['hour_start']and d['as_of']==c['as_of'],'same hour/cutoff');need({u['resource_id']for u in d['units']}==THERM,'exact original5 thermal population; no fabricated replacement')
  need(all(u.get('zone')==c['zone']and u.get('kind')=='thermal'for u in d['units']),'same zone/thermal only');need(d['parameters']['distribution_zone_demand_mw']['value']==50 and d['parameters']['provincial_procurement_target_mw']['value']==50,'Qzone/target50 fixed')
  need(not d.get('safety_excluded'),'exclusions are controller-owned');d['safety_excluded']=sorted(self.excluded);r73,_=algorithms();z=r73.run(d);b=binding(c,len(self.rounds),digest(self.rounds[-1])if self.rounds else None)
  row=dict(binding=b,input_sha256=digest(d),input=d,clearing=z,excluded=sorted(self.excluded),source_pins={str(p):sha(p)for p in [B/'loop.py',B/'proof.py',ROOT/'guangdong_r73_agc_parameter_interface_20261112/adapter.py',ROOT/'guangdong_r73_agc_parameter_interface_20261112/projection.py',ROOT/'guangdong_r77_agc_safety_gate_20261112/gate.py']})
  row['physical_request']=physical_request(c,z,b)if z['unmet_mw']<=1e-9 else None;self.rounds.append(row)
  self.status='shortage_pending'if z['unmet_mw']>1e-9 else 'physical_precondition_pending'if row['physical_request']['unresolved_phase']else 'awaiting_SCED_AC_evidence';self._seal();return deepcopy(row)
 def submit_declarations(self,safety):
  """R77 declarations are accepted as pending metadata, never physical proof."""
  self._check();need(self.status=='awaiting_SCED_AC_evidence','no physical candidate');_,gate=algorithms();c=self.context;need(safety['hour_start']==c['hour_start']and safety['procurement_target_mw']==50,'same gate hour/target');aw=self.rounds[-1]['clearing']['awards_mw'];need({u['resource_id']:u['awarded_mw']for u in safety['awarded_units']}==aw,'exact awarded population')
  z=gate.run(safety);self.rounds[-1]['unverified_safety_declarations']=z;self.status='physical_evidence_pending';self._seal();return dict(status=self.status,reason='caller declarations, including pass/fail, do not establish physical proof or certified attribution',gate=z,payable=False)
 def apply_verified_failure(self,proof):
  """Only numerical evidence adapter output reverified here may exclude a unit."""
  self._check();need(self.status in ('awaiting_SCED_AC_evidence','physical_evidence_pending'),'no candidate');from proof import verify
  z=verify(proof,self.rounds[-1]['physical_request']);self.rounds[-1]['physical_evidence']=z
  # Reuse R77 cause-local ordering only for numerically attributed awarded failures.
  _,gate=algorithms();clear=self.rounds[-1]['clearing'];ev=dict(id=digest(proof),source='new same-candidate raw numeric SCED/AC evidence',source_grade='research',hour_start=self.context['hour_start'],as_of=self.context['as_of']);units=[]
  for rid,award in clear['awards_mw'].items():
   checks={name:dict(status='unknown',reason='not independently established',evidence=ev)for name in gate.CHECKS}
   for name in ('hydro_vibration','storage_soc'):checks[name]=dict(status='not_applicable',reason='thermal resource',evidence=ev)
   if rid in z['unit_failures']:checks['capacity_balance']=dict(status='fail',reason='same-candidate actual post-AC P/AGC bounds violated',evidence=ev,attribution='unit_specific',cause_id='post_AC_P_AGC_'+digest(proof),cause_evidence=ev)
   offer=next(u for u in self.rounds[-1]['input']['units']if u['resource_id']==rid);km=self.rounds[-1]['input']['parameters']['full_control_area_kmax']['value'];P=offer['k']/km
   units.append(dict(resource_id=rid,kind='thermal',awarded_mw=award,sorting_price=offer['bid']/P,P=P,k1=offer['k1'],award_evidence=ev,checks=checks))
  g=gate.run(dict(schema='gd.agc.safety.r77.v1',mode='research',hour_start=self.context['hour_start'],gate_as_of=self.context['as_of'],procurement_target_mw=50,awarded_units=units));self.rounds[-1]['attributed_R77_gate']=g
  if g['removed']:
   # Same sourced physical cause ordering follows R77; no cross-cause prescription.
   self.excluded.update(g['removed']);self.status='reclear_required'if len(self.rounds)<self.max_rounds else'iteration_limit_pending'
  else:self.status='loss_allocation_pending'if any(x['reason']=='slack_loss_allocation_pending'for x in z['global_unsafe'])else'global_safety_pending'if z['global_unsafe']else'joint_safety_pending' # no fabricated unit attribution
  self._seal();return dict(status=self.status,verified=z,payable=False,formal_market_complete=False)
 def snapshot(self):self._check();return deepcopy(dict(schema='gd.agc.joint.loop.v1',context=self.context,rounds=self.rounds,excluded=sorted(self.excluded),status=self.status,seal=self.seal,max_rounds=self.max_rounds,payable=False,formal_market_complete=False,native_calls=0))
