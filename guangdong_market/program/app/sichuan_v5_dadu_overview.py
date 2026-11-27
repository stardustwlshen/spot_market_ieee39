"""Read-only summaries, nodal prices and paired comparisons of archived executions."""
from math import fsum

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart


def _clock(q):
    m = 15 * q
    return f'{m//60:02d}:{m%60:02d}'


def _value(row, key):
    return row.get(key, row.get(str(key)))


def _sum_shed(row):
    return fsum(row['shed'].values())


def _money(v):
    return '待核' if v is None else f'{v/10000:,.2f} 万元'


def render_overview(result):
    rt, da = result['rt'], result['da']
    n = len(rt)
    demand = [fsum(r['demand'].values()) for r in rt]
    shortages = [_sum_shed(r) for r in rt]
    count = sum(v > 1e-5 for v in shortages)
    energy = .25 * fsum(demand)
    unserved = .25 * fsum(shortages)
    cash = result['summary']['company_revenue']
    st.html("""<style>@media (max-width: 640px) {
        .st-key-dadu_overview_metrics [data-testid="stHorizontalBlock"] {
            display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.4rem .8rem;
        }
        .st-key-dadu_overview_metrics [data-testid="stColumn"] {width:100%; min-width:0;}
    }</style>""")
    with st.container(key='dadu_overview_metrics'):
        c = st.columns(4)
    c[0].metric('已执行负荷电量', f'{energy/1000:,.2f} GWh')
    c[1].metric('已执行最大负荷', f'{max(demand):,.0f} MW')
    c[2].metric('失负荷电量', f'{unserved:,.2f} MWh')
    c[3].metric('组合现货毛收入', _money(cash))
    peak = max(range(n), key=shortages.__getitem__)
    if count:
        nodal = rt[peak]['shed']
        worst_bus = max(nodal, key=nodal.get)
        st.warning(f'{n} 个已执行段中，{count} 段出现失负荷。最大缺口在 '
                   f'{_clock(peak)}–{_clock(peak+1)}，全网 {shortages[peak]:,.2f} MW，'
                   f'其中节点 {worst_bus} 为 {nodal[worst_bus]:,.2f} MW。这些失负荷时段的采用节点价和现金待核。')
    elif result['money']['data_quality'] != 'verified':
        st.info('已执行段未出现失负荷。计价覆盖和待核原因见「收入与对照」。')
    else:
        st.caption('已执行段失负荷为零。')
    if n < len(da):
        st.caption(f'日前覆盖 {len(da)} 段。实时电量与现金只累计到 {_clock(n)}，日末水账待执行至 24:00。')


def render_node_prices(result, station, quarter):
    da, rt, money = result['da'], result['rt'], result['money']
    with st.expander('39 节点价格与有效限价'):
        buses = sorted(int(b) for b in da[0]['raw_nodal_prices'])
        uid = st.selectbox('电价节点', buses, index=buses.index(int(station['bus'])),
                           key=f"dadu_price_bus_{result['run']}_{station['id']}")
        by_q = {p['quarter']:p for p in money['quarters']}
        series = []
        for r in da:
            q = r['quarter']
            p = by_q.get(q)
            series.append({'时段末':_clock(q+1), '日前节点参考价':_value(r['raw_nodal_prices'],uid),
                           '实时原始节点价':_value(p['raw_price_by_bus'],uid) if p else None,
                           '实时采用节点价':_value(p['effective_price_by_bus'],uid) if p else None,
                           '统一结算点电价':p['uniform_price'] if p else None})
        frame = pd.DataFrame(series).set_index('时段末')
        time_chart(frame, '元/MWh')
        p = by_q[rt[quarter]['quarter']]
        table = pd.DataFrame([{'节点':b,'日前节点参考价':_value(da[quarter]['raw_nodal_prices'],b),
                               '实时原始节点价':_value(p['raw_price_by_bus'],b),
                               '实时采用节点价':_value(p['effective_price_by_bus'],b)}
                              for b in buses])
        st.dataframe(table, hide_index=True)
        policy = result['policy']
        st.caption(f"所选段末 {_clock(quarter+1)}。研究限价 {policy['cap']:.2f} 元/MWh。"
                   "日前节点价用于参考，收入取有效实时节点价；零价显示为 0，暂停计价留空。")
        st.download_button('下载所选节点全日价格',frame.to_csv().encode('utf-8-sig'),
                           f"bus{uid}-prices.csv",'text/csv',key=f"dadu_prices_csv_{result['run']}_{uid}")


def _pair_evidence(result):
    a, c, s = result.get('inputs', {}), result.get('company', {}), result.get('summary', {})
    return (a.get('scenario'), a.get('seed'), len(result['rt']), a.get('source_hashes'),
            s.get('boundary_hash'), s.get('issued_boundary_hash'), c.get('company_forecast_sha256'))


