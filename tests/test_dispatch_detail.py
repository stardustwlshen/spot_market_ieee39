"""Dispatch detail filtering and exports are presentation-only and stage-aware."""
from io import StringIO
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from chinese_labels import display_frame
from dispatch_detail import (dispatch_csv_bytes, dispatch_display_frame, dispatch_options,
                             dispatch_plan_frame, filter_dispatch_plan)


def sample():
    return pd.DataFrame([
        {"period": p, "time": f"00:{p * 15:02d}", "unit": unit, "type": typ,
         "bus": bus, "P_mw": power, "energy_mwh": power * .25}
        for p in range(4)
        for unit, typ, bus, power in (("C1", "coal", 31, 100.), ("H1", "hydro", 20, 50.),
                                      ("ES1", "storage", 3, -20.))
    ])


def test_defaults_show_all_and_filters_combine_without_mutating_source():
    source = sample()
    before = source.copy(deep=True)
    frame = dispatch_plan_frame(source)
    pd.testing.assert_frame_equal(filter_dispatch_plan(frame), source)
    filtered = filter_dispatch_plan(frame, units=["H1", "C1"], resource_types=["hydro"], period_range=(1, 2))
    assert filtered.unit.tolist() == ["H1", "H1"]
    assert filtered.period.tolist() == [1, 2]
    pd.testing.assert_frame_equal(source, before)
    assert filter_dispatch_plan(frame, units=["C1"], resource_types=["hydro"]).empty


@pytest.mark.parametrize("header_stage", [None, "日前", "实时"])
def test_chinese_and_english_headers_preserve_energy(header_stage):
    source = sample()
    source.loc[0, "energy_mwh"] = 99.  # Recorded quantity must never be recomputed.
    source.loc[1, "energy_mwh"] = np.nan
    translated = display_frame(source) if header_stage is None else dispatch_display_frame(source, header_stage)
    result = dispatch_plan_frame(translated)
    assert result.energy_mwh.iloc[0] == 99.
    assert pd.isna(result.energy_mwh.iloc[1])
    assert result.P_mw.tolist() == source.P_mw.tolist()
    assert filter_dispatch_plan(result, resource_types=["储能"]).unit.unique().tolist() == ["ES1"]
    assert "energy_mwh" not in dispatch_display_frame(result, "日前")
    assert "日前中标电量 MWh" in dispatch_display_frame(result, "日前")


def test_missing_energy_uses_signed_power_not_price_component():
    source = pd.DataFrame({"unit": ["ES1", "PS1"], "P_mw": [-40., 80.], "energy": [800., 900.]})
    result = dispatch_plan_frame(source)
    assert result.energy_mwh.tolist() == [-10., 20.]
    assert "energy" not in result
    assert "energy_mwh" not in source


def test_missing_fields_empty_and_incomplete_rows_stay_unknown():
    assert dispatch_plan_frame(None).empty
    assert dispatch_plan_frame(pd.DataFrame()).empty
    frame = dispatch_plan_frame(pd.DataFrame({"unit": ["H1", None]}))
    assert frame.energy_mwh.isna().all()
    assert len(filter_dispatch_plan(frame)) == 2
    assert dispatch_options(frame) == {"unit": ["H1"], "type": [], "period": []}
    assert filter_dispatch_plan(frame, period_range=(0, 1)).empty
    assert filter_dispatch_plan(frame, resource_types=["hydro"]).empty
    partial = dispatch_plan_frame(pd.DataFrame({"period": [0, np.nan, -1, .5, "bad"], "P_mw": [4.] * 5}))
    assert dispatch_options(partial)["period"] == [0]
    assert len(filter_dispatch_plan(partial)) == 5
    assert filter_dispatch_plan(partial, period_range=(0, 0)).period.tolist() == [0]


@pytest.mark.parametrize("stage,label", [("日前", "日前中标电量 MWh"), ("实时", "实时执行电量 MWh")])
def test_downloads_match_filtered_rows_and_identify_stage(stage, label):
    frame = filter_dispatch_plan(dispatch_plan_frame(sample()), units=["ES1"], period_range=(1, 2))
    raw = pd.read_csv(StringIO(dispatch_csv_bytes(frame, stage, chinese=False).decode("utf-8-sig")))
    chinese = pd.read_csv(StringIO(dispatch_csv_bytes(frame, stage).decode("utf-8-sig")))
    pd.testing.assert_frame_equal(raw, frame.reset_index(drop=True))
    pd.testing.assert_frame_equal(chinese, dispatch_display_frame(frame, stage).reset_index(drop=True))
    assert chinese[label].tolist() == [-5., -5.]
    assert len(raw) == len(chinese) == 2


