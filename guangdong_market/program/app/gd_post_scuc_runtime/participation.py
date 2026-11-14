"""Research-only post-SCUC participation. Does not qualify resources or plan SCUC."""
from copy import deepcopy
from collections import Counter
from datetime import datetime
import math

def need(x,message):
 if not x:raise ValueError(message)
def allocate(data,case,values,ticks,clear):
 need(data.get('mode')=='research','formal mode unsupported; overshoot convention not certified official')
 need(len(ticks)==12,'whole hour required');thermal=case['energy_inputs']['thermal'];eligible=[];excluded=[];ids=set();now=datetime.fromisoformat(data['as_of'])
 for unit in data['units']:
  g=unit['resource_id'];need(g not in ids,'duplicate resource');ids.add(g)
  need(unit['kind']=='thermal'and g in thermal and unit['region']=='Guangdong','only declared Guangdong thermal resources')
  need(unit['qualified']is True and bool(unit['qualification_evidence'])and unit['submitted']is True,'explicit qualification/submission required')
  need(datetime.fromisoformat(unit['available_at'])<=now,'future qualification')
  need(case['agc_awards'][g]['qualification']=='explicit_synthetic_qualified','consumer qualification absent')
  need(all(thermal[g]['max_mw'][t]==unit['capacity_excluding_outage_mw']for t in ticks),'source capacity mismatch')
  stable=[values[f'stable[{g},{t}]']for t in ticks];need(all(math.isfinite(v)and min(abs(v),abs(v-1))<=1e-8 for v in stable),'invalid stable integer evidence')
  if all(abs(v-1)<=1e-8 for v in stable):
   u=deepcopy(unit);u['stable_online']=True;eligible.append(u)
  else:excluded.append(dict(resource_id=g,reason='qualified_but_not_stable_entire_hour'))
 need(eligible,'no stable qualified participant')
 selected=deepcopy(data);selected['units']=eligible
 safety=data.get('safety_excluded',[]);need(isinstance(safety,list)and set(safety)<=ids,'unknown safety exclusion')
 selected['safety_excluded']=[g for g in safety if g in {u['resource_id']for u in eligible}]
 result=clear(selected);d=data['parameters']['provincial_procurement_target_mw']['value'];zone=data['parameters']['distribution_zone_demand_mw']['value'];awards=result['awards_mw'];byid={u['resource_id']:u for u in eligible}
 need(math.isfinite(d)and d>0 and result['unmet_requirement_mw']<=1e-9 and result['price']is not None,'insufficient/unpriced declared requirement')
 need(set(awards)<=set(byid)-set(safety),'allocation includes ineligible/excluded resource')
 counts=Counter(byid[g]['plant_id']for g in awards)
 for g,v in awards.items():
  u=byid[g];cap=min(u['declared_ratio']*u['capacity_excluding_outage_mw'],.2*zone/counts[u['plant_id']])
  need(math.isfinite(v)and v>=0 and abs(v-cap)<=1e-9,'allocator capacity/same-plant constraint mismatch')
 total=math.fsum(awards.values());need(total+1e-9>=d,'allocation below requirement')
 return result,dict(eligible_resource_ids=[u['resource_id']for u in eligible],excluded=excluded,requirement_mw=d,allocated_mw=total,excess_mw=max(0.,total-d),allocation_policy='existing_ranked_full_capacity_stop_at_or_above_demand_RESEARCH',official_overshoot_certified=False,SCUC_allocatability_planning_added=False,formal=False)
