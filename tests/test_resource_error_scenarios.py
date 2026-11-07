"""Pure, in-memory forecast scenarios: no scene writes, solver or production run."""
from copy import deepcopy
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
import pytest

from smm.resource_error_scenarios import (
    SCENARIO_LABELS, apply_error_scenarios, normalized_scenarios, scenario_metadata,
)


def example(**recipes):
    cfg = SimpleNamespace(T=96, dt=.25,
        base={"seed": 917, "forecast": {"model": "multiscale_v2", "error_scenarios": {"version": 1, **recipes}}},
        units={"renewable": [
            {"id": "W1", "type": "wind", "cap": 100.},
            {"id": "W2", "type": "wind", "cap": 100.},
            {"id": "PV1", "type": "pv", "cap": 100.},
            {"id": "PV2", "type": "pv", "cap": 100.},
        ]})
    hours = (np.arange(96) + .5) * .25
    gate = np.where((hours > 6.5) & (hours < 19.5),
                    np.maximum(np.sin(np.pi * (hours - 6.5) / 13.), 0.) ** 1.3, 0.)
    actuals = {"load": 800 + 100*np.sin(2*np.pi*hours/24.),
               "W1": np.full(96, 50.), "W2": np.full(96, 50.),
               "PV1": 60.*gate, "PV2": 60.*gate}
    frame = pd.DataFrame({"period": np.arange(1, 97), "time": [str(x) for x in hours]})
    for uid, actual in actuals.items():
        for stage in ("actual", "da", "id", "rt"):
            frame[f"{uid}_{stage}"] = actual.copy()
    frame["TIE_sched"] = 37.
    frame.attrs = {"source": {"description": "test-only in-memory fixture"}}
    return cfg, frame


def test_missing_configuration_preserves_every_value_and_detaches():
    cfg, frame = example()
    before = frame.copy(deep=True)
    result = apply_error_scenarios(cfg, frame)
    pd.testing.assert_frame_equal(result, before, check_exact=True)
    result.attrs["source"]["description"] = "changed copy"
    result.loc[0, "load_da"] = -1.
    pd.testing.assert_frame_equal(frame, before, check_exact=True)
    assert frame.attrs["source"]["description"] != "changed copy"
    assert normalized_scenarios({})["load"] == "configured"
    assert normalized_scenarios(cfg) == normalized_scenarios(cfg.base)
    assert normalized_scenarios(cfg) == normalized_scenarios(cfg.base["forecast"])
    assert normalized_scenarios(cfg) == normalized_scenarios(cfg.base["forecast"]["error_scenarios"])


@pytest.mark.parametrize("kind", ["load", "wind", "pv"])
@pytest.mark.parametrize("recipe", ["normal", "overestimate", "underestimate", "lag", "volatile"])
def test_active_resource_only_actuals_untouched_and_repeatable(kind, recipe):
    cfg, frame = example(**{kind: recipe})
    before = frame.copy(deep=True)
    result = apply_error_scenarios(cfg, frame)
    repeated = apply_error_scenarios(cfg, result)
    pd.testing.assert_frame_equal(result, repeated, check_exact=True)
    pd.testing.assert_frame_equal(frame, before, check_exact=True)
    selected = {"load"} if kind == "load" else {"W1", "W2"} if kind == "wind" else {"PV1", "PV2"}
    for column in frame:
        editable = any(column == f"{uid}_{stage}" for uid in selected for stage in ("da", "id", "rt"))
        if not editable:
            pd.testing.assert_series_equal(frame[column], result[column], check_exact=True)
    assert any(not np.array_equal(frame[f"{uid}_da"], result[f"{uid}_da"]) for uid in selected)
    assert not np.array_equal(result[f"{sorted(selected)[0]}_da"],
                              apply_error_scenarios(cfg, frame, seed=918)[f"{sorted(selected)[0]}_da"])


def test_reordering_and_adding_resources_keeps_existing_outputs_exact():
    cfg, frame = example(load="normal", wind="normal", pv="normal")
    baseline = apply_error_scenarios(cfg, frame)
    cfg.units["renewable"].reverse()
    reordered = apply_error_scenarios(cfg, frame)
    pd.testing.assert_frame_equal(baseline, reordered, check_exact=True)
    cfg.units["renewable"].append({"id": "Wnew", "type": "wind", "cap": 100.})
    for stage in ("actual", "da", "id", "rt"):
        frame[f"Wnew_{stage}"] = 50.
    extra = apply_error_scenarios(cfg, frame)
    pd.testing.assert_frame_equal(baseline, extra[baseline.columns], check_exact=True)


