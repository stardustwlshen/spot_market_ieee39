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

page_setup("运行与出清结果", "查看出力计划与实际执行、结算电价电量，以及各主体收入、成本和利润")
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
selected_meta=next(m for m in res if m["dir"]==sel)
if selected_meta.get("demo_description"):st.info(selected_meta["demo_description"])
with st.spinner("读取结果文件…"):
    R = get_result(selected_meta)
S, DS = R["summary"], R["da_summary"]
FULL = R["full"]
if not R.get("input_provenance_known", False):
    st.warning("历史结果缺少冻结输入：仅使用已保存的限额与参数，缺失项记为未知；网络拓扑/节点基础负荷仅为当前 IEEE39 示意，不作为历史运行证据。")
buses = pd.read_csv(Path(R["case_dir"]) / "buses.csv")
T_da = int(R["da_nodal_prices"].period.max()) + 1
T_rt = int(R["rt_prices"].period.max()) + 1 if FULL else 0
lbl = period_labels(96)
st.caption(f"结果目录：`{sel}`" + ("" if FULL else "　· 仅日前结果：实时与偏差结算内容不显示") +
           (f"　· 实时只滚动了前 {T_rt} 个时段" if FULL and T_rt < 96 else ""))

ll_da = line_loading(R, "da")
ll_rt = line_loading(R, "rt") if FULL else None

def recorded_tracking(unit, kind):
    path = Path(sel) / 'rt_tracking_windows.csv'
    if not path.exists():return pd.DataFrame()
    try:frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:return pd.DataFrame()
    if not {'unit','kind','period','target_lower','target_upper'} <= set(frame):return pd.DataFrame()
    return frame[(frame.unit == unit) & (frame.kind == kind)].drop_duplicates(['period','target_lower','target_upper'])

def add_tracking_markers(fig, unit, kind, *, axis=None):
    frame = recorded_tracking(unit, kind)
    if frame.empty:return False
    for bound, label, symbol in [('target_lower','下目标','triangle-up'),('target_upper','上目标','triangle-down')]:
        fig.add_scatter(x=[lbl[int(t)] for t in frame.period], y=frame[bound], mode='markers',
                        yaxis=axis, name='已记录前瞻跟踪'+label+'（非已执行末值）', marker=dict(symbol=symbol,size=6))
    return True

PRICE = "结算电价（元/MWh）"
TIME = "时刻（每15分钟）"
from market_views import result_health_dashboard
result_health_dashboard(S, DS, directory=sel)
st.caption("图例：蓝色实线＝日前，莓红虚线＝实时（阶段颜色不代表盈亏）；收益图单独标明正负。功率 MW · 能量 MWh · 库容 万m³ · 流量 m³/s。")
main_tabs = st.tabs(["运行总览", "结算电价与电量", "出力计划与实际", "收入、成本与利润"])
with main_tabs[2]:
    operation_tabs = st.tabs(["机组出力", "储能", "水电与抽蓄"])
advanced = st.expander('高级诊断与技术记录（按需查看）', expanded=False)
with advanced:
    if DS.get('pricing_source') == 'bounded_auxiliary_pricing':
        st.caption('物理出力与状态来自正式调度；结算价采用独立辅助定价并经市场限价。辅助模型数量不执行。')
    st.caption('日前混合整数相对间隙：'+('未记录' if DS.get('scuc_gap') is None else f"{DS['scuc_gap']:.2%}"))
    diagnostic_tabs = st.tabs(["线路", "网络拓扑", "主体与求解阶段"])
tabs = [main_tabs[0],main_tabs[1],operation_tabs[0],operation_tabs[1],operation_tabs[2],diagnostic_tabs[0],diagnostic_tabs[1],main_tabs[3],diagnostic_tabs[2]]


def stage_radio(key):
    return st.radio("阶段", ["日前"] + (["实时"] if FULL else []), horizontal=True, key=key)


