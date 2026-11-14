"""HiGHS relative MIP gap uses the absolute incumbent objective denominator."""
import math

def gap_settings(requested):
    if isinstance(requested,bool): raise ValueError('numeric relative gap required')
    g=float(requested)
    if not math.isfinite(g) or not 0 <= g <= .01:
        raise ValueError('HiGHS requested gap must be finite in [0, 0.01]')
    return dict(requested_project_mip_rel_gap=g,native_highs_rel_gap=g,native_highs_abs_gap=0.,
        project_gap_denominator='max(1, abs(primal))',native_gap_denominator='abs(primal)',
        translation='Direct relative setting; absolute gap disabled; project gap recomputed from primal/bound')
