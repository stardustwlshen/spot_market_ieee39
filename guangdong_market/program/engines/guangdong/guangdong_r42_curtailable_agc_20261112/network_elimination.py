"""Data-only elimination of original DC states, no pricing PTDF import or solve.
All nondefining rows and objective references are algebraically substituted.
Numerical inverse coefficients are not thresholded; exact-float residuals are certified.
"""
import math
from collections import defaultdict
from fractions import Fraction as F
import numpy as np

def require(v,msg):
 if not v:raise ValueError(msg)
def parts(name):
 stem,tail=name.split('[',1);key,t=tail[:-1].rsplit(',',1);return stem,key.replace("'",''),int(t)
def connected(nodes,edges):
 require(len(nodes)==len(set(nodes)),'unique buses');g={b:set()for b in nodes}
 for a,b,w in edges:require(a in g and b in g and a!=b and math.isfinite(w)and w>0,'positive original branch susceptance');g[a].add(b);g[b].add(a)
 seen={nodes[0]};todo=list(seen)
 while todo:
  for b in g[todo.pop()]-seen:seen.add(b);todo.append(b)
 require(seen==set(nodes),'connected original graph; no silent island/slack repair')
def derive(q,n,case):
 e=case['energy_inputs'];T=len(e['horizon']['periods']);nodes=list(e['buses']);lines=e['lines'];refs=e['reference_buses'];require(len(refs)==1,'one explicit reference bus');ref=refs[0]
 connected(nodes,[(d['from_bus'],d['to_bus'],d['susceptance_mw_per_rad'])for d in lines.values()])
 vi={parts(name):i for i,name in enumerate(n['variables'])if name.startswith(('angle[','flow['))};netkeys=[('angle',b)for b in nodes]+[('flow',l)for l in lines];K=len(netkeys);netids=[[vi[(s,k,t)]for s,k in netkeys]for t in range(T)];flat={j for z in netids for j in z};require(len(flat)==K*T,'complete unique network state inventory')
 for j in flat:require(q['variables'][j]['kind']=='C'and q['variables'][j]['lb']is None and q['variables'][j]['ub']is None,'network states must be free continuous; finite bounds need explicit row conversion')
 rows={name:i for i,name in enumerate(n['constraints'])};byfamily={}
 for name,i in rows.items():
  if name.startswith(('c_reference[','c_dc_flow[','c_balance[','c_line_upper[','c_line_lower[')):byfamily[parts(name)]=i
 require(len([k for k in byfamily if k[0]=='c_reference'])==T,'one reference per physical cell')
 defids=[];balanceids=[]
 for t in range(T):
  allb=[byfamily[('c_balance',b,t)]for b in nodes];balanceids.append(allb)
  ids=[byfamily[('c_reference',ref,t)]]+[byfamily[('c_dc_flow',l,t)]for l in lines]+[byfamily[('c_balance',b,t)]for b in nodes if b!=ref];require(len(ids)==K,'square network definition');defids.append(ids)
 first=set(netids[0]);inj0=sorted({j for rid in balanceids[0]for j,c in q['rows'][rid]['terms']if c and j not in first});injkeys=[]
 for j in inj0:
  s,k,t=parts(n['variables'][j]);require(t==0 and s in('power','battery.charge','battery.discharge','renewable_power'),'explicit retained physical injections');injkeys.append((s,k))
 allvi={parts(name):i for i,name in enumerate(n['variables'])if name.startswith(('power[','battery.charge[','battery.discharge[','renewable_power['))};injids=[[allvi[(s,k,t)]for s,k in injkeys]for t in range(T)];J=len(injkeys);rhs=np.zeros((K,T));A=B=None;orientations=None
 for t in range(T):
  nz={j:k for k,j in enumerate(netids[t])};ix={j:k for k,j in enumerate(injids[t])};At=np.zeros((K,K));Bt=np.zeros((K,J));ori=[]
  for b,rid in zip(nodes,balanceids[t]):
   z=q['rows'][rid];require(z['lb']==z['ub']and z['lb']is not None,'all original node balances equalities');ts=dict(z['terms']);ratios=[]
   for line,d in lines.items():
    expected=1 if d['from_bus']==b else -1 if d['to_bus']==b else 0;actual=ts.get(vi[('flow',line,t)],0.)
    if expected:ratios.append(actual/expected)
    else:require(actual==0,'balance nonincident branch')
   require(ratios and all(v==ratios[0]for v in ratios)and ratios[0]in(-1.,1.),'original directed nodal incidence');o=ratios[0];ori.append(o)
   for j,c in z['terms']:
    if j in nz:require(netkeys[nz[j]][0]=='flow','balance has unsupported angle term')
    else:
     require(j in ix,'unknown balance injection reference');s,g=injkeys[ix[j]]
     d=({'bus':g} if s=='renewable_power' else e['thermal'][g]if s=='power'else case['batteries'][g]);require(d['bus']==b,'injection bus identity');require(c==o*(-1 if s in('power','battery.discharge','renewable_power')else 1),'original injection orientation')
  for l,d in lines.items():
   z=q['rows'][byfamily[('c_dc_flow',l,t)]];actual=dict(z['terms']);expected={vi[('flow',l,t)]:1.,vi[('angle',d['from_bus'],t)]:-d['susceptance_mw_per_rad'],vi[('angle',d['to_bus'],t)]:d['susceptance_mw_per_rad']};require(actual==expected and z['lb']==z['ub']==0.,'original DC line coefficients')
  z=q['rows'][byfamily[('c_reference',ref,t)]];require(dict(z['terms'])=={vi[('angle',ref,t)]:1.}and z['lb']==z['ub']==0.,'exact zero reference')
  for k,rid in enumerate(defids[t]):
   z=q['rows'][rid];require(z['lb']==z['ub']and z['lb']is not None,'definition equality');rhs[k,t]=z['lb']
   for j,c in z['terms']:
    if j in nz:At[k,nz[j]]+=c
    else:require(j in ix,'unknown definition variable');Bt[k,ix[j]]+=c
  if t==0:A=At;B=Bt;orientations=ori
  else:require(np.array_equal(A,At)and np.array_equal(B,Bt)and orientations==ori,'every cell exact original A/B structure and coefficients')
 # Positive connected susceptances and one reference prove invertible reduced Laplacian.
 # np.linalg.solve is linear algebra, not LP/MIP optimization.
 H=np.linalg.solve(A,-B);offset=np.linalg.solve(A,rhs);require(np.isfinite(H).all()and np.isfinite(offset).all(),'finite linear inverse')
 box=[]
 for t in range(T):
  box.append([float(e['renewable_mw'][g,t])if s=='renewable_power'else float(e['thermal'][g]['max_mw'][t])if s=='power'else float(case['batteries'][g]['charge_max_mw'if s=='battery.charge'else'discharge_max_mw'])for s,g in injkeys])
 require(all(math.isfinite(v)and v>=0 for z in box for v in z),'finite physical injection boxes')
 # Exact Fraction(float) errors certify the serialized float affine map, not a fictitious exact inverse.
 sparseA=[[(j,F(float(c)))for j,c in enumerate(row)if c]for row in A];FH=[[F(float(v))for v in row]for row in H]
 coeff=[[F(float(B[k,j]))+sum((c*FH[i][j]for i,c in sparseA[k]),F(0))for j in range(J)]for k in range(K)]
 const_max=F(0);worst=F(0);worst_at=None
 for t in range(T):
  fo=[F(float(offset[k,t]))for k in range(K)];bx=[F(v)for v in box[t]]
  for k in range(K):
   residual=sum((c*fo[i]for i,c in sparseA[k]),F(0))-F(float(rhs[k,t]));const_max=max(const_max,abs(residual));bound=abs(residual)+sum((abs(c)*v for c,v in zip(coeff[k],bx)),F(0))
   if bound>worst:worst=bound;worst_at=[t,k]
 require(float(worst)<=1e-6,'serialized map worst physical-box error exceeds original audit tolerance')
 return {'T':T,'net_keys':netkeys,'net_indices':netids,'injection_keys':injkeys,'injection_indices':injids,'definition_rows':defids,'balance_rows':balanceids,'reference_bus':ref,'bus_order':nodes,'balance_orientations':orientations,'H':H.tolist(),'offsets_by_cell':offset.T.tolist(),'box_upper_by_cell':box,'guard':{'connected_positive_original_graph':True,'one_reference':ref,'all_cell_A_B_exact_equal':True,'network_state_count_per_cell':K,'injection_count_per_cell':J,'max_coefficient_identity_residual':float(max(abs(v)for row in coeff for v in row)),'max_constant_identity_residual':float(const_max),'worst_original_definition_error_over_injection_box':float(worst),'exact_worst_box_error':str(worst),'worst_cell_definition':worst_at,'all_coefficients_unthresholded':True,'box_basis':'R42 renewable 0<=p<=unchanged A; original nonnegative power plus phase-partition/free_upper bound<=Pmax; battery nonnegative charge/discharge and c_power<=declared max*binary mode<=max; pinned original model, not added bounds.','invertibility_proof':'Positive weighted connected graph, exactly one zero reference: reduced Laplacian positive definite; these original DC+nonreference balance equations uniquely determine angles/flows.'}}

