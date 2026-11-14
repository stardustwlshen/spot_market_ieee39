"""Explicit trusted future thermal upper-bound tightening, no automatic AC inference."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import json,hashlib,math
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
class TighteningAdapter:
 def __init__(self,host):
  from unified import RollingHost
  need(isinstance(host,RollingHost),'trusted live host required');self.host=host
 def _validate(self,d):
  h=self.host;h.session.verify_sources();need(d['schema']=='gd.research.thermal_power_cap.v1'and d['branch_id']==h.branch_id and d['seed_case_sha256']==h.agc.seed_sha,'declaration branch/case mismatch')
  need(d['constraint_semantics']=='redispatch_basepoint_only','technical capacity derating requires joint P/AGC/reserve adapter; unsupported')
  need(d['mode']=='explicit_research_tightening'and bool(d['reason'])and bool(d['source_statement']),'explicit source and research scope required')
  need(d['preserve_AGC_mw']==50 and d['preserve_positive_reserve_mw']==d['preserve_accident_reserve_mw']==400,'50/400/400 invariant')
  need(set(d)=={'constraint_semantics','schema','branch_id','seed_case_sha256','mode','reason','source_statement','base_source_window','base_SCED_sha256','declared_at','constraints','preserve_AGC_mw','preserve_positive_reserve_mw','preserve_accident_reserve_mw'},'unknown/relaxing declaration fields')
  source_p,a=h.accepted_source(d['base_source_window']);source_p=Path(source_p);source_case=h.session.program.engine.loads((source_p/'derived_case.json').read_bytes());source_start=datetime.fromisoformat(read(source_p/'SUMMARY.json')['domain_start']);need(sha(Path(a)/'SCED/response.json.gz')==d['base_SCED_sha256'],'declaration accepted source mismatch')
  need(d['base_source_window']<h.window,'source not before candidate')
  c=read(h.cp);cut=datetime.fromisoformat(c['cutoff']);begin=cut+timedelta(minutes=15)
  need(source_start<=datetime.fromisoformat(d['declared_at'])<=cut,'source unavailable/backdated or future declaration')
  need(isinstance(d['constraints'],list)and 0<len(d['constraints'])<=240,'bounded nonempty constraints')
  seen=set()
  for x in d['constraints']:
   need(set(x)=={'unit','start','end','maximum_power_mw'},'only thermal power cap supported')
   start,end=datetime.fromisoformat(x['start']),datetime.fromisoformat(x['end']);cap=x['maximum_power_mw']
   need(start.utcoffset()==cut.utcoffset()==end.utcoffset()and begin<=start<end<=begin+timedelta(hours=4),'issued prefix or outside candidate horizon')
   need((start-begin).total_seconds()%300==0 and (end-begin).total_seconds()%300==0,'5min alignment')
   need(type(cap)in(int,float)and math.isfinite(cap)and cap>0,'positive finite cap; zero not an AGC substitute')
   for t in range(int((start-begin).total_seconds()/300),int((end-begin).total_seconds()/300)):
    g=x['unit'];known=source_case['energy_inputs']['thermal'];need(g in known,'unknown/unqualified thermal unit')
    st=int(((begin+timedelta(minutes=5*t))-source_start).total_seconds()/300);need(st in known[g]['min_mw']and st in known[g]['max_mw'],'cap outside sourced availability horizon')
    need(known[g]['min_mw'][st]+10<=cap<=known[g]['max_mw'][st]-10,'cap outside original AGC-compatible range or relaxes bound')
    key=(g,t);need(key not in seen,'duplicate cap');seen.add(key)
  return begin
 def registration_plan(self,declaration):
  d=deepcopy(declaration);begin=self._validate(d);h=self.host
  plan=dict(branch_id=h.branch_id,window=h.window,carry_sha256=sha(h.cp),declaration=d,domain_start=begin.isoformat(),effect='append future thermal basepoint upper-bound rows only; not technical derating/AC repair',automatic_execution=False,requested_native_calls_after_separate_approval={'SCUC':1,'SCED':1,'PRICE':0},native_calls_now=0)
  plan['confirmation_digest']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest();return plan
 def register_declaration(self,declaration,*,confirmed=False,expected_confirmation_digest=None):
  # Called only by trusted UI after displaying registration_plan and explicit owner confirmation.
  # Approval is an API argument, never inferred from fields inside uploaded JSON.
  need(confirmed is True,'explicit confirmation required after plan display')
  plan=self.registration_plan(declaration);need(plan['confirmation_digest']==expected_confirmation_digest,'confirmation stale or declaration/carry changed')
  folder=self.host.output/'safety_declarations';folder.mkdir(exist_ok=True)
  p=folder/(plan['confirmation_digest']+'.json');need(not p.exists(),'already registered; do not silently overwrite')
  with p.open('x')as f:json.dump(declaration,f,indent=2,allow_nan=False)
  self.host.session.registry['source_pins'][str(p.resolve())]=sha(p)
  self.host.session.registry['source_pins'][str(Path(__file__).resolve())]=sha(__file__)
  return dict(declaration_path=str(p.resolve()),sha256=sha(p),plan=plan,registered=True,prepared=False,native_calls=0)
 def declaration(self,path):
  h=self.host;h.session.verify_sources();p=Path(path).resolve();pins=h.session.registry['source_pins']
  need(str(p)in pins and sha(p)==pins[str(p)],'same-run declaration not registered by trusted host')
  d=read(p);begin=self._validate(d);return p,d,begin
 def prepare(self,path):
  h=self.host;need(h.pending is None,'unresolved prepared occurrence; never replace it');p,d,begin=self.declaration(path)
  h.session.registry['source_pins'][str(Path(__file__).resolve())]=sha(__file__)
  builder=h.session.builder;original=builder.serialize_model;proof=[]
  def hook(model,options):
   from pyomo.environ import ConstraintList
   case=model._r23['inputs'];thermal=case['energy_inputs']['thermal'];agc=model._r23.get('agc_awards')
   need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 changed')
   model.add_component('research_safety_power_caps',ConstraintList())
   for x in d['constraints']:
    g=x['unit'];need(g in thermal,'unknown/unqualified resource; thermal only')
    for t in range(int((datetime.fromisoformat(x['start'])-begin).total_seconds()/300),int((datetime.fromisoformat(x['end'])-begin).total_seconds()/300)):
     cap=x['maximum_power_mw'];need(thermal[g]['min_mw'][t]+10<=cap<=thermal[g]['max_mw'][t]-10,'cap must retain existing 10MW up/down headroom; no relaxation or qualification change')
     row=model.research_safety_power_caps.add(model.power[g,t]<=cap)
     proof.append(dict(name=row.name,unit=g,tick=t,cap_mw=cap,kind='additional_upper_bound_only'))
   return original(model,options)
  builder.serialize_model=hook
  try:result=h.prepare()
  finally:builder.serialize_model=original
  q=h.session.program.read(h.pending/'template.json')if hasattr(h.session.program,'read')else read(h.pending/'template.json')
  n=read(h.pending/'names.json');need(len(proof)>0 and all(r['name']in n['constraints']for r in proof),'declared rows missing')
  report=dict(branch_id=h.branch_id,window=h.window,prepared=str(h.pending),declaration_path=str(p),declaration_sha256=sha(p),added_rows=proof,native_calls=0,status='PREPARED_UNSOLVED_RESEARCH_TIGHTENING',original_AGC_50_preserved=True,original_400_400_preserved=True,safety_release=False,base_source_window=d['base_source_window'],not_AC_loss_linearization=True,constraint_semantics='redispatch_basepoint_only',technical_capacity_unchanged=True,AC_failure_resolved_not_claimed=True)
  dest=h.pending.parent/'SAFETY_TIGHTENING.json';dest.write_text(json.dumps(report,indent=2))
  # Full builder records the added rows and current host source pins; register our report as well.
  record=h.hooks.prepared[str(h.pending.resolve())];record['artifact_pins'][str(dest)]=sha(dest)
  return report
 def plan(self):
  h=self.host;need(h.pending is not None and (h.pending.parent/'SAFETY_TIGHTENING.json').exists(),'no registered tightened candidate')
  h.session.verify_sources()
  return dict(branch_id=h.branch_id,window=h.window,status='pending_authorization',requested_native_calls={'SCUC':1,'SCED':1,'PRICE':0},SCUC_seconds=3600,SCED_seconds=3600,mip_gap=.01,SCED_mip_gap=None,threads=1,solver_time_limit_scope='each_native_solve',process_grace_seconds=300,automatic_fallback=False,actual_native_calls=0,actual_AC_calls=0,new_AC_not_authorized=True,safety_release=False,execution_entry='existing live host.run(execute_native=True), only after separate authorization; no old raw adoption')
