"""Bounded same-origin prepared re-registration after process exit, never native."""
from pathlib import Path
import json,hashlib
from copy import deepcopy
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def restore_same_origin(hooks,rebuilt,original,actual):
 s=hooks.session;s.verify_sources();rebuilt=Path(rebuilt).resolve();original=Path(original).resolve();actual=Path(actual).resolve()
 r=hooks.prepared[str(rebuilt)];old=read(original/'SUMMARY.json');new=read(rebuilt/'SUMMARY.json')
 for f,h in old['source_pins'].items():
  if sha(f)!=h:raise ValueError('old source drift '+f)
 for name in ('template.json','names.json','derived_case.json','CARRY.json','BOUNDARY.json','agc.json','NOTICE_AVAILABILITY.json'):
  if (original/name).read_bytes()!=(rebuilt/name).read_bytes():raise ValueError('same-origin exact reconstruction mismatch '+name)
 if old['carry_sha256']!=r['carry_sha256']:raise ValueError('registered origin mismatch')
 record=deepcopy(r);record.update(summary_sha256=sha(original/'SUMMARY.json'),artifact_pins={str(p):sha(p)for p in original.glob('*.json')})
 # Original session consumer fully checks raw, matrices, stored/raw digests and KKT.
 bundle,digest=s.export_generated_pair(original,actual,record)
 accepted=read(actual/'ACCEPTED_PAIR.json')
 if accepted['run_id']!=s.registry['run_id']or accepted['prepared_sha256']!=record['summary_sha256']or accepted['ledger_id']!=r['ledger_id']:raise ValueError('accepted source identity mismatch')
 hooks.prepared[str(original)]=record
 return dict(kind='exact_rebuild_same_origin_only',original=str(original),rebuilt=str(rebuilt),accepted_bundle_sha256=digest,native_calls=0)