@pytest.mark.parametrize("kind,uid", [("load", "load"), ("wind", "W1"), ("pv", "PV1")])
def test_persistent_bias_has_declared_pointwise_sign(kind, uid):
    cfg, frame = example(**{kind: "overestimate"})
    high = apply_error_scenarios(cfg, frame)
    cfg.base["forecast"]["error_scenarios"][kind] = "underestimate"
    low = apply_error_scenarios(cfg, frame)
    for stage in ("da", "id", "rt"):
        assert (high[f"{uid}_{stage}"] >= frame[f"{uid}_actual"] - 1e-10).all()
        assert (low[f"{uid}_{stage}"] <= frame[f"{uid}_actual"] + 1e-10).all()
        assert high[f"{uid}_{stage}"].sum() > frame[f"{uid}_actual"].sum()
        assert low[f"{uid}_{stage}"].sum() < frame[f"{uid}_actual"].sum()


def test_extreme_amplitude_physical_bounds_and_declared_night():
    cfg, frame = example(load="volatile", wind="volatile", pv="volatile")
    cfg.base["forecast"]["sigma"] = {k: {s: 100. for s in ("da", "id", "rt")} for k in ("load", "wind", "pv")}
    result = apply_error_scenarios(cfg, frame)
    for uid in ("load", "W1", "W2", "PV1", "PV2"):
        for stage in ("da", "id", "rt"):
            values = result[f"{uid}_{stage}"]
            assert np.isfinite(values).all() and (values >= 0).all()
            if uid != "load":
                assert (values <= 100.).all()
            if uid.startswith("PV"):
                assert (values[frame["PV1_actual"] == 0.] == 0.).all()


def test_zero_sigma_means_actual_even_for_lag():
    cfg, frame = example(load="lag", wind="lag", pv="lag")
    cfg.base["forecast"]["sigma"] = {k: {s: 0. for s in ("da", "id", "rt")} for k in ("load", "wind", "pv")}
    result = apply_error_scenarios(cfg, frame)
    for uid in ("load", "W1", "W2", "PV1", "PV2"):
        for stage in ("da", "id", "rt"):
            np.testing.assert_array_equal(result[f"{uid}_{stage}"], frame[f"{uid}_actual"])


def test_pv_daytime_zero_is_not_a_night_mask_and_custom_clock_is_honored():
    cfg, frame = example(pv="overestimate")
    frame.loc[48, "PV1_actual"] = 0.  # A noon cloud event, not night.
    result = apply_error_scenarios(cfg, frame)
    assert result.loc[48, "PV1_da"] > 0.
    cfg.base["forecast"]["error_scenarios"]["pv_daylight"] = {"sunrise": 8., "sunset": 17.}
    hour = (np.arange(96) + .5) * .25
    outside = (hour < 8.) | (hour > 17.)
    for uid in ("PV1", "PV2"):
        frame.loc[outside, uid + "_actual"] = 0.
    result = apply_error_scenarios(cfg, frame)
    assert (result.loc[outside, ["PV1_da", "PV1_id", "PV1_rt", "PV2_da"]] == 0.).all().all()
    assert scenario_metadata(cfg)["pv_daylight"]["sunrise"] == 8.


def test_protected_predictions_exact_invalid_values_rejected_without_mutation():
    cfg, frame = example(load="normal", wind="normal", pv="normal")
    frame["load_id"] = 789.123456789
    frame["W1_da"] = 12.3456789123
    frame["PV1_rt"] = frame["PV1_actual"] / 2.
    protected = ("load_id", "W1_da", "PV1_rt")
    result = apply_error_scenarios(cfg, frame, protected_columns=protected)
    for column in protected:
        pd.testing.assert_series_equal(result[column], frame[column], check_exact=True)
    bad = frame.copy()
    bad.loc[0, "PV1_rt"] = .1
    before = bad.copy()
    with pytest.raises(ValueError, match="PV1_rt.*daylight"):
        apply_error_scenarios(cfg, bad, protected_columns=protected)
    pd.testing.assert_frame_equal(bad, before, check_exact=True)
    bad = frame.copy()
    bad.loc[0, "W1_da"] = 101.
    with pytest.raises(ValueError, match="W1_da.*capacity"):
        apply_error_scenarios(cfg, bad, protected_columns=protected)


