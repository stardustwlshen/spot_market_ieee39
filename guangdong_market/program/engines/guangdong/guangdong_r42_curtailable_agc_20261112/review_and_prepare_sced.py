"""One actual R42 review, then accepted-only fixed-integer SCED preparation.
No optimization. No acceptance against the obsolete fixed-VRE scenario.
"""
from prepare import *
import math
sys.path[:0]=[str(ROOT/'guangdong_r35_accepted_reduced_da_chain_20261112')]
from r35_pipeline import load_event
from common import event_paths
from network_elimination import lift as net_lift
from smm.gd_r23_equivalent_compression import lift,audit,fix_all_original_integers

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();P=BASE/'prepared_v1';meta=read(P/'PREPARATION.json');ready=read(BASE/'READY.json')
 for path,h in ready['pins'].items():need(sha(path)==h,'ready '+path)
 for f,h in meta['artifact_hashes'].items():need(sha(P/f)==h,'prepared '+f)
 for p,h in meta['source_pins'].items():need(sha(p)==h,'source '+p)
 e=load_event(event_paths(a.native));q=e['request'];r=e['response'];need({k:v for k,v in q.items()if k!='deadline'}==read(P/'template.json'),'actual new scenario template')
 ident=read(a.native/'IDENTITY.json');launch=read(a.native/'launch.json');worker=BASE/'checked_worker_v1/r42_checked_worker.py';policy=BASE/'checked_worker_v1/STORAGE_POLICY.json';need(ident['worker_sha256']==launch['worker_sha256']==sha(worker),'new worker event')
 storage=r['matrix_storage_verification'];imported=read(BASE/'checked_worker_v1/ACTUAL_STORAGE_IMPORT_VERIFICATION.json')
 for k in ('discarded_coefficients','affected_rows','stored_nonzeros','exact_worst_activity_error','all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked'):need(storage[k]==imported[k],'actual new model storage '+k)
 need(r.get('optimize_calls')==1 and r['solver_version']=='1.15.1','one actual original HiGHS event')
 need(r['native_settings']['threads']==1 and r['native_settings']['mip_rel_gap']==.001 and r['native_settings']['mip_abs_gap']==0,'unchanged solver tolerance')
 report=dict(schema='gd.research.r42.new_scenario.actual_review.v1',native_status=r['native_status'],has_primal=r['has_primal'],SCUC_accepted=False,accepted=False,native_event_pins=e['pins'],auditor_solver_calls=0,formal_publication_allowed=False,research_publication_allowed=False,execution_authority=False,scenario_changed_from_fixed_VRE=True,old_fixed_VRE_full_not_used_for_acceptance=True,worker_sha256=sha(worker),storage_policy_sha256=sha(policy),matrix_storage_verification=storage)
 a.output.mkdir(exist_ok=False)
 if not r['has_primal']:
  report['status']='actual_no_primal_not_accepted';write(a.output/'POST_MIP_REVIEW.json',report);print(report['status']);return
 need(r['status']in('optimal','maxTimeLimit'),'known primal status')
 rc=audit(q,r['primal'],objective=r['objective']);compact=read(P/'compact_template.json');np=read(P/'network_elimination_plan.json');cx=net_lift(np,r['primal']);cc=audit(compact,cx,objective=r['objective'])
 full=read(P/'full_template.json');names=read(P/'full_names.json');cp=read(P/'compression_plan.json');x=lift(full,cp,cx);fc=audit(full,x,objective=r['objective'])
 need(all(cx[j]==v for j,v in zip(np['kept_original_compact_columns'],r['primal'])),'network lift retains decisions')
 allowed=set(cp['guard']['thermal_relax']+cp['guard']['battery_relax']);changed=[j for j,v in zip(cp['kept_columns'],cx)if x[j]!=v];need(set(changed)<=allowed,'only proven permission/type lift')
 ix={s.replace("'",''):i for i,s in enumerate(names['variables'])};delta=read(P/'SCENARIO_DELTA.json');inv=delta['inventory'];tol=1e-6
 stable_min=min(x[ix[f'stable[{g},{t}]']]for g in ('C1','C2','C3','CCGT','GT')for t in range(288));need(stable_min>=1-tol,'D AGC all five stable')
 curtail_by_cell=[0.]*576;maxid=0.;maxbound=0.
 for z in inv:
  p,c=x[z['p_index']],x[z['c_index']];A=z['available_mw'];maxid=max(maxid,abs(p+c-A));maxbound=max(maxbound,max(0,-p,-c,p-A,c-A));curtail_by_cell[z['physical_cell']]+=c
 need(maxid<=tol and maxbound<=tol,'VRE original availability and accounting')
 interval=math.fsum(curtail_by_cell[89:169])/12;lower=read(P/'PURE_OBJECTIVE_SENSITIVITY.json')['curtailment_energy_necessary_lower_mwh'];need(interval>=lower-1e-5,'R39 necessary curtailment bound')
 cost=math.fsum(curtail_by_cell)/12*meta['curtailment_penalty_yuan_mwh'];obj=r['objective'];bound=r['bound'];need(all(type(v)in(int,float)and math.isfinite(v)for v in(obj,bound))and bound<=obj+1e-5,'finite lower bound')
 gap=abs(obj-bound)/max(abs(obj),1e-10);accepted=r['status']=='optimal'and r['native_status']=='Optimal'and gap<=.001
 spectrum=[dict(index=i,name=name.replace("'",''),value=round(x[i]))for i,(name,v)in enumerate(zip(names['variables'],full['variables']))if v['kind']!='C'];need(len(spectrum)==16560,'original full integer inventory unchanged')
 report.update(status='pass_accepted_new_scenario'if accepted else'verified_feasible_incumbent_not_accepted',SCUC_accepted=accepted,accepted=accepted,gap=gap,objective=obj,bound=bound,reduced_certificate=rc,compact_certificate=cc,new_full_certificate=fc,new_full_original_integer_count=16560,all_D_thermal_stable_min=stable_min,AGC_MW_each_hour=50,positive_and_accident_reserve_requirement_MW=400,VRE_max_identity_violation_mw=maxid,VRE_max_bound_violation_mw=maxbound,curtailment_MWh_D=math.fsum(curtail_by_cell[:288])/12,curtailment_MWh_48h=math.fsum(curtail_by_cell)/12,curtailment_MWh_R39_interval=interval,R39_interval_necessary_lower_MWh=lower,curtailment_penalty_component_yuan=cost,curtailment_by_cell_MW=curtail_by_cell,declared_qualifications_remain_synthetic=True)
 write(a.output/'full_lifted_primal.json',x);write(a.output/'fixed_integer_spectrum.json',{'values':spectrum,'integer_count':16560});report['full_lifted_primal_sha256']=sha(a.output/'full_lifted_primal.json');report['fixed_integer_spectrum_sha256']=sha(a.output/'fixed_integer_spectrum.json');write(a.output/'POST_MIP_REVIEW.json',report)
 if not accepted:print('incumbent rejected gap/status; no fixed SCED prepared');return
 # Restore full DC for fixed LP: no old network or storage-warning policy borrowed.
 fixed=fix_all_original_integers(full,x);fixed.pop('deadline',None);fixed.update(schema='gd.research.r42.accepted_new_scenario.fixed_SCED.v1',dual=True,warmstart=False,preparation_elapsed_s=0.)
 fixed['options'].update(time_limit=60.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False)
 for k in tuple(fixed['options']):
  if k.startswith('mip_heuristic'):del fixed['options'][k]
 ip=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1/ieee39_separate_prepared_input.json';case=loads(ip.read_bytes());fq,fn,fp=compress(fixed,names,case);need(all(v['kind']=='C'for v in fq['variables']),'fixed SCED LP')
 out=a.output/'sced_prepared_v1';out.mkdir(exist_ok=False)
 for f,z in [('fixed_full_payload.json',fixed),('full_names.json',names),('template.json',fq),('names.json',fn),('compression_plan.json',fp),('fixed_integer_spectrum.json',{'values':spectrum,'integer_count':16560})]:write(out/f,z)
 sm=dict(schema='gd.research.r42.accepted_new_SCED.preparation.v1',SCUC_accepted=True,new_model='R42 curtailable+synthetic AGC, not old fixed VRE',SCUC_review_path=str((a.output/'POST_MIP_REVIEW.json').resolve()),SCUC_review_sha256=sha(a.output/'POST_MIP_REVIEW.json'),SCUC_event_pins=e['pins'],same_integer_spectrum=16560,columns=len(fq['variables']),rows=len(fq['rows']),native_calls=0,native_calls_allowed=1,time_limit_s=60,curtailment_penalty_yuan_mwh=meta['curtailment_penalty_yuan_mwh'],artifact_hashes={p.name:sha(p)for p in out.iterdir()},formal_publication_allowed=False,research_publication_allowed=False,execution_authority=False)
 write(out/'PREPARATION.json',sm);print(str(out));print('template_sha256',sha(out/'template.json'))
if __name__=='__main__':main()
