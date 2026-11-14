"""Bounded research SCUC allocatability. Pure model layer, NOT dispatch authority.
One full unpublished hour, <=8 declared thermal resources; exact existing allocator.
Caller retains same-host source/qualification/declaration registry gates.
"""
from copy import deepcopy
from itertools import product
from datetime import datetime,timedelta
import hashlib,json
from .participation import allocate,need

def fingerprint(z):
 from dataclasses import is_dataclass,asdict
 def pack(v):
  if isinstance(v,bytes):return {"bytes_hex":v.hex()}
  if is_dataclass(v):return pack(asdict(v))
  if isinstance(v,dict):return sorted([(repr(k),pack(x))for k,x in v.items()])
  if isinstance(v,(tuple,list)):return [pack(x)for x in v]
  return v
 return hashlib.sha256(json.dumps(pack(z),sort_keys=True,allow_nan=False).encode()).hexdigest()

class FinitePlan:
 def __init__(self,data,case,clear,*,issued_end,already_published=False,force_finite_validation=False):
  need(not already_published,'published award cannot be replanned')
  need(data['mode']=='research','research only')
  units=[u['resource_id']for u in data['units']];need(1<=len(units)<=8 and len(set(units))==len(units),'unique thermal inventory limit8')
  start=datetime.fromisoformat(data['hour_start']);cut=datetime.fromisoformat(case['contract']['cutoff']);begin=datetime.fromisoformat(case['contract']['domain_start']);end=start+timedelta(hours=1)
  need(datetime.fromisoformat(data['as_of'])==cut<=start-timedelta(minutes=30),'known model-cutoff declaration only')
  need(start>=datetime.fromisoformat(issued_end),'cannot change issued prefix')
  need(all(datetime.fromisoformat(p['available_at'])<=cut and datetime.fromisoformat(p['effective_from'])<=start and end<=datetime.fromisoformat(p['effective_to'])for p in data['parameters'].values()),'future/expired hourly demand, no implicit nextday extension')
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'original400 preserved')
  ticks=[t for t in case['energy_inputs']['horizon'].ids if start<=begin+timedelta(minutes=5*t)<end];need(len(ticks)==12,'complete target hour inside model')
  self.data=deepcopy(data);self.units=units;self.ticks=ticks;self.clear=clear;self.case_digest=fingerprint(case);self.candidates=[]
  for mask in product((0,1),repeat=len(units)):
   values={f'stable[{g},{t}]':float(bit)for g,bit in zip(units,mask)for t in ticks}
   try:result,participation=allocate(data,case,values,ticks,clear)
   except ValueError as ex:
    if str(ex)in ('no stable qualified participant','insufficient/unpriced declared requirement'):continue
    raise
   awards=result['awards_mw']
   if any(2*a>case['energy_inputs']['thermal'][g]['max_mw'][t]-case['energy_inputs']['thermal'][g]['min_mw'][t]for g,a in awards.items()for t in ticks):continue
   self.candidates.append(dict(mask=mask,awards=awards,result=result,participation=participation))
  need(self.candidates,'no allocatable candidate; never reduce demand')
  self.saturated=(len(units)==5 and len(self.candidates)==1 and self.candidates[0]['mask']==(1,)*5 and set(self.candidates[0]['awards'].values())=={10} and data['parameters']['provincial_procurement_target_mw']['value']==50 and len({u['plant_id']for u in data['units']})==5 and set(units)==set(case['energy_inputs']['thermal']) and all(case['agc_awards'][g][side][t]==10 for g in units for side in ('up_mw','down_mw')for t in ticks))
  need(type(force_finite_validation)is bool,'explicit validation switch')
  self.saturated=self.saturated and not force_finite_validation
  self._seal=fingerprint((self.data,self.units,self.ticks,self.candidates,self.saturated,self.case_digest))
 def verify(self):need(self._seal==fingerprint((self.data,self.units,self.ticks,self.candidates,self.saturated,self.case_digest)),'planning table mutated')
 def verify_case(self,case):
  self.verify();need(fingerprint(case)==self.case_digest,'plan built for different full model case')
 def check_result(self,values,result):
  self.verify()
  mask=tuple(int(all(abs(values[f'stable[{g},{t}]']-1)<=1e-8 for t in self.ticks))for g in self.units)
  matches=[(j,c)for j,c in enumerate(self.candidates)if c['mask']==mask];need(len(matches)==1,'SCUC has no registered allocatable mask');j,c=matches[0]
  need(result==c['result'],'actual post-SCUC allocator differs from planning candidate')
  if not self.saturated:
   need(all(abs(values[f'agc_plan_choice[{k}]']-int(k==j))<=1e-8 for k in range(len(self.candidates))),'SCUC planning selection not actual stable mask')
  return dict(candidate=j,planning_not_award=True,actual_allocator_match=True)

 def build(self,original_builder,case):
  self.verify_case(case)
  if self.saturated:return original_builder(**case) # exact original byte/math path
  import pyomo.environ as p
  baseline=deepcopy(case)
  for g,a in baseline['agc_awards'].items():
   for side in ('up_mw','down_mw'):
    for t in self.ticks:a[side][t]=0.
  m=original_builder(**baseline)
  return self.attach(m,case)

 def attach(self,m,case):
  self.verify_case(case)
  import pyomo.environ as p
  if self.saturated:return m
  need(not hasattr(m,'agc_plan_choice'),'duplicate planning attachment')
  m.agc_plan_choice=p.Var(range(len(self.candidates)),domain=p.Binary)
  m.agc_plan_rows=p.ConstraintList();m.agc_plan_rows.add(sum(m.agc_plan_choice[j]for j in range(len(self.candidates)))==1)
  for gi,g in enumerate(self.units):
   member=sum(c['mask'][gi]*m.agc_plan_choice[j]for j,c in enumerate(self.candidates))
   for t in self.ticks:m.agc_plan_rows.add(m.stable[g,t]>=member)
   m.agc_plan_rows.add(sum(m.stable[g,t]for t in self.ticks)<=11+member)
  for g in m.G:
   a=sum(c['awards'].get(g,0)*m.agc_plan_choice[j]for j,c in enumerate(self.candidates));d=case['energy_inputs']['thermal'][g]
   for t in self.ticks:
    lo=d['min_mw'][t]*m.stable[g,t]+m.transition_mean[g,t]+a
    hi=d['max_mw'][t]*m.stable[g,t]+m.transition_mean[g,t]-a
    m.free_lower[g,t].set_value(m.power[g,t]>=lo);m.free_upper[g,t].set_value(m.power[g,t]<=hi)
    # Existing reserve/primary/contingency constraints reference these named
    # expressions, so the same reserve headroom is deducted exactly once.
    m.online_min[g,t].set_value(lo);m.online_max[g,t].set_value(hi)
  return m

