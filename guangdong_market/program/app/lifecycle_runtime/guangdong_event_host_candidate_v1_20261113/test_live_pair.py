from v6_runtime import *
from v6_host import write
from native_runtime import execute_pair
import json
B=Path(__file__).resolve().parent;R=B.parent
out=B/'new_raw_W2_v1';out.mkdir(exist_ok=False)
hooks=open_hooks(R/'guangdong_unified_batch_candidate_v1_20261113/actual_first_hour_v1/rt',research_fixed_curve_events=True)
cp,lid=hooks.adopt_registered_boundary(2)
built=hooks.build(cp,out/'prepared',ledger_id=lid)
q=read(out/'prepared/template.json');old=read(B/'arbitrary_runtime_prepared/template.json')
assert q==old,'must use exact existing candidate mathematical template'
write(out/'PRE_NATIVE.json',dict(rows=len(q['rows']),variables=len(q['variables']),existing_candidate_template_equal=True,build=built,original_400_AGC_storage_protections_unchanged=True))
print('PRE_NATIVE verified identical existing W2 candidate; starting exactly two stages',flush=True)
z=execute_pair(hooks,out/'prepared',execute_native=True);print('PAIR_ACCEPTED '+json.dumps(z),flush=True)
nxt=hooks.handoff_accepted(out/'prepared',out/'actual',out/'CARRY_W3.json',simulate_no_deviation=True)
print('EXACT_HANDOFF '+json.dumps(nxt),flush=True)
b=hooks.build(nxt['carry_path'],out/'W3_prepared',ledger_id=nxt['ledger_id'])
write(out/'RESULT.json',dict(status='new_raw_W2_strict_acceptance_exact_handoff_W3_full_build_passed',accepted=z,handoff=nxt,next_build=b,native_calls=2,W3_native_calls=0,old_raw_adopted=False,formal=False,safety_release=False))
print('COMPLETE new raw pair -> exact carry -> full W3 model; no W3 solve',flush=True)
