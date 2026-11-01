"""结果展示：概览、电价、出力、储能、线路、拓扑、结算。"""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (TYPE_CN, TYPE_COLOR, all_results, branches_with_rate, df_download, fmt_money, get_result, line_loading,
                    load_weighted, page_setup, period_labels, result_label, topo_layout)

page_setup("结果")
res = all_results()
if not res:
    st.info("还没有完成的运行。请先到“运行”页启动。")
    st.stop()
keys = [m["dir"] for m in res]
lab = {m["dir"]: result_label(m) for m in res}
w = st.session_state.get("result_sel")
sel = st.selectbox("选择结果", keys, index=keys.index(w) if w in keys else 0, format_func=lab.get)
st.session_state["result_sel"] = sel
R = get_result(next(m for m in res if m["dir"] == sel))
S, DS = R["summary"], R["da_summary"]
FULL = R["full"]
buses = pd.read_csv(Path(R["case_dir"]) / "buses.csv")
T_da = int(R["da_nodal_prices"].period.max()) + 1
T_rt = int(R["rt_prices"].period.max()) + 1 if FULL else 0
lbl = period_labels(96)
st.caption(f"目录：{sel}" + ("" if FULL else "　（仅日前结果：实时/结算相关内容不显示）") +
           (f"　实时只滚动了前 {T_rt} 个时段" if FULL and T_rt < 96 else ""))

ll_da = line_loading(R, "da")
ll_rt = line_loading(R, "rt") if FULL else None
tabs = st.tabs(["系统概览", "节点电价", "出力", "储能", "线路", "网络拓扑", "结算"])

# ============================================================================ 概览
with tabs[0]:
    lw_da = load_weighted(R["da_nodal_prices"], buses)
    c = st.columns(6)
    c[0].metric("日前总报价成本", fmt_money(DS.get("objective")), help="SCUC 目标值：电能量报价成本 + 启动 + 空载（元）")
    c[1].metric("日前负荷加权均价 元/MWh", f"{DS.get('lmp_load_weighted_avg', lw_da.mean()):.1f}")
    if FULL:
        sp = S["spread"]
        c[2].metric("实时负荷加权均价 元/MWh", f"{sp['rt_lw_avg']:.1f}", f"{sp['rt_lw_avg'] - sp['da_lw_avg']:+.1f}")
        c[3].metric("实时−日前价差 均值/绝对均值", f"{sp['lw_spread_mean']:+.0f} / {sp['lw_spread_abs_mean']:.0f}")
        c[4].metric("弃电 日前/实时 MWh", f"{DS.get('curtailment_mwh', 0):.0f} / {S['realtime']['curtail_mwh']:.0f}")
        c[5].metric("新能源+负荷偏差损益", fmt_money(S["forecast_error"]["dev_pnl"]),
                    help="(实际−日前)×(实时−日前价) 汇总；负值表示偏差带来损失")
    else:
        c[2].metric("节点电价 最低/最高", f"{DS.get('lmp_min', 0):.0f} / {DS.get('lmp_max', 0):.0f}")
        c[3].metric("日前弃电 MWh", f"{DS.get('curtailment_mwh', 0):,.0f}")
        c[4].metric("启动费", fmt_money(DS.get("startup_cost")))
        c[5].metric("MIP gap", f"{DS.get('scuc_gap', 0):.3%}")
    cong_da = int(ll_da.groupby("period").congested.any().sum()) if ll_da is not None else None
    cong_rt = int(ll_rt.groupby("period").congested.any().sum()) if ll_rt is not None else None
    c = st.columns(4)
    c[0].metric("日前阻塞时段数", "—" if cong_da is None else f"{cong_da} / {T_da}")
    if FULL:
        c[1].metric("实时阻塞时段数", f"{cong_rt} / {T_rt}")
        c[2].metric("用时 s（日前/实时）", f"{S['time_s']['da']:.0f} / {S['time_s']['realtime']:.0f}")
        c[3].metric("实时储能模式", {"fixed_da": "A 执行日前"}.get(S["realtime"]["storage_mode"],
                    "B1 窗口末±x" if S["realtime"]["anchor"] == "window_end_pct" else "B2 整点等值"))
    else:
        c[1].metric("日前用时 s", f"{DS.get('scuc_time_s', 0) + DS.get('sced_time_s', 0):.0f}")
    st.markdown("**阻塞线路（日前）**")
    st.dataframe(pd.DataFrame(DS.get("congested_lines", [])).rename(columns={"line": "线路", "periods": "阻塞时段数",
                 "max_loading": "最大负载率", "max_mu": "最大影子价格"}), hide_index=True)
    fig = go.Figure()
    fig.add_scatter(x=[lbl[t] for t in lw_da.index], y=lw_da.values, name="日前负荷加权均价")
    if FULL:
        lw_rt = load_weighted(R["rt_prices"])
        fig.add_scatter(x=[lbl[t] for t in lw_rt.index], y=lw_rt.values, name="实时负荷加权均价")
    fig.update_layout(height=340, yaxis_title="元/MWh", title="系统均价", margin=dict(t=40, b=30))
    st.plotly_chart(fig, width="stretch")
    if FULL and S.get("checks"):
        with st.expander("一致性检查"):
            st.json(S["checks"])

