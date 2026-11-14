from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
d=read(B/'LOCAL_P_DOMAIN.json');r=read(B/'DIAGNOSTIC_SUMMARY.json');loc=read(B/'FAILED_CONTROL_LOCATION.json');assert not r['review']['strict_feasible_candidate']and not(B/'AWAIT_FULL_SCED.json').exists()
assert set(d['battery'])=={'ES4','ES5'}and all(z['P_min_MW']==0 and z['P_max_MW']==150 for z in d['battery'].values())
for z in d['battery'].values():assert all(t['all_battery_rows_max_violation']<1e-6 for t in z['endpoint_midpoint_block_checks'])
assert read(B/'CAPABILITY_CONTRACT.json')==read(R/'guangdong_short_qv_zeroequiv_v2_20261112/CAPABILITY_CONTRACT.json')
orig=read(R/'guangdong_short_qv_zeroequiv_v2_20261112/diagnostic_once/MODEL_PLAN.json');now=read(B/'diagnostic_once/MODEL_PLAN.json');assert orig['active_resources']==now['active_resources']and orig['disabled_resources']==now['disabled_resources']
zero=[z for z in loc['active_inverters']if abs(z['failed_iterate_P_MW'])<=1e-8 and abs(z['failed_iterate_Q_Mvar'])>1e-6]
assert {z['id']for z in zero}=={'ES4','ES5'}and not r['review']['zero_P_Q_policy_passed']
report={'status':'one_local_PQV_candidate_search_rejected','actual_new_LP':0,'actual_new_AC':0,'actual_nonlinear_diagnostics':1,'nfev':r['review']['nfev'],'final_review':r['review'],'full48_storage_domain_pure_validated':True,'capability_and_initial_active_set_unchanged':True,'source_same_legal_PF_from_v2':True,'zero_P_Q_rejection':zero,'failed_iterate_never_applied':True,'global_infeasibility_proved':False,'conditional_LP_and_final_AC_not_triggered':True,'formal_release':False,'security_release':False,'execution_ledger_allowed':False,'source_pins':{str(p.relative_to(R)):sha(p)for p in [B/'derive_domain.py',B/'LOCAL_P_DOMAIN.json',B/'diagnose_pqv_once.py',B/'diagnostic_once/RESULT.json',B/'FAILED_CONTROL_LOCATION.json',R/'guangdong_short_qv_zeroequiv_v2_20261112/LEGAL_INITIAL_AC.json']}}
(B/'FINAL_STATUS.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'])
