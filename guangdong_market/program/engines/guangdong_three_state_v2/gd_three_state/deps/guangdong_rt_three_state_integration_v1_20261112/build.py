from pathlib import Path
import sys,importlib.util,copy
B=Path(__file__).resolve().parent;R=B.parent;V=R/'guangdong_rt_recommit_program_v1_20261112/vendor'
sys.path.insert(0,str(V/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_horizon import EnergyHorizon,EnergyPeriod

def uniform_module():
 sp=importlib.util.spec_from_file_location('frozen_uniform',V/'guangdong_r56_market_builder_20261112/physical_builder.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

def build(case,agc,*,mode='uniform',parameters=None):
 c=copy.deepcopy(case);e=c['energy_inputs'];e['renewable_mode']='dispatchable_bus_aggregate';h=e['horizon']
 if isinstance(h,dict):e['horizon']=EnergyHorizon(**dict(h,periods=tuple(EnergyPeriod(**x)for x in h['periods'])))
 kwargs={k:c[k]for k in ('energy_inputs','reserve','curves','contract','batteries','battery_contract','no_load_cost')};kwargs['agc_awards']=agc
 if mode=='uniform':
  if parameters is not None:raise ValueError('uniform must not silently consume three-state parameters')
  return uniform_module().build_free_scuc(**kwargs)
 if mode!='three_state' or parameters is None:raise ValueError('explicit mode and contract required')
 from physical_three import build_free_scuc
 return build_free_scuc(**kwargs,three_state=parameters)
