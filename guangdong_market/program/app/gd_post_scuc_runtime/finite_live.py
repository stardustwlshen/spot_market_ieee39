"""Explicit source-bound finite planning, one currently due complete H1..H24; H24 needs registered D1 source.
Install as same package as Controller/LivePhase. No user JSON can mint registration.
"""
from pathlib import Path
from copy import deepcopy
import json,hashlib
from datetime import timedelta
from .planner import FinitePlan,phase_class,fingerprint
from .phase_runtime import clear,stamp,need,digest
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
_REG={}
def bind(controller):
 from .controller import Controller
 from runner import GeneralRollingHost
 h=controller.h
 need(type(controller)is Controller and isinstance(h,GeneralRollingHost)and h.hooks.post_scuc is controller,'registered same-host Controller only')
 need(h.mode=='native'and not h.session.compatibility and h.pending is None,'fresh live host only')
 need(id(controller)not in _REG,'duplicate finite planner registration')
 original=h.session.builder.serialize_model
 state=dict(controller=controller,host=h,session=h.session,original=original,plan=None,case=None,identity=None)
 _REG[id(controller)]=state
 for f in Path(__file__).parent.glob('*.py'):h.session.registry['source_pins'][str(f.resolve())]=sha(f)
 def serialize(model,options):
  current=verified(controller);plan=current['plan']
  if plan is not None:
   need(current['identity']==identity(h),'planning model clock advanced')
   inputs=getattr(model,'_r23',{}).get('inputs');need(isinstance(inputs,dict),'complete thermal builder provenance required')
   expected=current['case']
   need(all(fingerprint(v)==fingerprint(expected[k])for k,v in inputs.items()if k!='agc_awards'),'different nonAGC model in serialize callback')
   actual=inputs['agc_awards'];ticks=set(plan.ticks)
   need(all(actual[g]['qualification']==expected['agc_awards'][g]['qualification'] and all(actual[g][side][t]==expected['agc_awards'][g][side][t]for side in ('up_mw','down_mw')for t in actual[g][side]if t not in ticks)for g in actual),'published/outside model awards changed')
   target={g:{side:[actual[g][side][t]for t in plan.ticks]for side in ('up_mw','down_mw')}for g in actual}
   zero=all(v==0 for z in target.values()for a in z.values()for v in a)
   matching=any(all(v==c['awards'].get(g,0)for g,z in target.items()for a in z.values()for v in a)for c in plan.candidates)
   need(zero or matching,'unregistered actual target allocation')
   plan.attach(model,current['case'])
  return original(model,options)
 h.session.builder.serialize_model=serialize

def identity(h):return (id(h.session),h.branch_id,h.window,str(h.cp),h.lid)
def verified(controller):
 st=_REG.get(id(controller));need(st is not None and st['controller']is controller and st['host']is controller.h and st['session']is controller.h.session,'unknown finite planning registration')
 h=controller.h;h.session.verify_sources();need(h.hooks.post_scuc is controller and h.hooks.carries.get(str(h.cp))==sha(h.cp),'unregistered controller/carry')
 return st

def stage(controller,kw,carry):
 st=verified(controller);h=controller.h;need(carry==read(h.cp),'wrong current carry');cut=stamp(carry['cutoff'])
 due=due_hours(controller.declarations,controller.accepted,h.agc.issued,cut)
 if not due:st.update(plan=None,case=None,identity=None);return None
 need(len(due)==1 and 1<=due[0]<=24,'only one due H1..H24; no implicit H25 extension')
 i=due[0];p,hs,d=controller.declarations[i];need(sha(p)==hs,'registered declaration changed')
 # Same source used by existing lifecycle; uploaded declarations cannot expand
 # qualifications, demand, capacity or parameters beyond its verified input.
 import agc_lifecycle
 src=Path(agc_lifecycle.B)/'agc_runtime/RESEARCH_INPUT.json';need(sha(src)==h.agc.input_sha,'lifecycle qualification source drift');source=read(src);selected_source=src
 if i==24:
  need(getattr(controller,'finite_hourly',False),'H24 finite mode not selected')
  path=getattr(h.agc,'nextday_agc_path',None);need(path is not None,'H24 requires explicit D1 selection');path=Path(path).resolve();need(h.session.registry['source_pins'].get(str(path))==sha(path),'D1 source not host registered')
  from nextday_declaration import validate
  extension=validate(path,cutoff=carry['cutoff']);need(extension['units']==source['units'],'D1 may not invent qualifications');source=extension;selected_source=path
 need(d['units']==source['units']and d['parameters']==source['parameters'],'declaration differs from trusted lifecycle inventory/parameters')
 h.agc.verify();h.session.registry['source_pins'][str(selected_source.resolve())]=sha(selected_source);h.session.registry['source_pins'][str(src.resolve())]=sha(src);h.session.registry['source_pins'][str(Path(p).resolve())]=hs
 before=deepcopy(kw)
 force=i in getattr(controller,'force_finite_validation_hours',())
 plan=FinitePlan(d,before,clear,force_finite_validation=force,issued_end=(h.agc.origin+timedelta(minutes=5*carry['issued_end_exclusive'])).isoformat())
 if plan.saturated:
  st.update(plan=None,case=None,identity=None);return dict(hour=i,saturated_original_path=True,planning_not_award=True)
 # Distinct source-bound planning baseline, never written to scenario/source files.
 for a in kw['agc_awards'].values():
  for side in ('up_mw','down_mw'):
   for t in plan.ticks:a[side][t]=0.
 plan=FinitePlan(d,kw,clear,force_finite_validation=force,issued_end=(h.agc.origin+timedelta(minutes=5*carry['issued_end_exclusive'])).isoformat())
 st.update(plan=plan,case=deepcopy(kw),identity=identity(h),hour=i)
 return dict(hour=i,planning_not_award=True,published=False,candidates=len(plan.candidates),source_path=str(selected_source),source_sha256=sha(selected_source),qualification_source_path=str(src),qualification_source_sha256=sha(src))

def factory(controller,base):
 st=verified(controller);plan=st['plan']
 if plan is None:return base
 need(st['identity']==identity(controller.h),'stale finite phase')
 plan.verify();return phase_class(base,plan)

def due_hours(declarations,accepted,issued,cut):
 pending=[(i,stamp(d['as_of']))for i,(_,_,d)in declarations.items()if i not in accepted and i not in issued]
 need(all(1<=i<=24 for i,t in pending),'unregistered H25+ declaration')
 need(all(t>=cut for i,t in pending),'missed unpublished finite hour; cannot silently skip requirement')
 return [i for i,t in pending if t==cut]
