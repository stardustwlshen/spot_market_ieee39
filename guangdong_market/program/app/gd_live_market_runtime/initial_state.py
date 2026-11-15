"""Construct only a new-run t=0 physical state, without executing the DA plan."""
from copy import deepcopy
import math

def from_declared(case, previous):
 c=deepcopy(previous);thermal={};ages={};storage={}
 for g,z in case['energy_inputs']['thermal'].items():
  kind=z['initial_state_kind']
  if kind not in ('stable','off','offline'):raise ValueError('initial active trajectory requires an explicit state importer')
  age=z['initial_state_age_h'];online=kind=='stable';ages[g]=age
  thermal[g]=dict(phase='stable' if online else 'off',source='declared initial state at simulation origin; no DA execution',available_at=c['origin'],end_power_mw=z['initial_power_mw'],residual_up_ticks=max(0,math.ceil((z['min_up_h']-age)*4-1e-8)) if online else 0,residual_down_ticks=0 if online else max(0,math.ceil((z['min_down_h']-age)*4-1e-8)),min_up_basis='startup_begin',min_down_basis='shutdown_end',event_id=None,event_elapsed_ticks=0,remaining_curve=[])
 for g,z in case['batteries'].items():
  storage[g]={k:deepcopy(z[k]) for k in ('initial_energy_mwh','initial_power_mw','initial_mode','initial_mode_age_h','cycle_budget')}
  if 'initial_hour_direction' in z:storage[g]['initial_hour_direction']=z['initial_hour_direction']
 c.update(thermal=thermal,thermal_age_h=ages,storage=storage,cutoff=c['origin'],boundary_at=c['origin'],cutoff_tick=0,delivery_tick=0,executed_end_exclusive=0,issued_end_exclusive=0,predicted_issued_prefix=False,rt_clock_mode='provincial_15min_execution',initial_state_basis='declared_physical_state_not_DA_prefix')
 return c
