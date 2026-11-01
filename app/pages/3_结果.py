"""结果展示：概览、电价、出力、储能、线路、拓扑、结算。"""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (DA_COLOR, DIFF_SCALE, LMP_SCALE, LOAD_SCALE, PALETTE, RT_COLOR, TYPE_CN, TYPE_COLOR, TYPE_SYMBOL,
                    all_results, branches_with_rate, bus_resources, df_download, empty_state, get_result, line_loading,
                    load_weighted, metric_grid, page_setup, period_labels, result_label, show_df, show_fig, style_fig,
                    topo_layout, wan)

page_setup("结果", "日前 SCUC/SCED 与实时滚动出清结果：节点电价、出力、储能、线路、拓扑与两部制结算")
res = all_results()
if not res:
    empty_state("还没有完成的运行结果。", "请先到左侧 **运行** 页启动“仅日前”或“全流程”，完成后回到本页查看。")
    st.page_link("pages/2_运行.py", label="前往运行页", icon="▶️")
    st.stop()
keys = [m["dir"] for m in res]
lab = {m["dir"]: result_label(m) for m in res}
w = st.session_state.get("result_sel")
sel = st.selectbox("选择结果", keys, index=keys.index(w) if w in keys else 0, format_func=lab.get)
st.session_state["result_sel"] = sel
with st.spinner("读取结果文件…"):
    R = get_result(next(m for m in res if m["dir"] == sel))
S, DS = R["summary"], R["da_summary"]
FULL = R["full"]
buses = pd.read_csv(Path(R["case_dir"]) / "buses.csv")
T_da = int(R["da_nodal_prices"].period.max()) + 1
T_rt = int(R["rt_prices"].period.max()) + 1 if FULL else 0
lbl = period_labels(96)
st.caption(f"结果目录：`{sel}`" + ("" if FULL else "　· 仅日前结果：实时与偏差结算内容不显示") +
           (f"　· 实时只滚动了前 {T_rt} 个时段" if FULL and T_rt < 96 else ""))

ll_da = line_loading(R, "da")
ll_rt = line_loading(R, "rt") if FULL else None
PRICE = "电价（元/MWh）"
TIME = "时刻（15 min）"
tabs = st.tabs(["📈 系统概览", "💲 节点电价", "⚙️ 出力", "🔋 储能", "🔌 线路", "🗺️ 网络拓扑", "🧾 结算"])


def stage_radio(key):
    return st.radio("阶段", ["日前"] + (["实时"] if FULL else []), horizontal=True, key=key)


