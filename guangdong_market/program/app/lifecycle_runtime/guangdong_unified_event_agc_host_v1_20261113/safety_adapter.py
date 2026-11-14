"""Explicit safety boundary: optimization feasibility never grants dispatch clearance."""
def pending_safety():
 return dict(status='BLOCKED_UNVERIFIED_SYSTEM_SAFETY',numerical_acceptance_is_safety_release=False,
             checks={k:'not_verified' for k in ('agc_normal','capacity_balance','network_sections_ac','hydro_vibration','storage_soc','no_increase_curtailment')},
             publication_allowed=False,dispatch_authorized=False,safety_release=False,
             original_requirement_mw=50,positive_reserve_mw=400,accident_reserve_mw=400,
             automatic_reallocation=False,AGC_zero_substitution_allowed=False,
             failure_route='confirmed source-bound failures require failure workflow; no unit attribution from aggregate failure')


def model_verified_safety(names,*,source_kind):
 """Call only after host verifies full raw pair or strict mathematical compatibility."""
 from collections import Counter
 counts=Counter(x.split('[')[0]for x in names['constraints'])
 z=pending_safety()
 for label,groups in {'energy_balance':['c_balance','phase_balance'],'reserve_capacity':['reserves'],'storage_soc':['battery.c_soc']}.items():
  if not all(counts[g]>0 for g in groups):raise ValueError('missing model safety rows: '+label)
  z['checks'][label]={'status':'MODEL_MATHEMATICS_VERIFIED','row_counts':{g:counts[g]for g in groups},'source_kind':source_kind,'operating_safety_proven':False}
 z['checks']['capacity_balance']='model balance/reserve rows verified; operating conditions not verified'
 z['checks']['storage_soc']=dict(z['checks']['storage_soc'])
 z['historical_other_branch_failure_not_applied']=True
 return z
