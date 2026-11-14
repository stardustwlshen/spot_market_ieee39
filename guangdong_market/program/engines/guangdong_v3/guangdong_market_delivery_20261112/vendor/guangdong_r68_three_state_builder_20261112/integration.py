"""Replace R56 uniform thermal phases and objective, not post-process event fees."""
from pathlib import Path
import sys,dataclasses,math,importlib.util
from datetime import datetime,timedelta
import pyomo.environ as p
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_r65_three_state_startup_20261112'))
import startup as original15
from events5 import Curve,StartupDTO,validate,attach,CLASSES,need

def expand(d,sampling):
 policy=d.classification_boundary_policy
 need(policy in('hot_lt_H_warm_lt_W','hot_le_H_warm_le_W'),'UnsupportedRegime: classification endpoint policy')
 original15.validate(dataclasses.replace(d,classification_boundary_policy='hot_lt_H_warm_lt_W'))
 need(sampling.get('source'),'15-to-5 sampling source required');mode=sampling.get('mode')
 need(mode in('linear_endpoints_preserve_mean','explicit_subcurves'),'explicit supported 15-to-5 semantics required')
 def cv(k,c):
  rows=[]
  if mode=='linear_endpoints_preserve_mean':
   for a,m,z in zip(c.begin_mw,c.mean_mw,c.end_mw):
    need(abs(m-(a+z)/2)<1e-9,'15min mean not implied by linear endpoints; provide explicit subcurves')
    for j in range(3):
     lo=a+(z-a)*j/3;hi=a+(z-a)*(j+1)/3;rows.append((lo,(lo+hi)/2,hi))
  else:
   rows=[tuple(x)for x in sampling['subcurves'][k]];need(len(rows)==3*len(c.mean_mw),'three explicit cells per15min row')
   for i,(a,m,z)in enumerate(zip(c.begin_mw,c.mean_mw,c.end_mw)):
    q=rows[3*i:3*i+3];need(abs(q[0][0]-a)<1e-9 and abs(q[-1][2]-z)<1e-9 and abs(sum(x[1]for x in q)/3-m)<1e-9,'subcurve boundary/energy mismatch')
  return Curve(tuple(x[1]for x in rows),tuple(x[0]for x in rows),tuple(x[2]for x in rows),c.fee_yuan,3*c.notice_lead_ticks,c.source+'; sampling:'+sampling['source'])
 x=dataclasses.asdict(d);x.update(dt_minutes=5,hot_upper_ticks=3*d.hot_upper_ticks+int(policy=='hot_le_H_warm_le_W'),warm_upper_ticks=3*d.warm_upper_ticks+int(policy=='hot_le_H_warm_le_W'),classification_boundary_policy='hot_lt_H_warm_lt_W',threshold_source=d.threshold_source+'; exact5min integer encoding of '+policy,initial_offline_ticks=3*d.initial_offline_ticks,curves={k:cv(k,c)for k,c in d.curves.items()},shutdown=cv('shutdown',d.shutdown))
 return validate(StartupDTO(**x))

def readiness(resources,specs,sampling,curve_prices):
 missing=[]
 for g in resources:
  if g not in specs:missing.append(g+': three-state DTO (fees/limits/notice/curves/threshold policy/history)');continue
  if g not in sampling:missing.append(g+': 15-to-5 sampling semantics');continue
  if g not in curve_prices or not curve_prices[g].get('source'):missing.append(g+': curve-energy price series/source');continue
  try:expand(specs[g],sampling[g])
  except (ValueError,KeyError,TypeError)as e:missing.append(g+': '+str(e))
 return {'ready':not missing,'missing':missing,'native_calls':0,'large_model_constructed':False}

