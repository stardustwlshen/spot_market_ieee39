"""Source-bound pure joint48 physical build. No optimizer. Old case/raw evidence unchanged."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import sys,json,hashlib,math,re
import pyomo.environ as p
B=Path(__file__).resolve().parent;ROOT=B.parent;DEP=ROOT/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src';sys.path.insert(0,str(DEP))
from smm.gd_october_job_codec import loads,dumps
from smm.gd_horizon import EnergyHorizon,EnergyPeriod
from smm.highs_solver import serialize_model,validate_options
from smm.gd_r23_equivalent_compression import audit
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def slice_time(z,start,n):
 if isinstance(z,dict):
  if z and all(type(k)is int for k in z):
   if all(t in z for t in range(start,start+n)):return {t:deepcopy(z[start+t])for t in range(n)}
  if z and all(isinstance(k,tuple)and len(k)==2 and type(k[1])is int for k in z):return {(b,t-start):deepcopy(v)for (b,t),v in z.items()if start<=t<start+n}
  return {k:slice_time(v,start,n)for k,v in z.items()}
 if isinstance(z,tuple):return tuple(slice_time(v,start,n)for v in z)
 if isinstance(z,list):return [slice_time(v,start,n)for v in z]
 return deepcopy(z)
def end_power(case,values,g,t):
 d=case['curves'][g];mean=end=0.
 for kind,stem in [('startup','start'),('shutdown','stop')]:
  for j,row in enumerate(d[kind]):
   if t>=j:
    x=values[f'{stem}[{g},{t-j}]'];mean+=row['mean']*x;end+=row['end']*x
 return values[f'power[{g},{t}]']-mean+end

def build(seed, carry, out, agc_projection=None):
 from thermal_physical import build_free_scuc
 seed=Path(seed).resolve();out=Path(out).resolve();c=read(carry)
 for f,h in c['source_pins'].items():assert sha(f)==h,('carry source drift',f)
 original=loads((seed/'case.json').read_bytes());r=read(seed/'request.json')
 from day_contract import apply,plan_at
 declaration=read(c['day_declaration_path']);assert sha(c['day_declaration_path'])==c['day_declaration_sha256'];original,dayproof=apply(original,c,declaration)
 assert c['seed_case_sha256']==sha(seed/'case.json') and c['seed_request_sha256']==sha(seed/'request.json')
 start=c['delivery_tick'];n=48;origin=datetime.fromisoformat(r['origin']);begin=origin+timedelta(minutes=5*start);end=begin+timedelta(minutes=5*n)
 assert start==c['cutoff_tick']+3 and c['boundary_at']==begin.isoformat() and start>=3 and start+n<=len(original['energy_inputs']['load_mw'])//len(original['energy_inputs']['buses'])
 case=slice_time(original,start,n);e=case['energy_inputs'];e['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,1/12,1/12,'delivery',t<3)for t in range(n)),'component','explicit joint48 delivery anchor research');e['renewable_mode']='dispatchable_bus_aggregate'
 bs=c['thermal'];assert set(bs)==set(e['thermal']) and set(c['storage'])==set(case['batteries'])
 for g,d in e['thermal'].items():
  z=bs[g];d.update(initial_power_mw=z['end_power_mw'],initial_online=int(z['phase']!='off'),initial_state_kind='stable'if z['phase']=='stable'else'off'if z['phase']=='off'else'research_assumption_trajectory',initial_state_age_h=c['thermal_age_h'][g])
 for u,z in case['batteries'].items():
  z.update(c['storage'][u])
 bc=case['battery_contract'];bc.update(schema='gd.short.battery.research.v1',boundary_at=begin.isoformat(),cutoff=c['cutoff'],terminal_policy='exact_declared_target_if_present_else_necessary_reachability_envelope; residual_mode_exported')
 sid=bc['storage']['battery_parameters_source_id'];artifact=json.dumps(dict(batteries=case['batteries'],pumped_storage=bc['storage']['pumped_storage']),sort_keys=True,allow_nan=False);bc['sources'][sid].update(artifact=artifact,sha256=hashlib.sha256(artifact.encode()).hexdigest(),available_at=c['cutoff'])
 case['contract']=dict(schema='gd.short.physical.contract.v1',mode='research',origin=r['origin'],cutoff=c['cutoff'],domain_start=begin.isoformat(),publication_at=original['contract']['publication_at'],clock_interpretation_source='explicit research delivery anchor; official near/far zero-hour interpretation unresolved',near_far_mode='joint48_equal_energy_research')
 agc=slice_time({g:{**z,'up_mw':{int(t):v for t,v in z['up_mw'].items()},'down_mw':{int(t):v for t,v in z['down_mw'].items()}}for g,z in read(seed/'agc.json').items()},start,n);policy=read(seed/'policy.json')
 binding=None
 if agc_projection is not None:
  config=read(agc_projection);assert sha(config['receipt_path'])==config['receipt_sha256'],'AGC receipt bytes mismatch'
  import importlib.util
  module_path=ROOT/'guangdong_short_hour_agc_receipt_v2_20261112/project.py';spec=importlib.util.spec_from_file_location('_fixed_AGC_remaining',module_path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  result=mod.project(config['receipt_path'],case,agc,carry,seed,**{k:config[k]for k in ('expected_reference_plan_sha256','expected_receipt_context_sha256','zone','hour_start')});agc=result['agc'];binding=result['consumption_binding']
 m=build_free_scuc(**{k:case[k]for k in ('energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost')},agc_awards=agc,boundary_states=bs)
 m.short_notices=p.ConstraintList();m.da_protection=p.ConstraintList();due={}
 for g in m.G:
  lead=round(case['curves'][g]['notice_h']*12);due[g]=[]
  for t in range(n):
   absolute=start+t;nt=absolute-lead
   if nt<c['cutoff_tick']:
    issued=r['preorigin_notices'][g].get(str(nt),0)if nt<0 else c['notice_values'][g].get(str(nt),0);m.short_notices.add(m.start[g,t]==issued)
   at=begin+timedelta(minutes=5*t);date=at.date().isoformat()
   if plan_at(declaration,g,at.isoformat()):m.da_protection.add(m.online[g,t]==1)
   if any(declaration['days'][date]['artifact']['thermal_online'][g]):m.stop[g,t].fix(0)
  due[g]=[{'notice_tick':int(nt),'target_tick':int(nt)+lead,'issued':True}for nt,v in r['preorigin_notices'][g].items()if v]
 m.day_future_targets=p.ConstraintList()
 for bound in dayproof['future_target_envelopes']:
  u=bound['unit'];z=case['batteries'][u];eta=math.sqrt(z['roundtrip_efficiency']);hours=bound['remaining_hours'];target=bound['target_mwh'];m.day_future_targets.add(m.battery.energy[u,n-1]>=target-eta*z['charge_max_mw']*hours);m.day_future_targets.add(m.battery.energy[u,n-1]<=target+z['discharge_max_mw']/eta*hours)
 terms=[];ds=m._energy_inputs['thermal']
 for g,d in ds.items():
  for t in range(n):
   terms.extend([d['startup_yuan'][t]*m.start[g,t],d['shutdown_yuan'][t]*m.stop[g,t]])
   energy=d['bid_base_mw']*policy['minimum_adjustable_prices'][g]['yuan_per_mwh']*m.online[g,t]+sum(z['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,z in d['segments'].items())
   for kind,var in [('startup',m.start),('shutdown',m.stop)]:energy+=sum((row['cost']-case['no_load_cost'][g])*var[g,t-j]for j,row in enumerate(case['curves'][g][kind])if t>=j)
   terms.append(energy/12)
 for bus in m.B:
  for t in range(n):terms.append((-50*m.renewable_power[bus,t]+policy['curtailment_penalty']['yuan_per_mwh']*m.renewable_curtailment[bus,t])/12)
 terms.append(sum((row['cost']-case['no_load_cost'][g])/12 for g,z in bs.items() for row in z['remaining_curve'][:n]));terms.append(m.battery.bid_cost);m.total_cost.set_value(sum(terms))
 q,vs,cs=serialize_model(m,validate_options(dict(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;q['warmstart']=False
 names=dict(variables=[v.name for v in vs],constraints=[v.name for v in cs])
 from cycle_tail_cut import append_cycle_tail_rows
 q,names,cycle_tail_proof=append_cycle_tail_rows(q,names,case,declaration)
 out.mkdir(exist_ok=False)
 (out/'CYCLE_TAIL_PROOF.json').write_text(json.dumps(cycle_tail_proof,indent=2))
 for name,z in [('template',q),('names',names),('BOUNDARY',dict(thermal=bs,storage=c['storage'],issued_notices=due)),('CARRY',c),('DAY_CONTRACT_REVIEW',dayproof)]:
  with(out/(name+'.json')).open('x')as f:json.dump(z,f,allow_nan=False,sort_keys=True)
 from dataclasses import asdict
 serial=deepcopy(case);serial['energy_inputs']['horizon']=asdict(e['horizon']);(out/'derived_case.json').write_bytes(dumps(serial));(out/'agc.json').write_text(json.dumps(agc,sort_keys=True))
 summary=dict(status='prepared_short39',columns=len(vs),rows=len(q['rows']),integers=sum(v['kind']!='C'for v in q['variables']),domain_start=begin.isoformat(),domain_end=end.isoformat(),formal=False,safety_release=False,source_pins={str(p):sha(p)for p in [*sorted(B.glob('*.py')),*sorted(seed.glob('*.json')),Path(carry)]},carry_sha256=sha(carry),native_calls=0)
 if binding is not None:
  (out/'AGC_CONSUMPTION.json').write_text(json.dumps(binding,indent=2));summary['source_pins'].update(binding['source_pins']);summary['source_pins'][str(Path(agc_projection).resolve())]=sha(agc_projection);summary['AGC_consumption_sha256']=sha(out/'AGC_CONSUMPTION.json')
 (out/'SUMMARY.json').write_text(json.dumps(summary,indent=2));return summary
if __name__=='__main__':
 import argparse
 a=argparse.ArgumentParser();a.add_argument('--seed',required=True);a.add_argument('--carry',required=True);a.add_argument('--output',required=True);z=a.parse_args();print(json.dumps(build(z.seed,z.carry,z.output)))
