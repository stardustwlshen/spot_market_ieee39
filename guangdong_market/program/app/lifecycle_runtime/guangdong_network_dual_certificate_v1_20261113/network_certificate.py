"""Pure, explicit zero-RHS network dual certification. No optimizer or file writes."""
from pathlib import Path
from decimal import Decimal as D,localcontext
from collections import defaultdict,deque
import json,copy,math,hashlib,importlib.util
B=Path(__file__).resolve().parent
_spec=importlib.util.spec_from_file_location('_gd_network_cert_strict_kkt',B/'strict_kkt.py')
k=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(k)
SCHEMA='gd.network_dual_certificate.v1'
def need(x,m):
 if not x:raise ValueError(m)
def dec(x):
 need(isinstance(x,(int,float))and not isinstance(x,bool)and math.isfinite(x),'finite numeric input');return D.from_float(float(x))
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def source_hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in (Path(__file__),B/'strict_kkt.py')}
def prerequisites(q,raw,names,before):
 need(raw.get('status')=='optimal' and raw.get('native_status')=='Optimal' and raw.get('has_primal')is True and raw.get('dual_valid')is True,'native Optimal continuous primal/dual required')
 need(before['max_primal_violation']<=1e-6,'original primal infeasible')
 tol=before['objective_complementarity_tolerance']
 need(before['max_complementarity']<=tol and before['absolute_gap']<=tol,'original complementarity/gap rejected')
 need(abs(before['primal_objective']-raw['objective'])<=tol and abs(before['primal_objective']-raw['bound'])<=tol,'original primal objective/bound rejected')
 # No correction of unrelated dual failure is permitted.
 costs=[0.]*len(q['variables']);parts=[[]for _ in costs]
 for i,c in q['objective']['terms']:costs[i]+=c
 for row,y in zip(q['rows'],raw['dual']):
  need(not ((y>0 and row['lb']is None)or(y<0 and row['ub']is None))or abs(y)<=1e-8,'original row dual sign rejected')
  for i,c in row['terms']:parts[i].append(-c*y)
 for i,(name,v)in enumerate(zip(names['variables'],q['variables'])):
  if name.startswith('angle['):continue
  rc=math.fsum([costs[i],*parts[i]]);selected=v['lb']if rc>=0 else v['ub']
  need(selected is not None or abs(rc)<=1e-8,'original non-angle dual sign rejected')

