"""Physics, compatibility, causal construction and distributional validation."""
import copy
import hashlib

import numpy as np
import pandas as pd
import pytest

from smm.config import load_config
from smm.forecast import apply_profile_overrides, forecast_view, generate_profiles, pv_shape
from smm.forecast_multiscale import error_path, stationary_ar2


def config_v2():
    cfg = load_config()
    cfg.base['forecast'] = {'model': 'multiscale_v2'}
    return cfg


def test_legacy_omission_and_explicit_version_are_bitwise_unchanged():
    cfg = load_config()
    cfg.base.pop("tie_line_mode", None)  # exact historical supply-profile golden
    cfg.base.pop("forecast", None)  # omission contract, independent of repository defaults
    cfg.units["renewable"] = [u for u in cfg.units["renewable"] if u["id"] not in {"PV2", "PV3"}]  # original asset golden
    frame = generate_profiles(cfg)
    # Recorded with published c83d3ba. Round only this golden assertion to avoid
    # platform libm's last-bit sin differences; actual outputs are not rounded.
    values = np.round(frame.to_numpy()[:, 2:].astype(float), 8).astype('<f8').tobytes()
    assert hashlib.sha256(values).hexdigest() == '4460d2b5de859a6585d0ab4f9debe364adde35aa453272d8897525cf9d2a4f6e'
    cfg.base['forecast'] = {'model': 'legacy_v1'}
    pd.testing.assert_frame_equal(generate_profiles(cfg), frame, check_exact=True)


def test_determinism_independent_seed_truth_and_unchanged_load_tie():
    cfg = config_v2()
    a = generate_profiles(cfg)
    pd.testing.assert_frame_equal(a, generate_profiles(cfg), check_exact=True)
    b = generate_profiles(cfg, cfg.base['seed'] + 1)
    assert not np.array_equal(a.W1_actual, b.W1_actual)
    assert not np.array_equal(a.PV1_actual, b.PV1_actual)
    cfg.base['forecast']['model'] = 'legacy_v1'
    old = generate_profiles(cfg)
    columns = [c for c in a if c.startswith(('load_', 'TIE_')) or c in ('period', 'time')]
    pd.testing.assert_frame_equal(a[columns], old[columns], check_exact=True)


def test_truth_independent_of_forecast_parameters_and_unit_order():
    cfg = config_v2()
    a = generate_profiles(cfg)
    cfg.base['forecast'].update(sigma={'wind': {'da': .3, 'id': .1, 'rt': .02}}, phi={'wind': .8})
    cfg.units['renewable'].reverse()
    b = generate_profiles(cfg)
    for uid in ('W1', 'W2', 'PV1'):
        np.testing.assert_array_equal(a[uid + '_actual'], b[uid + '_actual'])
    cfg.base['forecast'] = {'model': 'multiscale_v2'}
    b = generate_profiles(cfg)
    for uid in ('W1', 'W2', 'PV1'):
        for stage in ('actual', 'da', 'id', 'rt'):
            np.testing.assert_array_equal(a[uid + '_' + stage], b[uid + '_' + stage])


@pytest.mark.parametrize('phi', [0., .5, .999999999999])
@pytest.mark.parametrize('sigma', [0., .15, 100.])
def test_extreme_valid_parameters_bounds_night_zero_and_accuracy_order(phi, sigma):
    cfg = config_v2()
    cfg.base['forecast'].update(phi={'wind': phi, 'pv': phi},
        sigma={kind: {'da': sigma, 'id': sigma / 2, 'rt': sigma / 4} for kind in ('wind', 'pv')})
    p = generate_profiles(cfg)
    for unit in cfg.units['renewable']:
        uid, cap = unit['id'], unit['cap']
        for stage in ('actual', 'da', 'id', 'rt'):
            values = p[f'{uid}_{stage}'].to_numpy()
            assert np.isfinite(values).all() and (values >= 0).all() and (values <= cap).all()
            if uid == 'PV1':
                assert (values[pv_shape(cfg.T) == 0] == 0).all()
        errors = [abs(p[f'{uid}_{stage}'] - p[f'{uid}_actual']) for stage in ('da', 'id', 'rt')]
        assert (errors[0] + 1e-10 >= errors[1]).all()
        assert (errors[1] + 1e-10 >= errors[2]).all()
        if sigma == 0:
            for stage in ('da', 'id', 'rt'):
                np.testing.assert_array_equal(p[f'{uid}_actual'], p[f'{uid}_{stage}'])


