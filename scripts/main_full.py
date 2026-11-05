"""一键全流程：日前出清 →（可选日内）→ 实时 15 min 滚动 SCED → 两级偏差结算。

用法：
  python scripts/main_full.py                       # 统一规则 MKT、丰水期、最新版本报价、全天 96 时段
  python scripts/main_full.py --storage-mode fixed_da          # 储能执行日前曲线（模式 A）
  python scripts/main_full.py --storage-mode sced_anchor --anchor window_end_pct  # 显式选择历史锚点 B1
  python scripts/main_full.py --intraday --id-interval-h 2     # 打开日内计划滚动（对比用）
  python scripts/main_full.py --periods 24 --no-plots   # 只滚动前 6 h（调试）
"""
import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config, rt_terminal_policy  # noqa: E402
from smm.full import run_full, write_full_outputs  # noqa: E402
from smm.pipeline import prepare  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--bid-version", type=int, default=None)
ap.add_argument("--season", default=None)
ap.add_argument("--periods", type=int, default=None, help="只滚动前 N 个时段（默认 96）")
ap.add_argument("--id-interval-h", type=float, default=None, help="日内滚动周期（h），默认见 modes.yaml")
ap.add_argument("--rt-lookahead-h", type=float, default=None, help="实时前瞻窗口（h），默认 2")
ap.add_argument("--storage-mode", choices=["value", "sced_anchor", "fixed_da"], default=None, help="储能/抽蓄实时模式，默认 value（窗口末价值法）")
ap.add_argument("--anchor", choices=["two_hour_eq", "window_end_pct", "hourly_eq"], default=None, help="仅 sced_anchor：two_hour_eq 每2小时硬一致（不可行时软化回退）；hourly_eq(B2)/window_end_pct(B1)")
ap.add_argument("--hydro-mode", choices=["value", "window_end_da", "fixed_da", "sced"], default=None, help="水电实时模式，默认 value")
ap.add_argument("--band-fraction", type=float, default=None, help="价值法偏差带 δ0 比例（假设值 0.02）；同时作用于储能/抽蓄/水电")
ap.add_argument("--terminal-policy", choices=["hard", "soft"], default=None, help="仅实时价值法日末目标：soft有限成本（新默认）/hard历史严格目标；日前和日内不变")
ap.add_argument("--terminal-penalty", type=float, default=None, help="soft日末偏差研究罚价，元/可放电MWh；不是结算价，不改写场景")
ap.add_argument("--no-storage-reserve", action="store_true", help="储能/抽蓄不提供备用")
ap.add_argument("--no-pumped", action="store_true", help="移除抽水蓄能电站（对比场景）")
ap.add_argument("--anchor-x", type=float, default=None, help="甘肃锚点浮动比例 x（假设值 0.05）")
ap.add_argument("--intraday", action="store_true", help="打开日内计划滚动（默认关闭）")
ap.add_argument("--handoff-source", choices=["intraday", "day_ahead"], default=None, help="日内→实时交接来源（默认见 modes.yaml realtime.handoff_source）")
ap.add_argument("--rt-commit", choices=["inherit", "free"], default=None, help="实时是否继承日内 GT 启停（realtime.intraday_commit_rt）")
ap.add_argument("--no-plots", action="store_true")
ap.add_argument("--bid-file", default=None, help="直接读取指定报价 JSON，不写报价库；若同时指定bid-version，版本必须一致")
ap.add_argument("--read-only-inputs", action="store_true", help="只在内存生成曲线，不改写 data/profiles 或自动保存缺省报价（批跑安全模式）")
ap.add_argument("--tag", default="", help="输出目录后缀（对比方案用，如 _fixed_da）")
a = ap.parse_args()
if not re.fullmatch(r"[\w.-]*", a.tag):
    ap.error("--tag 只能包含字母、数字、中文、下划线、点和短横线，不能包含路径分隔符")
if a.periods is not None and a.periods <= 0:
    ap.error("--periods 必须为正整数")

cfg = load_config(season=a.season)
if a.id_interval_h is not None:
    cfg.mode["intraday"]["interval_h"] = a.id_interval_h
if a.rt_lookahead_h is not None:
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
if a.handoff_source:
    cfg.mode["realtime"]["handoff_source"] = a.handoff_source
if a.rt_commit:
    cfg.mode["realtime"]["intraday_commit_rt"] = a.rt_commit
if a.hydro_mode:
    cfg.mode["realtime"]["hydro"] = a.hydro_mode
if a.band_fraction is not None:
    vm = cfg.mode["realtime"].setdefault("value_method", {})
    vm["band_fraction"] = vm["hydro_band_fraction"] = a.band_fraction
if a.terminal_policy is not None or a.terminal_penalty is not None:
    vm = cfg.mode["realtime"].setdefault("value_method", {})
    spec = vm.setdefault("terminal_policy", {"mode":"hard"})
    if a.terminal_policy is not None:
        spec["mode"] = a.terminal_policy
    if a.terminal_penalty is not None:
        if spec.get("mode") != "soft":
            ap.error("--terminal-penalty 只用于 soft；请同时明确 --terminal-policy soft")
        spec["penalty_yuan_per_discharge_mwh"] = a.terminal_penalty
    rt_terminal_policy(cfg.mode)
if a.no_storage_reserve:
    cfg.mode["reserve"]["storage_reserve"] = False
if a.no_pumped:
    cfg.units["pumped"] = []
print(f"开始 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
bid_options = {}
if a.bid_file:
    import json
    bid_options["bid_data"] = json.loads(Path(a.bid_file).read_text(encoding="utf-8"))
case = prepare(cfg, a.bid_version, read_only=a.read_only_inputs, **bid_options)
v = case.bids["version"]
d = cfg.root / "outputs" / f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{v}{a.tag}"
if d.exists():
    raise FileExistsError(f"输出目录已存在，历史结果未覆盖：{d}；请用 --tag 指定新的运行后缀")
from smm.run_input_provenance import freeze_cli_inputs
freeze_cli_inputs(cfg, case, d)
res = run_full(cfg, case, periods=a.periods)
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
_vm = rtc.get("value_method") or {}
_mode_txt = (f"value δ0={_vm.get('band_fraction')}（假设值）/水电 {rtc.get('hydro')} δ0={_vm.get('hydro_band_fraction')}"
             if rtc['storage_mode'] == 'value' else f"{rtc['storage_mode']}/{rtc['anchor']} x={rtc['x']}")
print(f"  储能实时 {_mode_txt}；锚点松弛触发 {rtc['anchor_slack_periods']} 个窗口；"
      f"辅助算价定价时段（启用辅助算价时应为全部时段） {rtc['repriced_periods']} 个；硬→软回退 {rtc.get('fallback_count', 0)} 个窗口")
print(f"  锚点最大松弛：储能 {rtc.get('storage_anchor_slack_max_mwh', '不可得')} MWh；抽蓄 {rtc.get('pumped_anchor_slack_max_1e4m3', '不可得')} 万m³（旧混合指标不作能量值）")
print(f"  日末 21:00–24:00 负荷加权均价 日前 {sp['da_lw_tail']:.2f} / 实时 {sp['rt_lw_tail']:.2f}；实时节点价最大 {sp['rt_lmp_max']:.1f}")
print(f"  校核 {ch}")
print(f"  用时 日前 {ts['da']:.0f}s / 日内 {ts['intraday']:.0f}s / 实时 {ts['realtime']:.0f}s / 结算 {ts['settlement']:.0f}s / 合计 {ts['total']:.0f}s")
print(f"  输出目录 {d}")
print(f"结束 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区）")
