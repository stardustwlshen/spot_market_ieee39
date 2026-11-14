import json,hashlib,math,argparse
from pathlib import Path
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def audit(prepared,native):
 p=Path(prepared);raw=Path(native);label=p.name
 n=load(p/'names.json');q=load(raw/'request.json');r=load(raw/'response.json');ident=load(raw/'IDENTITY.json');launch=load(raw/'launch.json');done=load(raw/'CONFIRMED_NATIVE_RESULT.json');meta=load(p/'PREPARATION.json')
 for f,h in meta['artifacts'].items():assert sha(p/f)==h
 for path,h in meta['source_pins'].items():assert sha(Path(path))==h
 assert {k:v for k,v in q.items() if k!='deadline'}==load(p/'template.json')
 assert ident['preparation_sha256']==sha(p/'PREPARATION.json') and ident['request_sha256']==sha(raw/'request.json')
 assert ident['worker_sha256']==launch['worker_sha256']=='dc439a81e2cb2eb2a3595d24f0c8ead08ffa633387a259a868f1d0d9fcdc46ea'
 assert q['dual'] is True and launch['attempt']==1 and done['returncode']==0 and done['pid']==launch['pid']
 assert done['response_sha256']==sha(raw/'response.json')
 out={'label':label,'source_transport_only_delta':True,'attempt':1,'pid':launch['pid'],'native_status':r['native_status'],'raw_hashes':{f.name:sha(f)for f in raw.iterdir()if f.is_file()},'new_solver_calls':0}
 if r['status']!='optimal' or r.get('native_status')!='Optimal' or not r.get('dual_valid') or not r.get('has_primal'):
  return dict(out,status='unpriced_pending',raw_price=None,market_price=None,settlement_price=None,reason='nonOptimal or missing valid primal/dual; no old-run substitution')
 x=r['primal'];y=r['dual'];assert len(x)==len(q['variables'])and len(y)==len(q['rows'])
 assert len(n['constraints'])==len(y) and len(n['variables'])==len(x) and len(set(n['constraints']))==len(y)
 assert len(set(n['variables']))==len(x) and q['objective']['minimize'] is True
 assert all(math.isfinite(v)for v in x+y)
 c=[0.]*len(x)
 for j,v in q['objective']['terms']:c[j]+=v
 rc=c[:];pviol=0.;dviol=0.;comp=0.;db=q['objective']['constant'];families={}
 for name,row,z in zip(n['constraints'],q['rows'],y):
  ax=math.fsum(v*x[j]for j,v in row['terms']);lo=row['lb'];hi=row['ub'];viol=max(0,lo-ax if lo is not None else 0,ax-hi if hi is not None else 0);pviol=max(pviol,viol);family=name.split('[')[0];families[family]=max(families.get(family,0),viol)
  for j,v in row['terms']:rc[j]-=v*z
  if z>0:
   if lo is None:dviol=max(dviol,z)
   else:db+=z*lo;comp=max(comp,abs(z*(ax-lo)))
  elif z<0:
   if hi is None:dviol=max(dviol,-z)
   else:db+=z*hi;comp=max(comp,abs(z*(hi-ax)))
 for j,(v,z)in enumerate(zip(q['variables'],rc)):
  lo=v['lb'];hi=v['ub'];pviol=max(pviol,0,lo-x[j]if lo is not None else 0,x[j]-hi if hi is not None else 0)
  if z>0:
   if lo is None:dviol=max(dviol,z)
   else:db+=z*lo;comp=max(comp,abs(z*(x[j]-lo)))
  elif z<0:
   if hi is None:dviol=max(dviol,-z)
   else:db+=z*hi;comp=max(comp,abs(z*(hi-x[j])))
 obj=q['objective']['constant']+math.fsum(v*x[j]for j,v in q['objective']['terms'])
 assert pviol<1e-7 and dviol<1e-7 and comp<1e-5 and abs(obj-db)<1e-4 and abs(obj-r['objective'])<1e-5,(pviol,dviol,comp,obj-db)
 inp=load(p/'input.json');du=dict(zip(n['constraints'],y));assert len(inp['nodes'])==39;prices={bus:(du['balance[0]']+math.fsum((du[f'line_upper[{l},0]']-du[f'line_lower[{l},0]'])*line['ptdf'][bus]for l,line in inp['network'].items()))/.25 for bus in inp['nodes']}
 out['first_quarter_dual_load_derivative_yuan_per_mw']={bus:price*.25 for bus,price in prices.items()}
 out['derivative_scope']='dual-supported RHS sensitivity with frozen model coefficients/bounds, not finite-difference reoptimization or uniqueness/differentiability claim'
 out.update({'status':'verified_research_raw_price','market_price':None,'settlement_price':None,'all_rows':len(y),'all_columns':len(x),'max_primal_violation':pviol,'max_dual_sign_violation':dviol,'max_complementarity_yuan':comp,'objective':obj,'recomputed_dual_objective':db,'duality_gap_yuan':obj-db,'constraint_family_max_violation':families,'first_quarter_research_nodal_price_yuan_mwh':prices,'price_is_official':False,'price_is_executable':False,'price_dual_unique_claim':False,'quarter_energy_mwh':{s:x[j]for j,s in enumerate(n['variables'])if s.startswith('energy[')},'reserve_aggregate_rows':[{'name':s,'row':q['rows'][i],'activity':math.fsum(v*x[j]for j,v in q['rows'][i]['terms'])}for i,s in enumerate(n['constraints'])if 'reserve' in s]})
 return out

def review(plan_path,native,output):
 plan=load(plan_path);results=[]
 for item in plan['items']:
  p=Path(item['prepared']);assert sha(p/'PREPARATION.json')==item['preparation_sha256']
  try:results.append(audit(p,Path(native)/('W'+str(item['window']))))
  except Exception as exc:results.append(dict(window=item['window'],status='review_failed_pending',raw_price=None,market_price=None,settlement_price=None,error=type(exc).__name__+': '+str(exc)))
 o=Path(output);o.mkdir(exist_ok=False);(o/'BATCH_REVIEW.json').write_text(json.dumps(dict(new_native_calls=0,results=results,formal_publication_allowed=False,no_RT_or_old_PRICE_backfill=True),indent=2))
 print(json.dumps({str(z.get('label',z.get('window'))):z['status']for z in results}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();[ap.add_argument('--'+k,required=True)for k in ('plan','native','output')];a=ap.parse_args();review(a.plan,a.native,a.output)