# ============================================================================ 概览
with tabs[0]:
    lw_da = load_weighted(R["da_nodal_prices"], buses)
    cong_da = int(ll_da.groupby("period").congested.any().sum()) if ll_da is not None else None
    cong_rt = int(ll_rt.groupby("period").congested.any().sum()) if ll_rt is not None else None
    st.subheader("关键指标")
    if not FULL:
        metric_grid([('日前加权结算价 · 元/MWh',f"{lw_da.mean():.1f}"),
                     ('日前中标电量 · MWh',f"{R['da_unit_awards'].energy_mwh.sum():,.1f}"),
                     ('日前中标收入 · 万元',wan(R['da_unit_awards'].revenue_yuan.sum()))])
    if FULL:
        op = S.get('operating_profit', {})
        scope = '全天' if S.get('completed_scope') == 'full_day' else f'已执行 {T_rt} 段'
        metric_grid([
            ('日前 / 实时结算价 · 元/MWh',f"{S['spread']['da_lw_avg']:.1f} / {S['spread']['rt_lw_avg']:.1f}"),
            (f'{scope}净能量结算收入 · 万元',wan(op.get('cash_revenue_yuan'))),
            (f'{scope}模拟运营利润 · 万元',wan(op.get('simulated_operating_profit_yuan')),None,'独立固定成本核算；负值保留。不含资本、融资与税费。'),
            (f'{scope}现金运营成本 · 万元',wan(op.get('cash_operating_cost_yuan')),None,'按冻结固定成本参数与实际运行量核算；不是报价目标。'),
        ])
    with advanced:
      with st.expander('完整运行指标与计算记录', expanded=False):
          if FULL:
              sp, rt = S["spread"], S["realtime"]
              smode = {"fixed_da": "A 执行日前"}.get(rt["storage_mode"], "B1 窗口末±x" if rt["anchor"] == "window_end_pct" else "B2 整点带罚软锚点")
              metric_grid([
                  ("日前负荷加权均价（元/MWh）", f"{sp['da_lw_avg']:.1f}"),
                  ("实时负荷加权均价（元/MWh）", f"{sp['rt_lw_avg']:.1f}", f"{sp['rt_lw_avg'] - sp['da_lw_avg']:+.1f} 较日前"),
                  ("实时−日前价差（元/MWh）", f"{sp['lw_spread_mean']:+.1f}", f"绝对均值 {sp['lw_spread_abs_mean']:.1f}", "负荷加权价差的均值；delta 为绝对值均值"),
                  ("日前调度目标（含罚，万元）", wan(DS.get("objective")), None, "正式能量调度目标：能量报价、启停空载及软约束罚项，不是社会真实成本"),
                  ("弃电 日前 / 实时（MWh）", f"{DS.get('curtailment_mwh', 0):,.0f} / {rt['curtail_mwh']:,.0f}"),
                  ("新能源+负荷偏差损益（万元）", wan(S["forecast_error"]["dev_pnl"]), None, "(实际−日前)×(实时价−日前价) 汇总；负值为偏差损失"),
                  ("阻塞时段 日前 / 实时", f"{cong_da if cong_da is not None else '—'}/{T_da} · {cong_rt}/{T_rt}"),
                  ("用时 日前 / 实时（s）", f"{S['time_s']['da']:.0f} / {S['time_s']['realtime']:.0f}", f"储能模式 {smode}", "实时储能模式见“储能”页"),
              ], per_row=4)
          else:
              metric_grid([
                  ("日前负荷加权均价（元/MWh）", f"{DS.get('lmp_load_weighted_avg', lw_da.mean()):.1f}"),
                  ("节点电价 最低 / 最高（元/MWh）", f"{DS.get('lmp_min', 0):.0f} / {DS.get('lmp_max', 0):.0f}"),
                  ("日前调度目标（含罚，万元）", wan(DS.get("objective")), None, "正式能量调度目标：能量报价、启停空载及软约束罚项，不是社会真实成本"),
                  ("启动费（万元）", wan(DS.get("startup_cost"), 2)),
                  ("日前弃电（MWh）", f"{DS.get('curtailment_mwh', 0):,.0f}"),
                  ("日前阻塞时段", "—" if cong_da is None else f"{cong_da} / {T_da}"),
                  ("混合整数相对间隙", f"{DS.get('scuc_gap', 0):.3%}"),
                  ("日前用时（s）", f"{DS.get('scuc_time_s', 0) + DS.get('sced_time_s', 0):.0f}"),
              ], per_row=4)
    fig = go.Figure()
    fig.add_scatter(x=[lbl[t] for t in lw_da.index], y=lw_da.values, name="日前", line=dict(color=DA_COLOR, width=2.2))
    if FULL:
        lw_rt = load_weighted(R["rt_prices"])
        fig.add_scatter(x=[lbl[t] for t in lw_rt.index], y=lw_rt.values, name="实时", line=dict(color=RT_COLOR, width=2, dash="dash"))
    show_fig(style_fig(fig, "系统负荷加权平均电价", TIME, PRICE, 360))

    cl = pd.DataFrame(DS.get("congested_lines", []))
    with st.expander(f"阻塞线路（日前）· {len(cl)} 条", expanded=False):
        if len(cl):
            if "max_loading" in cl:
                cl["max_loading"] = cl.max_loading * 100
            show_df(cl.rename(columns={"line": "线路", "periods": "阻塞时段数", "max_loading": "最大负载率 %",
                                       "max_mu": "最大影子价格 元/MWh"}), "%.1f")
        else:
            empty_state("日前无阻塞线路。", icon="✅")
    if FULL and S.get("checks"):
        with st.expander("一致性核查与技术记录"):
            check_names={'physical_feasible_checked_constraints':'已检查的物理约束通过','network_feasible':'线路潮流检查通过',
                         'hydro_window_constraints_satisfied':'水量与库容约束检查通过','rules_satisfied_no_slack':'全部窗口无松弛或软跟踪偏差',
                         'da_line_overload_count':'日前越限记录数','rt_line_overload_count':'实时越限记录数'}
            show_df(pd.DataFrame([{'检查项目':v,'保存结果':('是' if S['checks'].get(k) is True else '否' if S['checks'].get(k) is False else '未记录' if S['checks'].get(k) is None else str(S['checks'][k]))} for k,v in check_names.items()]))
            st.download_button('下载完整原字段核查记录（技术用途）',__import__('json').dumps(S['checks'],ensure_ascii=False,indent=2),file_name='checks.json')

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
    fig.update_coloraxes(colorbar_title_text=("价差<br>元/MWh" if diff else "节点电价<br>元/MWh"))
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
        with advanced:
          with st.expander("电价分解（日前）：原始节点电价 = 能量分量 + 阻塞分量；结算价另列", expanded=False):
              dd = R["da_nodal_prices"][R["da_nodal_prices"].bus.isin(bsel)][["period", "time", "bus", "lmp_raw", "lmp", "energy", "congestion"]]
              dd = dd.rename(columns={"period": "时段", "time": "时刻", "bus": "节点", "lmp_raw": "原始节点电价 元/MWh", "lmp": "限价后结算价 元/MWh", "energy": "能量分量 元/MWh",
                                      "congestion": "阻塞分量 元/MWh"})
              show_df(dd, "%.2f", height=300)
              df_download(dd, "da_lmp_selected.csv")