@pytest.mark.parametrize('forecast', [
    {'model': 'unknown'}, {'model': 'multiscale_v2', 'extra': 1},
    {'model': 'multiscale_v2', 'phi': {'wind': -.1}},
    {'model': 'multiscale_v2', 'phi': {'wind': 1.}},
    {'model': 'multiscale_v2', 'phi': {'wind': float('nan')}},
    {'model': 'multiscale_v2', 'sigma': {'wind': {'da': -.01}}},
    {'model': 'multiscale_v2', 'sigma': {'wind': {'da': .01, 'id': .1}}},
    {'model': 'multiscale_v2', 'sigma': {'pv': {'rt': 1.}}},
])
def test_invalid_configuration_fails_closed(forecast):
    cfg = load_config()
    cfg.base['forecast'] = forecast
    with pytest.raises(ValueError):
        generate_profiles(cfg)


def test_process_uses_no_future_innovations_or_daily_rescaling():
    z = np.linspace(-1, 1, 96)
    changed = z.copy()
    changed[48:] = 100.
    a = stationary_ar2(96, .9, np.random.default_rng(1), z)
    b = stationary_ar2(96, .9, np.random.default_rng(1), changed)
    np.testing.assert_array_equal(a[:48], b[:48])
    assert not np.array_equal(a[48:], b[48:])
    prefix = stationary_ar2(48, .9, np.random.default_rng(1), z[:48])
    np.testing.assert_array_equal(a[:48], prefix)


@pytest.mark.parametrize('phi', [0., .5, .96, .999])
def test_stationary_unit_variance_without_empirical_normalization(phi):
    draws = np.array([stationary_ar2(8, phi, np.random.default_rng(seed)) for seed in range(2000)])
    for t in (0, 7):
        assert abs(draws[:, t].mean()) < .075
        assert .88 < draws[:, t].var() < 1.12
    # Realized daily RMS varies, proving the implementation does not fit each day.
    rms = np.sqrt(np.mean(draws ** 2, axis=1))
    assert np.std(rms) > .1


def test_uploaded_values_remain_exact_and_rolling_contract_unchanged():
    cfg = config_v2()
    p = generate_profiles(cfg)
    override = pd.DataFrame({'W1_actual': np.linspace(100, 800, cfg.T),
                             'W1_da': np.full(cfg.T, 300.), 'PV1_rt': np.zeros(cfg.T)})
    after = apply_profile_overrides(cfg, p, override)
    for col in override:
        np.testing.assert_array_equal(after[col], override[col])
    pd.testing.assert_series_equal(after.W2_actual, p.W2_actual)
    assert forecast_view(after, 'W1', 0, [0, 1], first_actual=True)[0] == override.W1_actual.iloc[0]
    assert forecast_view(after, 'W1', 0, [1], first_actual=True)[0] == pytest.approx(after.W1_rt.iloc[1])


def test_multiseed_smoothness_without_erasing_prediction_error():
    old = load_config()
    old.base['forecast'] = {'model': 'legacy_v1'}  # explicit comparator, not the new v2 default
    new = config_v2()
    metrics = {model: [] for model in ('legacy', 'v2')}
    for seed in range(20260715, 20260715 + 128):
        for model, cfg in (('legacy', old), ('v2', new)):
            p = generate_profiles(cfg, seed)
            a, f = p.W1_actual.to_numpy(), p.W1_da.to_numpy()
            metrics[model].append([np.mean(abs(np.diff(f))), np.mean(np.diff(f, n=2) ** 2),
                                   np.mean((f - a) ** 2), np.mean(abs(np.diff(a)))])
            if model == 'v2':
                for uid in ('W1', 'W2', 'PV1'):
                    errors = [np.mean((p[uid + '_' + stage] - p[uid + '_actual']) ** 2)
                              for stage in ('da', 'id', 'rt')]
                    assert errors[0] >= errors[1] >= errors[2]
    o, n = (np.mean(metrics[k], axis=0) for k in ('legacy', 'v2'))
    assert n[0] < .65 * o[0]       # Less adjacent movement.
    assert n[1] < .35 * o[1]       # Less alternating jaggedness, squared measure.
    assert .8 < np.sqrt(n[2] / o[2]) < 1.2  # Do not simply shrink forecast errors.
    assert n[3] > 1.2 * o[3]      # Actual wind no longer a fixed smooth template.


def test_new_profiles_build_full_network_model_without_changing_dispatch_rules():
    from smm.bids import default_bids
    from smm.case import build_da_case
    from smm.network import build_network
    from smm.scuc import build_model
    import pyomo.environ as pyo
    cfg = config_v2()
    p = generate_profiles(cfg)
    case = build_da_case(cfg, build_network(cfg), p, default_bids(cfg))
    model = build_model(case)
    for t in range(96):
        assert pyo.value(model.c_wfc['W1', t].upper) == p.W1_da.iloc[t]
    bids = default_bids(cfg)
    bids['units']['W1']['avail_pmax'] = [432.125] * 96
    declared = build_da_case(cfg, case.net, p, bids)
    np.testing.assert_array_equal(declared.ren_fc['W1'], np.full(96, 432.125))
    np.testing.assert_array_equal(declared.profiles.W1_actual, p.W1_actual)
    assert declared.bids['units']['W1']['avail_pmax'] == [432.125] * 96
