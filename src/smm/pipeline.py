"""日前流程编排：数据准备 → 报价读取/校验 → 出清 → 输出 CSV/图/摘要。"""
from __future__ import annotations

import json
import copy
from pathlib import Path

from . import plots
from .bids import bid_dir, bids_to_csv, default_bids, fill_missing_bids, load_bids, raise_on_errors, save_bids, validate_bids
from .case import build_da_case
from .config import Config
from .da import DAResult, run_da, summarize
from .forecast import apply_profile_overrides, generate_profiles, save_profiles
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


_UNSET_BID_DATA = object()


def prepare(cfg: Config, bid_version: int | None = None, verbose=True, *, read_only=False,
            bid_data=_UNSET_BID_DATA):
    """Prepare a detached case; explicit bids never depend on the bid directory.

    ``bid_data`` is a supplied JSON object, not a replacement global loader.
    Even explicit None is invalid rather than a request to fall back to defaults.
    """
    from .input_validation import validate_time_grid
    validate_time_grid(cfg)
    if bid_data is not _UNSET_BID_DATA:
        bids = copy.deepcopy(bid_data)
        raise_on_errors(validate_bids(cfg, bids))
        version = bids.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("显式报价version必须是正整数，不能回退默认报价")
        if bid_version is not None and bid_version != version:
            raise ValueError(f"显式报价版本{version}与请求bid_version={bid_version}冲突")
        bids = fill_missing_bids(cfg, bids)
    elif read_only and not list(bid_dir(cfg).glob("bids_v*.json")):
        if bid_version not in (None, 1):
            raise FileNotFoundError(f"No frozen bid version {bid_version}")
        bids = default_bids(cfg)
    else:
        if not read_only:
            ensure_default_bids(cfg)
        bids = load_bids(cfg, bid_version)
    issues = validate_bids(cfg, bids)
    raise_on_errors(issues)
    if verbose:
        print(f"[报价] {cfg.rule_set} v{bids['version']}：校验 {len(issues)} 条提示")
        for i in issues:
            print("   ", i)
    net = build_network(cfg)
    prof = apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base["seed"]))
    from .forecast_settings import generation_metadata
    prof.attrs["generation_metadata"] = generation_metadata(cfg)
    if not read_only:
        save_profiles(cfg, prof)
    from .bids import effective_bids
    return build_da_case(cfg, net, prof, effective_bids(cfg, bids))


def out_dir(cfg: Config, bids_version: int, tag: str = "") -> Path:
    d = cfg.root / "outputs" / f"da_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v{bids_version}{tag}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def write_outputs(res: DAResult, d: Path, make_plots=True) -> dict:
    t = res.tables
    from .output_catalog import write_da_tables, write_catalog
    from .live_runner import completion_metadata
    write_da_tables(res, d)
    from .results import reserve_snapshot
    import pandas as pd
    reserve = reserve_snapshot(res.model)
    pd.DataFrame(reserve["resources"]).to_csv(d / "da_reserve.csv", index=False, float_format="%.4f")
    pd.DataFrame(reserve["system"]).to_csv(d / "da_reserve_system.csv", index=False, float_format="%.4f")
    (d / "reserve_window_evidence.json").write_text(json.dumps({"da":reserve,"rt_windows":[]}, ensure_ascii=False), encoding="utf-8")
    (d / "stage_diagnostics.json").write_text(json.dumps({"da":res.stage_diagnostics,"rt_windows":[]}, ensure_ascii=False, default=float), encoding="utf-8")
    from .full import write_pricing_evidence
    write_pricing_evidence(res, [], d)
    if "dispatch_with_slack_units" in t:
        t["dispatch_with_slack_units"].to_csv(d / "da_unit_awards_dispatch_penalty.csv", index=False)
    s = summarize(res)
    scope = completion_metadata(res.case.cfg.T, 0, mode="da")
    s.update(scope)
    s["costs"] = res.costs
    s["reserve_rule"] = {k: v for k, v in res.case.reserve_info.items() if not isinstance(v, list)}
    from .output_catalog import write_profile_evidence
    write_profile_evidence(res.case, d)
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    plot_files = []
    if make_plots:
        plot_files = plots.dispatch_plots(t, d)
        plots.forecast_plot(res.case.profiles, d / "forecast_vs_actual.png")
        if any(c.endswith("_actual") and c[:-7]+"_da" in res.case.profiles for c in res.case.profiles):
            plot_files.append("forecast_vs_actual.png")
    write_catalog(d, scope, plot_files=plot_files)
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
