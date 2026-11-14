from common67 import *
def build_full_input(plan,permit):
 source.validate_contract(plan,permit,recheck_files=True);entry=source._CONTRACTS[permit];data=entry['data'];src=entry['source']
 local=session.pure_template_input(plan,permit,0);inp=deepcopy(local);day=datetime.fromisoformat(plan['day_start']);cutoff=day-timedelta(minutes=15);families={};evidence={}
 for family in ('load_mw','wind_mw','solar_mw','boundary_tie_mw'):
  saved=src['contract']['sources']['prior:'+family];require(hashlib.sha256(saved['artifact']).hexdigest()==saved['sha256'],'prior bytes');prior=json.loads(saved['artifact'])
  require(not prior['uses_future_actual'] and datetime.fromisoformat(saved['available_at'])<=cutoff and datetime.fromisoformat(prior['information_end'])<=cutoff,'full future only existing prior')
  knots=[{r:prior_value(prior,r,day+timedelta(minutes=15*q)) for r in prior['resources']}for q in range(104)];values={n:[0.]*309 for n in inp['nodes']}
  for j in range(309):
   q,k=divmod(j,3)
   for r,z in prior['resources'].items():values[z['bus']][j]+=knots[q][r]+(knots[q+1][r]-knots[q][r])*(k+.5)/3
  families[family]=values;evidence[family]=dict(sha256=saved['sha256'],available_at=saved['available_at'],information_end=prior['information_end'],synthetic_linear_15_to_5=True,future_actual_used=False)
 inp.update(schema='gd.research.r67.frozen_full_input.v1',physical_horizon_cells=309,future_actual_used=False,forecast_locked_at=cutoff.isoformat(),frozen_prior_evidence=evidence,da_cells=deepcopy(plan['physical_cells']),reserve=deepcopy(data['physical_lift']['reserve'][:309]),load_mw=families['load_mw'],fixed_injection_mw=families['boundary_tie_mw'])
 for r,u in inp['resources'].items():u['rows']=deepcopy(data['physical_lift']['resources'][r]['rows'][:309])
 inp['network']={l:dict(ptdf=deepcopy(z['ptdf']),lower_mw=deepcopy(data['physical_lift']['network'][l]['lower_mw'][:309]),upper_mw=deepcopy(data['physical_lift']['network'][l]['upper_mw'][:309]))for l,z in data['network'].items()}
 for b,u in inp['renewable'].items():u.update(availability_mw=[families['wind_mw'][b][j]+families['solar_mw'][b][j]for j in range(309)],SCED_reference_mw=deepcopy(data['physical_lift']['renewable_dispatched_mw'][b][:309]),SCED_curtailed_mw=deepcopy(data['physical_lift']['renewable_curtailed_mw'][b][:309]))
 for n in inp['nodes']:
  require(inp['load_mw'][n][:24]==local['load_mw'][n] and inp['fixed_injection_mw'][n][:24]==local['fixed_injection_mw'][n],'frozen prior/current W0 exact')
 return inp,local
