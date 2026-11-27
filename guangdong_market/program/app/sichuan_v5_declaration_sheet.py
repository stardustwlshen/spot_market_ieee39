"""Review each station's daily production filing alongside its energy bid."""
from html import escape
import pandas as pd
import streamlit as st
from sichuan_v5_dadu_charts import time_chart


def clock(q):
    return f'{q//4:02}:{15*(q%4):02}'


def render_sheet(sheet, scenario):
    company = sheet['company']
    offers = company['offers']
    stations = sheet['inputs']['stations']
    names = {s['id']:s['name'] for s in stations}
    st.html(f"""<div style="padding:17px 22px;background:#123f49;border-radius:10px;color:white;">
    <strong style="font-size:1.3rem">{escape(scenario)} · 水电生产运行报送</strong><br>
    <span style="color:#d4e9e8">日前耦合范围：本算例纳入 19 站　｜　实时按日选择：参加（模拟审核通过）</span></div>""")
    st.caption('草案 §4.2、§7.5.2、§7.5.8 及附件 5 · 竞价日 09:45 前报送。实时耦合未申报时默认不参加。')
    uid = st.selectbox('查看电站（沿河从上游到下游）', list(names),index=list(names).index('D10'),
                       format_func=names.get,key='declaration_station')
    o = offers[uid]
    st.subheader(f'{names[uid]} · 次日报送值')
    left, middle, right = st.columns(3)
    left.metric('三小时顶峰能力', f"{o['sustained_3h_mw']:,.1f} MW")
    left.caption(f"模型可持续窗口 {clock(o['sustained_start_quarter'])}–{clock(o['sustained_start_quarter']+12)}")
    middle.metric('次日平均发电耗水率', f"{o['consumption_m3_kwh']:.3f} m³/kWh")
    middle.caption('水情系统 · 本算例采用固定耗水率')
    right.metric('特殊运行控制需求', '未单列')
    right.caption('水情系统 · 当前申报包缺少独立字段')
    end = [clock(q+1) for q in range(96)]
    a,b=st.columns(2)
    with a:
        st.markdown('**发电能力上下限 · 96 点**')
        time_chart(pd.DataFrame({'上限':o['available_mw'],'下限':o['minimum_mw']},index=end),'MW',height=175)
    with b:
        st.markdown('**区间天然来水预测 · 96 点**')
        time_chart(pd.DataFrame({'区间来水':o['natural_inflow_m3s']},index=end),'m³/s',height=175)
    st.caption('两条时序均通过现货生产信息申报系统报送；区间来水仅含本站区间新增天然来水。')
    st.subheader('电能量交易申报')
    st.caption(f"{names[uid]} · 附件 6 量价表 · 次日采用同一条分段曲线")
    blocks=[]
    start=0
    for i,(width,price) in enumerate(o['segments'],1):
        stop=round(start+width,2)
        blocks.append({'报价段':i,'出力起点 MW':start,'出力终点 MW':stop,'报价 元/MWh':price})
        start=stop
    st.dataframe(blocks,hide_index=True,width='stretch',height='content')
    st.subheader('19 站报送总览')
    st.dataframe([{'电站':name,'三小时顶峰 MW':offers[k]['sustained_3h_mw'],
                   '平均耗水率 m³/kWh':offers[k]['consumption_m3_kwh'],
                   '96 点上限 MW':offers[k]['available_mw'],'96 点下限 MW':offers[k]['minimum_mw'],
                   '96 点区间来水 m³/s':offers[k]['natural_inflow_m3s']} for k,name in names.items()],
                 hide_index=True,width='stretch',height='content',column_config={
                 col:st.column_config.LineChartColumn(col) for col in ('96 点上限 MW','96 点下限 MW','96 点区间来水 m³/s')})
    with st.expander('查看本站 96 点原始报送数值'):
        st.dataframe(pd.DataFrame({'时段末':end,'上限 MW':o['available_mw'],'下限 MW':o['minimum_mw'],
                     '区间来水 m³/s':o['natural_inflow_m3s']}),hide_index=True)
    with st.expander('耦合计算采用哪些基础参数'):
        st.write('本算例采用库容、下泄约束、固定耗水率、河段时滞与期初在途水量。水位—库容及 NHQ 特性曲线未完整建模。基础参数随研究申报包归档。')
        st.caption('草案 §7.5.9 与附件 2。日前耦合范围由调度确定并下发，当前页面没有真实调度审核回执。')