# ============================================================================ 概览
with tabs[0]:
    lw_da = load_weighted(R["da_nodal_prices"], buses)
    cong_da = int(ll_da.groupby("period").congested.any().sum()) if ll_da is not None else None
    cong_rt = int(ll_rt.groupby("period").congested.any().sum()) if ll_rt is not None else None
    st.subheader("关键指标")
    if FULL:
        sp, rt = S["spread"], S["realtime"]
        smode = {"fixed_da": "A 执行日前"}.get(rt["storage_mode"], "B1 窗口末±x" if rt["anchor"] == "window_end_pct" else "B2 整点等值")
        metric_grid([
            ("日前负荷加权均价（元/MWh）", f"{sp['da_lw_avg']:.1f}"),
            ("实时负荷加权均价（元/MWh）", f"{sp['rt_lw_avg']:.1f}", f"{sp['rt_lw_avg'] - sp['da_lw_avg']:+.1f} 较日前"),
            ("实时−日前价差（元/MWh）", f"{sp['lw_spread_mean']:+.1f}", f"绝对均值 {sp['lw_spread_abs_mean']:.1f}", "负荷加权价差的均值；delta 为绝对值均值"),
            ("日前总报价成本（万元）", wan(DS.get("objective")), None, "SCUC 目标值：电能量报价成本 + 启动 + 空载"),
            ("弃电 日前 / 实时（MWh）", f"{DS.get('curtailment_mwh', 0):,.0f} / {rt['curtail_mwh']:,.0f}"),
            ("新能源+负荷偏差损益（万元）", wan(S["forecast_error"]["dev_pnl"]), None, "(实际−日前)×(实时价−日前价) 汇总；负值为偏差损失"),
            ("阻塞时段 日前 / 实时", f"{cong_da if cong_da is not None else '—'}/{T_da} · {cong_rt}/{T_rt}"),
            ("用时 日前 / 实时（s）", f"{S['time_s']['da']:.0f} / {S['time_s']['realtime']:.0f}", f"储能模式 {smode}", "实时储能模式见“储能”页"),
        ], per_row=4)
    else:
        metric_grid([
            ("日前负荷加权均价（元/MWh）", f"{DS.get('lmp_load_weighted_avg', lw_da.mean()):.1f}"),
            ("节点电价 最低 / 最高（元/MWh）", f"{DS.get('lmp_min', 0):.0f} / {DS.get('lmp_max', 0):.0f}"),
            ("日前总报价成本（万元）", wan(DS.get("objective")), None, "SCUC 目标值：电能量报价成本 + 启动 + 空载"),
            ("启动费（万元）", wan(DS.get("startup_cost"), 2)),
            ("日前弃电（MWh）", f"{DS.get('curtailment_mwh', 0):,.0f}"),
            ("日前阻塞时段", "—" if cong_da is None else f"{cong_da} / {T_da}"),
            ("MIP gap", f"{DS.get('scuc_gap', 0):.3%}"),
            ("日前用时（s）", f"{DS.get('scuc_time_s', 0) + DS.get('sced_time_s', 0):.0f}"),
        ], per_row=4)

    fig = go.Figure()
    fig.add_scatter(x=[lbl[t] for t in lw_da.index], y=lw_da.values, name="日前", line=dict(color=DA_COLOR, width=2.2))
    if FULL:
        lw_rt = load_weighted(R["rt_prices"])
        fig.add_scatter(x=[lbl[t] for t in lw_rt.index], y=lw_rt.values, name="实时", line=dict(color=RT_COLOR, width=2, dash="dash"))
    show_fig(style_fig(fig, "系统负荷加权平均电价", TIME, PRICE, 360))

    cl = pd.DataFrame(DS.get("congested_lines", []))
    with st.expander(f"阻塞线路（日前）· {len(cl)} 条", expanded=len(cl) > 0):
        if len(cl):
            if "max_loading" in cl:
                cl["max_loading"] = cl.max_loading * 100
            show_df(cl.rename(columns={"line": "线路", "periods": "阻塞时段数", "max_loading": "最大负载率 %",
                                       "max_mu": "最大影子价格 元/MWh"}), "%.1f")
        else:
            empty_state("日前无阻塞线路。", icon="✅")
    if FULL and S.get("checks"):
        with st.expander("一致性检查（checks）"):
            st.json(S["checks"])

