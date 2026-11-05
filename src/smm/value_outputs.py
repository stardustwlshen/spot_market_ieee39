"""第二轮H项：价值法输出。

- da_value_curves.csv       日前最终SCED库存对偶给出的逐时段状态价值（原生单位与元/MWh）
- rt_value_windows.csv      每个实时窗口末 τ 实际使用的价值、窗口末状态、日前同时刻状态、ΔE/ΔV、偏差带上下限及是否绑定
- rt_fallbacks.csv          硬→软回退记录（时段、原因、罚因子、软化后松弛）
- rt_value_price_setting.csv 价值项定价时段（启发式：价值主体在执行时段处于内点且节点电价不等于其任一报价段价格）
- price_aux_vs_dual.csv     日前/实时 辅助算价价格与调度SCED对偶价格的差
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from . import scuc
from .rolling import snapshot

KIND_STATE = {"E": "E", "Vu": "Vu", "V": "Vh"}
KIND_LABEL = {"E": "储能SOC电量", "Vu": "抽蓄上库库容", "V": "水电库容"}


def _conversion(cfg, kind, unit):
    """原生单位价值 → 元/MWh 的乘数。"""
    if kind == "E":
        return 1.
    if kind == "Vu":
        d = next(p for p in cfg.units.get("pumped", []) if p["id"] == unit)
        return 1. / float(d["e_gen"])
    d = next(h for h in cfg.units["hydro"] if h["id"] == unit)
    return .36 * float(d["water_rate"])


def da_value_curves(da) -> pd.DataFrame:
    m = da.model
    if not hasattr(m, "dual"):
        return pd.DataFrame()
    cfg = da.case.cfg
    times = [f"{int(t*cfg.dt):02d}:{int(round((t*cfg.dt % 1)*60)):02d}" for t in range(cfg.T)]
    rows = []
    for kind, units in scuc.state_values(m).items():
        for unit, rec in units.items():
            for t, v in enumerate(rec["value_native"]):
                terminal = rec.get("terminal_target_dual", {}) if t == cfg.T - 1 else {}
                rows.append(dict(kind=kind, kind_label=KIND_LABEL[kind], unit=unit, period=t, time=times[t],
                                 value_native=float(v), native_unit=rec["native_unit"],
                                 value_yuan_per_mwh=float(rec["value_yuan_per_mwh"][t]),
                                 dual_raw=float(rec["dual_raw"][t]),
                                 continuation_value_scope="no_cross_day_residual" if t == cfg.T - 1 else "within_day",
                                 terminal_target_constraint=terminal.get("constraint"),
                                 terminal_target_dual_native=terminal.get("value_native"),
                                 terminal_target_dual_yuan_per_mwh=terminal.get("value_yuan_per_mwh"),
                                 terminal_target_dual_available=terminal.get("available")))
    return pd.DataFrame(rows)


def rt_value_windows(da, rr, curves: pd.DataFrame | None = None) -> pd.DataFrame:
    cfg = da.case.cfg
    da_sn = snapshot(da.model)
    rows = []
    for r in rr.rt:
        for w in r["snap"].get("value_window", []):
            k = int(w["period"])
            x_da = float(da_sn[KIND_STATE[w["kind"]]][w["unit"]][k])
            v = w["value_native"]
            src = w.get("reference_source", r.get("reference_source", "day_ahead"))
            x_id = None
            if str(src).startswith("intraday") and "reference_state" in w:
                x_id = float(w["reference_state"])
            elif str(src).startswith("intraday") and r.get("plan_idx") is not None and r["plan_idx"] < len(rr.plans):
                arr = rr.plans[r["plan_idx"]].get(KIND_STATE[w["kind"]], {}).get(w["unit"])
                x_id = None if arr is None else float(arr[k])
            rows.append(dict(window_start=int(r["t"]), tau=k, reference_source=src, state_id=x_id, kind=w["kind"], kind_label=KIND_LABEL[w["kind"]],
                             value_source=w.get("value_source", src), reference_round=w.get("reference_round"),
                             reference_fallback_reason=w.get("reference_fallback_reason"),
                             state_reference=w.get("reference_state", x_da),
                             delta_from_reference=w["state"] - w.get("reference_state", x_da),
                             unit=w["unit"], label=w["label"], value_native=v,
                             value_yuan_per_mwh=None if v is None else float(v) * _conversion(cfg, w["kind"], w["unit"]),
                             state=w["state"], state_da=x_da, delta=w["state"] - x_da,
                             lower=w["lower"], upper=w["upper"],
                             lower_binding=w["lower_binding"], upper_binding=w["upper_binding"],
                             band_binding=bool(w["lower_binding"] or w["upper_binding"]) and w["upper"] - w["lower"] > 1e-6,
                             band_slack=float(w["slack_up"] + w["slack_dn"]), soft=w["soft"],
                             fallback=bool(r.get("fallback")),
                             **{key:w.get(key) for key in ("terminal_policy", "planned_terminal_soft",
                                 "terminal_penalty_yuan_per_discharge_mwh", "discharge_energy_factor",
                                 "deviation_discharge_mwh", "terminal_penalty_cost")}))
    return pd.DataFrame(rows)


def rt_fallbacks(rr) -> pd.DataFrame:
    rows = [dict(**r["fallback"]) for r in rr.rt if r.get("fallback")]
    rows += [dict(t=int(r["t"]), reason="state_deviation_at_anchor", **dev)
             for r in rr.rt for dev in (r.get("state_deviations") or [])]
    return pd.DataFrame(rows, columns=None if rows else ["t", "window_end_exclusive", "reason", "penalty", "softened",
                                                           "band_slack_max", "anchor_slack_max"])


def _bid_prices(da, uid):
    """该主体全部报价段价格（元/MWh）。"""
    out = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("price", "prices", "dis_prices", "ch_prices", "gen_prices", "pump_prices") or "price" in str(k):
                    walk_values(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    def walk_values(v):
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, (list, tuple)):
            for y in v:
                walk_values(y)
        elif isinstance(v, dict):
            walk(v)
    walk((da.case.bids or {}).get("units", {}).get(uid, {}))
    return sorted(out)


def value_price_setting(da, rr, tol=1e-3) -> pd.DataFrame:
    """价值项定价时段（启发式判别，非严格证明）：价值主体在执行时段功率内点（非0、非满功率）、库存不在上下限，
    且节点实时价格与其所有报价段价格、以及本时段已出力火电/燃机的报价段价格都相差 > 1 元/MWh，则判为由跨时段价值（窗口末价值项）决定。"""
    cfg = da.case.cfg
    buses = list(da.case.net.buses)
    rows = []
    stor = {d["id"]: d for d in cfg.units["storage"]}
    pumped = {d["id"]: d for d in cfg.units.get("pumped", [])}
    hydro = {d["id"]: d for d in cfg.units["hydro"]}
    bid_cache = {}
    for r in rr.rt:
        if not r["snap"].get("value_window"):
            continue
        t, sn = int(r["t"]), r["snap"]
        lmp = r["prices"]["lmp"][:, 0]
        cands = []
        for s, d in stor.items():
            if s in sn.get("Pd", {}):
                p = sn["Pd"][s][0] or sn["Pc"][s][0]
                e = sn["E"][s][0]
                cands.append((s, "storage", d["bus"], p, d["pmax"], d["soc_min"]*d["ecap"] + tol < e < d["soc_max"]*d["ecap"] - tol))
        for pid, d in pumped.items():
            if pid in sn.get("Pg", {}):
                n = sn["ngen"][pid][0] or sn["npump"][pid][0]
                p = sn["Pg"][pid][0] or sn["Pp"][pid][0]
                cap = float(d["unit_gen_max"]) * n if sn["ngen"][pid][0] else float(d["unit_pump"]) * n
                lo = float(d["unit_gen_min"]) * n if sn["ngen"][pid][0] else cap
                cands.append((pid, "pumped", d["bus"], p if p > lo + tol else 0., cap, True))
        for h, d in hydro.items():
            if h in sn.get("Ph", {}):
                hc = sn["hydro_constraints"][h]
                cap = float(d["unit_pmax"]) * hc["units_on"][0]
                cands.append((h, "hydro", d["bus"], sn["Ph"][h][0], cap, hc["spill_m3s"][0] < tol))
        # 已出力的火电/燃机报价段价格：节点价贴近这些价格时归因于报价，不判为价值定价（K项排查：
        # 湿季 6–10 时 RT 300 元/MWh 来自 C3 第2段报价，旧判别把内点运行的 H1 误判为价值定价）
        other = set()
        for g, series in sn.get("P", {}).items():
            if float(series[0]) > tol:
                other.update(bid_cache.setdefault(g, _bid_prices(da, g)))
        for uid, typ, bus, p, cap, interior_state in cands:
            price = float(lmp[buses.index(bus)])
            interior = bool(p > tol and p < cap - tol and interior_state)
            bids = bid_cache.setdefault(uid, _bid_prices(da, uid))
            gap = min((abs(price - b) for b in bids), default=np.inf)
            rows.append(dict(period=t, unit=uid, type=typ, bus=bus, power_mw=float(p), capacity_mw=float(cap),
                             interior=interior, node_lmp_raw=price, nearest_bid_price_gap=float(gap),
                             other_bid_price_gap=float(min((abs(price - b) for b in other), default=np.inf)),
                             value_sets_price=bool(interior and gap > 1. and all(abs(price - b) > 1. for b in other))))
    return pd.DataFrame(rows)


def price_aux_vs_dual(da, rr) -> pd.DataFrame:
    rows = []
    dp = da.tables["prices"]
    if {"lmp_raw", "dispatch_raw_lmp"} <= set(dp):
        for x in dp.itertuples():
            rows.append(dict(stage="DA", period=int(x.period), bus=x.bus, aux_lmp_raw=float(x.lmp_raw),
                             dual_lmp_raw=float(x.dispatch_raw_lmp), diff=float(x.lmp_raw - x.dispatch_raw_lmp)))
    buses = list(da.case.net.buses)
    for r in rr.rt:
        pr, dpr = r["prices"], r.get("dispatch_prices", r["prices"])
        for i, b in enumerate(buses):
            rows.append(dict(stage="RT", period=int(r["t"]), bus=b, aux_lmp_raw=float(pr["lmp"][i, 0]),
                             dual_lmp_raw=float(dpr["lmp"][i, 0]), diff=float(pr["lmp"][i, 0] - dpr["lmp"][i, 0])))
    return pd.DataFrame(rows)


def hydro_terminal_lookahead_rows(rr):
    return [dict(window_start=int(r["t"]), tau=int(r["t"])+int(row["relative_period"]), **row)
            for r in rr.rt for row in r["snap"].get("hydro_terminal_lookahead", [])]


def write_value_outputs(da, rr, d):
    f = dict(index=False, float_format="%.4f")
    rows = hydro_terminal_lookahead_rows(rr)
    (d / "hydro_terminal_lookahead_evidence.json").write_text(json.dumps(dict(
        scope="reference_continuation_forecast_not_executed_cost_or_feasibility_proof", rows=rows),
        ensure_ascii=False, default=float, indent=2) + "\n", encoding="utf-8")
    scalar_rows = [{key:value for key,value in row.items() if not isinstance(value,(dict,list))} for row in rows]
    pd.DataFrame(scalar_rows, columns=None if scalar_rows else ["window_start","tau","unit","reference_source",
        "projected_terminal_volume","projected_terminal_shortfall","projected_terminal_shortfall_mwh","penalty_cost"]
        ).to_csv(d / "rt_hydro_terminal_lookahead.csv", **f)
    curves = da_value_curves(da)
    curves.to_csv(d / "da_value_curves.csv", **f)
    rt_value_windows(da, rr, curves).to_csv(d / "rt_value_windows.csv", **f)
    rt_fallbacks(rr).to_csv(d / "rt_fallbacks.csv", **f)
    value_price_setting(da, rr).to_csv(d / "rt_value_price_setting.csv", **f)
    price_aux_vs_dual(da, rr).to_csv(d / "price_aux_vs_dual.csv", **f)
