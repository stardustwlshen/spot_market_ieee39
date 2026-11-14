"""Saturated five-unit planning reservation, NOT an AGC award or allocation.
Exact special-case elimination: 0<=a_g<=10 and sum a_g=50 => all a_g=10.
No changes to original model rows, integers, reserve headroom or objective.
"""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(x,msg):
 if not x:raise ValueError(msg)
def stamp(x):
 d=datetime.fromisoformat(x);need(d.utcoffset()==timedelta(hours=8),'explicit +08 clock');return d
def algebra(data,*,hour_start,cutoff):
 start,now=stamp(hour_start),stamp(cutoff);end=start+timedelta(hours=1)
 need(start.minute==start.second==start.microsecond==0,'whole hour only');need(now<=start-timedelta(minutes=30),'missed postSCUC allocation due time')
 need(data['mode']=='research'and data['scope']=='guangdong_local_research_projection'and data['cross_zone_exchange_assumption']==0,'explicit province-only research profile')
 pars=data['parameters'];units=data['units'];need(len(units)==5 and len({u['resource_id']for u in units})==5,'only exact five-resource case supported')
 need(len({u['plant_id']for u in units})==5 and len({u['zone']for u in units})==1,'five separate plants in one research zone required')
 for p in pars.values():
  need(stamp(p['available_at'])<=now,'future parameter source');need(stamp(p['effective_from'])<=start and end<=stamp(p['effective_to']),'hour outside declared parameter validity; no automatic D1 extension')
 zone=pars['distribution_zone_demand_mw']['value'];target=pars['provincial_procurement_target_mw']['value'];need(zone==target==50,'original50 requirement only');need(pars['local_reserved_floor_mw']['value']==0,'reserved floor changes unsupported')
 caps={}
 for u in units:
  need(stamp(u['available_at'])<=now,'future qualification source');need(u['qualified']is True and u['qualification_evidence']and u['kind']=='thermal'and u['region']=='Guangdong'and u['submitted']is True,'no invented qualification/resource')
  # Same fixed allocator cap formula, distinct plants => count1. No dispatch assumed.
  cap=min(u['declared_ratio']*u['capacity_excluding_outage_mw'],.2*zone)
  need(cap==10,'non-saturated/general planning requires separate model');caps[u['resource_id']]=cap
 need(sum(caps.values())==target,'insufficient/excess planning capacity')
 return dict(schema='gd.agc.saturated_planning.v1',hour_start=start.isoformat(),hour_end=end.isoformat(),known_at=now.isoformat(),planning_capacity_mw=caps,requirement_mw=50,variables_algebraically_eliminated=True,proof='0<=a[g,h]<=10; five units; sum(a)=50 implies a[g,h]=10 for every g. a<=10*stable implies every covered cell stable=1.',planning_not_award=True,actual_allocation_still_required_after_SCUC=True,formal_rule_formula=False,mathematical_scope='only five distinct qualified plants, one zone, exact capacity saturation',published=False,safety_release=False)
_KEY=object()
class PlanningReservation:
 def __init__(self,key,owner,hour,data,pins):
  need(key is _KEY,'private planning capability');self.owner=owner;self.hour=hour;self.data=deepcopy(data);self.pins=dict(pins)
 def verify(self,host,hour,*,case):
  need(host is self.owner.host and hour==self.hour,'planning capability host/hour mismatch');self.owner.verify();h=host
  need(hour not in h.agc.issued,'published award must use existing receipt, not planning')
  for p,v in self.pins.items():need(sha(p)==v,'planning source drift')
  start=h.agc.origin+timedelta(hours=hour);proof=algebra(self.data,hour_start=start.isoformat(),cutoff=read(h.cp)['cutoff']);begin=stamp(case['contract']['domain_start']);n=len(case['energy_inputs']['horizon'].ids)
  need(case['contract']['cutoff']==read(h.cp)['cutoff'],'planning/model cutoff mismatch');ticks=[t for t in range(n)if start<=begin+timedelta(minutes=5*t)<start+timedelta(hours=1)];need(len(ticks)==12,'complete planning hour must be inside model')
  need(start>=stamp(read(h.cp)['origin'])+timedelta(minutes=5*read(h.cp).get('issued_end_exclusive',read(h.cp)['delivery_tick'])),'cannot relabel issued prefix')
  thermal=case['energy_inputs']['thermal'];need(set(thermal)==set(proof['planning_capacity_mw']),'different thermal inventory')
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400/400 must remain')
  for u in self.data['units']:
   g=u['resource_id'];a=case['agc_awards'][g];need(a['qualification']=='explicit_synthetic_qualified','consumer qualification mismatch')
   for t in ticks:
    need(thermal[g]['max_mw'][t]==u['capacity_excluding_outage_mw'],'capacity source mismatch')
    need(a['up_mw'][t]==a['down_mw'][t]==10,'only exact original10 numeric baseline can be authenticated')
  return dict(proof,branch_id=h.branch_id,seed_case_sha256=h.agc.seed_sha,source_pins=dict(self.pins),local_ticks=ticks,baseline_preserved=True,additional_headroom_subtraction=0,model_changed=False,native_calls=0)
class SaturatedPlanning:
 def __init__(self,host):
  from unified import RollingHost
  need(isinstance(host,RollingHost),'registered live host required');self.host=host;self.identity=(id(host.session),host.branch_id,host.window,str(host.cp),host.lid);self.verify()
 def verify(self):
  h=self.host;h.session.verify_sources();need(self.identity==(id(h.session),h.branch_id,h.window,str(h.cp),h.lid),'planning capability clock advanced; mint fresh per-build capability');need(h.hooks.carries.get(str(h.cp))==sha(h.cp),'unregistered planning clock');h.agc.verify()
 def authorize(self,hour):
  self.verify();h=self.host;need(type(hour)is int and 1<=hour<=24 and hour not in h.agc.issued,'only unissued H1..H24 planning; never overwrite receipt')
  import agc_lifecycle
  source=Path(agc_lifecycle.B)/'agc_runtime/RESEARCH_INPUT.json';need(sha(source)==h.agc.input_sha,'fixed research input drift');data=read(source);pins={str(source):sha(source),str(Path(__file__).resolve()):sha(__file__)}
  if hour==24:
   p=getattr(h.agc,'nextday_agc_path',None);need(p is not None,'D1 declaration not selected');p=Path(p).resolve();need(h.session.registry['source_pins'].get(str(p))==sha(p),'D1 declaration not host registered')
   from nextday_declaration import validate
   extension=validate(p,cutoff=read(h.cp)['cutoff']);need(extension['units']==data['units'],'new D1 qualification not permitted');data=extension;pins[str(p)]=sha(p)
  algebra(data,hour_start=(h.agc.origin+timedelta(hours=hour)).isoformat(),cutoff=read(h.cp)['cutoff']);h.session.registry['source_pins'].update(pins);return PlanningReservation(_KEY,self,hour,data,pins)
