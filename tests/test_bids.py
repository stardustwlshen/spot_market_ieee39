"""报价规则校验与网络数据检查（快速）。"""
import copy

import numpy as np
import pytest

from smm.bids import default_bids, validate_bids
from smm.config import load_config
from smm.network import build_network


@pytest.mark.parametrize("rs", ["SC", "GS"])
def test_default_bids_valid(rs):
    cfg = load_config(rs)
    assert not [i for i in validate_bids(cfg, default_bids(cfg)) if i.level == "error"]


def _errs(cfg, b):
    return [i.rule for i in validate_bids(cfg, b) if i.level == "error"]


def test_sc_rules_catch_errors():
    cfg = load_config("SC")
    b0 = default_bids(cfg)
    b = copy.deepcopy(b0)
    s = b["units"]["CCGT"]["segments"]
    s[3]["price"], s[4]["price"] = s[4]["price"], s[3]["price"]
    assert _errs(cfg, b)                       # 非单调
    b = copy.deepcopy(b0)
    b["units"]["C1"]["segments"][-1]["price"] = 900
    assert _errs(cfg, b)                       # 超报价上限
    b = copy.deepcopy(b0)
    b["units"]["ES1"]["dis_segments"][0]["price"] = 250
    assert _errs(cfg, b)                       # 放电最低价 ≤ 充电最高价
    b = copy.deepcopy(b0)
    b["units"]["C2"]["startup_cost"]["hot"] *= 2
    assert _errs(cfg, b)                       # 启动费超上限


def test_gs_rules_catch_errors():
    cfg = load_config("GS")
    b = default_bids(cfg)
    b["units"]["C1"]["segments"][-1]["price"] += 5   # 不是 10 元步长
    assert _errs(cfg, b)


def test_ptdf_reference_column_zero():
    cfg = load_config()
    net = build_network(cfg)
    assert np.allclose(net.ptdf[:, net.bus_index(net.ref_bus)], 0)
    assert net.ptdf.shape == (46, 39)
