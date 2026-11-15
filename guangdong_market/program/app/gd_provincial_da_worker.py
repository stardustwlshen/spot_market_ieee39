"""New 96x15min source-bound research DA; never overwrite historical DA outputs."""
import pathlib,sys,json,hashlib,time,gzip,copy,importlib.util
from datetime import datetime,timedelta
from dataclasses import asdict
P=pathlib.Path(__file__).resolve().parents[1];RT=P/'engines/guangdong_short_unified_v6/rt';V=RT/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src';sys.path[:0]=[str(P/'app'),str(V)]
from smm.gd_october_job_codec import loads,dumps
from smm.gd_horizon import EnergyHorizon,EnergyPeriod
from smm.highs_solver import serialize_model,validate_options
from gd_provincial15_model.adapter import convert_full_case
from gd_provincial15_model.compiler import slice_time
from gd_provincial15_model.thermal_physical import build_free_scuc
import pyomo.environ as py
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--source-base',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
OUT=pathlib.Path(args.output).resolve();OUT.mkdir(parents=True,exist_ok=False);seed=pathlib.Path(args.source_base).resolve()/'seed';source=loads((seed/'case.json').read_bytes());full=convert_full_case(source);case=slice_time(full,0,96);case.pop('_grid_transform',None);case['energy_inputs']['renewable_mode']='dispatchable_bus_aggregate'
from gd_provincial15_model.representative98 import extend
case,representative_mapping=extend(case)
origin=source['contract']['day_start'];cutoff=source['contract']['publication_at'] # honest research source availability, not official DA cutoff claim
case['energy_inputs']['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,.25,.25,'delivery' if t<96 else 'representative',t<96) for t in range(98)),'day_ahead_reference_98','Guangdong provincial formula indexed 98','same-day extrema inputs reused for D+1; compressed index dynamics not actual adjacent quarter hours')
case['contract']=dict(schema='gd.provincial.research.day_ahead.v1',mode='research',origin=origin,cutoff=cutoff,domain_start=origin,publication_at=cutoff,rt_clock_mode='provincial_15min_day_ahead')
boundary={}
for g,d in case['energy_inputs']['thermal'].items():
 phase='stable' if d['initial_online'] else 'off'
 boundary[g]=dict(phase=phase,end_power_mw=d['initial_power_mw'],remaining_curve=[],event_id=None,event_elapsed_ticks=0,residual_up_ticks=max(0,__import__('math').ceil((d['min_up_h']-d['initial_state_age_h'])*4)) if phase=='stable' else 0,residual_down_ticks=max(0,__import__('math').ceil((d['min_down_h']-d['initial_state_age_h'])*4)) if phase=='off' else 0,min_up_basis='startup_begin',min_down_basis='shutdown_end',available_at=cutoff,source='archived declared initial state; synthetic study, not measured state')
bc=case['battery_contract'];bc.update(boundary_at=origin,cutoff=cutoff)
sid=bc['storage']['battery_parameters_source_id'];bc['sources'][sid]['artifact']=json.dumps(dict(batteries=case['batteries'],pumped_storage=bc['storage']['pumped_storage']),sort_keys=True)
# AGC source is explicitly inherited research input pending separate DA co-optimization.
agc0=json.loads((seed/'agc.json').read_text());agc={g:{**z,**{side:{q:sum(z[side][str(t)] for t in range(q*3,q*3+3))/3 for q in range(96)} for side in ['up_mw','down_mw']}} for g,z in agc0.items()}
for z in agc.values():
 for side in ['up_mw','down_mw']:
  for rep in representative_mapping:z[side][rep['tick']]=z[side][rep['source_day_quarter']]
# Declarations supplied by the dedicated PS source adapter; never infer official bids.
from gd_provincial15_model.pumped_storage import attach
ps_path=OUT/'PUMPED_DECLARATIONS.json'
from gd_provincial15_model.pumped_declarations import day_ahead
ps_path.write_text(json.dumps(day_ahead(case['battery_contract']['storage']['pumped_storage']['inventory'],bid_source=P/'src/smm/bids.py'),indent=2))
ps=json.loads(ps_path.read_text())
# normalize JSON local tick maps if needed
for d in ps.values():
 for key in ['da_upper_m3','da_lower_m3']:
  if key in d:d[key]={int(k):v for k,v in d[key].items()}
from gd_provincial15_model.penalty_policy import apply_dispatch
case=apply_dispatch(case)
m=build_free_scuc(**{k:case[k] for k in ['energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost']},agc_awards=agc,boundary_states=boundary,pumped_storage_declarations=ps)
q,vs,cs=serialize_model(m,validate_options(dict(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;q['warmstart']=False
names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]);(OUT/'names.json').write_text(json.dumps(names));(OUT/'case.json').write_bytes(dumps({**case,'energy_inputs':{**case['energy_inputs'],'horizon':asdict(case['energy_inputs']['horizon'])}}));(OUT/'boundary.json').write_text(json.dumps(boundary));(OUT/'agc.json').write_text(json.dumps(agc));
(OUT/'SOURCE.json').write_text(json.dumps(dict(source_pins={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [seed/'case.json',seed/'agc.json',ps_path,*list((P/'app/gd_provincial15_model').glob('*.py'))]},assumptions=['historical source forecasts, no new measured data','source availability cutoff20:30 is research replay not official DA gate','AGC inherited sourced research awards'],grid_minutes=15,periods=98,executed_periods=96,representative_mapping=representative_mapping),indent=2))
worker=RT/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_r65_three_state_startup_20261112/standard_counted_worker.py';spec=importlib.util.spec_from_file_location('da_native',worker);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
def solve(q,stage):
 q=copy.deepcopy(q);q['deadline']=time.perf_counter()+3900;start=time.monotonic();(OUT/(stage+'_request.json.gz')).write_bytes(gzip.compress(json.dumps(q).encode()));r=w.solve(q);(OUT/(stage+'_response.json.gz')).write_bytes(gzip.compress(json.dumps(r).encode()));print(stage,r['status'],time.monotonic()-start,flush=True);
 if not r['has_primal']:raise ValueError(stage+'没有可行解（'+str(r['status'])+'）；当前输入与模型约束无法同时满足，未生成或复用旧出清结果。已保留原始模型和求解回执用于定位。')
 return r
u=solve(q,'SCUC');lp=copy.deepcopy(q)
for v,x in zip(lp['variables'],u['primal']):
 if v['kind']!='C':v.update(kind='C',lb=round(x),ub=round(x))
s=solve(lp,'SCED');assert s['dual_valid']
from gd_network_violations import summarize
(OUT/'NETWORK_VIOLATIONS.json').write_text(json.dumps(summarize(names,s),ensure_ascii=False,indent=2))
for v,x in zip(vs,s['primal']):v.set_value(x,skip_validation=True)
# export hook provided by PS specialist follows actual raw native witness.
try:
 from gd_provincial15_model.physical_export import export_physical
 (OUT/'physical_solution.json').write_text(json.dumps(export_physical(m,origin),indent=2))
except ImportError:pass
(OUT/'COMPLETE.json').write_text(json.dumps(dict(status='native_pair_solved',SCUC_status=u['status'],SCED_status=s['status'],mip_gap=u['native_mip_gap'],columns=len(vs),rows=len(cs),physical_cells_exported=(OUT/'physical_solution.json').exists()),indent=2))