def transform(q,n,affine):
 T=affine['T'];netids=affine['net_indices'];flat={j:(t,k)for t,ids in enumerate(netids)for k,j in enumerate(ids)};keep=[j for j in range(len(q['variables']))if j not in flat];reindex={j:i for i,j in enumerate(keep)};definitions={j for ids in affine['definition_rows']for j in ids};injections=affine['injection_indices'];H=affine['H'];offsets=affine['offsets_by_cell'];rows=[];labels=[];origins=[];global_map={}
 for t,ids in enumerate(affine['balance_rows']):
  refpos=affine['bus_order'].index(affine['reference_bus']);rid=ids[refpos];ori=affine['balance_orientations'];weights=[ori[refpos]/v for v in ori];terms=defaultdict(list);rhs=[]
  for j,w in zip(ids,weights):
   r=q['rows'][j];rhs.append(w*r['ub'])
   for c,a in r['terms']:terms[c].append(w*a)
  combined={j:math.fsum(v)for j,v in terms.items()};require(all(combined.get(j,0.)==0. for j in netids[t]),'exact original nodal-sum cancellation')
  rr={'terms':[[j,c]for j,c in sorted(combined.items())if c],'lb':math.fsum(rhs),'ub':math.fsum(rhs)};global_map[rid]=(rr,{'kind':'exact_oriented_balance_sum','original_rows':ids,'weights':weights,'retained_original_slack_row':rid})
 paired={};skip=set();byname={name:i for i,name in enumerate(n['constraints'])}
 for name,i in byname.items():
  if name.startswith('c_line_upper['):
   lower='c_line_lower'+name[len('c_line_upper'):];require(lower in byname,'line has corresponding original lower row');j=byname[lower];a=q['rows'][i];b=q['rows'][j]
   if a['lb']is None and b['lb']is None and a['ub']is not None and b['ub']is not None and len(a['terms'])==len(b['terms'])==1 and a['terms'][0][0]==b['terms'][0][0]and a['terms'][0][1]==1. and b['terms'][0][1]==-1. and a['terms'][0][0]in flat:
    require(n['variables'][a['terms'][0][0]].startswith('flow['),'pair is original flow');paired[i]=({'terms':a['terms'],'lb':-b['ub'],'ub':a['ub']},j);skip.add(j)
 def substitute(terms):
  coeff=defaultdict(list);constants=[]
  for j,c in terms:
   if j not in flat:coeff[reindex[j]].append(c)
   else:
    t,k=flat[j];constants.append(c*offsets[t][k])
    for h,i in zip(H[k],injections[t]):coeff[reindex[i]].append(c*h)
  return [[j,v]for j,vals in sorted(coeff.items())if (v:=math.fsum(vals))!=0.],math.fsum(constants)
 for i,r in enumerate(q['rows']):
  if i in definitions or i in skip:continue
  origin={'kind':'direct_substitution','original_rows':[i]};label=n['constraints'][i]
  if i in global_map:r,origin=global_map[i];label='r34_global_balance['+str(parts(label)[2])+']'
  elif i in paired:r,j=paired[i];origin={'kind':'equivalent_original_two_sided_line','original_rows':[i,j]};label='r34_line_range'+label[len('c_line_upper'):]
  terms,c=substitute(r['terms']);rows.append({'terms':terms,'lb':None if r['lb']is None else r['lb']-c,'ub':None if r['ub']is None else r['ub']-c});labels.append(label);origins.append(origin)
 objterms,constant=substitute(q['objective']['terms']);objective={**q['objective'],'terms':objterms,'constant':q['objective']['constant']+constant}
 out={k:v for k,v in q.items()if k not in ('variables','rows','objective')};out.update(variables=[dict(q['variables'][j])for j in keep],rows=rows,objective=objective)
 names={'variables':[n['variables'][j]for j in keep],'constraints':labels}
 plan={'schema':'gd.research.r34.network_elimination_plan.v1','kept_original_compact_columns':keep,'network_affine':affine,'row_origins':origins,'removed_definition_rows':sorted(definitions),'paired_line_row_count':len(paired),'no_coefficient_thresholding':True,'all_row_and_objective_references_substituted':True,'new_variables':0,'new_fixed_decisions':0}
 return out,names,plan

def lift(plan,x):
 aff=plan['network_affine'];keep=plan['kept_original_compact_columns'];total=len(keep)+sum(len(z)for z in aff['net_indices']);require(len(x)==len(keep)and all(math.isfinite(z)for z in x),'complete finite reduced primal');out=[None]*total
 for j,v in zip(keep,x):out[j]=v
 for t,ids in enumerate(aff['net_indices']):
  inj=[out[j]for j in aff['injection_indices'][t]]
  for k,j in enumerate(ids):out[j]=math.fsum([aff['offsets_by_cell'][t][k]]+[a*v for a,v in zip(aff['H'][k],inj)])
 require(all(v is not None and math.isfinite(v)for v in out),'complete recovered original compact vector');return out
