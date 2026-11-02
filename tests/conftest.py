import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(scope="session")
def da_result():
    from smm.bids import default_bids
    from smm.case import build_da_case
    from smm.config import load_config
    from smm.da import run_da
    from smm.forecast import generate_profiles
    from smm.network import build_network

    cfg = load_config()
    # Preserve the original energy-only regression contract when new markets default on.
    if "regulation" in cfg.mode:
        cfg.mode["regulation"]["enabled"] = False
    # 保留原始500 MW网络作为回归基准；新600 MW默认场景另做全天端到端校核。
    cfg.base.get("line_scale", {}).pop("L2-3", None)
    cfg.base["line_capacity_multiplier"] = 1.0
    cfg.mode["penalties"]["line"] = 1.0e5  # 原500MW/高罚回归基准；新默认另有完整96段验证。
    cfg.base["solver"]["mip_rel_gap"] = 0.01  # 测试放宽 gap 以缩短用时
    cfg.base["solver"]["time_limit_s"] = 150  # 测试限时（含抽蓄时 MIP 可能达不到 gap，取时限内可行解）
    net = build_network(cfg)
    prof = generate_profiles(cfg, cfg.base["seed"])
    case = build_da_case(cfg, net, prof, default_bids(cfg))
    return run_da(case, verbose=False)


def _short_run(da_result, periods=8, **storage):
    """缩短的实时滚动（默认：无日内，储能 B2 整点锚点，执行前 periods 个 15 min 时段）。storage 覆盖 realtime.storage。"""
    import copy
    from dataclasses import replace

    from smm import rolling, settlement
    from smm.full import checks

    cfg = da_result.case.cfg
    mode = copy.deepcopy(cfg.mode)
    mode["realtime"]["storage"].update(storage)
    cfg2 = replace(cfg, mode=mode)
    da2 = replace(da_result, case=replace(da_result.case, cfg=cfg2))
    rr = rolling.run_rolling(da2, periods=periods, log=lambda *a: None)
    rt = rolling.rt_tables(da2, rr)
    return dict(da=da2, rr=rr, rt=rt, id=rolling.intraday_tables(da2, rr),
                settle=settlement.settle(da2, rr), checks=checks(da2, rr, rt))


@pytest.fixture(scope="session")
def full_result(da_result):
    return _short_run(da_result)


@pytest.fixture(scope="session")
def fixed_da_result(da_result):
    return _short_run(da_result, periods=6, mode="fixed_da")


@pytest.fixture(scope="session")
def window_end_result(da_result):
    return _short_run(da_result, periods=6, anchor="window_end_pct")
