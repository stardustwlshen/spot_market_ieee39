"""Read-only registration consistency check, never host authority or raw acceptance.
The caller must hold a live registered RuntimeHooks entry and subsequently call
register_predicted_stage, which performs original host/raw/KKT verification.
"""
from pathlib import Path
import hashlib,json
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def check_registered_near(*,record,prepared,actual,carry,ledger_id,run_id,stage,expected_loss_profile=None):
 p=Path(prepared).resolve();a=Path(actual).resolve();cp=Path(carry).resolve();summary=read(p/'SUMMARY.json')
 need(record['carry_path']==str(cp)and record['carry_sha256']==sha(cp),'registered origin changed')
 need(record['ledger_id']==ledger_id,'registered ledger changed')
 need(record['summary_sha256']==sha(p/'SUMMARY.json'),'registered summary changed')
 need(summary['stage']==stage,'registered stage changed')
 for f,h in record['artifact_pins'].items():need(sha(f)==h,'registered artifact drift: '+f)
 for f,h in summary['source_pins'].items():need(sha(f)==h,'registered source drift: '+f)
 acc=read(a/'ACCEPTED_PAIR.json')
 need(acc['run_id']==run_id and acc['prepared_sha256']==record['summary_sha256']and acc['ledger_id']==ledger_id,'accepted pair identity changed')
 profile=p/'LOSS_PROFILE.json';receipt=p/'LOSS_OVERLAY.json'
 if expected_loss_profile is None:need(not profile.exists()and not receipt.exists(),'unexpected registered loss overlay')
 else:
  need(str(profile)in record['artifact_pins']and str(receipt)in record['artifact_pins'],'loss overlay not registered')
  need(read(profile)==expected_loss_profile,'requested loss profile differs from registered near')
 return dict(kind='same_live_registration_no_rebuild',original=str(p),rebuilt=None,raw_acceptance='still required by immediately following register_predicted_stage export',execution=False,publication=False,native_calls=0)
