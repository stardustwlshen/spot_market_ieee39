"""IEEE39-capable network builder using original R56 physical blocks, never R80 fixture.
Full absolute axis retains inherited curves/min-times/SOC/hour direction across windows.
"""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import sys,importlib.util,math
from contracts import validate,need,stamp
from runtime_config import dependency_root
ROOT=dependency_root()

def dependencies():
 sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
 spec=importlib.util.spec_from_file_location('_generic_r56_physical',ROOT/'guangdong_r56_market_builder_20261112/physical_builder.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def required_prefix_variables(m,issued_end,origin):
 import pyomo.environ as p
 result={}
 for v in m.component_data_objects(p.Var):
  if v.parent_component().name=='rt_notice':continue
  idx=v.index();idx=idx if isinstance(idx,tuple)else(idx,)
  if v.parent_component().name=='battery.hour_direction':
   if stamp(idx[-1])<origin+timedelta(minutes=5*issued_end):result[v.name]=v
  elif idx and type(idx[-1])is int and 0<=idx[-1]<issued_end:result[v.name]=v
 return result

def build(case,request,agc,policy):
 import pyomo.environ as p
 phys=dependencies()
 from smm.gd_horizon import EnergyHorizon,EnergyPeriod
 r=validate(request);need(policy['event_scope']=='all48h_physical_events_once_research'and not policy['cold_warm_hot_implemented'],'original uniform event policy required');c=deepcopy(case);e=c['energy_inputs'];e['renewable_mode']='dispatchable_bus_aggregate';h=e['horizon']
 if isinstance(h,dict):e['horizon']=EnergyHorizon(**dict(h,periods=tuple(EnergyPeriod(**x)for x in h['periods'])))
 need(len(e['horizon'].ids)==576,'explicit original48h case required')
 need(c['contract']['day_start']==r['origin'],'case absolute origin')
 need(all(z['initial_state_kind']=='stable'for z in e['thermal'].values()),'origin ongoing curves unsupported; preserve full prior event axis, never reset stable')
 need(all(v==400 for v in c['reserve']['positive_mw'].values())and all(v==400 for v in c['reserve']['contingency_mw'].values()),'400 requirements')
 m=phys.build_free_scuc(**{k:c[k]for k in ('energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost')},agc_awards=agc)
 day=stamp(r['origin']);cut=r['cutoff_tick'];delivery=r['delivery_tick'];prefix=required_prefix_variables(m,delivery,day);given=r['state']['fixed_values']
 need(set(given)==set(prefix),'missing/extra fixed prefix variables: '+str((len(given),len(prefix))))
 for name,var in prefix.items():
  value=given[name];need((var.lb is None or value>=var.lb-1e-8)and(var.ub is None or value<=var.ub+1e-8),'prefix bounds');var.fix(value)
 # One explicit notice clock per unit, uniform original lead; never fabricate cold/warm/hot.
 m.rt_notice=p.Var(m.G,m.T,domain=p.Binary);m.rt_notifications=p.ConstraintList()
 need(set(r['DA_online'])==set(m.G),'DA online inventory')
 notices=r['state']['notice_values'];need(set(notices)==set(m.G),'notice history inventory')
 for g in m.G:
  lead=c['curves'][g]['notice_h']*12;need(abs(lead-round(lead))<1e-9,'notice not exact5min requires explicit sampling policy');lead=int(round(lead))
  need(set(map(int,notices[g]))==set(range(cut)),'complete issued past notice history required')
  need(len(r['DA_online'][g])==576,'DA physical status coverage')
  need(set(r['preorigin_notices'])==set(m.G),'preorigin notice inventory')
  pre=r['preorigin_notices'][g];need(set(map(int,pre))==set(range(-lead,0)),'explicit preorigin notice history required')
  need(all(v in(0,1)for v in notices[g].values())and all(type(v)is bool for v in r['DA_online'][g]),'binary notice/DA status required')
  for t in m.T:
   if t<cut:m.rt_notice[g,t].fix(notices[g][str(t)])
   if t+lead>=576:m.rt_notice[g,t].fix(0)
   if t-lead>=0:m.rt_notifications.add(m.start[g,t]==m.rt_notice[g,t-lead])
   else:
    value=pre[str(t-lead)];need(value in(0,1),'preorigin notice boolean');m.rt_notifications.add(m.start[g,t]==value)
   # Never withdraw a DA awarded on period via a new stop without a safety authorization.
   if delivery<=t<288 and any(r['DA_online'][g][:288]):m.stop[g,t].fix(0)
   if delivery<=t<288 and r['DA_online'][g][t]:m.rt_notifications.add(m.online[g,t]==1)
 # Explicit near2h energy, remainder feasibility; future events charged once. Past fee sunk.
 terms=[];ts=range(delivery,delivery+24);ds=m._energy_inputs['thermal']
 for g,d in ds.items():
  for t in range(delivery,576):terms.extend([d['startup_yuan'][t]*m.start[g,t],d['shutdown_yuan'][t]*m.stop[g,t]])
  for t in ts:
   energy=d['bid_base_mw']*policy['minimum_adjustable_prices'][g]['yuan_per_mwh']*m.online[g,t]+sum(v['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,v in d['segments'].items())
   for kind,var in [('startup',m.start),('shutdown',m.stop)]:
    energy+=sum((row['cost']-c['no_load_cost'][g])*var[g,t-j]for j,row in enumerate(c['curves'][g][kind])if t-j>=0)
   terms.append(energy/12)
 for b in m.B:
  for t in ts:terms.append((-50*m.renewable_power[b,t]+policy['curtailment_penalty']['yuan_per_mwh']*m.renewable_curtailment[b,t])/12)
 for u,d,k in m.battery.SEG:
  for t in ts:terms.append(c['batteries'][u][d+'_curve']['prices_yuan_per_mwh'][k]*(1 if d=='discharge'else-1)*m.battery.segment[u,d,k,t]/12)
 m.total_cost.set_value(sum(terms))
 m._generic_rt=dict(request=r,fixed_prefix_names=sorted(prefix),formal=False,physical_horizon=576,near=(delivery,delivery+24),far=(delivery+24,delivery+48),tail='retained explicit feasibility to576',original_400_unchanged=True,new_BESS_modes=True,three_state=False,AGC='explicit supplied research awards, not a cleared formal auction')
 return m
