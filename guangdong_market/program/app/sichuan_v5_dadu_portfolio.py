"""Company-wide offers as known when the 19 individual submissions were frozen."""
from math import fsum
from html import escape

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart


def render_river_offers(result, quarter, table, segments):
    stations = result['inputs']['stations']
    offers = result['company']['offers']
    names = {s['id']: s['name'] for s in stations}
    reaches = result['inputs'].get('market_case', {}).get('cascade', {}).get('reaches', [])
    downstream = {r['upstream']: r for r in reaches}
    st.subheader('大渡河梯级与多站日前计划')
    st.write('公司按全流域来水、库存和传输时滞联合安排用水，再按电站分别提交日分段量价曲线及分时出力边界。日前市场按冻结申报和独立水电边界预出清；实时假设梯级选择水电耦合并通过审核。')
    st.caption('沿干流从上游向下游阅读。深蓝为库容式，浅色为径流式；箭头旁为模拟河段传播时间。')
    chips = []
    for station in stations:
        uid = station['id']
        reach = downstream.get(uid)
        fill, ink = ('#155471', '#fff') if station['model']=='reservoir' else ('#e1f2ef', '#154c4a')
        link = f" → {15 * reach['travel_quarters']} 分钟" if reach else ''
        chips.append(f'<span style="display:inline-flex;align-items:center;gap:.25rem">'
                     f'<b style="padding:.28rem .5rem;border-radius:.4rem;background:{fill};color:{ink}">'
                     f'{escape(names[uid])}</b><small>{link}</small></span>')
    st.html('<div style="display:flex;flex-wrap:wrap;gap:.55rem;align-items:center">' + ''.join(chips) + '</div>')
    selected = st.multiselect('对照电站（可选全部 19 站）', list(names),
                              default=[s['id'] for s in stations if s['model']=='reservoir'],
                              format_func=names.get, key='river_compare_'+result['run'])
    if not selected:
        st.info('请选择至少一座电站查看计划与申报。')
        return
    plans = result['company']['internal_plan_mw']
    stamps = [f'{(i+1)//4:02}:{15*((i+1)%4):02}' for i in range(len(plans))]
    metric = st.radio('96 点对照指标', ['内部计划 MW','申报可达上限 MW','公司预测区间天然来水 m³/s'],
                      horizontal=True, key='river_metric_'+result['run'])
    fields = {'申报可达上限 MW':'available_mw','公司预测区间天然来水 m³/s':'natural_inflow_m3s'}
    data = {names[u]: ([row[u] for row in plans] if metric not in fields else
                        offers[u].get(fields[metric], [None]*len(plans))) for u in selected}
    time_chart(pd.DataFrame(data, index=stamps), metric)
    selected_names = list(data)
    st.caption(f'所选时段截至 {stamps[quarter]}。以下按站核对内部计划、申报上下限和设计装机。')
    st.dataframe(table[table['电站'].isin(selected_names)], hide_index=True, width='stretch')
    st.markdown('**各站冻结报价曲线**')
    rows = []
    for name in selected_names:
        for band in segments[segments['电站']==name].to_dict('records'):
            for endpoint in ('起点 MW', '终点 MW'):
                rows.append({'电站':name, '累计申报 MW':band[endpoint],
                             '申报价 元/MWh':band['冻结申报价 元/MWh'], '顺序':len(rows)})
    st.vega_lite_chart(pd.DataFrame(rows), {'height':230, 'mark':'line', 'encoding':{
        'x':{'field':'累计申报 MW','type':'quantitative'}, 'y':{'field':'申报价 元/MWh','type':'quantitative'},
        'order':{'field':'顺序','type':'quantitative'}, 'color':{'field':'电站','type':'nominal'},
        'tooltip':[{'field':key} for key in ('电站','累计申报 MW','申报价 元/MWh')]}}, width='stretch')


def portfolio_offers(result, quarter):
    company = result['company']
    offers = company['offers']
    internal = company['internal_plan_mw']
    table, segments = [], []
    for station in result['inputs']['stations']:
        uid, name = station['id'], station['name']
        offer = offers[uid]
        points = offer['segments']
        tech = offer.get('technical_available_mw')
        minimum = offer.get('minimum_mw')
        table.append({
            '电站': name, '类型': '库容式' if station['model']=='reservoir' else '径流式',
            '设计装机 MW': station['capacity_mw'],
            '当段技术可用 MW': tech[quarter] if tech is not None else None,
            '当段内部计划 MW': internal[quarter][uid],
            '当段申报可达下限 MW': minimum[quarter] if minimum is not None else None,
            '当段申报可达上限 MW': offer['available_mw'][quarter],
            '内部计划日电量 MWh': .25 * fsum(row[uid] for row in internal),
            '首段价 元/MWh': points[0][1], '末段价 元/MWh': points[-1][1],
        })
        start = 0.
        for i, (width, price) in enumerate(points, 1):
            stop = start + width
            segments.append({'电站': name, '段号': i, '起点 MW': start,
                             '终点 MW': stop, '段宽 MW': width, '冻结申报价 元/MWh': price})
            start = stop
    return pd.DataFrame(table), pd.DataFrame(segments)


def render_company_portfolio(result, quarter):
    company = result['company']
    table, segments = portfolio_offers(result, quarter)
    total = fsum(table['内部计划日电量 MWh'])
    price = segments['冻结申报价 元/MWh']
    st.subheader('公司冻结申报')
    st.caption(f"{company['as_of']} 签发 · {len(table)} 站分别向市场提交日分段曲线。全组合由同一模拟报价团队统筹。")
    c = st.columns(3)
    c[0].metric('组合内部计划电量', f'{total/1000:,.2f} GWh')
    c[1].metric('各站提交的报价段', f'{len(segments)} 段')
    c[2].metric('冻结报价范围', f'{price.min():,.0f}–{price.max():,.0f} 元/MWh')
    render_river_offers(result, quarter, table, segments)
    with st.expander('19 站报量、报价及组合内部计划'):
        st.dataframe(table, hide_index=True, width='stretch', placeholder='待核')
        series = [{kind: fsum(row[s['id']] for s in result['inputs']['stations']
                              if (s['model']=='reservoir')==reservoir)
                   for kind, reservoir in (('四库内部计划', True), ('径流式内部计划', False))}
                  for row in company['internal_plan_mw']]
        end = [f'{(i+1)//4:02}:{15*((i+1)%4):02}' for i in range(len(series))]
        time_chart(pd.DataFrame(series, index=end), '内部计划 MW', area=True)
        st.caption('内部计划为同一次梯级联合优化的结果，可以加总。各站单独求得的可达上界受共享水量约束，逐站上界之和不能作为同时可发能力。')
        st.download_button('下载 19 站冻结分段报价', segments.to_csv(index=False).encode('utf-8-sig'),
                           f"{result['run']}-frozen-offers.csv", 'text/csv',
                           key='dadu_offer_export_'+result['run'])
