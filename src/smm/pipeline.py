"""日前流程编排：数据准备 → 报价读取/校验 → 出清 → 输出 CSV/图/摘要。"""
from __future__ import annotations

import json
from pathlib import Path

from . import plots
from .bids import bid_dir, bids_to_csv, default_bids, load_bids, raise_on_errors, save_bids, validate_bids
from .case import build_da_case
from .config import Config
from .da import DAResult, run_da, summarize
from .forecast import accuracy_summary, apply_profile_overrides, generate_profiles, save_profiles
from .network import build_network


def ensure_default_bids(cfg: Config) -> Path:
    d = bid_dir(cfg)
    if not list(d.glob("bids_v*.json")):
        b = default_bids(cfg)
        raise_on_errors(validate_bids(cfg, b))
        p = save_bids(cfg, b, note="缺省报价（假设值）")
        bids_to_csv(json.loads(p.read_text(encoding="utf-8")), p.with_suffix(".csv"))
        return p
    return sorted(d.glob("bids_v*.json"), key=lambda p: int(p.stem.split("_v")[-1]))[-1]


def prepare(cfg: Config, bid_version: int | None = None, verbose=True):
    net = build_network(cfg)
    prof = apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base["seed"]))   # 无覆盖文件时不变
    save_profiles(cfg, prof)
    ensure_default_bids(cfg)
    bids = load_bids(cfg, bid_version)
    issues = validate_bids(cfg, bids)
    if verbose:
        print(f"[报价] {cfg.rule_set} v{bids['version']}：校验 {len(issues)} 条提示")
        for i in issues:
            print("   ", i)
    raise_on_errors(issues)
    from .bids import effective_bids
    return build_da_case(cfg, net, prof, effective_bids(cfg, bids))


def out_dir(cfg: Config, bids_version: int, tag: str = "") -> Path:
    d = cfg.root / "outputs" / f"da_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{bids_version}{tag}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_outputs(res: DAResult, d: Path, make_plots=True) -> dict:
    t = res.tables
    t["units"].to_csv(d / "da_unit_awards.csv", index=False, float_format="%.4f")
    t["prices"].to_csv(d / "da_nodal_prices.csv", index=False, float_format="%.4f")
    t["prices"].pivot(index="bus", columns="time", values="lmp").to_csv(d / "da_lmp_matrix.csv", float_format="%.2f")
    t["lines"].to_csv(d / "da_line_flows.csv", index=False, float_format="%.4f")
    t["storage"].to_csv(d / "da_storage.csv", index=False, float_format="%.4f")
    t["hydro"].to_csv(d / "da_hydro.csv", index=False, float_format="%.4f")
    t["pumped"].to_csv(d / "da_pumped.csv", index=False, float_format="%.4f")
    t["system"].to_csv(d / "da_system.csv", index=False, float_format="%.4f")
    if "dispatch_with_slack_units" in t:
        t["dispatch_with_slack_units"].to_csv(d / "da_unit_awards_dispatch_penalty.csv", index=False)
    s = summarize(res)
    s["costs"] = res.costs
    s["reserve_rule"] = {k: v for k, v in res.case.reserve_info.items() if not isinstance(v, list)}
    acc = accuracy_summary(res.case.profiles, {r["id"]: r["cap"] for r in res.case.cfg.units["renewable"]})
    acc.to_csv(d / "forecast_accuracy.csv", index=False)
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    if make_plots:
        plots.lmp_heatmap(t["prices"], d / "lmp_heatmap.png")
        plots.lmp_curves(t["prices"], t["system"], d / "lmp_curves.png")
        plots.generation_stack(t["units"], t["system"], d / "generation_stack.png")
        plots.storage_soc(t["storage"], d / "storage_soc.png", t["prices"])
        plots.line_loading(t["lines"], d / "line_loading.png")
        plots.forecast_plot(res.case.profiles, d / "forecast_vs_actual.png")
    return s


def print_summary(s: dict):
    print("=" * 60)
    print(f"日前出清 {s['date']} {s['rule_set']} {s['season']} 报价 v{s['bid_version']}")
    print(f"  目标函数（含罚）  : {s['objective']:,.0f} 元   报价总成本 {s['bid_cost_total']:,.0f} 元（其中启动费 {s['startup_cost']:,.0f}）")
    print(f"  LMP 范围          : {s['lmp_min']:.2f} ~ {s['lmp_max']:.2f} 元/MWh（负荷加权均价 {s['lmp_load_weighted_avg']:.2f}）")
    print(f"  系统能量价 λ 范围 : {s['energy_price_min']:.2f} ~ {s['energy_price_max']:.2f}")
    print(f"  LMP 分解最大误差  : {s['lmp_decomp_max_err']:.2e}")
    cl = ", ".join(f"{c['line']}({c['periods']}点, μmax={c['max_mu']:.0f})" for c in s["congested_lines"]) or "无"
    print(f"  阻塞线路          : {cl}")
    print(f"  新能源弃电        : {s['curtailment_mwh']:.1f} MWh")
    print(f"  SCUC 用时         : {s['scuc_time_s']:.1f}s（gap {s['scuc_gap']:.3%}）  SCED 用时 {s['sced_time_s']:.1f}s")
    print(f"  松弛              : {s['slack']}  定价重算: {s['pricing_rerun']}")
    print("=" * 60)