def certify(model,names,native_response):
 """Return a distinct certificate and optional derived_response. Never mutate raw.
 Native-passed certificates have derived_response=None. Derived acceptance remains
 numerical only; caller must still run existing physical/source/SCUC pair gates.
 """
 q=model;raw=native_response
 before=k.metrics(q,raw,names,angle_tolerance=1e-8)
 prerequisites(q,raw,names,before)
 binding=dict(model_sha256=digest(q),names_sha256=digest(names),native_response_sha256=digest(raw))
 common=dict(schema=SCHEMA,algorithm='zero_rhs_reference_and_incidence_tree_decimal80_v1',source_hashes=source_hashes(),binding=binding,native_calls=0,native_KKT=before,raw_KKT=before,model_unchanged=True,thresholds_unchanged=True,formal=False)
 if before['passed']:
  cert=dict(common,kind='native_strict_certificate',derived_response_sha256=None,changes=[],derived_KKT=None)
  return dict(certificate=cert,certificate_sha256=digest(cert),derived_response=None)
 z=copy.deepcopy(raw)
 a={i:n for i,n in enumerate(names['variables'])if n.startswith('angle[')}
 need(a and all(q['variables'][i]['lb']is None and q['variables'][i]['ub']is None for i in a),'all supported angle columns free; no invented fixed coldual')
 groups=defaultdict(list)
 for i,n in a.items():groups[int(n.rsplit(',',1)[1][:-1])].append(i)
 reports=[];changes=[]
 with localcontext()as ctx:
  ctx.prec=80
  residual={i:D(0)for i in a}
  for i,c in q['objective']['terms']:
   if i in a:residual[i]+=dec(c)
  for row,y in zip(q['rows'],raw['dual']):
   for i,c in row['terms']:
    if i in a:residual[i]-=dec(c)*dec(y)
  for t,nodes in sorted(groups.items()):
   nodes=set(nodes);adj={i:[]for i in nodes};refs=[];edges=[]
   for ri,(name,row)in enumerate(zip(names['constraints'],q['rows'])):
    hits=[(i,c)for i,c in row['terms']if i in nodes]
    if not hits:continue
    if name.startswith('c_dc_flow['):
     need(row['lb']==row['ub']==0 and len(hits)==2,'DC edge zero RHS twoangles required')
     (u,cu),(v,cv)=hits;need(dec(cu)+dec(cv)==0 and cu!=0,'incidence antisymmetry');adj[u].append((v,ri,cu,cv));adj[v].append((u,ri,cv,cu));edges.append(ri)
    elif name.startswith('c_reference['):
     need(row['lb']==row['ub']==0 and len(row['terms'])==1 and len(hits)==1,'zero RHS pure reference required');refs.append((ri,*hits[0]))
    else:raise ValueError('unsupported additional angle row: '+name)
   need(len(refs)==1,'exactly one source reference per connected time network')
   rr,root,rc=refs[0];need(rc!=0,'nonzero reference coefficient')
   parent={root:None};order=[root];queue=deque([root]);tree={}
   while queue:
    u=queue.popleft()
    for v,ri,cu,cv in sorted(adj[u],key=lambda e:(e[1],e[0])):
     if v not in parent:parent[v]=u;tree[v]=(ri,dec(cv));order.append(v);queue.append(v)
   need(set(parent)==nodes,'disconnected network or rank deficiency; stop')
   total=sum((residual[i]for i in nodes),D(0));corrections={rr:total/dec(rc)}
   sub={i:residual[i]for i in nodes};sub[root]-=total
   for i in reversed(order[1:]):
    ri,coef=tree[i];corrections[ri]=sub[i]/coef;sub[parent[i]]+=sub[i]
   need(abs(sub[root])<D('1e-60'),'high precision consistency failure')
   for ri,d in corrections.items():
    value=float(dec(raw['dual'][ri])+d);need(math.isfinite(value),'nonfinite correction');z['dual'][ri]=value
    if value!=raw['dual'][ri]:changes.append(dict(index=ri,name=names['constraints'][ri],original=raw['dual'][ri],derived=value,delta=value-raw['dual'][ri],high_precision_correction=str(d),zero_rhs=True))
   reports.append(dict(tick=t,nodes=len(nodes),DC_edges=len(edges),tree_edges=len(tree),rank=len(nodes),reference=names['constraints'][rr],reference_correction=str(corrections[rr]),sum_original_angle_stationarity=str(total),root_consistency=str(sub[root])))
 need(z['primal']==raw['primal'],'primal changed')
 changed={c['index']for c in changes}
 need(all(q['rows'][i]['lb']==q['rows'][i]['ub']==0 and names['constraints'][i].startswith(('c_dc_flow[','c_reference['))for i in changed),'nonzero RHS or forbidden row modification')
 need(all(z[k]==raw[k]for k in raw if k!='dual'),'nondual raw field changed')
 after=k.metrics(q,z,names,angle_tolerance=1e-8)
 need(after['passed'],'derived certificate failed full strict KKT; no retry or threshold tuning')
 costs=defaultdict(list);parts={i:[]for i in a}
 for i,c in q['objective']['terms']:costs[i].append(c)
 for row,y in zip(q['rows'],z['dual']):
  for i,c in row['terms']:
   if i in a:parts[i].append(-c*y)
 ar={a[i]:math.fsum(costs[i]+parts[i])for i in a}
 cert=dict(common,kind='native_primal_independent_derived_dual',derived_response_sha256=digest(z),changes=changes,derived_KKT=after,network_rank_checks=reports,max_all_free_angle_stationarity=max(map(abs,ar.values())),primal_unchanged=True,only_zero_RHS_network_duals_changed=True,native_dual_passed=False,metric_raw_witness_unchanged_means='input to metric unchanged, not equality of derived dual to native dual')
 return dict(certificate=cert,certificate_sha256=digest(cert),derived_response=z)

def verify(model,names,native_response,bundle):
 """Recompute deterministic certificate, never trust a passed JSON or mutable hash."""
 expected=certify(model,names,native_response)
 need(bundle==expected,'certificate/source/derived witness mismatch')
 return expected['certificate']
