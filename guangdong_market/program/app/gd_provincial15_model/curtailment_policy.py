"""Retain GD Mc*SLc term; numeric default0 is user-selected research, not official."""
import math
KEY='research_curtailment_penalty_yuan_per_mwh'
def value(case):
    x=case.get(KEY,0.)
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<0:raise ValueError('finite nonnegative explicit Mc required')
    return float(x)
def apply(case):
    x=value(case);e=case['energy_inputs'];e['renewable_curtailment_penalty_yuan_per_mwh']={k:x for k in e['renewable_mw']}
    case['curtailment_penalty_policy']=dict(value=x,unit='yuan/MWh',basis='user_selected_research_default_zero; GD formula retained, official numeric parameter unverified',editable_key=KEY)
    return case
