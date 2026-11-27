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
    st.html("""<style>
    .st-key-declaration_station button{border-radius:6px!important;min-height:35px!important}
    .dd-station{border-left:5px solid #1b9682;padding:12px 18px;background:#eaf5f2;color:#103d43;border-radius:0 10px 10px 0;margin:3px 0 8px}
    .dd-station strong{font-size:1.45rem}.dd-station span{display:block;color:#32645e;margin-top:4px}
    </style>""")
    st.caption('沿大渡河由上游至下游排列 · 点站名查看申报')
    uid = st.pills('查看电站', list(names), default='D10',format_func=names.get,
                   key='declaration_station',label_visibility='collapsed') or 'D10'
    reaches = company['hydraulic_declaration']['reaches']
    adjacent = next((r for r in reaches if r['upstream']==uid), None)
    if adjacent is None:
        adjacent = next((r for r in reaches if r['downstream']==uid), None)
    peer = None
    relation = ''
    if adjacent:
        peer = adjacent['downstream'] if adjacent['upstream']==uid else adjacent['upstream']
        relation = f"{names[adjacent['upstream']]} → {names[adjacent['downstream']]}　·　模拟到水时滞 {15*adjacent['travel_quarters']} 分钟"
    label = '库容式' if next(s for s in stations if s['id']==uid)['model']=='reservoir' else '径流式'
    st.html(f'<div class="dd-station"><strong>{escape(names[uid])}</strong>　{label}<span>{escape(relation)}</span></div>')
    o = offers[uid]
    st.caption(f'{scenario} · 日前模拟纳入 19 站，实时选择参加并按模拟审核通过处理 · 竞价日 09:45 前报送')
    left, middle, right = st.columns(3)
    left.metric('三小时顶峰能力', f"{o['sustained_3h_mw']:,.1f} MW")
    left.caption(f"模型可持续窗口 {clock(o['sustained_start_quarter'])}–{clock(o['sustained_start_quarter']+12)}")
    middle.metric('次日平均发电耗水率', f"{o['consumption_m3_kwh']:.3f} m³/kWh")
    middle.caption('水情系统 · 本算例采用固定耗水率')
    right.metric('区间来水日均值', f"{sum(o['natural_inflow_m3s'])/96:,.1f} m³/s")
    right.caption('次日 96 点预测均值')
    end = [clock(q+1) for q in range(96)]
    a,b=st.columns(2)
    with a:
        st.markdown('**发电能力上下限 · 96 点**')
        power = {names[uid]+'上限':o['available_mw'], names[uid]+'下限':o['minimum_mw']}
        if peer:
            power[names[peer]+'上限'] = offers[peer]['available_mw']
        time_chart(pd.DataFrame(power,index=end),'MW',height=190)
    with b:
        st.markdown('**区间天然来水预测 · 96 点**')
        water = {names[uid]:o['natural_inflow_m3s']}
        if peer:
            water[names[peer]] = offers[peer]['natural_inflow_m3s']
        time_chart(pd.DataFrame(water,index=end),'m³/s',height=190)
    st.caption('区间来水为本区间新增天然来水。两站采用同一时间轴；特殊运行控制需求在本模拟申报包中未单列。')
    st.subheader('电能量交易申报')
    st.caption(f"{names[uid]} · 附件 6 量价表 · 次日采用同一条分段曲线")
    blocks=[]
    start=0
    for i,(width,price) in enumerate(o['segments'],1):
        stop=round(start+width,2)
        blocks.append({'报价段':i,'出力起点 MW':start,'出力终点 MW':stop,'报价 元/MWh':price})
        start=stop
    curve, detail = st.columns([1.15,1])
    points = [{'出力 MW':b[x],'报价 元/MWh':b['报价 元/MWh'],'段':b['报价段'],'序':i*2+k}
              for i,b in enumerate(blocks) for k,x in enumerate(('出力起点 MW','出力终点 MW'))]
    with curve:
        st.vega_lite_chart(pd.DataFrame(points), {'height':215,'mark':{'type':'line','color':'#167969','strokeWidth':3},
          'encoding':{'x':{'field':'出力 MW','type':'quantitative'},'y':{'field':'报价 元/MWh','type':'quantitative'},
                      'order':{'field':'序','type':'quantitative'},'tooltip':[{'field':x} for x in ('段','出力 MW','报价 元/MWh')]}},width='stretch')
    with detail:
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
