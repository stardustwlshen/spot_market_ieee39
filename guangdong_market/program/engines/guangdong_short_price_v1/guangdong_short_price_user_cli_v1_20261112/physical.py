"""3.4.4.3/.4/.6/.7, explicit province-only research adapter.
Q denotes MWh, NOT reactive power. Primary epigraph auxiliaries have no cost.
"""
import math
from contracts import need

def vector(v,n,name,nonnegative=False):
 need(len(v)==n and all(type(x)in(int,float)and math.isfinite(x)and(not nonnegative or x>=0)for x in v),'finite vector '+name)

def add_physical(m,P,thermal,periods,minimum,maximum,online,load,tie,spec):
 import pyomo.environ as p
 T=list(periods);n=len(T);G=set(thermal)
 # Qualified resources only from existing thermal inventory. No VRE/BESS promotion.
 m.additional=p.Block();b=m.additional;b.rows=p.ConstraintList();b.zero=p.Var(bounds=(0,0));b.zero.fix(0)
 trace={'negative':[],'primary':[],'groups':[],'Q_unit':'MWh','five_minute_intermediate_feasibility_proved':False}
 for key in ['negative_reserve','primary_frequency_reserve']:
  z=spec[key]
  if z['status']=='inapplicable':continue
  need(z['status']=='required','explicit required status '+key);d=z['data'];vector(d['requirement_MW'],n,key,True)
  U=d['qualified_resources'];need(len(U)==len(set(U))and set(U)<=G,'thermal-only unique qualification')
  if key=='negative_reserve':
   need(d['load_side_upward_capability_MW']==[0]*n,'load-side capability not implemented; cannot invent BESS qualification')
   for q in T:
    lhs=sum(online[g,q]*minimum[g,q] for g in U)+tie[q]
    rhs=load[q]-d['requirement_MW'][q]
    b.rows.add(lhs+b.zero<=rhs);trace['negative'].append(dict(q=q,lhs=lhs,rhs=rhs,constant_capacity_check=True))
  else:
   need(set(d['fraction'])==set(U),'exact primary coefficient inventory')
   for g in U:vector(d['fraction'][g],n,'fraction',True);need(all(v<=1 for v in d['fraction'][g]),'fraction <=1')
   b.primary=p.Var(U,T,domain=p.NonNegativeReals)
   for q in T:
    for g in U:
     cap=online[g,q]*maximum[g,q]
     b.rows.add(b.primary[g,q]<=cap-P[g,q]);b.rows.add(b.primary[g,q]<=cap*d['fraction'][g][q])
    b.rows.add(sum(b.primary[g,q]for g in U)+b.zero>=d['requirement_MW'][q])
    trace['primary'].append(dict(q=q,requirement=d['requirement_MW'][q],qualified=U,auxiliary_zero_objective=True,interpretation='existence-equivalent sum(min(headroom,cap*alpha)) >= R; auxiliary need not equal min'))
 for key in ['group_power','group_energy']:
  z=spec[key]
  if z['status']=='inapplicable':continue
  need(z['status']=='required','explicit group status');d=z['data'];need(d['groups'],'nonempty group data');seen=set()
  for group in d['groups']:
   need(group['id']not in seen,'duplicate group');seen.add(group['id']);U=group['units'];need(U and len(U)==len(set(U))and set(U)<=G,'thermal-only explicit group members')
   if key=='group_power':
    vector(group['lower_MW'],n,'group lower');vector(group['upper_MW'],n,'group upper')
    for q in T:
     lo,hi=group['lower_MW'][q],group['upper_MW'][q];need(lo<=hi,'group bounds');b.rows.add(p.inequality(lo,sum(P[g,q]for g in U),hi))
   else:
    need(d['interpretation']=='eight_quarter_total_energy_equivalent_to_24_mean5_RESEARCH','literal mixed time formula unsupported')
    lo,hi=group['lower_MWh'],group['upper_MWh'];need(all(type(v)in(int,float)and math.isfinite(v)for v in [lo,hi])and lo<=hi,'group energy bounds')
    b.rows.add(p.inequality(lo,sum(.25*P[g,q]for g in U for q in T),hi))
   trace['groups'].append(dict(kind=key,id=group['id'],members=U))
 return trace
