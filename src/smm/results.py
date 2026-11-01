"""从求解后的 Pyomo 模型提取出清结果表（DataFrame）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyomo.environ as pyo

V = pyo.value


def _times(T):
    return [f"{(t*15)//60:02d}:{(t*15)%60:02d}" for t in range(T)]


def unit_results(m, prices) -> pd.DataFrame:
    md, case = m._md, m._case
    T, dt = md.T, md.dt
    bidx = {b: i for i, b in enumerate(md.buses)}
    lmp = prices["lmp_clamped"]
    rows = []
    for g, d in md.thermal.items():
        for t in range(T):
            P = V(m.P[g, t])
            rows.append(dict(period=t, unit=g, type=d["type"], bus=d["bus"], P_mw=P, on=round(V(m.u[g, t])),
                             startup=round(V(m.v[g, t])), r_up=V(m.r_up[g, t]), r_dn=V(m.r_dn[g, t]),
                             avail_mw=d["pmax"] * round(V(m.u[g, t]))))
    for w, d in md.ren.items():
        for t in range(T):
            rows.append(dict(period=t, unit=w, type=d["type"], bus=d["bus"], P_mw=V(m.Pw[w, t]), on=1, startup=0,
                             r_up=0.0, r_dn=0.0, avail_mw=float(d["fc"][t])))
    for h, d in md.hydro.items():
        for t in range(T):
            P = sum(V(m.Ph[j, t]) for j in d["units"])
            non = sum(round(V(m.delta[j, z, t])) for j in d["units"] for z in range(len(d["zones"])))
            rows.append(dict(period=t, unit=h, type="hydro", bus=d["bus"], P_mw=P, on=non, startup=0,
                             r_up=sum(V(m.rh_up[j, t]) for j in d["units"]), r_dn=sum(V(m.rh_dn[j, t]) for j in d["units"]),
                             avail_mw=d["unit_pmax"] * non))
    for s, d in md.storage.items():
        for t in range(T):
            rows.append(dict(period=t, unit=s, type="storage", bus=d["bus"], P_mw=V(m.Pd[s, t]) - V(m.Pc[s, t]), on=1,
                             startup=0, r_up=V(m.rs_up[s, t]), r_dn=V(m.rs_dn[s, t]), avail_mw=d["pmax"]))
    for tid, (b, sch) in case.tie.items():
        for t in range(T):
            rows.append(dict(period=t, unit=tid, type="tie", bus=b, P_mw=float(sch[t]), on=1, startup=0, r_up=0.0,
                             r_dn=0.0, avail_mw=float(sch[t])))
    df = pd.DataFrame(rows)
    df["time"] = df.period.map(dict(enumerate(_times(T))))
    df["lmp"] = [lmp[bidx[b], t] for b, t in zip(df.bus, df.period)]
    df["energy_mwh"] = df.P_mw * dt
    df["revenue_yuan"] = df.energy_mwh * df.lmp
    if "ren_fc" in dir(case):
        pass
    df["curtail_mw"] = np.where(df.type.isin(["wind", "pv"]), df.avail_mw - df.P_mw, 0.0)
    return df[["period", "time", "unit", "type", "bus", "P_mw", "on", "startup", "r_up", "r_dn", "avail_mw",
               "curtail_mw", "lmp", "energy_mwh", "revenue_yuan"]]


def price_tables(m, prices, clear_min, clear_max):
    md = m._md
    T = md.T
    cols = _times(T)
    lmp = pd.DataFrame(prices["lmp"], index=md.buses, columns=range(T))
    lmp.index.name = "bus"
    long = []
    for i, b in enumerate(md.buses):
        for t in range(T):
            long.append(dict(period=t, time=cols[t], bus=b, lmp_raw=prices["lmp"][i, t], lmp=prices["lmp_clamped"][i, t],
                             energy=prices["energy"][t], congestion=prices["congestion"][i, t],
                             lmp_decomp=prices["lmp_decomp"][i, t]))
    return pd.DataFrame(long)


def line_results(m, prices) -> pd.DataFrame:
    md, case = m._md, m._case
    rate = dict(zip(case.net.branches.branch_id, case.net.branches.rate_mw))
    rows = []
    for i, l in enumerate(md.lines):
        for t in range(md.T):
            f = V(m.flow[l, t])
            rows.append(dict(period=t, line=l, flow_mw=f, rate_mw=rate[l], loading=abs(f) / rate[l],
                             mu_plus=prices["mu_plus"][i, t], mu_minus=prices["mu_minus"][i, t], slack_mw=V(m.sl[l, t])))
    df = pd.DataFrame(rows)
    df["congested"] = (df.mu_plus.abs() + df.mu_minus.abs() > 1e-6) | (df.loading >= 1 - 1e-6)
    return df


def storage_results(m) -> pd.DataFrame:
    md = m._md
    rows = []
    for s, d in md.storage.items():
        for t in range(md.T):
            rows.append(dict(period=t, unit=s, bus=d["bus"], P_dis=V(m.Pd[s, t]), P_ch=V(m.Pc[s, t]), E_mwh=V(m.E[s, t]),
                             soc=V(m.E[s, t]) / d["ecap"]))
    return pd.DataFrame(rows)


def hydro_results(m) -> pd.DataFrame:
    md = m._md
    rows = []
    for h, d in md.hydro.items():
        for t in range(md.T):
            rows.append(dict(period=t, station=h, P_mw=sum(V(m.Ph[j, t]) for j in d["units"]),
                             units_on=sum(round(V(m.delta[j, z, t])) for j in d["units"] for z in range(len(d["zones"]))),
                             V_1e4m3=V(m.V[h, t]), Q_spill=V(m.Qspill[h, t]),
                             Q_gen=d["water_rate"] * sum(V(m.Ph[j, t]) for j in d["units"])))
    return pd.DataFrame(rows)


def system_results(m, prices) -> pd.DataFrame:
    md, case = m._md, m._case
    T = md.T
    rows = []
    for t in range(T):
        rows.append(dict(period=t, load_mw=float(sum(v[t] for v in case.load_bus.values())),
                         R_up_req=float(case.R_up[t]), R_dn_req=float(case.R_dn[t]),
                         R_up_short=V(m.sR_up[t]), R_dn_short=V(m.sR_dn[t]),
                         shed_mw=sum(V(m.shed[n, t]) for n in m.N), surplus_mw=V(m.surplus[t]),
                         energy_price=prices["energy"][t], reserve_up_price=prices["reserve_up_price"][t],
                         reserve_dn_price=prices["reserve_dn_price"][t]))
    df = pd.DataFrame(rows)
    df.insert(1, "time", _times(T))
    return df


def cost_breakdown(m) -> dict:
    md = m._md
    dt = md.dt
    out = {}
    out["thermal_energy"] = sum(md.thermal[g]["c"][k] * V(m.p[g, k, t]) * dt for (g, k) in m.GK for t in m.T)
    out["thermal_noload"] = sum(md.thermal[g]["noload_bid"] * V(m.u[g, t]) * dt for g in m.G for t in m.T)
    out["thermal_startup"] = sum(dict((s, c) for s, _, c in md.thermal[g]["states"])[s] * V(m.y[g, s, t]) for (g, s) in m.GST for t in m.T) \
        + sum(md.thermal[g]["cold"] * V(m.ycold[g, t]) for g in m.G for t in m.T)
    out["renewable"] = sum(md.ren[w]["c"][k] * V(m.pw[w, k, t]) * dt for (w, k) in m.WK for t in m.T)
    out["hydro"] = sum(md.hydro_units[j]["c"][k] * V(m.ph[j, k, t]) * dt for (j, k) in m.JK for t in m.T)
    out["hydro_spill_penalty"] = sum(md.hydro[h]["spill_penalty"] * V(m.Qspill[h, t]) * dt for h in m.HS for t in m.T)
    out["storage_net"] = sum(md.storage[s]["cd"][k] * V(m.pd[s, k, t]) * dt for (s, k) in m.SD for t in m.T) \
        - sum(md.storage[s]["cc"][k] * V(m.pc[s, k, t]) * dt for (s, k) in m.SC for t in m.T)
    out["bid_cost_total"] = sum(out.values())
    out["objective_incl_penalties"] = V(m.obj)
    return out


def clamp_prices(prices: dict, lo: float, hi: float) -> dict:
    prices = dict(prices)
    prices["lmp_clamped"] = np.clip(prices["lmp"], lo, hi)
    return prices