with tabs[1]:
    st.subheader('结算电价明细')
    price_stage=st.radio('电价明细范围',['日前','实时'] if FULL else ['日前'],horizontal=True,key='business_price_stage')
    market_prices=R['rt_prices'] if price_stage=='实时' else R['da_nodal_prices']
    business_prices=market_prices[[c for c in ('period','time','bus','lmp') if c in market_prices]].copy()
    show_df(business_prices,'%.2f',height=280)
    df_download(business_prices,'settlement_prices.csv',label='下载结算电价',key='business_prices')

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
    u["typ"] = np.where(u.type == "storage", np.where(u.P_mw >= 0, "storage_dis", "storage_ch"),
                        np.where(u.type == "pumped", np.where(u.P_mw >= 0, "pumped_gen", "pumped_pump"), u.type))
    g = u.groupby(["period", "typ"]).P_mw.sum().unstack(fill_value=0)
    order = [t for t in ["tie", "coal", "hydro", "gas_ccgt", "gas_gt", "wind", "pv", "storage_dis", "pumped_gen", "storage_ch", "pumped_pump"] if t in g]
    fig = go.Figure()
    x = [lbl[t] for t in g.index]
    for t in order:
        fig.add_scatter(x=x, y=g[t].clip(lower=0) if t not in ("storage_ch", "pumped_pump") else g[t], name=TYPE_CN.get(t, t),
                        stackgroup="neg" if t in ("storage_ch", "pumped_pump") else "pos", mode="lines",
                        line=dict(width=0.5, color=TYPE_COLOR.get(t)), fillcolor=TYPE_COLOR.get(t))
    if stage == "日前" and R["da_system"] is not None:
        fig.add_scatter(x=[lbl[t] for t in R["da_system"].period], y=R["da_system"].load_mw, name="负荷", line=dict(color="#111", width=2))
    elif stage == "实时":
        fig.add_scatter(x=[lbl[t] for t in R["rt_system"].period], y=R["rt_system"].load_mw, name="实际负荷", line=dict(color="#111", width=2))
    show_fig(style_fig(fig, f"{stage}分类型出力堆叠（储能充电、抽蓄抽水为负）", TIME, "出力（MW）", 480, legend="资源类型"))

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
    st.subheader('逐主体出力与中标电量')
    plan_columns=[c for c in ('period','time','unit','type','bus','P_mw','energy_mwh') if c in u]
    plan=u[plan_columns].copy()
    if 'energy_mwh' not in plan:plan['energy_mwh']=plan.P_mw*0.25
    show_df(plan,'%.2f',height=280)
    df_download(plan,'dispatch_plan.csv',label='下载当前出力与电量',key='business_dispatch_plan')


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
    st.caption("横轴标示15分钟时段起点；电量/库容曲线是相应时段结束状态，功率为该时段值。")
    if FULL:
        tracking = S.get("realtime", {}).get("tracking_policy") or {}
        if tracking.get("mode") == "soft_deliverable_energy":
            st.caption(f"实时跟踪为带罚软目标：{tracking.get('penalty_yuan_per_discharge_mwh', '未知')} 元/可交付放电 MWh；储能按放电效率、水电与抽蓄按发电转换系数归一化。罚项属于前瞻窗口调度目标，不是已执行结算收费；下图保留该次运行的储能跟踪时刻与允许范围。")
        st.info({"fixed_da": "实时模式 A：执行日前充放电曲线"}.get(smode, "实时模式 B1：窗口末 实时电量在日前电量的上下浮动范围 带内（阴影）"
                if anchor == "window_end_pct" else "实时模式 B2（默认）：各整点以 实时电量＝日前电量 为带罚软锚点目标，可产生有代价的偏差（菱形为目标）"), icon="🔋")
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
                        fig.add_scatter(x=[lbl[t] for t in h.period], y=h.E_mwh, mode="markers", name="日前整点电量目标",
                                        marker=dict(symbol="diamond", size=7, color=DA_COLOR))
                    else:
                        fig.add_scatter(x=x, y=a.E_mwh * (1 + xx), line=dict(width=0), showlegend=False, hoverinfo="skip")
                        fig.add_scatter(x=x, y=a.E_mwh * (1 - xx), fill="tonexty", line=dict(width=0), name=f"±{xx:.0%} 带",
                                        fillcolor="rgba(31,95,168,0.15)")
            if info:
                for v, nm in ((info["soc_min"] * info["ecap"], "荷电状态下限"), (info["soc_max"] * info["ecap"], "荷电状态上限")):
                    fig.add_hline(y=v, line_dash="dot", line_color="#6b7280", annotation_text=nm, annotation_position="top left")
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="充放电功率（MW）", showgrid=False))
            show_fig(style_fig(fig, f"{un} 电量与充放电", TIME, "电量（MWh）", 340), key=f"st_{un}")

