"""One R34 actual reduced certificate, DC backsubstitution, original full audit.
No solver or automatic follow-on. Only Optimal+0.1% can enable accepted SCED.
"""
import argparse,math,importlib.util
from common import *
from smm.gd_r23_equivalent_compression import lift as original_lift
spec=importlib.util.spec_from_file_location('original_primal_audit',ROOT/'guangdong_after_reset_r23_compression_review_20261112/post_mip_prepare_sced.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
net_spec=importlib.util.spec_from_file_location('r34_frozen_elimination',R34/'network_elimination.py');network=importlib.util.module_from_spec(net_spec);net_spec.loader.exec_module(network)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();m=read(R34/'prepared/PURE_PREPARATION.json')
 req(sha(R34/'prepared/PURE_PREPARATION.json')=='524cdd007fd6a3071bd5d434cf8351209f33fcd770657898837cc8fda8e31814','frozen R34 preparation')
 for group in ('source_pins','input_pins'):
  for p,h in m[group].items():req(sha(p)==h,'immutable '+group+': '+p)
 for p,h in m['artifact_hashes'].items():req(sha(R34/'prepared'/p)==h,'prepared artifact '+p)
 actual_worker=verified_worker_override(a.native)
 e=load_event(event_paths(a.native));q=e['request'];r=e['response'];req({k:v for k,v in q.items()if k!='deadline'}==read(R34/'prepared/template.json'),'actual original-domain reduced template')
 req('research_restriction'not in q and sum(z['kind']!='C'and z['lb']!=z['ub']for z in q['variables'])==2160,'all global decisions free')
 req(r['native_settings']=={'output_flag':True,**m['native_profile_requested']},'actual native requested/readback profile')
 nplan=read(R34/'prepared/network_elimination_plan.json');orig=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1/ieee39_separate_prepared_scuc';pre=ROOT/'guangdong_after_reset_r23_compression_review_20261112/prepared_payload'
 req(nplan['source_payload_sha256']==sha(R25/'prepared/template.json')and nplan['original_full_payload_sha256']==sha(orig/'request.json')and nplan['original_compression_plan_sha256']==sha(pre/'compression_plan.json'),'full global transformation chain')
 a.output.mkdir(exist_ok=False)
 summary={'schema':'gd.research.r35.actual_R34_original_global_review.v1','auditor_solver_calls':0,'native_execution_performed_by_auditor':False,'confirmed_native_calls':1,'native_request_sha256':e['pins']['request']['sha256'],'raw_response_sha256':e['pins']['response']['sha256'],'native_status':r['native_status'],'native_has_primal':r['has_primal'],'accepted':False,'SCUC_accepted':False,'formal_publication_allowed':False,'research_publication_allowed':False,'execution_authority':False,'native_bound_domain':'original full free-SCUC projected through bound numerical DC inverse','native_bound_valid_for_original_SCUC':True,'all_2160_global_decisions_free':True,'network_elimination_plan_sha256':sha(R34/'prepared/network_elimination_plan.json'),'original_R25_template_sha256':sha(R25/'prepared/template.json'),'native_settings_verified':r['native_settings'],'native_counters':r.get('native_counters'),'native_run_wall_time_s':r.get('native_run_wall_time_s'),'time_settings':r.get('time_settings'),'automatic_retry':False,'new_RT_calls':0,'preparation_sha256':sha(R34/'prepared/PURE_PREPARATION.json'),'numerical_elimination_profile_sha256':sha(R34/'prepared/NUMERICAL_STORAGE_PROFILE.json'),'worker_override_and_actual_storage':actual_worker,'native_matrix_warning_lines':[s for s in (a.native/'worker.log').read_text().splitlines()if 'warning'in s.lower()or 'ignored'in s.lower()]}
 if r['has_primal']:
  req(r['status']in ('optimal','maxTimeLimit'),'confirmed usable incumbent')
  reduced=old.independent_primal(q,r['primal'],r['objective']);cq=read(R25/'prepared/template.json');cx=network.lift(nplan,r['primal']);compact=old.independent_primal(cq,cx,r['objective']);full=read(orig/'request.json');fn=read(orig/'names.json');plan=read(pre/'compression_plan.json');x=original_lift(full,plan,cx);cert=old.independent_primal(full,x,r['objective'])
  req(all(cx[j]==v for j,v in zip(nplan['kept_original_compact_columns'],r['primal'])),'no retained power/SOC/segments/events/hour decisions changed by network lift')
  allowed=set(plan['guard']['thermal_relax']+plan['guard']['battery_relax']);changed=[j for j,v in zip(plan['kept_columns'],cx)if x[j]!=v];req(set(changed)<=allowed,'original lift altered physical/retained decisions')
  cuts=read(R25/'prepared/added_rows.json')['rows'];worst=max([0.]+[math.fsum(c*cx[j]for j,c in row['terms'])-row['ub']for row in cuts]);req(worst<=1e-6,'all original valid ramp cuts')
  gap=(r['objective']-r['bound'])/max(abs(r['objective']),1e-12);req(0<=gap and abs(gap-r['native_mip_gap'])<=1e-12,'actual bound/gap')
  accepted=r['status']=='optimal'and r['native_status']=='Optimal'and gap<=.001 and r['native_mip_gap']<=.001
  write(a.output/'full_lifted_primal.json',x);values={n.replace("'",''):v for n,v in zip(fn['variables'],x)};req(len(values)==len(x),'unique normalized map');write(a.output/'expanded_values.json',values)
  spectrum=[{'index':j,'name':name.replace("'",''),'value':round(v)}for j,(name,v,z)in enumerate(zip(fn['variables'],x,full['variables']))if z['kind']!='C'];req(len(spectrum)==16560,'complete original integer spectrum');write(a.output/'fixed_integer_spectrum.json',{'integer_count':16560,'values':spectrum})
  base=read(R25/'independent_actual/result_v1/POST_MIP_REVIEW.json');delta=max(abs(v-z['start'])for v,z in zip(r['primal'],q['variables']))
  summary.update(status='accepted_original_full_SCUC'if accepted else'verified_original_feasible_candidate_not_accepted',accepted=accepted,SCUC_accepted=accepted,reduced_certificate=reduced,recovered_R25_compact_certificate=compact,full_original_certificate=cert,strong_ramp_certificate={'rows':5760,'maximum_violation':worst},relative_gap=gap,native_gap=r['native_mip_gap'],reported_bound=r['bound'],original_v1_request_sha256=sha(orig/'request.json'),original_names_sha256=sha(orig/'names.json'),compression_plan_sha256=sha(pre/'compression_plan.json'),full_lifted_primal_sha256=sha(a.output/'full_lifted_primal.json'),SCUC_expanded_values_sha256=sha(a.output/'expanded_values.json'),fixed_spectrum_sha256=sha(a.output/'fixed_integer_spectrum.json'),maximum_native_vs_start_difference=delta,incumbent_improvement_over_start_yuan=m['warmstart_objective']-r['objective'],independent_prior_global_bound=base['reported_bound'],full_domain_bound_improvement=r['bound']-base['reported_bound'],bound_evidence='Same original integer-domain projection with explicit numerical inverse/threshold error envelope and actual full-original primal validation. Native reported search bound is not an independently replayed search tree.')
 else:summary['status']='confirmed_native_no_primal'
 summary['artifact_hashes']={p.name:sha(p)for p in a.output.iterdir()if p.is_file()};write(a.output/'POST_MIP_REVIEW.json',summary);print(json.dumps(summary))
if __name__=='__main__':main()
