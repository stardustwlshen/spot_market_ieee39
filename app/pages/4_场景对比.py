"""两个结果对比：指标、均价曲线、节点电价差、出力结构、价值曲线（第二轮J项）、结算。"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (DA_COLOR, PALETTE, RT_COLOR, TYPE_CN, all_results, df_download, empty_state, get_result,
                    load_weighted, page_setup, period_labels, result_label, show_df, show_fig, style_fig)
from smm.time_grid import result_time_grid, result_time_axis
from result_comparison import comparison_metrics, frozen_price_buses
from result_navigation import validate_display_result

page_setup("场景对比", "任选两次运行结果（方案甲 / 方案乙），对比指标、电价、出力结构与结算")
from result_catalog import is_single_day_result
res = [m for m in all_results() if is_single_day_result(m)]
from result_story_view import render_experiment_matrix_guide
render_experiment_matrix_guide()
if len(res) < 2:
    empty_state("至少需要两个完成的结果才能对比。", "请到 **报价模拟** 页另建或修改场景，分别运行后再来对比。")
    st.stop()
keys = [m["dir"] for m in res]
lab = {m["dir"]: result_label(m) for m in res}
c1, c2 = st.columns(2)
a = c1.selectbox("方案甲", keys, index=0, format_func=lab.get)
b = c2.selectbox("方案乙", keys, index=1, format_func=lab.get)
if a == b:
    st.warning("方案甲与方案乙 选择了同一个结果，差值全为 0。", icon="⚠️")
try:
    with st.spinner("读取结果…"):
        RA, RB = get_result(next(m for m in res if m["dir"] == a)), get_result(next(m for m in res if m["dir"] == b))
        for result in (RA, RB):
            validate_display_result(result)
        price_buses = [frozen_price_buses(result) for result in (RA, RB)]
        ma, mb = comparison_metrics(RA), comparison_metrics(RB)
except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
    st.error('所选结果缺失或损坏，暂不能对比。原始存档未修改；可在上方切换其他运行。')
    with st.expander('读取失败详情'):
        st.code(str(exc))
    st.stop()
for result, name in ((RA, '方案甲'), (RB, '方案乙')):
    for line_warning in result.get('line_scope_warnings', []):
        st.warning(name + '：' + line_warning)
if any(bus is None for bus in price_buses):
    st.warning('部分结果缺少冻结节点记录；只用已保存的负荷权重，缺失时均价曲线留空，不借用当前网络或等权均价。')
if result_time_grid(RA) != result_time_grid(RB):
    st.warning('两次运行时间网格不同，不能按时段序号直接对比。请选择相同步长和时段数的结果。')
    st.stop()
lbl = period_labels(*result_time_grid(RA))


same_rt_periods = (RA["full"] and RB["full"] and
                   set(RA["rt_prices"].period) == set(RB["rt_prices"].period))
df = pd.DataFrame({"指标": list(dict.fromkeys(list(ma) + list(mb)))})
df["A"] = df.指标.map(ma)
df["B"] = df.指标.map(mb)
df["B−A"] = df.B - df.A
if RA['full'] and RB['full'] and not same_rt_periods:
    rt_metrics = set(ma) - set(comparison_metrics({**RA, 'full': False}))
    df.loc[df.指标.isin(rt_metrics - {'实时时段数'}), 'B−A'] = np.nan
    st.warning('两次实时执行范围不同；实时相关均价、总量和损益不计算跨范围差值。日前仍按各自完整计划比较。')
st.subheader("指标对比")
st.caption("电价单位 元/MWh；金额单位 万元。空白表示未记录或不可比；电量加权结算价与逐时价差算术均值口径不同。")
show_df(df, "%.2f")
df_download(df, "compare_metrics.csv")

fig = go.Figure()
for R, bus, n, dash in ((RA, price_buses[0], "方案甲", "solid"), (RB, price_buses[1], "方案乙", "dash")):
    lw = load_weighted(R["da_nodal_prices"], bus)
    fig.add_scatter(x=[lbl[t] for t in lw.index], y=lw, name=f"{n} 日前", line=dict(dash=dash, color=DA_COLOR))
    if R["full"]:
        lr = load_weighted(R["rt_prices"], bus)
        fig.add_scatter(x=[lbl[t] for t in lr.index], y=lr, name=f"{n} 实时", line=dict(dash=dash, color=RT_COLOR))
st.subheader("电价")
show_fig(style_fig(fig, "系统负荷加权均价（方案甲实线 / 方案乙虚线；蓝 = 日前，红 = 实时）", result_time_axis(RA), "电价（元/MWh）", 380))

pa = RA["da_nodal_prices"].groupby("bus").lmp.mean()
pb = RB["da_nodal_prices"].groupby("bus").lmp.mean()
fig = go.Figure()
fig.add_bar(x=pa.index.astype(str), y=pa, name="方案甲", marker_color=PALETTE[0])
fig.add_bar(x=pb.index.astype(str), y=pb, name="方案乙", marker_color=PALETTE[3])
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
fig.add_bar(x=e.index, y=e.A, name="方案甲", marker_color=PALETTE[0])
fig.add_bar(x=e.index, y=e.B, name="方案乙", marker_color=PALETTE[3])
fig.update_layout(barmode="group", hovermode="x unified")
show_fig(style_fig(fig, "日前分类型电量", "资源类型", "电量（MWh）", 340))
with st.expander("分类型电量表（MWh）"):
    show_df(e.reset_index(names="类型"), "%.1f")

st.subheader("价值曲线（储能 / 抽蓄 / 水电）")
from value_views import render_value_compare
render_value_compare(RA, RB, lbl, 'cmp_value')

if RA["full"] and RB["full"] and not same_rt_periods:
    st.warning("两次运行的已执行时段范围不同（可能包含调试前缀）；上表总量不具同范围可比性，不计算结算总额差。")
if same_rt_periods:
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
    st.markdown("**储能：日前与实时 末时段电量（MWh）**")
    from result_policy_notes import storage_endpoint_rows
    rows = storage_endpoint_rows(RA, '方案甲') + storage_endpoint_rows(RB, '方案乙')
    if any(pd.isna(row['日前电量@末时段']) for row in rows):
        st.info('部分方案未保存相应日前储能末态，参照值留空；不补零，也不以当前配置代替。')
    show_df(pd.DataFrame(rows), "%.2f")
else:
    st.caption("两个方案均为全流程且执行范围一致时显示结算对比。")