# ============================================================================ 抽蓄 / 水电
with tabs[4]:
    import yaml
    try:
        uy = yaml.safe_load((Path(R["snapshot_root"]) / "config" / "units.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        uy = {}
    pcfg = {u["id"]: u for u in (uy.get("pumped") or []) if u.get("enabled", True)}
    pdd, prt_ = R.get("da_pumped"), R.get("rt_pumped")
    st.subheader("抽水蓄能")
    if pdd is None or not len(pdd):
        empty_state("该结果没有抽蓄数据（旧结果或抽蓄未启用）。", "在“边界条件 → 抽水蓄能”启用 PS1 后重新运行。")
    else:
        PSs = S.get("pumped") or {}
        for un in pdd.unit.unique():
            a = pdd[pdd.unit == un]
            info = pcfg.get(un, {})
            ps = PSs.get(un) or {}
            dsum = (DS.get("pumped") or {}).get(un, {})
            head = (f"{un}（节点 {info.get('bus', '?')}，{info.get('n_units', '?')}×{info.get('unit_gen_max', '?')} MW，"
                    + (f"规则同储能：报价 {ps.get('da_mode', '—')}，实时 {ps.get('rt_mode', '—')}）" if ps.get("rules") == "storage"
                       else f"旧规则结果：日前模式 {ps.get('da_mode', '—')}，实时模式 {ps.get('rt_mode', '—')}）"))
            st.markdown(f"**{head}**")
            items = [("日前 发电 / 抽水（MWh）", f"{dsum.get('gen_mwh', 0):,.0f} / {dsum.get('pump_mwh', 0):,.0f}"),
                     ("日前 发电 / 抽水小时", f"{dsum.get('gen_hours', 0):.2f} / {dsum.get('pump_hours', 0):.2f}"),
                     ("循环效率（日前）", "—" if dsum.get("cycle_eff") is None else f"{dsum['cycle_eff']:.1%}"),
                     ("日前 发电均价 / 抽水均价（元/MWh）",
                      f"{dsum.get('avg_gen_price') or 0:.0f} / {dsum.get('avg_pump_price') or 0:.0f}")]
            if ps:
                items += [("实时 发电 / 抽水（MWh）", f"{ps['rt']['gen_mwh']:,.0f} / {ps['rt']['pump_mwh']:,.0f}"),
                          ("电能量结算净收入（万元）", wan(ps.get("energy_net_revenue")), None, "日前 + 实时偏差两部制结算，发电收入 − 抽水电费"),
                          ("实时 已执行末时段上库库容（万m³）", f"{ps['rt'].get('V_up_end') or 0:.0f}")]
            metric_grid(items, per_row=4)
            x = [lbl[t] for t in a.period]
            fig = go.Figure()
            fig.add_bar(x=x, y=a.P_gen, name="日前 发电 MW", marker_color=TYPE_COLOR["pumped_gen"])
            fig.add_bar(x=x, y=-a.P_pump, name="日前 抽水 MW（负）", marker_color=TYPE_COLOR["pumped_pump"])
            if prt_ is not None and len(prt_):
                b = prt_[prt_.unit == un]
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.P_gen - b.P_pump, name="实时 净出力 MW",
                                line=dict(color=RT_COLOR, dash="dash", width=2))
            fig.update_layout(barmode="relative")
            show_fig(style_fig(fig, f"{un} 发电 / 抽水功率", TIME, "功率（MW，抽水为负）", 340), key=f"ps_p_{un}")
            fig = go.Figure()
            fig.add_scatter(x=x, y=a.V_up, name="日前 上库库容", line=dict(color=DA_COLOR, width=2))
            if prt_ is not None and len(prt_):
                b = prt_[prt_.unit == un]
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.V_up, name="实时 上库库容", line=dict(color=RT_COLOR, dash="dash", width=2))
            if FULL and not add_tracking_markers(fig, un, 'Vu'):
                st.caption('抽蓄前瞻跟踪目标：未记录（未保存逐窗口目标，不从当前配置反推）。')
            if info:
                for v, nm in ((max(info["vup_min"],info["v_total"]-info["vdn_max"]), "有效上库下限"), (min(info["vup_max"],info["v_total"]-info["vdn_min"]), "有效上库上限")):
                    fig.add_hline(y=v, line_dash="dot", line_color="#6b7280", annotation_text=nm, annotation_position="top left")
            show_fig(style_fig(fig, f"{un} 上库库容（万m³）", TIME, "库容（万m³）", 320), key=f"ps_v_{un}")
            if FULL and R.get("settlement_by_entity") is not None:
                se = R["settlement_by_entity"]
                se = se[se.entity.str.startswith(f"{un}_")]
                if len(se):
                    st.markdown("**结算（日前 + 实时偏差）**")
                    show_df(se.rename(columns={"entity": "主体", "type": "类型", "q_da": "日前电量 MWh", "q_rt": "实际电量 MWh",
                                               "da_amount": "日前电费 元", "rt_amount": "偏差电费 元", "total": "合计 元",
                                               "avg_price": "均价 元/MWh"}).drop(columns=["dev_mwh", "dev_abs_mwh", "dev_pnl"], errors="ignore"))
    st.subheader("水电")
    if FULL and (S.get("realtime", {}).get("tracking_policy") or {}).get("mode") == "soft_deliverable_energy":
        st.caption("水电跟踪日前窗口末库容为软目标，并非硬等式或逐时出力锁定；正式执行只取每个实时窗口首时段。日前申报终端约束仍按原规则保留。")
    hd = R.get("da_hydro")
    if hd is None or not len(hd):
        empty_state("该结果没有水电数据。")
    else:
        hcfg = {u["id"]: u for u in (uy.get("hydro") or [])}
        vc = "V_1e4m3" if "V_1e4m3" in hd else ("V" if "V" in hd else None)
        for hn in hd.station.unique() if "station" in hd else hd.unit.unique():
            a = hd[(hd.station if "station" in hd else hd.unit) == hn]
            reg = hcfg.get(hn, {}).get("regulation", "reservoir")
            fig = go.Figure()
            pc = "P_mw" if "P_mw" in a else a.columns[2]
            fig.add_scatter(x=[lbl[t] for t in a.period], y=a[pc], name="日前 出力 MW", line=dict(color=TYPE_COLOR["hydro"]))
            if vc:
                fig.add_scatter(x=[lbl[t] for t in a.period], y=a[vc], name="日前 库容 万m³", yaxis="y2", line=dict(color="#6a4c93", dash="dot"))
            rh=R.get('rt_hydro')
            if FULL and rh is not None and len(rh):
                idcol='station' if 'station' in rh else 'unit'
                executed_hydro=rh[rh[idcol]==hn]
                if len(executed_hydro):
                    fig.add_scatter(x=[lbl[int(t)] for t in executed_hydro.period],y=executed_hydro.P_mw,
                                    name="实时 已执行出力 MW",line=dict(color=RT_COLOR))
                    if 'V_1e4m3' in executed_hydro:
                        fig.add_scatter(x=[lbl[int(t)] for t in executed_hydro.period],y=executed_hydro.V_1e4m3,
                                        name="实时 已执行库容 万m³",yaxis="y2",line=dict(color=RT_COLOR,dash="dot"))

            hspec = hcfg.get(hn, {})
            season_spec = hspec.get('seasons', {}).get(S.get('season', DS.get('season')), {})
            if season_spec:
                lo,hi = season_spec.get('vmin'),season_spec.get('vmax')
                if hspec.get('regulation') == 'run_of_river':
                    hi=season_spec.get('v0');lo=None if hi is None else hi-float(hspec.get('pondage',0) or 0)
                for value,name in ((lo,'物理库容下限（冻结输入）'),(hi,'物理库容上限（冻结输入）')):
                    if value is not None:
                        fig.add_scatter(x=[lbl[int(a.period.min())],lbl[int(a.period.max())]],y=[value,value],yaxis='y2',
                                        name=name,mode='lines',line=dict(dash='dot',color='#888'))
            if FULL and not add_tracking_markers(fig, hn, 'V', axis='y2'):
                st.caption('水电软跟踪标记：未保存可用软目标；硬目标/历史目标请查下方逐窗口证据，不推断当前规则。')
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="库容（万m³）", showgrid=False))
            show_fig(style_fig(fig, f"{hn}（{'库容型' if reg == 'reservoir' else '径流式'}）出力与库容", TIME, "出力（MW）", 320), key=f"hy_{hn}")
            water_fig = go.Figure()
            for table,tag in ((hd,'日前'),(R.get('rt_hydro'),'实时已执行')):
                if table is None or table.empty:continue
                idcol='station' if 'station' in table else 'unit'
                water=table[table[idcol]==hn]
                qcol=next((c for c in ('qout_m3s','Q_out') if c in water),None)
                if qcol and len(water):
                    water_fig.add_scatter(x=[lbl[int(t)] for t in water.period],y=water[qcol],name=tag+'总下泄',mode='lines')
            limit=hspec.get('qout_max')
            if limit is not None:
                water_fig.add_hline(y=float(limit),line_dash='dot',annotation_text='下泄上限（冻结研究参数）')
                st.caption(f"{hn} 总下泄上限 {limit} m³/s；来源：{hspec.get('qout_max_source') or '冻结输入未注明来源，不视为经核验工程额定值'}。")
            else:st.caption(f'{hn} 总下泄工程上限未知/本次未声明；不以装机功率反推，不能据此声称无限工程泄流能力。')
            if season_spec.get('qeco') is not None:water_fig.add_hline(y=float(season_spec['qeco']),line_dash='dot',annotation_text='生态最小下泄')
            if water_fig.data:show_fig(style_fig(water_fig,f'{hn} 总下泄（发电用水 + 弃水）',TIME,'流量（m³/s）',280),key=f'hy_q_{hn}')

