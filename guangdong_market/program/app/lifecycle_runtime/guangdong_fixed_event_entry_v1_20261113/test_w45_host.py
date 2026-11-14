from pathlib import Path
import json,hashlib,math
from event_path import *
from host_export import export
B=Path(__file__).resolve().parent;R=B.parent;producer=R/'guangdong_rt_short_user_cli_v10_20261113';root=producer/'import_W34_recovered_v1';bundles={}
for w in (44,45):
 p=root/f'W{w}/prepared';c=read(p/'CARRY.json');s=read(p/'SUMMARY.json');b,h=export(p,p.parent/'actual',producer_root=producer,expected_summary_sha256=hashlib.sha256((p/'SUMMARY.json').read_bytes()).hexdigest(),case_identity=c['seed_case_sha256'],run_id='isolated-fixed-W45-v1',available_at=s['domain_start']);bundles[w]=(b,h)
 # Host supplies trust digest separately. No digest taken from the bundle itself.
 (B/f'W{w}_BUNDLE.json').write_text(json.dumps(b,separators=(',',':')))
 (B/f'W{w}_HOST_DIGEST.txt').write_text(h+'\n')
ledger=publish(bundles[44][0],None,expected_bundle_sha256=bundles[44][1],cutoff_tick=135,simulate=True)
b,h=bundles[45];built=build_candidate(b,ledger,expected_bundle_sha256=h,expected_ledger_sha256=ledger['ledger_sha256']);carried=carry_candidate(b,ledger,expected_bundle_sha256=h,expected_ledger_sha256=ledger['ledger_sha256'])
old=read(root/'handoff/W46/CARRY.json')
for key in ('thermal_age_h','storage','cutoff','boundary_at','executed_end_exclusive','issued_end_exclusive'):assert carried[key]==old[key],key
assert carried['boundary_states']==old['thermal']
q=b['SCUC_request'];new=built['template'];assert new['variables']==q['variables']and new['objective']==q['objective']and new['rows'][:len(q['rows'])]==q['rows']
x=b['SCED_response']['primal'];added=new['rows'][len(q['rows']):];error=max(abs(math.fsum(a*x[j]for j,a in row['terms'])-row['lb'])for row in added);assert error<1e-7
(B/'W45_LEDGER.json').write_text(json.dumps(ledger));(B/'W45_CARRY.json').write_text(json.dumps(carried));r=dict(status='host_checked_IEEE39_W44_to_W45_ledger_build_carry_passed',new_native_calls=0,added_notice_rows=len(added),added_residual=error,original400_AGC_policy_matrix_unchanged=True,storage_and_thermal_old_carry_equal=True,plan_available_at=bundles[44][0]['available_at'],consumer_cutoff=unpack(b['case'])['contract']['cutoff']);(B/'W45_RESULT.json').write_text(json.dumps(r,indent=2));print(r)
