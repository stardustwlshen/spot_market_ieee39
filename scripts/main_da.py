"""日前出清主程序。

用法：
  python scripts/main_da.py                      # 统一规则 MKT、丰水期、最新版本报价
  python scripts/main_da.py --bid-version 1 --season dry
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config  # noqa: E402
from smm.da import run_da  # noqa: E402
from smm.pipeline import out_dir, prepare, print_summary, write_outputs  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--season", default=None)
ap.add_argument("--bid-version", type=int, default=None)
ap.add_argument("--time-limit", type=float, default=None)
ap.add_argument("--no-plots", action="store_true")
a = ap.parse_args()
cfg = load_config(season=a.season)
if a.time_limit:
    cfg.base["solver"]["time_limit_s"] = a.time_limit
case = prepare(cfg, a.bid_version)
res = run_da(case)
d = out_dir(cfg, case.bids["version"])
s = write_outputs(res, d, make_plots=not a.no_plots)
print_summary(s)
print("输出目录：", d)
