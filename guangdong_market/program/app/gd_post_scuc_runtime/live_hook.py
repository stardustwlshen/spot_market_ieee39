"""Fresh live post-SCUC phase; no solver, no historical-source promotion.
Consumer payload is a distinct phase, never compatible with the old fixed-matrix verifier.
"""
from pathlib import Path
from copy import deepcopy
import json,hashlib
from .phase_runtime import PostSCUC,need,digest,stamp
from content_hash import sha
read=lambda p:json.loads(Path(p).read_bytes())

class LivePhase:
 def __init__(self,host,work):
  from runner import GeneralRollingHost,GeneralEventHooks
  need(isinstance(host,GeneralRollingHost) and type(host.hooks)is GeneralEventHooks,'full General live host required')
  need(host.mode=='native' and host.pending is not None,'fresh native pending SCUC only')
  need(not getattr(host,'source_kind',None) and not host.session.compatibility,'historical replay has no fresh permission')
  from .safety_composer import PreparedSafetyCaps
  self.safety_caps=PreparedSafetyCaps(host,host.pending)
  self.h=host;self.p=Path(host.pending).resolve();self.work=Path(work).resolve();need(not self.work.exists(),'fresh phase output only');self.work.mkdir()
  self.record=host.hooks.prepared[str(self.p)];self.identity=(id(host.session),host.branch_id,host.window,str(host.cp),host.lid)
  self.pins=dict(self.record['artifact_pins']);self.pins[self.record['carry_path']]=self.record['carry_sha256'];self.count=0;self.registered={};self.candidate=None;self.planning_basis={};self.issued_snapshot=digest(host.agc.issued)
  self.verify_source();self.base,self.names,self.case=self._build();self._build_cache={digest(self.case):(deepcopy(self.base),deepcopy(self.names))}
  source=read(self.p/'template.json');need(self.base==source and self.names==read(self.p/'names.json'),'complete same-awards General reconstruction mismatch')
  from .finite_live import factory
  ctrl=host.hooks.post_scuc
  phase_type=factory(ctrl,PostSCUC) if getattr(ctrl,'finite_planning',False) else PostSCUC
  self.phase=phase_type.from_native(program=host.session.program,prepared=self.p,case=self.case,builder=self._rebuild,branch_id=host.branch_id,domain_start=self.case['contract']['domain_start'],internal_completed_at=self.case['contract']['cutoff'])
  for name in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz'):
   f=self.p.parent/'actual/SCUC'/name;self.pins[str(f)]=sha(f)
 def verify_source(self):
  h=self.h;h.session.verify_sources();self.safety_caps.verify();need(digest(h.agc.issued)==self.issued_snapshot,'issued hourly receipt changed during pending phase');need(self.identity==(id(h.session),h.branch_id,h.window,str(h.cp),h.lid)and h.pending==self.p,'live source advanced or replaced')
  for p,v in self.pins.items():need(sha(p)==v,'source artifact drift')
 def _build(self,awards=None):
  self.verify_source();h=self.h;import thermal_physical
  original=thermal_physical.build_free_scuc;serialize=h.session.builder.serialize_model;captures=[]
  def thermal(**kw):
   h.agc.apply_before_scuc(kw,read(h.cp))
   if awards is not None:kw['agc_awards']=deepcopy(awards)
   captures.append(deepcopy(kw));return original(**kw)
  def final(model,options):
   if hasattr(h,'stop_permissions'):h.stop_permissions.apply(model)
   self.safety_caps.apply(model)
   return serialize(model,options)
  thermal_physical.build_free_scuc=thermal;h.session.builder.serialize_model=final
  self.count+=1;out=self.work/f'full_build_{self.count}'
  try:h.hooks.build(h.cp,out,ledger_id=h.lid)
  finally:thermal_physical.build_free_scuc=original;h.session.builder.serialize_model=serialize
  need(len(captures)==1,'single complete builder invocation');self.verify_source();return read(out/'template.json'),read(out/'names.json'),captures[0]
 def _rebuild(self,case):
  need({k:v for k,v in case.items()if k!='agc_awards'}=={k:v for k,v in self.case.items()if k!='agc_awards'},'nonAGC build context changed')
  self.verify_source();key=digest(case)
  if key in self._build_cache:return deepcopy(self._build_cache[key])
  q,n,c=self._build(case['agc_awards']);need(c==case,'full builder input mismatch');self._build_cache[key]=(deepcopy(q),deepcopy(n));return q,n
 def register_research_declaration(self,path,*,confirmed=False):
  self.verify_source();need(confirmed is True,'explicit research declaration confirmation required')
  f=Path(path).resolve();data=read(f);need(data.get('hour_start') and data.get('as_of'),'explicit internal clocks required')
  cutoff=self.case['contract']['cutoff'];need(stamp(data['as_of'])==stamp(cutoff),'future/backdated declaration')
  hour=data['hour_start'];need(hour not in self.registered,'published declaration cannot be overwritten')
  incoming=read(self.h.cp);origin=stamp(incoming['origin']);first=int((stamp(hour)-origin).total_seconds()/300)
  need(first>=incoming.get('issued_end_exclusive',incoming['delivery_tick']),'already issued prefix protected')
  # Existing lifecycle issues integer hour indices; cannot replace any prior award.
  offset=(stamp(hour)-self.h.agc.origin).total_seconds();need(offset>=0 and offset%3600==0,'aligned nonnegative market hour');index=int(offset/3600)
  need(index not in self.h.agc.issued,'already published hourly receipt protected')
  begin=stamp(self.case['contract']['domain_start']);start=stamp(hour);ticks=[t for t in self.case['energy_inputs']['horizon'].ids if start<=begin+__import__('datetime').timedelta(minutes=5*t)<start+__import__('datetime').timedelta(hours=1)]
  nonzero=any(a[side][t]!=0 for a in self.case['agc_awards'].values()for side in ('up_mw','down_mw')for t in ticks)
  if nonzero:
   controller=getattr(self.h.hooks,'post_scuc',None);need(controller is not None,'unpublished nonzero target lacks planning controller');self.planning_basis=controller.verify_planning(index,self.case)
   self.pins.update(self.planning_basis['source_pins'])
  token=digest(dict(branch=self.h.branch_id,source=self.phase.binding,path=str(f),sha256=sha(f),data=data))
  self.pins[str(f)]=sha(f);self.registered[hour]=(token,str(f),sha(f),deepcopy(data));return dict(token=token,hour_start=hour,mode='research_internal_post_SCUC',formal=False)
 def prepare(self,token):
  self.verify_source();need(self.candidate is None,'one immutable phase per SCUC')
  rows=[r for r in self.registered.values()if r[0]==token];need(len(rows)==1,'unregistered declaration')
  _,p,h,data=rows[0];need(sha(p)==h,'declaration changed')
  self.candidate=self.phase.prepare(data,expected_source=self.phase.binding,stage_clock=self.case['contract']['cutoff'],price_policy='DA_band_intersect_current_AGC_research');self.phase.verify(self.candidate)
  return deepcopy(self.candidate)
 def validate_sced(self):
  self.verify_source();need(self.candidate is not None,'phase not prepared');self.phase.verify(self.candidate)
  program=self.h.session.program;q=deepcopy(self.candidate['model']);n=self.candidate['names']
  q['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='simplex',run_crossover='off',ipm_optimality_tolerance=1e-12)
  actual,raw=program.stage(self.p.parent,'SCED',q,n,False)
  for k in ('variables','rows','objective'):need(actual[k]==q[k],'SCED differs from full phase model '+k)
  audit=program.checks(actual,raw,n,False)
  from v6_host import e
  certificate=e.dual_adapter().metadata(actual,raw,n)
  return dict(schema='gd.post_scuc.live_phase.v1',branch_id=self.h.branch_id,source=self.phase.binding,phase_proof=deepcopy(self.candidate['proof']),safety_caps=self.safety_caps.binding(),full_builder_calls=self.count,unpublished_planning_basis=deepcopy(self.planning_basis),AGC_semantics=self.consumer_semantics(),receipt=deepcopy(self.candidate['receipt']),effective_case=deepcopy(self.candidate['case']),SCED_request=actual,SCED_response=raw,names=n,SCED_audit=audit,certificate=certificate,consumer_rule='must independently reconstruct this phase; legacy fixed-matrix verifier must reject',formal=False,safety_release=False)

 def consumer_semantics(self):
  controller=getattr(self.h.hooks,'post_scuc',None);z=deepcopy(controller.forecast_context)if controller else{}
  if self.candidate is not None:
   offset=(stamp(self.candidate['receipt']['hour_start'])-self.h.agc.origin).total_seconds();z[int(offset/3600)]=dict(kind='mathematical_post_SCUC_candidate_NOT_consumable',published=False,receipt_sha256=digest(self.candidate['receipt']),formal=False)
  return z