def matched_pair(left, right):
    evidence = _pair_evidence(left)
    left_bid = left.get('summary', {}).get('bid_hash')
    right_bid = right.get('summary', {}).get('bid_hash')
    return (bool(left_bid and right_bid) and all(v is not None and v != '' for v in evidence)
            and bool(evidence[3]) and evidence == _pair_evidence(right)
            and {left.get('company', {}).get('strategy'), right.get('company', {}).get('strategy')} == {'water_value','naive'}
            and left_bid != right_bid)


def pair_metrics(result):
    rt, s = result['rt'], result['summary']
    hydro = [x['id'] for x in result['inputs']['assets'] if x['kind']=='hydro']
    shed = [_sum_shed(r) for r in rt]
    return {
        '水电实发（MWh）': .25 * fsum(r['power'][i] for r in rt for i in hydro),
        '失负荷（MWh）': .25*fsum(shed),
        '最大失负荷（MW）': max(shed),
        '气电保供（MWh）': s['gas_mwh'],
        '光伏弃电（MWh）': s['solar_curtail_mwh'],
        '逐站弃水合计（百万m³）': s['water_spill_m3']/1e6,
        '组合现货毛收入（万元）': None if s['company_revenue'] is None else s['company_revenue']/1e4,
        '含期末库存及在途水估值的研究收益（万元）': (
            s['water_adjusted_value']/1e4 if len(rt)==96 and s.get('water_adjusted_value') is not None else None)
    }


def slot_metrics(result, station, quarter):
    row, planned = result['rt'][quarter], result['da'][quarter]
    uid, bus = station['id'], station['bus']
    hydro = row['hydro'][uid]
    types = result['inputs']['assets']
    metrics = {
        '本站日前中标（MW）': planned['power'][uid], '本站实时实发（MW）': row['power'][uid],
        '本站采用节点价（元/MWh）': _value(result['money']['quarters'][quarter]['effective_price_by_bus'], bus),
        '本站上游到水（m³/s）': hydro['upstream_arrival_m3']/900,
        '本站弃水（m³/s）': hydro['water_spill_m3s'],
    }
    if station['model']=='reservoir':
        metrics['本站段末库容（百万m³）'] = hydro['storage_m3']/1e6
    for kind, label in (('hydro','系统水电实发（MW）'),('thermal','系统煤电实发（MW）')):
        metrics[label] = fsum(row['power'][a['id']] for a in types if a['kind']==kind)
    metrics['系统失负荷（MW）'] = _sum_shed(row)
    return metrics


def _switch_run(name):
    st.session_state['dadu_saved'] = name


def render_pair(root, receipts, result, station, quarter):
    from sichuan_v5_dadu_run import read_dadu
    selected = result['inputs']
    strategy = result['company']['strategy']
    other = 'naive' if strategy=='water_value' else 'water_value'
    candidates = [name for name, info in receipts.items()
                  if info.get('scenario') == selected['scenario'] and info.get('strategy') == other
                  and info.get('periods') == len(result['rt'])
                  and info.get('seed') == selected['seed']
                  and info.get('boundary_hash') == result['summary']['boundary_hash']]
    st.markdown('**同一市场条件下的两种报价**')
    if not candidates:
        st.caption('当前归档缺少同边界另一策略；可用页面下方的六算例入口补算。')
        return
    for name in candidates:
        try:
            companion = read_dadu(root, name)
        except (ValueError, OSError, KeyError):
            continue
        if matched_pair(result,companion):
            break
    else:
        st.caption('已有档案的求解版本或预测签发记录不同，策略差值留空。')
        return
    w, n = (result,companion) if strategy=='water_value' else (companion,result)
    scope = st.radio('对照范围', ('所选时段','已执行累计'), horizontal=True, key='dadu_pair_scope')
    if scope=='所选时段':
        wm, nm = slot_metrics(w, station, quarter), slot_metrics(n, station, quarter)
        st.caption(f"{station['name']} · {_clock(quarter)}–{_clock(quarter+1)} · 第 {quarter+1} 段。")
    else:
        wm, nm = pair_metrics(w), pair_metrics(n)
    rows = [{'指标':k, '水价值报价':v, '朴素报价':nm[k], '水价值减朴素':
             v-nm[k] if v is not None and nm[k] is not None else None} for k,v in wm.items()]
    st.dataframe(rows, hide_index=True)
    st.button('回放另一策略的同站同时段', on_click=_switch_run, args=(companion['run'],),
              key='dadu_switch_pair_'+result['run'])
    uid = station['id']
    df = pd.DataFrame([{'时段末':_clock(a['quarter']+1),
                        '水价值报价':a['power'][uid], '朴素报价':b['power'][uid]}
                       for a,b in zip(w['rt'],n['rt'])]).set_index('时段末')
    time_chart(df, f"{station['name']}实发（MW）")
    st.caption('逐站弃水合计允许同一股水在多个电站被统计。研究收益包括按事前水价值折算的库水和在途水量变动；缺少有效现金时停止比较收益。')
