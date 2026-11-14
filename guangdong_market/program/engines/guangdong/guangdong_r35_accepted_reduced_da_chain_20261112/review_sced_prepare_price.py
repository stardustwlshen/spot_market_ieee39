"""One new actual SCED KKT/lift, then new source-bound PRICE preparation. No solve."""
import argparse
from datetime import datetime
from common import *
from smm.gd_october_job_codec import loads
from smm.gd_r23_equivalent_compression import lift
from independent_price_math import certificate
from physical_view import build_physical
from r35_pipeline import load_event,prepare_source_receipt
from r24_bridge import build_price_dto,case_digest,digest
from smm.gd_r14_da_lmp import build_model,serialize

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prepared',type=Path,required=True);ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();m=read(a.prepared/'PREPARATION.json')
 for f,h in m['artifact_hashes'].items():req(sha(a.prepared/f)==h,'SCED preparation pin '+f)
 for f,h in m['source_pins'].items():req(sha(f)==h,'SCED source pin '+f)
 scuc_native=Path(m['scuc_paths']['request']).parent;scuc_event,scuc_assessment,proofs=accepted_r34(scuc_native,Path(m['scuc_audit']))
 e=load_event(event_paths(a.native));q=e['request'];r=e['response'];req({k:v for k,v in q.items()if k!='deadline'}==read(a.prepared/'template.json'),'actual SCED template identity')
 req(r['status']=='optimal'and r['native_status']=='Optimal'and r['dual_valid']is True and r['has_primal']is True,'actual Optimal SCED with valid dual')
 compact=certificate(q,r,read(a.prepared/'names.json'));compact.pop('details',None)
 full=read(a.prepared/'fixed_full_payload.json');names=read(a.prepared/'full_names.json');plan=read(a.prepared/'compression_plan.json');x=lift(full,plan,r['primal']);y=[0.]*len(full['rows'])
 for j,v in zip(plan['kept_rows_original_indices'],r['dual']):y[j]=v
 expanded={**r,'primal':x,'dual':y};whole=certificate(full,expanded,names);whole.pop('details',None)
 spectrum=read(a.prepared/'fixed_integer_spectrum.json');req(len(spectrum['values'])==16560,'complete integer spectrum')
 for z in spectrum['values']:
  j=z['index'];req(names['variables'][j].replace("'",'')==z['name']and x[j]==z['value']and full['variables'][j]['lb']==full['variables'][j]['ub']==z['value'],'same selected discrete decision')
 req(sha(m['original_input_path'])==m['original_input_sha256'],'physical input provenance');case=loads(Path(m['original_input_path']).read_bytes());values={n.replace("'",''):v for n,v in zip(names['variables'],x)};req(len(values)==len(x),'normalized complete names')
 a.output.mkdir(exist_ok=False);write(a.output/'expanded_response.json',expanded);write(a.output/'expanded_values.json',values)
 report=build_physical(case,names,expanded,{'SCUC_request_sha256':scuc_event['pins']['request']['sha256'],'SCUC_response_sha256':scuc_event['pins']['response']['sha256'],'SCED_request_sha256':e['pins']['request']['sha256'],'SCED_response_sha256':e['pins']['response']['sha256'],'expanded_response_sha256':sha(a.output/'expanded_response.json')});write(a.output/'physical_solution.json',report)
 audit={'schema':'gd.research.r35.accepted_fixed_SCED.audit.v1','status':'pass_new_conditional_SCED','auditor_solver_calls':0,'native_request_sha256':e['pins']['request']['sha256'],'native_response_sha256':e['pins']['response']['sha256'],'compact_KKT':compact,'expanded_full_KKT':whole,'fixed_original_integers':16560,'SCUC_accepted':True,'SCUC_expanded_values_sha256':sha(a.prepared/'SCUC_expanded_values.json'),'SCED_expanded_values_sha256':sha(a.output/'expanded_values.json'),'fixed_spectrum_sha256':sha(a.prepared/'fixed_integer_spectrum.json'),'physical_report_content_sha256':digest(report),'expanded_response_sha256':sha(a.output/'expanded_response.json'),'original_input_sha256':sha(m['original_input_path']),'formal_publication_allowed':False,'research_publication_allowed':False,'execution_authority':False};write(a.output/'SCED_REVIEW.json',audit)
 receipt=prepare_source_receipt(case,report,values,scuc_paths=m['scuc_paths'],scuc_audit=m['scuc_audit'],proof_files=proofs,sced_paths=event_paths(a.native),sced_audit=str(a.output/'SCED_REVIEW.json'),scuc_values_path=str(a.prepared/'SCUC_expanded_values.json'),scuc_values_sha256=audit['SCUC_expanded_values_sha256'],sced_values_path=str(a.output/'expanded_values.json'),sced_values_sha256=audit['SCED_expanded_values_sha256'],spectrum_path=str(a.prepared/'fixed_integer_spectrum.json'),spectrum_sha256=audit['fixed_spectrum_sha256'],logical_available_at=case['contract']['publication_at'],generated_at=datetime.now().astimezone().isoformat())
 spec=read(R24/'RESEARCH_PRICING_SPEC.json');req(spec['new_case_sha256']==case_digest(case),'same explicit research pricing assumptions')
 bridge=build_price_dto(case,report,values,receipt,spec);model,d,model_audit=build_model(bridge['DTO']);payload,vs,cs=serialize(model);payload['options']['time_limit']=60.;payload.pop('deadline',None);payload['preparation_elapsed_s']=0.
 out=a.output/'price_prepared';out.mkdir(exist_ok=False)
 for name,v in [('bridge.json',bridge),('input.json',d),('template.json',payload),('names.json',{'variables':[z.name for z in vs],'constraints':[z.name for z in cs]}),('PURE_PREPARATION.json',{'schema':'gd.research.r35.accepted_source.PRICE.preparation.v1','source_receipt':receipt,'model_audit':model_audit,'SCUC_accepted':True,'formal_publication_allowed':False,'research_publication_allowed':False,'native_calls':0,'allowed_native_calls':1,'SCED_audit_path':str((a.output/'SCED_REVIEW.json').resolve()),'SCED_physical_path':str((a.output/'physical_solution.json').resolve()),'SCED_paths':event_paths(a.native),'SCUC_paths':m['scuc_paths'],'SCUC_audit_path':m['scuc_audit'],'proof_files':proofs})]:write(out/name,v)
 write(out/'PINS.json',{p.name:sha(p)for p in out.iterdir()if p.is_file()});print(str(out))
if __name__=='__main__':main()