class PlannedBuilder:
 """Adapter for the existing full-builder callback; not a live source capability.
 The registered General caller supplies the original builder and serializer.
 """
 def __init__(self,plan,original_builder,case,serialize):
  plan.verify_case(case);self.plan=plan;self.original=original_builder;self.case=deepcopy(case);self.serialize=serialize
 def __call__(self,case):
  need({k:v for k,v in case.items()if k!='agc_awards'}=={k:v for k,v in self.case.items()if k!='agc_awards'},'nonAGC physical case changed')
  if case['agc_awards']!=self.case['agc_awards']:
   ticks=set(self.plan.ticks)
   for g,a in case['agc_awards'].items():
    old=self.case['agc_awards'][g];need(a['qualification']==old['qualification'],'qualification changed')
    for side in ('up_mw','down_mw'):
     need(all(a[side][t]==old[side][t]for t in a[side]if t not in ticks),'published/outside planning hour changed')
   matches=[c for c in self.plan.candidates if all(case['agc_awards'][g][side][t]==c['awards'].get(g,0)for g in case['agc_awards']for side in ('up_mw','down_mw')for t in ticks)]
   need(matches,'post award not a registered allocator candidate')
  # Rebuild complete model; its planning-choice integer remains present and is
  # fixed by the existing phase's full SCUC integer fixation, never reoptimized.
  return self.serialize(self.plan.build(self.original,self.case))

def phase_class(base_phase,plan):
 """Bind to participation-enabled PostSCUC, preserving its original acceptance.
 Caller uses ordinary from_native full-builder verification, never creates raw.
 """
 class PlannedPhase(base_phase):
  def prepare(self,*args,**kwargs):
   candidate=super().prepare(*args,**kwargs)
   evidence=plan.check_result(dict(zip(self.n['variables'],self.x)),candidate['receipt']['allocator'])
   candidate['planning_selection_check']=evidence
   return candidate
 return PlannedPhase
