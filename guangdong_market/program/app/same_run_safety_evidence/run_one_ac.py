from pathlib import Path
import json,sys,hashlib,time
O=Path(__file__).resolve().parent; W=O.parent;B=W/'guangdong_delivery_integration_20261114/app_copy/app/lifecycle_runtime/guangdong_safety_session_v1_20261113';sys.path.insert(0,str(B));from ac_security import check_ac
from ac_adapter import digest
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
m=json.loads((O/'MAPPING.json').read_bytes());e=json.loads((O/'SOURCE.json').read_bytes())
for p,h in e['source_pins'].items():assert sha(Path(p))==h
with (O/'AC_STARTED.json').open('x') as f:json.dump(dict(run_id=e['run_id'],executed_tick=285,input_sha256=digest(m['ac_input']),max_AC_calls=1,optimizer_calls=0,source_pins={str(p):sha(p) for p in [B/'ac_security.py',B/'ac_adapter.py',Path(__file__),O/'MAPPING.json',O/'SOURCE.json']}),f)
t=time.monotonic();z=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10);elapsed=time.monotonic()-t
assert z['input_sha256']==digest(m['ac_input'])
with (O/'AC_RESULT.json').open('x') as f:json.dump(z,f,indent=2)
r=dict(run_id=e['run_id'],executed_tick=285,coverage={'checked':1,'executed':288},elapsed_s=elapsed,new_AC_calls=1,new_native_calls=0,status=z['status'],ac_snapshot_safe=z.get('ac_snapshot_safe'),violations=z.get('violations'),safety_release=False,dispatch_authorized=False,posthoc_not_live_safety_receipt=True)
with (O/'ACTUAL_DIAGNOSTIC.json').open('x') as f:json.dump(r,f,indent=2)
print(json.dumps(r))
