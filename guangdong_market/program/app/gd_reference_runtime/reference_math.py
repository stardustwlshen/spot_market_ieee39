"""Portable pure mathematics for the explicit first-D+1 reference research profile.
No authentication, publication, acceptance, filesystem writes or native calls.
Caller MUST obtain inputs from registered accepted-DA/H24 source capabilities;
these functions do not turn user JSON/hash claims into source authority.
"""
from copy import deepcopy
from datetime import datetime,timedelta
import math,re
PROFILE='D1_fixed_original_DA_commitment_H24_only_reference_revision_RESEARCH_v1'
def need(ok,msg):
 if not ok:raise ValueError(msg)
def prepare_boundary(case,da_values,receipt,*,origin,case_sha256,start_tick=288,cells=48):
 day=datetime.fromisoformat(origin);start=day+timedelta(minutes=5*start_tick);cut=start-timedelta(minutes=30);thermal=case['energy_inputs']['thermal'];units=set(thermal)
 need(start_tick==288 and cells==48,'bounded first D+1 48-cell research profile only')
 need(len(units)==5 and units==set(receipt['result']['awards_mw']),'same five originally qualified units; no added resources')
 need(receipt['context']['seed_case_sha256']==case_sha256,'receipt/case identity mismatch')
 need(receipt['context']['as_of']==cut.isoformat()and receipt['context']['hour_start']==start.isoformat(),'H24 causal issue clock')
 need(receipt['result']['binding_end']==(start+timedelta(hours=1)).isoformat(),'H24 one hour only')
 need(all(v==10 for v in receipt['result']['awards_mw'].values()),'original five by10 MW preserved')
 need(all(da_values[f'stable[{g},{t}]']==1 for g in units for t in range(start_tick+cells)),'unknown/activity/age state needs another sourced adapter')
 states={};ages={};storage={}
 for g,d in thermal.items():states[g]=dict(end_power_mw=da_values[f'power[{g},{start_tick-1}]'],phase='stable',remaining_curve=[],residual_up_ticks=0,residual_down_ticks=0);ages[g]=d['initial_state_age_h']+start_tick/12
 for u,d in case['batteries'].items():
  need(datetime.fromisoformat(d['terminal_at'])==start,'only original boundary target; never invent D+2 target')
  need(abs(da_values[f'battery.energy[{u},{start_tick-1}]']-d['terminal_energy_mwh'])<=1e-7,'original expired target must be already satisfied')
  mode=lambda t:'charge'if da_values[f'battery.charge_mode[{u},{t}]']>.5 else'discharge'if da_values[f'battery.discharge_mode[{u},{t}]']>.5 else'idle'
  md=mode(start_tick-1);age=0
  for t in range(start_tick-1,-1,-1):
   if mode(t)!=md:break
   age+=1
  eta=math.sqrt(d['roundtrip_efficiency']);budgets=deepcopy(d['cycle_budget'])
  for date,budget in budgets.items():
   ts=[t for t in range(start_tick)if(day+timedelta(minutes=5*t)).date().isoformat()==date]
   if ts:budget['used_internal_mwh']+=math.fsum((eta*da_values[f'battery.charge[{u},{t}]']+da_values[f'battery.discharge[{u},{t}]']/eta)/12 for t in ts)
   else:need(budget['used_internal_mwh']==0,'future dated throughput cannot preexist')
  storage[u]=dict(initial_energy_mwh=da_values[f'battery.energy[{u},{start_tick-1}]'],initial_power_mw=da_values[f'battery.discharge[{u},{start_tick-1}]']-da_values[f'battery.charge[{u},{start_tick-1}]'],initial_mode=md,initial_mode_age_h=age/12,initial_hour_direction=0,cycle_budget=budgets)
 agc={g:{side:{str(t):10. if t<12 else 0. for t in range(cells)}for side in ('up_mw','down_mw')}for g in units}
 return dict(profile=PROFILE,thermal=states,thermal_age_h=ages,storage=storage,agc_numeric=agc,origin=origin,cutoff=cut.isoformat(),boundary_at=start.isoformat(),start_tick=start_tick,cells=cells,unissued_following_hours='no_additional_reserved_capacity_research_boundary_not_award',original_D0_unchanged=True,new_D2_terminal=False,source_authentication_performed=False,native_calls=0,publication=False)
def fix_original_da_integers(model,names,da_values,*,start_tick=288):
 out=deepcopy(model);mapping=[]
 for name,column in zip(names['variables'],out['variables']):
  if column['kind']=='C':continue
  m=re.search(r',(\d+)\]$',name);key=name[:m.start()]+','+str(int(m[1])+start_tick)+']'if m else name;need(key in da_values,'missing original DA integer '+key);v=da_values[key];need(abs(v-round(v))<=1e-8,'noninteger original DA source');column.update(kind='C',lb=float(round(v)),ub=float(round(v)));mapping.append(dict(column=name,source_name=key,value=v))
 need(out['rows']==model['rows']and out['objective']==model['objective'],'integer fixation cannot change physical math')
 return out,mapping

def thermal_overlay(original_physical,reference_values,agc,units,*,start_tick=288,cells=48):
 need(start_tick==288 and cells==48,'only declared D+1 reference scope');out=deepcopy(original_physical)
 for j in range(cells):
  for g in units:
   need(reference_values[f'stable[{g},{j}]']==1 and reference_values[f'start[{g},{j}]']==reference_values[f'stop[{g},{j}]']==0,'fixed original stable commitment required')
   for side in ('up_mw','down_mw'):need(agc[g][side][str(j)]==(10. if j<12 else 0.),'H24 only, no future hourly award')
   z=out[start_tick+j]['thermal'][g];z['mean_mw']=reference_values[f'power[{g},{j}]'];z['agc_up_reserved_mw']=agc[g]['up_mw'][str(j)];z['agc_down_reserved_mw']=agc[g]['down_mw'][str(j)]
 need(out[:start_tick]==original_physical[:start_tick]and out[start_tick+cells:]==original_physical[start_tick+cells:],'overlay modified outside declared reference interval')
 return out
