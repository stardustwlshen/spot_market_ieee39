"""两级偏差结算：日前电量×日前 LMP + (实时−日前)×实时 LMP（按 15 min、按节点）。

符号：q > 0 为向电网注入（发电、放电），q < 0 为从电网取用（负荷、充电）。金额 > 0 为收入、< 0 为支出。
主体：火电/燃气/水电/风光逐台，储能放电与充电分列，负荷按节点（实际用电 = 负荷 − 切负荷），区外联络线（计划固定、无偏差），
      “弃电/过剩”松弛作为平衡伪主体。
对账：市场盈余 MS = −Σ金额。由 Σ_n q_n = 0 与 π = λ − PTDFᵀ(μ⁺−μ⁻)：
      日前 MS_DA = Σ_l (μ⁺−μ⁻)_DA·F_DA·Δt（阻塞盈余） + 限价修正；
      实时 MS_RT = Σ_l (μ⁺−μ⁻)_RT·(F_RT − F_DA)·Δt + 限价修正。
      限价修正 = −Σ (π_clamped − π_raw)·q·Δt；残差应 ≈ 0。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .rolling import snapshot


def _entities(cfg, case, sn, t_index, T):
    """sn = 一次 LP 的快照；t_index(t) → 快照中的列下标。返回 rows(entity,type,bus,period,q)。"""
    rows = []
    th = {u["id"]: u for u in cfg.units["thermal"]}
    ren = {r["id"]: r for r in cfg.units["renewable"]}
    hy = {h["id"]: h for h in cfg.units["hydro"]}
    sto = {s["id"]: s for s in cfg.units["storage"]}
    for t in range(T):
        sn_t, k = t_index(t)
        for g, u in th.items():
            rows.append(("%s" % g, u["type"], u["bus"], t, sn_t["P"][g][k]))
        for w, r in ren.items():
            rows.append((w, r["type"], r["bus"], t, sn_t["Pw"][w][k]))
        for h, d in hy.items():
            rows.append((h, "hydro", d["bus"], t, sn_t["Ph"][h][k]))
        for s, d in sto.items():
            rows.append((f"{s}_放电", "storage_dis", d["bus"], t, sn_t["Pd"][s][k]))
            rows.append((f"{s}_充电", "storage_ch", d["bus"], t, -sn_t["Pc"][s][k]))
        for tid, (b, sch) in case.tie.items():
            rows.append((tid, "tie", b, t, float(sch[t])))
        for n, lb in sn_t["load_bus"].items():
            if lb[k] > 0 or sn_t["shed"][n][k] > 0:
                rows.append((f"L{n}", "load", n, t, -(lb[k] - sn_t["shed"][n][k])))
        rows.append(("平衡松弛(过剩)", "slack", case.net.ref_bus, t, -sn_t["surplus"][k]))
    return pd.DataFrame(rows, columns=["entity", "type", "bus", "period", "q"])


def settle(da_res, rr) -> dict:
    case = da_res.case
    cfg, net = case.cfg, case.net
    dt = cfg.dt
    T = len(rr.rt)
    da_sn = snapshot(da_res.model)
    q_da = _entities(cfg, case, da_sn, lambda t: (da_sn, t), T)
    q_rt = _entities(cfg, case, None, lambda t: (rr.rt[t]["snap"], 0), T)
    df = q_da.rename(columns={"q": "q_da"}).merge(q_rt.rename(columns={"q": "q_rt"}),
                                                    on=["entity", "type", "bus", "period"], how="outer").fillna(0.0)
    bidx = {b: i for i, b in enumerate(net.buses)}
    pda, pda_raw = da_res.prices["lmp_clamped"], da_res.prices["lmp"]
    prt = np.stack([r["prices"]["lmp_clamped"][:, 0] for r in rr.rt], axis=1)
    prt_raw = np.stack([r["prices"]["lmp"][:, 0] for r in rr.rt], axis=1)
    bi = df.bus.map(bidx).to_numpy()
    tt = df.period.to_numpy()
    df["lmp_da"] = pda[bi, tt]
    df["lmp_rt"] = prt[bi, tt]
    df["dq"] = df.q_rt - df.q_da
    df["da_amount"] = df.q_da * df.lmp_da * dt
    df["rt_amount"] = df.dq * df.lmp_rt * dt
    df["total"] = df.da_amount + df.rt_amount
    df["dev_pnl"] = df.dq * (df.lmp_rt - df.lmp_da) * dt           # 偏差损益：相对“偏差电量按日前价结算”的得失
    df["clamp_da"] = -df.q_da * (pda[bi, tt] - pda_raw[bi, tt]) * dt
    df["clamp_rt"] = -df.dq * (prt[bi, tt] - prt_raw[bi, tt]) * dt

    agg = dict(q_da=("q_da", lambda x: x.sum() * dt), q_rt=("q_rt", lambda x: x.sum() * dt),
               dev_mwh=("dq", lambda x: x.sum() * dt), dev_abs_mwh=("dq", lambda x: x.abs().sum() * dt),
               da_amount=("da_amount", "sum"), rt_amount=("rt_amount", "sum"), total=("total", "sum"),
               dev_pnl=("dev_pnl", "sum"))
    by_entity = df.groupby(["entity", "type"]).agg(**agg).reset_index()
    by_entity["avg_price"] = by_entity.total / by_entity.q_rt.where(by_entity.q_rt.abs() > 1e-9)
    by_type = df.groupby("type").agg(**agg).reset_index()
    by_type["avg_price"] = by_type.total / by_type.q_rt.where(by_type.q_rt.abs() > 1e-9)

    # ---- 对账
    ptdf = net.ptdf
    L = list(net.branches.branch_id)
    inj_da = np.zeros((len(net.buses), T))
    inj_rt = np.zeros((len(net.buses), T))
    np.add.at(inj_da, (bi, tt), df.q_da.to_numpy())
    np.add.at(inj_rt, (bi, tt), df.q_rt.to_numpy())
    F_da, F_rt = ptdf @ inj_da, ptdf @ inj_rt
    mu_da = (da_res.prices["mu_plus"] - da_res.prices["mu_minus"])[:, :T]
    mu_rt = np.stack([r["prices"]["mu_plus"][:, 0] - r["prices"]["mu_minus"][:, 0] for r in rr.rt], axis=1)
    rec = []
    for stage, amt, cong, clamp, bal in (
            ("日前", df.da_amount.sum(), float((mu_da * F_da).sum() * dt), df.clamp_da.sum(), np.abs(inj_da.sum(0)).max()),
            ("实时偏差", df.rt_amount.sum(), float((mu_rt * (F_rt - F_da)).sum() * dt), df.clamp_rt.sum(), np.abs(inj_rt.sum(0)).max())):
        ms = -amt
        rec.append(dict(stage=stage, merchandising_surplus=ms, congestion_rent=cong, clamp_adjustment=clamp,
                        residual=ms - cong - clamp, max_power_imbalance_mw=bal))
    r = pd.DataFrame(rec)
    tot = r.drop(columns=["stage"]).sum()
    tot["max_power_imbalance_mw"] = r.max_power_imbalance_mw.max()
    r.loc[len(r)] = dict(stage="合计", **tot.to_dict())
    lines = pd.DataFrame([dict(line=l, rent_da=float((mu_da[i] * F_da[i]).sum() * dt),
                               rent_rt=float((mu_rt[i] * (F_rt[i] - F_da[i])).sum() * dt)) for i, l in enumerate(L)])
    lines = lines[(lines.rent_da.abs() > 1e-6) | (lines.rent_rt.abs() > 1e-6)]
    return dict(detail=df, by_entity=by_entity, by_type=by_type, reconciliation=r, line_rent=lines,
                prices=dict(da=pda[:, :T], rt=prt))
