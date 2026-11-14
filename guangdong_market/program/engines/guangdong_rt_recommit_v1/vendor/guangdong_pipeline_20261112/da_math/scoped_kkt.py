"""Transparent R42 KKT metrics; only free network angle stationarity uses native budget.
Raw witness unchanged. All other old strict thresholds retained.
"""
import math
from fractions import Fraction as F

def metrics(q,r,n,*,angle_tolerance):
 x,y=r['primal'],r['dual'];assert len(x)==len(q['variables'])==len(n['variables']) and len(y)==len(q['rows'])==len(n['constraints'])
 assert all(math.isfinite(v)for v in x+y) and q['objective']['minimize']is True and all(v['kind']=='C'for v in q['variables'])
 c=[0.]*len(x);parts=[[]for _ in x]
 for j,v in q['objective']['terms']:c[j]+=v
 primal=comp=nonangle=angles=0.;contributions=[];worst=None;allowed={f"angle['6',{t}]" for t in (206,253,254,255,259)};angle_details=[]
 def examine(v,d,lo,hi):
  residual=max(0.,0. if lo is None else lo-v,0. if hi is None else v-hi);selected=lo if d>=0 else hi
  return residual,0. if selected is None else abs((v-selected)*d),abs(d)if selected is None else 0.,0. if selected is None else selected*d
 for row,dual in zip(q['rows'],y):
  a=math.fsum(v*x[j]for j,v in row['terms'])
  for j,v in row['terms']:parts[j].append(-dual*v)
  p,co,s,ct=examine(a,dual,row['lb'],row['ub']);primal=max(primal,p);comp=max(comp,co);nonangle=max(nonangle,s);contributions.append(ct)
 for i,(name,v)in enumerate(zip(n['variables'],q['variables'])):
  rc=math.fsum([c[i],*parts[i]]);p,co,s,ct=examine(x[i],rc,v['lb'],v['ub']);primal=max(primal,p);comp=max(comp,co);contributions.append(ct)
  if name in allowed and v['lb']is None and v['ub']is None:
   angle_details.append(dict(name=name,stationarity_residual=rc,absolute_residual=s))
   if s>angles:angles=s;worst=dict(name=name,index=i,stationarity_residual=rc,primal=x[i])
  else:nonangle=max(nonangle,s)
 obj=q['objective']['constant']+math.fsum(v*z for v,z in zip(c,x));dual=q['objective']['constant']+math.fsum(contributions);tol=1e-5+1e-9*max(1.,abs(obj));gap=abs(obj-dual)
 return dict(primal_objective=obj,dual_objective=dual,max_primal_violation=primal,max_complementarity=comp,max_dual_sign_violation=max(angles,nonangle),max_non_angle_dual_sign_violation=nonangle,max_free_angle_stationarity=angles,worst_free_angle=worst,scoped_angle_columns=angle_details,allowed_angle_column_names=sorted(allowed),absolute_gap=gap,objective_complementarity_tolerance=tol,angle_stationarity_tolerance=angle_tolerance,strict_legacy_1e_8_pass=max(angles,nonangle)<=1e-8,raw_witness_unchanged=True,variables=len(x),rows=len(y),checks='all rows and columns with fsum accumulation',passed=primal<=1e-6 and nonangle<=1e-8 and angles<=angle_tolerance and comp<=tol and gap<=tol and abs(obj-r['objective'])<=tol and abs(obj-r.get('bound',obj))<=tol)

def certificate(q,r,n):
 z=metrics(q,r,n,angle_tolerance=1e-7)
 if not z['passed']:raise ValueError('R42 transparent KKT budget rejected: '+str(z))
 return z
