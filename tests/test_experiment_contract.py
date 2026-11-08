"""Layered experiment card: saved facts, assumption marks, and explicit confirmation."""
from copy import deepcopy
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from experiment_contract import (assumption_rows, contract_rows, input_fingerprint, research_rows,
                                 scenario_facts)
from test_single_day_workspace import app, confirm_contract, item, scene, step


def saved_config():
    from types import SimpleNamespace
    base = yaml.safe_load((ROOT / "config/base.yaml").read_text(encoding="utf-8"))
    modes = yaml.safe_load((ROOT / "config/modes.yaml").read_text(encoding="utf-8"))
    return SimpleNamespace(base=base, mode=modes["MKT"], T=base["periods"], dt=base["dt_h"])


def test_default_intraday_stays_off_and_assumptions_are_marked():
    text = (ROOT / "config/modes.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in text
    cfg = saved_config()
    facts = scenario_facts(cfg)
    assert facts["intraday_enabled"] is False
    assert "关闭" in facts["intraday_label"]
    assert "甘肃模式" in facts["intraday_note"]
    rows = assumption_rows(cfg)
    assert rows and all(row["标记"] == "假设值" for row in rows)
    penalty = next(row for row in rows if row["项目"].startswith("软日末罚价"))
    assert penalty["当前值"] == "1000"
    reserve = next(row for row in rows if row["项目"].startswith("上备用固定总量"))
    assert reserve["当前值"] == "400"
    switches = {row["项目"]: row["当前值"] for row in research_rows(cfg)}
    assert switches["日内市场"] == "关闭"
    assert switches["GT 实时策略"] == "inherit"
    assert switches["价值法 · 储能/抽蓄"] == "value"


def test_fingerprint_tracks_saved_intraday_and_quote_without_writing():
    original = (ROOT / "config/modes.yaml").read_bytes()
    cfg = saved_config()
    draft = {"version": 3, "units": {"C1": {"noload_cost": 1}}}
    first = input_fingerprint(cfg, draft)
    assert input_fingerprint(cfg, draft) == first
    opened = deepcopy(cfg.mode)
    opened["intraday"] = {**opened["intraday"], "enabled": True}
    changed = type("Cfg", (), {})()
    changed.base, changed.mode, changed.T, changed.dt = cfg.base, opened, cfg.T, cfg.dt
    assert scenario_facts(changed)["intraday_enabled"] is True
    assert input_fingerprint(changed, draft) != first
    assert input_fingerprint(cfg, {"version": 4, "units": draft["units"]}) != first
    rows, fingerprint = contract_rows(cfg, draft, {"basis_label": "测试来源"}, mode="full", periods=None)
    assert fingerprint == first
    card = {row["项"]: row["内容"] for row in rows}
    assert "关闭" in card["日内市场"] and "甘肃模式" in card["日内市场"]
    assert "inherit" in card["GT 策略"]
    assert "假设值" in card["价值法 / 软日末"]
    assert "版本 3" in card["报价包"] and "测试来源" in card["报价包"]
    assert (ROOT / "config/modes.yaml").read_bytes() == original


def test_layers_and_contract_confirmation_are_visible(scene):
    before = (scene.scenario_path("默认") / "config/modes.yaml").read_bytes()
    at = app()
    assert not at.exception
    assert any("日内市场：关闭" in item.value and "甘肃模式" in item.value for item in at.info)
    assert not item(at.expander, "研究开关（默认折叠）").proto.expanded
    assert not item(at.expander, "假设值一览").proto.expanded
    assert not item(at.expander, "资源层（按需）").proto.expanded
    step(at, "4 运行")
    assert any("将要生效的合约卡" in title.value for title in at.subheader)
    assert item(at.button, "开始单日出清").disabled
    confirm_contract(at)
    assert not item(at.button, "开始单日出清").disabled
    assert (scene.scenario_path("默认") / "config/modes.yaml").read_bytes() == before
