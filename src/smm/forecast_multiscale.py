"""Version 2 renewable *scenario generator*, not a trained/online forecaster.

All stochastic sequences are forward recurrences with stationary initialization.
No future actual samples, centered filters, splines, or full-day empirical
normalization are used to smooth a forecast. The joint offline construction
F = A - sigma * E still conditions forecasts on a simulated truth: it must not
be presented as a deployable predictor or empirical accuracy validation.

The common error path is scaled at DA/ID/RT. This deliberately idealized nested
error assumption preserves the accuracy order even after physical clipping;
it does not model independent forecast revisions. Sigma is the population
standard deviation BEFORE clipping, not an enforced single-day nRMSE.
"""
from __future__ import annotations

import hashlib

import numpy as np

from .forecast import PHI, RHO_W1W2, SIGMA, WIND_A, WIND_B, ar1, interp96, pv_shape

MODEL = "multiscale_v2"
# Versioned research assumptions, fractions of nameplate capacity. They are not
# measured-site calibration. Keep truth independent of forecast sigma/phi.
ACTUAL = {"wind": {"slow_sigma": .045, "fast_sigma": .018,
                    "slow_phi": .90, "fast_phi": .65},
          "pv": {"slow_sigma": .040, "fast_sigma": .016,
                  "slow_phi": .85, "fast_phi": .55}}
SLOW_VARIANCE_WEIGHT = .85


def stream(seed: int, label: str) -> np.random.Generator:
    """Independent reproducible substreams, stable under resource reordering."""
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    words = np.frombuffer(digest[:16], dtype="<u4").astype(int).tolist()
    return np.random.default_rng(np.random.SeedSequence([int(seed), *words]))


def stationary_ar2(T, phi, rng, innovations=None):
    """Unit-variance two-pole process, x[t] = 2p*x[t-1]-p²*x[t-2]+s*z[t].

    Analytic stationary variance fixes s, rather than scaling by a day's realized
    RMS (which would use future observations and artificially force accuracy).
    lag-1 correlation is 2p/(1+p²). Initialization is jointly stationary.
    ``innovations`` is exposed for causality/variance tests only.
    """
    if T < 1 or not np.isfinite(phi) or not 0 <= phi < 1:
        raise ValueError("多尺度过程须T>=1且0<=phi<1")
    rho = 2 * phi / (1 + phi * phi)
    previous = float(rng.standard_normal())
    older = rho * previous + np.sqrt(max(0., 1 - rho * rho)) * float(rng.standard_normal())
    z = rng.standard_normal(T) if innovations is None else np.asarray(innovations, float)
    if z.shape != (T,) or not np.isfinite(z).all():
        raise ValueError("多尺度过程创新项须为T个有限数")
    innovation_sd = np.sqrt((1 - phi * phi) ** 3 / (1 + phi * phi))
    out = np.empty(T)
    for t in range(T):
        value = 2 * phi * previous - phi * phi * older + innovation_sd * z[t]
        out[t] = value
        older, previous = previous, value
    return out


def error_path(T, phi, rng):
    """Slow bias/drift plus medium-scale forecast error, both causal AR(2)."""
    slow = stationary_ar2(T, phi, rng)
    medium = stationary_ar2(T, phi ** 4, rng)
    return np.sqrt(SLOW_VARIANCE_WEIGHT) * slow + np.sqrt(1 - SLOW_VARIANCE_WEIGHT) * medium


def renewable_profiles(cfg, frame, seed=None):
    seed = cfg.base["seed"] if seed is None else seed
    fo = cfg.base.get("forecast") or {}
    sigma = {k: {**v, **((fo.get("sigma") or {}).get(k) or {})} for k, v in SIGMA.items()}
    phi = {**PHI, **(fo.get("phi") or {})}
    for kind in ("wind", "pv"):
        if not 0 <= phi[kind] < 1:
            raise ValueError(f"multiscale_v2的forecast.phi.{kind}须满足0<=phi<1")
        if not sigma[kind]["da"] >= sigma[kind]["id"] >= sigma[kind]["rt"]:
            raise ValueError(f"multiscale_v2的{kind}误差须满足da>=id>=rt>=0")
    out = frame.copy()
    T = cfg.T
    common = {}
    for kind in ("wind", "pv"):
        spec = ACTUAL[kind]
        common[kind] = {
            "slow": stationary_ar2(T, spec["slow_phi"], stream(seed, f"v2:{kind}:actual:common:slow")),
            "fast": ar1(T, spec["fast_phi"], stream(seed, f"v2:{kind}:actual:common:fast")),
            "error": error_path(T, phi[kind], stream(seed, f"v2:{kind}:error:common")),
        }
    for unit in cfg.units["renewable"]:
        uid, cap = unit["id"], float(unit["cap"])
        kind = "wind" if unit["profile"] in ("wind_a", "wind_b") else "pv"
        spec = ACTUAL[kind]
        shape = interp96(WIND_A if unit["profile"] == "wind_a" else WIND_B, T) if kind == "wind" else pv_shape(T)
        gate = np.ones(T) if kind == "wind" else np.sqrt(shape / shape.max())
        # Independent solar sites are assumed; wind sites share rho=0.6 weather.
        rho = RHO_W1W2 if kind == "wind" else 0.
        def combine(local, field):
            return np.sqrt(rho) * common[kind][field] + np.sqrt(1 - rho) * local
        slow = combine(stationary_ar2(T, spec["slow_phi"], stream(seed, f"v2:{uid}:actual:slow")), "slow")
        fast = combine(ar1(T, spec["fast_phi"], stream(seed, f"v2:{uid}:actual:fast")), "fast")
        actual = np.clip(cap * (shape + gate * (spec["slow_sigma"] * slow + spec["fast_sigma"] * fast)), 0., cap)
        actual = np.where(gate > 0, actual, 0.)
        out[f"{uid}_actual"] = actual
        error = combine(error_path(T, phi[kind], stream(seed, f"v2:{uid}:error")), "error")
        for stage in ("da", "id", "rt"):
            forecast = np.clip(actual - cap * gate * sigma[kind][stage] * error, 0., cap)
            out[f"{uid}_{stage}"] = np.where(gate > 0, forecast, 0.)
    return out