# ============================================================================ 电价
with tabs[1]:
    opts = ["日前"] + (["实时", "实时−日前"] if FULL else [])
    which = st.radio("热力图", opts, horizontal=True)
    pda = R["da_nodal_prices"].pivot(index="bus", columns="period", values="lmp")
    prt = R["rt_prices"].pivot(index="bus", columns="period", values="lmp") if FULL else None
    mat = pda if which == "日前" else (prt if which == "实时" else prt - pda.reindex(columns=prt.columns))
    fig = px.imshow(mat.values, x=[lbl[t] for t in mat.columns], y=[str(b) for b in mat.index], aspect="auto",
                    color_continuous_scale="RdBu_r" if which == "实时−日前" else "Turbo",
                    color_continuous_midpoint=0 if which == "实时−日前" else None,
                    labels=dict(x="时刻", y="节点", color="元/MWh"))
    fig.update_layout(height=600, title=f"{which} 节点电价热力图", margin=dict(t=40))
    st.plotly_chart(fig, width="stretch")
    bsel = st.multiselect("节点曲线（日前实线 / 实时虚线）", list(pda.index), default=[16, 19, 25, 30, 31, 33][:4] if 16 in pda.index else list(pda.index[:3]))
    fig = go.Figure()
    for i, b in enumerate(bsel):
        col = px.colors.qualitative.Plotly[i % 10]
        fig.add_scatter(x=[lbl[t] for t in pda.columns], y=pda.loc[b], name=f"节点{b} 日前", line=dict(color=col))
        if FULL:
            fig.add_scatter(x=[lbl[t] for t in prt.columns], y=prt.loc[b], name=f"节点{b} 实时", line=dict(color=col, dash="dash"))
    fig.update_layout(height=400, yaxis_title="元/MWh", margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    with st.expander("电价分解（日前，选中节点）：能量分量 + 阻塞分量"):
        dd = R["da_nodal_prices"][R["da_nodal_prices"].bus.isin(bsel)][["period", "time", "bus", "lmp", "energy", "congestion"]]
        st.dataframe(dd, hide_index=True, height=300)
        df_download(dd, "da_lmp_selected.csv")

# ============================================================================ 出力
with tabs[2]:
    stage = st.radio("阶段", ["日前"] + (["实时"] if FULL else []), horizontal=True, key="gen_stage")
    ua = R["da_unit_awards"]
    if stage == "日前":
        u = ua
    else:
        u = R["rt_units"]
        tie = ua[(ua.type == "tie") & (ua.period < T_rt)]
        if not (u.type == "tie").any():
            u = pd.concat([u, tie[u.columns.intersection(tie.columns)]], ignore_index=True)
    u = u.copy()
    u["typ"] = np.where(u.type == "storage", np.where(u.P_mw >= 0, "storage_dis", "storage_ch"), u.type)
    g = u.groupby(["period", "typ"]).P_mw.sum().unstack(fill_value=0)
    order = [t for t in ["tie", "coal", "hydro", "gas_ccgt", "gas_gt", "wind", "pv", "storage_dis", "storage_ch"] if t in g]
    fig = go.Figure()
    x = [lbl[t] for t in g.index]
    for t in order:
        fig.add_scatter(x=x, y=g[t].clip(lower=0) if t != "storage_ch" else g[t], name=TYPE_CN.get(t, t), stackgroup="neg" if t == "storage_ch" else "pos",
                        mode="lines", line=dict(width=0.5, color=TYPE_COLOR.get(t)))
    if stage == "日前" and R["da_system"] is not None:
        fig.add_scatter(x=[lbl[t] for t in R["da_system"].period], y=R["da_system"].load_mw, name="负荷", line=dict(color="black", width=2))
    elif stage == "实时":
        fig.add_scatter(x=[lbl[t] for t in R["rt_system"].period], y=R["rt_system"].load_mw, name="实际负荷", line=dict(color="black", width=2))
    fig.update_layout(height=480, yaxis_title="MW", title=f"{stage}出力堆叠（储能充电为负）", margin=dict(t=40))
    st.plotly_chart(fig, width="stretch")
    usel = st.multiselect("单机曲线", sorted(ua.unit.unique()), default=["C1", "CCGT", "GT", "W1"])
    fig = go.Figure()
    for i, un in enumerate(usel):
        col = px.colors.qualitative.Plotly[i % 10]
        a = ua[ua.unit == un]
        fig.add_scatter(x=[lbl[t] for t in a.period], y=a.P_mw, name=f"{un} 日前", line=dict(color=col))
        if FULL:
            b = R["rt_units"][R["rt_units"].unit == un]
            if len(b):
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.P_mw, name=f"{un} 实时", line=dict(color=col, dash="dash"))
    fig.update_layout(height=360, yaxis_title="MW", margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")

# ============================================================================ 储能
with tabs[3]:
    sd = R["da_storage"]
    ecap = {}
    try:
        import yaml
        uy = yaml.safe_load((Path(R["snapshot_root"]) / "config" / "units.yaml").read_text(encoding="utf-8"))
        ecap = {u["id"]: u for u in uy["storage"]}
    except Exception:  # noqa: BLE001
        pass
    anchor = S.get("realtime", {}).get("anchor") if FULL else None
    smode = S.get("realtime", {}).get("storage_mode") if FULL else None
    xx = S.get("realtime", {}).get("x", 0.05) if FULL else 0
    if FULL:
        st.caption({"fixed_da": "实时模式 A：执行日前充放电曲线"}.get(smode, "实时模式 B1：窗口末 E_RT 在 (1±x)·E_DA 带内（阴影）"
                   if anchor == "window_end_pct" else "实时模式 B2：窗口内各整点 E_RT = E_DA（菱形为整点锚点）"))
    for un in sd.unit.unique():
        a = sd[sd.unit == un]
        fig = go.Figure()
        x = [lbl[t] for t in a.period]
        fig.add_bar(x=x, y=a.P_dis - a.P_ch, name="日前 放电−充电 MW", marker_color="#bbb", yaxis="y2", opacity=0.5)
        fig.add_scatter(x=x, y=a.E_mwh, name="日前 电量 MWh", line=dict(color="#1f77b4"))
        if FULL:
            b = R["rt_storage"][R["rt_storage"].unit == un]
            xb = [lbl[t] for t in b.period]
            fig.add_scatter(x=xb, y=b.E_mwh, name="实时 电量 MWh", line=dict(color="#d62728", dash="dash"))
            if smode != "fixed_da":
                if anchor == "hourly_eq":
                    h = a[(a.period + 1) % 4 == 0]
                    fig.add_scatter(x=[lbl[t] for t in h.period], y=h.E_mwh, mode="markers", name="整点锚点 E_DA",
                                    marker=dict(symbol="diamond", size=7, color="#1f77b4"))
                else:
                    fig.add_scatter(x=x, y=a.E_mwh * (1 + xx), line=dict(width=0), showlegend=False, hoverinfo="skip")
                    fig.add_scatter(x=x, y=a.E_mwh * (1 - xx), fill="tonexty", line=dict(width=0), name=f"±{xx:.0%} 带",
                                    fillcolor="rgba(31,119,180,0.15)")
        if un in ecap:
            u = ecap[un]
            for v, nm in ((u["soc_min"] * u["ecap"], "SOC 下限"), (u["soc_max"] * u["ecap"], "SOC 上限")):
                fig.add_hline(y=v, line_dash="dot", line_color="gray", annotation_text=nm)
        fig.update_layout(title=f"{un}", height=330, margin=dict(t=40, b=20), yaxis_title="MWh",
                          yaxis2=dict(overlaying="y", side="right", title="MW", showgrid=False), legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, width="stretch")

# ============================================================================ 线路
with tabs[4]:
    stage = st.radio("阶段", ["日前"] + (["实时"] if FULL else []), horizontal=True, key="line_stage")
    ll = ll_da if stage == "日前" else ll_rt
    if ll is None:
        st.info("该结果没有日前线路潮流文件（命令行全流程旧输出不含 da_line_flows.csv）。")
    else:
        mat = ll.pivot(index="line", columns="period", values="loading")
        top = mat.max(axis=1).sort_values(ascending=False)
        mat = mat.loc[top.index]
        fig = px.imshow(mat.values * 100, x=[lbl[t] for t in mat.columns], y=list(mat.index), aspect="auto", zmin=0, zmax=100,
                        color_continuous_scale="YlOrRd", labels=dict(color="负载率 %", x="时刻", y="线路"))
        fig.update_layout(height=800, title=f"{stage}线路负载率（按最大负载排序）", margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")
        stat = ll.groupby("line").agg(最大负载率=("loading", "max"), 平均负载率=("loading", "mean"), 阻塞时段数=("congested", "sum"),
                                      最大影子价格=("mu", "max"), 限额MW=("rate_eff", "first")).sort_values("最大负载率", ascending=False)
        st.dataframe(stat.round(3), width="stretch")
        lsel = st.multiselect("线路潮流曲线", list(top.index), default=list(top.index[:3]))
        fig = go.Figure()
        for i, ln in enumerate(lsel):
            col = px.colors.qualitative.Plotly[i % 10]
            a = ll[ll.line == ln]
            fig.add_scatter(x=[lbl[t] for t in a.period], y=a.flow_mw, name=ln, line=dict(color=col))
            fig.add_hline(y=a.rate_eff.iloc[0], line_dash="dot", line_color=col)
            fig.add_hline(y=-a.rate_eff.iloc[0], line_dash="dot", line_color=col)
        fig.update_layout(height=360, yaxis_title="MW（虚线 = ±限额）", margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")

# ============================================================================ 拓扑
with tabs[5]:
    c1, c2 = st.columns([1, 3])
    stage = c1.radio("阶段", ["日前"] + (["实时"] if FULL else []), key="topo_stage")
    Tmax = T_da if stage == "日前" else T_rt
    t = c2.slider("时段", 0, Tmax - 1, min(Tmax - 1, 76), format="%d", help="0 = 00:00，76 = 19:00")
    c2.caption(f"时刻 {lbl[t]}")
    pos = topo_layout(R["case_dir"])
    pr = (R["da_nodal_prices"] if stage == "日前" else R["rt_prices"])
    pr = pr[pr.period == t].set_index("bus").lmp
    ll = ll_da if stage == "日前" else ll_rt
    br = branches_with_rate(R["snapshot_root"])
    import yaml as _y
    try:
        uy = _y.safe_load((Path(R["snapshot_root"]) / "config" / "units.yaml").read_text(encoding="utf-8"))
        ub = {}
        for grp in ("thermal", "tie", "renewable", "hydro", "storage"):
            for u in uy.get(grp, []):
                ub.setdefault(int(u["bus"]), []).append(u["id"])
    except Exception:  # noqa: BLE001
        ub = {}
    fig = go.Figure()
    cs = px.colors.sequential.YlOrRd
    flows = ll[ll.period == t].set_index("line") if ll is not None else None
    for r in br.itertuples():
        (x0, y0), (x1, y1) = pos[r.from_bus], pos[r.to_bus]
        if flows is not None and r.branch_id in flows.index:
            f = flows.loc[r.branch_id]
            lo = float(f.loading)
            col = "#7f0000" if lo >= 0.999 else cs[min(int(lo * (len(cs) - 1)), len(cs) - 1)]
            wdt = 1.5 + 5 * min(lo, 1)
            txt = f"{r.branch_id}<br>潮流 {f.flow_mw:.0f} MW / 限额 {f.rate_eff:.0f}<br>负载率 {lo:.0%}" + (f"<br>影子价格 {f.mu:.1f}" if f.mu > 1e-6 else "")
        else:
            col, wdt, txt = "#999", 1.5, r.branch_id
        dash = "dot" if r.kind != "line" else "solid"
        fig.add_scatter(x=[x0, (x0 + x1) / 2, x1], y=[y0, (y0 + y1) / 2, y1], mode="lines", line=dict(color=col, width=wdt, dash=dash),
                        hoverinfo="text", text=["", txt, ""], showlegend=False)
    bl = sorted(pos)
    fig.add_scatter(x=[pos[b][0] for b in bl], y=[pos[b][1] for b in bl], mode="markers+text",
                    text=[f"{b}" + (f"<br>{'/'.join(ub[b])}" if b in ub else "") for b in bl], textposition="top center",
                    hovertext=[f"节点 {b}<br>LMP {pr.get(b, np.nan):.1f} 元/MWh" + (f"<br>{'、'.join(ub[b])}" if b in ub else "") for b in bl],
                    hoverinfo="text",
                    marker=dict(size=[18 if b in ub else 12 for b in bl], color=[pr.get(b, np.nan) for b in bl], colorscale="Turbo",
                                cmin=float(R["da_nodal_prices"].lmp.min()), cmax=float(R["da_nodal_prices"].lmp.max()),
                                colorbar=dict(title="LMP<br>元/MWh"), line=dict(width=1, color="black")), showlegend=False)
    fig.update_layout(height=720, xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"), plot_bgcolor="white",
                      title=f"{stage} {lbl[t]}：节点颜色 = 节点电价，线路颜色/粗细 = 负载率（深红 = 满载阻塞，虚线 = 变压器）", margin=dict(t=50))
    st.plotly_chart(fig, width="stretch")
    if ll is None:
        st.caption("该结果无日前线路潮流文件，线路未着色。")

# ============================================================================ 结算
with tabs[6]:
    if not FULL:
        st.info("仅日前结果：显示日前中标收入（电量 × 日前 LMP）。全流程结果才有偏差结算。")
        ua = R["da_unit_awards"]
        t1 = ua.groupby(["unit", "type"]).agg(日前电量MWh=("energy_mwh", "sum"), 日前收入元=("revenue_yuan", "sum")).reset_index()
        t1["均价"] = t1.日前收入元 / t1.日前电量MWh.replace(0, np.nan)
        st.dataframe(t1.round(1), hide_index=True, width="stretch")
        df_download(t1, "da_revenue_by_unit.csv")
    else:
        cn = {"entity": "主体", "type": "类型", "q_da": "日前电量MWh", "q_rt": "实际电量MWh", "dev_mwh": "偏差MWh", "dev_abs_mwh": "偏差绝对值MWh",
              "da_amount": "日前电费元", "rt_amount": "实时偏差电费元", "total": "合计元", "dev_pnl": "偏差损益元", "avg_price": "结算均价"}
        bt = R["settlement_by_type"].copy()
        bt["type"] = bt.type.map(lambda x: TYPE_CN.get(x, x))
        st.markdown("**按类型**（发电为正收入，负荷为负 = 支付）")
        st.dataframe(bt.rename(columns=cn).round(1), hide_index=True, width="stretch")
        df_download(R["settlement_by_type"], "settlement_by_type.csv", key="dl_t")
        fig = go.Figure()
        b2 = bt[bt.type != TYPE_CN["slack"]]
        fig.add_bar(x=b2.type, y=b2.da_amount / 1e4, name="日前电费")
        fig.add_bar(x=b2.type, y=b2.rt_amount / 1e4, name="实时偏差电费")
        fig.add_bar(x=b2.type, y=b2.dev_pnl / 1e4, name="偏差损益")
        fig.update_layout(barmode="group", height=360, yaxis_title="万元", margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")
        st.markdown("**按主体**")
        be = R["settlement_by_entity"]
        tsel = st.multiselect("类型筛选", sorted(be.type.unique()), default=[t for t in be.type.unique() if t != "load"],
                              format_func=lambda x: TYPE_CN.get(x, x))
        st.dataframe(be[be.type.isin(tsel)].rename(columns=cn).round(1), hide_index=True, width="stretch")
        df_download(R["settlement_by_entity"], "settlement_by_entity.csv", key="dl_e")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**资金平衡校核**（盈余 = 阻塞租金）")
            st.dataframe(R["settlement_reconciliation"].round(2), hide_index=True)
        with c2:
            st.markdown("**线路阻塞租金**")
            st.dataframe(R["settlement_line_rent"].round(1), hide_index=True)
        with st.expander("逐时段明细（settlement_detail）"):
            ent = st.selectbox("主体", sorted(R["settlement_detail"].entity.unique()))
            st.dataframe(R["settlement_detail"][R["settlement_detail"].entity == ent].round(2), hide_index=True)
            df_download(R["settlement_detail"], "settlement_detail.csv", "下载全部明细 CSV", key="dl_d")
