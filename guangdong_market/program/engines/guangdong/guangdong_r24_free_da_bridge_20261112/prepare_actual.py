"""Zero-native preparation from newly audited diagnostic SCED artifacts."""
from pathlib import Path
import json,sys
from hashlib import sha256
from datetime import datetime
from smm.gd_october_job_codec import loads
from smm.gd_r14_da_lmp import build_model,serialize
from r24_bridge import *
from r24_provenance import bind_scuc_event
ROOT=Path('/workspace/scratch/42c5fe32dad0')
def read(p):return json.loads(p.read_bytes())
def sha(p):return sha256(p.read_bytes()).hexdigest()
def main():
 base=ROOT/'guangdong_after_reset_r23_diagnostic_sced_20261112';review=base/'actual_review';prep=base/'prepared';a=read(review/'DIAGNOSTIC_SCED_AUDIT_RECEIPT.json');meta=read(prep/'DIAGNOSTIC_PREPARATION_RECEIPT.json')
 for path,key in [(Path(a['expanded_response_path']),'expanded_response_sha256'),(Path(a['expanded_names_path']),'expanded_names_sha256'),(Path(a['original_input_path']),'original_input_sha256'),(Path(a['fixed_integer_spectrum_path']),'fixed_integer_spectrum_sha256'),(base/'native_once_v1/request.json','native_request_sha256'),(base/'native_once_v1/response.json','native_response_sha256')]:require(sha(path)==a[key],'actual artifact changed: '+str(path))
 require(a['SCED_native_status']=='Optimal'and a['SCUC_accepted']is False and a['formal_publication_allowed']is False,'diagnostic source status')
 require(all(a[k]['max_primal_violation']<=1e-6 and a[k]['max_dual_sign_violation']<=1e-6 and a[k]['max_complementarity']<=1e-6 and a[k]['absolute_gap']<=1e-4 for k in ('compact_KKT','expanded_full_KKT')),'new source KKT failed')
 case=loads(Path(a['original_input_path']).read_bytes());report=read(review/'physical_solution.json');require(report['source_expanded_response_sha256']==a['expanded_response_sha256']and report['SCED_audit_receipt_sha256']==sha(review/'DIAGNOSTIC_SCED_AUDIT_RECEIPT.json'),'physical report provenance')
 response=read(Path(a['expanded_response_path']));names=read(Path(a['expanded_names_path']));values=dict(zip(names['variables'],response['primal']));require(len(values)==len(response['primal']),'exact expanded values inventory')
 receipt=dict(schema=SCHEMA+'.source_receipt',stage='fixed_SCED',status='Optimal',run_id='R23-new-diagnostic-SCED-'+a['native_response_sha256'][:16],scuc_run_id='R23-nonoptimal-SCUC-'+a['source_response_sha256'][:16],case_sha256=case_digest(case),physical_report_sha256=digest(report),expanded_values_sha256=digest(values),source_kind='native_highs_sced',logical_available_at=case['contract']['publication_at'],generated_at=datetime.now().astimezone().isoformat(),SCUC_accepted=False,diagnostic_only=True,publication_allowed=False,new_fixed_sced_primal_KKT_verified=True,SCUC_fixed_discrete_match_verified=True,SCUC_request_sha256=meta['source_original_payload_sha256'],SCUC_response_sha256=a['source_response_sha256'],SCED_request_sha256=a['native_request_sha256'],SCED_response_sha256=a['native_response_sha256'],expanded_witness_source_sha256=a['expanded_response_sha256'],source_audit_sha256=sha(review/'DIAGNOSTIC_SCED_AUDIT_RECEIPT.json'),source_gap=a['source_relative_gap'])
 event=bind_scuc_event(ROOT/'guangdong_after_reset_r23_compressed_native_20261112/native_once_v1',ROOT/'guangdong_after_reset_r23_compression_review_20261112/prepared_payload',ROOT/'guangdong_after_reset_r23_compression_review_20261112/actual_post_review/POST_MIP_REVIEW.json',a['source_response_sha256'],meta['source_postreview_sha256']);receipt['original_full_math_request_sha256']=receipt['SCUC_request_sha256'];receipt.update(event)
 spec=read(ROOT/'guangdong_r24_free_da_bridge_20261112/RESEARCH_PRICING_SPEC.json');require(spec['new_case_sha256']==case_digest(case),'spec case binding')
 bridge=build_price_dto(case,report,values,receipt,spec);model,d,audit=build_model(bridge['DTO']);payload,vs,cs=serialize(model);payload['options']['time_limit']=60.
 out=ROOT/'guangdong_r24_free_da_bridge_20261112/prepared_actual_v2';out.mkdir(exist_ok=False)
 for name,obj in [('bridge.json',bridge),('input.json',d),('template.json',payload),('names.json',dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])),('PURE_PREPARATION.json',dict(audit=audit,source_receipt=receipt,SCUC_accepted=False,publication_allowed=False,diagnostic_only=True,native_calls=0))]: (out/name).write_bytes(canonical(obj))
 (out/'PINS.json').write_bytes(canonical({p.name:sha(p)for p in out.iterdir()}));print(str(out))
if __name__=='__main__':main()
