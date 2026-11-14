"""Retained R53 derivative/physics checks; explicit current-run arguments only."""
import math
from scoped_kkt import metrics
def require(x,m):
 if not x:raise ValueError(m)
def close(a,b,msg,tol=1e-6):require(math.isfinite(a)and math.isfinite(b)and abs(a-b)<=tol,msg)
def review_math(q,r,n,w):
 kkt=metrics(q,r,n,angle_tolerance=1e-8);require(kkt['passed'],'strict PRICE KKT')
 d=w['base_DTO'];vre=w['eligible_VRE'];x=d['physical_lift'];require(w['same_bid_policy']['noquote_energy_bid']['yuan_per_mwh']==vre['bid_proxy_yuan_mwh']==-50,'same new bid source through PRICE');vi={name:i for i,name in enumerate(n['variables'])};ri={name:i for i,name in enumerate(n['constraints'])};duals={name:v for name,v in zip(n['constraints'],r['dual'])}
 # Pyomo names quote numeric string bus indices. Resolve by unique quote-normalized names.
 normvi={name.replace("'",''):i for name,i in vi.items()};require(len(normvi)==len(vi),'unique normalized columns')
 nodes=d['nodes'];lines=d['network'];R=d['resources'];V=vre['nodes'];derivative_rows=0
 for prefix,count,dd in(('',98,d),('physical_',576,x)):
  for t in range(count):
   refs={b:(vre['rows'][b][t]['SCED_reference_dispatched_mw']if not prefix else vre['physical_SCED_dispatched_mw'][b][t])for b in V};fixed={b:dd['fixed_injection_mw'][b][t]-refs.get(b,0.)for b in nodes}
   expected={normvi[f'{prefix}power[{uid},{t}]']:1. for uid in R};expected.update({normvi[f'{prefix}vre_power[{b},{t}]']:1. for b in V})
   row=q['rows'][ri[f'{prefix}balance[{t}]']];rhs=math.fsum(dd['load_mw'][b][t]-fixed[b]for b in nodes);close(row['lb'],rhs,'balance RHS excludes VRE reference');close(row['ub'],rhs,'balance equality');require(dict(row['terms'])==expected,'all thermal/BESS/VRE balance terms');derivative_rows+=1
   for line,z in lines.items():
    constant=math.fsum(z['ptdf'][b]*(fixed[b]-dd['load_mw'][b][t])for b in nodes)
    for side,sign in(('upper',1.),('lower',-1.)):
     row=q['rows'][ri[f'{prefix}line_{side}[{line},{t}]']];rhs=sign*dd['network'][line]['upper_mw'if side=='upper'else'lower_mw'][t]-sign*constant;close(row['ub'],rhs,'PTDF RHS');require(row['lb']is None,'upper-oriented row')
     terms={normvi[f'{prefix}power[{uid},{t}]']:sign*z['ptdf'][u['node']]for uid,u in R.items()if z['ptdf'][u['node']]};terms.update({normvi[f'{prefix}vre_power[{b},{t}]']:sign*z['ptdf'][b]for b in V if z['ptdf'][b]})
     if not prefix:terms[normvi[f'slack_{"up"if side=="upper"else"down"}[{line},{t}]']]=-1.
     actual=dict(row['terms']);require(set(terms)==set(actual),'network derivative inventory')
     for j,c in terms.items():close(actual[j],c,'PTDF coefficient',1e-12)
     derivative_rows+=1
 prices={b:[]for b in nodes}
 for b in nodes:
  for t,g in enumerate(d['grid']):
   value=duals[f'balance[{t}]']+math.fsum(z['ptdf'][b]*(duals[f'line_upper[{line},{t}]']-duals[f'line_lower[{line},{t}]'])for line,z in lines.items())
   value+=math.fsum(duals[f'physical_balance[{j}]']+math.fsum(z['ptdf'][b]*(duals[f'physical_line_upper[{line},{j}]']-duals[f'physical_line_lower[{line},{j}]'])for line,z in lines.items())for j in x['market_to_physical'][t]);prices[b].append(value/g['objective_weight_h'])
 require(len(prices)==39 and all(len(z)==98 for z in prices.values()),'3822 prices')
 # Explicit conservation/averaging and service boundaries on the actual new PRICE primal.
 max_vre_identity=0.;price_curtail_D=0.;minimum_positive=minimum_accident=math.inf
 for b in V:
  for j in range(576):
   pv=r['primal'][normvi[f'physical_vre_power[{b},{j}]']];cv=r['primal'][normvi[f'physical_vre_curtailment[{b},{j}]']];av=vre['physical_availability_mw'][b][j]
   max_vre_identity=max(max_vre_identity,abs(pv+cv-av));require(min(pv,cv)>=-1e-6 and max(pv,cv)<=av+1e-6,'actual VRE hard bounds')
   if j<288:price_curtail_D+=cv/12
  for t,row in enumerate(vre['rows'][b]):
   pv=r['primal'][normvi[f'vre_power[{b},{t}]']];cv=r['primal'][normvi[f'vre_curtailment[{b},{t}]']]
   close(pv+cv,row['availability_mw'],'market VRE conservation');close(pv*3,math.fsum(r['primal'][normvi[f'physical_vre_power[{b},{j}]']]for j in row['physical_indices']),'VRE physical mean')
   require(row['price_power_lower_mw']-1e-6<=pv<=row['price_power_upper_mw']+1e-6,'VRE actual DA band')
 require(max_vre_identity<=1e-6,'physical VRE conservation')
 for j in range(576):
  require(x['reserve'][j]['positive_mw']==400 and x['reserve'][j]['accident_mw']==400,'original independent400 requirements')
  positive=math.fsum(r['primal'][normvi[f'physical_up[{uid},{j}]']]for uid in R);accident=math.fsum(r['primal'][normvi[f'physical_accident[{uid},{j}]']]for uid in R)
  require(min(positive,accident)>=400-1e-6,'actual separate400 reserve');minimum_positive=min(minimum_positive,positive);minimum_accident=min(minimum_accident,accident)
  for uid,u in R.items():
   row=x['resources'][uid]['rows'][j]
   if u['kind']=='generator':require(row['agc_up_reserved_mw']==row['agc_down_reserved_mw']==(10. if j<288 else 0.),'bound new source AGC, not oldcase zero')
   power=r['primal'][normvi[f'physical_power[{uid},{j}]']]
   for service in('up','primary','accident'):
    reserve=r['primal'][normvi[f'physical_{service}[{uid},{j}]']];require(reserve<=row['max_mw']-power+1e-6,'AGC-reserve shared physical headroom')

 return dict(prices=prices,KKT=kkt,verified_derivative_rows=derivative_rows,max_VRE_violation=max_vre_identity,minimum_positive_MW=minimum_positive,minimum_accident_MW=minimum_accident)