def render(rows=None):
    rows = sample().to_dict("records") if rows is None else rows
    return AppTest.from_string(
        f"import sys\nsys.path.insert(0, {str(ROOT / 'app')!r})\n"
        "import pandas as pd\nimport streamlit as st\n"
        "from dispatch_detail import dispatch_detail_table\n"
        f"data = pd.DataFrame(st.session_state.get('test_rows', {rows!r}))\n"
        "stage = st.radio('测试阶段', ['日前', '实时'])\n"
        "result = st.selectbox('测试结果', ['运行甲', '运行乙'])\n"
        "if stage == '实时':\n"
        "    if 'period' in data: data = data[data.period < 2]\n"
        "    data = data.drop(columns=['energy_mwh', 'time'], errors='ignore')\n"
        "filtered = dispatch_detail_table(data, stage, result_key=result)\n"
        "st.session_state['filtered_rows'] = filtered\n",
        default_timeout=20,
    ).run()


def test_ui_search_multiselect_time_range_empty_and_reset():
    at = render()
    assert not at.exception
    assert len(at.dataframe[0].value) == 12
    assert at.multiselect[0].value == at.multiselect[1].value == []
    at.multiselect[0].set_value(["H1", "ES1"]).run()
    at.multiselect[1].set_value(["storage"]).run()
    at.select_slider[0].set_value((1, 2)).run()
    assert not at.exception
    shown = at.dataframe[0].value
    assert shown["主体"].tolist() == ["ES1", "ES1"]
    assert shown["时段序号"].tolist() == [1, 2]
    assert any("2 / 12 条" in c.value for c in at.caption)
    assert all("当前筛选结果" in b.proto.label for b in at.get("download_button"))

    at.multiselect[0].set_value(["C1"]).run()
    assert not at.exception
    assert at.dataframe[0].value.empty
    assert any("没有符合条件" in i.value for i in at.info)
    assert all(b.proto.disabled for b in at.get("download_button"))
    at.button[0].click().run()
    assert not at.exception
    assert len(at.dataframe[0].value) == 12
    assert at.multiselect[0].value == at.multiselect[1].value == []
    assert at.select_slider[0].value == (0, 3)
    # Repeated reset is safe and stays on the same result.
    at.button[0].click().run()
    assert not at.exception and len(at.dataframe[0].value) == 12


def test_ui_switch_result_and_realtime_never_inherits_unavailable_periods():
    at = render()
    at.multiselect[0].set_value(["ES1"]).run()
    at.select_slider[0].set_value((2, 3)).run()
    at.radio[0].set_value("实时").run()
    assert not at.exception
    shown = at.dataframe[0].value
    assert len(shown) == 6
    assert shown["时段序号"].max() == 1
    assert "实时执行电量 MWh" in shown
    assert "日前中标电量 MWh" not in shown
    assert at.select_slider[0].value == (0, 1)
    assert any("已执行时段" in c.value for c in at.caption)
    at.multiselect[0].set_value(["H1"]).run()
    at.selectbox[0].set_value("运行乙").run()
    assert not at.exception and len(at.dataframe[0].value) == 6
    assert at.multiselect[0].value == []
    at.radio[0].set_value("日前").run()
    assert not at.exception and len(at.dataframe[0].value) == 12


@pytest.mark.parametrize("rows", [[], [{"unit": "H1"}], [{"period": 0, "unit": "H1", "P_mw": 8.}]])
def test_ui_missing_columns_empty_and_single_period(rows):
    at = render(rows)
    assert not at.exception
    if not rows:
        assert not at.dataframe and not at.get("download_button")
        assert any("没有逐主体出力记录" in i.value for i in at.info)
    elif "period" not in rows[0]:
        assert at.dataframe[0].value["日前中标电量 MWh"].isna().all()
        assert not at.select_slider
        assert at.multiselect[1].disabled
    else:
        assert at.select_slider[0].disabled
        assert at.dataframe[0].value["日前中标电量 MWh"].tolist() == [2.]


def test_ui_refresh_changed_options_in_same_result_resets_stale_filters():
    at = render()
    at.multiselect[0].set_value(["ES1"]).run()
    at.select_slider[0].set_value((2, 3)).run()
    at.session_state["test_rows"] = [{"period": 0, "unit": "C9", "type": "coal", "P_mw": 12.}]
    at.run()
    assert not at.exception
    assert at.multiselect[0].value == []
    assert at.select_slider[0].value == (0, 0)
    assert at.dataframe[0].value["主体"].tolist() == ["C9"]
    at.button[0].click().run()
    assert not at.exception
    assert at.dataframe[0].value["日前中标电量 MWh"].tolist() == [3.]