# ============================================================================ 电价
with tabs[1]:
    opts = ["日前", "实时", "实时−日前"] if FULL else ["日前"]
    which = st.radio("热力图", opts, horizontal=True)
    pda = R["da_nodal_prices"].pivot(index="bus", columns="period", values="lmp")
    prt = R["rt_prices"].pivot(index="bus", columns="period", values="lmp") if FULL else None
    mat = pda if which == "日前" else (prt if which == "实时" else prt - pda.reindex(columns=prt.columns))
    diff = which == "实时−日前"
    lo, hi = (None, None) if diff else (float(np.nanmin(pda.values)), float(np.nanmax(pda.values if prt is None else np.r_[pda.values.ravel(), prt.values.ravel()])))
    fig = px.imshow(mat.values, x=[lbl[t] for t in mat.columns], y=[str(b) for b in mat.index], aspect="auto",
                    color_continuous_scale=DIFF_SCALE if diff else LMP_SCALE, color_continuous_midpoint=0 if diff else None,
                    zmin=lo, zmax=hi, labels=dict(x=TIME, y="节点编号", color="元/MWh"))
    fig.update_coloraxes(colorbar_title_text=("价差<br>元/MWh" if diff else "LMP<br>元/MWh"))
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(tickfont=dict(size=10), dtick=1, autorange="reversed", type="category")
    show_fig(style_fig(fig, f"{which}节点电价热力图（39 节点 × {mat.shape[1]} 时段）", TIME, "节点编号", 680))
    st.caption("日前/实时热力图使用相同色标范围便于对比；价差图以 0 为中心（红 = 实时高于日前，蓝 = 实时低于日前）。")

    default_b = [b for b in [16, 19, 25, 30] if b in pda.index] or list(pda.index[:3])
    bsel = st.multiselect("节点电价曲线（实线 = 日前，虚线 = 实时）", list(pda.index), default=default_b,
                          format_func=lambda b: f"节点{b}")
    if not bsel:
        empty_state("请选择至少一个节点。", icon="👆")
    else:
        fig = go.Figure()
        for i, b in enumerate(bsel):
            col = PALETTE[i % len(PALETTE)]
            fig.add_scatter(x=[lbl[t] for t in pda.columns], y=pda.loc[b], name=f"节点{b} 日前", line=dict(color=col),
                            legendgroup=str(b))
            if FULL:
                fig.add_scatter(x=[lbl[t] for t in prt.columns], y=prt.loc[b], name=f"节点{b} 实时",
                                line=dict(color=col, dash="dash"), legendgroup=str(b))
        show_fig(style_fig(fig, "选中节点电价曲线", TIME, PRICE, 400))
        with st.expander("电价分解（日前，选中节点）：LMP = 能量分量 + 阻塞分量"):
            dd = R["da_nodal_prices"][R["da_nodal_prices"].bus.isin(bsel)][["period", "time", "bus", "lmp", "energy", "congestion"]]
            dd = dd.rename(columns={"period": "时段", "time": "时刻", "bus": "节点", "lmp": "LMP 元/MWh", "energy": "能量分量 元/MWh",
                                    "congestion": "阻塞分量 元/MWh"})
            show_df(dd, "%.2f", height=300)
            df_download(dd, "da_lmp_selected.csv")

