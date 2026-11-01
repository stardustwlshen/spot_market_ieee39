"""两个结果对比：指标、均价曲线、节点电价差、出力结构、结算。"""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (DA_COLOR, PALETTE, RT_COLOR, TYPE_CN, all_results, df_download, empty_state, get_result, line_loading,
                    load_weighted, page_setup, period_labels, result_label, show_df, show_fig, style_fig)

page_setup("场景对比", "任选两次运行结果（A / B），对比指标、电价、出力结构与结算")
res = all_results()
if len(res) < 2:
    empty_state("至少需要两个完成的结果才能对比。", "请到 **边界条件** 页复制/修改场景，再到 **运行** 页各运行一次。")
    st.stop()
keys = [m["dir"] for m in res]
lab = {m["dir"]: result_label(m) for m in res}
c1, c2 = st.columns(2)
a = c1.selectbox("结果 A", keys, index=0, format_func=lab.get)
b = c2.selectbox("结果 B", keys, index=1, format_func=lab.get)
if a == b:
    st.warning("A 与 B 选择了同一个结果，差值全为 0。", icon="⚠️")
with st.spinner("读取结果…"):
    RA, RB = get_result(next(m for m in res if m["dir"] == a)), get_result(next(m for m in res if m["dir"] == b))
lbl = period_labels(96)


def metrics(R):
    S, DS = R["summary"], R["da_summary"]
    bus = pd.read_csv(Path(R["case_dir"]) / "buses.csv")
    lw = load_weighted(R["da_nodal_prices"], bus)
    ll = line_loading(R, "da")
    m = {"日前总报价成本 万元": DS.get("objective", np.nan) / 1e4, "启动费 万元": DS.get("startup_cost", np.nan) / 1e4,
         "日前负荷加权均价": DS.get("lmp_load_weighted_avg", lw.mean()), "日前节点电价最高": DS.get("lmp_max"),
         "日前节点电价最低": DS.get("lmp_min"), "日前弃电 MWh": DS.get("curtailment_mwh"),
         "日前阻塞时段数": int(ll.groupby("period").congested.any().sum()) if ll is not None else np.nan}
    if R["full"]:
        sp = S["spread"]
        llr = line_loading(R, "rt")
        m.update({"实时负荷加权均价": sp["rt_lw_avg"], "价差均值(实时−日前)": sp["lw_spread_mean"], "价差绝对均值": sp["lw_spread_abs_mean"],
                  "实时弃电 MWh": S["realtime"]["curtail_mwh"], "实时阻塞时段数": int(llr.groupby("period").congested.any().sum()),
                  "负荷+新能源偏差损益 万元": S["forecast_error"]["dev_pnl"] / 1e4,
                  "负荷总支付 万元": -S["settlement_by_type"].get("load", {}).get("total", np.nan) / 1e4,
                  "实时时段数": S["periods"]})
    return m


ma, mb = metrics(RA), metrics(RB)
df = pd.DataFrame({"指标": list(dict.fromkeys(list(ma) + list(mb)))})
df["A"] = df.指标.map(ma)
df["B"] = df.指标.map(mb)
df["B−A"] = df.B - df.A
st.subheader("指标对比")
st.caption("电价单位 元/MWh；金额单位 万元")
show_df(df, "%.2f")
df_download(df, "compare_metrics.csv")

fig = go.Figure()
for R, n, dash in ((RA, "A", "solid"), (RB, "B", "dash")):
    bus = pd.read_csv(Path(R["case_dir"]) / "buses.csv")
    lw = load_weighted(R["da_nodal_prices"], bus)
    fig.add_scatter(x=[lbl[t] for t in lw.index], y=lw, name=f"{n} 日前", line=dict(dash=dash, color=DA_COLOR))
    if R["full"]:
        lr = load_weighted(R["rt_prices"])
        fig.add_scatter(x=[lbl[t] for t in lr.index], y=lr, name=f"{n} 实时", line=dict(dash=dash, color=RT_COLOR))
st.subheader("电价")
show_fig(style_fig(fig, "系统负荷加权均价（A 实线 / B 虚线；蓝 = 日前，红 = 实时）", "时刻（15 min）", "电价（元/MWh）", 380))

pa = RA["da_nodal_prices"].groupby("bus").lmp.mean()
pb = RB["da_nodal_prices"].groupby("bus").lmp.mean()
fig = go.Figure()
fig.add_bar(x=pa.index.astype(str), y=pa, name="A", marker_color=PALETTE[0])
fig.add_bar(x=pb.index.astype(str), y=pb, name="B", marker_color=PALETTE[3])
fig.update_layout(barmode="group", hovermode="x unified")
fig.update_xaxes(type="category")
show_fig(style_fig(fig, "日前节点全天平均电价", "节点", "电价（元/MWh）", 360))

st.subheader("出力结构")
ea = RA["da_unit_awards"].groupby("type").energy_mwh.sum()
eb = RB["da_unit_awards"].groupby("type").energy_mwh.sum()
e = pd.DataFrame({"A": ea, "B": eb}).fillna(0)
e["B−A"] = e.B - e.A
e.index = [TYPE_CN.get(i, i) for i in e.index]
fig = go.Figure()
fig.add_bar(x=e.index, y=e.A, name="A", marker_color=PALETTE[0])
fig.add_bar(x=e.index, y=e.B, name="B", marker_color=PALETTE[3])
fig.update_layout(barmode="group", hovermode="x unified")
show_fig(style_fig(fig, "日前分类型电量", "资源类型", "电量（MWh）", 340))
with st.expander("分类型电量表（MWh）"):
    show_df(e.reset_index(names="类型"), "%.1f")

if RA["full"] and RB["full"]:
    st.subheader("结算")
    ta = RA["settlement_by_type"].set_index("type")[["total", "dev_pnl"]]
    tb = RB["settlement_by_type"].set_index("type")[["total", "dev_pnl"]]
    t = ta.join(tb, lsuffix="_A", rsuffix="_B", how="outer").fillna(0)
    t["合计差 B−A"] = t.total_B - t.total_A
    t["偏差损益差 B−A"] = t.dev_pnl_B - t.dev_pnl_A
    t.index = [TYPE_CN.get(i, i) for i in t.index]
    t = (t / 1e4).rename(columns={"total_A": "合计 A 万元", "dev_pnl_A": "偏差损益 A 万元", "total_B": "合计 B 万元",
                                  "dev_pnl_B": "偏差损益 B 万元", "合计差 B−A": "合计差 B−A 万元", "偏差损益差 B−A": "偏差损益差 B−A 万元"})
    st.caption("按资源类型，发电为正（收入），负荷为负（支付）；单位 万元")
    show_df(t.reset_index(names="类型"), "%.2f")
    df_download(t.reset_index(names="类型"), "compare_settlement_by_type.csv", key="dl_cs")
    st.markdown("**储能：日前 vs 实时 末时段电量（MWh）**")
    rows = []
    for R, n in ((RA, "A"), (RB, "B")):
        for un, g in R["rt_storage"].groupby("unit"):
            d = R["da_storage"][R["da_storage"].unit == un]
            T = int(g.period.max())
            rows.append({"结果": n, "储能": un, "日前电量@末时段": float(d[d.period == T].E_mwh.iloc[0]), "实时电量@末时段": float(g[g.period == T].E_mwh.iloc[0])})
    show_df(pd.DataFrame(rows), "%.2f")
else:
    st.caption("A、B 均为全流程结果时显示结算对比。")
