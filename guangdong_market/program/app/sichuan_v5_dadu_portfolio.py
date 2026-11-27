"""Company-wide offers as known when the 19 individual submissions were frozen."""
from math import fsum

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart


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
