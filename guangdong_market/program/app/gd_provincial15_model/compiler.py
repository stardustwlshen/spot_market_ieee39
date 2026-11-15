"""Source-bound pure joint48 physical build. No optimizer. Old case/raw evidence unchanged."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import sys,json,hashlib,math,re
import pyomo.environ as p
B=Path(__file__).resolve().parents[2]/'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112';ROOT=B.parent;DEP=ROOT/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src';sys.path.insert(0,str(DEP))
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
 from .thermal_physical import build_free_scuc
 seed=Path(seed).resolve();out=Path(out).resolve();c=read(carry)
 for f,h in c['source_pins'].items():assert sha(f)==h,('carry source drift',f)
 original=loads((seed/'case.json').read_bytes());r=read(seed/'request.json')
 from .day_contract import apply,plan_at
 declaration=read(c['day_declaration_path']);assert sha(c['day_declaration_path'])==c['day_declaration_sha256'];original,dayproof=apply(original,c,declaration)
 assert c['seed_case_sha256']==sha(seed/'case.json') and c['seed_request_sha256']==sha(seed/'request.json')
 from .adapter import convert_full_case
 original=convert_full_case(original)
 start=c['delivery_tick'];assert type(start)is int and 0<=start<96,'single-day RT start must be 0..95';n=min(8,96-start);origin=datetime.fromisoformat(r['origin']);begin=origin+timedelta(minutes=15*start);end=begin+timedelta(minutes=15*n)
 zero=c.get('rt_clock_mode')=='provincial_15min_execution'
 assert zero,'provincial grid marker required'
 assert start==c['cutoff_tick']+(0 if zero else 3) and c['boundary_at']==begin.isoformat() and start>=(0 if zero else 3) and start+n<=len(original['energy_inputs']['load_mw'])//len(original['energy_inputs']['buses'])
 from gd_forecast_inputs import apply_forecast
 original=apply_forecast(original,seed,start)
 from .penalty_policy import apply_dispatch
 case=apply_dispatch(slice_time(original,start,n));e=case['energy_inputs'];e['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,.25,.25,'delivery',t==0)for t in range(n)),'real_time','provincial fifteen-minute eight-step research window');e['renewable_mode']='dispatchable_bus_aggregate'
 bs=c['thermal'];assert set(bs)==set(e['thermal']) and set(c['storage'])==set(case['batteries'])
 for g,d in e['thermal'].items():
  z=bs[g];d.update(initial_power_mw=z['end_power_mw'],initial_online=int(z['phase']!='off'),initial_state_kind='stable'if z['phase']=='stable'else'off'if z['phase']=='off'else'research_assumption_trajectory',initial_state_age_h=c['thermal_age_h'][g])
 for u,z in case['batteries'].items():
  z.update(c['storage'][u])
 bc=case['battery_contract'];bc.update(schema='gd.short.battery.research.v1',boundary_at=begin.isoformat(),cutoff=c['cutoff'],terminal_policy='exact_declared_target_if_present_else_necessary_reachability_envelope; residual_mode_exported')
 sid=bc['storage']['battery_parameters_source_id'];artifact=json.dumps(dict(batteries=case['batteries'],pumped_storage=bc['storage']['pumped_storage']),sort_keys=True,allow_nan=False);bc['sources'][sid].update(artifact=artifact,sha256=hashlib.sha256(artifact.encode()).hexdigest(),available_at=c['cutoff'])
 case['contract']=dict(schema='gd.short.physical.contract.v1',mode='research',origin=r['origin'],cutoff=c['cutoff'],domain_start=begin.isoformat(),publication_at=original['contract']['publication_at'],clock_interpretation_source='explicit research delivery anchor; official near/far zero-hour interpretation unresolved',near_far_mode='provincial8',rt_clock_mode=c.get('rt_clock_mode','issued_prefix_15min'))
 agc=slice_time({g:{**z,**{side:{q:sum(z[side][str(t)] for t in range(q*3,q*3+3))/3 for q in range(len(z[side])//3)} for side in ('up_mw','down_mw')}}for g,z in read(seed/'agc.json').items()},start,n);policy=read(seed/'policy.json')
 binding=None
 if agc_projection is not None:
  config=read(agc_projection);assert sha(config['receipt_path'])==config['receipt_sha256'],'AGC receipt bytes mismatch'
  import importlib.util
  module_path=ROOT/'guangdong_short_hour_agc_receipt_v2_20261112/project.py';spec=importlib.util.spec_from_file_location('_fixed_AGC_remaining',module_path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  result=mod.project(config['receipt_path'],case,agc,carry,seed,**{k:config[k]for k in ('expected_reference_plan_sha256','expected_receipt_context_sha256','zone','hour_start')});agc=result['agc'];binding=result['consumption_binding']
 ps_path=c.get('pumped_storage_declaration_path')
 if not ps_path:raise ValueError('sourced pumped-storage declarations with new DA reference required')
 assert sha(ps_path)==c['pumped_storage_declaration_sha256'],'PS declaration drift'
 ps_decl=read(ps_path)
 for z in ps_decl.values():
  for key in ('da_upper_m3','da_lower_m3'):
   if key in z:z[key]={int(k):v for k,v in z[key].items()}
 ps_decl=slice_time(ps_decl,start,n)
 inv=bc['storage']['pumped_storage']['inventory']
 if set(c.get('pumped_storage',{}))!=set(inv):raise ValueError('actual pumped boundary required')
 for station,state in c['pumped_storage'].items():
  inv[station].update(upper_m3=state['upper_m3'],lower_m3=state['lower_m3'],net_power_mw=state['net_power_mw'],modes={u:v['mode']for u,v in state['units'].items()})
  ps_decl[station]['initial_units']={u:{k:v[k]for k in ('gen_starts_used','pump_starts_used','last_active_mode','opposite_mode_block_steps','mode_age_steps','gen_mw','pump_mw')}for u,v in state['units'].items()}
 artifact=json.dumps(dict(batteries=case['batteries'],pumped_storage=bc['storage']['pumped_storage']),sort_keys=True,allow_nan=False);bc['sources'][sid].update(artifact=artifact,sha256=hashlib.sha256(artifact.encode()).hexdigest())
 case['pumped_storage_declarations']=ps_decl
 m=build_free_scuc(**{k:case[k]for k in ('energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost')},agc_awards=agc,boundary_states=bs,pumped_storage_declarations=ps_decl)
 m.short_notices=p.ConstraintList();m.da_protection=p.ConstraintList();due={}
 for g in m.G:
  lead=round(case['curves'][g]['notice_h']*4);due[g]=[]
  for t in range(n):
   absolute=start+t;nt=absolute-lead
   if nt<c['cutoff_tick']:
    issued=r['preorigin_notices'][g].get(str(nt),0)if nt<0 else c['notice_values'][g].get(str(nt),0);m.short_notices.add(m.start[g,t]==issued)
   at=begin+timedelta(minutes=15*t);date=at.date().isoformat()
   m.da_protection.add(m.online[g,t]==int(bool(plan_at(declaration,g,at.isoformat()))))
  due[g]=[{'notice_tick':int(nt),'target_tick':int(nt)+lead,'issued':True}for nt,v in r['preorigin_notices'][g].items()if v]
 from .soft_terminal import attach as attach_soft_terminal
 terminal_report=attach_soft_terminal(m,case,begin=begin,periods=n)
 terms=[];ds=m._energy_inputs['thermal']
 for g,d in ds.items():
  for t in range(n):
   terms.extend([d['startup_yuan'][t]*m.start[g,t],d['shutdown_yuan'][t]*m.stop[g,t]])
   energy=d['bid_base_mw']*policy['minimum_adjustable_prices'][g]['yuan_per_mwh']*m.online[g,t]+sum(z['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,z in d['segments'].items())
   for kind,var in [('startup',m.start),('shutdown',m.stop)]:energy+=sum((row['cost']-case['no_load_cost'][g])*var[g,t-j]for j,row in enumerate(case['curves'][g][kind])if t>=j)
   terms.append(energy/4)
 for bus in m.B:
  for t in range(n):terms.append((-50*m.renewable_power[bus,t]+case['curtailment_penalty_policy']['value']*m.renewable_curtailment[bus,t])/4)
 terms.append(sum((row['cost']-case['no_load_cost'][g])/4 for g,z in bs.items() for row in z['remaining_curve'][:n]));terms.append(m.battery.bid_cost);terms.append(m.pumped.cost);terms.append(m.rt_terminal_target_cost)
 from .objective_components import append_slack_penalties
 retained_penalty_names=append_slack_penalties(m,terms)
 m.total_cost.set_value(sum(terms))
 q,vs,cs=serialize_model(m,validate_options(dict(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;q['warmstart']=False
 names=dict(variables=[v.name for v in vs],constraints=[v.name for v in cs])
 from .cycle_tail_cut import append_cycle_tail_rows
 cycle_tail_proof=dict(schema='gd.rt.soft_target.cycle_tail.v1',rows_added=0,reason='day-end target is soft; actual daily cycle constraints stay hard')
 out.mkdir(exist_ok=False)
 (out/'OBJECTIVE_COMPONENTS.json').write_text(json.dumps(dict(retained_slack_penalty_expressions=retained_penalty_names,renewable_curtailment='included once in commercial timeline terms',soft_soc_penalty='included once; optimization only not operating cost'),indent=2))
 (out/'RT_SOFT_TERMINAL.json').write_text(json.dumps(terminal_report,indent=2))
 (out/'CYCLE_TAIL_PROOF.json').write_text(json.dumps(cycle_tail_proof,indent=2))
 for name,z in [('template',q),('names',names),('BOUNDARY',dict(thermal=bs,storage=c['storage'],issued_notices=due)),('CARRY',c),('DAY_CONTRACT_REVIEW',dayproof)]:
  with(out/(name+'.json')).open('x')as f:json.dump(z,f,allow_nan=False,sort_keys=True)
 from dataclasses import asdict
 serial=deepcopy(case);serial['energy_inputs']['horizon']=asdict(e['horizon']);(out/'derived_case.json').write_bytes(dumps(serial));(out/'agc.json').write_text(json.dumps(agc,sort_keys=True))
 summary=dict(status='prepared_short39',columns=len(vs),rows=len(q['rows']),integers=sum(v['kind']!='C'for v in q['variables']),domain_start=begin.isoformat(),domain_end=end.isoformat(),formal=False,safety_release=False,source_pins={str(p):sha(p)for p in [*sorted(B.glob('*.py')),*sorted(Path(__file__).parent.glob('*.py')),*sorted(seed.glob('*.json')),Path(carry),Path(ps_path)]},carry_sha256=sha(carry),native_calls=0)
 summary['source_pins'][terminal_report['settings_path']]=terminal_report['settings_sha256']
 summary['source_pins'][str((out/'RT_SOFT_TERMINAL.json').resolve())]=sha(out/'RT_SOFT_TERMINAL.json')
 if binding is not None:
  (out/'AGC_CONSUMPTION.json').write_text(json.dumps(binding,indent=2));summary['source_pins'].update(binding['source_pins']);summary['source_pins'][str(Path(agc_projection).resolve())]=sha(agc_projection);summary['AGC_consumption_sha256']=sha(out/'AGC_CONSUMPTION.json')
 (out/'SUMMARY.json').write_text(json.dumps(summary,indent=2));return summary
if __name__=='__main__':
 import argparse
 a=argparse.ArgumentParser();a.add_argument('--seed',required=True);a.add_argument('--carry',required=True);a.add_argument('--output',required=True);z=a.parse_args();print(json.dumps(build(z.seed,z.carry,z.output)))
