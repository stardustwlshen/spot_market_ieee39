"""从求解后的 Pyomo 模型提取出清结果表（DataFrame）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyomo.environ as pyo

V = pyo.value


def _times(T, dt=.25):
    from .time_grid import period_label
    return [period_label(t, dt) for t in range(T)]


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
    for p, d in md.pumped.items():
        for t in range(T):
            ng, npm = round(V(m.ngen[p, t])), round(V(m.npump[p, t]))
            rows.append(dict(period=t, unit=p, type="pumped", bus=d["bus"], P_mw=V(m.Pg[p, t]) - V(m.Pp[p, t]), on=ng + npm,
                             startup=round(V(m.sg[p, t]) + V(m.sp[p, t])), r_up=V(m.rp_up[p, t]), r_dn=V(m.rp_dn[p, t]),
                             avail_mw=d["unit_gen_max"] * d["N"]))
    for tid, (b, sch) in case.tie.items():
        for t in range(T):
            rows.append(dict(period=t, unit=tid, type="tie", bus=b, P_mw=float(sch[t]), on=1, startup=0, r_up=0.0,
                             r_dn=0.0, avail_mw=float(sch[t])))
    df = pd.DataFrame(rows)
    reserve_rows=reserve_snapshot(m)["resources"]
    eligibility={(x["unit"],x["relative_period"]):x["reserve_eligible"] for x in reserve_rows}
    df["reserve_formulation"]="period_end_stock"
    df["reserve_eligible"]=[bool(eligibility.get((r.unit,r.period),False)) for r in df.itertuples()]
    df["time"] = df.period.map(dict(enumerate(_times(T, dt))))
    df["lmp"] = [lmp[bidx[b], t] for b, t in zip(df.bus, df.period)]
    df["energy_mwh"] = df.P_mw * dt
    df["revenue_yuan"] = df.energy_mwh * df.lmp
    df["curtail_mw"] = np.where(df.type.isin(["wind", "pv"]), df.avail_mw - df.P_mw, 0.0)
    return df[["period", "time", "unit", "type", "bus", "P_mw", "on", "startup", "r_up", "r_dn", "avail_mw",
               "curtail_mw", "lmp", "energy_mwh", "revenue_yuan", "reserve_eligible", "reserve_formulation"]]


def price_tables(m, prices, clear_min, clear_max, *, dispatch_prices=None, pricing_source="standard_sced"):
    md = m._md
    T = md.T
    cols = _times(T, getattr(md, "dt", .25))
    dispatch_prices = prices if dispatch_prices is None else dispatch_prices
    lmp = pd.DataFrame(prices["lmp"], index=md.buses, columns=range(T))
    lmp.index.name = "bus"
    long = []
    for i, b in enumerate(md.buses):
        for t in range(T):
            long.append(dict(period=t, time=cols[t], bus=b,
                             load_mw=float(m._case.load_bus.get(b, np.zeros(T))[t]),
                             fixed_load_mw=float(sum(v[t] for bus, v in m._case.fixed_load.values() if bus == b)),
                             shed_mw=V(m.shed[b, t]), inj_mw=V(m.inj[b, t]) if hasattr(m, "inj") else np.nan,
                             served_load_mw=float(m._case.load_bus.get(b, np.zeros(T))[t])-V(m.shed[b,t]), lmp_raw=prices["lmp"][i, t], lmp=prices["lmp_clamped"][i, t],
                             energy=prices["energy"][t], congestion=prices["congestion"][i, t],
                             lmp_decomp=prices["lmp_decomp"][i, t],
                             dispatch_raw_lmp=dispatch_prices["lmp"][i,t],
                             dispatch_energy=dispatch_prices["energy"][t],
                             dispatch_congestion=dispatch_prices["congestion"][i,t],
                             pricing_source=pricing_source, raw_price_source=pricing_source,
                             settlement_price_source=pricing_source+"_clipped_to_market_limits",
                             physical_quantity_source="standard_sced"))
    return pd.DataFrame(long)


def line_results(m, prices, *, dual_source="standard_sced") -> pd.DataFrame:
    md, case = m._md, m._case
    rate = dict(zip(case.net.branches.branch_id, case.net.branches.rate_mw))
    rows = []
    for i, l in enumerate(md.lines):
        for t in range(md.T):
            f = V(m.flow[l, t])
            rows.append(dict(period=t, line=l, flow_mw=f, rate_mw=rate[l], loading=abs(f) / rate[l],
                             mu_plus=prices["mu_plus"][i, t], mu_minus=prices["mu_minus"][i, t], slack_mw=V(m.sl[l, t]),
                             flow_source="standard_sced",dual_source=dual_source))
    df = pd.DataFrame(rows)
    df["dispatch_at_capacity"] = df.loading >= 1 - 1e-6
    df["pricing_dual_active"] = df.mu_plus.abs() + df.mu_minus.abs() > 1e-6
    df["congested"] = df.pricing_dual_active | df.dispatch_at_capacity  # legacy union; distinct sources above
    return df


def storage_results(m) -> pd.DataFrame:
    md = m._md
    rows = []
    for s, d in md.storage.items():
        for t in range(md.T):
            rows.append(dict(period=t, unit=s, bus=d["bus"], P_dis=V(m.Pd[s, t]), P_ch=V(m.Pc[s, t]),
                             u_ch=int(round(V(m.u_ch[s, t]))), u_dis=int(round(V(m.u_dis[s, t]))),
                             E_start=float(d["soc0"] * d["ecap"]) if t == 0 else V(m.E[s,t-1]), E_mwh=V(m.E[s, t]),
                             soc=V(m.E[s, t]) / d["ecap"],reserve_formulation="period_end_stock"))
    return pd.DataFrame(rows, columns=["period", "unit", "bus", "P_dis", "P_ch", "u_ch", "u_dis", "E_start", "E_mwh", "soc", "reserve_formulation"])


def pumped_results(m) -> pd.DataFrame:
    """抽蓄：发电/抽水功率、工况、台数、上/下库库容（万m3）与上库折算电量（MWh，按发电转换系数）。"""
    md = m._md
    rows = []
    for p, d in md.pumped.items():
        for t in range(md.T):
            xg, xp = int(round(V(m.xg[p, t]))), int(round(V(m.xp[p, t])))
            Vu = V(m.Vu[p, t])
            rows.append(dict(period=t, unit=p, bus=d["bus"], P_gen=V(m.Pg[p, t]), P_pump=V(m.Pp[p, t]),
                             n_gen=int(round(V(m.ngen[p, t]))), n_pump=int(round(V(m.npump[p, t]))),
                             mode="发电" if xg else ("抽水" if xp else "停机"), V_start=float(d["init"]["V"]) if t == 0 else V(m.Vu[p,t-1]), V_up=Vu, V_dn=float(d["v_total"]) - Vu,
                             E_equiv_mwh=(Vu - d["v_lo"]) * d["e_gen"],reserve_formulation="period_end_stock"))
    return pd.DataFrame(rows, columns=["period", "unit", "bus", "P_gen", "P_pump", "n_gen", "n_pump", "mode", "V_start", "V_up", "V_dn", "E_equiv_mwh", "reserve_formulation"])


def hydro_results(m) -> pd.DataFrame:
    from .natural_inflow import model_natural
    md = m._md
    selected = getattr(getattr(m, "_case", None), "selected_inputs", None)
    rows = []
    for h, d in md.hydro.items():
        limit = d.get("qout_max")
        limit = None if limit is None else float(limit)
        for t in range(md.T):
            qout = float(d["water_rate"] * sum(V(m.Ph[j,t]) for j in d["units"]) + V(m.Qspill[h,t]))
            rows.append(dict(period=t, station=h, P_mw=sum(V(m.Ph[j, t]) for j in d["units"]),
                             units_on=sum(round(V(m.delta[j, z, t])) for j in d["units"] for z in range(len(d["zones"]))),
                             V_start=float(d.get("v0", np.nan)) if t == 0 else V(m.V[h,t-1]), V_1e4m3=V(m.V[h, t]), Q_spill=V(m.Qspill[h, t]),
                             Q_gen=d["water_rate"] * sum(V(m.Ph[j, t]) for j in d["units"]),
                             Q_in=model_natural(md,h,t) + float(V(m._upstream_out(h, t))) if hasattr(m, "_upstream_out") else model_natural(md,h,t),
                             Q_natural=model_natural(md,h,t), Q_upstream=float(V(m._upstream_out(h,t))) if hasattr(m,'_upstream_out') else 0.,
                             natural_input_source=(selected.sources[t] if selected is not None else 'legacy_season_constant'),
                             natural_forecast_id=(selected.forecast_id if selected is not None else None),
                             natural_issue_time_utc=(selected.issue_time_utc if selected is not None else None),
                             regulation=d.get("regulation", "reservoir"),
                             qout_m3s=qout, qout_max_m3s=limit,
                             qout_max_source=d.get("qout_max_source"),
                             qout_limit_active=limit is not None,
                             qout_limit_margin_m3s=None if limit is None else limit-qout))
    return pd.DataFrame(rows, columns=["period", "station", "P_mw", "units_on", "V_start", "V_1e4m3", "Q_spill", "Q_gen", "Q_in", "Q_natural", "Q_upstream", "natural_input_source", "natural_forecast_id", "natural_issue_time_utc", "regulation", "qout_m3s", "qout_max_m3s", "qout_max_source", "qout_limit_active", "qout_limit_margin_m3s"])


def system_results(m, prices) -> pd.DataFrame:
    md, case = m._md, m._case
    T = md.T
    rows = []
    for t in range(T):
        rows.append(dict(period=t, load_mw=float(sum(v[t] for v in case.load_bus.values())),
                         R_up_req=float(case.R_up[t]), R_dn_req=float(case.R_dn[t]),
                         R_up_short=V(m.sR_up[t]), R_dn_short=V(m.sR_dn[t]),
                         shed_mw=sum(V(m.shed[n, t]) for n in m.N), surplus_mw=V(m.surplus[t]),
                         fixed_load_mw=float(sum(v[t] for _,v in case.fixed_load.values())),
                         curtail_mw=sum(float(d["fc"][t])-V(m.Pw[w,t]) for w,d in md.ren.items()),
                         energy_price=prices["energy"][t], reserve_up_price=prices["reserve_up_price"][t],
                         reserve_dn_price=prices["reserve_dn_price"][t]))
    df = pd.DataFrame(rows)
    reserve_system=pd.DataFrame(reserve_snapshot(m)["system"])
    extra=["reserve_down_enabled","R_up_supply","R_dn_supply","R_up_re_credit_mw","R_up_balance_mw","R_dn_balance_mw"]
    for name in extra:df[name]=reserve_system[name].to_numpy()
    df.insert(1, "time", _times(T, md.dt))
    return df


def cost_breakdown(m) -> dict:
    md = m._md
    dt = md.dt
    out = {}
    out["thermal_energy"] = sum(md.thermal[g]["c"][k] * V(m.p[g, k, t]) * dt for (g, k) in m.GK for t in m.T)
    out["thermal_noload"] = sum(md.thermal[g]["noload_bid"] * V(m.u[g, t]) * dt for g in m.G for t in m.T)
    from .scuc import thermal_startup_cost
    out["thermal_startup"] = V(thermal_startup_cost(m))
    out["renewable"] = sum(md.ren[w]["c"][k] * V(m.pw[w, k, t]) * dt for (w, k) in m.WK for t in m.T)
    out["hydro"] = sum(md.hydro_units[j]["c"][k] * V(m.ph[j, k, t]) * dt for (j, k) in m.JK for t in m.T)
    # Retained numeric output key for old report readers; no spill penalty is charged.
    out["hydro_spill_penalty"] = 0.0
    out["storage_net"] = sum(md.storage[s]["cd"][k] * V(m.pd[s, k, t]) * dt for (s, k) in m.SD for t in m.T) \
        - sum(md.storage[s]["cc"][k] * V(m.pc[s, k, t]) * dt for (s, k) in m.SC for t in m.T)
    out["pumped_net"] = sum(md.pumped[p]["cg"][k] * V(m.pg[p, k, t]) * dt for (p, k) in m.PGK for t in m.T) \
        - sum(md.pumped[p]["cp"][k] * V(m.pp[p, k, t]) * dt for (p, k) in m.PPK for t in m.T) \
        + sum(md.pumped[p]["start_cost_gen"] * V(m.sg[p, t]) + md.pumped[p]["start_cost_pump"] * V(m.sp[p, t]) for p in m.PS for t in m.T)
    out["bid_cost_total"] = sum(out.values())  # legacy energy bids only, not regulation procurement payment
    spec = getattr(m, "_agc", {})
    if spec.get("enabled"):
        out["regulation_performance_adjusted_score_cost"] = sum(
            d["bid_yuan_per_mw"] / d["performance"] * spec["mileage_ratio"] * V(m.q_agc[u, t])
            for u, d in spec["resources"].items() for t in m.T)
        out["regulation_shortage_penalty"] = spec["shortage_penalty"] * sum(V(m.s_agc[t]) for t in m.T)
    else:
        out["regulation_performance_adjusted_score_cost"] = 0.0
        out["regulation_shortage_penalty"] = 0.0
    tracking = tracking_results(m)
    for kind, label in (("E", "storage"), ("Vu", "pumped"), ("V", "hydro")):
        out[f"{label}_tracking_penalty_yuan"] = float(tracking.loc[tracking.kind == kind, "penalty_yuan"].sum())
    out["objective_incl_penalties"] = V(m.obj)
    return out


def clamp_prices(prices: dict, lo: float, hi: float) -> dict:
    from .input_validation import validate_clearing_limits
    lo, hi = validate_clearing_limits(dict(clear_min=lo, clear_max=hi))
    prices = dict(prices)
    prices["lmp_clamped"] = np.clip(prices["lmp"], lo, hi)
    return prices


def pumped_summary(pdf: pd.DataFrame, prices: pd.DataFrame, dt: float) -> dict:
    """抽蓄日统计：发电/抽水电量、综合效率、发电/抽水小时数、按节点电价的净收入与加权均价。prices 为长表（period, bus, lmp）。"""
    out = {}
    if pdf is None or pdf.empty:
        return out
    df = pdf.merge(prices[["period", "bus", "lmp"]], on=["period", "bus"], how="left")
    for u, g in df.groupby("unit"):
        eg, ep = g.P_gen.sum() * dt, g.P_pump.sum() * dt
        rg, rp = (g.P_gen * g.lmp).sum() * dt, (g.P_pump * g.lmp).sum() * dt
        out[u] = dict(gen_mwh=float(eg), pump_mwh=float(ep), cycle_eff=float(eg / ep) if ep > 1e-6 else None,
                      gen_hours=float((g.P_gen > 1e-3).sum() * dt), pump_hours=float((g.P_pump > 1e-3).sum() * dt),
                      gen_revenue=float(rg), pump_cost=float(rp), net_revenue=float(rg - rp),
                      avg_gen_price=float(rg / eg) if eg > 1e-6 else None, avg_pump_price=float(rp / ep) if ep > 1e-6 else None,
                      V_up_start=float(g.V_start.iloc[0]) if "V_start" in g else None, V_up_end=float(g.V_up.iloc[-1]))
    return out


def reserve_snapshot(m) -> dict:
    """Detached full-window reserve evidence; no activation or extra payment.

    Battery activation may cross zero/idle; inverter response is idealized. Both
    period-end stock E[t]/Vu[t] bounds the incremental reserve energy for H (round-2 item F).
    This is NOT a baseline-plus-activation delivery guarantee.
    Pumped reserve is generation-only. Reported margins are independently auditable.
    """
    md, case = m._md, m._case
    cfg=case.cfg; rc=cfg.mode['reserve']; enabled=bool(rc.get('storage_reserve',False)); down_enabled=bool(rc.get('down_enabled',True))
    hydro_water_enabled=bool(m._hydro_reserve_water_enabled)
    H=m._reserve_duration_h; providers=set(rc['providers']); rows=[]; systems=[]
    def row(uid,typ,t,up,dn,power,eligible,pmin,pmax,mode):
        return dict(period=case.t0+int(t),relative_period=int(t),unit=uid,type=typ,
                    reserve_formulation='period_end_stock',reserve_eligible=bool(eligible),reserve_qualified=None,reserve_down_enabled=down_enabled,r_up=float(up),r_dn=float(dn),P_mw=float(power),
                    pmin_mw=float(pmin),pmax_mw=float(pmax),mode=mode,T_dur_h=H,
                    reserve_power_up_margin_mw=float(pmax-power-up) if eligible else None,
                    reserve_power_down_margin_mw=float(power-pmin-dn) if eligible else None,
                    reserve_duration_up_margin_mwh=None,reserve_duration_down_margin_mwh=None,
                    reserve_duration_up_margin_1e4m3=None,reserve_duration_down_margin_1e4m3=None)
    for t in m.T:
        for uid,d in md.thermal.items():
            on=int(round(V(m.u[uid,t])))
            rows.append(row(uid,d['type'],t,V(m.r_up[uid,t]),V(m.r_dn[uid,t]),V(m.P[uid,t]),
                            d['type'] in providers and on>0,d['pmin']*on,d['pmax']*on,'online' if on else 'offline'))
        for uid,d in md.hydro.items():
            low=sum(d['zones'][z][0]*V(m.delta[j,z,t]) for j in d['units'] for z in range(len(d['zones'])))
            high=sum(d['zones'][z][1]*V(m.delta[j,z,t]) for j in d['units'] for z in range(len(d['zones'])))
            up=sum(V(m.rh_up[j,t]) for j in d['units'])
            r=row(uid,'hydro',t,up,sum(V(m.rh_dn[j,t]) for j in d['units']),
                  sum(V(m.Ph[j,t]) for j in d['units']),'hydro' in providers and high>0,
                  low,high,'online' if high>0 else 'offline')
            start=d['v0'] if t==0 else V(m.V[uid,t-1]); end=V(m.V[uid,t])
            available=end-d['vmin']
            required=.36*d['water_rate']*H*up if hydro_water_enabled else None
            r.update(V_start_1e4m3=float(start),V_end_1e4m3=float(end),
                     V_min_1e4m3=float(d['vmin']),V_max_1e4m3=float(d['vmax']),
                     water_rate_m3s_per_mw=float(d['water_rate']),
                     hydro_regulation=d.get('regulation','reservoir'),
                     hydro_reserve_water_checked=hydro_water_enabled,
                     reserve_available_water_1e4m3=float(available),
                     reserve_incremental_water_up_1e4m3=None if required is None else float(required))
            r['reserve_duration_up_margin_1e4m3']=None if required is None else float(available-required)
            r['reserve_duration_lower_margin_1e4m3']=r['reserve_duration_up_margin_1e4m3']
            r['reserve_duration_upper_margin_1e4m3']=None  # No new downward-reserve water rule.
            rate=float(d['water_rate']); spill=float(V(m.Qspill[uid,t])); qgen=rate*r['P_mw']
            qmax=None if d.get('qout_max') is None else float(d['qout_max'])
            checked=hydro_water_enabled and qmax is not None
            activation_gen=qgen+rate*up; activation_spill=max(0.,spill-rate*up)
            activation_out=activation_gen+activation_spill
            r.update(hydro_reserve_outflow_checked=checked,
                     hydro_reserve_outflow_status=('checked' if checked else 'not_configured' if qmax is None else 'not_participating'),
                     qout_max_m3s=qmax,qeco_m3s=float(d['qeco']),
                     Qgen_base_m3s=qgen,Qspill_base_m3s=spill,Qout_base_m3s=qgen+spill,
                     reserve_up_activation_generation_m3s=activation_gen,
                     reserve_up_activation_spill_m3s=activation_spill,
                     reserve_up_activation_outflow_m3s=activation_out,
                     reserve_up_outflow_margin_m3s=qmax-activation_out if checked else None,
                     reserve_up_ecological_margin_m3s=activation_out-float(d['qeco']))
            rows.append(r)
        for uid,d in md.storage.items():
            pc,pd=V(m.Pc[uid,t]),V(m.Pd[uid,t]);up,dn=V(m.rs_up[uid,t]),V(m.rs_dn[uid,t]);p=pd-pc
            e0=d['soc0']*d['ecap'] if t==0 else V(m.E[uid,t-1]);e1=V(m.E[uid,t])
            emin,emax=d['soc_min']*d['ecap'],d['soc_max']*d['ecap']
            r=row(uid,'storage',t,up,dn,p,enabled,-d['pmax'],d['pmax'],
                  'discharging' if V(m.u_dis[uid,t])>.5 else ('charging' if V(m.u_ch[uid,t])>.5 else 'idle'))
            r.update(P_ch_mw=float(pc),P_dis_mw=float(pd),E_start_mwh=float(e0),E_end_mwh=float(e1),
                     E_min_mwh=float(emin),E_max_mwh=float(emax),eta_c=float(d['eta_c']),eta_d=float(d['eta_d']))
            # Compatibility field: derived eligibility, not a decision variable.
            r['reserve_qualified']=int(enabled)
            if enabled:
                r['reserve_duration_up_margin_mwh']=float(e1-emin-H*up/d['eta_d'])   # 第二轮F项：时段末库存E[t]
                r['reserve_duration_down_margin_mwh']=float(emax-e1-H*dn*d['eta_c']) if down_enabled else None
                r['full_activation_up_margin_mwh']=float(min(e0,e1)-emin-H*(p+up)/d['eta_d'])
                r['full_activation_upper_margin_mwh']=float(emax-max(e0,e1)+H*d['eta_c']*(p-dn if down_enabled else p+up))
            r['reserve_duration_lower_margin_mwh']=r['reserve_duration_up_margin_mwh']
            r['reserve_duration_upper_margin_mwh']=r['reserve_duration_down_margin_mwh']
            rows.append(r)
        for uid,d in md.pumped.items():
            pg,pp=V(m.Pg[uid,t]),V(m.Pp[uid,t]);up,dn=V(m.rp_up[uid,t]),V(m.rp_dn[uid,t]);ng=int(round(V(m.ngen[uid,t])))
            xg=int(round(V(m.xg[uid,t])));xp=int(round(V(m.xp[uid,t])));eligible=enabled and bool(xg) and ng>0
            v0=d['init']['V'] if t==0 else V(m.Vu[uid,t-1]);v1=V(m.Vu[uid,t])
            r=row(uid,'pumped',t,up,dn,pg-pp,eligible,d['unit_gen_min']*ng,d['unit_gen_max']*ng,
                  'generating' if xg else ('pumping' if xp else 'idle'))
            r.update(P_gen_mw=float(pg),P_pump_mw=float(pp),n_gen=ng,x_gen=xg,x_pump=xp,total_units=d['N'],unit_gen_max_mw=d['unit_gen_max'],unit_gen_min_mw=d['unit_gen_min'],
                     V_start_1e4m3=float(v0),V_end_1e4m3=float(v1),V_min_1e4m3=float(d['v_lo']),V_max_1e4m3=float(d['v_hi']),e_gen_mwh_per_1e4m3=float(d['e_gen']))
            r['reserve_qualified']=int(eligible)
            if eligible:
                r['reserve_duration_up_margin_1e4m3']=float(v1-d['v_lo']-H*up/d['e_gen'])   # 第二轮F项：时段末上库库容Vu[t]
                r['reserve_duration_down_margin_1e4m3']=float(d['v_hi']-v1-H*dn/d['e_gen']) if down_enabled else None
                r['full_activation_up_margin_1e4m3']=float(min(v0,v1)-d['v_lo']-H*(pg+up)/d['e_gen'])
                r['full_activation_upper_margin_1e4m3']=float(d['v_hi']-max(v0,v1)+H*(pg-dn if down_enabled else pg+up)/d['e_gen'])
            r['reserve_duration_lower_margin_1e4m3']=r['reserve_duration_up_margin_1e4m3']
            r['reserve_duration_upper_margin_1e4m3']=r['reserve_duration_down_margin_1e4m3']
            rows.append(r)
        current=[r for r in rows if r['relative_period']==int(t)]
        re_credit=float(rc.get('mu_RE',0.))*sum(float(d['fc'][t])-V(m.Pw[w,t]) for w,d in md.ren.items())
        up=sum(r['r_up'] for r in current if r['reserve_eligible'])+re_credit
        dn=sum(r['r_dn'] for r in current if r['reserve_eligible'])
        systems.append(dict(period=case.t0+int(t),relative_period=int(t),reserve_down_enabled=down_enabled,load_mw=float(sum(v[t] for v in case.load_bus.values())),renewable_available_mw=float(sum(d['fc'][t] for d in md.ren.values())),renewable_dispatch_mw=float(sum(V(m.Pw[w,t]) for w in md.ren)),R_up_req=float(case.R_up[t]),R_dn_req=float(case.R_dn[t]),
                            R_up_supply=float(up),R_dn_supply=float(dn),R_up_re_credit_mw=re_credit,
                            R_up_short=float(V(m.sR_up[t])),R_dn_short=float(V(m.sR_dn[t])),
                            R_up_balance_mw=float(up+V(m.sR_up[t])-case.R_up[t]),R_dn_balance_mw=float(dn+V(m.sR_dn[t])-case.R_dn[t])))
    from .natural_inflow import case_input_evidence
    return dict(input_provenance=case_input_evidence(case),evidence_version=3,hydro_reserve_outflow_formulation='generation_flow_with_reducible_spill_v1',
                hydro_reserve_outflow_enabled=hydro_water_enabled and any(d.get('qout_max') is not None for d in md.hydro.values()),
                hydro_reserve_water_formulation='period_end_stock_incremental_up_water_v1',
                hydro_reserve_water_enabled=hydro_water_enabled,
                hydro_reserve_water_scope='incremental_end_stock_only;no_future_inflow_or_spill_recovery;not_full_activation_or_network_delivery',
                storage_reserve_enabled=enabled,down_enabled=down_enabled,T_dur_h=H,window_start=case.t0,periods=md.T,resources=rows,system=systems,
                resource_catalog=sorted({r['unit'] for r in rows}),providers=sorted(providers),contingency_mw=case.reserve_info.get('R_ctg'),
                formulation='period_end_stock',qualification_semantics='derived_eligibility_not_decision_variable',
                scope='capacity_constraint_only_no_reserve_payment;incremental_reserve_energy_from_period_end_stock;not_full_baseline_plus_activation_delivery_guarantee',
                battery_response_assumption='ideal_inverter_activation_can_cross_zero_and_idle;not_certified_response',
                reserve_requirement_parameters={k:rc[k] for k in ('rho_L','rho_RE','rho_L_dn','rho_RE_dn','include_ctg','mu_RE')})


def tracking_results(m) -> pd.DataFrame:
    """Forecast-window tracking penalties, not executed energy or settlement charges."""
    columns = ['unit','kind','stock_unit','period','relative_period','target_lower','target_upper',
               'actual','deviation_up','deviation_down','slack_up','slack_down',
               'penalty_yuan_per_stock_unit','penalty_yuan','constraints_active','constraint_kind','state_end_hour']
    rows=[]
    coeff=getattr(m,'_anchor_cost_coefficients',{})
    term=m._case.terminal or {}
    for i,(unit,t,lo,hi,kind) in enumerate(getattr(m,'_anchors',[])):
        actual=float(V(getattr(m,kind)[unit,t]))
        lo=float(V(m.A_lo[i]));hi=float(V(m.A_hi[i]))
        up=float(V(m.sA_up[i]));down=float(V(m.sA_dn[i]))
        active=bool(m.c_anc_lo[i].active and m.c_anc_hi[i].active)
        c=coeff.get(i)
        if c is None:
            c=term.get('pumped_anchor_penalty',term.get('anchor_penalty',1e5)) if kind=='Vu' else term.get('anchor_penalty',1e5)
        rows.append(dict(unit=unit,kind=kind,stock_unit='MWh' if kind=='E' else '1e4m3',
            period=int(m._case.t0+t),relative_period=int(t),target_lower=lo,target_upper=hi,
            actual=actual,deviation_up=max(0.,actual-hi),deviation_down=max(0.,lo-actual),
            slack_up=up,slack_down=down,penalty_yuan_per_stock_unit=float(c) if active else 0.,
            penalty_yuan=float(c)*(up+down) if active else 0.,constraints_active=active,
            constraint_kind="hard_equality" if i in getattr(m,"_hard_anchor_indices",()) else "soft_target",
            state_end_hour=(m._case.t0+t+1)*m._md.dt if hasattr(m,"_md") else None))
    return pd.DataFrame(rows,columns=columns)
