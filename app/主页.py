"""IEEE39 现货市场出清网页应用 —— 入口页。启动：bash scripts/run_app.sh"""
from pathlib import Path

import pandas as pd
import streamlit as st

from common import (MODE_CN, STATUS_CN, TYPE_CN, all_results, bus_resources, empty_state, get_result, metric_grid, page_setup,
                    result_label, run_status, sc, scenario_selector, wan, ROOT)

page_setup("IEEE39 现货市场出清仿真", "日前 SCUC/SCED 出清 → 实时 15 min 滚动出清 → 日前/实时两部制偏差结算 · 统一规则 MKT（参考四川、甘肃）")
name = scenario_selector()

# ----------------------------------------------------------------------------- 项目说明
c1, c2 = st.columns([1.15, 1])
with c1:
    st.subheader("项目说明")
    st.markdown("""
以 **IEEE 39 节点系统** 为算例，模拟含 **燃煤、燃气（CCGT/GT）、水电、风电、光伏、区外来电、储能** 的省级现货市场：

- **日前市场**：发电侧报量报价（新能源报量报价、燃气报价），HiGHS 求解 SCUC（MILP，储能充放电 0/1 变量）→ 固定启停后 SCED（LP）求 **节点电价 LMP**（能量分量 + 阻塞分量）。
- **实时市场**：每 15 min 滚动出清未来 2 h、只执行第 1 个时段；负荷与风光用超短期预测；储能默认 **B2 整点电量等值** 跟踪日前计划。
- **结算**：日前电量 × 日前价 + (实际 − 日前) × 实时价；校核资金盈余 = 阻塞租金。
- 规则参数标“假设值”的不是规则原文数值；完整方案见项目 Page。
""")
with c2:
    st.subheader("出清流程")
    st.graphviz_chart("""
digraph G {
  rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.28;
  node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=11, color="#9fb3c8", fillcolor="#eef3f9", width=2.6];
  edge [color="#6b7280", arrowsize=0.7, fontname="Noto Sans CJK SC", fontsize=9];
  in  [label="边界条件\\n负荷/风光预测 · 报价 · 机组/储能/水电 · 线路限额", fillcolor="#fff7e6", color="#e9a03b"];
  da1 [label="日前 SCUC（MILP）\\n启停 · 储能 0/1 · 备用 · DC-PTDF 潮流"];
  da2 [label="日前 SCED（LP）\\n固定启停 → 对偶 → 节点电价 LMP"];
  rt  [label="实时滚动 SCED（96 次）\\n15 min 步长 · 2 h 窗口 · 只执行首时段"];
  st  [label="两部制偏差结算\\n日前电费 + 实时偏差电费 · 资金平衡校核", fillcolor="#e8f6f3", color="#2a9d8f"];
  out [label="网页结果\\n电价热力图 · 出力 · 储能 SOC · 线路 · 拓扑 · 结算", fillcolor="#f3eefa", color="#6a4c93"];
  in -> da1 -> da2 -> rt -> st -> out;
  da2 -> st [style=dashed, label=" 日前价/电量"];
}
""", width="stretch")

# ----------------------------------------------------------------------------- 算例资源
st.subheader("算例资源")
br = bus_resources(str(ROOT))
cnt: dict = {}
for lst in br.values():
    for uid, ty in lst:
        cnt.setdefault(ty, []).append(uid)
order = ["coal", "gas_ccgt", "gas_gt", "hydro", "wind", "pv", "tie", "storage"]
items = [(TYPE_CN.get(t, t), "、".join(cnt[t])) for t in order if t in cnt]
metric_grid(items, per_row=4)

# ----------------------------------------------------------------------------- 最新结果
st.subheader("最新结果速览")
res = [m for m in all_results() if m.get("scenario") != "命令行outputs"]
if res:
    m = res[0]
    with st.spinner("读取最新结果…"):
        R = get_result(m)
    S, DS = R["summary"], R["da_summary"]
    st.caption(result_label(m))
    if R["full"]:
        sp = S["spread"]
        metric_grid([("日前负荷加权均价（元/MWh）", f"{sp['da_lw_avg']:.1f}"),
                     ("实时负荷加权均价（元/MWh）", f"{sp['rt_lw_avg']:.1f}", f"{sp['rt_lw_avg'] - sp['da_lw_avg']:+.1f} 较日前"),
                     ("日前总报价成本（万元）", wan(DS.get("objective"))),
                     ("偏差损益（万元）", wan(S["forecast_error"]["dev_pnl"]))], per_row=4)
    else:
        metric_grid([("日前负荷加权均价（元/MWh）", f"{DS.get('lmp_load_weighted_avg', 0):.1f}"),
                     ("节点电价 最低 / 最高（元/MWh）", f"{DS.get('lmp_min', 0):.0f} / {DS.get('lmp_max', 0):.0f}"),
                     ("日前总报价成本（万元）", wan(DS.get("objective"))),
                     ("日前弃电（MWh）", f"{DS.get('curtailment_mwh', 0):,.0f}")], per_row=4)
    st.page_link("pages/3_结果.py", label="查看完整结果", icon="📊")
else:
    empty_state("还没有完成的运行。", "到 **运行** 页选择场景，一键运行“仅日前”（约 2 min）或“全流程”（约 3 min）。")
    st.page_link("pages/2_运行.py", label="前往运行页", icon="▶️")

# ----------------------------------------------------------------------------- 使用步骤
st.subheader("使用步骤")
steps = [("🧩 边界条件", "选择或新建场景，修改负荷、风光预测误差、报价、机组/储能/水电参数、线路限额、限价与备用、实时储能模式。", "pages/1_边界条件.py"),
         ("▶️ 运行", "一键运行“仅日前”或“全流程”，后台执行，可随意切换页面，进度与日志实时刷新。", "pages/2_运行.py"),
         ("📊 结果", "指标卡、LMP 热力图与节点曲线、出力堆叠、储能 SOC、线路负载、拓扑着色、结算表（可下载 CSV）。", "pages/3_结果.py"),
         ("⚖️ 场景对比", "任选两次运行结果，对比指标、均价曲线、节点电价、出力结构与结算。", "pages/4_场景对比.py")]
cols = st.columns(4)
for c, (t, d, p) in zip(cols, steps):
    with c:
        with st.container(border=True, height="stretch"):
            st.markdown(f"**{t}**")
            st.caption(d)
            st.page_link(p, label="打开", icon="➡️")

# ----------------------------------------------------------------------------- 场景与运行
with st.expander("场景列表与最近运行", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**场景**")
        rows = []
        for n in sc.list_scenarios():
            mm = sc.scenario_meta(n)
            rows.append({"场景": n, "说明": mm.get("note", ""), "来源": mm.get("source", ""), "修改": mm.get("modified", "")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with c2:
        st.markdown("**最近运行**")
        runs = sc.list_runs()[:10]
        if runs:
            st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"], x["mode"]),
                                        "状态": STATUS_CN.get(run_status(x), x.get("status")), "开始": x.get("started", ""),
                                        "用时 s": x.get("elapsed_s")} for x in runs]),
                         hide_index=True, width="stretch")
        else:
            empty_state("还没有运行记录，请到“运行”页启动。")
st.caption(f"当前场景：**{name}**（左侧切换，各页面共用）· 场景文件 `scenarios/<场景名>/` · 运行结果 `runs/<场景名>/<时间戳>_<模式>/`")
