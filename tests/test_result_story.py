"""Result narrative reads saved evidence and degrades when old archives omit fields."""
from pathlib import Path
import json
import sys

import pandas as pd
import yaml
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from result_story import (credibility_summary, curtail_series, export_payload, narrative_tracks,
                          reserve_shortage, state_series, terminal_deviations, top_issues)


def _result(**extra):
    result = {"summary": {}, "full": False, "dir": None}
    result.update(extra)
    return result


def test_old_result_without_new_fields_stays_unknown():
    summary = credibility_summary(_result())
    assert summary["periods"]["label"] == "否"
    assert "仅日前" in summary["periods"]["detail"]
    assert summary["reserve"]["label"] == "未记录"
    assert summary["terminal"]["headline"] == "未记录"
    assert "储能 未记录" in summary["terminal"]["detail"]
    assert "抽蓄 未记录" in summary["terminal"]["detail"]
    assert "水电 未记录" in summary["terminal"]["detail"]
    assert top_issues(_result()).empty
    payload, name = export_payload(_result(), code_sha="abc1234", input_fingerprint="deadbeef")
    assert name == "credibility_abc1234_deadbeef.json"
    assert payload["assumptions"][0]["当前值"] == "未记录"
    assert payload["top_issues"] == []


def test_full_day_reserve_and_terminal_are_separate_and_ranked():
    reserve = pd.DataFrame({
        "period": [0, 1, 2],
        "R_up_req": [400., 400., 400.],
        "R_up_supply": [400., 390., 400.],
        "R_up_short": [0., 10., 0.],
    })
    storage_da = pd.DataFrame({"period": [0, 1], "unit": ["ES1", "ES1"], "E_mwh": [10., 10.]})
    storage_rt = pd.DataFrame({"period": [0, 1], "unit": ["ES1", "ES1"], "E_mwh": [10., 7.]})
    windows = pd.DataFrame({
        "window_start": [0, 0],
        "unit": ["ES1", "GT"],
        "kind": ["E", "E"],
        "reference_source": ["intraday", "day_ahead"],
        "reference_fallback_reason": ["id_infeasible", None],
    })
    result = _result(
        full=True,
        summary={
            "expected_periods": 96, "executed_periods": 96, "completed_scope": "full_day",
            "realtime": {"executed_terminal_deviation_records": [
                {"kind": "E", "unit": "ES1", "deviation_native": 1.0, "deviation_discharge_mwh": 1.0, "penalty_cost": 1000.0},
                {"kind": "Vu", "unit": "PS1", "deviation_native": 0.0, "deviation_discharge_mwh": 0.0, "penalty_cost": 0.0},
                {"kind": "V", "unit": "H1", "deviation_native": 2.0, "deviation_discharge_mwh": 4.0, "penalty_cost": 4000.0},
            ]},
        },
        rt_reserve_system=reserve,
        da_storage=storage_da,
        rt_storage=storage_rt,
        rt_value_windows=windows,
    )
    summary = credibility_summary(result)
    assert summary["periods"]["done_96"] is True
    assert summary["periods"]["label"] == "是"
    shortage = reserve_shortage(result)
    assert shortage["max_mw"] == 10
    assert shortage["worst_period"] == 1
    terminal = terminal_deviations(result)
    assert terminal["headline"] == "有偏差"
    assert "储能 ES1 1 可放电MWh" in terminal["detail"]
    assert "抽蓄 PS1 0 可放电MWh" in terminal["detail"]
    assert "水电 H1 4 可放电MWh" in terminal["detail"]
    issues = top_issues(result)
    reserve_rows = issues[issues["类别"].eq("备用缺口")]
    assert int(reserve_rows.iloc[0]["时段"]) == 1
    assert reserve_rows.iloc[0]["绝对值"] >= reserve_rows.iloc[-1]["绝对值"]
    state = issues[issues["类别"].eq("SOC/库容偏离")]
    assert "ES1" in state.iloc[0]["对象"]
    assert float(state.iloc[0]["差值"]) == -3
    handoff = issues[issues["类别"].eq("交接回退")]
    assert handoff.iloc[0]["对象"] == "id_infeasible"
    assert "价差" not in set(issues["类别"])
    assert "弃电" not in set(issues["类别"])


