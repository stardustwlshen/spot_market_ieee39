"""日前出清主程序。

用法：
  python scripts/main_da.py                      # 统一规则 MKT、丰水期、最新版本报价
  python scripts/main_da.py --bid-version 1 --season dry
"""
import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config  # noqa: E402
from smm.da import run_da  # noqa: E402
from smm.pipeline import prepare, print_summary, write_outputs  # noqa: E402
from smm.run_input_provenance import freeze_cli_inputs  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--season", default=None)
ap.add_argument("--bid-version", type=int, default=None)
ap.add_argument("--time-limit", type=float, default=None)
ap.add_argument("--no-plots", action="store_true")
ap.add_argument("--tag", default="", help="新结果目录后缀；同名历史结果不覆盖，例如 _review2")
a = ap.parse_args()
if not re.fullmatch(r"[\w.-]*", a.tag):
    ap.error("--tag 只能含中文、字母、数字、下划线、点或短横线，不能包含路径分隔符")
if a.time_limit is not None and (not math.isfinite(a.time_limit) or a.time_limit <= 0):
    ap.error("--time-limit 必须为有限正数")
from smm.reserve_policy import for_new_run
cfg = for_new_run(load_config(season=a.season))
if a.time_limit is not None:
    cfg.base["solver"]["time_limit_s"] = a.time_limit
case = prepare(cfg, a.bid_version, read_only=True)
d = cfg.root / "outputs" / f"da_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{case.bids['version']}{a.tag}"
if d.exists():
    raise FileExistsError(f"输出目录已存在，历史结果未覆盖：{d}；请用 --tag 指定新的运行后缀")
# Exclusive creation and effective-input snapshot precede the expensive solve.
freeze_cli_inputs(cfg, case, d)
res = run_da(case)
s = write_outputs(res, d, make_plots=not a.no_plots)
print_summary(s)
print("输出目录：", d)
