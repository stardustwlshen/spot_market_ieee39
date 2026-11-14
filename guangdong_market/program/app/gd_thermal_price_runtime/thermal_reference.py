"""Isolated thermal reference API. No storage, VRE, native, or official certification."""
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class ThermalReferencePolicy:
    pricing_reference: str = 'DA_SCED_text_3_4_4_5'
    fixed_reference: str = 'current_final_SCED_explicit_interpretation'
    current_DA_physical_bounds_tolerance: float = 1e-8

def thermal_bounds(*, status, current_sced, da_sced, current_min, current_max,
                   da_min, da_max, delta, policy=ThermalReferencePolicy()):
    """Only pricing-stable reference switches to DA. Other physical rows untouched.

    DA and current physical bounds must agree for this minimal patch, else fail
    with a diagnostic rather than silently relax security or replace capacities.
    Nonpricing uses current SCED by an explicit interpretation, not text certification.
    """
    if policy != ThermalReferencePolicy():
        raise ValueError('unsupported policy; do not silently select another reference')
    vals=(current_sced,da_sced,current_min,current_max,da_min,da_max,delta)
    if not all(isinstance(x,(int,float)) and isfinite(x) for x in vals):
        raise ValueError('finite sourced values required')
    if min(current_min,da_min)<0 or current_min>current_max or da_min>da_max or not 0<=delta<=1:
        raise ValueError('invalid thermal domain or research delta')
    if status=='off':
        if abs(current_sced)>1e-8: raise ValueError('off witness inconsistent')
        return (0.,0.)
    if status=='fixed_nonpricing':
        if not current_min<=current_sced<=current_max: raise ValueError('current fixed SCED beyond physical bounds')
        return (current_sced,current_sced)
    if status!='pricing_stable': raise ValueError('unsupported or unknown eligibility')
    if max(abs(current_min-da_min),abs(current_max-da_max))>policy.current_DA_physical_bounds_tolerance:
        raise ValueError('DA/current physical bounds differ: minimal reference-only patch requires explicit broader constraint decision')
    lo=max(da_min,(1-delta)*da_sced);hi=min(da_max,(1+delta)*da_sced)
    if lo>hi: raise ValueError('empty DA thermal reference band; do not adjust delta to force feasibility')
    return (lo,hi)

def apply_to_serialized_model(model, names, thermal_specs, *, policy=ThermalReferencePolicy()):
    """Return independent matrix plus bound trace; never edits original objects.

    thermal_specs maps exact thermal P[g,q] column names to thermal_bounds kwargs.
    Caller must supply validated thermal inventory; ES/VRE cannot be passed here.
    Only variable lb/ub may change; rows/objective/options are copied unchanged.
    """
    from copy import deepcopy
    lookup={name:i for i,name in enumerate(names['variables'])}
    new=deepcopy(model);trace=[]
    for name,spec in thermal_specs.items():
        if name not in lookup or not name.startswith('P[') or name.startswith('P[ES'):
            raise ValueError('exact thermal power column required')
        j=lookup[name];lo,hi=thermal_bounds(policy=policy,**spec)
        before=(model['variables'][j]['lb'],model['variables'][j]['ub'])
        new['variables'][j]['lb']=lo;new['variables'][j]['ub']=hi
        trace.append(dict(column=name,old_bounds=before,new_bounds=(lo,hi),status=spec['status'],policy=policy.pricing_reference,formal=False))
    return new,trace
