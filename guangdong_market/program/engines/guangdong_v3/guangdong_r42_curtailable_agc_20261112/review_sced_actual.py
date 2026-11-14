"""Post actual new SCED: full KKT and explicit VRE dispatch physical series.
No PRICE, publication, runtime, payments, or optimizer calls.
"""
from prepare import *
import math
sys.path[:0]=[str(ROOT/'guangdong_r35_accepted_reduced_da_chain_20261112'),str(ROOT/'guangdong_r19_day_checkpoints_20261112/independent_actual_day_v1/helpers')]
from common import event_paths
from r35_pipeline import load_event
from scoped_kkt import certificate
from smm.gd_r23_equivalent_compression import lift,audit
from r42_physical_view import build_physical

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prepared',type=Path,required=True);ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 m=read(a.prepared/'PREPARATION.json');need(m['SCUC_accepted']is True and sha(m['SCUC_review_path'])==m['SCUC_review_sha256'],'same accepted new SCUC')
 for f,h in m['artifact_hashes'].items():need(sha(a.prepared/f)==h,'prepared '+f)
 agcp=a.prepared.parent/'ACTUAL_AGC_SELECTION_TO_SCED.json';agc=read(agcp);need(agc['status']=='pass_24_hours_5x10MW'and agc['fixed_SCED_preparation_sha256']==sha(a.prepared/'PREPARATION.json'),'same new actual-state AGC selection')
 e=load_event(event_paths(a.native));q=e['request'];r=e['response'];need({k:v for k,v in q.items()if k!='deadline'}==read(a.prepared/'template.json'),'same new SCED template')
 ident=read(a.native/'IDENTITY.json');need(sha(agcp) in json.dumps(ident),'native identity explicitly binds selection hash')
 need(r['status']=='optimal'and r['native_status']=='Optimal'and r['has_primal']is True and r['dual_valid']is True,'actual new SCED Optimal')
 cc=certificate(q,r,read(a.prepared/'names.json'));cc.pop('details',None)
 full=read(a.prepared/'fixed_full_payload.json');names=read(a.prepared/'full_names.json');plan=read(a.prepared/'compression_plan.json');x=lift(full,plan,r['primal']);y=[0.]*len(full['rows'])
 for j,v in zip(plan['kept_rows_original_indices'],r['dual']):y[j]=v
 expanded={**r,'primal':x,'dual':y};fc=certificate(full,expanded,names);fc.pop('details',None)
 # The correct new unrestricted SCUC matrix, NOT the old fixed-VRE model.
 original_new=read(BASE/'prepared_v1/full_template.json');original_cert=audit(original_new,x,objective=r['objective'])
 spectrum=read(a.prepared/'fixed_integer_spectrum.json');need(len(spectrum['values'])==16560,'same original integer inventory')
 for z in spectrum['values']:
  i=z['index'];need(names['variables'][i].replace("'",'')==z['name']and x[i]==z['value']and full['variables'][i]['lb']==full['variables'][i]['ub']==z['value'],'same selected spectrum')
 ip=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1/ieee39_separate_prepared_input.json';case=loads(ip.read_bytes());meta=read(BASE/'prepared_v1/PREPARATION.json');source=dict(SCUC_review_sha256=m['SCUC_review_sha256'],actual_AGC_selection_sha256=sha(agcp),SCED_request_sha256=e['pins']['request']['sha256'],SCED_response_sha256=e['pins']['response']['sha256'],new_model_full_template_sha256=sha(BASE/'prepared_v1/full_template.json'))
 physical=build_physical(case,names,expanded,source,curtailment_penalty=m['curtailment_penalty_yuan_mwh']);ix={s.replace("'",''):i for i,s in enumerate(names['variables'])};ri={s:i for i,s in enumerate(names['constraints'])};maxbalance=maxbound=0.;margins=[]
 for t,row in enumerate(physical['physical_cells']):
  for z in row['renewable'].values():maxbalance=max(maxbalance,abs(z['availability_mw']-z['dispatched_mw']-z['curtailed_mw']));maxbound=max(maxbound,0.,-z['dispatched_mw'],-z['curtailed_mw'],z['dispatched_mw']-z['availability_mw'],z['curtailed_mw']-z['availability_mw'])
  if t<288:
   selection=agc['hours'][t//12]['selection']['awards_mw'];need(set(selection)==set(row['thermal']),'five declared providers')
   for gi,(g,d)in enumerate(case['energy_inputs']['thermal'].items()):
    z=row['thermal'][g];need(z['phase']=='stable' and z['agc_up_reserved_mw']==z['agc_down_reserved_mw']==selection[g]==10.,'actual stable same AGC award')
    p=ix[f'power[{g},{t}]'];lo=d['min_mw'][t]+10;hi=d['max_mw'][t]-10;need(lo-1e-6<=x[p]<=hi+1e-6,'AGC band')
    for var in('primary','contingency'):margins.append(hi-x[p]-x[ix[f'{var}[{g},{t}]']])
  need(full['rows'][ri[f'reserves[{24*t+24}]']]['lb']==400,'accident400 maintained')
 need(maxbalance<=1e-6 and maxbound<=1e-6 and min(margins)>=-1e-6,'new VRE/capacity conservation')
 totals=lambda first,last:{key:math.fsum(z[key]for row in physical['physical_cells'][first:last]for z in row['renewable'].values())/12 for key in('availability_mw','dispatched_mw','curtailed_mw')}
 D=totals(0,288);whole=totals(0,576);interval=totals(89,169);lower=read(BASE/'prepared_v1/PURE_OBJECTIVE_SENSITIVITY.json')['curtailment_energy_necessary_lower_mwh'];need(interval['curtailed_mw']>=lower-1e-5,'necessary R39 interval curtailment')
 a.output.mkdir(exist_ok=False);write(a.output/'expanded_response.json',expanded);write(a.output/'physical_solution.json',physical)
 report=dict(schema='gd.research.r42.new_SCED.actual_review.v1',status='pass_new_curtailable_AGC_SCED',numerical_budget='Only five identified free angle columns allow absolute stationarity1e-7; other dual-sign1e-8/primal1e-6/objective gates unchanged',legacy_strict_review_failed=True,legacy_strict_failure_path=str(BASE/'strict_sced_kkt_failure_v1'),raw_primal_dual_model_unchanged=True,auditor_solver_calls=0,compact_KKT=cc,new_fixed_full_KKT=fc,new_free_SCUC_matrix_feasibility=original_cert,same_fixed_integer_count=16560,VRE_energy_MWh_D=D,VRE_energy_MWh_48h=whole,VRE_energy_MWh_R39_interval=interval,R39_curtailment_lower_MWh=lower,VRE_identity_max_violation=maxbalance,VRE_bound_max_violation=maxbound,AGC_shared_capacity_min_margin=min(margins),positive_and_accident_reserve_requirements_MW=400,thermal_cost_yuan=physical['thermal_cost_yuan'],included_event_cost_yuan=physical['event_cost_yuan'],storage_bid_cost_yuan=physical['storage_bid_cost_yuan'],research_curtailment_penalty_cost_yuan=physical['curtailment_penalty_cost_yuan'],total_objective_yuan=r['objective'],actual_AGC_selection_sha256=sha(agcp),native_event_pins=e['pins'],expanded_response_sha256=sha(a.output/'expanded_response.json'),physical_solution_sha256=sha(a.output/'physical_solution.json'),formal_bill=None,formal_publication_allowed=False,research_publication_allowed=False,execution_authority=False,downstream_must_keep_renewable_availability_separate_from_dispatch=True)
 write(a.output/'SCED_REVIEW.json',report);print(json.dumps({k:report[k]for k in('status','total_objective_yuan','VRE_energy_MWh_D','AGC_shared_capacity_min_margin','physical_solution_sha256')}))
if __name__=='__main__':main()
