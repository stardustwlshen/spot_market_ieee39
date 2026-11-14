"""Early necessary PRICE compatibility check, never an H24 award/qualification receipt."""
from pathlib import Path
from datetime import datetime,timedelta
import math,json

def diagnose(case,physical,*,origin,awards,delta=.1):
 # Caller supplies only the currently supported fixed same-award research plan.
 if set(awards)!=set(case['energy_inputs']['thermal'])or len(awards)!=5 or any(v!=10 for v in awards.values()):raise ValueError('unsupported future awards: cannot infer a price compatibility result')
 rows=[];unknown=[];avg=lambda a:math.fsum(a)/3
 for g,d in case['energy_inputs']['thermal'].items():
  for first in range(288,300,3):
   ts=list(range(first,first+3));cells=[physical[t]['thermal'][g]for t in ts]
   if not all(c.get('phase')=='stable'and c.get('stable')is True for c in cells):
    unknown.append(dict(unit=g,ticks=ts,reason='selected DA not pricing-stable; no future RT eligibility inferred'));continue
   ref=avg([c['mean_mw']for c in cells]);lo=avg([d['min_mw'][t]+c['agc_down_reserved_mw']for t,c in zip(ts,cells)]);hi=avg([d['max_mw'][t]-c['agc_up_reserved_mw']for t,c in zip(ts,cells)])
   curlo=avg([d['min_mw'][t]+awards[g]for t in ts]);curhi=avg([d['max_mw'][t]-awards[g]for t in ts]);intersection=[max(lo,(1-delta)*ref,curlo),min(hi,(1+delta)*ref,curhi)]
   if abs(lo-curlo)>1e-8 or abs(hi-curhi)>1e-8:
    rows.append(dict(unit=g,start=(datetime.fromisoformat(origin)+timedelta(minutes=5*first)).isoformat(),ticks=ts,DA_reference_MW=ref,DA_bounds_MW=[lo,hi],expected_research_AGC_MW=awards[g],current_bounds_MW=[curlo,curhi],research_delta=delta,diagnostic_intersection_MW=intersection,empty_intersection=intersection[0]>intersection[1],reason='current default reference profile rejects DA/current bound mismatch'))
 return dict(status='incompatible'if rows else'no_proven_conflict_not_feasibility_certificate',differences=rows,unknown=unknown,native_calls=0,actual_future_awards_issued=False,formal=False,physical_only_run_enabled=False)

def check(session,case,declaration):
 from source import read,sha,e
 # day_selection.validate already established exact fixed declaration payload.
 units=declaration['units'];e.need(len(units)==5 and all(u['qualified']is True and u['stable_online']is True for u in units),'no inference for unknown future qualification')
 e.need(declaration['parameters']['provincial_procurement_target_mw']['value']==50,'unsupported future target')
 da=Path(session.context['selected_DA']);art=read(da/'ARTIFACTS.json');ref=art['stages']['da_sced']['physical_solution'];p=da/ref['path']
 # Reuse selected accepted artifact binding, not a supplied physical JSON.
 expected=ref.get('sha256');e.need(expected is not None and sha(p)==expected,'DA physical reference source changed')
 origin=read(session.run/'seed/request.json')['origin'];result=diagnose(case,read(p)['physical_cells'],origin=origin,awards={u['resource_id']:10 for u in units})
 result['basis']='conditional necessary check for fixed five×10 same-award research declaration; not actual H24 selection or eligibility certification'
 if result['differences']:raise ValueError('跨日价格参考不兼容；尚未运行任何窗口，不承诺完整96价/24h核算。'+json.dumps(result,ensure_ascii=False))
 return result
