"""Independent PRICE physical reserve capacity; same accepted modes/end state.
Not a financial service award, excludes VRE and external imports.
"""
from pathlib import Path
import hashlib,json,math
import pyomo.environ as p
from .resource_reserve import pumped_response

def attach_soft_target(m,prepared,n,pins):
 f=(prepared/'RT_SOFT_TERMINAL.json').resolve()
 if not f.is_file():raise ValueError('accepted RT soft terminal evidence missing')
 digest=hashlib.sha256(f.read_bytes()).hexdigest()
 if pins.get(str(f))!=digest:raise ValueError('RT soft terminal report not accepted/source-bound')
 report=json.loads(f.read_bytes());units=list(report['resources']);m.price_target_under=p.Var(units,domain=p.NonNegativeReals);m.price_target_over=p.Var(units,domain=p.NonNegativeReals);cost=0
 for u,z in report['resources'].items():
  m.rows.add(m.energy[u,n]+m.price_target_under[u]>=z['lower_reachable_target_mwh'])
  m.rows.add(m.energy[u,n]-m.price_target_over[u]<=z['upper_reachable_target_mwh'])
  cost+=z['penalty_yuan_per_mwh_deviation']*(m.price_target_under[u]+m.price_target_over[u])
 return dict(cost=cost,report_path=str(f),report_sha256=digest,not_operating_cost=True)

def attach_reserves(m,case,values,first,n,modes):
 b=m.price_reserve=p.Block();U=list(case['batteries']);S=list(m.pumped.S);T=range(n);response_basis={}
 b.battery_up=p.Var(U,T,domain=p.NonNegativeReals);b.pumped_up=p.Var(S,T,domain=p.NonNegativeReals);b.pumped_primary=p.Var(S,T,domain=p.NonNegativeReals);b.rows=p.ConstraintList()
 for u,r in case['batteries'].items():
  eta=math.sqrt(r['roundtrip_efficiency'])
  for t in T:
   mode=modes[u,t]['mode'];charge=-m.P[u,t]if mode=='charge'else 0;discharge=m.P[u,t]if mode=='discharge'else 0
   b.rows.add(b.battery_up[u,t]<=r['discharge_max_mw']*int(mode=='discharge')-discharge+charge)
   b.rows.add(b.battery_up[u,t]<=(m.energy[u,t+1]-r['energy_min_mwh'])*eta/.25+charge)
   b.rows.add(b.battery_up[u,t]<=r['ramp_up_mw_per_h']/6)
 for s in S:
  z=m._pumped_data[s];d=z['p'];v=m.pumped;response_cap,response_basis[s]=pumped_response(d,len(z['names']))
  for t in T:
   online=sum(d['unit_gen_max']*v.gen_mode[s,u,t]for u in z['names']);gen=sum(v.gen[s,u,t]for u in z['names']);joint=b.pumped_up[s,t]+b.pumped_primary[s,t]
   b.rows.add(joint<=online-gen)
   b.rows.add(joint<=(v.upper[s,t]-d['vup_min']*10000)*d['e_gen']/10000/.25)
   b.rows.add(joint<=(d['vdn_max']*10000-v.lower[s,t])*d['e_gen']/10000/.25)
   b.rows.add(b.pumped_primary[s,t]<=.1*online)
   b.rows.add(b.pumped_up[s,t]<=response_cap)
 b.up=p.Expression(T,rule=lambda _,t:sum(b.battery_up[u,t]for u in U)+sum(b.pumped_up[s,t]for s in S));b.primary=p.Expression(T,rule=lambda _,t:sum(b.pumped_primary[s,t]for s in S))
 return dict(PS_response_basis=response_basis,energy_basis='period_end',battery_primary=False,PS_primary_and_contingency_share_capacity_and_water=True,thermal_primary_and_contingency_share_capacity=True,response_minutes=10,sustain_minutes=15,qualification='user-selected thermal battery PS only; no wind solar imports',market_award=False)
