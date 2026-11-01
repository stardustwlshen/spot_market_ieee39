"""一键全流程：日前 →（可选日内）→ 实时 15 min 滚动 SCED → 两级偏差结算，并输出表、图与摘要。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import plots, rolling, settlement
from .da import run_da, summarize


def checks(da_res, rr, rt_tab) -> dict:
    """连续性与平衡性校核（测试同样使用）。"""
    cfg = da_res.case.cfg
    dt = cfg.dt
    sto = {s["id"]: s for s in cfg.units["storage"]}
    out = {}
    # SOC 连续：每时段起点 = 上一时段实际终点，且满足充放电方程
    st = rt_tab["storage"]
    err_cont, err_eq = 0.0, 0.0
    for s, d in st.groupby("unit"):
        d = d.sort_values("period")
        e0 = sto[s]["soc0"] * sto[s]["ecap"]
        prev = np.r_[e0, d.E_mwh.to_numpy()[:-1]]
        err_cont = max(err_cont, float(np.abs(d.E_start.to_numpy() - prev).max()))
        calc = d.E_start + (sto[s]["eta_c"] * d.P_ch - d.P_dis / sto[s]["eta_d"]) * dt
        err_eq = max(err_eq, float(np.abs(calc - d.E_mwh).max()))
        out.setdefault("storage_simultaneous", 0)
        out["storage_simultaneous"] += int(((d.P_ch > 1e-6) & (d.P_dis > 1e-6)).sum())
    out["soc_continuity_err_mwh"] = err_cont
    out["soc_equation_err_mwh"] = err_eq
    # 机组组合连续：锁定类型（煤电/CCGT）在日内各轮与实时均等于日前
    lock = set(cfg.mode["realtime"]["lock_types"])
    da_u = da_res.tables["units"].set_index(["unit", "period"]).on
    mism_id = mism_rt = 0
    ru = rt_tab["units"]
    for u in cfg.units["thermal"]:
        if u["type"] not in lock:
            continue
        for r in rr.id_rounds:
            arr = r["snap"]["u"][u["id"]]
            mism_id += int(sum(int(arr[k]) != int(da_u[(u["id"], r["tau"] + k)]) for k in range(len(arr))))
        d = ru[ru.unit == u["id"]]
        mism_rt += int(sum(int(o) != int(da_u[(u["id"], p)]) for o, p in zip(d.on, d.period)))
    out["locked_commitment_mismatch_id"] = mism_id
    out["locked_commitment_mismatch_rt"] = mism_rt
    # 实时锁定机组开机状态 = 执行时所用参考计划（无日内时即日前）
    mism = 0
    types = {u["id"]: u["type"] for u in cfg.units["thermal"]}
    for r in rr.rt:
        plan = rr.plans[r["plan_idx"]]
        for g, arr in r["snap"]["u"].items():
            if types[g] in lock:
                mism += int(arr[0] != plan["u"][g][r["t"]])
    out["rt_vs_plan_commitment_mismatch"] = mism
    # 水电执行日前曲线
    rc = cfg.mode["realtime"]
    if rc.get("hydro", "fixed_da") == "fixed_da":
        dah = da_res.tables["units"].set_index(["unit", "period"]).P_mw
        dh = ru[ru.type == "hydro"]
        out["hydro_vs_da_err_mw"] = float(max([abs(p - dah[(u, t)]) for u, t, p in zip(dh.unit, dh.period, dh.P_mw)] or [0.0]))
    # 储能：锚点满足情况（实时各窗口末 E 相对日前 E 的偏差占比）/ 模式 A 跟随日前
    sc = rc["storage"]
    da_sto = da_res.tables["storage"].set_index(["unit", "period"])
    if sc.get("mode") == "fixed_da":
        out["storage_vs_da_err_mw"] = float(max(abs((r.P_dis - r.P_ch) - (da_sto.loc[(r.unit, r.period), "P_dis"] - da_sto.loc[(r.unit, r.period), "P_ch"]))
                                                for r in st.itertuples()))
    out["anchor_relaxed_periods"] = int(sum(1 for r in rr.rt if r.get("anchor_relaxed", 0)))
    out["anchor_slack_max_mwh"] = float(max(r["slack"].get("anchor_slack_max_mwh", 0.0) for r in rr.rt))
    # 最小开停机（实际执行序列）
    viol = 0
    for u in cfg.units["thermal"]:
        d = ru[ru.unit == u["id"]].sort_values("period").on.to_numpy()
        init = u["init"]
        seq = np.r_[[1 if init["online"] else 0] * int(round(init["hours"] / dt)), d]
        runs, cur, n = [], seq[0], 0
        for x in seq:
            if x == cur:
                n += 1
            else:
                runs.append((cur, n)); cur, n = x, 1
        for k, (val, n) in enumerate(runs[1:], 1):           # 中间完整段
            need = u["ut"] if val == 1 else u["dt"]
            if n * dt < need - 1e-9:
                viol += 1
    out["min_updown_violations"] = viol
    # 实时功率平衡
    sysd = rt_tab["system"]
    gen = rt_tab["units"].groupby("period").P_mw.sum()
    tie = sum(np.asarray(s)[: len(sysd)] for _, (b, s) in da_res.case.tie.items())
    bal = gen.to_numpy() + tie + sysd.shed_mw.to_numpy() - sysd.surplus_mw.to_numpy() - sysd.load_mw.to_numpy()
    out["rt_balance_err_mw"] = float(np.abs(bal).max())
    inj = rt_tab["prices"].groupby("period").inj_mw.sum()
    out["rt_injection_sum_err_mw"] = float(np.abs(inj).max())
    return out


def run_full(cfg, case, log=print, periods: int | None = None) -> dict:
    t0 = time.perf_counter()
    da = run_da(case, verbose=True)
    t_da = time.perf_counter() - t0
    rr = rolling.run_rolling(da, periods=periods, log=log)
    t1 = time.perf_counter()
    rt_tab = rolling.rt_tables(da, rr)
    id_tab = rolling.intraday_tables(da, rr)
    st = settlement.settle(da, rr)
    t_set = time.perf_counter() - t1
    return dict(da=da, rr=rr, rt=rt_tab, id=id_tab, settle=st, checks=checks(da, rr, rt_tab),
                time_s=dict(da=t_da, intraday=rr.time_s["intraday"], realtime=rr.time_s["realtime"], settlement=t_set,
                            total=time.perf_counter() - t0))


def summarize_full(res) -> dict:
    da, st, rt = res["da"], res["settle"], res["rt"]
    cfg = da.case.cfg
    T = len(res["rr"].rt)
    pda, prt = st["prices"]["da"], st["prices"]["rt"]
    net = da.case.net
    load_da = np.array([[da.case.load_bus.get(b, np.zeros(cfg.T))[t] for t in range(T)] for b in net.buses])
    load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
    wda = (pda * load_da).sum(0) / load_da.sum(0)
    wrt = (prt * load_rt).sum(0) / load_rt.sum(0)
    sp = prt - pda
    bt = st["by_type"].set_index("type")
    fe = bt.reindex(["load", "wind", "pv"]).fillna(0)
    s = dict(
        rule_set=cfg.rule_set, rule_name=cfg.mode["name"], date=cfg.base["date"], season=cfg.base["season"], periods=T,
        intraday=dict(enabled=bool(cfg.mode["intraday"].get("enabled", False)), interval_h=cfg.mode["intraday"]["interval_h"], rounds=len(res["rr"].id_rounds),
                      lock_types=cfg.mode["intraday"]["lock_types"]),
        realtime=dict(interval_min=cfg.mode["realtime"]["interval_min"], lookahead_h=cfg.mode["realtime"]["lookahead_h"],
                      storage_mode=cfg.mode["realtime"]["storage"]["mode"], anchor=cfg.mode["realtime"]["storage"]["anchor"],
                      x=cfg.mode["realtime"]["storage"]["x"], hydro=cfg.mode["realtime"].get("hydro", "fixed_da"),
                      anchor_relaxed_periods=res["checks"]["anchor_relaxed_periods"],
                      anchor_slack_max_mwh=res["checks"]["anchor_slack_max_mwh"],
                      repriced_periods=int(rt["system"].repriced.sum()),
                      shed_mwh=float(rt["system"].shed_mw.sum() * cfg.dt), surplus_mwh=float(rt["system"].surplus_mw.sum() * cfg.dt),
                      curtail_mwh=float(rt["system"].curtail_mw.sum() * cfg.dt)),
        da=dict(objective=da.scuc_info["objective"], gap=da.scuc_info["gap"],
                curtail_mwh=float(da.tables["units"].curtail_mw.sum() * cfg.dt)),
        spread=dict(da_lw_avg=float((wda * load_da.sum(0)).sum() / load_da.sum()),
                    rt_lw_avg=float((wrt * load_rt.sum(0)).sum() / load_rt.sum()),
                    lw_spread_mean=float((wrt - wda).mean()), lw_spread_abs_mean=float(np.abs(wrt - wda).mean()),
                    lw_spread_max=float((wrt - wda).max()), lw_spread_min=float((wrt - wda).min()),
                    nodal_spread_abs_max=float(np.abs(sp).max()), nodal_spread_rms=float(np.sqrt((sp ** 2).mean())),
                    rt_lmp_min=float(prt.min()), rt_lmp_max=float(prt.max()),
                    da_lmp_min=float(pda.min()), da_lmp_max=float(pda.max()),
                    da_lw_tail=float((wda[84:] * load_da.sum(0)[84:]).sum() / load_da.sum(0)[84:].sum()) if T > 84 else float("nan"),
                    rt_lw_tail=float((wrt[84:] * load_rt.sum(0)[84:]).sum() / load_rt.sum(0)[84:].sum()) if T > 84 else float("nan"),
                    rt_lw_hourly=[float(x) for x in (wrt[: T // 4 * 4].reshape(-1, 4).mean(1))],
                    da_lw_hourly=[float(x) for x in (wda[: T // 4 * 4].reshape(-1, 4).mean(1))]),
        settlement_by_type={k: {c: float(v) for c, v in r.items()} for k, r in bt.iterrows()},
        forecast_error=dict(dev_abs_mwh=float(fe.dev_abs_mwh.sum()), rt_amount=float(fe.rt_amount.sum()),
                            dev_pnl=float(fe.dev_pnl.sum()),
                            by_type={k: dict(dev_mwh=float(fe.loc[k, "dev_mwh"]), dev_abs_mwh=float(fe.loc[k, "dev_abs_mwh"]),
                                             rt_amount=float(fe.loc[k, "rt_amount"]), dev_pnl=float(fe.loc[k, "dev_pnl"]))
                                     for k in fe.index}),
        reconciliation=st["reconciliation"].set_index("stage").to_dict(orient="index"),
        checks=res["checks"], time_s=res["time_s"],
    )
    return s


def write_full_outputs(res, d: Path, make_plots=True) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    da, st, rt, idt = res["da"], res["settle"], res["rt"], res["id"]
    cfg = da.case.cfg
    f = dict(index=False, float_format="%.4f")
    da.tables["units"].to_csv(d / "da_unit_awards.csv", **f)
    da.tables["prices"].to_csv(d / "da_nodal_prices.csv", **f)
    da.tables["storage"].to_csv(d / "da_storage.csv", **f)
    idt["summary"].to_csv(d / "id_rounds_summary.csv", **f)
    idt["plans"].to_csv(d / "id_plans.csv", **f)
    idt["prices"].to_csv(d / "id_prices_indicative.csv", **f)
    idt["storage"].to_csv(d / "id_storage.csv", **f)
    for k in ("units", "prices", "lines", "storage", "system"):
        rt[k].to_csv(d / f"rt_{k}.csv", **f)
    rt["prices"].pivot(index="bus", columns="period", values="lmp").to_csv(d / "rt_lmp_matrix.csv", float_format="%.2f")
    st["detail"].to_csv(d / "settlement_detail.csv", **f)
    st["by_entity"].to_csv(d / "settlement_by_entity.csv", **f)
    st["by_type"].to_csv(d / "settlement_by_type.csv", **f)
    st["reconciliation"].to_csv(d / "settlement_reconciliation.csv", **f)
    st["line_rent"].to_csv(d / "settlement_line_rent.csv", **f)
    s = summarize_full(res)
    s["da_summary"] = summarize(da)
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    if make_plots:
        net = da.case.net
        T = s["periods"]
        load_da = np.array([[da.case.load_bus.get(b, np.zeros(96))[t] for t in range(T)] for b in net.buses])
        load_rt = rt["prices"].pivot(index="bus", columns="period", values="load_mw").reindex(net.buses).to_numpy()
        plots.da_vs_rt_lmp(st["prices"]["da"], st["prices"]["rt"], load_da, load_rt, list(net.buses), d / "lmp_da_vs_rt.png")
        plots.rolling_plans(idt["plans"], idt["storage"], rt["units"], rt["storage"], da.tables["units"], da.tables["storage"],
                            d / "rolling_plans.png", x=cfg.mode["realtime"]["storage"]["x"]
                            if cfg.mode["realtime"]["storage"]["mode"] == "sced_anchor" and cfg.mode["realtime"]["storage"]["anchor"] == "window_end_pct" else None)
        plots.settlement_bars(st["by_type"], st["by_entity"], d / "settlement_bars.png")
    return s
