"""Accept only actual Optimal R34, then fix its entire original integer spectrum.
Never launches SCED. New source occurrence, no old receipt or capability reuse.
"""
import argparse
from common import *
from smm.gd_october_job_codec import loads
from smm.gd_r23_equivalent_compression import fix_all_original_integers,compress

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--native',type=Path,required=True);ap.add_argument('--audit',type=Path,required=True);ap.add_argument('--expanded',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 event,assessment,proofs=accepted_r34(a.native,a.audit);review=read(a.audit)
 expected=review.get('full_lifted_primal_sha256',review.get('artifact_hashes',{}).get('full_lifted_primal.json'));req(expected and sha(a.expanded)==expected,'audited full original witness required')
 old=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1';qpath=old/'ieee39_separate_prepared_scuc/request.json';npath=qpath.with_name('names.json');ipath=old/'ieee39_separate_prepared_input.json'
 q=read(qpath);names=read(npath);case=loads(ipath.read_bytes());x=read(a.expanded);req(sha(qpath)==review['original_v1_request_sha256']and len(x)==len(names['variables']),'original model/expanded identity')
 fixed=fix_all_original_integers(q,x);fixed.pop('deadline',None);fixed['schema']='gd.research.r35.accepted_SCUC.fixed_SCED.v1';fixed['options'].update(time_limit=60.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False);fixed['warmstart']=False;fixed['dual']=True;fixed['preparation_elapsed_s']=0.
 spectrum=[{'index':i,'name':name.replace("'",''),'value':round(x[i])}for i,(name,z)in enumerate(zip(names['variables'],q['variables']))if z['kind']!='C'];req(len(spectrum)==16560,'all original integers')
 for i,(oldv,newv)in enumerate(zip(q['variables'],fixed['variables'])):
  req(oldv==newv if oldv['kind']=='C'else newv['kind']=='C'and newv['lb']==newv['ub']==round(x[i]),'no continuous decision fixed or changed')
 cq,cn,plan=compress(fixed,names,case);req(all(z['kind']=='C'for z in cq['variables']),'fixed SCED LP')
 values={n.replace("'",''):v for n,v in zip(names['variables'],x)};req(len(values)==len(x),'unique expanded map')
 a.output.mkdir(exist_ok=False)
 for name,v in [('fixed_full_payload.json',fixed),('full_names.json',names),('template.json',cq),('names.json',cn),('compression_plan.json',plan),('fixed_integer_spectrum.json',{'integer_count':16560,'values':spectrum}),('SCUC_expanded_values.json',values)]:write(a.output/name,v)
 meta={'schema':'gd.research.r35.fixed_SCED.preparation.v1','SCUC_accepted':True,'SCUC_assessment':assessment,'scuc_paths':event_paths(a.native),'scuc_audit':str(a.audit.resolve()),'proof_files':proofs,'source_native_request_sha256':event['pins']['request']['sha256'],'source_native_response_sha256':event['pins']['response']['sha256'],'source_full_primal_sha256':sha(a.expanded),'source_gap':assessment['gap'],'commitment_basis':'actual_Optimal_gap_0.1percent_accepted_research_SCUC','original_input_path':str(ipath),'original_input_sha256':sha(ipath),'original_names_sha256':sha(npath),'same_original_integer_spectrum_count':16560,'remaining_integers':0,'columns':len(cq['variables']),'rows':len(cq['rows']),'native_calls':0,'native_calls_allowed':1,'time_limit_s':60,'formal_publication_allowed':False,'research_publication_allowed':False,'execution_authority':False,'new_source_occurrence':True,'legacy_permit_reused':False,'source_pins':{str(p):sha(p)for p in [BASE/'common.py',BASE/'prepare_fixed_sced.py',BASE/'r35_pipeline.py',SOURCE/'smm/gd_r23_equivalent_compression.py']},'artifact_hashes':{p.name:sha(p)for p in a.output.iterdir()if p.is_file()}}
 write(a.output/'PREPARATION.json',meta);print(str(a.output))
if __name__=='__main__':main()
