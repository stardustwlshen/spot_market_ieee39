"""Versioned, uncalibrated OFFLINE joint forecast-error stress scenarios.

The complete actual trajectory is deliberately an input. These are NOT causal
forecasts, calibrated distributions, or accuracy guarantees. Positive error means
forecast minus actual. No sample/day normalization or actual mutation is used.

Call after resolving final actual/weather inputs. The caller owns provenance:
pass all externally supplied prediction columns in protected_columns. This
module does not infer ownership from values, inspect files, or alter bids.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import math

import numpy as np

VERSION = 1
KINDS = ("load", "wind", "pv")
STAGES = ("da", "id", "rt")
SCENARIO_LABELS = {
    "load": {"configured": "保留当前配置／上传预测", "normal": "常规持续误差",
             "overestimate": "持续高估负荷", "underestimate": "持续低估负荷",
             "lag": "负荷峰谷跟踪滞后", "volatile": "负荷误差较强波动"},
    "wind": {"configured": "保留当前配置／上传预测", "normal": "常规持续误差",
             "overestimate": "持续高估风电", "underestimate": "持续低估风电",
             "lag": "风电爬坡跟踪滞后", "volatile": "风电误差较强波动"},
    "pv": {"configured": "保留当前配置／上传预测", "normal": "常规持续误差",
           "overestimate": "持续高估光伏", "underestimate": "持续低估光伏",
           "lag": "云团变化跟踪滞后", "volatile": "光伏误差较强波动"},
}
RESOURCE_LABELS = {"load": "负荷", "wind": "风电", "pv": "光伏"}
# Research coefficients, not estimates from observations.
RECIPES = {
    "normal": dict(noise_multiplier=1., signed_bias_sigma=0., lag_multiplier=0., phi_power=1.),
    "overestimate": dict(noise_multiplier=.5, signed_bias_sigma=2., lag_multiplier=0., phi_power=1.),
    "underestimate": dict(noise_multiplier=.5, signed_bias_sigma=-2., lag_multiplier=0., phi_power=1.),
    "lag": dict(noise_multiplier=.25, signed_bias_sigma=0., lag_multiplier=1., phi_power=1.),
    "volatile": dict(noise_multiplier=2., signed_bias_sigma=0., lag_multiplier=0., phi_power=2.),
}
DEFAULT_DAYLIGHT = dict(sunrise=6.5, sunset=19.5, shape_power=1.3)
COMMON_VARIANCE = dict(load=0., wind=.6, pv=.3)
LAG_HOURS = dict(load=1., wind=.75, pv=.5)
STAGE_LAG_MULTIPLIER = dict(da=1., id=.5, rt=.25)
SHARED_STAGE_VARIANCE = .8
SLOW_VARIANCE = .7


def _number(value, label, *, minimum=None, maximum=None):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{label} is outside its finite allowed range")
    return value


def _forecast_config(cfg_or_dict):
    if hasattr(cfg_or_dict, "base"):
        result = cfg_or_dict.base.get("forecast") or {}
    elif cfg_or_dict is None:
        result = {}
    elif isinstance(cfg_or_dict, Mapping):
        if "forecast" in cfg_or_dict:
            result = cfg_or_dict["forecast"] or {}
        elif "error_scenarios" in cfg_or_dict or any(k in cfg_or_dict for k in ("model", "sigma", "phi")):
            result = cfg_or_dict
        else:
            result = {"error_scenarios": cfg_or_dict}
    else:
        raise ValueError("forecast/error_scenarios must be a mapping")
    if not isinstance(result, Mapping):
        raise ValueError("forecast must be a mapping")
    return result


def normalized_scenarios(cfg_or_dict=None):
    """Accept Config, base/forecast mapping, or the error_scenarios mapping.

    Always returns a detached full version-1 mapping, including the declared
    PV clock. Missing recipes mean configured (strict pass-through).
    """
    fo = _forecast_config(cfg_or_dict)
    raw = fo.get("error_scenarios")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("forecast.error_scenarios must be a mapping")
    unknown = set(raw) - {"version", *KINDS, "pv_daylight"}
    if unknown:
        raise ValueError(f"unknown error_scenarios keys: {sorted(unknown)}")
    version = raw.get("version", VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != VERSION:
        raise ValueError("error_scenarios.version must be integer 1")
    out = {"version": VERSION}
    for kind in KINDS:
        recipe = raw.get(kind, "configured")
        if not isinstance(recipe, str) or recipe not in SCENARIO_LABELS[kind]:
            raise ValueError(f"unknown {kind} error scenario: {recipe!r}")
        out[kind] = recipe
    daylight = raw.get("pv_daylight", {})
    if not isinstance(daylight, Mapping) or set(daylight) - set(DEFAULT_DAYLIGHT):
        raise ValueError("pv_daylight supports only sunrise, sunset, shape_power")
    daylight = {**DEFAULT_DAYLIGHT, **daylight}
    rise = _number(daylight["sunrise"], "pv_daylight.sunrise", minimum=0., maximum=24.)
    setting = _number(daylight["sunset"], "pv_daylight.sunset", minimum=0., maximum=24.)
    power = _number(daylight["shape_power"], "pv_daylight.shape_power", minimum=.5, maximum=3.)
    if not rise < setting:
        raise ValueError("pv_daylight requires sunrise < sunset")
    out["pv_daylight"] = dict(sunrise=rise, sunset=setting, shape_power=power)
    return out


def _settings(cfg_or_dict, scenarios):
    from .forecast import PHI, SIGMA
    fo = _forecast_config(cfg_or_dict)
    sig, ph = fo.get("sigma") or {}, fo.get("phi") or {}
    if not isinstance(sig, Mapping) or not isinstance(ph, Mapping) or (set(sig) | set(ph)) - set(KINDS):
        raise ValueError("forecast sigma/phi must use load/wind/pv keys")
    sigma = {}
    phi = {}
    for kind in KINDS:
        raw = sig.get(kind) or {}
        if not isinstance(raw, Mapping) or set(raw) - set(STAGES):
            raise ValueError(f"invalid forecast.sigma.{kind}")
        sigma[kind] = {stage: _number(raw.get(stage, SIGMA[kind][stage]),
                        f"forecast.sigma.{kind}.{stage}", minimum=0.) for stage in STAGES}
        phi[kind] = _number(ph.get(kind, PHI[kind]), f"forecast.phi.{kind}")
        if scenarios[kind] != "configured" and not 0 <= phi[kind] < 1:
            raise ValueError(f"active {kind} error scenarios require 0 <= forecast.phi.{kind} < 1")
    return sigma, phi


def _seed(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 0:
        raise ValueError("error scenario seed must be a nonnegative integer")
    return int(value)


def _stream(seed, label):
    words = np.frombuffer(hashlib.sha256(label.encode("utf-8")).digest()[:16], dtype="<u4")
    return np.random.default_rng(np.random.SeedSequence([seed, *map(int, words)]))


def _ar1(count, phi, rng):
    # Stationary initialization; no full-day empirical RMS/mean adjustment.
    z = rng.standard_normal(count)
    result = np.empty(count)
    result[0] = z[0]
    innovation_sd = math.sqrt(max(0., 1. - phi * phi))
    for t in range(1, count):
        result[t] = phi * result[t - 1] + innovation_sd * z[t]
    return result


def _path(count, phi, seed, label):
    slow = _ar1(count, phi ** .25, _stream(seed, label + ":slow"))
    medium = _ar1(count, phi, _stream(seed, label + ":medium"))
    return math.sqrt(SLOW_VARIANCE) * slow + math.sqrt(1. - SLOW_VARIANCE) * medium


def _resource_path(count, phi, seed, kind, uid, stage):
    prefix = f"resource_error_scenarios:v{VERSION}:{kind}"
    rho = COMMON_VARIANCE[kind]

    def field(role):
        common = _path(count, phi, seed, f"{prefix}:common:{role}")
        local = _path(count, phi, seed, f"{prefix}:resource:{uid}:{role}")
        return math.sqrt(rho) * common + math.sqrt(1. - rho) * local

    return (math.sqrt(SHARED_STAGE_VARIANCE) * field("shared")
            + math.sqrt(1. - SHARED_STAGE_VARIANCE) * field(stage))


def _daylight(count, dt, settings):
    # The same interval-midpoint convention as the existing single-day shapes.
    hours = (np.arange(count) + .5) * dt
    rise, setting = settings["sunrise"], settings["sunset"]
    active = (hours > rise) & (hours < setting)
    gate = np.zeros(count)
    gate[active] = np.sin(np.pi * (hours[active] - rise) / (setting - rise)) ** settings["shape_power"]
    return gate


def _values(frame, column, count, *, cap=None, gate=None):
    if column not in frame:
        raise ValueError(f"missing error-scenario column {column}")
    raw = frame[column]
    if any(isinstance(v, (bool, np.bool_)) for v in raw):
        raise ValueError(f"{column} cannot contain booleans")
    try:
        value = raw.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} must contain finite numeric values") from exc
    if value.shape != (count,) or not np.isfinite(value).all() or (value < 0).any():
        raise ValueError(f"{column} must contain {count} finite nonnegative values")
    if cap is not None and (value > cap + 1e-9).any():
        raise ValueError(f"{column} exceeds declared capacity {cap} MW")
    if gate is not None and (value[gate == 0] != 0).any():
        raise ValueError(f"{column} must be zero outside the declared PV daylight; input was not changed")
    return value


def _lag_delta(actual, kind, cap, gate, delay_periods):
    positions = np.arange(len(actual), dtype=float)
    if kind == "pv":
        active = gate > 0
        if not active.any():
            return np.zeros_like(actual)
        # Lag only the daylight-normalized cloud/output component, not the known
        # solar clock. Edge extrapolation is constant, not previous-day truth.
        component = np.interp(positions, positions[active], actual[active] / (cap * gate[active]))
        shifted = np.interp(positions - delay_periods, positions, component)
        return cap * gate * (shifted - component)
    shifted = np.interp(positions - delay_periods, positions, actual)
    return shifted - actual


def apply_error_scenarios(cfg, frame, *, seed=None, protected_columns=()):
    """Return a detached frame; change only unprotected active prediction columns.

    All actuals and protected predictions for an active resource are validated,
    never repaired. Configured resources are untouched, including historical
    values. Source files, prices and availability declarations are caller-owned.
    """
    scenarios = normalized_scenarios(cfg)
    out = frame.copy(deep=True)
    out.attrs = copy.deepcopy(frame.attrs)
    if all(scenarios[k] == "configured" for k in KINDS):
        return out
    if (cfg.base.get("forecast") or {}).get("model") == "causal_physical_v3":
        raise ValueError("offline error scenarios cannot modify causal_physical_v3 issued forecasts")
    if not frame.columns.is_unique:
        raise ValueError("error-scenario frame columns must be unique")
    count = cfg.T
    dt = _number(cfg.dt, "dt_h", minimum=np.finfo(float).eps)
    if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1 or len(frame) != count:
        raise ValueError("error-scenario frame must match cfg.T")
    if not math.isclose(count * dt, 24., rel_tol=0., abs_tol=1e-9):
        raise ValueError("single-day error scenarios require a full 24-hour profile")
    if isinstance(protected_columns, str):
        raise ValueError("protected_columns must be an iterable of column names, not a string")
    protected = set(protected_columns)
    if not all(isinstance(c, str) for c in protected) or protected - set(frame.columns):
        raise ValueError("protected_columns contains a missing or invalid column")
    sigma, phi = _settings(cfg, scenarios)
    seed = _seed(cfg.base.get("seed", 0) if seed is None else seed)
    gate = _daylight(count, dt, scenarios["pv_daylight"])
    resources = [("load", "load", None)]
    seen = {"load"}
    for unit in cfg.units["renewable"]:
        uid, kind = unit["id"], unit["type"]
        if not isinstance(uid, str) or uid in seen or kind not in ("wind", "pv"):
            raise ValueError("renewable identities/kinds must be unique wind/pv resources")
        seen.add(uid)
        cap = _number(unit["cap"], f"{uid}.cap", minimum=np.finfo(float).eps)
        resources.append((uid, kind, cap))
    from .forecast import SIGMA
    for uid, kind, cap in resources:
        recipe_id = scenarios[kind]
        if recipe_id == "configured":
            continue
        actual = _values(frame, uid + "_actual", count, cap=cap, gate=gate if kind == "pv" else None)
        recipe = RECIPES[recipe_id]
        # phi is a 15-minute base coefficient; adjust it to the actual grid.
        effective_phi = phi[kind] ** (recipe["phi_power"] * dt / .25)
        for stage in STAGES:
            column = f"{uid}_{stage}"
            if column in protected:
                _values(frame, column, count, cap=cap, gate=gate if kind == "pv" else None)
                continue
            path = _resource_path(count, effective_phi, seed, kind, uid, stage)
            if recipe["signed_bias_sigma"]:
                # Bounded jitter preserves the declared direction pointwise.
                path = np.tanh(path)
            error = recipe["signed_bias_sigma"] + recipe["noise_multiplier"] * path
            scale = actual if kind == "load" else cap * gate if kind == "pv" else cap
            try:
                with np.errstate(over="raise", invalid="raise"):
                    value = actual + sigma[kind][stage] * scale * error
                    if recipe["lag_multiplier"]:
                        delay = LAG_HOURS[kind] * STAGE_LAG_MULTIPLIER[stage] / dt
                        value = value + (sigma[kind][stage] / SIGMA[kind]["da"]) * recipe["lag_multiplier"] * _lag_delta(actual, kind, cap, gate, delay)
            except FloatingPointError as exc:
                raise ValueError(f"{column}: error-scenario arithmetic overflow") from exc
            if not np.isfinite(value).all():
                raise ValueError(f"{column}: error-scenario values are not finite")
            value = np.maximum(value, 0.) if cap is None else np.clip(value, 0., cap)
            if kind == "pv":
                value = np.where(gate > 0, value, 0.)
            out[column] = value
    return out


def scenario_metadata(cfg_or_dict):
    """Serializable method/parameter disclosure, not realized accuracy statistics."""
    scenarios = normalized_scenarios(cfg_or_dict)
    sigma, phi = _settings(cfg_or_dict, scenarios)
    selected = {}
    for kind in KINDS:
        recipe = scenarios[kind]
        selected[kind] = dict(recipe=recipe, label=SCENARIO_LABELS[kind][recipe],
            coefficients=None if recipe == "configured" else copy.deepcopy(RECIPES[recipe]),
            sigma_baseline=sigma[kind], phi_baseline_15min=phi[kind],
            same_kind_common_variance=COMMON_VARIANCE[kind],
            lag_base_hours=LAG_HOURS[kind])
    return dict(version=VERSION, method="offline_joint_resource_error_stress_scenarios",
        calibrated=False, error_definition="forecast_minus_actual", scenarios=selected,
        seed=_seed(cfg_or_dict.base.get("seed", 0)) if hasattr(cfg_or_dict, "base") else None,
        numpy_version=np.__version__,
        pv_daylight=scenarios["pv_daylight"],
        pv_daylight_role="declared_midpoint_error_amplitude_envelope_and_night_gate; hard_upper_bound_is_nameplate_capacity",
        normalization=dict(load="contemporaneous_actual_load", wind="nameplate_capacity",
                           pv="nameplate_capacity_times_declared_daylight_shape"),
        shared_stage_variance=SHARED_STAGE_VARIANCE, slow_variance=SLOW_VARIANCE,
        process="stationary_AR1_slow_plus_medium; slow_phi=effective_phi**0.25",
        effective_phi="configured_phi**(recipe.phi_power*dt_h/0.25)",
        signed_bias="over/under use +/-2 sigma plus 0.5 sigma*tanh(correlated_path)",
        lag_scale="configured_stage_sigma/default_kind_DA_sigma times delayed_component_minus_current_component",
        lag_stage_multiplier=copy.deepcopy(STAGE_LAG_MULTIPLIER),
        lag_edge_policy="constant_edge_extension; PV lags daylight-normalized component, not solar clock",
        rng="sha256_labelled_numpy_SeedSequence_default_rng; independent_of_resource_order",
        sigma_role="amplitude_baseline_before_bounds; not a fitted or enforced realized standard deviation",
        limits=["Uncalibrated offline joint stress scenarios, not operational or causal forecasts.",
                "Complete actual trajectories are used. No empirical daily normalization.",
                "DA/ID/RT have correlated revisions, with no guaranteed per-day accuracy ordering.",
                "Explicit protected values remain unchanged; invalid active-resource inputs fail closed.",
                "No source ownership is inferred and no bid availability or price is changed."])

