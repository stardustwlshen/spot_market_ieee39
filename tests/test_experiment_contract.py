"""Layered experiment card: saved facts, assumption marks, and explicit confirmation."""
from copy import deepcopy
from pathlib import Path
import sys

import yaml
import pytest

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


def _layer_labels(at):
    return {expander.label for expander in at.expander}


def test_layers_and_contract_confirmation_are_visible(scene):
    before = (scene.scenario_path("默认") / "config/modes.yaml").read_bytes()
    at = app()
    assert not at.exception
    assert any("日内市场：关闭" in item.value and "甘肃模式" in item.value for item in at.info)
    assert not {"研究开关（默认折叠）", "假设值一览", "资源层（按需）"} & _layer_labels(at)
    step(at, "2 机组")
    assert any(caption.value.startswith("已保存场景状态：") and "日内关闭" in caption.value for caption in at.caption)
    assert "资源层（按需）" not in _layer_labels(at)
    step(at, "3 报价")
    assert any(caption.value.startswith("已保存场景状态：") for caption in at.caption)
    assert "研究开关（默认折叠）" not in _layer_labels(at)
    step(at, "4 运行")
    assert not item(at.expander, "研究开关（默认折叠）").proto.expanded
    assert not item(at.expander, "假设值一览").proto.expanded
    assert not item(at.expander, "资源层（按需）").proto.expanded
    assert not any(box.label == "加载资源容量摘要" for box in at.checkbox)
    assert len(item(at.expander, "资源层（按需）").dataframe) >= 1
    assert any("将要生效的合约卡" in title.value for title in at.subheader)
    assert item(at.button, "开始单日出清").disabled
    confirm_contract(at)
    assert not item(at.button, "开始单日出清").disabled
    assert (scene.scenario_path("默认") / "config/modes.yaml").read_bytes() == before


def test_run_has_one_saved_summary_and_one_assumption_table(scene):
    at = step(app(), "4 运行")
    assert not at.exception
    assert len([notice for notice in at.info if "日内市场：关闭" in notice.value]) == 1
    assert len([expander for expander in at.expander if "假设值" in expander.label]) == 1
    assert not any("场景层 · 已保存" in value.value for value in at.markdown)
    # Optional inspection never becomes another setup task.
    for label in ("资源层（按需）", "研究开关（默认折叠）", "假设值一览", "高级设置"):
        assert not item(at.expander, label).proto.expanded
    confirm_contract(at)
    at.run()
    assert not item(at.button, "开始单日出清").disabled


@pytest.mark.parametrize("source", ["units", "base", "network", "profile", "costs"])
def test_confirmation_invalidates_after_saved_physical_input_edit(scene, source):
    root = scene.scenario_path("默认")
    if source == "profile":
        import shutil
        directory = root / "data/profiles"
        directory.mkdir(parents=True, exist_ok=True)
        profile = next((ROOT / "data/profiles").glob("*.csv"))
        shutil.copy2(profile, directory / profile.name)
    at = step(app(), "4 运行")
    confirm_contract(at)
    assert not item(at.button, "开始单日出清").disabled
    if source in ("units", "base"):
        path = root / "config" / f"{source}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if source == "units":
            data["storage"][0]["soc0"] = .55
        else:
            data["load_scale"] = float(data["load_scale"]) * .99
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    elif source in ("network", "profile"):
        import pandas as pd
        path = root / "data/case39/branches.csv" if source == "network" else next((root / "data/profiles").glob("*.csv"))
        data = pd.read_csv(path)
        column = "rate_mw" if source == "network" else next(c for c in data.columns if "load" in c.lower())
        data.loc[0, column] = float(data.loc[0, column]) * .99
        data.to_csv(path, index=False)
    else:
        path = root / "config/spot_market_costs.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["units"]["C1"]["fuel_cost_yuan_per_mwh"] += 1
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    after = path.read_bytes()
    at.run()
    assert not at.exception
    assert not item(at.checkbox, "已核对合约卡，确认按此启动").value
    assert item(at.button, "开始单日出清").disabled
    assert path.read_bytes() == after


def test_fingerprint_is_content_based_not_location_or_modification_time(scene, tmp_path):
    import shutil
    from smm.config import load_config
    root = scene.scenario_path("默认")
    cfg = load_config(root=root)
    draft = {"version": 1, "units": {}}
    first = input_fingerprint(cfg, draft)
    (root / "config/units.yaml").touch()
    assert input_fingerprint(cfg, draft) == first
    other = tmp_path / "copied-scene"
    shutil.copytree(root, other)
    assert input_fingerprint(load_config(root=other), draft) == first
