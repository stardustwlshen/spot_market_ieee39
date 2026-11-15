import ast, math
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
from types import SimpleNamespace
B=Path(__file__).resolve().parents[1]/'app'
def loadfn(path,name,ns):
 tree=ast.parse(path.read_text());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns);return ns[name]
def need(ok,msg):
 if not ok:raise ValueError(msg)
def test_first_one_15min_storage_state_not_future_plan():
 f=B/'lifecycle_runtime/guangdong_normalized_event_handoff_v1_20261113/normalization.py'
 fn=loadfn(f,'replay',dict(deepcopy=deepcopy,math=math,timedelta=timedelta,TOL=1e-10,e=SimpleNamespace(need=need,stamp=datetime.fromisoformat)))
 date='2026-11-10';origin=date+'T00:00:00+08:00'
 bat=dict(initial_energy_mwh=50,roundtrip_efficiency=1,initial_mode='discharge',initial_mode_age_h=1,cycle_budget={date:{'used_internal_mwh':0}},energy_min_mwh=0,energy_max_mwh=100)
 case=dict(contract={'domain_start':origin},batteries={'B':bat})
 vals={f'battery.{k}[B,0]':v for k,v in dict(charge=0,discharge=20,charge_mode=0,discharge_mode=1,energy=45).items()}
 vals['battery.hour_direction[B,'+origin+']']=1
 # Deliberately absurd future values must never enter executed carry.
 vals.update({f'battery.{k}[B,1]':999 for k in ('energy','charge','discharge')})
 out,_,_,proof=fn(case,vals,lambda *a:[])
 assert out['B']['initial_energy_mwh']==45
 assert out['B']['initial_mode_age_h']==1.25
 assert out['B']['cycle_budget'][date]['used_internal_mwh']==5
 assert len(proof['B']['cells'])==1

def test_initial_state_residual_duration_15min():
 fn=loadfn(B/'gd_live_market_runtime/initial_state.py','from_declared',dict(deepcopy=deepcopy,math=math))
 case={'energy_inputs':{'thermal':{'G':dict(initial_state_kind='stable',initial_state_age_h=1,min_up_h=3,min_down_h=2,initial_power_mw=60)}},'batteries':{}}
 c=fn(case,{'origin':'2026-11-10T00:00:00+08:00'})
 assert c['thermal']['G']['residual_up_ticks']==8
 assert c['delivery_tick']==0 and c['rt_clock_mode']=='provincial_15min_execution'
