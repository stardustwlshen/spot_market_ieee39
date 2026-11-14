from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent;O=B.parent/'guangdong_short_loss_ac_loop_v1_20261112'
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
r=read(B/'diagnostic_once/RESULT.json');v=r['variable_indices'];lo=r['physical_lower'];hi=r['physical_upper'];sl=r['search_lower'];sh=r['search_upper']
for j in range(len(lo)):
 if v['i1']['Vm']<=j<v['iN']['Vm']:assert lo[j]==.94 and hi[j]==1.06 and sl[j]==.8 and sh[j]==1.2
 else:assert lo[j]==sl[j]and hi[j]==sh[j]
old=read(O/'diagnostic_once/MODEL_PLAN.json');new=read(B/'diagnostic_once/MODEL_PLAN.json')
for key in ('active_resources','disabled_resources','thermal_limits'):assert old[key]==new[key]
assert read(O/'CAPABILITY_CONTRACT.json')==read(B/'CAPABILITY_CONTRACT.json')
policy=read(B/'LEGAL_INITIAL_POLICY.json');assert [(d['id'],d['original_VG'],d['legal_VG'])for d in policy['controls']if d['original_VG']!=d['legal_VG']]==[('GT',1.0636,1.06)]
assert not r['review']['strict_feasible_candidate']and not(B/'CONTROL_AC_STARTED.json').exists()
voltage=[]
for j in range(39):
 x=r['x'][v['i1']['Vm']+j]
 if x<.94 or x>1.06:voltage.append({'external_bus':j+1,'Vpu':x,'violation_pu':max(.94-x,x-1.06)})
report={'status':'alternative_numeric_diagnostic_rejected_original_constraints','actual_new_LP':0,'actual_legal_initial_AC':1,'actual_bounded_diagnostics':1,'actual_independent_candidate_AC':0,'nfev':r['review']['nfev'],'initial':read(B/'diagnostic_once/INITIAL_STATE_REVIEW.json'),'final_review':r['review'],'voltage_violations':voltage,'only_search_Vm_box_changed':True,'physical_V_limits_and_nonV_bounds_unchanged':True,'active_disabled_device_capabilities_unchanged':True,'thermal_P_ramp_and_double400_limits_unchanged':True,'capability_contract_equal_v1':True,'failed_iterate_applied':False,'physical_infeasibility_proved':False,'formal_release':False,'security_release':False,'execution_ledger_allowed':False,'source_pins':{str(p.relative_to(B.parent)):sha(p)for p in [B/'diagnose_once.py',B/'LEGAL_INITIAL_POLICY.json',B/'LEGAL_INITIAL_MAPPING.json',B/'LEGAL_INITIAL_AC.json',B/'diagnostic_once/RESULT.json',O/'MANIFEST.json']}}
(B/'FINAL_STATUS.json').write_text(json.dumps(report,indent=2)+'\n')
print(report['status'],report['nfev'])
