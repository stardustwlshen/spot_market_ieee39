"""按场景运行（网页应用后台调用，也可命令行使用）。

用法：
  python scripts/run_scenario.py --scenario 默认 --mode da          # 仅日前
  python scripts/run_scenario.py --scenario 默认 --mode full        # 日前 → 实时 15 min 滚动 → 偏差结算
  python scripts/run_scenario.py --scenario 默认 --mode full --periods 8 --bid-version 1
结果写入 runs/<场景>/<时间戳>_<mode>/：meta.json（状态）、run.log、da/（日前全部表）、实时与结算 CSV、summary.json、profiles.csv。
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.live_runner import ProgressWriter, RunCancelled, atomic_json, freeze_inputs
from smm.config import load_config  # noqa: E402
from smm.scenario import new_run_dir, scenario_path  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--scenario", required=True)
ap.add_argument("--mode", choices=["da", "full"], default="full")
ap.add_argument("--bid-version", type=int, default=None)
ap.add_argument("--periods", type=int, default=None, help="全流程只滚动前 N 个时段（调试）")
ap.add_argument("--run-dir", default=None, help="由网页应用预先创建的结果目录")
ap.add_argument("--plots", action="store_true", help="同时输出 PNG 图（网页用 plotly，默认不出图）")
a = ap.parse_args()

root = scenario_path(a.scenario)
d = Path(a.run_dir) if a.run_dir else new_run_dir(a.scenario, a.mode)
d.mkdir(parents=True, exist_ok=True)
meta_p = d / "meta.json"
meta = dict(scenario=a.scenario, mode=a.mode, bid_version=a.bid_version, periods=a.periods,
            started=datetime.now().isoformat(timespec="seconds"), status="running", stage="准备", pid=os.getpid(), pid_namespace=os.readlink("/proc/self/ns/pid"))
if meta_p.exists():
    meta = {**json.loads(meta_p.read_text(encoding="utf-8")), **{k: v for k, v in meta.items() if k != "started"}}


def put(**kw):
    meta.update(kw)
    atomic_json(meta_p, meta)


put()
t0 = time.time()
progress = ProgressWriter(d, total=a.periods or 96)
try:
    # 场景快照（便于复现）
    snap = d / "scenario_snapshot"
    if not snap.exists():
        freeze_inputs(root, d)
    root = snap
    from smm.da import run_da
    from smm.pipeline import prepare, print_summary, write_outputs

    cfg = load_config(root=root)
    if cfg.mode.get("regulation",{}).get("enabled",False):
        raise ValueError("当前运行入口仅支持纯电能量现货；调频配置仅作历史实验归档")
    print(f"开始 {datetime.now():%Y-%m-%d %H:%M:%S}（本机时区） 场景 {a.scenario} 模式 {a.mode}", flush=True)
    put(stage="报价校验与数据准备")
    progress.check_cancel()
    case = prepare(cfg, a.bid_version)
    atomic_json(d/"resolved_bids.json",case.bids)
    case.profiles.to_csv(d / "profiles.csv", index=False)
    put(bid_version=case.bids["version"], stage="日前出清")
    (d / "da").mkdir(exist_ok=True)
    if a.mode == "da":
        res = run_da(case)
        progress.check_cancel()
        progress({"phase":"da_done", "da":res})
        s = write_outputs(res, d / "da", make_plots=a.plots)
        print_summary(s)
    else:
        from smm.full import run_full, write_full_outputs
        put(stage="日前出清 + 实时滚动")
        res = run_full(cfg, case, periods=a.periods, progress_callback=progress, should_cancel=progress.cancelled)
        progress.check_cancel()
        put(stage="写出结果")
        write_outputs(res["da"], d / "da", make_plots=False)
        s = write_full_outputs(res, d, make_plots=a.plots)
        sp = s["spread"]
        print(f"负荷加权均价 日前 {sp['da_lw_avg']:.2f} 实时 {sp['rt_lw_avg']:.2f} 价差均值 {sp['lw_spread_mean']:+.2f}", flush=True)
    progress.state.update(phase="done", settled=a.mode == "full" and a.periods in (None, cfg.T),
                          completed_scope="full_day" if a.periods in (None,cfg.T) else "debug_prefix")
    progress.write()
    put(status="done", stage="完成", finished=datetime.now().isoformat(timespec="seconds"), elapsed_s=round(time.time() - t0, 1))
    print(f"结束 {datetime.now():%Y-%m-%d %H:%M:%S}，用时 {time.time() - t0:.0f}s，输出 {d}", flush=True)
except RunCancelled as e:
    progress.state.update(phase="stopped", settled=False, error=str(e)); progress.write()
    put(status="stopped", stage="用户取消（已执行前缀保留，未结算）", error=str(e), elapsed_s=round(time.time()-t0,1))
except Exception as e:  # noqa: BLE001
    traceback.print_exc()
    progress.state.update(phase="failed", settled=False, error=str(e)); progress.write()
    put(status="failed", stage="失败", error=f"{type(e).__name__}: {e}", finished=datetime.now().isoformat(timespec="seconds"),
        elapsed_s=round(time.time() - t0, 1))
    sys.exit(1)