# ============================================================================ 出力
with tabs[2]:
    stage = stage_radio("gen_stage")
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
        fig.add_scatter(x=x, y=g[t].clip(lower=0) if t != "storage_ch" else g[t], name=TYPE_CN.get(t, t),
                        stackgroup="neg" if t == "storage_ch" else "pos", mode="lines",
                        line=dict(width=0.5, color=TYPE_COLOR.get(t)), fillcolor=TYPE_COLOR.get(t))
    if stage == "日前" and R["da_system"] is not None:
        fig.add_scatter(x=[lbl[t] for t in R["da_system"].period], y=R["da_system"].load_mw, name="负荷", line=dict(color="#111", width=2))
    elif stage == "实时":
        fig.add_scatter(x=[lbl[t] for t in R["rt_system"].period], y=R["rt_system"].load_mw, name="实际负荷", line=dict(color="#111", width=2))
    show_fig(style_fig(fig, f"{stage}分类型出力堆叠（储能充电为负）", TIME, "出力（MW）", 480, legend="资源类型"))

    e = u.groupby("typ").P_mw.sum() * 0.25
    e = e.reindex(order).dropna()
    st.caption("分类型电量（MWh）：" + "　".join(f"{TYPE_CN.get(k, k)} {v:,.0f}" for k, v in e.items()))

    usel = st.multiselect("单机出力曲线（实线 = 日前，虚线 = 实时）", sorted(ua.unit.unique()),
                          default=[x for x in ["C1", "CCGT", "GT", "W1"] if x in set(ua.unit)])
    if usel:
        fig = go.Figure()
        for i, un in enumerate(usel):
            col = PALETTE[i % len(PALETTE)]
            a = ua[ua.unit == un]
            fig.add_scatter(x=[lbl[t] for t in a.period], y=a.P_mw, name=f"{un} 日前", line=dict(color=col), legendgroup=un)
            if FULL:
                b = R["rt_units"][R["rt_units"].unit == un]
                if len(b):
                    fig.add_scatter(x=[lbl[t] for t in b.period], y=b.P_mw, name=f"{un} 实时", line=dict(color=col, dash="dash"),
                                    legendgroup=un)
        show_fig(style_fig(fig, "单机出力曲线", TIME, "出力（MW）", 380))
    else:
        empty_state("请选择至少一台机组。", icon="👆")

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
        st.info({"fixed_da": "实时模式 A：执行日前充放电曲线"}.get(smode, "实时模式 B1：窗口末 E_RT 在 (1±x)·E_DA 带内（阴影）"
                if anchor == "window_end_pct" else "实时模式 B2（默认）：窗口内各整点 E_RT = E_DA（菱形为整点锚点）"), icon="🔋")
    if sd is None or not len(sd):
        empty_state("该结果没有储能数据。")
    for k, un in enumerate(sd.unit.unique() if sd is not None else []):
        a = sd[sd.unit == un]
        info = ecap.get(un, {})
        head = f"{un}（节点 {info.get('bus', '?')}，{info.get('pmax', '?')} MW / {info.get('ecap', '?')} MWh）"
        with st.expander(head, expanded=k == 0):
            fig = go.Figure()
            x = [lbl[t] for t in a.period]
            fig.add_bar(x=x, y=a.P_dis - a.P_ch, name="日前 放电−充电（MW，右轴）", marker_color="#9ca3af", yaxis="y2", opacity=0.45)
            fig.add_scatter(x=x, y=a.E_mwh, name="日前 电量", line=dict(color=DA_COLOR, width=2))
            if FULL:
                b = R["rt_storage"][R["rt_storage"].unit == un]
                xb = [lbl[t] for t in b.period]
                fig.add_scatter(x=xb, y=b.E_mwh, name="实时 电量", line=dict(color=RT_COLOR, dash="dash", width=2))
                if smode != "fixed_da":
                    if anchor == "hourly_eq":
                        h = a[(a.period + 1) % 4 == 0]
                        fig.add_scatter(x=[lbl[t] for t in h.period], y=h.E_mwh, mode="markers", name="整点锚点 E_DA",
                                        marker=dict(symbol="diamond", size=7, color=DA_COLOR))
                    else:
                        fig.add_scatter(x=x, y=a.E_mwh * (1 + xx), line=dict(width=0), showlegend=False, hoverinfo="skip")
                        fig.add_scatter(x=x, y=a.E_mwh * (1 - xx), fill="tonexty", line=dict(width=0), name=f"±{xx:.0%} 带",
                                        fillcolor="rgba(31,95,168,0.15)")
            if info:
                for v, nm in ((info["soc_min"] * info["ecap"], "SOC 下限"), (info["soc_max"] * info["ecap"], "SOC 上限")):
                    fig.add_hline(y=v, line_dash="dot", line_color="#6b7280", annotation_text=nm, annotation_position="top left")
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="充放电功率（MW）", showgrid=False))
            show_fig(style_fig(fig, f"{un} 电量与充放电", TIME, "电量（MWh）", 340), key=f"st_{un}")

