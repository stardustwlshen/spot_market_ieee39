"""一键全流程：日前出清 →（可选日内）→ 实时 15 min 滚动 SCED → 两级偏差结算。

用法：
  python scripts/main_full.py                       # 统一规则 MKT、丰水期、最新版本报价、全天 96 时段
  python scripts/main_full.py --storage-mode fixed_da          # 储能执行日前曲线（模式 A）
  python scripts/main_full.py --anchor window_end_pct          # 甘肃式窗口末 ±x（模式 B1；默认为 B2 研究整点 SOC 软跟踪）
  python scripts/main_full.py --intraday --id-interval-h 2     # 打开独立日内滚动（对比用）
  python scripts/main_full.py --periods 24 --no-plots   # 只滚动前 6 h（调试）
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config  # noqa: E402
from smm.full import run_full, write_full_outputs  # noqa: E402
from smm.pipeline import prepare  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--bid-version", type=int, default=None)
ap.add_argument("--season", default=None)
ap.add_argument("--periods", type=int, default=None, help="只滚动前 N 个时段（默认 96）")
ap.add_argument("--id-interval-h", type=float, default=None, help="日内滚动周期（h），默认见 modes.yaml")
ap.add_argument("--rt-lookahead-h", type=float, default=None, help="实时前瞻窗口（h），默认 2")
ap.add_argument("--storage-mode", choices=["sced_anchor", "fixed_da"], default=None, help="储能实时模式，默认 sced_anchor")
ap.add_argument("--anchor", choices=["window_end_pct", "hourly_eq"], default=None, help="SOC 锚点：hourly_eq 广东整点相等（默认）/ window_end_pct 甘肃窗口末±x")
ap.add_argument("--anchor-x", type=float, default=None, help="甘肃锚点浮动比例 x（假设值 0.05）")
ap.add_argument("--intraday", action="store_true", help="打开独立日内滚动（默认关闭）")
ap.add_argument("--no-plots", action="store_true")
ap.add_argument("--tag", default="", help="输出目录后缀（对比方案用，如 _fixed_da）")
a = ap.parse_args()

cfg = load_config(season=a.season)
if a.id_interval_h:
    cfg.mode["intraday"]["interval_h"] = a.id_interval_h
if a.rt_lookahead_h:
    cfg.mode["realtime"]["lookahead_h"] = a.rt_lookahead_h
sto = cfg.mode["realtime"]["storage"]
if a.storage_mode:
    sto["mode"] = a.storage_mode
if a.anchor:
    sto["anchor"] = a.anchor
if a.anchor_x is not None:
    sto["x"] = a.anchor_x
if a.intraday:
    cfg.mode["intraday"]["enabled"] = True
print(f"开始 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
case = prepare(cfg, a.bid_version)
res = run_full(cfg, case, periods=a.periods)
v = case.bids["version"]
d = cfg.root / "outputs" / f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{v}{a.tag}"
s = write_full_outputs(res, d, make_plots=not a.no_plots)
sp, ch, ts = s["spread"], s["checks"], s["time_s"]
print("=" * 70)
print(f"全流程 {s['date']} {s['rule_name']} {s['season']}  日内 {s['intraday']['rounds']} 轮 / 实时 {s['periods']} 时段")
print(f"  负荷加权均价  日前 {sp['da_lw_avg']:.2f}  实时 {sp['rt_lw_avg']:.2f}  价差均值 {sp['lw_spread_mean']:+.2f}"
      f"（|价差|均值 {sp['lw_spread_abs_mean']:.2f}，范围 {sp['lw_spread_min']:+.1f}~{sp['lw_spread_max']:+.1f}）")
print(f"  节点价差 |RT−DA| 最大 {sp['nodal_spread_abs_max']:.1f}，RMS {sp['nodal_spread_rms']:.1f}")
print("  结算（万元，+收入/−支出）：")
for k, r in s["settlement_by_type"].items():
    print(f"    {k:12s} 日前 {r['da_amount']/1e4:10.2f}  实时偏差 {r['rt_amount']/1e4:8.2f}  合计 {r['total']/1e4:10.2f}"
          f"  偏差损益 {r['dev_pnl']/1e4:7.2f}  偏差电量 {r['dev_mwh']:8.1f} MWh")
fe = s["forecast_error"]
print(f"  预测误差（负荷+风光）偏差 |ΔQ| {fe['dev_abs_mwh']:.1f} MWh，实时偏差结算 {fe['rt_amount']/1e4:.2f} 万元，偏差损益 {fe['dev_pnl']/1e4:.2f} 万元")
for k, r in s["reconciliation"].items():
    print(f"  对账[{k}] 市场盈余 {r['merchandising_surplus']/1e4:.2f} 万 = 阻塞盈余 {r['congestion_rent']/1e4:.2f} 万 + 限价修正 {r['clamp_adjustment']/1e4:.2f} 万，残差 {r['residual']:.2e} 元")
rtc = s["realtime"]
print(f"  储能实时 {rtc['storage_mode']}/{rtc['anchor']} x={rtc['x']}；锚点松弛触发 {rtc['anchor_slack_periods']} 个窗口；"
      f"额外重定价（标准流程应为0） {rtc['repriced_periods']} 个时段")
print(f"  锚点最大松弛：储能 {rtc.get('storage_anchor_slack_max_mwh', '不可得')} MWh；抽蓄 {rtc.get('pumped_anchor_slack_max_1e4m3', '不可得')} 万m³（旧混合指标不作能量值）")
print(f"  日末 21:00–24:00 负荷加权均价 日前 {sp['da_lw_tail']:.2f} / 实时 {sp['rt_lw_tail']:.2f}；实时节点价最大 {sp['rt_lmp_max']:.1f}")
print(f"  校核 {ch}")
print(f"  用时 日前 {ts['da']:.0f}s / 日内 {ts['intraday']:.0f}s / 实时 {ts['realtime']:.0f}s / 结算 {ts['settlement']:.0f}s / 合计 {ts['total']:.0f}s")
print(f"  输出目录 {d}")
print(f"结束 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
