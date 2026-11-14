"""Causal available renewable power; independent RT and PRICE blocks, no service credit."""
import math
from .gd_r15_da_rt_bridge import require,close

def attach(m,inp,stage):
 import pyomo.environ as p
 V=list(inp['renewable']);J=range(24);require(stage in('RT','PRICE'),'stage')
 m.vre_power=p.Var(V,J,domain=p.NonNegativeReals);m.vre_curtailment=p.Var(V,J,domain=p.NonNegativeReals);m.vre_rows=p.ConstraintList();cost=0.
 for b,u in inp['renewable'].items():
  require(u['reserve_credit_mw']==u['AGC_credit_mw']==0,'no VRE service credit')
  for j,A in enumerate(u['availability_mw']):
   require(math.isfinite(A)and A>=0,'causal available upper bound')
   m.vre_power[b,j].setub(A);m.vre_curtailment[b,j].setub(A);m.vre_rows.add(m.vre_power[b,j]+m.vre_curtailment[b,j]==A)
   if stage=='RT':
    m.injection[b,j].set_value(m.injection[b,j].expr+m.vre_power[b,j]);cost+=(u['bid_proxy_yuan_mwh']*m.vre_power[b,j]+u['M2_dispatch_research']*m.vre_curtailment[b,j])/12
 if stage=='PRICE':
  m.vre_quarter_power=p.Var(V,range(8),domain=p.NonNegativeReals);m.vre_quarter_curtailment=p.Var(V,range(8),domain=p.NonNegativeReals)
  for b,u in inp['renewable'].items():
   for q in range(8):
    A=math.fsum(u['availability_mw'][3*q:3*q+3])/3;ref=math.fsum(u['SCED_reference_mw'][3*q:3*q+3])/3;lo=max(0.,ref*.9);hi=min(A,ref*1.1);require(lo<=hi,'empty causal VRE/DA band')
    m.vre_quarter_power[b,q].setlb(lo);m.vre_quarter_power[b,q].setub(hi);m.vre_quarter_curtailment[b,q].setub(A)
    m.vre_rows.add(m.vre_quarter_power[b,q]+m.vre_quarter_curtailment[b,q]==A);m.vre_rows.add(3*m.vre_quarter_power[b,q]==sum(m.vre_power[b,j]for j in range(3*q,3*q+3)))
    m.injection[b,q].set_value(m.injection[b,q].expr+m.vre_quarter_power[b,q]);cost+=.25*(u['bid_proxy_yuan_mwh']*m.vre_quarter_power[b,q]+u['M2_price_prime']*m.vre_quarter_curtailment[b,q])
 m.objective.set_value(m.objective.expr+cost)

def add_cells(inp,cells,names,primal,*,stage):
 v={k.replace("'",''):z for k,z in zip(names['variables'],primal)}
 for j,cell in enumerate(cells):
  cell['renewable']={}
  for b,u in inp['renewable'].items():
   P=v[f'vre_power[{b},{j}]'];C=v[f'vre_curtailment[{b},{j}]'];A=u['availability_mw'][j];close(P+C,A,'raw VRE conservation');require(min(P,C)>=-1e-6 and max(P,C)<=A+1e-6,'raw VRE physical bounds')
   cell['renewable'][b]=dict(availability_mw=A,dispatched_mw=P,curtailed_mw=C,SCED_reference_mw=u['SCED_reference_mw'][j],reserve_credit_mw=0.,AGC_credit_mw=0.,forecast_vintage_sha256=u['forecast_vintage_sha256'],source='causal synthetic forecast; actual simulated '+stage+' primal',is_measurement=False)
  if stage=='RT':
   injections={b:sum(z['mean_mw']for g,z in cell['thermal'].items()if inp['resources'][g]['node']==b)+sum(z['power_mw']for g,z in cell['batteries'].items()if inp['resources'][g]['node']==b)+cell['renewable'].get(b,{}).get('dispatched_mw',0.)+inp['fixed_injection_mw'][b][j]-inp['load_mw'][b][j]for b in inp['nodes']}
   close(math.fsum(injections.values()),0.,'actual RT physical balance with VRE')
   for l,z in inp['network'].items():
    flow=math.fsum(z['ptdf'][b]*injections[b]for b in inp['nodes']);require(z['lower_mw'][j]-1e-6<=flow<=z['upper_mw'][j]+1e-6,'actual RT VRE network')
 return cells
