"""改报价演示：在 v1 基础上修改 CCGT 与 ES2 报价 → 校验 → 存为新版本 → 重新出清 → 对比电价与中标量。

演示内容（均为假设报价）：
  1) 先提交一份违规报价（CCGT 报价非单调递增），展示校验拦截；
  2) 修正后提交：CCGT 全部分段 +150 元/MWh；ES2 放电降为 360/380/400、充电升为 340/320/300；
  3) 两个版本分别出清，输出 LMP 与中标量对比 CSV/图。
用法：python scripts/demo_rebid.py [--base-version 1]
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pandas as pd  # noqa: E402

from smm import plots  # noqa: E402
from smm.bids import bids_to_csv, load_bids, raise_on_errors, save_bids, validate_bids  # noqa: E402
from smm.case import build_da_case  # noqa: E402
from smm.config import load_config  # noqa: E402
from smm.da import run_da  # noqa: E402
from smm.pipeline import out_dir, prepare, print_summary, write_outputs  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--base-version", type=int, default=1)
a = ap.parse_args()
cfg = load_config()

base_case = prepare(cfg, a.base_version)
base = base_case.bids

# 1) 违规报价
bad = copy.deepcopy(base)
segs = bad["units"]["CCGT"]["segments"]
segs[3]["price"], segs[4]["price"] = segs[4]["price"], segs[3]["price"]
print("\n[演示] 提交违规报价（CCGT 第4、5段价格对调）：")
for i in validate_bids(cfg, bad):
    print("   ", i)

# 2) 合规改报价
new = copy.deepcopy(base)
for s in new["units"]["CCGT"]["segments"]:
    s["price"] += 150
for s, p in zip(new["units"]["ES2"]["dis_segments"], [360, 380, 400]):
    s["price"] = p
for s, p in zip(new["units"]["ES2"]["ch_segments"], [340, 320, 300]):
    s["price"] = p
iss = validate_bids(cfg, new)
print("\n[演示] 修正后报价校验：", "通过" if not [i for i in iss if i.level == "error"] else iss)
raise_on_errors(iss)
p = save_bids(cfg, new, note=f"演示改报价：基于 v{base['version']}，CCGT +150；ES2 放电 360/380/400、充电 340/320/300")
new = json.loads(p.read_text(encoding="utf-8"))
bids_to_csv(new, p.with_suffix(".csv"))
print("已保存新版本：", p)

# 3) 两版分别出清
print("\n[出清] 改前 v%d" % base["version"])
r0 = run_da(base_case)
s0 = write_outputs(r0, out_dir(cfg, base["version"]))
print_summary(s0)
print("\n[出清] 改后 v%d" % new["version"])
case1 = build_da_case(cfg, base_case.net, base_case.profiles, new)
r1 = run_da(case1)
d1 = out_dir(cfg, new["version"])
s1 = write_outputs(r1, d1)
print_summary(s1)

# 对比
cmp_dir = cfg.root / "outputs" / f"rebid_v{base['version']}_vs_v{new['version']}"
cmp_dir.mkdir(parents=True, exist_ok=True)
p0, p1 = r0.tables["prices"], r1.tables["prices"]
pc = p0[["period", "time", "bus", "lmp"]].merge(p1[["period", "bus", "lmp"]], on=["period", "bus"], suffixes=("_before", "_after"))
pc["delta"] = pc.lmp_after - pc.lmp_before
pc.to_csv(cmp_dir / "lmp_compare.csv", index=False, float_format="%.3f")
u0, u1 = r0.tables["units"], r1.tables["units"]
uc = u0[["period", "time", "unit", "type", "P_mw", "lmp", "revenue_yuan"]].merge(
    u1[["period", "unit", "P_mw", "lmp", "revenue_yuan"]], on=["period", "unit"], suffixes=("_before", "_after"))
uc.to_csv(cmp_dir / "award_compare.csv", index=False, float_format="%.3f")
dt = cfg.dt
agg = uc.groupby("unit").agg(energy_before=("P_mw_before", lambda x: x.sum() * dt), energy_after=("P_mw_after", lambda x: x.sum() * dt),
                             revenue_before=("revenue_yuan_before", "sum"), revenue_after=("revenue_yuan_after", "sum"))
agg["energy_delta"] = agg.energy_after - agg.energy_before
agg["revenue_delta"] = agg.revenue_after - agg.revenue_before
agg.to_csv(cmp_dir / "award_compare_daily.csv", float_format="%.1f")
plots.rebid_compare(p0, p1, u0, u1, ["CCGT", "GT", "ES2"], cmp_dir / "rebid_compare.png", buses=[16, 35, 31],
                    title=f"改报价前后对比（v{base['version']} → v{new['version']}）", dt_h=cfg.dt)
summary = dict(before=dict((k, s0[k]) for k in ("objective", "lmp_min", "lmp_max", "lmp_load_weighted_avg")),
               after=dict((k, s1[k]) for k in ("objective", "lmp_min", "lmp_max", "lmp_load_weighted_avg")))
(cmp_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
print("\n日电量/收入变化（MWh / 元）：")
print(agg.round(1).to_string())
from smm.pipeline import format_optional_price
print(f"\n负荷加权均价：{format_optional_price(s0['lmp_load_weighted_avg'])} → {format_optional_price(s1['lmp_load_weighted_avg'])} 元/MWh")
print("对比输出：", cmp_dir)
