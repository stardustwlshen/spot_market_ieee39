"""一键全流程：日前出清 → 日内滚动优化 → 实时 15 min SCED → 两级偏差结算。

用法：
  python scripts/main_full.py                       # 统一规则 MKT、丰水期、最新版本报价、全天 96 时段
  python scripts/main_full.py --id-interval-h 2 --rt-lookahead-h 1 --storage-state fixed
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
ap.add_argument("--storage-state", choices=["redecide", "fixed"], default=None)
ap.add_argument("--no-plots", action="store_true")
a = ap.parse_args()

cfg = load_config(season=a.season)
if a.id_interval_h:
    cfg.mode["intraday"]["interval_h"] = a.id_interval_h
if a.rt_lookahead_h:
    cfg.mode["realtime"]["lookahead_h"] = a.rt_lookahead_h
if a.storage_state:
    cfg.mode["realtime"]["storage_state"] = a.storage_state
print(f"开始 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
case = prepare(cfg, a.bid_version)
res = run_full(cfg, case, periods=a.periods)
v = case.bids["version"]
d = cfg.root / "outputs" / f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{v}"
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
print(f"  校核 {ch}")
print(f"  用时 日前 {ts['da']:.0f}s / 日内 {ts['intraday']:.0f}s / 实时 {ts['realtime']:.0f}s / 结算 {ts['settlement']:.0f}s / 合计 {ts['total']:.0f}s")
print(f"  输出目录 {d}")
print(f"结束 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
