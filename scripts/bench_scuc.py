"""SCUC 求解参数对比（只跑 SCUC，不出图）。用法：python scripts/bench_scuc.py SC base threads8 ..."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm import scuc  # noqa: E402
from smm.config import load_config  # noqa: E402
from smm.pipeline import prepare  # noqa: E402

VARIANTS = {
    "relaxed": dict(storage_binary=False, tighten=False, warm_start=False),   # 旧做法（仅对比）
    "plain": dict(tighten=False, warm_start=False),             # 默认：0-1，无额外提速措施
    "tight": dict(tighten=True),                                # + 启停能力收紧
    "ws": dict(warm_start=True),                                # + 热启动
    "base": {},                                                 # 当前配置默认值（= plain）
    "tight_ws": dict(tighten=True, warm_start=True),
    "hourly": dict(commit_step_periods=4),                      # 可选：火电启停按小时决策
    "hourly_plain": dict(commit_step_periods=4, tighten=False, warm_start=False),
    "gap05": dict(mip_rel_gap=0.005),
    "threads8": dict(threads=8),
    "heur03": dict(highs_extra={"mip_heuristic_effort": 0.3}),
    "presolve_off": dict(highs_extra={"presolve": "off"}),
}

rs = sys.argv[1]
out = []
for name in sys.argv[2:]:
    cfg = load_config(rs)
    cfg.base["solver"].update(VARIANTS[name])
    case = prepare(cfg, bid_version=1, verbose=False)
    t0 = time.perf_counter()
    m = scuc.build_model(case)
    info = scuc.solve_scuc(m, print)
    nb = sum(1 for v in m.component_data_objects(scuc.pyo.Var) if v.is_integer() or v.is_binary())
    r = dict(rule_set=rs, variant=name, total_s=round(time.perf_counter() - t0, 1),
             solve_s=round(info["time_s"], 1), obj=round(info["objective"]), bound=round(info["best_bound"]),
             gap=round(info["gap"] * 100, 3), int_vars=nb, ws=(info.get("warm_start") or {}).get("incumbent"), simultaneous=scuc.storage_simultaneous(m))
    print(json.dumps(r, ensure_ascii=False), flush=True)
    out.append(r)
