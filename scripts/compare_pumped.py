"""抽蓄接入前后对比：同一默认场景（MKT、丰水期、最新报价）分别在 PS1 停用 / 启用下跑完整 日前 + 实时 15 min 滚动 + 结算。

用法：python scripts/compare_pumped.py [--periods N] [--no-plots]
输出：汇总新建于 outputs/pumped_comparisons/，默认两次运行使用独立 outputs/full_* 目录，可在网页查看。
不复用历史结果；--output 可指定尚不存在的新目录（含两次运行，不自动加入网页目录）。
对比指标：负荷加权均价（日前/实时）、峰谷价差（小时负荷加权价 max−min）、阻塞盈余、弃电量、运行小时、日前目标值。
"""
import argparse
import copy
import json
import hashlib
import math
import tempfile
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.config import load_config, ROOT  # noqa: E402
from smm.full import run_full, write_full_outputs  # noqa: E402
from smm.pipeline import prepare  # noqa: E402
from smm.run_input_provenance import freeze_cli_inputs  # noqa: E402


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


def input_fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("config", "data", "bids", "src", "scripts")
            for p in sorted((root / folder).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def require_unchanged(root, expected, output):
    if input_fingerprint(root) != expected:
        raise RuntimeError(f"对比期间输入或源码已变化，停止混合不同条件的结果；已有证据保留在 {output}，请另建新对比。")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--periods", type=int, default=None)
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--bid-version", type=int, default=None)
    ap.add_argument("--time-limit", type=float, default=None, help="覆盖日前 MIP 时限（秒）")
    ap.add_argument("--solver-log", action="store_true", help="打印 HiGHS 日志")
    ap.add_argument("--output", type=Path, help="尚不存在的新对比目录；默认自动生成独立目录")
    arguments = sys.argv[1:] if argv is None else argv
    if "--reuse" in arguments:
        ap.error("--reuse 已移除：旧结果来源无法证明一致。请不带 --reuse 新建对比，或用 --output 指定尚不存在的新目录；旧结果保留。")
    a = ap.parse_args(arguments)
    if a.time_limit is not None and (not math.isfinite(a.time_limit) or a.time_limit <= 0):
        ap.error("--time-limit 必须为有限正数")
    if a.bid_version is not None and a.bid_version < 1:
        ap.error("--bid-version 必须为正整数")
    source_root = ROOT
    # Capture before reading configuration so concurrent edits cannot be hidden.
    before = input_fingerprint(source_root)
    cfg_base = load_config()
    if a.periods is not None and not 1 <= a.periods <= cfg_base.T:
        ap.error(f"--periods 必须为1到{cfg_base.T}之间的整数")
    if a.output is None:
        parent = cfg_base.root / "outputs" / "pumped_comparisons"
        parent.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="comparison_", dir=parent))
    else:
        output = a.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    print(f"独立对比目录：{output}", flush=True)
    require_unchanged(source_root, before, output)
    out = {}
    # Both variants share the loaded configuration; each preparation is read-only.
    for tag, on in (("ps_off", False), ("ps_on", True)):
        cfg = copy.deepcopy(cfg_base)
        if a.time_limit is not None:
            cfg.base["solver"]["time_limit_s"] = a.time_limit
        if a.solver_log:
            cfg.base["solver"]["log"] = True
        if not on:
            cfg.units["pumped"] = []
        print(f"[{tag}] 开始 {datetime.now().astimezone():%H:%M:%S %Z}", flush=True)
        require_unchanged(source_root, before, output)
        case = prepare(cfg, a.bid_version, read_only=True)
        if a.output is None:
            d = cfg.root / "outputs" / (
                f"full_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_"
                f"v{case.bids['version']}_{output.name}_{tag}")
        else:
            d = output / tag
        # Exclusive creation and frozen effective inputs precede the solve.
        freeze_cli_inputs(cfg, case, d)
        require_unchanged(source_root, before, output)
        res = run_full(cfg, case, periods=a.periods, log=lambda *x: None, retain_id_models=False)
        s = write_full_outputs(res, d, make_plots=not a.no_plots)
        out[tag] = dict(dir=str(d), metrics=metrics(s), time_s=s["time_s"], checks=s["checks"])
        print(f"[{tag}] 完成，用时 {s['time_s']['total']:.0f}s → {d}", flush=True)
    require_unchanged(source_root, before, output)
    df = pd.DataFrame({k: v["metrics"] for k, v in out.items()})
    df["变化(on−off)"] = df["ps_on"] - df["ps_off"]
    df.to_csv(output / "pumped_compare.csv", float_format="%.3f", encoding="utf-8-sig")
    (output / "pumped_compare.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(df.round(2).to_string())


if __name__ == "__main__":
    main()
