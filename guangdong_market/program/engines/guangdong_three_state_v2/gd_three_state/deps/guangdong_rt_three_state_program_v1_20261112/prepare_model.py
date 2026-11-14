from pathlib import Path
import json,sys,math,time
import pyomo.environ as p
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'guangdong_rt_three_state_integration_v1_20261112'))
from build import build,uniform_module
from contract import from_uniform
from component5 import classify
from smm.gd_october_job_codec import loads
B=Path(__file__).resolve().parent;R=B.parent

def original_witness(parent):
 run=Path(parent).resolve();a=json.loads((run/'ARTIFACTS.json').read_bytes())
 case=loads((run/a['inputs']['case']['path']).read_bytes());agc=json.loads((run/a['inputs']['agc']['path']).read_bytes())
 for z in agc.values():
  for k in ('up_mw','down_mw'):z[k]={int(t):v for t,v in z[k].items()}
 s=a['stages']['da_sced'];names=json.loads((run/s['full_names']['path']).read_bytes())['variables'];vals=json.loads((run/s['expanded_response']['path']).read_bytes())['primal'];x=dict(zip(names,vals));parameters=from_uniform(case)
 
 m=build(case,agc,mode='three_state',parameters=parameters)
 for v in m.component_data_objects(p.Var):
  if v.name in x:v.set_value(x[v.name])
 phases=[]
 for g in m.G:
  b=m.three[g].event;d=b._dto;starts={};stops={t for t in m.T if p.value(m.stop[g,t])>.5};age=d.initial_offline_ticks;off=int(d.initial_phase=='off');stable=1-off
  for t in b.B:
   if t:
    age=0 if t-len(d.shutdown.mean_mw)in stops else age+1 if off else 0
   if t<len(m.T) and p.value(m.start[g,t])>.5:starts[t]=classify(d,age)
   complete=sum(1 for s,k in starts.items() if s+len(d.curves[k].mean_mw)==t)
   off+=int(t-len(d.shutdown.mean_mw)in stops)-int(t in starts);stable+=complete-int(t in stops)
   b.off[t].set_value(off);b.stable[t].set_value(stable);b.offline_ticks[t].set_value(age)
   if t<len(m.T):
    b.start[t].set_value(int(t in starts));b.stop[t].set_value(int(t in stops))
    for k in b.K:b.kind[t,k].set_value(int(starts.get(t)==k))
  for t in b.T:
   for k in b.K:b.notice[t,k].set_value(int(starts.get(t+d.curves[k].notice_lead_ticks)==k))
  phases.append(dict(unit=g,starts=starts,stops=sorted(stops)))
 missing=[v.name for v in m.component_data_objects(p.Var)if v.value is None];assert not missing,missing[:10]
 return m,case,agc,parameters,phases
