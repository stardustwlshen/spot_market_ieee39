"""Physical capacity only, not reserve award/payment eligibility.
PS primary: Guangdong 2026 energy rules pp104-105 (10% online capacity).
Battery primary is not added to that published formula.
Contingency participation: user's all-except-wind/solar/import selection.
15min sustained energy and 10min response are labeled model assumptions.
Only current discharge/generation modes plus battery charging reduction counted;
no unsupported PS startup/pump-trip capacity is credited.
"""
import pyomo.environ as pyo
import math

def pumped_response(p,unit_count):
 enabled=p.get("contingency_response_enabled",True)
 if type(enabled)is not bool:raise ValueError("contingency_response_enabled must be Boolean")
 step=p.get("gen_up_step_mw",min(2,p.get("n_units",unit_count))*p["unit_gen_max"])
 cap=p.get("contingency_response_mw_10min",float(step)*10/15)
 if any(isinstance(v,bool) or not math.isfinite(float(v)) or float(v)<0 for v in (step,cap)):raise ValueError("nonnegative finite PS response input required")
 return (min(float(cap),float(step)*10/15) if enabled else 0.),dict(enabled=enabled,basis="declared_10min_response" if "contingency_response_mw_10min" in p else "research_linear_ramp_response_not_certified",ramp_basis="declared_15min_step" if "gen_up_step_mw" in p else "research_capacity_based_fallback_not_certified")


def attach(m,duration_h=.25):
 b=m.extra_reserve=pyo.Block();ts=tuple(m.T)
 bats=tuple(m.battery.BAT) if hasattr(m,'battery') else ()
 stations=tuple(m.pumped.S) if hasattr(m,'pumped') else ()
 b.BAT=pyo.Set(initialize=bats);b.PS=pyo.Set(initialize=stations);b.T=pyo.Set(initialize=ts)
 b.battery_up=pyo.Var(b.BAT,b.T,domain=pyo.NonNegativeReals)
 b.pumped_up=pyo.Var(b.PS,b.T,domain=pyo.NonNegativeReals)
 b.pumped_primary=pyo.Var(b.PS,b.T,domain=pyo.NonNegativeReals)
 b.cap=pyo.ConstraintList()
 for u in bats:
  r=m._battery_inputs[u];v=m.battery;eta=r['_eta']
  for t in ts:
   # Same-hour direction cannot be switched by a reserve offer.
   b.cap.add(b.battery_up[u,t]<=r['discharge_max_mw']*v.discharge_mode[u,t]-v.discharge[u,t]+v.charge[u,t])
   b.cap.add(b.battery_up[u,t]<=(v.energy[u,t]-r['energy_min_mwh'])*eta/duration_h+v.charge[u,t])
   b.cap.add(b.battery_up[u,t]<=r['ramp_up_mw_per_h']/6.)
 for s in stations:
  z=m._pumped_data[s];p=z['p'];v=m.pumped
  for t in ts:
   online=sum(p['unit_gen_max']*v.gen_mode[s,u,t] for u in z['names'])
   generation=sum(v.gen[s,u,t] for u in z['names']);headroom=online-generation
   joint=b.pumped_up[s,t]+b.pumped_primary[s,t]
   b.cap.add(joint<=headroom)
   b.cap.add(joint<=(v.upper[s,t]-p['vup_min']*10000)*p['e_gen']/10000/duration_h)
   b.cap.add(joint<=(p['vdn_max']*10000-v.lower[s,t])*p['e_gen']/10000/duration_h)
   b.cap.add(b.pumped_primary[s,t]<=.10*online)
   # Same sourced aggregate 15min ramp as dispatch; scale to 10min response.
   response_cap,response_basis=pumped_response(p,len(z['names']))
   b.cap.add(b.pumped_up[s,t]<=response_cap)
 b.up=pyo.Expression(b.T,rule=lambda _,t:sum(b.battery_up[u,t] for u in bats)+sum(b.pumped_up[s,t] for s in stations))
 b.primary=pyo.Expression(b.T,rule=lambda _,t:sum(b.pumped_primary[s,t] for s in stations))
 m._physical_reserve_basis=dict(pumped_response={s:pumped_response(m._pumped_data[s]['p'],len(m._pumped_data[s]['names']))[1] for s in stations},primary='GD provincial thermal/PS/hydro formula; battery not in primary formula',contingency='user specified participation; no wind solar imports',response_minutes=10,sustain_minutes=15,energy_basis='end-of-period available state',scope='current modes only; no PS pump-trip or offline start credit; not monetary settlement eligibility')
 return b
