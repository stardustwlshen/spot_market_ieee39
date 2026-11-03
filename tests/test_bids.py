"""统一规则 MKT 的报价校验与网络数据检查（快速）。"""
import copy

import numpy as np

from smm.bids import default_bids, validate_bids
from smm.config import load_config
from smm.network import build_network


def test_single_rule_set():
    cfg = load_config()
    assert cfg.rule_set == "MKT"
    import yaml
    modes = yaml.safe_load((cfg.root / "config" / "modes.yaml").read_text(encoding="utf-8"))
    assert list(modes) == ["MKT"]          # 只保留一套统一规则


def test_default_bids_valid():
    cfg = load_config()
    assert not [i for i in validate_bids(cfg, default_bids(cfg)) if i.level == "error"]


def _errs(cfg, b):
    return [i.rule for i in validate_bids(cfg, b) if i.level == "error"]


def test_rules_catch_errors():
    cfg = load_config()
    cfg.mode["startup_cost_mode"] = "state_dependent"  # legacy per-state cap contract
    # Preserve regression coverage of the legacy research step/strictness rules.
    cfg.mode["bid_rules"] = {"profile": "generic"}
    b0 = default_bids(cfg)
    b = copy.deepcopy(b0)
    s = b["units"]["CCGT"]["segments"]
    s[3]["price"], s[4]["price"] = s[4]["price"], s[3]["price"]
    assert "价格严格单调" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    b["units"]["C1"]["segments"][-1]["price"] = 900
    assert "申报限价" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    b["units"]["ES1"]["dis_segments"][0]["price"] = 250
    assert "放电首段>充电末段" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    b["units"]["C2"]["startup_cost"]["hot"] *= 2
    assert "启动费上限" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    b["units"]["C1"]["segments"][-1]["price"] += 5
    assert "价格步长" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    b["units"]["C1"]["segments"][-1]["price"] += 200
    assert "相邻段价差" in _errs(cfg, b)
    b = copy.deepcopy(b0)
    seg = b["units"]["W1"]["segments"]
    seg[0]["p_end"] = seg[1]["p_start"] = 50.0                # 首段 < 10% 装机
    assert "段长下限" in _errs(cfg, b)


def test_ptdf_reference_column_zero():
    cfg = load_config()
    net = build_network(cfg)
    assert np.allclose(net.ptdf[:, net.bus_index(net.ref_bus)], 0)
    assert net.ptdf.shape == (46, 39)


def test_storage_binary_default_on():
    cfg = load_config()
    assert cfg.base["solver"]["storage_binary"] is True
