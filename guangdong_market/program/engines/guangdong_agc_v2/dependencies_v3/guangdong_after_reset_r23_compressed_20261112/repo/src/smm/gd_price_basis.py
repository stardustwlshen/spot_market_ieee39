"""Explicit, non-cash diagnostic of equivalent RT reserve-dual row bases.

No official operator algorithm, scarcity trigger, cap, or settlement is inferred.
Raw nodal dual prices already obtained from the original headroom LP are input.
"""
from numbers import Real
import math


def _num(x,name):
    if isinstance(x,bool) or not isinstance(x,Real) or not math.isfinite(float(x)):
        raise ValueError(f'{name}: finite numeric value required')
    return float(x)


def decompose_conditional_price(*, stage, raw_nodal_yuan_per_mwh,
        emergency_dual_yuan_per_mwh, scarcity_increment_yuan_per_mwh,
        scarcity_source, model_basis, source):
    """Reference row-basis identity, NOT a rule-certified final-price function."""
    if stage not in ('DA','RT'):raise ValueError('explicit DA/RT stage required')
    if model_basis!='nodal_balance_and_original_headroom_emergency_row':
        raise ValueError('supported original row basis must be explicit; avoid adding reserve twice')
    if not isinstance(source,str) or not source.strip() or not isinstance(scarcity_source,str) or not scarcity_source.strip():
        raise ValueError('model and scarcity sources required, including explicit research-zero source')
    raw=_num(raw_nodal_yuan_per_mwh,'raw nodal price')
    increment=_num(scarcity_increment_yuan_per_mwh,'scarcity increment')
    if increment<0:raise ValueError('scarcity increment must not be negative')
    if stage=='DA':
        if emergency_dual_yuan_per_mwh is not None:raise ValueError('DA does not use RT emergency multiplier addition')
        xi=0.
    else:
        xi=_num(emergency_dual_yuan_per_mwh,'emergency multiplier with positive lower-bound convention')
        if xi<0:raise ValueError('emergency multiplier sign is inconsistent with declared row basis')
    component=raw-xi
    reconstructed=component+xi
    residual=reconstructed-raw
    if abs(residual)>max(1e-7,1e-9*abs(raw)):
        raise ValueError('unstable floating-point reserve-dual reconstruction; report original components without asserting identity')
    result=raw+increment
    if not all(math.isfinite(v) for v in (component,reconstructed,result,raw+xi+increment)):
        raise ValueError('price arithmetic overflow')
    return dict(stage=stage,raw_nodal_yuan_per_mwh=raw,
        original_emergency_dual_yuan_per_mwh=xi if stage=='RT' else None,
        rebased_nodal_component_yuan_per_mwh=component,
        reconstructed_before_scarcity_yuan_per_mwh=reconstructed,
        explicit_scarcity_increment_yuan_per_mwh=increment,
        research_formula_candidate_yuan_per_mwh=result,
        literal_unverified_candidate_yuan_per_mwh=raw+xi+increment if stage=='RT' else None,
        source=source,scarcity_source=scarcity_source,
        constraint_basis_interpretation=('equivalent_emergency_row_plus_sum_nodal_balance_rows' if stage=='RT' else 'DA_nodal_no_RT_emergency_rebasing'),
        floating_reconstruction_residual_yuan_per_mwh=residual,
        interpretation_status='research_implementation_not_verified_operator_algorithm',
        publication_mapping_verified=False,final_official_price=None,settlement_cash_created=False,price_cap_applied=False)
