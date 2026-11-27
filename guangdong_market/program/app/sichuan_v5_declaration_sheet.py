"""Single-page review of the public coupled declaration."""
from html import escape

import pandas as pd
import streamlit as st


def clock(q):
    return f'{q//4:02}:{15*(q%4):02}'


def render_sheet(sheet, scenario):
    company = sheet['company']
    stations = sheet['inputs']['stations']
    offers = company['offers']
    water = company['hydraulic_declaration']
    names = {s['id']:s['name'] for s in stations}
    count = max(len(o['segments']) for o in offers.values())
    rows, hours, limits = [], [], []
    for station in stations:
        uid, name = station['id'], escape(station['name'])
        o = offers[uid]
        cells = []
        end = 0
        for width, price in o['segments']:
            end += width
            limit = f'{end:,.2f}'.rstrip('0').rstrip('.')
            cells.append(f'<td><b>{price:g}</b><small>{limit}</small></td>')
        cells.extend('<td class="absent">—</td>' for _ in range(count-len(cells)))
        rows.append(f'<tr><th>{name}</th>{"".join(cells)}</tr>')
        limits.append({'电站':name,'日最大可发 MWh':o['daily_energy_mwh'],
                       '连续三小时 MW':o['sustained_3h_mw'],'起始时刻':clock(o['sustained_start_quarter']),
                       '全天可达上限 MW':o['available_mw'],'全天区间来水 m³/s':o['natural_inflow_m3s']})
        for q in range(len(o['available_mw'])):
            hours.append({'电站':name,'时段起':clock(q),'时段止':clock(q+1),
                          '可达下限 MW':o['minimum_mw'][q],'可达上限 MW':o['available_mw'][q],
                          '技术可用 MW':o['technical_available_mw'][q],'区间来水 m³/s':o['natural_inflow_m3s'][q]})
    st.html(f"""<style>
    .dd-head{{background:#123e46;color:#fff;border-left:6px solid #55bcaa;border-radius:9px;padding:16px 22px;margin:3px 0 10px}}
    .dd-head strong{{font-size:1.25rem}} .dd-head span{{display:block;color:#d0e4e0;font-size:.9rem;margin-top:4px}}
    .dd-scroll{{overflow-x:auto;border:1px solid #d1dfdf;border-radius:8px}}
    .dd-grid{{width:100%;border-collapse:collapse;font-size:.86rem;background:#fff;color:#153c43;font-variant-numeric:tabular-nums}}
    .dd-grid th,.dd-grid td{{padding:5px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid #e2eaea}}
    .dd-grid thead th{{background:#e6f1ee;color:#285851}} .dd-grid tbody th{{text-align:left;position:sticky;left:0;background:#f4f8f7}}
    .dd-grid tbody tr:nth-child(even){{background:#f6faf9}} .dd-grid small{{display:block;color:#486069;font-size:.72rem}} .dd-grid .absent{{color:#aebfc0}}
    </style><div class="dd-head"><strong>{escape(scenario)} · 日前水电耦合申报单</strong>
    <span>{len(stations)} 座电站　·　{sum(len(o['segments']) for o in offers.values())} 个量价段　·　96 个时段　·　模拟申报时点 D−1 09:45</span></div>""")
    st.subheader('分站量价申报')
    st.caption('按大渡河从上游至下游排列。每格上行为申报价（元/MWh），下行为该段累计出力终点（MW）；首段从 0 MW 起。')
    headers = ''.join(f'<th>第 {i+1} 段</th>' for i in range(count))
    st.html(f'<div class="dd-scroll"><table class="dd-grid"><thead><tr><th>电站</th>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    st.subheader('可发能力与预测来水')
    st.dataframe(limits, hide_index=True,width='stretch',height='content',column_config={
        '全天可达上限 MW':st.column_config.LineChartColumn('96 点可达上限 MW'),
        '全天区间来水 m³/s':st.column_config.LineChartColumn('96 点区间来水 m³/s')})
    st.caption('逐时段原值 · 北京时间，每段 15 分钟')
    st.dataframe(pd.DataFrame(hours).sort_values(['时段起'],kind='stable'),hide_index=True,width='stretch',height=244)
    st.subheader('水库与下泄边界')
    st.dataframe([{'电站':names[r['id']],'类型':'库容式' if r['regulation']=='reservoir' else '径流式',
          '期初库容 m³':r['initial_storage_m3'],'最低库容 m³':r['storage_min_m3'],
          '最高库容 m³':r['storage_max_m3'],'期末下限 m³':r['terminal_min_m3'],'期末上限 m³':r['terminal_max_m3'],
          '最小总下泄 m³/s':r['min_release_m3s'],'最大总下泄 m³/s':r['max_release_m3s'],
          '最小旁泄 m³/s':r['min_bypass_m3s'],'最大弃水 m³/s':r['max_spill_m3s'],
          '耗水率 m³/kWh':offers[r['id']]['consumption_m3_kwh']} for r in water['reservoirs']],
          width='stretch',hide_index=True,height='content')
    st.subheader('河段传播与期初在途水量')
    st.dataframe([{'上游站':names[r['upstream']],'下游站':names[r['downstream']],
          '传播时间 min':15*r['travel_quarters'],'期初在途 m³':sum(r['initial_arrivals_m3']),
          '分时段到水 m³':r['initial_arrivals_m3']} for r in water['reaches']],width='stretch',hide_index=True,height='content')
