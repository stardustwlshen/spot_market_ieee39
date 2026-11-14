from pathlib import Path
import json,gzip,hashlib
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
src=json.loads(gzip.decompress((R/'guangdong_rt_short_physical_v1_20261112/actual39_v1/SCED_template.json.gz').read_bytes()));q=json.loads(gzip.decompress((B/'round_1/SCED/request.json.gz').read_bytes()));names=read(R/'guangdong_rt_short_physical_v1_20261112/pure39_v2/names.json');ci={x:i for i,x in enumerate(names['constraints'])};feedback=read(B/'round_1/LOSS_FEEDBACK.json');changed={ci[d['row']]for d in feedback['changed_original_rows']}
assert len(changed)==41 and q['variables']==src['variables']and q['objective']==src['objective']
assert all(r==src['rows'][i]for i,r in enumerate(q['rows'])if i not in changed)
for change in feedback['changed_original_rows']:
 i=ci[change['row']];assert q['rows'][i]['terms']==src['rows'][i]['terms']
 if 'renewable_coefficient'in change:
  s=change['renewable_coefficient'];assert s in (-1,1)
  for side in ('lb','ub'):assert abs(q['rows'][i][side]-src['rows'][i][side]-change['delta_'+side])<1e-9
 else:assert abs(q['rows'][i][change['side']]-src['rows'][i][change['side']]-change['delta'])<1e-9
review=read(B/'round_1/SCED_REVIEW.json');assert review['KKT']['passed'];ac=read(B/'round_1/SUMMARY.json');diag=read(B/'DIAGNOSTIC_SUMMARY.json');assert not diag['review']['strict_feasible_candidate']and not(B/'CONTROL_AC_STARTED.json').exists()
report={'status':'loss_feedback_LP_verified_but_AC_reactive_candidate_not_found','actual_new_LP_calls':1,'actual_new_AC_calls':1,'actual_bounded_QV_diagnostics':1,'independent_candidate_AC_calls':0,'LP_budget_remaining':2,'no_second_QV_diagnostic_authorized_or_run':True,'full_SCED_KKT':review['KKT'],'original_rows_modified':41,'modified_rows':'39 first-point node balances with actual per-row orientation plus first-point positive/negative reserve demand constants','all_other_original_rows_columns_objective_unchanged':True,'SCUC_integers_still_fixed':True,'full48_SOC_ramp_constraints_preserved':True,'original_Q0_AC':ac,'failed_QV':diag['review'],'original_first_AC_failure_preserved':True,'failure_does_not_prove_full_AC_infeasibility':True,'never_applied_failed_iterate':True,'zero_P_inverter_Q_disabled_unchanged':True,'formal_release':False,'security_release':False,'execution_ledger_allowed':False,'PRICE_safety_certified':False,'source_hashes':{str(p.relative_to(R)):sha(p)for p in [B/'run_round.py',B/'export_point.py',B/'diagnose_controls_once.py',B/'round_1/SCED/request.json.gz',B/'round_1/SCED/response.json.gz',B/'round_1/AC_RESULT.json',B/'diagnostic_once/RESULT.json',B/'FAILED_CONTROL_LOCATION.json']}}
(B/'FINAL_STATUS.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':report['status'],'newLP':1,'newAC':1,'QV':1,'remainingLPbudget':2}))
