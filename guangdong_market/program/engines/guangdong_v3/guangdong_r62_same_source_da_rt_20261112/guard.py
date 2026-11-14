"""Necessary remaining internal-throughput reachability, not sufficient feasibility."""
from datetime import datetime,timedelta
import math
POLICY=dict(schema='gd.research.r62.d96_reachability.v1',internal_throughput='charge_plus_discharge_with_efficiency',inequalities=['U+E<=B+Efin','U-E<=B-Efin'],only_at_or_before_original_D96=True,rolling_2h_endpoint='independent_research_remove_non_D96_only',reserve_mw=400,official_new_rule=False)
def margin(used,energy,budget,target):
 if not all(math.isfinite(x) for x in (used,energy,budget,target)) or used<0 or budget<0:raise ValueError('finite physical reachability inputs')
 return budget+target-used-energy,budget-target-used+energy

def attach(m,inp):
 import pyomo.environ as p
 day=datetime.fromisoformat(inp['day_start']);end=day+timedelta(days=1);date=day.date().isoformat();start=datetime.fromisoformat(inp['window']['start'])
 m.d96_reachability=p.Constraint(p.Any);record=[]
 for r,u in inp['resources'].items():
  if u['kind']!='battery':continue
  initial=inp['projection']['state']['batteries'][r];used=initial['daily_used_internal_mwh'].get(date,0.);budget=u['cycle_budget'][date]['limit_internal_mwh'];target=inp['midnight_targets'][r]
  if start<=end:
   if min(margin(used,initial['energy_mwh'],budget,target)) < -1e-6:raise ValueError('original D96 necessary reachability already lost: '+r)
  terms=[]
  for k in range(1,25):
   j=k-1;at=start+timedelta(minutes=5*k)
   if inp['da_cells'][j]['start'][:10]==date:
    factor=-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge']
    terms.append(factor*m.power[r,j]/12)
   if at>end:continue
   U=used+sum(terms)
   m.d96_reachability.add((r,k,'plus'),U+m.energy[r,k]<=budget+target)
   m.d96_reachability.add((r,k,'minus'),U-m.energy[r,k]<=budget-target)
   record.append(dict(resource=r,local_energy_index=k,at=at.isoformat(),date=date,budget_internal_mwh=budget,target_mwh=target))
 return dict(policy=POLICY,states=len(record),added_rows=2*len(record),records=record,sufficient_for_full_day_feasibility=False)
