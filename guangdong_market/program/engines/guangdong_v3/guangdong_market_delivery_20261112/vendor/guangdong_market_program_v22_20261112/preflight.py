"""Complete standard-worker protocol validation, no optimizer invocation."""
import math
REQUIRED={'version','variables','rows','objective','options','warmstart','dual','gap_settings','preparation_elapsed_s'}
def need(x,s):
 if not x:raise ValueError(s)
def finite(x):return type(x)in(int,float)and math.isfinite(x)
def validate(q,request=False):
 need(REQUIRED<=set(q),'missing worker fields: '+str(REQUIRED-set(q)))
 need(q['version']==1 and q['warmstart']is False and type(q['dual'])is bool,'protocol/warmstart/dual')
 need(finite(q['preparation_elapsed_s'])and q['preparation_elapsed_s']>=0,'preparation_elapsed_s')
 if request:need(finite(q.get('deadline')),'monotonic deadline required')
 o=q['options'];need(set(o)=={'threads','mip_rel_gap','mip_abs_gap','output_flag','presolve','solve_relaxation','time_limit'},'standard option inventory')
 need(o['threads']==1 and o['mip_rel_gap']==.001 and o['mip_abs_gap']==0 and type(o['output_flag'])is bool and o['presolve']in('choose','on','off')and o['solve_relaxation']is False and finite(o['time_limit'])and o['time_limit']>0,'solver option values')
 g=q['gap_settings'];need(g['requested_project_mip_rel_gap']==g['native_highs_rel_gap']==.001 and g['native_highs_abs_gap']==0,'gap protocol')
 vs=q['variables'];need(isinstance(vs,list)and len(vs)>0,'variables')
 for v in vs:
  need(set(v)=={'kind','lb','ub','start'}and v['kind']in('C','I','B'),'variable fields')
  need(all(v[k]is None or finite(v[k])for k in('lb','ub','start')),'finite variable bounds/start')
  need(v['lb']is None or v['ub']is None or v['lb']<=v['ub'],'reversed column bounds')
 def terms(a):
  need(isinstance(a,list),'terms list')
  for j,c in a:need(type(j)is int and 0<=j<len(vs)and finite(c),'coefficient/index')
 for r in q['rows']:
  need(set(r)=={'lb','ub','terms'}and all(r[k]is None or finite(r[k])for k in('lb','ub')),'row fields/bounds');terms(r['terms'])
  need(r['lb']is None or r['ub']is None or r['lb']<=r['ub'],'reversed row bounds')
 ob=q['objective'];need(set(ob)=={'constant','minimize','terms'}and finite(ob['constant'])and ob['minimize']is True,'minimization objective');terms(ob['terms'])
 return {'status':'protocol_valid','variables':len(vs),'rows':len(q['rows']),'optimizer_calls':0}
