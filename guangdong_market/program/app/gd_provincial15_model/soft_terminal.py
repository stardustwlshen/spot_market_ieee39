"""RT-only energy target; physical SOC/rate/throughput bounds remain hard."""
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib,math
import pyomo.environ as p

def attach(model,case,*,begin,periods):
 path=Path(__file__).with_name('rt_terminal_target.json');cfg=json.loads(path.read_bytes())
 band=cfg['band_fraction_of_capacity'];penalty=cfg['penalty_yuan_per_mwh_deviation']
 if not (isinstance(band,(int,float)) and math.isfinite(band) and 0<=band<=1 and isinstance(penalty,(int,float)) and math.isfinite(penalty) and penalty>0):raise ValueError('invalid RT soft terminal settings')
 b=model.battery;b.c_terminal.deactivate()
 model.rt_target_under=p.Var(b.BAT,domain=p.NonNegativeReals)
 model.rt_target_over=p.Var(b.BAT,domain=p.NonNegativeReals)
 model.rt_target_rows=p.ConstraintList();items={};end=begin+timedelta(minutes=15*periods)
 for u,r in case['batteries'].items():
  at=datetime.fromisoformat(r['terminal_at']);remaining=max(0.,(at-end).total_seconds()/3600);eta=math.sqrt(r['roundtrip_efficiency']);width=band*r['energy_capacity_mwh'];target=r['terminal_energy_mwh']
  low=target-width-eta*r['charge_max_mw']*remaining
  high=target+width+r['discharge_max_mw']/eta*remaining
  model.rt_target_rows.add(b.energy[u,periods-1]+model.rt_target_under[u]>=low)
  model.rt_target_rows.add(b.energy[u,periods-1]-model.rt_target_over[u]<=high)
  items[u]=dict(target_at=at.isoformat(),target_mwh=target,band_mwh=width,window_end_at=end.isoformat(),lower_reachable_target_mwh=low,upper_reachable_target_mwh=high,penalty_yuan_per_mwh_deviation=penalty)
 model.rt_terminal_target_cost=p.Expression(expr=penalty*sum(model.rt_target_under[u]+model.rt_target_over[u]for u in b.BAT))
 report=dict(schema='gd.rt.soft_terminal.report.v1',settings=cfg,settings_path=str(path.resolve()),settings_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),resources=items,hard_physical_SOC_bounds_unchanged=True,not_operating_cost=True)
 return report

def record(bundle):
 from smm.gd_october_job_codec import loads
 # Bundle case uses the authenticated event codec, not arbitrary pickle.
 from v6_host import e
 case=e.unpack(bundle['case']);values=dict(zip(bundle['names']['variables'],bundle['SCED_response']['primal']))
 begin=datetime.fromisoformat(case['contract']['domain_start']);periods=len(case['energy_inputs']['horizon']['periods']);end=begin+timedelta(minutes=15*periods)
 cfg=json.loads(Path(__file__).with_name('rt_terminal_target.json').read_bytes());rows={}
 for u,r in case['batteries'].items():
  target=r['terminal_energy_mwh'];actual=values[f'battery.energy[{u},0]'];planned=values[f'battery.energy[{u},{periods-1}]'];under=values.get(f'rt_target_under[{u}]',0);over=values.get(f'rt_target_over[{u}]',0)
  rows[u]=dict(executed_first_end_mwh=actual,window_end_plan_mwh=planned,target_at=r['terminal_at'],target_mwh=target,executed_day_end_deviation_mwh=(actual-target)if periods==1 and end.isoformat()==r['terminal_at'] else None,planned_target_penalty_yuan=cfg['penalty_yuan_per_mwh_deviation']*(under+over),penalty_is_not_operating_cost=True)
 return dict(resources=rows,settings=cfg,executed_end_at=(begin+timedelta(minutes=15)).isoformat(),window_end_at=end.isoformat())
