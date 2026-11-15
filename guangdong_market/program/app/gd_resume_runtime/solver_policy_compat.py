"""One explicit old .001 to new .01/3600 policy transition; no generic version bypass."""
from pathlib import Path
import json
from content_hash import sha
APP=Path(__file__).resolve().parents[2]
ALLOWED=frozenset(['APPLICATION_MANIFEST.json', 'app/LIVE_MARKET_MANIFEST.json', 'app/TRUSTED_INSTALL.json', 'app/gd_da_only.py', 'app/gd_da_only_panel.py', 'app/gd_unified_supervisor.py', 'app/gd_live_market_runtime/domain_event.py', 'app/gd_live_market_runtime/session.py', 'app/lifecycle_runtime/PORTABLE_MANIFEST.json', 'app/lifecycle_runtime/portable_gate.py', 'app/gd_post_scuc_runtime/phase_runtime.py', 'app/gd_thermal_price_runtime/PRODUCER.json', 'app/gd_hourly_agc_runtime/host.py', 'app/gd_reference_runtime/builder.py', 'app/gd_reference_runtime/precision_worker.py', 'app/gd_reference_runtime/build_process.py', 'app/gd_reference_runtime/reference_session.py', 'app/gd_reference_runtime/post_reference.py', 'app/lifecycle_runtime/gd_sced_precision_runtime/precision_worker.py', 'app/lifecycle_runtime/gd_sced_precision_runtime/policy.py', 'app/lifecycle_runtime/guangdong_event_host_candidate_v1_20261113/native_runtime.py', 'app/lifecycle_runtime/guangdong_event_host_candidate_v1_20261113/v6_host.py', 'app/lifecycle_runtime/guangdong_fixed_event_entry_v1_20261113/event_path.py', 'engines/guangdong_short_unified_v6/FROZEN_MANIFEST.json', 'engines/guangdong_v3/SOURCE_MAP.json', 'engines/guangdong_short_unified_v6/rt/INSTALL_MANIFEST.json', 'engines/guangdong_short_unified_v6/price/TRUSTED_RT.json', 'engines/guangdong_short_unified_v6/price/domain_v23.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112/builder_user_v2.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112/lp_ipm_worker.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112/rolling_midnight.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112/program.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/DEPENDENCIES.json', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_physical_v1_20261112/run_actual39.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v32_20261112/scuc_worker.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v32_20261112/lp_worker.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v32_20261112/program.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/runtime/acceptance.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_solver.py', 'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_gap.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/REVIEWERS.json', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/price_worker.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/evidence.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/native.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/domain.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_solver.py', 'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_gap.py', 'engines/guangdong_v3/guangdong_pipeline_20261112/da_pipeline_v4.py', 'engines/guangdong_v3/guangdong_pipeline_20261112/pipeline_da_checked.py', 'engines/guangdong_v3/guangdong_market_delivery_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_solver.py', 'engines/guangdong_v3/guangdong_market_delivery_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src/smm/highs_gap.py', 'engines/guangdong_v3/guangdong_pipeline_20261112/da_math/lp_worker.py', 'engines/guangdong_v3/guangdong_pipeline_20261112/da_math/mip_worker.py', 'engines/guangdong_v3/guangdong_pipeline_20261112/precision_recovery_v3/lp_worker_1e10.py'])
ALLOWED=ALLOWED|{'app/lifecycle_runtime/guangdong_unified_event_agc_host_v1_20261113/agc_lifecycle.py','app/gd_tail_runtime/tail_core.py'}
ALLOWED=ALLOWED|{'app/gd_resume_runtime/partial_pair.py', 'app/gd_tail_runtime/worker.py', 'app/gd_tail_runtime/builder.py', 'app/loss_callback.py'}
ALLOWED=ALLOWED|{'app/lifecycle_runtime/guangdong_clocked_general_host_v1_20261113/clocked_host.py', 'app/lifecycle_runtime/guangdong_unified_event_agc_host_v1_20261113/unified.py'}
# Explicit non-mathematical recovery wiring and documented parameter repairs.
REPAIR_ALLOWED=frozenset(['APPLICATION_MANIFEST.json','app/LIVE_MARKET_MANIFEST.json','app/gd_tail_runtime/worker.py','app/gd_tail_control.py','app/gd_reference_runtime/post_reference.py','app/gd_live_market_runtime/tightening.py','app/gd_post_scuc_runtime/consumers.py','app/gd_resume_control.py','app/gd_resume_runtime/hourly_durable.py','app/gd_resume_runtime/historical_hour.py','app/gd_resume_runtime/solver_policy_compat.py'])
def need(x,m):
 if not x:raise ValueError(m)
