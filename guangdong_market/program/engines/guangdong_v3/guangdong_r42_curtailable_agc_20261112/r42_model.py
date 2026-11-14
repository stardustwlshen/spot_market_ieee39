"""Restore explicit noquote p+c=A and impose synthetic D-day AGC capacity.
New scenario, not algebraically equivalent to old fixed-renewable case.
"""
from copy import deepcopy
import math

def need(x,msg):
 if not x:raise ValueError(msg)
def addcoef(row,j,c):
 terms=dict(row['terms']);terms[j]=terms.get(j,0.)+c;row['terms']=[[k,v]for k,v in sorted(terms.items())if v]

def build(full,names,case,*,curtailment_penalty,penalty_basis):
 need(not full.get('r42_scenario'),'already modified')
 need(type(curtailment_penalty)in(int,float)and math.isfinite(curtailment_penalty)and curtailment_penalty>=0 and penalty_basis=='explicit_research_parameter','explicit nonnegative research penalty')
 q=deepcopy(full);n=deepcopy(names);vi={s.replace("'",''):i for i,s in enumerate(n['variables'])};ri={s.replace("'",''):i for i,s in enumerate(n['constraints'])};e=case['energy_inputs'];G=list(e['thermal']);T=576
 need(G==['C1','C2','C3','CCGT','GT'],'same five thermal order');active=[b for b in e['buses']if any(e['renewable_mw'][b,t]>0 for t in range(T))]
 need(len(active)==5,'same original five VRE buses');ids={vi[f'{v}[{b},{t}]']for b in active for t in range(T)for v in ('renewable_power','renewable_curtailment')}
 need(not any(j in ids and c for row in full['rows']for j,c in row['terms']),'old substituted renewable must have no remaining row references')
 need(not any(j in ids and c for j,c in full['objective']['terms']),'old renewable objective coefficient already present')
 need(all(v==0 for v in e['renewable_bid_yuan_per_mwh'].values()),'noquote zero offer scope; nonzero bids require separate formula')
 inventory=[];changed=set();objective_add=[]
 for t in range(T):
  available=0.
  for b in active:
   A=e['renewable_mw'][b,t];need(math.isfinite(A)and A>=0,'availability')
   p=vi[f'renewable_power[{b},{t}]'];c=vi[f'renewable_curtailment[{b},{t}]'];available+=A
   need(full['variables'][p]['lb']==full['variables'][p]['ub']==A and full['variables'][c]['lb']==full['variables'][c]['ub']==0,'original fixed availability values')
   for j in(p,c):q['variables'][j].update(kind='C',lb=0.,ub=A,start=None)
   k=ri[f'c_renewable_available[{b},{t}]'];need(full['rows'][k]==dict(lb=0.,ub=0.,terms=[]),'original substituted renewable equality')
   q['rows'][k]=dict(lb=A,ub=A,terms=[[p,1.],[c,1.]]);changed.add(k)
   k=ri[f'c_balance[{b},{t}]'];r=q['rows'][k];ts=dict(r['terms']);ori=[]
   for line,z in e['lines'].items():
    exp=1 if z['from_bus']==b else -1 if z['to_bus']==b else 0
    if exp:ori.append(ts[vi[f'flow[{line},{t}]']]/exp)
   need(ori and all(v==ori[0]for v in ori)and ori[0]in(-1,1),'balance orientation')
   o=ori[0];need(r['lb']==r['ub']and r['lb']is not None,'balance equality');addcoef(r,p,-o);r['lb']-=o*A;r['ub']-=o*A;changed.add(k)
   objective_add.append([c,curtailment_penalty/12])
   inventory.append(dict(bus=b,physical_cell=t,available_mw=A,p_index=p,c_index=c,curtailment_penalty_yuan_mwh=curtailment_penalty,energy_weight_h=1/12,noquote_bid_yuan_mwh=0))
  for offset,side in ((1,'lb'),(2,'ub')):
   k=ri[f'reserves[{24*t+offset}]'];r=q['rows'][k];need(r[side]is not None,'aggregate reserve RHS')
   for b in active:addcoef(r,vi[f'renewable_power[{b},{t}]'],1.)
   r[side]+=available;changed.add(k)
  if t<288:
   for gi,g in enumerate(G):
    st=vi[f'stable[{g},{t}]'];need(q['variables'][st]['kind']=='B','original stable binary')
    n['constraints'].append(f'r42_AGC_stable[{g},{t}]');q['rows'].append(dict(lb=1.,ub=1.,terms=[[st,1.]]))
    for label in(f'free_lower[{g},{t}]',f'free_upper[{g},{t}]',f'reserves[{24*t+3+4*gi}]',f'reserves[{24*t+4+4*gi}]'):
     k=ri[label];addcoef(q['rows'][k],st,10.);changed.add(k)
    for off,coef in ((1,-10.),(2,10.)):
     k=ri[f'reserves[{24*t+off}]'];addcoef(q['rows'][k],st,coef);changed.add(k)
   need(q['rows'][ri[f'reserves[{24*t+24}]']]['lb']==400,'accident400')
 q['objective']['terms']+=objective_add;q['objective']['terms'].sort();q.update(schema='gd.research.r42.noquote_curtailable.synthetic_D_AGC.v1',r42_scenario=True)
 q.pop('deadline',None);q['warmstart']=False;q['dual']=False;q['preparation_elapsed_s']=0.
 q['options'].update(time_limit=600.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True,presolve='choose',solve_relaxation=False)
 need(all(a==b for i,(a,b)in enumerate(zip(full['rows'],q['rows']))if i not in changed),'no unrelated original row change')
 need(all(a==b for i,(a,b)in enumerate(zip(full['variables'],q['variables']))if i not in ids),'no unrelated bounds/integers')
 return q,n,dict(active_buses=active,inventory=inventory,changed_original_rows=sorted(changed),added_stable_rows=1440,changed_variable_indices=sorted(ids),added_objective_terms=objective_add,original_400_and_SOC_network_unchanged=True,all_day_AGC_scope='D288 only; next-day research physical tail no AGC assumption',native_calls=0)