@pytest.mark.parametrize("column,value,message", [
    ("W1_actual", 101., "capacity"), ("load_actual", -1., "nonnegative"),
    ("PV1_actual", 1., "daylight"), ("load_actual", float("nan"), "finite"),
])
def test_invalid_actual_is_never_silently_repaired(column, value, message):
    cfg, frame = example(load="normal", wind="normal", pv="normal")
    frame.loc[0, column] = value
    before = frame.copy()
    with pytest.raises(ValueError, match=message):
        apply_error_scenarios(cfg, frame)
    pd.testing.assert_frame_equal(frame, before, check_exact=True)


def test_configured_kind_is_not_revalidated_against_new_pv_clock():
    cfg, frame = example(wind="normal")
    frame.loc[0, "PV1_actual"] = 1.  # Historical source remains caller's responsibility.
    result = apply_error_scenarios(cfg, frame)
    pd.testing.assert_series_equal(result["PV1_actual"], frame["PV1_actual"])


def test_lag_tracks_resource_specific_changes_without_delaying_solar_clock():
    cfg, frame = example(load="lag", wind="lag", pv="lag")
    frame["load_actual"] = np.r_[np.full(48, 500.), np.full(48, 1000.)]
    frame["W1_actual"] = np.r_[np.full(48, 10.), np.full(48, 90.)]
    # For PV, a cloud suddenly clears at noon. The delay acts on normalized
    # cloud/output, not on the deterministic solar position itself.
    frame.loc[:47, "PV1_actual"] *= .1
    result = apply_error_scenarios(cfg, frame)
    assert (result.loc[48:50, "load_da"] < frame.loc[48:50, "load_actual"] - 200.).all()
    assert (result.loc[48:50, "W1_da"] < frame.loc[48:50, "W1_actual"] - 40.).all()
    assert result.loc[48, "PV1_da"] < frame.loc[48, "PV1_actual"] - 20.
    # PV2 has constant daylight-normalized fraction; its lag term is zero.
    cfg2 = deepcopy(cfg)
    cfg2.base["forecast"]["error_scenarios"]["pv"] = "normal"
    normal = apply_error_scenarios(cfg2, frame)
    a = result["PV2_da"] - frame["PV2_actual"]
    b = normal["PV2_da"] - frame["PV2_actual"]
    np.testing.assert_allclose(a, .25*b, atol=1e-12)


def test_temporal_and_cross_site_structure_not_independent_point_noise():
    cfg, frame = example(wind="normal")
    cfg.base["forecast"]["sigma"] = {"wind": {"da": .01, "id": .01, "rt": .01}}
    lag1, site1, site2, rms = [], [], [], []
    for seed in range(64):
        result = apply_error_scenarios(cfg, frame, seed=seed)
        e1 = (result["W1_da"] - frame["W1_actual"]).to_numpy()
        e2 = (result["W2_da"] - frame["W2_actual"]).to_numpy()
        lag1.append(np.corrcoef(e1[:-1], e1[1:])[0, 1])
        site1.extend(e1); site2.extend(e2)
        rms.append(np.sqrt(np.mean(e1**2)))
    assert np.mean(lag1) > .75
    assert .35 < np.corrcoef(site1, site2)[0, 1] < .85
    assert np.std(rms) > .15  # No per-day forced RMSE.
    assert not np.array_equal(site1, site2)


@pytest.mark.parametrize("raw", [
    {"version": 2}, {"version": True}, {"wind": "unknown"}, {"wind": {}},
    {"load": None}, {"unknown": "normal"}, {"pv_daylight": {"sunrise": 20., "sunset": 5.}},
    {"pv_daylight": {"sunrise": float("nan")}}, {"pv_daylight": {"extra": 1}},
])
def test_invalid_scenarios_fail_closed(raw):
    with pytest.raises(ValueError):
        normalized_scenarios(raw)


@pytest.mark.parametrize("field,value", [
    ("phi", {"wind": -.1}), ("phi", {"wind": 1.}),
    ("sigma", {"wind": {"da": -1.}}), ("sigma", {"wind": {"unknown": .1}}),
])
def test_invalid_active_parameters_rejected(field, value):
    cfg, frame = example(wind="normal")
    cfg.base["forecast"][field] = value
    with pytest.raises(ValueError):
        apply_error_scenarios(cfg, frame)