def test_saved_blank_fallback_is_zero_not_missing():
    result = _result(full=True, rt_value_windows=pd.DataFrame({
        "window_start": [0], "reference_fallback_reason": [None],
    }))
    issues = top_issues(result)
    row = issues[issues["类别"].eq("交接回退")].iloc[0]
    assert row["对象"] == "未发现回退"
    assert row["差值"] == 0
    unknown = top_issues(_result(full=True))
    assert unknown.empty


def test_tracks_keep_price_and_curtailment_and_list_only_saved_states():
    empty = narrative_tracks(_result(), curtailment={"stages": {}})
    assert [track["label"] for track in empty] == ["负荷加权电价", "弃电"]
    assert all(track["frame"] is None for track in empty)
    curtailment = {"stages": {"日前": {"detail": pd.DataFrame({
        "period": [0, 1], "curtail_mwh": [1.5, 0.0], "valid": [True, True],
    })}}}
    series = curtail_series(curtailment)
    assert list(series.columns) == ["日前"]
    assert "实时" not in series.columns
    assert series.loc[0, "日前"] == 1.5
    stored = _result(
        full=True,
        da_storage=pd.DataFrame({"period": [0], "unit": ["ES1"], "E_mwh": [4.0]}),
        rt_storage=pd.DataFrame({"period": [0], "unit": ["ES1"], "E_mwh": [3.0]}),
        da_hydro=pd.DataFrame({"period": [0], "station": ["H1"], "V_1e4m3": [float("nan")]}),
    )
    labels = [track["label"] for track in narrative_tracks(stored)]
    assert labels == ["负荷加权电价", "弃电", "储能电量"]
    state = state_series(stored, "storage")
    assert set(state["stage"]) == {"日前", "实时"}
    assert state_series(stored, "hydro") is None
    assert state_series(stored, "pumped") is None


def test_overview_kpis_stay_outside_the_stage_block():
    import ast
    tree = ast.parse((ROOT / "app/pages/3_结果.py").read_text(encoding="utf-8"))
    overview = next(node for node in tree.body if isinstance(node, ast.With)
                    and ast.unparse(node.items[0].context_expr) == "main_tabs[0]")
    text = ast.unparse(overview)
    assert "_render_overview_kpis()" in text
    stage = next(node for node in ast.walk(overview) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == "st.expander" and node.args
                 and ast.unparse(node.args[0]) == "'阶段结果与主体经济'")
    expanded = {keyword.arg: ast.unparse(keyword.value) for keyword in stage.keywords}
    assert expanded.get("expanded") == "True"
    saved = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_render_saved_overview")
    assert "_render_overview_kpis" not in ast.unparse(saved)
    assert "overview_stage" in ast.unparse(saved)


def test_prefix_is_not_called_a_finished_96():
    summary = credibility_summary(_result(full=True, summary={"expected_periods": 96, "executed_periods": 8, "completed_scope": "debug_prefix"}))
    assert summary["periods"]["label"] == "否"
    assert summary["periods"]["done_96"] is False