# ============================================================================ 线路
with tabs[4]:
    stage = stage_radio("line_stage")
    ll = ll_da if stage == "日前" else ll_rt
    if ll is None:
        empty_state("该结果没有日前线路潮流文件。", "命令行全流程旧输出不含 da_line_flows.csv；请在网页“运行”页重新运行。")
    else:
        mat = ll.pivot(index="line", columns="period", values="loading")
        top = mat.max(axis=1).sort_values(ascending=False)
        mat = mat.loc[top.index]
        nshow = st.slider("热力图显示负载最高的线路数", 10, len(mat), min(20, len(mat)), 5)
        m2 = mat.iloc[:nshow]
        fig = px.imshow(m2.values * 100, x=[lbl[t] for t in m2.columns], y=list(m2.index), aspect="auto", zmin=0, zmax=100,
                        color_continuous_scale=LOAD_SCALE, labels=dict(color="负载率 %", x=TIME, y="线路"))
        fig.update_coloraxes(colorbar_title_text="负载率<br>%")
        fig.update_layout(hovermode="closest")
        show_fig(style_fig(fig, f"{stage}线路负载率热力图（按最大负载率排序，前 {nshow} 条）", TIME, "线路", max(360, 22 * nshow + 120)))
        stat = ll.groupby("line").agg(最大负载率=("loading", "max"), 平均负载率=("loading", "mean"), 阻塞时段数=("congested", "sum"),
                                      最大影子价格=("mu", "max"), 限额MW=("rate_eff", "first")).sort_values("最大负载率", ascending=False)
        stat = stat.reset_index().rename(columns={"line": "线路", "最大影子价格": "最大影子价格 元/MWh", "限额MW": "限额 MW"})
        stat["最大负载率"] *= 100
        stat["平均负载率"] *= 100
        stat = stat.rename(columns={"最大负载率": "最大负载率 %", "平均负载率": "平均负载率 %"})
        with st.expander("线路统计表", expanded=False):
            show_df(stat, "%.1f", height=360)
        lsel = st.multiselect("线路潮流曲线（虚线 = ±限额）", list(top.index), default=list(top.index[:3]))
        if lsel:
            fig = go.Figure()
            for i, ln in enumerate(lsel):
                col = PALETTE[i % len(PALETTE)]
                a = ll[ll.line == ln]
                fig.add_scatter(x=[lbl[t] for t in a.period], y=a.flow_mw, name=ln, line=dict(color=col))
                fig.add_hline(y=a.rate_eff.iloc[0], line_dash="dot", line_color=col)
                fig.add_hline(y=-a.rate_eff.iloc[0], line_dash="dot", line_color=col)
            show_fig(style_fig(fig, f"{stage}线路潮流", TIME, "潮流（MW）", 380, legend="线路"))

