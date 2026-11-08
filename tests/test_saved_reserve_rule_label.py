"""The result labels must never migrate historical reserve inputs."""
import ast
from pathlib import Path

import numpy as np
import pytest

SOURCE = Path(__file__).parents[1] / "app/pages/3_结果.py"


def label(summary, da_summary=None, *, full=True, stage=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "saved_reserve_rule_label")
    scope = {"S": summary, "DS": da_summary or {}, "FULL": full, "np": np}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["saved_reserve_rule_label"](stage)


def test_full_fixed_uses_recorded_number_not_hardcoded_default():
    assert label({"reserve": {"up_requirement_mode": "fixed", "fixed_up_mw": 400}}) == "固定总上备用 400 MW"
    assert label({"reserve": {"up_requirement_mode": "fixed", "fixed_up_mw": 325.5}}) == "固定总上备用 325.5 MW"


def test_da_only_reads_its_frozen_summary():
    assert label({}, {"reserve_rule": {"up_requirement_mode": "fixed", "fixed_up_mw": 400}}, full=False) == "固定总上备用 400 MW"


def test_phase_specific_saved_rule_wins():
    summary = {"reserve": {"up_requirement_mode": "fixed", "fixed_up_mw": 400}}
    da = {"reserve_rule": {"up_requirement_mode": "formula"}}
    assert label(summary, da, stage="da").startswith("比例＋事故项公式")
    assert label(summary, da, stage="rt") == "固定总上备用 400 MW"


def test_legacy_missing_mode_is_formula_not_current_rule():
    assert label({"reserve": {"down_enabled": False}}).startswith("历史比例＋事故项公式")
    assert label({}, full=False).startswith("历史比例＋事故项公式")


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1, True, "400"])
def test_fixed_missing_or_bad_amount_is_not_assumed(value):
    text = label({"reserve": {"up_requirement_mode": "fixed", "fixed_up_mw": value}})
    assert "数值未记录" in text
    assert "400" not in text


def test_label_is_connected_to_rules_and_reserve_page_without_migration():
    source = SOURCE.read_text(encoding="utf-8")
    assert "parts.append(saved_reserve_rule_label())" in source
    assert "st.caption(saved_reserve_rule_label(k)" in source
    assert "for_new_run" not in source