# Water targets are a lookahead diagnostic, distinct from executed reservoir state.
with tabs[4]:
    from market_views import hydro_window_dashboard
    from smm.live_runner import read_live
    target_file=Path(sel)/'hydro_window_targets.csv'
    if target_file.exists():
        try:target_table=pd.read_csv(target_file)
        except pd.errors.EmptyDataError:target_table=pd.DataFrame()
        target_history={str(t):g.to_dict('records') for t,g in target_table.groupby('window_start')} if len(target_table) else {}
    else:target_history=read_live(sel).get('hydro_window_history',{})
    hydro_window_dashboard(target_history,prefix='result_hydro:'+sel)

# ============================================================================ 线路
with tabs[5]:
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
with tabs[6]:
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
        if flows is not None and r.branch_id in flows.index and np.isfinite(flows.loc[r.branch_id].loading):
            f = flows.loc[r.branch_id]
            lo = float(f.loading)
            _, _, col, name = next((b for b in bins if b[0] <= lo < b[1]), bins[-1])
            wdt = 1.5 + 5 * min(lo, 1)
            txt = (f"<b>{r.branch_id}</b>（{r.from_bus}–{r.to_bus}）<br>潮流 {f.flow_mw:.0f} MW / 限额 {f.rate_eff:.0f} MW"
                   f"<br>负载率 {lo:.0%}" + (f"<br>影子价格 {f.mu:.1f} 元/MWh" if f.mu > 1e-6 else ""))
        else:
            col, wdt, txt, name = "#c7d2de", 1.5, r.branch_id + "（负载率未知）", "负载率未知"
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
                    hovertext=[f"<b>节点 {b}</b><br>节点电价 {pr.get(b, np.nan):.1f} 元/MWh<br>负荷 "
                               f"{buses.set_index('bus').load_mw.get(b, 0):.0f} MW"
                               + (f"<br>资源：{'、'.join(i for i, _ in ub[b])}" if b in ub else "") for b in bl],
                    hoverinfo="text", name="母线（颜色＝节点电价）", legendrank=1,
                    marker=dict(size=24, color=[pr.get(b, np.nan) for b in bl], colorscale=LMP_SCALE, cmin=lmin, cmax=lmax,
                                colorbar=dict(title=dict(text="节点电价<br>元/MWh"), x=1.0, len=0.75, thickness=14),
                                line=dict(width=1, color="#374151"), opacity=0.9))
    # 资源：形状/颜色 = 类型（偏移到母线右上方）
    xs = [p[0] for p in pos.values()]
    dx = (max(xs) - min(xs)) * 0.028
    for typ in ["coal", "gas_ccgt", "gas_gt", "hydro", "wind", "pv", "tie", "storage", "pumped"]:
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
with tabs[7]:
    from market_views import operating_profit_dashboard
    operating_profit_dashboard(R.get('operating_profit_by_unit'), R.get('operating_profit_by_period'), prefix='profit:'+sel)
    if not FULL:
        st.info("仅日前结果：显示日前中标收入（电量 × 日前节点电价）。全流程结果才有偏差结算。")
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
        with st.expander("资金平衡校核（盈余 = 阻塞项 + 限价修正 + 残差）与线路阻塞项", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                rc = R["settlement_reconciliation"].copy()
                st.markdown("**资金平衡校核**（元）")
                show_df(rc, "%.2f")
            with c2:
                st.markdown("**线路阻塞租金**（元）")
                show_df(R["settlement_line_rent"], "%.1f")
        with st.expander("逐时段结算明细"):
            ent = st.selectbox("主体", sorted(R["settlement_detail"].entity.unique()))
            show_df(R["settlement_detail"][R["settlement_detail"].entity == ent], "%.2f", height=320)
            df_download(R["settlement_detail"], "settlement_detail.csv", "下载全部明细 CSV", key="dl_d")


# Historical regulation outputs remain on disk, not a current market feature.
if S.get('regulation',{}).get('enabled',False):
    st.warning('历史调频实验结果：不属于当前纯电能量版本，不应作为当前默认研究结果。')
    with st.expander('历史调频实验只读原始表（不纳入当前能量收益）'):
        if R.get('rt_regulation_system') is not None:show_df(R['rt_regulation_system'])
        if R.get('rt_regulation') is not None:show_df(R['rt_regulation'])

with tabs[8]:
    from market_views import unit_dashboard, stages_dashboard, frozen_bids, system_metrics, frozen_units
    stage=st.radio('诊断正式结果阶段',['实时','日前'] if FULL else ['日前'],key='unit_diagnostic_stage')
    is_rt=stage=='实时'
    tables={'units':R.get('rt_units') if is_rt else R.get('da_unit_awards'),
            'prices':R.get('rt_prices') if is_rt else R.get('da_nodal_prices'),
            'storage':R.get('rt_storage') if is_rt else R.get('da_storage'),
            'pumped':R.get('rt_pumped') if is_rt else R.get('da_pumped'),
            'hydro':R.get('rt_hydro') if is_rt else R.get('da_hydro'),
            'regulation':R.get('rt_regulation') if is_rt else R.get('da_regulation_indicative'),
            'regulation_system':R.get('rt_regulation_system') if is_rt else R.get('da_regulation_system_indicative'),
            'system':R.get('rt_system') if is_rt else R.get('da_system')}
    system_metrics(tables)
    from market_views import reserve_dashboard
    with st.expander('安全备用：系统与逐主体（非交易市场）'):
        reserve_dashboard(tables,prefix='result_reserve:'+sel+stage,indicative=not is_rt)
    unit_dashboard(tables,bids=frozen_bids(sel),settlement=R.get('settlement_detail') if is_rt else None,prefix='result:'+sel+stage,indicative=not is_rt,units_config=frozen_units(sel))
    from smm.live_runner import read_live
    import json
    diagnostic_file=Path(sel)/'stage_diagnostics.json'
    diagnostics=json.loads(diagnostic_file.read_text()) if diagnostic_file.exists() else {}
    history={str(r['t']):r['stages'] for r in diagnostics.get('rt_windows',[])} or read_live(sel).get('stage_history',{})
    if diagnostics.get('da'):
        with st.expander('日前启停与正式调度诊断'):
            stages_dashboard(diagnostics['da'],prefix='result_da_stage:'+sel)
    if history:
        window=st.selectbox('窗口起点',list(history),key='result_window:'+sel)
        stages_dashboard(history[window],prefix='result_stage:'+sel)
    else:stages_dashboard(None,prefix='result_stage:'+sel)


# Separate the immutable run evidence from the current, independently revised UI.
with st.expander('运行来源与界面版本'):
    import hashlib, json
    provenance_path=Path(sel)/'run_provenance.json'
    if provenance_path.exists():
        provenance=json.loads(provenance_path.read_text())
        st.caption('运行时源码指纹（含当时界面）：'+str(provenance.get('source',{}).get('content_fingerprint','未知')))
        st.caption('采集时间：'+str(provenance.get('captured_at_utc','未知'))+'；未提交工作区：'+str(provenance.get('git_dirty','未知')))
    else:st.caption('历史运行未保存源码指纹，不借用当前版本作为来源。')
    ui_files=[Path(__file__),Path(__file__).parents[1]/'common.py',Path(__file__).parents[1]/'market_views.py']
    ui_hash=hashlib.sha256(b''.join(p.read_bytes() for p in ui_files)).hexdigest()
    st.caption('当前结果界面指纹：'+ui_hash+'；界面更新不表示重新求解。')
