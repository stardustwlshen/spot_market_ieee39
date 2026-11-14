from pathlib import Path
import sys,json,hashlib,dataclasses
import pyomo.environ as p
from integration import *
sys.path.insert(0,str(ROOT/'guangdong_r65_three_state_startup_20261112'))
# Explicitly load the already-disclosed R65 synthetic DTO, not production case data.
sp=importlib.util.spec_from_file_location('r68_r65_fixture',ROOT/'guangdong_r65_three_state_startup_20261112/prepare.py');f=importlib.util.module_from_spec(sp);sp.loader.exec_module(f)

def build():
 dto=f.fixture();n=90;m=p.ConcreteModel();m.G=p.Set(initialize=['G']);m.T=p.RangeSet(0,n-1);m.GK=p.Set(dimen=2,initialize=[])
 m.start=p.Var(m.G,m.T,domain=p.Binary);m.stop=p.Var(m.G,m.T,domain=p.Binary);m.stable=p.Var(m.G,m.T,domain=p.Binary);m.off=p.Var(m.G,m.T,domain=p.Binary);m.online=p.Var(m.G,m.T,domain=p.Binary);m.power=p.Var(m.G,m.T,bounds=(0,100));m.segment_power=p.Var(m.GK,m.T)
 for name in ('transition_mean','transition_begin','transition_end','transition_active'):setattr(m,name,p.Expression(m.G,m.T,rule=lambda _,g,t:0.))
 m.end_power=p.Expression(m.G,m.T,rule=lambda _,g,t:m.power[g,t]-m.transition_mean[g,t]+m.transition_end[g,t]);m.event_cost=p.Expression(expr=0)
 for name in ('phase_balance','event_availability','residence'):setattr(m,name,p.ConstraintList())
 m.free_power=p.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]==100*m.stable[g,t]+m.transition_mean[g,t]);m.physical_ramps=p.ConstraintList()
 for t in m.T:
  prev=0 if t==0 else m.end_power['G',t-1]
  m.physical_ramps.add(prev-m.transition_begin['G',t]<=100*(1-m.transition_active['G',t]));m.physical_ramps.add(m.transition_begin['G',t]-prev<=100*(1-m.transition_active['G',t]))
 d={'initial_online':False,'initial_state_age_h':.75,'min_mw':{t:100 for t in m.T},'min_up_h':0,'min_down_h':0,'status':{t:'free'for t in m.T},'max_starts':3,'max_stops':3,'bid_base_mw':100,'segments':{}}
 m._energy_inputs={'thermal':{'G':d}};m._r23={'inputs':{'agc_awards':{'G':{'up_mw':{t:0 for t in m.T},'down_mw':{t:0 for t in m.T}}}}}
 sampling={'G':{'mode':'linear_endpoints_preserve_mean','source':'R68 explicit synthetic linear endpoint interpolation; preserves15minmean'}};prices={'G':{'values':[5.]*n,'source':'R68 synthetic curve energy price5yuan/MWh'}}
 connect_thermal(m,{'G':dto},sampling,[1/12]*n,[1/12]*n,{'G':0},prices,'2026-11-11T00:00:00+08:00','2026-11-11T00:00:00+08:00')
 for t in m.T:m.stop['G',t].fix(int(t in (18,42,84)))
 m.service=p.ConstraintList()
 for t in (12,36,81):m.service.add(m.stable['G',t]==1)
 for t in (24,48,69):m.service.add(m.off['G',t]==1)
 m.service.add(sum(m.start['G',t]for t in m.T)==3)
 m.external_supply=p.Var(m.T,domain=p.NonNegativeReals);m.energy_balance=p.Constraint(m.T,rule=lambda _,t:m.power['G',t]+m.external_supply[t]==100)
 m.total_cost=p.Objective(expr=m.three_state_thermal_cost+sum(m.external_supply[t]*10/12 for t in m.T));return m,dto,sampling

def witness(m,dto):
 b=m.three_state['G'].events;d=b._dto;starts={6:'warm',30:'hot',72:'cold'};stops={18,42,84};age=d.initial_offline_ticks
 for t in b.T:
  b.start[t].value=int(t in starts);b.stop[t].value=int(t in stops)
  for k in CLASSES:b.kind[t,k].value=int(starts.get(t)==k);b.notice[t,k].value=int(starts.get(t+d.curves[k].notice_lead_ticks)==k)
 for t in b.B:
  active=any(s<=t<s+len(d.curves[k].mean_mw)for s,k in starts.items())or any(s<=t<s+len(d.shutdown.mean_mw)for s in stops)
  stable=any(s+len(d.curves[k].mean_mw)<=t<next(z for z in sorted(stops)if z>s)for s,k in starts.items());b.stable[t].value=int(stable);b.off[t].value=int(not active and not stable)
  if t:
   if t-len(d.shutdown.mean_mw)in stops:age=0
   elif p.value(b.off[t-1]):age+=1
   else:age=0
  b.offline_ticks[t].value=age
  if t<90:
   for name in ('start','stop','stable','off'):getattr(m,name)['G',t].value=p.value(getattr(b,name)[t])
   m.online['G',t].value=1-p.value(b.off[t]);m.power['G',t].value=100*p.value(b.stable[t])+p.value(m.transition_mean['G',t]);m.external_supply[t].value=100-p.value(m.power['G',t])

def main():
 sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
 from smm.highs_solver import serialize_model,validate_options
 from smm.gd_r23_equivalent_compression import audit
 m,d,sampling=build();q,vs,cs=serialize_model(m,validate_options({'time_limit':30.,'threads':1,'mip_rel_gap':.001,'mip_abs_gap':0.,'output_flag':False}));q['preparation_elapsed_s']=0.;names={'variables':[v.name for v in vs],'constraints':[c.name for c in cs]};witness(m,d);xs=[p.value(v)for v in vs];a=audit(q,xs,p.value(m.total_cost))
 out=B/'prepared_small_v2';out.mkdir(exist_ok=False)
 for name,data in [('template.json',q),('names.json',names),('DTO15.json',dataclasses.asdict(d)),('DTO5.json',dataclasses.asdict(m.three_state['G'].events._dto)),('sampling.json',sampling),('CONSTRUCTED_WITNESS.json',{'primal':xs,'audit':a,'native_calls':0})]: (out/name).write_text(json.dumps(data,indent=2,allow_nan=False))
 sha=lambda x:hashlib.sha256(x.read_bytes()).hexdigest();r={'native_calls':0,'large_builder_run':False,'variables':len(vs),'rows':len(cs),'source_pins':{str(x.resolve()):sha(x)for x in (B/'integration.py',B/'events5.py',B/'prepare_small.py')},'artifact_hashes':{x.name:sha(x)for x in out.iterdir()},'constructed_feasible_objective':p.value(m.total_cost),'baseline_data_changed':False};(out/'READY.json').write_text(json.dumps(r,indent=2));print(r)
if __name__=='__main__':main()
