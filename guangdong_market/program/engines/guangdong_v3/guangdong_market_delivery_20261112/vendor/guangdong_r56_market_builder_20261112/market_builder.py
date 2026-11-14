"""Direct Pyomo construction; explicit research scope, no template/primal input."""
from copy import deepcopy
from datetime import datetime
import math
import pyomo.environ as pyo
from physical_builder import build_free_scuc, require

def validate(case,grid,policy,agc):
 e=case['energy_inputs'];n=len(e['horizon'].ids)
 require(n==576 and len(grid)==98,'only verified 48h/98 interface supported')
 require(policy['minimum_fee_phase_policy']=='connected_alpha_research','unsupported minimum fee phase')
 require(policy['event_scope']=='all48h_physical_events_once_research' and not policy['cold_warm_hot_implemented'],'uniform events only; cold/warm/hot unsupported')
 require(policy.get('weight_source'),'explicit weight source')
 bid=policy['noquote_energy_bid'];require(bid['yuan_per_mwh']==-50 and bid['source'] and bid['basis']=='official_public_parameter_baseline_not_dynamic_confirmation','public floor source required')
 require(bid['stage_scope']==['SCUC','SCED','DA_PRICE','RT','RT_PRICE'] and bid['effective_from']<=case['contract']['day_start'][:10],'bid stage/date')
 require(set(policy['minimum_adjustable_prices'])==set(e['thermal']),'minimum fee inventory')
 for g,v in policy['minimum_adjustable_prices'].items():
  require(v['source'] and v['basis']=='synthetic_assumption' and v['unit']=='yuan/MWh' and math.isfinite(v['yuan_per_mwh']),'explicit synthetic minimum fee')
  require(v['study_delivery_day']==case['contract']['day_start'][:10] and datetime.fromisoformat(v['declared_available_at'])<=datetime.fromisoformat(case['contract']['cutoff']),'minimum fee timing')
  require(g in agc and agc[g]['qualification']=='explicit_synthetic_qualified','explicit qualified AGC')
 require(policy['curtailment_penalty']['source'] and policy['curtailment_penalty']['yuan_per_mwh']>=0,'curtailment source')
 w=[0.]*n;wm=[0.]*n;seen=set()
 for i,p in enumerate(grid):
  ids=p['physical_indices'];require(len(ids)==3 and ids==list(range(ids[0],ids[0]+3)) and not seen.intersection(ids),'disjoint triple mapping')
  require(all(0<=t<n for t in ids),'physical index')
  require(ids==list(range(i*3,i*3+3)) if i<96 else p['kind'] in ('peak','valley'),'D96/tail')
  a=p['objective_weight_h'];b=policy['minimum_weights_h'][i]
  require(all(math.isfinite(x) and x>=0 for x in (a,b)),'finite weights')
  for t in ids:w[t]=a/3;wm[t]=b/3
  seen.update(ids)
 return w,wm

def build_market(case,grid,policy,agc):
 w,wm=validate(case,grid,policy,agc);inp=deepcopy(case)
 e=inp['energy_inputs'];e['renewable_mode']='dispatchable_bus_aggregate'
 e['renewable_bid_yuan_per_mwh']={k:-50. for k in e['renewable_mw']}
 penalty=policy['curtailment_penalty']['yuan_per_mwh']
 e['renewable_curtailment_penalty_yuan_per_mwh']={k:penalty for k in e['renewable_mw']}
 m=build_free_scuc(**{k:inp[k] for k in ('energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost')},agc_awards=agc)
 active={b for b in m.B if any(e['renewable_mw'][b,t]>0 for t in m.T)}
 for b in m.B:
  for t in m.T:
   for v in (m.renewable_power[b,t],m.renewable_curtailment[b,t]):
    if b not in active:v.fix(0.)
    else:v.setub(e['renewable_mw'][b,t])
 ds=m._energy_inputs['thermal'];curves=inp['curves'];terms=[]
 for g,d in ds.items():
  fee=policy['minimum_adjustable_prices'][g]['yuan_per_mwh']
  for t in m.T:
   terms += [wm[t]*d['bid_base_mw']*fee*m.online[g,t]]
   terms += [w[t]*s['price_yuan_per_mwh'][t]*m.segment_power[g,k,t] for k,s in d['segments'].items()]
   for kind,var in (('startup',m.start),('shutdown',m.stop)):
    curve=curves[g][kind]
    for row in curve:require(abs(row['cost']-inp['no_load_cost'][g]-row['mean']*next(iter(d['segments'].values()))['price_yuan_per_mwh'][t])<1e-7,'unsupported curve energy/noload decomposition')
    coef=d[kind+'_yuan'][t]+math.fsum((row['cost']-inp['no_load_cost'][g])*w[t+k] for k,row in enumerate(curve) if t+k<576)
    terms.append(coef*var[g,t])
 for b in active:
  for t in m.T:terms += [w[t]*(-50*m.renewable_power[b,t]+penalty*m.renewable_curtailment[b,t])]
 for u,d,k in m.battery.SEG:
  price=inp['batteries'][u][d+'_curve']['prices_yuan_per_mwh'][k]
  for t in m.T:terms.append(w[t]*price*(1 if d=='discharge' else -1)*m.battery.segment[u,d,k,t])
 m.total_cost.set_value(sum(terms));m._r56_scope=dict(uniform_event_fee=True,cold_warm_hot=False,physical_dt_h=1/12,market_weights=w,minimum_weights=wm,official_certification=False)
 return m
