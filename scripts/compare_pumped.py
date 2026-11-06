"""抽蓄接入前后对比：同一默认场景（MKT、丰水期、最新报价）分别在 PS1 停用 / 启用下跑完整 日前 + 实时 15 min 滚动 + 结算。

用法：python scripts/compare_pumped.py [--periods N] [--no-plots]
输出：outputs/pumped_compare.json、outputs/pumped_compare.csv，以及两次全流程目录 outputs/full_..._ps_off / _ps_on。
对比指标：负荷加权均价（日前/实时）、峰谷价差（小时负荷加权价 max−min）、阻塞盈余、弃电量、运行小时、日前目标值。
"""
import argparse
import copy
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config  # noqa: E402
from smm.full import run_full, write_full_outputs  # noqa: E402
from smm.pipeline import prepare  # noqa: E402


def metrics(s: dict) -> dict:
    sp, rec, rh = s["spread"], s["reconciliation"], s["running_hours"]
    o = {
        "日前目标值_元": s["da"]["objective"], "日前MIP_gap": s["da"]["gap"],
        "日前负荷加权均价": sp["da_lw_avg"], "实时负荷加权均价": sp["rt_lw_avg"],
        "日前峰谷价差(小时)": max(sp["da_lw_hourly"]) - min(sp["da_lw_hourly"]),
        "实时峰谷价差(小时)": max(sp["rt_lw_hourly"]) - min(sp["rt_lw_hourly"]),
        "日前节点价最高": sp["da_lmp_max"], "日前节点价最低": sp["da_lmp_min"],
        "实时节点价最高": sp["rt_lmp_max"], "实时节点价最低": sp["rt_lmp_min"],
        "日前阻塞盈余_元": rec.get("日前", {}).get("congestion_rent"), "实时阻塞盈余_元": rec.get("实时偏差", {}).get("congestion_rent"),
        "日前弃电_MWh": s["da"]["curtail_mwh"], "实时弃电_MWh": s["realtime"]["curtail_mwh"],
        "实时切负荷_MWh": s["realtime"]["shed_mwh"],
    }
    for tag in ("da", "rt"):
        r = rh[tag]
        t = "日前" if tag == "da" else "实时"
        o[f"{t}火电开机台时_h"] = r["thermal_unit_hours"]
        o[f"{t}火电启动次数"] = r["thermal_starts"]
        for k in ("coal", "gas_ccgt", "gas_gt"):
            o[f"{t}{k}台时_h"] = r.get(f"{k}_unit_hours", 0.0)
        for h, v in r["hydro_gen_hours"].items():
            o[f"{t}{h}发电台时_h"] = v
        o[f"{t}抽蓄发电小时_h"] = r["pumped_gen_hours"]
        o[f"{t}抽蓄抽水小时_h"] = r["pumped_pump_hours"]
    for pid, p in s.get("pumped", {}).items():
        o[f"{pid}_日前发电_MWh"], o[f"{pid}_日前抽水_MWh"] = p["da"]["gen_mwh"], p["da"]["pump_mwh"]
        o[f"{pid}_实时发电_MWh"], o[f"{pid}_实时抽水_MWh"] = p["rt"]["gen_mwh"], p["rt"]["pump_mwh"]
        o[f"{pid}_电能量净收入_元"] = p["energy_net_revenue"]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=int, default=None)
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--bid-version", type=int, default=None)
    ap.add_argument("--time-limit", type=float, default=None, help="覆盖日前 MIP 时限（秒）")
    ap.add_argument("--solver-log", action="store_true", help="打印 HiGHS 日志")
    ap.add_argument("--reuse", action="store_true", help="输出目录已有 summary.json 时直接复用（分步保存，中断后可续跑）")
    a = ap.parse_args()
    out = {}
    root = None
    for tag, on in (("ps_off", False), ("ps_on", True)):
        cfg = load_config()
        root = cfg.root
        if a.time_limit:
            cfg.base["solver"]["time_limit_s"] = a.time_limit
        if a.solver_log:
            cfg.base["solver"]["log"] = True
        if not on:
            cfg.units["pumped"] = []
        print(f"[{tag}] 开始 {datetime.now(ZoneInfo("America/Los_Angeles")):%H:%M:%S} PST", flush=True)
        if a.reuse:
            hit = sorted((cfg.root / "outputs").glob(f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{a.bid_version or '*'}_{tag}/summary.json"))
            if hit:
                s = json.loads(hit[-1].read_text(encoding="utf-8"))
                out[tag] = dict(dir=str(hit[-1].parent.relative_to(cfg.root)), metrics=metrics(s), time_s=s["time_s"], checks=s["checks"])
                print(f"[{tag}] 复用 {hit[-1].parent}", flush=True)
                continue
        case = prepare(cfg, a.bid_version)
        res = run_full(cfg, case, periods=a.periods, log=lambda *x: None, retain_id_models=False)
        d = cfg.root / "outputs" / f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{case.bids['version']}_{tag}"
        s = write_full_outputs(res, d, make_plots=not a.no_plots)
        out[tag] = dict(dir=str(d.relative_to(cfg.root)), metrics=metrics(s), time_s=s["time_s"], checks=s["checks"])
        print(f"[{tag}] 完成，用时 {s['time_s']['total']:.0f}s → {d}", flush=True)
    df = pd.DataFrame({k: v["metrics"] for k, v in out.items()})
    df["变化(on−off)"] = df["ps_on"] - df["ps_off"]
    df.to_csv(root / "outputs" / "pumped_compare.csv", float_format="%.3f", encoding="utf-8-sig")
    (root / "outputs" / "pumped_compare.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(df.round(2).to_string())


if __name__ == "__main__":
    main()
