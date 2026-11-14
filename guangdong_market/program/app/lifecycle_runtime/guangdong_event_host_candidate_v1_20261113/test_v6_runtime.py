from v6_runtime import *
import json
B=Path(__file__).resolve().parent;R=B.parent
run=R/'guangdong_unified_batch_candidate_v1_20261113/actual_first_hour_v1/rt';hooks=open_hooks(run,research_fixed_curve_events=True);cp,lid=hooks.adopt_registered_boundary(2)
z=hooks.build(cp,B/'arbitrary_runtime_prepared',ledger_id=lid)
a=read(B/'arbitrary_runtime_prepared/template.json');b=read(B/'edited_run_W2/prepared/template.json');assert sorted(json.dumps(x,sort_keys=True)for x in a['rows'])==sorted(json.dumps(x,sort_keys=True)for x in b['rows'])and a['objective']==b['objective']and a['variables']==b['variables']
from v6_host import write
write(B/'V6_RUNTIME_RESULT.json',dict(status='normal_optional_runtime_API_accepts_new_run_and_full_builder',run_id=hooks.session.registry['run_id'],source_manifest=MANIFEST,rows=len(a['rows']),native_calls=0,callback_handoff_requires_actual_new_raw_pair=True));print('normal arbitrary-run callbacks build matches authenticated edited-run fixture,0native')