# ============================================================================ 拓扑
with tabs[5]:
    c1, c2 = st.columns([1, 3])
    with c1:
        stage = st.radio("阶段", ["日前"] + (["实时"] if FULL else []), key="topo_stage")
    Tmax = T_da if stage == "日前" else T_rt
    with c2:
        t = st.select_slider("时刻", options=list(range(Tmax)), value=min(Tmax - 1, 76), format_func=lambda i: lbl[i])
    pos = topo_layout(R["case_dir"])
    pr = (R["da_nodal_prices"] if stage == "日前" else R["rt_prices"])
    pr = pr[pr.period == t].set_index("bus").lmp
    ll = ll_da if stage == "日前" else ll_rt
    br = branches_with_rate(R["snapshot_root"])
    ub = bus_resources(R["snapshot_root"])
    fig = go.Figure()
    flows = ll[ll.period == t].set_index("line") if ll is not None else None
    bins = [(0, 0.5, "#c7d2de", "负载率 < 50%"), (0.5, 0.8, "#fdbb84", "50–80%"), (0.8, 0.999, "#ef6548", "80–100%"),
            (0.999, 9, "#7f0000", "满载阻塞")]
    shown = set()
    for r in br.itertuples():
        (x0, y0), (x1, y1) = pos[r.from_bus], pos[r.to_bus]
        if flows is not None and r.branch_id in flows.index:
            f = flows.loc[r.branch_id]
            lo = float(f.loading)
            _, _, col, name = next(b for b in bins if b[0] <= lo < b[1])
            wdt = 1.5 + 5 * min(lo, 1)
            txt = (f"<b>{r.branch_id}</b>（{r.from_bus}–{r.to_bus}）<br>潮流 {f.flow_mw:.0f} MW / 限额 {f.rate_eff:.0f} MW"
                   f"<br>负载率 {lo:.0%}" + (f"<br>影子价格 {f.mu:.1f} 元/MWh" if f.mu > 1e-6 else ""))
        else:
            col, wdt, txt, name = "#c7d2de", 1.5, r.branch_id, "负载率 < 50%"
        fig.add_scatter(x=[x0, (x0 + x1) / 2, x1], y=[y0, (y0 + y1) / 2, y1], mode="lines",
                        line=dict(color=col, width=wdt, dash="dot" if r.kind != "line" else "solid"),
                        hoverinfo="text", text=["", txt, ""], name=name, legendgroup=name, legendgrouptitle_text=None,
                        showlegend=name not in shown, legendrank=200)
        shown.add(name)
    # 母线：颜色 = LMP
    bl = sorted(pos)
    lmin = float(min(R["da_nodal_prices"].lmp.min(), pr.min()))
    lmax = float(max(R["da_nodal_prices"].lmp.max(), pr.max()))
    fig.add_scatter(x=[pos[b][0] for b in bl], y=[pos[b][1] for b in bl], mode="markers+text", text=[str(b) for b in bl],
                    textfont=dict(size=10, color="#ffffff", shadow="0px 0px 2px #000"), textposition="middle center",
                    hovertext=[f"<b>节点 {b}</b><br>LMP {pr.get(b, np.nan):.1f} 元/MWh<br>负荷 "
                               f"{buses.set_index('bus').load_mw.get(b, 0):.0f} MW"
                               + (f"<br>资源：{'、'.join(i for i, _ in ub[b])}" if b in ub else "") for b in bl],
                    hoverinfo="text", name="母线（颜色 = LMP）", legendrank=1,
                    marker=dict(size=24, color=[pr.get(b, np.nan) for b in bl], colorscale=LMP_SCALE, cmin=lmin, cmax=lmax,
                                colorbar=dict(title=dict(text="节点电价<br>元/MWh"), x=1.0, len=0.75, thickness=14),
                                line=dict(width=1, color="#374151"), opacity=0.9))
    # 资源：形状/颜色 = 类型（偏移到母线右上方）
    xs = [p[0] for p in pos.values()]
    dx = (max(xs) - min(xs)) * 0.028
    for typ in ["coal", "gas_ccgt", "gas_gt", "hydro", "wind", "pv", "tie", "storage"]:
        pts = [(b, uid) for b, lst in ub.items() for uid, ty in lst if ty == typ and b in pos]
        if not pts:
            continue
        fig.add_scatter(x=[pos[b][0] + dx for b, _ in pts], y=[pos[b][1] + dx for b, _ in pts], mode="markers+text",
                        text=[uid for _, uid in pts], textposition="top right", textfont=dict(size=10, color=TYPE_COLOR[typ]),
                        marker=dict(symbol=TYPE_SYMBOL[typ], size=15, color=TYPE_COLOR[typ], line=dict(width=1, color="#222")),
                        hovertext=[f"{uid}（{TYPE_CN[typ]}）@ 节点 {b}" for b, uid in pts], hoverinfo="text",
                        name=TYPE_CN[typ], legendgroup="res", legendrank=10)
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), plot_bgcolor="white",
                      hovermode="closest", legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0),
                      margin=dict(l=10, r=10, t=60, b=10))
    show_fig(style_fig(fig, f"{stage} {lbl[t]} 网络拓扑：母线颜色 = 节点电价，线路颜色/粗细 = 负载率（虚线 = 变压器）",
                       height=760))
    if ll is None:
        st.caption("该结果无日前线路潮流文件，线路未着色。")

