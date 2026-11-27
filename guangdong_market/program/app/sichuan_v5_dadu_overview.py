"""Read-only summaries, nodal prices and paired comparisons of archived executions."""
from math import fsum

import pandas as pd
import streamlit as st


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
                   f'其中节点 {worst_bus} 为 {nodal[worst_bus]:,.2f} MW。')
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
        st.line_chart(frame, x_label='时段末（北京时间）', y_label='元/MWh')
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
    a, c, s = result['inputs'], result['company'], result['summary']
    return (a.get('scenario'), a.get('seed'), len(result['rt']), a.get('source_hashes'),
            s.get('boundary_hash'), s.get('issued_boundary_hash'), c.get('company_forecast_sha256'))


def matched_pair(left, right):
    evidence = _pair_evidence(left)
    return (all(v is not None and v != '' for v in evidence)
            and bool(evidence[3]) and evidence == _pair_evidence(right)
            and {left['company']['strategy'], right['company']['strategy']} == {'water_value','naive'}
            and left['summary']['bid_hash'] != right['summary']['bid_hash'])


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


def render_pair(root, receipts, result, station):
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
    wm, nm = pair_metrics(w), pair_metrics(n)
    rows = [{'指标':k, '水价值报价':v, '朴素报价':nm[k], '水价值减朴素':
             v-nm[k] if v is not None and nm[k] is not None else None} for k,v in wm.items()]
    st.dataframe(rows, hide_index=True)
    uid = station['id']
    df = pd.DataFrame([{'时段末':_clock(a['quarter']+1),
                        '水价值报价':a['power'][uid], '朴素报价':b['power'][uid]}
                       for a,b in zip(w['rt'],n['rt'])]).set_index('时段末')
    st.line_chart(df, x_label='时段末（北京时间）', y_label=f"{station['name']}实发（MW）")
    st.caption('逐站弃水合计允许同一股水在多个电站被统计。研究收益包括按事前水价值折算的库水和在途水量变动；缺少有效现金时停止比较收益。')
