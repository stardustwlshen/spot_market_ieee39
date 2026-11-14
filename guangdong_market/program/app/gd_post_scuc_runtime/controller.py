"""Opt-in fresh research runner hook. No implicit native authorization."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
from .phase_runtime import need,stamp,digest
from .live_hook import LivePhase,read,sha
from . import consumers
class Controller:
 def __init__(self,host,declarations,*,research=False,saturated_planning=False,day_calendar=None,finite_planning=False,finite_h1=False,finite_hourly=False):
  from runner import GeneralRollingHost
  need(isinstance(host,GeneralRollingHost)and host.mode=='native'and host.pending is None and not host.session.compatibility and research is True,'fresh explicit research host only')
  need(type(saturated_planning)is bool,'explicit planning mode boolean');declarations=tuple(declarations);self.saturated_planning=saturated_planning;self.planning_tokens={};self.planning_evidence={};self.forecast_context={}
  self.scope=preflight(host.session.run,declarations,saturated_planning=saturated_planning);self.h=host;self.declarations={};self.accepted={};self.candidates={};self._receipt_commit_grant=object();self.original_produce=host.agc.produce
  for path in declarations:
   p=Path(path).resolve();d=read(p);hour=stamp(d['hour_start']);need(stamp(d['as_of'])==hour-timedelta(minutes=30),'T-30 research declaration required');offset=(hour-host.agc.origin).total_seconds();need(offset>=0 and offset%3600==0,'aligned nonnegative market hour');i=int(offset/3600);need(i not in host.agc.issued and i not in self.declarations,'already published or duplicate declaration');self.declarations[i]=(p,sha(p),d)
  need(self.declarations,'explicit declaration inventory required')
  for p in Path(__file__).parent.rglob('*.py'):host.session.registry['source_pins'][str(p.resolve())]=sha(p)
  host.hooks.post_scuc=self;host.agc.produce=self.produce;host.agc.apply_before_scuc=self.apply
  consumers.install(host.session.program,__import__('v6_host').e)
  need(type(finite_h1)is bool and type(finite_hourly)is bool,'explicit finite mode booleans');need(not(finite_h1 and finite_hourly),'choose H1 or hourly mode');need(not finite_hourly or(day_calendar is not None and set(self.declarations)==set(range(1,25))),'hourly requires complete source-bound H1..H24 calendar');self.finite_hourly=finite_hourly;self.force_finite_validation_hours=tuple(range(1,25))if finite_hourly else((1,)if finite_h1 else ())
  finite_planning=finite_planning or bool(self.force_finite_validation_hours)
  self.finite_planning=finite_planning;need(type(finite_planning)is bool,'explicit finite planning boolean')
  if finite_planning:
   from .finite_live import bind
   bind(self)
  if day_calendar is not None:
   from .day_calendar import bind
   bind(self,day_calendar)
 def produce(self,hour,*,cutoff):
  if hour in self.declarations and hour not in self.h.agc.issued:return None # unpublished: SCUC phase will clear, never fabricate a receipt
  return self.original_produce(hour,cutoff=cutoff)
 def apply(self,kw,carry):
  # Prior published lifecycle receipts retain their own source/timestamps. No metadata
  # rewrite of those receipts; only per-cell award values are composed.
  h=self.h;h.agc.verify();cut=stamp(carry['cutoff']);begin=stamp(kw['contract']['domain_start']);applied=[]
  for i in range(25):
   start=h.agc.origin+timedelta(hours=i)
   if start+timedelta(hours=1)>begin and start<begin+timedelta(hours=4) and start-timedelta(minutes=30)<=cut:
    need(i in h.agc.issued or i in self.accepted or i in self.declarations,'due unpublished hour has no registered declaration; no implicit awards')
  for i,r in h.agc.issued.items():
   r=self.original_produce(i,cutoff=cut.isoformat());start=h.agc.origin+timedelta(hours=i);awards=r['result']['awards_mw']
   for t in kw['energy_inputs']['horizon'].ids:
    if start<=begin+timedelta(minutes=5*t)<start+timedelta(hours=1):
     for g,a in kw['agc_awards'].items():
      for side in ('up_mw','down_mw'):a[side][t]=awards.get(g,0)
   applied.append(digest(r))
  for i,record in self.accepted.items():
   z=consumers.verified_state(record);r=z['receipt'];start=stamp(r['hour_start']);need(stamp(r['published_at'])<=cut,'future receipt')
   for t in kw['energy_inputs']['horizon'].ids:
    if start<=begin+timedelta(minutes=5*t)<stamp(r['hour_end']):
     for g,a in kw['agc_awards'].items():
      for side in ('up_mw','down_mw'):a[side][t]=r['allocator']['awards_mw'].get(g,0)
   applied.append(digest(r))
  finite_evidence=None
  if self.finite_planning:
   from .finite_live import stage
   finite_evidence=stage(self,kw,carry)
  # A pending declaration is not consumed as an award in SCUC. Existing seed
  # baseline must explicitly be zero for its unpublished target; do not silently
  # interpret an inherited nonzero future award as unissued.
  for i,(p,hs,d)in self.declarations.items():
   need(sha(p)==hs,'declaration drift')
   if i in self.accepted:continue
   start=stamp(d['hour_start'])
   ticks=[t for t in kw['energy_inputs']['horizon'].ids if start<=begin+timedelta(minutes=5*t)<start+timedelta(hours=1)]
   zero=all(a[side][t]==0 for a in kw['agc_awards'].values()for side in ('up_mw','down_mw')for t in ticks)
   if ticks and not zero and len(ticks)==12:self.verify_planning(i,kw)
   # Partial future hours remain source-bound forecast, not a whole-hour award.
  self.forecast_context={}
  end_hour=int((begin+timedelta(hours=4)-h.agc.origin).total_seconds()//3600)
  for i in range(end_hour+1):
   start=h.agc.origin+timedelta(hours=i);ticks=[t for t in kw['energy_inputs']['horizon'].ids if start<=begin+timedelta(minutes=5*t)<start+timedelta(hours=1)]
   if not ticks:continue
   if i in h.agc.issued or i in self.accepted:
    self.forecast_context[i]=dict(kind='previously_issued_research_receipt',published=True);continue
   nonzero=any(a[side][t]!=0 for a in kw['agc_awards'].values()for side in ('up_mw','down_mw')for t in ticks)
   if not nonzero:self.forecast_context[i]=dict(kind='zero_unpublished_forecast',published=False,declared_requirement_mw=self.declarations[i][2]['parameters']['provincial_procurement_target_mw']['value'] if i in self.declarations else None,forecast_is_not_requirement=True,renewal_authorized=False);continue
   need(i<=24,'unsupported nonzero H25+ forecast; no automatic renewal')
   need(self.saturated_planning,'nonzero future forecast requires explicit planning mode')
   from .planning import SaturatedPlanning
   reservation=SaturatedPlanning(h).authorize(i)
   if len(ticks)==12:
    evidence=reservation.verify(h,i,case=kw);self.forecast_context[i]=dict(kind='saturated_planning_forecast_NOT_award',published=False,evidence=evidence)
   else:
    # No full-hour model proof for a truncated lookahead. Keep original numerical
    # forecast without pretending these cells certify an entire future hour.
    self.forecast_context[i]=dict(kind='retained_partial_hour_forecast_NOT_award',published=False,whole_hour_model_verified=False,local_ticks=ticks,source_pins=dict(reservation.pins))
  if finite_evidence is not None:
   hour=finite_evidence['hour'];self.forecast_context.setdefault(hour,{}).update(finite_planning=finite_evidence)
  self.last_case=deepcopy(kw)
  return kw,dict(mode='fresh_post_SCUC_existing_receipts_only',applied_receipt_hashes=applied,unpublished_planning=deepcopy(self.planning_evidence),forecast_semantics=deepcopy(self.forecast_context),planning_is_not_published_award=True,formal=False,native_calls=0)
 def verify_planning(self,hour,case):
  need(self.saturated_planning,'nonzero unpublished baseline requires explicit saturated-planning mode')
  need(hour in self.declarations and hour not in self.accepted and hour not in self.h.agc.issued,'unpublished registered target required')
  from .planning import SaturatedPlanning,PlanningReservation
  token=SaturatedPlanning(self.h).authorize(hour)
  need(type(token)is PlanningReservation,'unknown planning token type')
  proof=token.verify(self.h,hour,case=case)
  need(proof['planning_not_award'] is True and proof['requirement_mw']==50 and set(proof['planning_capacity_mw'].values())=={10} and len(proof['planning_capacity_mw'])==5,'unsupported planning capacity')
  self.planning_tokens[hour]=token;self.planning_evidence[hour]=proof;return deepcopy(proof)
 def after_scuc(self,prepared):
  cut=stamp(read(self.h.cp)['cutoff']);pending=[(i,stamp(d['as_of']))for i,(_,_,d)in self.declarations.items()if i not in self.accepted];need(all(t>=cut for i,t in pending),'missed unpublished hour: cannot silently skip demand');due=[i for i,t in pending if t==cut];need(len(due)<=1,'multiple due declarations')
  if not due:
   consumers.register_context(self,prepared,self.last_case);return None
  i=due[0];p,hs,_=self.declarations[i];need(sha(p)==hs,'declaration changed');phase=LivePhase(self.h,Path(prepared).parent/'post_scuc_phase');token=phase.register_research_declaration(p,confirmed=True)['token'];phase.prepare(token);phase.target_hour=i;return phase
 def accepted_phase(self,phase):
  # Numerical registration is needed by the pair verifier, NOT publication.
  from .receipt_commit import stage
  return stage(self,phase)
 def commit_after_handoff(self,p,actual,decision,nxt,*,grant):
  from .receipt_commit import commit
  return commit(self,p,actual,decision,nxt,grant=grant)

def preflight(run,declarations,*,saturated_planning=False):
 """Scope check before any SCUC call, not source authentication or feasibility."""
 run=Path(run).resolve();seed=run/'seed';request=read(seed/'request.json');origin=stamp(request['origin']);awards=read(seed/'agc.json');seen=set();hours=[]
 for path in declarations:
  p=Path(path).resolve();d=read(p);start=stamp(d['hour_start']);offset=(start-origin).total_seconds();need(offset>=0 and offset%3600==0,'aligned nonnegative market hour');i=int(offset/3600);need(i not in seen,'duplicate hour');seen.add(i)
  need(stamp(d['as_of'])==start-timedelta(minutes=30),'T-30 required');ticks=range(12*i,12*i+12)
  zero=all(str(t)in a[side]and a[side][str(t)]==0 for a in awards.values()for side in ('up_mw','down_mw')for t in ticks)
  baseline='explicit_zero_unpublished_target';planning=None
  if not zero:
   need(saturated_planning is True,'unsupported nonzero unpublished target; explicit planning mode required')
   from .planning import algebra
   import agc_lifecycle
   data=read(Path(agc_lifecycle.B)/'agc_runtime/RESEARCH_INPUT.json')
   # For D1 this input may be outside validity: require the declaration itself to
   # pass arithmetic preflight; live authorization separately requires host pins.
   if i==24:data=d
   planning=algebra(data,hour_start=start.isoformat(),cutoff=d['as_of'])
   need(set(awards)==set(planning['planning_capacity_mw']),'planning inventory mismatch')
   need(all(str(t)in a[side]and a[side][str(t)]==10 for a in awards.values()for side in ('up_mw','down_mw')for t in ticks),'only exact five-by-ten planning baseline supported')
   baseline='saturated_five_by_ten_planning_NOT_award'
  hours.append(dict(hour=i,path=str(p),sha256=sha(p),baseline=baseline,arithmetic_preflight_only=planning))
 need(hours,'declarations required');return dict(mode='saturated_planning_post_SCUC_research'if saturated_planning else'zero_baseline_post_SCUC_research',hours=hours,default50_scope_candidate=saturated_planning,live_planning_authorization=False,native_calls=0,formal=False)

def enable_hourly(controller):
 """Explicit pre-build activation for a fresh program-level study host."""
 need(type(controller)is Controller and controller.h.hooks.post_scuc is controller,'same registered Controller required')
 h=controller.h;need(h.window==0 and h.pending is None and not h.accepted and not controller.accepted,'enable before any candidate or imported initial condition')
 need(not controller.finite_planning,'finite planner already bound')
 need(getattr(controller,'calendar_inventory',None)is not None and set(controller.declarations)==set(range(1,25)),'complete registered day inventory required')
 controller.finite_hourly=True;controller.finite_planning=True;controller.force_finite_validation_hours=tuple(range(1,25))
 from .finite_live import bind
 bind(controller)
