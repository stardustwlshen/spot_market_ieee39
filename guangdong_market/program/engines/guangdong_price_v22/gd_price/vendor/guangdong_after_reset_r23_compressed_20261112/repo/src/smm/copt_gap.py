"""Translate the project's incumbent-denominator gap to COPT's native convention."""
import math


def gap_settings(requested):
    g = float(requested)
    if not math.isfinite(g) or g < 0:
        raise ValueError('COPT requested project MIP gap must be finite and nonnegative')
    # d <= r B, B=max(|primal|,|dual|) <= D+d, D=max(1,|primal|).
    # r=g/(1+g) therefore implies d <= g D, including negative/zero objectives.
    # Round conservatively so binary floating-point division cannot loosen r.
    native = g / (1.0 + g)
    native = math.nextafter(native, 0.0) if native > 0 else 0.0
    return dict(requested_project_mip_rel_gap=g, native_copt_rel_gap=native,
                native_copt_abs_gap=0.0,
                project_gap_denominator='max(1, abs(primal))',
                native_gap_denominator='max(abs(primal), abs(dual))',
                translation='g/(1+g), rounded toward zero; AbsGap disabled')
