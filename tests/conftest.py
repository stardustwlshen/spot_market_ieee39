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
