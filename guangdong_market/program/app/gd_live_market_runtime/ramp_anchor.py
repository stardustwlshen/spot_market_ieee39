"""Optional PRICE-only thermal DA preceding-quarter anchor. Research interpretation."""
from copy import deepcopy
from math import fsum,isfinite
POLICY='thermal_DA_reference_plus_DA_preceding_complete_15min_first_ramp_RESEARCH'
def preceding_anchor(da_mean5,first,declared_initial):
 if type(first)is not int or first<0 or first%3:raise ValueError('quarter-aligned nonnegative first index required')
 if first==0:
  if not isfinite(declared_initial):raise ValueError('finite declared source initial required')
  return declared_initial
 if first>len(da_mean5):raise ValueError('DA prefix missing')
 values=da_mean5[first-3:first]
 if len(values)!=3 or not all(isfinite(v)for v in values):raise ValueError('three actual DA mean-MW cells required')
 return fsum(values)/3

def apply_first_ramp(model,names,specs,*,policy):
 """specs keyed exact thermal P[g,0], values DA anchor and existing up/down budgets.
 Caller binds inventory and hashes; only first-ramp RHS changes, no capability edits.
 """
 if policy!=POLICY:raise ValueError('explicit supported research policy required')
 out=deepcopy(model);lookup={s:i for i,s in enumerate(names['variables'])};trace=[]
 for column,s in specs.items():
  if column not in lookup or not column.startswith('P[')or column.startswith('P[ES')or not column.endswith(',0]'):raise ValueError('thermal first-period column required')
  anchor,up,down=s['DA_anchor_MW'],s['up_budget_MW'],s['down_budget_MW']
  if not all(isfinite(v)for v in [anchor,up,down])or min(up,down)<0:raise ValueError('finite anchor/nonnegative unchanged ramp budget required')
  j=lookup[column];ui=[i for i,r in enumerate(model['rows'])if r['terms']==[[j,1.0]]and r['lb']is None];di=[i for i,r in enumerate(model['rows'])if r['terms']==[[j,-1.0]]and r['lb']is None]
  if len(ui)!=1 or len(di)!=1:raise ValueError('unique first-ramp pair required')
  u,d=ui[0],di[0];previous=model['rows'][u]['ub']-up
  if abs(down-model['rows'][d]['ub']-previous)>1e-7:raise ValueError('supplied budgets inconsistent with same previous anchor')
  out['rows'][u]['ub']=anchor+up;out['rows'][d]['ub']=down-anchor;trace.append(dict(column=column,previous_anchor=previous,new_DA_anchor=anchor,up_row=u,down_row=d,policy=policy,formal=False))
 return out,trace
