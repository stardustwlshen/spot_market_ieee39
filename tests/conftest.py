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
    cfg.base["solver"]["mip_rel_gap"] = 0.01  # 测试放宽 gap 以缩短用时
    net = build_network(cfg)
    prof = generate_profiles(cfg, cfg.base["seed"])
    case = build_da_case(cfg, net, prof, default_bids(cfg))
    return run_da(case, verbose=False)


@pytest.fixture(scope="session")
def full_result(da_result):
    """缩短的滚动：日内每 1 h 一轮、只执行前 8 个时段（2 轮日内 + 8 次实时）。"""
    from smm import rolling, settlement
    from smm.full import checks

    cfg = da_result.case.cfg
    cfg.mode["intraday"]["interval_h"] = 1
    rr = rolling.run_rolling(da_result, periods=8, log=lambda *a: None)
    rt = rolling.rt_tables(da_result, rr)
    return dict(da=da_result, rr=rr, rt=rt, id=rolling.intraday_tables(da_result, rr),
                settle=settlement.settle(da_result, rr), checks=checks(da_result, rr, rt))
