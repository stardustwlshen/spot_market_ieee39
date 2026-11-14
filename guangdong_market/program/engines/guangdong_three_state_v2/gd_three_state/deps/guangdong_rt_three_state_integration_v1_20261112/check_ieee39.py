from pathlib import Path
import json,sys,math,time
import pyomo.environ as p
from build import build,uniform_module
from contract import from_uniform
from component5 import classify
from smm.gd_october_job_codec import loads
B=Path(__file__).resolve().parent;R=B.parent
run=R/'guangdong_pipeline_20261112/pure_fresh_run_v1';a=json.loads((run/'ARTIFACTS.json').read_bytes())
case=loads((run/a['inputs']['case']['path']).read_bytes());agc=json.loads((run/a['inputs']['agc']['path']).read_bytes())
for z in agc.values():
 for k in ('up_mw','down_mw'):z[k]={int(t):v for t,v in z[k].items()}
s=a['stages']['da_sced'];names=json.loads((run/s['full_names']['path']).read_bytes())['variables'];vals=json.loads((run/s['expanded_response']['path']).read_bytes())['primal'];x=dict(zip(names,vals));parameters=from_uniform(case)
(B/'RESEARCH_PARAMETERS.json').write_text(json.dumps(parameters,indent=2))
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
worst=[];n=0
for z in m.component_data_objects(p.Constraint,active=True):
 v=p.value(z.body);err=max(0,0 if z.lower is None else p.value(z.lower)-v,0 if z.upper is None else v-p.value(z.upper));n+=1
 if err>1e-6:worst.append((z.name,err))
obj=p.value(m.total_cost);event=p.value(m.event_cost)
result=dict(rows=n,violations=sorted(worst,key=lambda z:-z[1])[:20],objective=obj,event_fee=event,phases=phases,native_calls=0,parameters='explicit degenerate identical three-state research',tail_option='complete_all_curves_by_horizon')
(B/'IEEE39_WITNESS.json').write_text(json.dumps(result,indent=2));assert not worst,worst[:3]
# Uniform physical model independently evaluated at exactly the same original vector.
u=build(case,agc)
for v in u.component_data_objects(p.Var):v.set_value(x[v.name])
result['uniform_objective']=p.value(u.total_cost);result['uniform_event_fee']=p.value(u.event_cost);assert abs(obj-result['uniform_objective'])<1e-6 and abs(event-result['uniform_event_fee'])<1e-6
result['kept_components']={name:len(getattr(m,name))for name in ('free_power','free_lower','free_upper','physical_ramps','residence','reserves')};result['battery_variables']=sum(1 for _ in m.battery.component_data_objects(p.Var));result['three_state_variables']=sum(1 for _ in m.component_data_objects(p.Var) if _.name.startswith('three['));result['original_variables']=sum(1 for _ in u.component_data_objects(p.Var))
from rt_bridge import configure_rt,expected_prefix,binding
from datetime import datetime
r=json.loads((R/'guangdong_rt_recommit_program_v1_20261112/user_ready_run_v1/seed/request.json').read_bytes());policy=json.loads((run/a['inputs']['policy']['path']).read_bytes())
state=dict(schema='gd.rt.three_state.issued_prefix.v1',origin=r['origin'],cutoff_tick=0,delivery_tick=3,DA_online=r['DA_online'],**binding(case,parameters))
state['fixed_values']={name:p.value(v)for name,v in expected_prefix(m,3,0,datetime.fromisoformat(r['origin'])).items()}
configure_rt(m,case,parameters,policy,state)
# Evaluate original frozen generic RT objective text against existing uniform model; no construction or native call.
source=(R/'guangdong_rt_recommit_program_v1_20261112/runtime/builder.py').read_text()
code=source.split(" terms=[];ts=range(delivery,delivery+24);ds=m._energy_inputs['thermal']")[1].split(' m._generic_rt=')[0]
import textwrap
namespace=dict(m=u,c=case,delivery=3,policy=policy,terms=[],ts=range(3,27),ds=u._energy_inputs['thermal'])
exec(textwrap.dedent(code),namespace)
result['RT_objective']=p.value(m.total_cost);result['uniform_RT_reference_objective']=p.value(u.total_cost);assert abs(result['RT_objective']-result['uniform_RT_reference_objective'])<1e-6
result['new_prefix_variables']=len(state['fixed_values']);result['RT_state_schema']=state['schema'];result['RT_objective_reference']='exact frozen local generic builder objective source, no receipt-directed code'
(B/'NEW_RESEARCH_RT_STATE.json').write_text(json.dumps(state,indent=2));(B/'IEEE39_WITNESS.json').write_text(json.dumps(result,indent=2));(B/'FINAL_COMPLETE.json').write_text(json.dumps(dict(status='pure_model_and_witness_checks_passed',native_calls=0),indent=2));print(json.dumps(result))
