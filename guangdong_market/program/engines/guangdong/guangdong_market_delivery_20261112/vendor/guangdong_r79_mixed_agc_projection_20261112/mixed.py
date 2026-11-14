"""Mixed thermal/independent-storage local research procurement; no native."""
from pathlib import Path
import sys,importlib.util,json,argparse,hashlib
from decimal import Decimal,localcontext
from collections import Counter
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'guangdong_r73_agc_parameter_interface_20261112'))
import adapter as thermal
spec=importlib.util.spec_from_file_location('r79_storage_source',ROOT/'guangdong_r78_storage_agc_competition_20261112/storage.py');storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)
def need(v,m):
 if not v:raise ValueError(m)
def D(v):return Decimal(str(v))
def run(d):
 with localcontext()as ctx:
  ctx.prec=60
  return calculate(d)
def calculate(d):
 pins=json.loads((Path(__file__).resolve().parent/'SOURCE_PINS.json').read_bytes())
 for name,h in pins.items():need(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,'frozen upstream changed '+name)
 need(d.get('schema')=='gd.agc.mixed.r79.v1'and d.get('mode')=='research','research schema required')
 t=d['thermal'];s=d['storage'];binding=d['common_binding']
 # Both upstream gates are executed intact. An incomplete Fm population never becomes a partial candidate list.
 tr=thermal.run(t);sr=storage.run(s)
 need(sr['status']=='research_candidates_only','entire storage competition group unresolved: '+sr['status'])
 need(t['hour_start']==s['hour_start']and t['as_of']==s['as_of'],'same hour/as_of required')
 need(binding['zone']==s['zone']and isinstance(binding['zone'],str),'same explicit zone required')
 sp=s['parameters'];tp=t['parameters'];universe=s['performance_universe'];pop=s['population']
 # The shared values alone are insufficient; exact source id/source/grade/time declarations are bound too.
 def matches(param,evidence,value):
  need(D(param['value'])==D(value),'shared parameter mismatch')
  need(param['source']==evidence['source']and {'research_assumption':'research','retained_reposted_scan':'retained_reposted_scan','caller_supplied_publication_unverified':'caller_supplied_unverified'}.get(param['source_grade'])==evidence['source_grade']and param['available_at']==evidence['as_of'],'shared parameter provenance mismatch')
  need(binding['source_ids'][param['version']]==evidence['id'],'explicit shared source id mapping required')
 matches(tp['distribution_zone_demand_mw'],pop['evidence'],pop['demand_mw'])
 matches(tp['full_control_area_kmax'],universe['evidence'],universe['kmax'])
 matches(tp['bid_floor'],sp['evidence'],sp['values']['bid_floor'])
 need(tp['bid_floor']['effective_from']==sp['effective_from']and tp['bid_floor']['effective_to']==sp['effective_to'],'floor validity mismatch')
 need(D(sp['values']['bid_upper'])==15,'thermal R73 upper15 only; heterogeneous bid upper unsupported')
 need(d.get('floor_crossing_policy')=='exclude_crossing_unit'and d.get('exact_tie_policy')=='resource_id_research','explicit research ambiguity policies required')
 cap=D(d['candidate_price_cap']);need(cap==15,'only retained scanned cap15 research configuration supported')
 target=D(tp['provincial_procurement_target_mw']['value']);floor=D(tp['local_reserved_floor_mw']['value']);zone=D(pop['demand_mw']);km=D(universe['kmax'])
 thermal_by={u['resource_id']:u for u in t['units']};storage_by={u['resource_id']:u for u in pop['offers']};need(not set(thermal_by)&set(storage_by),'duplicate cross-technology id')
 need(all(u.get('zone')==binding['zone']for u in t['units']),'thermal resource zone missing/mismatch')
 candidates=[]
 for rid in tr['ranked_ids']:
  u=thermal_by[rid];need(D(u['k1'])<=D(sp['values']['KI_upper']),'thermal k1 exceeds common KI_upper');candidates.append(dict(resource_id=rid,kind='thermal',plant_id=u['plant_id'],P=D(u['k'])/km,k1=D(u['k1']),sorting_price=D(u['bid'])/(D(u['k'])/km),declared_mw=D(u['declared_ratio'])*D(u['capacity_excluding_outage_mw'])))
 for r in sr['candidates']:
  u=storage_by[r['resource_id']];need('k1'in u,'storage market k1 required separately from internal score')
  k1=storage.dec(u['k1']);need(k1<=D(sp['values']['KI_upper']),'storage k1 exceeds common KI_upper');candidates.append(dict(resource_id=r['resource_id'],kind='independent_storage_third_party',P=r['P'],k1=k1,sorting_price=r['sorting_price'],declared_mw=r['q'],Fm=r['Fm']))
 candidates.sort(key=lambda r:(r['sorting_price'],-r['P'],-r['k1'],r['resource_id']))
 selected=[];awards={};trace=[]
 for r in candidates:
  selected.append(r);counts=Counter(v['plant_id']for v in selected if v['kind']=='thermal')
  awards={v['resource_id']:(min(v['declared_mw'],D('.2')*zone/counts[v['plant_id']])if v['kind']=='thermal'else v['declared_mw'])for v in selected}
  trace.append(dict(added=r['resource_id'],awards_mw=dict(awards),total_mw=sum(awards.values(),D(0))))
  if sum(awards.values(),D(0))>=target:break
 total=sum(awards.values(),D(0));unmet=max(D(0),target-total);reserved=[];cumulative=D(0)
 for r in selected:
  if cumulative<floor:reserved.append(r['resource_id'])
  cumulative+=awards[r['resource_id']]
 setters=[r for r in selected if r['resource_id']not in reserved];raw=setters[-1]['sorting_price']if setters else None
 return dict(schema='gd.agc.mixed.r79.result.v1',scope='Guangdong local research procurement candidates only',hour_start=t['hour_start'],zone=binding['zone'],ranked=candidates,awards_mw=awards,trace=trace,non_price_setters=reserved,raw_marginal=raw,candidate_price=None if unmet>0 or raw is None else min(raw,cap),unmet_mw=unmet,previous_hour_price_used=False,storage_competition=sr,thermal_nonparticipants=tr['nonparticipants'],requires_R77_and_joint_energy_safety=True,formal_publication_allowed=False,regional_uniform_price=False,qualification_granted=False,original_AGC50_chain_changed=False,native_calls=0)
def main():
 p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--output');a=p.parse_args()
 try:r=run(json.loads(Path(a.input).read_bytes()))
 except (ValueError,KeyError,TypeError)as e:r=dict(status='missing_or_unsupported',error=str(e),native_calls=0)
 text=json.dumps(r,default=str,ensure_ascii=False,indent=2)+'\n'
 if a.output:
  with open(a.output,'x')as f:f.write(text)
 else:print(text,end='')
 return 2 if r.get('status')=='missing_or_unsupported'else 0
if __name__=='__main__':raise SystemExit(main())