def test_story_widgets_render_old_and_partial_archives(tmp_path):
    modes = yaml.safe_load((ROOT / "config/modes.yaml").read_text(encoding="utf-8"))
    snapshot = tmp_path / "scenario_snapshot" / "config"
    snapshot.mkdir(parents=True)
    (snapshot / "modes.yaml").write_text(yaml.safe_dump(modes, allow_unicode=True), encoding="utf-8")
    (snapshot / "base.yaml").write_text((ROOT / "config/base.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    rich = {
        "summary": {"expected_periods": 96, "executed_periods": 96, "completed_scope": "full_day"},
        "full": True, "dir": str(tmp_path), "snapshot_root": str(tmp_path / "scenario_snapshot"),
        "rt_reserve_system": pd.DataFrame({"period": [0], "R_up_req": [400.], "R_up_supply": [400.], "R_up_short": [0.]}),
        "rt_value_windows": pd.DataFrame({
            "window_start": [95], "tau": [95], "unit": ["H1"], "kind": ["V"], "label": ["day_end"],
            "value_native": [-3.0], "planned_terminal_soft": [True], "deviation_discharge_mwh": [0.5],
            "terminal_penalty_cost": [500.0], "reference_source": ["day_ahead"], "reference_fallback_reason": [None],
        }),
    }
    script = f"""
import sys
sys.path.insert(0, {str(ROOT / 'app')!r})
import pandas as pd
from result_story_view import render_credibility, render_difference_narrative, render_mechanism_audit, render_experiment_matrix_guide
empty = {{'summary': {{}}, 'full': False, 'dir': {str(tmp_path / 'old')!r}}}
render_credibility(empty)
render_difference_narrative(empty, buses=None, labels={{}}, curtailment={{'stages': {{}}}})
render_mechanism_audit(empty, labels={{}})
rich = {{
    'summary': {{'expected_periods': 96, 'executed_periods': 96, 'completed_scope': 'full_day'}},
    'full': True, 'dir': {str(tmp_path)!r}, 'snapshot_root': {str(tmp_path / 'scenario_snapshot')!r},
    'da_storage': pd.DataFrame({{'period': [0], 'unit': ['ES1'], 'E_mwh': [4.]}}),
    'rt_storage': pd.DataFrame({{'period': [0], 'unit': ['ES1'], 'E_mwh': [3.]}}),
    'rt_reserve_system': pd.DataFrame({{'period': [0], 'R_up_req': [400.], 'R_up_supply': [400.], 'R_up_short': [0.]}}),
    'rt_value_windows': pd.DataFrame({{
        'window_start': [95], 'tau': [95], 'unit': ['H1'], 'kind': ['V'], 'label': ['day_end'],
        'value_native': [-3.0], 'planned_terminal_soft': [True], 'deviation_discharge_mwh': [0.5],
        'terminal_penalty_cost': [500.0], 'reference_source': ['day_ahead'], 'reference_fallback_reason': [None],
    }}),
}}
render_credibility(rich)
render_difference_narrative(rich, buses=None, labels={{0: '00:00'}}, curtailment={{'stages': {{}}}})
render_mechanism_audit(rich, labels={{}})
render_experiment_matrix_guide()
"""
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception, [item.message for item in at.exception]
    labels = [metric.label for metric in at.metric]
    assert "96段是否跑完" in labels
    assert "上备用缺额" in labels
    assert "软日末偏差" in labels
    assert not any("间隙" in label for label in labels)
    assert any("差异叙事" in item.value for item in at.subheader)
    assert not any("机制审计" in item.value for item in at.subheader)
    tracks = [radio for radio in at.radio if radio.label == "关键轨迹"]
    assert [list(radio.options) for radio in tracks] == [
        ["负荷加权电价", "弃电"],
        ["负荷加权电价", "弃电", "储能电量"],
    ]
    at.radio(key="story_track:" + str(tmp_path / "old")).set_value("弃电").run()
    assert not at.exception
    assert any("弃电未记录" in item.value for item in at.info)
    audit = next(item for item in at.expander if item.label == "机制审计")
    assert audit.proto.expanded is False
    guide = next(item for item in at.expander if item.label.startswith("实验矩阵取向"))
    assert "丰水 · 日内关" in guide.markdown[0].value
    assert not at.get("download_button")
    payload, name = export_payload(rich, code_sha="abc", input_fingerprint="fff")
    assert name.startswith("credibility_abc_")
    assert any(row["标记"] == "假设值" and row["当前值"] == "1000" for row in payload["assumptions"])
    json.dumps(payload)