# ============================================================================ 结算
with tabs[6]:
    if not FULL:
        st.info("仅日前结果：显示日前中标收入（电量 × 日前 LMP）。全流程结果才有偏差结算。")
        ua = R["da_unit_awards"]
        t1 = ua.groupby(["unit", "type"]).agg(日前电量MWh=("energy_mwh", "sum"), 日前收入元=("revenue_yuan", "sum")).reset_index()
        t1["均价 元/MWh"] = t1.日前收入元 / t1.日前电量MWh.replace(0, np.nan)
        t1["日前收入 万元"] = t1.pop("日前收入元") / 1e4
        t1["type"] = t1.type.map(lambda x: TYPE_CN.get(x, x))
        show_df(t1.rename(columns={"unit": "机组", "type": "类型", "日前电量MWh": "日前电量 MWh"}), "%.1f",
                overrides={"日前收入 万元": "%.2f"})
        df_download(t1, "da_revenue_by_unit.csv")
    else:
        money = ["da_amount", "rt_amount", "total", "dev_pnl"]

        def to_wan(df):
            df = df.copy()
            for c in money:
                if c in df:
                    df[c] = df[c] / 1e4
            return df

        cn = {"entity": "主体", "type": "类型", "q_da": "日前电量 MWh", "q_rt": "实际电量 MWh", "dev_mwh": "偏差 MWh",
              "dev_abs_mwh": "偏差绝对值 MWh", "da_amount": "日前电费 万元", "rt_amount": "实时偏差电费 万元", "total": "合计 万元",
              "dev_pnl": "偏差损益 万元", "avg_price": "结算均价 元/MWh"}
        wfmt = {v: "%.2f" for k, v in cn.items() if k in money}
        bt = R["settlement_by_type"].copy()
        bt = bt[~((bt.type == "slack") & (bt.total.abs() < 1e-6) & (bt.q_rt.abs() < 1e-6))]   # 平衡松弛为 0 时不显示
        bt["type"] = bt.type.map(lambda x: TYPE_CN.get(x, x))
        st.subheader("按资源类型")
        st.caption("发电为正（收入），负荷为负（支付）；金额单位：万元")
        show_df(to_wan(bt).rename(columns=cn), "%.1f", overrides=wfmt)
        df_download(R["settlement_by_type"], "settlement_by_type.csv", key="dl_t")
        b2 = bt[bt.type != TYPE_CN["slack"]]
        fig = go.Figure()
        fig.add_bar(x=b2.type, y=b2.da_amount / 1e4, name="日前电费", marker_color=DA_COLOR)
        fig.add_bar(x=b2.type, y=b2.rt_amount / 1e4, name="实时偏差电费", marker_color=RT_COLOR)
        fig.add_bar(x=b2.type, y=b2.dev_pnl / 1e4, name="偏差损益", marker_color="#2a9d8f")
        fig.update_layout(barmode="group", hovermode="x unified")
        show_fig(style_fig(fig, "分类型结算金额", "资源类型", "金额（万元）", 380))

        with st.expander("按主体结算", expanded=False):
            be = R["settlement_by_entity"]
            tsel = st.multiselect("类型筛选", sorted(be.type.unique()), default=[t for t in be.type.unique() if t != "load"],
                                  format_func=lambda x: TYPE_CN.get(x, x))
            bb = to_wan(be[be.type.isin(tsel)])
            bb["type"] = bb.type.map(lambda x: TYPE_CN.get(x, x))
            show_df(bb.rename(columns=cn), "%.1f", overrides=wfmt)
            df_download(R["settlement_by_entity"], "settlement_by_entity.csv", key="dl_e")
        with st.expander("资金平衡校核（盈余 = 阻塞租金）与线路阻塞租金", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                rc = R["settlement_reconciliation"].copy()
                st.markdown("**资金平衡校核**（元）")
                show_df(rc, "%.2f")
            with c2:
                st.markdown("**线路阻塞租金**（元）")
                show_df(R["settlement_line_rent"], "%.1f")
        with st.expander("逐时段明细（settlement_detail）"):
            ent = st.selectbox("主体", sorted(R["settlement_detail"].entity.unique()))
            show_df(R["settlement_detail"][R["settlement_detail"].entity == ent], "%.2f", height=320)
            df_download(R["settlement_detail"], "settlement_detail.csv", "下载全部明细 CSV", key="dl_d")