def load(path):
 path=Path(path).resolve();z=json.loads(path.read_bytes())
 need(z['schema']=='gd.solver_policy_transition.v1','unsupported policy transition')
 need(z['old_policy']=={'mip_rel_gap':0.001,'time_limits':'historical_unchanged'}and z['new_policy']=={'mip_rel_gap':0.01,'native_time_limit_s':3600,'threads':1,'process_grace_s':300},'unsupported solver policy values')
 need(Path(z['new_application']).resolve()==APP,'policy for another application')
 need(set(z['changes'])<=ALLOWED,'unapproved file in solver-policy transition')
 pins={str(path):sha(path)}
 repair=z.get('recovery_wiring_and_parameter_repairs')
 if repair:
  need(repair['schema']=='gd.resume_source_repairs.v1','unsupported source repair descriptor')
  need(set(repair['changes'])<=REPAIR_ALLOWED,'unapproved recovery repair file')
  for rel,v in repair['changes'].items():
   old=Path(repair['old_application'])/rel;new=APP/rel
   need(sha(old)==v['old_sha256'] and sha(new)==v['new_sha256'],'exact repair source changed: '+rel)
   pins[str(old)]=v['old_sha256'];pins[str(new)]=v['new_sha256']
 for rel,v in z['changes'].items():
  old=Path(z['old_application'])/rel;new=APP/rel
  need(sha(old)==v['old_sha256']and sha(new)==v['new_sha256'],'solver-policy exact source changed: '+rel)
  pins[str(old)]=v['old_sha256'];pins[str(new)]=v['new_sha256']
 return dict(path=str(path),manifest_sha256=sha(path),changes=z['changes'],source_pins=pins,old_policy=z['old_policy'],new_policy=z['new_policy'],historical_results_relabelled=False,recovery_wiring_and_parameter_repairs=repair)
def permits(policy,relative,old,new):
 if not policy:return False
 candidates=[policy['changes'].get(relative),(policy.get('recovery_wiring_and_parameter_repairs')or{}).get('changes',{}).get(relative)]
 return any(v is not None and v['old_sha256']==old and v['new_sha256']==new for v in candidates)

# Controlled numerical-policy transition only; all mathematical/KKT gates remain intact.
_original_load=load
def load(path):
 path=Path(path).resolve();z=json.loads(path.read_bytes())
 if z.get('schema')!='gd.sced_primal_precision_transition.v1':return _original_load(path)
 need(Path(z['new_application']).resolve()==APP,'precision transition for another installation')
 need(z['old_policy']=={'primal_feasibility_tolerance':1e-10} and z['new_policy']=={'primal_feasibility_tolerance':1e-8},'unsupported precision change')
 oldroot=Path(z['old_application']);worker='app/lifecycle_runtime/gd_sced_precision_runtime/precision_worker.py';policy='app/lifecycle_runtime/gd_sced_precision_runtime/policy.py'
 allowed={worker,policy,'APPLICATION_MANIFEST.json','app/LIVE_MARKET_MANIFEST.json','app/lifecycle_runtime/PORTABLE_MANIFEST.json','app/lifecycle_runtime/portable_gate.py','app/gd_resume_runtime/solver_policy_compat.py'}
 need(set(z['changes'])<=allowed and {worker,policy}<=set(z['changes']),'precision change inventory')
 old=(oldroot/policy).read_text();new=(APP/policy).read_text();need(old.replace('primal_feasibility_tolerance=1e-10','primal_feasibility_tolerance=1e-8')==new,'SCED policy changed beyond primal tolerance')
 line="if options['primal_feasibility_tolerance']!=1e-10 or h.getOptionValue('primal_feasibility_tolerance')[1]!=1e-10:"
 need((oldroot/worker).read_text().replace(line,line.replace('1e-10','1e-8'))==(APP/worker).read_text(),'SCED worker changed beyond exact tolerance readback')
 pins={str(path):sha(path)}
 for rel,v in z['changes'].items():
  a,b=oldroot/rel,APP/rel;need(sha(a)==v['old_sha256'] and sha(b)==v['new_sha256'],'precision exact source mismatch: '+rel);pins[str(a)]=v['old_sha256'];pins[str(b)]=v['new_sha256']
 # Whole-install inventory: do not use the transition as a bypass for model changes.
 aa=json.loads((oldroot/'APPLICATION_MANIFEST.json').read_bytes())['files'];bb=json.loads((APP/'APPLICATION_MANIFEST.json').read_bytes())['files']
 need(set(aa)==set(bb),'precision installation file inventory differs')
 for rel in aa:
  if aa[rel]['sha256']!=bb[rel]['sha256']:need(rel in allowed,'unapproved mathematical/input source change: '+rel)
 evidence=Path(z['validation_evidence']);need(sha(evidence)==z['validation_evidence_sha256'],'precision validation evidence changed');pins[str(evidence)]=sha(evidence)
 proof=json.loads(evidence.read_bytes());need(proof.get('passed')is True and proof['max_primal_violation']<=1e-6 and proof['max_dual_sign_violation']<=1e-8,'precision change lacks full strict KKT proof')
 for name,h in proof['source_pins'].items():need(sha(name)==h,'precision evidence source changed');pins[name]=h
 return dict(path=str(path),manifest_sha256=sha(path),changes=z['changes'],source_pins=pins,old_policy=z['old_policy'],new_policy=z['new_policy'],historical_results_relabelled=False,recovery_wiring_and_parameter_repairs=None,numerical_policy_only=True)