def connect_thermal(m,specs,sampling,w,wm,minimum_prices,curve_prices,publication_at,day_start):
 gs=list(m.G);n=len(m.T);need(list(m.T)==list(range(n))and len(w)==len(wm)==n,'dense five-minute physical grid and market weights')
 gate=readiness(gs,specs,sampling,curve_prices);need(gate['ready'],'missing three-state inputs: '+str(gate['missing']))
 need(all(type(v)in(int,float)and math.isfinite(v)and v>=0 for v in list(w)+list(wm)),'finite market/minimum weights')
 ds=m._energy_inputs['thermal'];d5={g:expand(specs[g],sampling[g])for g in gs}
 for g in gs:
  d=ds[g];s=d5[g];need(s.initial_phase==('stable'if d['initial_online']else'off'),'initial phase mismatch')
  if not d['initial_online']:need(abs(s.initial_offline_ticks/12-d['initial_state_age_h'])<1e-9,'initial actual offline age mismatch')
  need(s.curves['hot'].end_mw[-1]==d['min_mw'][0]and all(x==d['min_mw'][0]for x in d['min_mw'].values()),'stable endpoint mismatch/time-varying minimum unsupported')
  prices=curve_prices[g]['values'];need(len(prices)==n and all(type(x)in(int,float)and math.isfinite(x)for x in prices),'explicit finite per-cell curve energy prices')
 # All old uniform phases/event notice fixing and residence constraints are replaced.
 for name in ('phase_balance','event_availability','residence'):getattr(m,name).deactivate()
 for g in gs:
  for t in m.T:m.start[g,t].unfix();m.stop[g,t].unfix()
 m.three_state=p.Block(m.G);m.three_state_bind=p.ConstraintList();m.three_state_residence=p.ConstraintList();m.three_state_availability=p.ConstraintList()
 for g in gs:
  d=ds[g];s=d5[g];eligible=[t for t in range(n)if datetime.fromisoformat(day_start)+timedelta(minutes=5*t)>=datetime.fromisoformat(publication_at)]
  b=attach(m.three_state[g],'events',s,n,notice_eligible_ticks=eligible)
  ns={k:len(s.curves[k].mean_mw)for k in CLASSES};nd=len(s.shutdown.mean_mw)
  def active(t,field):
   return sum(getattr(s.curves[k],field)[t-j]*b.kind[j,k]for k in CLASSES for j in range(max(0,t-ns[k]+1),t+1))+sum(getattr(s.shutdown,field)[t-j]*b.stop[j]for j in range(max(0,t-nd+1),t+1))
  for t in m.T:
   for left,right in ((m.start[g,t],b.start[t]),(m.stop[g,t],b.stop[t]),(m.stable[g,t],b.stable[t]),(m.off[g,t],b.off[t])):m.three_state_bind.add(left==right)
   m.three_state_bind.add(m.online[g,t]==1-b.off[t])
   m.transition_mean[g,t].set_value(active(t,'mean_mw'));m.transition_begin[g,t].set_value(active(t,'begin_mw'));m.transition_end[g,t].set_value(active(t,'end_mw'));m.transition_active[g,t].set_value(b.start_active[t]+b.stop_active[t])
   if t%3:m.start[g,t].fix(0);m.stop[g,t].fix(0)
   if d['initial_state_age_h']+t/12<(d['min_up_h']if d['initial_online']else d['min_down_h'])-1e-9:(m.stop if d['initial_online']else m.start)[g,t].fix(0)
   up=range(max(0,t-math.ceil(d['min_up_h']*12)+1),t+1);down=range(max(0,t-nd-math.ceil(d['min_down_h']*12)+1),max(0,t-nd+1))
   m.three_state_residence.add(m.stop[g,t]+sum(m.start[g,j]for j in up)<=1);m.three_state_residence.add(m.start[g,t]+sum(m.stop[g,j]for j in down)<=1)
   if d['status'][t]!='free':m.three_state_availability.add(m.online[g,t]==int(d['status'][t]=='on'))
   # Preserve existing physical bound/AGC restrictions rather than inventing qualification.
   a=m._r23['inputs']['agc_awards'][g]
   if a['up_mw'][t]+a['down_mw'][t]>0:m.three_state_availability.add(m.stable[g,t]==1)
  for v,k in ((m.start,'max_starts'),(m.stop,'max_stops')):
   if d[k]is not None:m.three_state_availability.add(sum(v[g,t]for t in m.T)<=d[k])
 terms=[]
 for g in gs:
  d=ds[g];s=d5[g];b=m.three_state[g].events;terms.append(b.event_fee)
  for t in m.T:
   terms.append(wm[t]*d['bid_base_mw']*minimum_prices[g]*m.online[g,t])
   terms.extend(w[t]*z['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,z in d['segments'].items())
   terms.append(w[t]*curve_prices[g]['values'][t]*m.transition_mean[g,t])
 m.three_state_thermal_cost=p.Expression(expr=sum(terms))
 if hasattr(m,'free_thermal_cost'):m.free_thermal_cost.set_value(m.three_state_thermal_cost)
 m.event_cost.set_value(sum(m.three_state[g].events.event_fee for g in gs))
 m._r68={'cold_warm_hot':True,'sampling':sampling,'class_clock_basis':{g:specs[g].time_basis for g in gs},'declared_boundary_policy':{g:specs[g].classification_boundary_policy for g in gs},'terminal_transitions':'complete within physical horizon','official_certification':False,'cost_scope':'once event + explicit curve energy + minimum + incremental'}
 return m

def build_market_three_state(case,grid,policy,agc,*,specs,sampling,curve_prices):
 gate=readiness(case['energy_inputs']['thermal'],specs,sampling,curve_prices)
 for g,d in case['energy_inputs']['thermal'].items():
  if d.get('initial_state_kind')!='stable':gate['missing'].append(g+': ongoing initial transition requires selected class/progress/last disconnect; unsupported, not reset stable')
 gate['ready']=not gate['missing']
 if not gate['ready']:return gate
 need(policy.get('cold_warm_hot_implemented')is True,'explicit new three-state policy required')
 need(len(grid)==98 and sorted(x.get('kind','')for x in grid[96:])==['peak','valley'],'one next-day peak and valley required')
 penalty=policy['curtailment_penalty']['yuan_per_mwh'];need(type(penalty)in(int,float)and math.isfinite(penalty)and penalty>=0,'finite curtailment penalty')
 # Invoke immutable base constructor only to retain its network/storage/energy blocks.
 sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'));sys.path.insert(0,str(ROOT/'guangdong_r56_market_builder_20261112'))
 spec=importlib.util.spec_from_file_location('r68_frozen_base',ROOT/'guangdong_r56_market_builder_20261112/market_builder.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
 phys_spec=importlib.util.spec_from_file_location('r68_frozen_physical',ROOT/'guangdong_r56_market_builder_20261112/physical_builder.py');phys=importlib.util.module_from_spec(phys_spec);phys_spec.loader.exec_module(phys);base.build_free_scuc=phys.build_free_scuc;base.require=phys.require
 old=dict(policy,cold_warm_hot_implemented=False);w,wm=base.validate(case,grid,old,agc);m=base.build_market(case,grid,old,agc)
 connect_thermal(m,specs,sampling,w,wm,{g:v['yuan_per_mwh']for g,v in policy['minimum_adjustable_prices'].items()},curve_prices,case['contract']['publication_at'],case['contract']['day_start'])
 e=case['energy_inputs'];other=[]
 for b in m.B:
  for t in m.T:other.append(w[t]*(-50*m.renewable_power[b,t]+policy['curtailment_penalty']['yuan_per_mwh']*m.renewable_curtailment[b,t]))
 for u,d,k in m.battery.SEG:
  price=case['batteries'][u][d+'_curve']['prices_yuan_per_mwh'][k]
  for t in m.T:other.append(w[t]*price*(1 if d=='discharge'else-1)*m.battery.segment[u,d,k,t])
 m.total_cost.set_value(m.three_state_thermal_cost+sum(other));m._r56_scope.update(uniform_event_fee=False,cold_warm_hot=True,official_certification=False);return m
