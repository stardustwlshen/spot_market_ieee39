"""Pure RT objective and explicit new three-state prefix contract. No implicit legacy import."""
import pyomo.environ as p
import hashlib,json
from smm.gd_october_job_codec import dumps
def binding(case,parameters):return dict(case_identity=hashlib.sha256(dumps(case)).hexdigest(),parameters_identity=hashlib.sha256(json.dumps(parameters,sort_keys=True).encode()).hexdigest())
from datetime import datetime,timedelta
from build import build

def expected_prefix(m,delivery,cutoff,origin):
 out={}
 for v in m.component_data_objects(p.Var):
  idx=v.index();parts=idx if isinstance(idx,tuple)else(idx,)
  if v.name.startswith('three['):
   tick=parts[0];bound=cutoff if v.parent_component().local_name=='notice'else delivery
   if tick<bound:out[v.name]=v
  elif v.parent_component().name=='battery.hour_direction':
   if datetime.fromisoformat(parts[-1])<origin+timedelta(minutes=5*delivery):out[v.name]=v
  elif parts and type(parts[-1])is int and parts[-1]<delivery:out[v.name]=v
 return out

def configure_rt(m,case,parameters,policy,state):
 if state.get('schema')!='gd.rt.three_state.issued_prefix.v1':raise ValueError('new explicit state required; legacy single rt_notice cannot migrate implicitly')
 if state['origin']!=case['contract']['day_start'] or state['delivery_tick']!=state['cutoff_tick']+3:raise ValueError('absolute origin/delivery mismatch')
 if any(state.get(k)!=v for k,v in binding(case,parameters).items()):raise ValueError('case/parameters changed across window')
 delivery=state['delivery_tick'];cut=state['cutoff_tick'];prefix=expected_prefix(m,delivery,cut,datetime.fromisoformat(state['origin']))
 if set(prefix)!=set(state['fixed_values']):raise ValueError('missing/extra new phase/category/notice/clock prefix')
 for name,v in prefix.items():v.fix(state['fixed_values'][name])
 if set(state['DA_online'])!=set(m.G):raise ValueError('DA inventory required')
 m.da_protection=p.ConstraintList()
 for g in m.G:
  for t in range(delivery,288):
   if any(state['DA_online'][g][:288]):m.stop[g,t].fix(0)
   if state['DA_online'][g][t]:m.da_protection.add(m.online[g,t]==1)
 terms=[];ds=m._energy_inputs['thermal'];ts=range(delivery,delivery+24)
 for g,d in ds.items():
  for t in range(delivery,576):terms.append(sum(z['fee_yuan']*m.three[g].event.kind[t,k]for k,z in parameters['units'][g]['classes'].items())+d['shutdown_yuan'][t]*m.stop[g,t])
  for t in ts:
   energy=d['bid_base_mw']*policy['minimum_adjustable_prices'][g]['yuan_per_mwh']*m.online[g,t]+sum(v['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,v in d['segments'].items())
   energy+=sum((row['cost']-case['no_load_cost'][g])*m.three[g].event.kind[t-j,k]for k,z in parameters['units'][g]['classes'].items()for j,row in enumerate(z['rows'])if t>=j)
   energy+=sum((row['cost']-case['no_load_cost'][g])*m.stop[g,t-j]for j,row in enumerate(case['curves'][g]['shutdown'])if t>=j)
   terms.append(energy/12)
 for b in m.B:
  for t in ts:terms.append((-50*m.renewable_power[b,t]+policy['curtailment_penalty']['yuan_per_mwh']*m.renewable_curtailment[b,t])/12)
 for u,d,k in m.battery.SEG:
  for t in ts:terms.append(case['batteries'][u][d+'_curve']['prices_yuan_per_mwh'][k]*(1 if d=='discharge'else-1)*m.battery.segment[u,d,k,t]/12)
 m.total_cost.set_value(sum(terms));m._three_rt_scope=dict(safety_release=False,formal=False,prefix_variables=len(prefix),uniform_fees_replaced_not_added=True)
 return m

def build_rt(case,agc,parameters,policy,state):
 return configure_rt(build(case,agc,mode='three_state',parameters=parameters),case,parameters,policy,state)
