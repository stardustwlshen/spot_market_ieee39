"""Reference-only float boundary handling, never a solver feasibility relaxation.
Scale and bounds must use the SAME physical unit. Signed quantities must provide
signed bounds; prices/angles/unbounded slack are NOT assumed nonnegative.
Raw dispatch, accounting and carry values are never modified by this function.
"""
import math

def reference_value(raw, *, lower, upper, capacity_scale, unit):
    vals=(raw,lower,upper,capacity_scale)
    if any(isinstance(x,bool) or not math.isfinite(x) for x in vals) or lower>upper or capacity_scale<=0 or not unit:
        raise ValueError('finite ordered physical reference bounds and unit required')
    # Thirty-two binary64 spacings at the physical capacity scale, not a percentage.
    tol=32*math.ulp(capacity_scale)
    violation=max(0.,lower-raw,raw-upper)
    if violation>tol:raise ValueError('physical reference outside roundoff-only boundary')
    canonical=min(upper,max(lower,raw))
    return canonical,dict(raw=raw,canonical=canonical,delta=canonical-raw,lower=lower,upper=upper,capacity_scale=capacity_scale,unit=unit,tolerance=tol,policy='32_ULP_at_physical_capacity',reference_only=True,raw_state_unchanged=True)


def check_state(raw, *, lower, upper, capacity_scale, unit):
    """Validate inherited state without canonicalizing it or changing equations."""
    _, audit=reference_value(raw,lower=lower,upper=upper,capacity_scale=capacity_scale,unit=unit)
    audit.update(canonical=raw,delta=0.,reference_only=False,validation_only=True)
    return audit
