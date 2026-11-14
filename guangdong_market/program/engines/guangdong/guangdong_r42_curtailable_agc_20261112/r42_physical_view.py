"""Data-only accepted-research SCED physical view, not execution authority."""
import math
from datetime import datetime,timedelta
from smm.gd_r23_scuc_contract import validate_contract

def build_physical(case,names,response,source_info,*,curtailment_penalty):
 e=case['energy_inputs'];clock=validate_contract(case['contract'],len(e['horizon']['periods']));curves=case['curves'];U=e['thermal'];N=len(e['horizon']['periods']);day=datetime.fromisoformat(case['contract']['day_start']);clean=lambda name:name.replace("'",'');assert len(set(map(clean,names['variables'])))==len(names['variables']);v=dict(zip(map(clean,names['variables']),response['primal']));get=lambda stem,u,t:v[f'{stem}[{u},{t}]'];events=[];cost=0.
 for g,d in U.items():
  for t in range(N):
   for kind,stem,fee in (('startup','start','startup_yuan'),('shutdown','stop','shutdown_yuan')):
    if get(stem,g,t)>.5:events.append(dict(unit=g,kind=kind,index=t,at=(day+timedelta(minutes=5*t)).isoformat(),curve_cells=len(curves[g][kind]),fee_yuan=d[fee][t]));cost+=d[fee][t]
 cells=[]
 for t in range(N):
  row={'index':t,'start':(day+timedelta(minutes=5*t)).isoformat(),'end':(day+timedelta(minutes=5*(t+1))).isoformat(),'thermal':{},'battery':{},'renewable':{}}
  for bus in e['buses']:
   row['renewable'][bus]={'availability_mw':e['renewable_mw'][bus,t],'dispatched_mw':get('renewable_power',bus,t),'curtailed_mw':get('renewable_curtailment',bus,t),'reserve_credit_mw':0.,'AGC_credit_mw':0.}
  for g,d in U.items():
   active=[z for z in events if z['unit']==g and z['index']<=t<z['index']+z['curve_cells']];assert len(active)<=1
   z=active[0]if active else None;stable=get('stable',g,t)>.5;phase=z['kind']if z else'stable'if stable else'off';step=t-z['index']if z else None;p=get('power',g,t);end=p
   if z:
    c=curves[g][z['kind']][step];end=p-c['mean']+c['end'];cost+=c['cost']/12
   elif stable:
    cost+=(d['base_bid_yuan_per_h'][t]+case['no_load_cost'][g]+math.fsum(v[f"segment_power[{g},{s['key']},{t}]"]*s['price_yuan_per_mwh'][t]for s in d['segments']))/12
   row['thermal'][g]={'agc_up_reserved_mw':10. if t<288 else 0.,'agc_down_reserved_mw':10. if t<288 else 0.,'phase':phase,'mean_mw':p,'end_mw':end,'curve_step':step,'stable':stable}
  for b,d in case['batteries'].items():
   charge=get('battery.charge',b,t);discharge=get('battery.discharge',b,t);cm=round(get('battery.charge_mode',b,t));dm=round(get('battery.discharge_mode',b,t));energy=get('battery.energy',b,t)
   row['battery'][b]={'charge_mw':charge,'discharge_mw':discharge,'energy_mwh':energy,'permission':{'charge':cm,'discharge':dm,'idle':1-cm-dm},'actual_direction':'charge'if charge>1e-8 else'discharge'if discharge>1e-8 else'idle'}
  cells.append(row)
 carry={}
 for g,d in U.items():
  ev=[z for z in events if z['unit']==g];last=ev[-1]if ev else None;phase=cells[-1]['thermal'][g]['phase']
  if last and N==last['index']+last['curve_cells']:phase='stable'if last['kind']=='startup'else'off'
  origin=(last['index']+(last['curve_cells']if last['kind']=='shutdown'else 0))/12 if last else-d['initial_state_age_h'];age=max(0.,N/12-origin);minimum=d['min_up_h']if phase!='off'else d['min_down_h']
  if phase=='shutdown':
   starts=[z for z in ev if z['kind']=='startup'];origin=starts[-1]['index']/12 if starts else-d['initial_state_age_h'];age=N/12-origin
  unfinished={'kind':last['kind'],'next_step':N-last['index'],'remaining_steps':curves[g][last['kind']][N-last['index']:]}if last and N<last['index']+last['curve_cells']else None
  carry[g]={'phase':phase,'power_mw':cells[-1]['thermal'][g]['end_mw'],'state_age_h':age,'residence_remaining_h':max(0.,minimum-age),'post_curve_minimum_down_h':d['min_down_h']if phase=='shutdown'else None,'unfinished_curve':unfinished}
 market=[]
 for point in clock['market_grid']:
  q=dict(point);ids=q['physical_indices'];q['thermal']={g:{'mean_mw':sum(cells[t]['thermal'][g]['mean_mw']for t in ids)/len(ids),'phases':[cells[t]['thermal'][g]['phase']for t in ids]}for g in U};q['battery']={b:{'mean_mw':sum(cells[t]['battery'][b]['discharge_mw']-cells[t]['battery'][b]['charge_mw']for t in ids)/len(ids),'end_energy_mwh':cells[ids[-1]]['battery'][b]['energy_mwh']}for b in case['batteries']};q['renewable']={bus:{field:sum(cells[t]['renewable'][bus][field]for t in ids)/len(ids)for field in ('availability_mw','dispatched_mw','curtailed_mw')}for bus in e['buses']};market.append(q)
 battery_cost=0.
 for b,d in case['batteries'].items():
  for direct,sign in (('charge',-1),('discharge',1)):
   for k,price in enumerate(d[direct+'_curve']['prices_yuan_per_mwh']):battery_cost+=sign*price/12*math.fsum(v[f'battery.segment[{b},{direct},{k},{t}]']for t in range(N))
 curtailment_cost=curtailment_penalty/12*math.fsum(z['curtailed_mw']for row in cells for z in row['renewable'].values())
 assert abs(cost+battery_cost+curtailment_cost-response['objective'])<=1e-6+1e-9*abs(response['objective'])
 return {'schema':'gd.research.r42.curtailable_AGC.fixed_SCED.physical_view.v1','renewable_dispatch_is_not_availability':True,'curtailment_penalty_yuan_mwh_is_research':curtailment_penalty,'curtailment_penalty_cost_yuan':curtailment_cost,'scenario':'new curtailable VRE + synthetic D-day AGC50; not fixed full availability','events':events,'physical_cells':cells,'market_projection':market,'terminal_obligations':carry,'event_cost_yuan':sum(z['fee_yuan']for z in events),'thermal_cost_yuan':cost,'storage_bid_cost_yuan':battery_cost,'total_cost_yuan':response['objective'],'official_DA98':False,'formal_bill':None,'execution_authority':False,'SCUC_accepted':True,'formal_publication_allowed':False,'research_publication_allowed':False,'permission_is_not_actual_direction':True,'source_info':source_info}
