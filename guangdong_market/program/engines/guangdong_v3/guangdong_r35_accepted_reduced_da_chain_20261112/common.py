"""Paths and zero-solver provenance helpers for a new actual accepted R34 chain."""
from pathlib import Path
import json,hashlib,sys
ROOT=Path('/workspace/scratch/42c5fe32dad0');BASE=Path(__file__).resolve().parent
R34=ROOT/'guangdong_r34_network_elimination_20261112';R25=ROOT/'guangdong_r25_strong_scuc_20261112'
SOURCE=ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'
R26=ROOT/'guangdong_r26_accepted_da_pipeline_20261112';R24=ROOT/'guangdong_r24_free_da_bridge_20261112'
sys.path[:0]=[str(SOURCE),str(R26),str(R24),str(ROOT/'guangdong_r19_day_checkpoints_20261112/independent_actual_day_v1/helpers')]
from smm.gd_r23_equivalent_compression import canonical
from r35_pipeline import load_event,proof_bundle,assess_scuc

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):
 with Path(p).open('xb')as f:f.write(canonical(v))
def req(v,msg):
 if not v:raise ValueError(msg)
def event_paths(native):return {k:str(Path(native)/name)for k,name in [('request','request.json'),('response','response.json'),('identity','IDENTITY.json'),('launch','launch.json'),('confirmed','CONFIRMED_NATIVE_RESULT.json')]}
def proof_files(audit):
 old=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1/ieee39_separate_prepared_scuc/request.json';plan=ROOT/'guangdong_after_reset_r23_compression_review_20261112/prepared_payload/compression_plan.json'
 return {k:{'path':str(p),'sha256':sha(p)}for k,p in [('original_full_math_request',old),('compression_plan',plan),('compression_preparation',plan.parent/'PURE_PREPARATION.json'),('strongcut_proof',R25/'prepared/PURE_PREPARATION.json'),('strongcut_added_rows',R25/'prepared/added_rows.json'),('network_elimination_plan',R34/'prepared/network_elimination_plan.json'),('network_elimination_preparation',R34/'prepared/PURE_PREPARATION.json'),('original_R25_template',R25/'prepared/template.json'),('global_native_template',R34/'prepared/template.json'),('source_postreview',Path(audit))]}
def verified_worker_override(native):
 override=R34/'warning_checked_worker_v2/WORKER_OVERRIDE_READY.json'
 req(sha(override)=='4a3ec99ac92534a53c5298f2fe872f6e219a828b63b1bba2f0c5b5f461683531','frozen narrowly scoped warning-worker override')
 m=read(override);v=override.parent
 for path,key in [(Path(m['new_worker_path']),'new_worker_sha256'),(v/'matrix_warning_guard.py','new_helper_sha256'),(v/'STORAGE_POLICY.json','new_policy_sha256')]:req(sha(path)==m[key],'frozen worker storage guard source')
 i=read(Path(native)/'IDENTITY.json');l=read(Path(native)/'launch.json');r=read(Path(native)/'response.json')
 req(i['worker_sha256']==l['worker_sha256']==m['new_worker_sha256']and i.get('worker_override_sha256')==sha(override)and i.get('worker_override')==m,'actual new occurrence used exact reviewed storage-checked override chain')
 req(r.get('optimize_calls')==1,'exactly one optimizer call required for this new occurrence')
 storage=r.get('matrix_storage_verification',{})
 req(storage.get('all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked')is True and storage.get('discarded_coefficients')==61632 and storage.get('stored_nonzeros')==1095998,'actual stored model verification missing or changed')
 req(storage.get('worst_activity_error_over_original_injection_box_mw')==2.887479279994329e-13,'actual discarded-term bound changed')
 return {'override_path':str(override),'override_sha256':sha(override),'worker_sha256':m['new_worker_sha256'],'matrix_storage_verification':storage,'optimize_calls':1}

def accepted_r34(native,audit):
 """Require this actual full-domain R34 occurrence, never a restricted heuristic."""
 verified_worker_override(native)
 e=load_event(event_paths(native));q=e['request'];a=read(audit);m=read(R34/'prepared/PURE_PREPARATION.json')
 req(sha(R34/'prepared/PURE_PREPARATION.json')=='524cdd007fd6a3071bd5d434cf8351209f33fcd770657898837cc8fda8e31814','frozen full-domain R34 preparation')
 for f,h in m['artifact_hashes'].items():req(sha(R34/'prepared'/f)==h,'frozen R34 '+f)
 strip=lambda z:{k:v for k,v in z.items()if k!='deadline'}
 req(strip(q)==read(R34/'prepared/template.json'),'actual SCUC is not the full-domain R34 template')
 req('research_restriction'not in q,'restricted heuristic bound cannot support accepted SCUC')
 req(sum(z['kind']!='C'and z['lb']!=z['ub']for z in q['variables'])==2160,'global thermal events and BESS hours remain free')
 req(a['native_request_sha256']==e['pins']['request']['sha256'],'full-domain review request')
 req(e['response']['status']=='optimal'and e['response']['native_status']=='Optimal','accepted path requires actual Optimal full SCUC')
 proofs=proof_files(audit);assessment=assess_scuc(e,audit,proof_bundle(proofs));req(assessment['accepted'],'actual full SCUC not within unchanged0.1% gap')
 return e,assessment,proofs