def test_metadata_is_explicit_serializable_and_does_not_promise_accuracy_order():
    cfg, _ = example(load="lag", wind="overestimate", pv="volatile")
    metadata = scenario_metadata(cfg)
    assert metadata["calibrated"] is False
    assert metadata["error_definition"] == "forecast_minus_actual"
    assert metadata["scenarios"]["wind"]["coefficients"]["signed_bias_sigma"] == 2.
    assert metadata["scenarios"]["pv"]["coefficients"]["noise_multiplier"] == 2.
    assert metadata["scenarios"]["load"]["label"] == SCENARIO_LABELS["load"]["lag"]
    assert "no guaranteed" in " ".join(metadata["limits"])
    json.dumps(metadata, ensure_ascii=False, allow_nan=False)


def test_stage_amplitudes_are_independent_controls_not_a_forced_order():
    cfg, frame = example(wind="normal")
    cfg.base["forecast"]["sigma"] = {"wind": {"da": 0., "id": .01, "rt": .03}}
    result = apply_error_scenarios(cfg, frame)
    np.testing.assert_array_equal(result["W1_da"], frame["W1_actual"])
    assert not np.array_equal(result["W1_id"], frame["W1_actual"])
    assert not np.array_equal(result["W1_rt"], frame["W1_actual"])
    changed = deepcopy(cfg)
    changed.base["forecast"]["sigma"]["wind"]["id"] = .02
    again = apply_error_scenarios(changed, frame)
    np.testing.assert_array_equal(again["W1_da"], result["W1_da"])
    np.testing.assert_array_equal(again["W1_rt"], result["W1_rt"])
    assert not np.array_equal(again["W1_id"], result["W1_id"])


def test_offline_recipe_cannot_modify_v3_publications():
    cfg, frame = example(load="normal")
    cfg.base["forecast"]["model"] = "causal_physical_v3"
    with pytest.raises(ValueError, match="causal_physical_v3"):
        apply_error_scenarios(cfg, frame)


def test_final_actual_rebuilds_missing_forecasts_without_mutating_configuration():
    cfg, frame = example(wind="normal")
    before = deepcopy(cfg.base)
    clean = apply_error_scenarios(cfg, frame)
    changed = frame.drop(columns=["W1_da", "W1_id", "W1_rt"]).copy()
    rebuilt = apply_error_scenarios(cfg, changed)
    for stage in ("da", "id", "rt"):
        np.testing.assert_array_equal(clean[f"W1_{stage}"], rebuilt[f"W1_{stage}"])
    changed["W1_actual"] = 70.
    revised = apply_error_scenarios(cfg, changed)
    assert not np.array_equal(revised["W1_da"], clean["W1_da"])
    np.testing.assert_array_equal(revised["W1_actual"], changed["W1_actual"])
    assert cfg.base == before


@pytest.mark.parametrize("protected", ["W1_da", ["missing"], [3]])
def test_invalid_protected_column_contract_rejected(protected):
    cfg, frame = example(wind="normal")
    with pytest.raises(ValueError, match="protected_columns"):
        apply_error_scenarios(cfg, frame, protected_columns=protected)


@pytest.mark.parametrize("seed", [-1, True, 1.5])
def test_invalid_seed_rejected(seed):
    cfg, frame = example(wind="normal")
    with pytest.raises(ValueError, match="seed"):
        apply_error_scenarios(cfg, frame, seed=seed)


def test_grid_clock_and_original_metadata_are_preserved():
    cfg, frame = example(load="normal", wind="normal", pv="normal")
    cfg.T = 48
    cfg.dt = .5
    short = frame.iloc[::2].reset_index(drop=True).copy()
    # Recreate PV with the declared half-hour midpoints rather than copying an
    # incompatible first 48 slots from the original quarter-hour day.
    hours = (np.arange(48) + .5) * .5
    gate = np.where((hours > 6.5) & (hours < 19.5),
                    np.maximum(np.sin(np.pi*(hours-6.5)/13.), 0.)**1.3, 0.)
    short["PV1_actual"] = short["PV2_actual"] = 60.*gate
    result = apply_error_scenarios(cfg, short)
    assert result.attrs == short.attrs
    np.testing.assert_array_equal(result["period"], short["period"])
    assert np.isfinite(result["load_da"]).all()
    cfg.dt = .25
    with pytest.raises(ValueError, match="24-hour"):
        apply_error_scenarios(cfg, short)

