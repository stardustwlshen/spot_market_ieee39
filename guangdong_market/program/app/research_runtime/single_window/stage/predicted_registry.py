"""Predicted-only registration alongside, never inside, actual RuntimeHooks state.
No reconstruction of missing host source registration; no acceptance via JSON.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,json,sys
from stage_api import configure,predict_stage_end

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)

def register_predicted_stage(*,program_root,hooks,prepared,actual,receipts,output):
 root=configure(program_root);mod=sys.modules.get('runtime_hooks')
 need(mod is not None and isinstance(hooks,mod.RuntimeHooks),'existing RuntimeHooks required')
 need(Path(mod.__file__).resolve()==root/'app/lifecycle_runtime/guangdong_event_host_candidate_v1_20261113/runtime_hooks.py','different host source')
 s=hooks.session;s.verify_sources();p=Path(prepared).resolve();a=Path(actual).resolve();key=str(p)
 need(key in hooks.prepared,'near input was not prepared/registered by this live host; independent evidence cannot impersonate it')
 record=hooks.prepared[key]
 need(hooks.carries.get(record['carry_path'])==record['carry_sha256'] and sha(record['carry_path'])==record['carry_sha256'],'near registered origin mismatch')
 for path,h in record['artifact_pins'].items():need(sha(path)==h,'near prepared source drift')
 ledger=hooks.ledgers[record['ledger_id']]
 need(list(ledger['receipts'].values())==list(receipts),'cannot substitute issued notice inventory')
 # Normal session performs original accepted-pair checks, not a forged record.
 bundle,bundle_sha=s.export_generated_pair(p,a,record)
 from v6_host import e
 case=e.unpack(bundle['case']);incoming=json.loads(Path(record['carry_path']).read_bytes());boundary=e.unpack(bundle['boundary']);names=bundle['names'];response=bundle['SCED_response']
 need(len(case['energy_inputs']['horizon']['periods'])==24,'near24 accepted pair only')
 need(boundary['thermal']==incoming['thermal'],'near boundary differs from registered carry')
 evidence=dict(SCED_raw_sha256=json.loads((a/'SCED/COMPLETED.json').read_bytes())['response']['raw_sha256'],names_sha256=sha(p/'names.json'),SCED_stored_sha256=sha(a/'SCED/response.json.gz'),raw_hash_kind='uncompressed native SHA256')
 projected=predict_stage_end(program_root=root,case=case,incoming=incoming,names=names,primal=response['primal'],receipts=receipts,accepted_source=evidence,cell_count=24)
 target=Path(output).resolve();need(not target.exists(),'fresh predicted record only')
 result=dict(schema='gd.registered.predicted_stage.v1',host_run_id=s.registry['run_id'],origin_prepared=key,origin_carry_path=record['carry_path'],origin_carry_sha256=record['carry_sha256'],accepted_bundle_sha256=bundle_sha,projected=projected,pins={**record['artifact_pins'],record['carry_path']:record['carry_sha256'],str(a/'SCED/response.json.gz'):sha(a/'SCED/response.json.gz')},execution_allowed=False,publication_allowed=False,actual_carry_registered=False)
 target.write_text(json.dumps(result,indent=2));registry=getattr(hooks,'predicted_stages',None)
 if registry is None:registry={};hooks.predicted_stages=registry
 registry[str(target)]=dict(record=result,sha256=sha(target),session_identity=id(s),ledger_id=record['ledger_id'])
 return dict(path=str(target),sha256=sha(target),kind='prediction_only',execution_allowed=False)

def verify_predicted_stage(hooks,path):
 s=hooks.session;s.verify_sources();p=Path(path).resolve();registry=getattr(hooks,'predicted_stages',{});need(str(p)in registry,'prediction not registered by this host')
 r=registry[str(p)];need(r['session_identity']==id(s)and sha(p)==r['sha256'],'prediction owner/bytes changed')
 for f,h in r['record']['pins'].items():need(sha(f)==h,'prediction source drift')
 return deepcopy(r['record'])

def require_actual_consumer(record):
 need(record.get('schema')!='gd.registered.predicted_stage.v1' and record.get('execution_allowed')is True,'predicted-only record forbidden for actual execution/publication')
