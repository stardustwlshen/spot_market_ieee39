from copy import deepcopy
from fractions import Fraction as F
from collections import defaultdict
from provider import boundaries

def transform(q,n,inp):
 state=boundaries(inp);idx={s:j for j,s in enumerate(n['variables'])};removed_cols=set();removed_rows=set();added=[];labels=[];changes=[]
 for b,z in state.items():
  removed_cols|={idx[f'energy[{b},{k}]']for k in range(25)if k%3}
  dyn=[];means=[]
  for k in range(24):
   target={idx[f'energy[{b},{k+1}]'],idx[f'lift[{b},{k}]']}
   if k:target.add(idx[f'energy[{b},{k}]'])
   matches=[i for i,r in enumerate(q['rows'])if {j for j,c in r['terms']}==target and r['lb']==r['ub'] and r['lb']is not None];assert len(matches)==1;dyn.append(matches[0])
  for k in range(8):
   target={idx[f'power[{b},{k}]']}|{idx[f'lift[{b},{j}]']for j in range(3*k,3*k+3)}
   matches=[i for i,r in enumerate(q['rows'])if {j for j,c in r['terms']}==target];assert len(matches)==1;means.append(matches[0])
   acc=defaultdict(F);rhs=F(0)
   for ri in dyn[3*k:3*k+3]:
    row=q['rows'][ri];rhs+=F(row['lb'])
    for j,c in row['terms']:acc[j]+=F(c)
   mean=q['rows'][means[k]];mc=dict(mean['terms']);ls=[idx[f'lift[{b},{j}]']for j in range(3*k,3*k+3)];factor=-acc[ls[0]]/F(mc[ls[0]])
   assert all(acc[j]+factor*F(mc[j])==0 for j in ls),'mode/efficiency changes within quarter unsupported'
   for j,c in mean['terms']:acc[j]+=factor*F(c)
   if k==0:rhs=F(z['energy_mwh'])
   added.append(dict(lb=float(rhs),ub=float(rhs),terms=[[j,float(c)]for j,c in acc.items()if c]));labels.append(f'R61_SOC[{b},{k}]')
  removed_rows.update(dyn)
 out=deepcopy(q)
 for b,z in state.items():
  j=idx[f'energy[{b},0]'];out['variables'][j]['lb']=out['variables'][j]['ub']=z['energy_mwh']
  for date,used in z['used_internal_mwh'].items():
   matches=[i for i,s in enumerate(n['constraints'])if s.startswith('daily_throughput[') and s.split('[',1)[1].split(',')[0].strip("'")==b and date in s];assert len(matches)==1
   ri=matches[0];row=out['rows'][ri];assert row['lb']is None
   old=row['ub'];row['ub']+=z['actual_usage_by_date'][date]-used
   changes.append(dict(row=n['constraints'][ri],date=date,old_rhs=old,new_rhs=row['ub']))
 kept=[j for j in range(len(q['variables']))if j not in removed_cols];mapping={j:i for i,j in enumerate(kept)}
 def remap(row):
  row=deepcopy(row);assert all(j in mapping for j,c in row['terms']);row['terms']=[[mapping[j],c]for j,c in row['terms']];return row
 out['variables']=[out['variables'][j]for j in kept];out['rows']=[remap(row)for i,row in enumerate(out['rows'])if i not in removed_rows]+[remap(row)for row in added];out['objective']=remap(q['objective']);out.pop('deadline',None)
 names=dict(variables=[n['variables'][j]for j in kept],constraints=[s for i,s in enumerate(n['constraints'])if i not in removed_rows]+labels)
 midnight=inp['window']['midnight_local_energy_index']
 if midnight is not None:
  for b,target in inp['midnight_targets'].items():
   ri=names['constraints'].index(f'midnight[{b}]');row=out['rows'][ri];assert row['lb']==row['ub']==target and row['terms']==[[names['variables'].index(f'energy[{b},{midnight}]'),1.]]
 return out,names,dict(state=state,changed_dated_usage_rows=changes,removed_rows=sorted(removed_rows),removed_columns=sorted(removed_cols),old_to_new_columns=mapping,D96_index=midnight,D96_target_unchanged=True,RT_state_unchanged=True,PRICE_only=True,market_price=None,settlement_price=None,research_only=True)
