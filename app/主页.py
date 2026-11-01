"""IEEE39 现货市场出清网页应用 —— 入口页。启动：bash scripts/run_app.sh"""
import pandas as pd
import streamlit as st

from common import MODE_CN, STATUS_CN, page_setup, run_status, sc, scenario_selector

page_setup("IEEE39 现货市场出清 · 日前 + 实时滚动")
name = scenario_selector()

st.markdown("""
统一规则 **MKT**（参考四川 V4.0 / 甘肃 V3.2）：日前 SCUC（HiGHS MILP）→ SCED 节点电价 → 实时每 15 min 滚动出清未来 2 h → 日前/实时两部制偏差结算。

**使用步骤**
1. **边界条件**：选择或新建“场景”（一组边界条件），修改负荷、风光预测误差、报价、机组/储能/水电参数、线路限额、市场限价与备用、实时储能模式与滚动参数。
2. **运行**：选择场景，一键运行“仅日前”（约 2 min）或“全流程”（约 3 min），后台执行，页面可随意切换。
3. **结果**：指标卡、LMP 热力图与节点曲线、出力堆叠、储能 SOC、线路负载、拓扑着色、结算表（可下载 CSV）。
4. **场景对比**：任选两次运行结果对比。

> 标“假设值”的参数不是规则原文中的数值。场景文件位于 `scenarios/<场景名>/`，运行结果位于 `runs/<场景名>/<时间戳>_<模式>/`。
""")

c1, c2 = st.columns(2)
with c1:
    st.subheader("场景")
    rows = []
    for n in sc.list_scenarios():
        m = sc.scenario_meta(n)
        rows.append({"场景": n, "说明": m.get("note", ""), "来源": m.get("source", ""), "创建": m.get("created", ""),
                     "修改": m.get("modified", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
with c2:
    st.subheader("最近运行")
    runs = sc.list_runs()[:10]
    if runs:
        st.dataframe(pd.DataFrame([{"场景": m["scenario"], "模式": MODE_CN.get(m["mode"], m["mode"]),
                                    "状态": STATUS_CN.get(run_status(m), m.get("status")), "开始": m.get("started", ""),
                                    "用时 s": m.get("elapsed_s")} for m in runs]),
                     hide_index=True, width="stretch")
    else:
        st.info("还没有运行记录，请到“运行”页启动。")
st.caption(f"当前场景：{name}（左侧切换，各页面共用）")
