"""Curve-first scene previews preserve inputs, source precedence and time units."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from test_renewable_input_view import inputs, figure
from test_single_day_workspace import scene, app, item, files, step
from scenario_curve_view import scenario_curves, render_scenario_curves


@pytest.fixture
def curves(inputs):
    cfg, profiles, bids = inputs
    cfg.base = {"tie_line_mode": "supply"}
    for i, stage in enumerate(("actual", "da", "id", "rt")):
        profiles["load_" + stage] = np.arange(cfg.T, dtype=float) + 2000 + i
    return cfg, profiles, bids


@pytest.mark.parametrize("resource", ["load", "wind", "pv", "renewable"])
def test_resource_totals_and_all_stages_match_exact_inputs(curves, resource):
    cfg, profiles, bids = curves
    before = (deepcopy(cfg), profiles.copy(deep=True), deepcopy(bids))
    table, warnings, axis, quoted = scenario_curves(cfg, profiles, resource=resource, stages=["da", "id", "rt"], bids=bids)
    assert not warnings and table.shape == (96, 6)
    assert table["时间"].tolist() == profiles.time.tolist()
    assert table["时段"].tolist() == list(range(1, 97))
    if resource == "load":
        expected = profiles.load_actual
        assert axis == "负荷（MW）" and not quoted
        np.testing.assert_array_equal(table.iloc[:, 3], profiles.load_da)
    else:
        ids = {"wind": ["W1", "W2"], "pv": ["PV1", "PV2"], "renewable": ["W1", "W2", "PV1", "PV2"]}[resource]
        expected = profiles[[uid + "_actual" for uid in ids]].sum(axis=1)
        da = sum(np.asarray(bids["units"][uid]["avail_pmax"]) if bids["units"][uid]["avail_pmax"] is not None else profiles[uid + "_da"].to_numpy() for uid in ids)
        np.testing.assert_array_equal(table.iloc[:, 3], da)
        assert ("申报优先" in table.columns[3]) == ("W1" in ids)
    np.testing.assert_array_equal(table.iloc[:, 2], expected)
    pd.testing.assert_frame_equal(profiles, before[1])
    assert cfg == before[0] and bids == before[2]


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1, True, "invalid"])
def test_bad_load_is_a_gap_not_fabricated_zero(curves, bad):
    cfg, profiles, bids = curves
    profiles["load_da"] = profiles.load_da.astype(object)
    profiles.loc[7, "load_da"] = bad
    table, warnings, _, _ = scenario_curves(cfg, profiles, resource="load", stages=["da"], bids=bids)
    assert pd.isna(table.iloc[7, 3]) and warnings
    assert table.iloc[:, 3].notna().sum() == 95


def test_partial_renewable_total_and_missing_quote_stay_unknown(curves):
    cfg, profiles, bids = curves
    profiles = profiles.drop(columns="W2_id")
    bids["units"].pop("W2")
    table, warnings, _, _ = scenario_curves(cfg, profiles, resource="wind", stages=["da", "id"], bids=bids)
    assert table.iloc[:, 3:].isna().all().all() and warnings


def test_actual_only_and_selected_unit_do_not_duplicate_or_modify(curves):
    cfg, profiles, bids = curves
    table, warnings, _, _ = scenario_curves(cfg, profiles, resource="wind", stages=[], unit_ids=["W2", "W2"], bids=bids)
    assert table.shape == (96, 3) and not warnings
    np.testing.assert_array_equal(table.iloc[:, 2], profiles.W2_actual)


def render_test():
    import streamlit as st
    from scenario_curve_view import render_scenario_curves
    render_scenario_curves(st.session_state["cfg"], st.session_state["profiles"], bids=st.session_state["bids"], key_prefix="test")


def test_direct_chart_controls_and_details_are_optional(curves):
    at = AppTest.from_function(render_test)
    at.session_state["cfg"], at.session_state["profiles"], at.session_state["bids"] = curves
    at.run()
    assert not at.exception and len(figure(at)["data"]) == 4
    assert not item(at.expander, "曲线数据与来源").proto.expanded
    item(at.selectbox, "查看资源").set_value("wind").run()
    item(at.selectbox, "查看机组").set_value("W1").run()
    item(at.multiselect, "对比阶段").set_value(["da", "rt"]).run()
    assert not at.exception
    chart = figure(at)
    assert len(chart["data"]) == 3 and "申报优先" in chart["data"][1]["name"]
    np.testing.assert_array_equal(chart["data"][1]["y"], curves[2]["units"]["W1"]["avail_pmax"])
    assert chart["layout"]["yaxis"]["title"]["text"] == "可用功率（MW）"
    assert all(len(line["x"]) == len(line["y"]) == 96 for line in chart["data"])
    item(at.selectbox, "查看资源").set_value("pv").run()
    assert item(at.selectbox, "查看机组").value is None
    item(at.multiselect, "对比阶段").set_value([]).run()
    at.run()
    assert len(figure(at)["data"]) == 1 and item(at.multiselect, "对比阶段").value == []


def test_scene_home_defaults_to_current_curves_without_source_writes(scene):
    from smm.config import load_config
    from smm.forecast import generate_profiles, apply_profile_overrides
    before = files(scene.SCEN_DIR)
    cfg = load_config(root=scene.scenario_path("默认"))
    expected = apply_profile_overrides(cfg, generate_profiles(cfg))
    at = app()
    assert not at.exception and len(figure(at)["data"]) == 4
    np.testing.assert_array_equal(figure(at)["data"][0]["y"], expected.load_actual)
    np.testing.assert_array_equal(figure(at)["data"][1]["y"], expected.load_da)
    item(at.selectbox, "负荷预测误差").set_value("volatile").run()
    assert not at.exception
    np.testing.assert_array_equal(figure(at)["data"][0]["y"], expected.load_actual)
    assert not np.allclose(figure(at)["data"][1]["y"], expected.load_da)
    item(at.selectbox, "负荷预测误差").set_value("configured").run()
    np.testing.assert_array_equal(figure(at)["data"][1]["y"], expected.load_da)
    step(at, "2 机组"); step(at, "1 场景")
    assert not at.exception and files(scene.SCEN_DIR) == before


def test_uploaded_load_forecast_remains_visible_under_error_choice(scene):
    from smm.config import load_config
    cfg = load_config(root=scene.scenario_path("默认"))
    path = cfg.root / "data/profile_overrides.csv"
    pd.DataFrame({"load_da": [1234.567] * cfg.T}).to_csv(path, index=False)
    before = files(scene.SCEN_DIR)
    at = app()
    item(at.selectbox, "负荷预测误差").set_value("underestimate").run()
    assert not at.exception
    np.testing.assert_array_equal(figure(at)["data"][1]["y"], [1234.567] * cfg.T)
    assert any("负荷·日前" in warning.value for warning in at.warning)
    source = item(at.expander, "曲线数据与来源").dataframe[1].value
    assert source.loc[source["列"] == "load_da", "来源"].iloc[0] == "上传列"
    assert files(scene.SCEN_DIR) == before


def test_wind_preview_matches_selected_multiplier_and_error(scene):
    from smm.config import load_config
    from smm.typical_scenarios import prepare_typical
    at = app()
    before = files(scene.SCEN_DIR)
    item(at.selectbox, "风电").set_value(.8)
    item(at.selectbox, "风电预测误差").set_value("lag")
    item(at.selectbox, "查看资源").set_value("wind").run()
    item(at.selectbox, "查看机组").set_value("W1").run()
    assert not at.exception
    cfg = load_config(root=scene.scenario_path("默认"))
    expected = prepare_typical(cfg.root, bids=at.session_state["quote_current:默认"], season=cfg.base["season"],
                              wind_multiplier=.8, pv_multiplier=1., load_multiplier=1.,
                              error_scenarios={"load": "configured", "wind": "lag", "pv": "configured"})
    chart = figure(at)
    for line, stage in zip(chart["data"], ("actual", "da", "id", "rt")):
        np.testing.assert_array_equal(line["y"], expected["profiles"]["W1_" + stage])
    assert files(scene.SCEN_DIR) == before
