from prepare import *
import math
q=read(BASE/'sced_native_v1/request.json');r=read(BASE/'sced_native_v1/response.json');n=read(BASE/'actual_review_v1/sced_prepared_v1/names.json');x=r['primal'];y=r['dual'];terms=[[]for _ in x];cost=[0.]*len(x)
for j,c in q['objective']['terms']:cost[j]+=c
for row,dual in zip(q['rows'],y):
 for j,c in row['terms']:terms[j].append(-dual*c)
viol=[]
for i,v in enumerate(q['variables']):
 rc=math.fsum([cost[i],*terms[i]])
 if (rc>0 and v['lb']is None)or(rc<0 and v['ub']is None):viol.append(dict(name=n['variables'][i],reduced_cost_fsum=rc,primal=x[i],lb=v['lb'],ub=v['ub']))
viol.sort(key=lambda z:abs(z['reduced_cost_fsum']),reverse=True)
out=dict(status='diagnostic_only_not_KKT_acceptance',raw_response_sha256=sha(BASE/'sced_native_v1/response.json'),max_free_column_stationarity_residual=abs(viol[0]['reduced_cost_fsum']),over_1e_8=sum(abs(z['reduced_cost_fsum'])>1e-8 for z in viol),worst=viol[:20],solver_calls=0)
write(BASE/'SCED_DUAL_DIAGNOSTIC.json',out);print(out)