_precision_load=load
def load(path):
 path=Path(path).resolve();z=json.loads(path.read_bytes())
 if z.get('schema')!='gd.initial_power_ulp_transition.v1':return _precision_load(path)
 need(Path(z['new_application']).resolve()==APP,'ULP transition for another installation');oldroot=Path(z['old_application'])
 rel='app/gd_provincial15_model/battery_short.py'
 allowed={rel,'APPLICATION_MANIFEST.json','app/LIVE_MARKET_MANIFEST.json','app/lifecycle_runtime/PORTABLE_MANIFEST.json','app/lifecycle_runtime/portable_gate.py','app/gd_resume_runtime/solver_policy_compat.py'}
 need(set(z['changes'])<=allowed and rel in z['changes'],'ULP transition scope')
 before="        require(-r['charge_max_mw']<=p<=r['discharge_max_mw'], 'initial battery power capacity')"
 after="""        lower,upper=-r['charge_max_mw'],r['discharge_max_mw']
        ulp_tolerance=16*math.ulp(max(1.,abs(lower),abs(upper),abs(p)))
        violation=max(0.,lower-p,p-upper)
        require(violation<=ulp_tolerance, 'initial battery power capacity')
        r['_initial_power_capacity_check']=dict(raw_power_mw=p,lower_mw=lower,upper_mw=upper,violation_mw=violation,ulp_tolerance_mw=ulp_tolerance,raw_state_unchanged=True)"""
 need((oldroot/rel).read_text().replace(before,after)==(APP/rel).read_text(),'ULP patch changes more than initial input comparison')
 pins={str(path):sha(path)}
 for name,v in z['changes'].items():
  a,b=oldroot/name,APP/name;need(sha(a)==v['old_sha256']and sha(b)==v['new_sha256'],'ULP transition source changed');pins[str(a)]=v['old_sha256'];pins[str(b)]=v['new_sha256']
 aa=json.loads((oldroot/'APPLICATION_MANIFEST.json').read_bytes())['files'];bb=json.loads((APP/'APPLICATION_MANIFEST.json').read_bytes())['files'];need(set(aa)==set(bb),'ULP file inventory differs')
 for name in aa:
  if aa[name]['sha256']!=bb[name]['sha256']:need(name in allowed,'ULP transition contains unrelated change')
 for name,h in z['validation_pins'].items():need(sha(name)==h,'ULP validation evidence changed');pins[name]=h
 return dict(path=str(path),manifest_sha256=sha(path),changes=z['changes'],source_pins=pins,old_policy={},new_policy={},historical_results_relabelled=False,recovery_wiring_and_parameter_repairs=None,raw_carry_unchanged=True,physical_constraints_unchanged=True)

_boundary_load=load
def load(path):
 path=Path(path).resolve();z=json.loads(path.read_bytes())
 if z.get('schema')!='gd.price_reference_roundoff_transition.v1':return _boundary_load(path)
 need(Path(z['new_application']).resolve()==APP,'reference transition for another installation');oldroot=Path(z['old_application'])
 changed='app/gd_provincial15_model/pumped_price.py';added='app/gd_provincial15_model/numeric_boundary.py'
 allowed={changed,added,'APPLICATION_MANIFEST.json','app/LIVE_MARKET_MANIFEST.json','app/lifecycle_runtime/PORTABLE_MANIFEST.json','app/lifecycle_runtime/portable_gate.py','app/gd_resume_runtime/solver_policy_compat.py'}
 need(set(z['changes'])<=allowed and {changed,added}<=set(z['changes']),'reference roundoff scope')
 pins={str(path):sha(path)}
 for name,v in z['changes'].items():
  a,b=oldroot/name,APP/name
  if v['old_sha256']is None:need(name==added and not a.exists(),'unexpected new source')
  else:need(sha(a)==v['old_sha256'],'old reference source changed');pins[str(a)]=v['old_sha256']
  need(sha(b)==v['new_sha256'],'new reference source changed');pins[str(b)]=v['new_sha256']
 aa=json.loads((oldroot/'APPLICATION_MANIFEST.json').read_bytes())['files'];bb=json.loads((APP/'APPLICATION_MANIFEST.json').read_bytes())['files'];need(set(bb)-set(aa)=={added} and not(set(aa)-set(bb)),'reference source inventory differs')
 for name in aa:
  if aa[name]['sha256']!=bb[name]['sha256']:need(name in allowed,'reference transition has unrelated model change')
 for name,h in z['validation_pins'].items():need(sha(name)==h,'reference validation evidence changed');pins[name]=h
 # Independently tested replacement is exact; never accept a broad numerical bypass.
 for rel in (changed,added):
  proof=Path(z['patch_directory'])/Path(rel).name;need(proof.read_bytes()==(APP/rel).read_bytes(),'reference patch differs from verified sample')
 return dict(path=str(path),manifest_sha256=sha(path),changes=z['changes'],source_pins=pins,old_policy={},new_policy={},historical_results_relabelled=False,recovery_wiring_and_parameter_repairs=None,raw_carry_unchanged=True,physical_constraints_unchanged=True,price_reference_roundoff_only=True)


_reference_load=load
def load(path):
 path=Path(path).resolve();z=json.loads(path.read_bytes())
 if z.get('schema')!='gd.state_roundoff_transition.v1':return _reference_load(path)
 need(Path(z['new_application']).resolve()==APP,'state transition for another installation');oldroot=Path(z['old_application'])
 models={'app/gd_provincial15_model/'+n for n in ('numeric_boundary.py','pumped_storage.py','battery_short.py')}
 allowed=models|{'APPLICATION_MANIFEST.json','app/LIVE_MARKET_MANIFEST.json','app/lifecycle_runtime/PORTABLE_MANIFEST.json','app/lifecycle_runtime/portable_gate.py','app/gd_resume_runtime/solver_policy_compat.py'}
 need(set(z['changes'])<=allowed and models<=set(z['changes']),'state roundoff scope')
 pins={str(path):sha(path)}
 for name,v in z['changes'].items():
  a,b=oldroot/name,APP/name;need(sha(a)==v['old_sha256']and sha(b)==v['new_sha256'],'state patch source changed');pins[str(a)]=v['old_sha256'];pins[str(b)]=v['new_sha256']
 aa=json.loads((oldroot/'APPLICATION_MANIFEST.json').read_bytes())['files'];bb=json.loads((APP/'APPLICATION_MANIFEST.json').read_bytes())['files'];need(set(aa)==set(bb),'state source inventory differs')
 for name in aa:
  if aa[name]['sha256']!=bb[name]['sha256']:need(name in allowed,'state transition has unrelated model change')
 for name,h in z['validation_pins'].items():need(sha(name)==h,'state validation changed');pins[name]=h
 for rel in models:
  proof=Path(z['patch_directory'])/Path(rel).name;need(proof.read_bytes()==(APP/rel).read_bytes(),'state patch differs from validated sample')
 need(json.loads((Path(z['patch_directory'])/'VALIDATION.json').read_bytes())['result']=='PASS','state validation missing')
 return dict(path=str(path),manifest_sha256=sha(path),changes=z['changes'],source_pins=pins,old_policy={},new_policy={},historical_results_relabelled=False,recovery_wiring_and_parameter_repairs=None,raw_carry_unchanged=True,physical_constraints_unchanged=True)
