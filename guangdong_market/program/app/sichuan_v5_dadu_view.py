"""Operator, bidder and settlement views of a frozen Dadu River research run."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from math import fsum
from pathlib import Path

import pandas as pd
import streamlit as st


SCENARIOS = {'wet_low': '丰水低负荷', 'dry_peak': '枯水晚峰', 'forecast_error': '预测偏差'}
STRATEGIES = {'default':'默认耦合申报', 'water_value': '水价值报价', 'naive': '朴素报价'}
KINDS = {'hydro': '水电', 'thermal': '煤电', 'renewable': '光伏', 'fixed': '气电保供'}


def _end_label(quarter):
    minutes = (quarter + 1) * 15
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _number(value, precision=2):
    return '—' if value is None else f'{float(value):,.{precision}f}'


def _bus_value(values, bus):
    return values.get(bus, values.get(str(bus)))


PRICE_ISSUES = {
    'dispatch_not_verified':'执行量未通过可靠性核验', 'missing_quarter':'执行时段缺失',
    'missing_price_cap':'有效限价缺失', 'missing_metered_quantity':'计量电量缺失',
    'missing_cleared_quantity':'出清电量缺失', 'unavailable_uniform_price':'统一结算点电价缺失',
    'missing_rt_nodal_price':'实时节点价缺失', 'missing_rt_uniform_price':'统一结算点电价缺失',
    'incomplete_hour':'小时内执行段数不完整',
}


def reliability_rows(rt, money):
    """Reconstruct system totals from nodal execution, including older archives."""
    output = []
    prices = {row['quarter']:row for row in money['quarters']}
    for row in rt:
        q = row['quarter']
        shed, spill = row['shed'], row['spill']
        shed_mw, spill_mw = fsum(shed.values()), fsum(spill.values())
        short_mw = row['reliability_reserve_short_mw']
        issues = []
        for value, label in ((shed_mw,'失负荷'), (spill_mw,'电力过剩松弛'), (short_mw,'可靠性备用缺额')):
            if value > 1e-5:
                issues.append(label)
        source = prices.get(q,{})
        if not issues and source.get('reliable') is False:
            issues.append('执行量未通过可靠性核验')
        for code in source.get('issues',()):
            if code != 'dispatch_not_verified' or not issues:
                label = PRICE_ISSUES.get(code,code)
                if label not in issues:
                    issues.append(label)
        if not issues:
            continue
        shed_bus = max(shed, key=shed.get) if shed_mw>1e-5 else None
        spill_bus = max(spill, key=spill.get) if spill_mw>1e-5 else None
        output.append({'时段末':_end_label(q), '系统失负荷 MW':shed_mw, '系统过剩 MW':spill_mw,
                       '备用缺额 MW':short_mw, '最大失负荷节点':str(shed_bus) if shed_bus is not None else '—',
                       '该节点失负荷 MW':shed.get(shed_bus,0.), '最大过剩节点':str(spill_bus) if spill_bus is not None else '—',
                       '该节点过剩 MW':spill.get(spill_bus,0.), '暂停计价或需复核原因':'、'.join(issues)})
    return output


def _receipt(root, name):
    try:
        return json.loads((Path(root)/'dadu19'/name/'receipt.json').read_bytes())
    except (ValueError, OSError):
        return {}


def _archive_label(name, info):
    if name is None:
        return '选择已保存运行'
    try:
        stamp = datetime.strptime(name.split('_')[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        local = stamp.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')
        return (f"{local} · {SCENARIOS[info['scenario']]} · {STRATEGIES[info['strategy']]} · "
                f"{info['periods']}/96 段 · {name[-6:]}")
    except (ValueError, OSError, IndexError, KeyError, TypeError):
        return name


def _chart(series, unit, *, area=False):
    """Series share a physical unit. Missing prices and uncovered RT slots stay gaps."""
    size = max((len(values) for values in series.values()), default=0)
    if not size:
        return
    frame = pd.DataFrame(
        {label: [None if v is None else float(v) for v in values] + [None] * (size-len(values))
         for label, values in series.items()},
        index=[_end_label(q) for q in range(size)],
    ).rename_axis('时段末')
    from sichuan_v5_dadu_charts import time_chart
    time_chart(frame, unit, area=area)


def _asset_groups(assets):
    groups = {}
    for asset in assets:
        groups.setdefault(KINDS[asset['kind']], []).append(asset['id'])
    return groups


def _total(row, ids):
    return sum(row['power'][uid] for uid in ids)


def _submitted(curve, start=0.):
    rows = []
    for width, price in curve:
        stop = start + float(width)
        rows.append({'起点 MW': round(start, 2), '终点 MW': round(stop, 2),
                     '申报价（元/MWh）': float(price)})
        start = stop
    return rows


def _boundary_digest(summary):
    return summary.get('boundary_hash')


def comparison_row(scenario, strategy, result):
    summary = result['summary']
    portfolio = result['money']['portfolio']
    volumes = [hour['energy_mwh'] for hour in portfolio]
    mwh = sum(volumes) if all(value is not None for value in volumes) else None
    if result.get('inputs',{}).get('stations'):
        members = [s['id'] for s in result['inputs']['stations']]
        mwh = .25 * fsum(r['power'][i] for r in result['rt'] for i in members)
    shortfalls = [fsum(r['shed'].values()) for r in result['rt'] if 'shed' in r]
    income = summary['company_revenue']
    complete = (len(result['rt']) == 96 and summary['max_slack_mw']<=1e-5 and income is not None
                and result['money'].get('data_quality', 'verified')=='verified')
    return {'场景': SCENARIOS[scenario], '报价策略': STRATEGIES[strategy],
            '已执行时段': len(result['rt']),
            '实时可靠性': ('压力场景出现松弛' if scenario=='forecast_error' else '基准未通过')
                        if summary['max_slack_mw']>1e-5 else '已执行段无松弛',
            '水电实发（MWh）': mwh,
            '实发加权价（元/MWh）':income/mwh if mwh and income is not None else None,
            '组合现货毛收入（元）': income,
            '库存及在途水估值变化（元）': summary.get('water_stock_value_change') if complete else None,
            '全天含水库存估值的研究收益（元）': summary.get('water_adjusted_value') if complete else None,
            '各站弃水之和（m³）': summary['water_spill_m3'], '光伏弃电（MWh）': summary['solar_curtail_mwh'],
            '气电保供（MWh）': summary['gas_mwh'],
            '失负荷（MWh）':.25*fsum(shortfalls) if shortfalls else None,
            '失负荷段数':sum(v>1e-5 for v in shortfalls) if shortfalls else None,
            '边界摘要': _boundary_digest(summary)}


def _company_tab(results, station):
    company = results['company']
    uid = station['id']
    offer = company['offers'][uid]
    engineering = station.get('engineering_name')
    display = station['name'] if not engineering or engineering==station['name'] else f"{station['name']} / {engineering}"
    match = station.get('match') or '待核'
    st.caption(f"{display} · 台账主体 {station.get('company') or '待核'}（{match}） · IEEE39 合成节点 {station['bus']}")
    columns = st.columns(3)
    columns[0].metric('设计装机', _number(station['capacity_mw'], 0)+' MW')
    columns[1].metric('单站最大可发日电量', _number(offer.get('daily_energy_mwh'))+' MWh')
    columns[2].metric('单站连续 3 小时可达最大出力', _number(offer.get('sustained_3h_mw'))+' MW')
    st.markdown('**内部计划与申报边界**')
    series = {'公司内部发电计划': [r[uid] for r in company['internal_plan_mw']],
              '单站申报可达上界': offer['available_mw']}
    if 'minimum_mw' in offer:
        series['单站申报可达下界'] = offer['minimum_mw']
    if 'technical_available_mw' in offer:
        series['机组技术可用上限'] = offer['technical_available_mw']
    _chart(series, 'MW')
    st.caption(f"内部计划日电量 {_number(.25*sum(series['公司内部发电计划']))} MWh。"
               '逐时、全日和连续三小时能力分别计算，各站独立最大值同时实现还须通过梯级联合校验。')
    prices = {}
    for field, label in (('expected_price_yuan_mwh', '事前预期节点价'),
                         ('marginal_water_cost_yuan_mwh', '水的边际机会成本')):
        records = company.get(field) or []
        if len(records) == len(company['internal_plan_mw']):
            prices[label] = [r.get(uid) for r in records]
    if prices:
        _chart(prices, '元/MWh')
    if len(prices) < 2:
        st.caption('这份归档缺少完整的事前预期价或边际水机会成本。')
    with st.expander('核对申报版本和站名'):
        from sichuan_v5_dadu_case import RULE_PDF_SHA256
        recorded = results['inputs'].get('assumptions', {}).get('rules_pdf_sha256')
        st.caption('本归档的规则文件摘要（SHA-256）：' + (recorded or '未记录'))
        if recorded != RULE_PDF_SHA256:
            st.caption('该归档保留早期规则来源记录。当前复核的合订稿为 ' + RULE_PDF_SHA256 + '。')
        st.write({'签发时刻':company.get('as_of'), '申报摘要':results['summary']['bid_hash'],
                  '工程名':station.get('engineering_name'), '名称匹配':station.get('match'),
                  '工程调节性能':station.get('regulation'),
                  '本场景建模':'库容式' if station.get('model')=='reservoir' else '径流式近似',
                  '可用性依据':station.get('availability_status'), '市场资格':station.get('qualification_status'),
                  '期末水价值（元/m³）':company.get('water_value_yuan_m3',{}).get(uid)})


def _market_tab(results, station, quarter):
    inputs, da = results['inputs'], results['da']
    st.markdown('**逐资源日前申报与预出清**')
    name_by_id = {a['id']:a for a in inputs['assets']}
    resource_ids = list(name_by_id)
    uid = st.selectbox('查看电源的日前量价', resource_ids, index=resource_ids.index(station['id']),
                       key=f"dadu_market_{results['run']}_{station['id']}",
                       format_func=lambda u:name_by_id[u]['name']+' · '+KINDS[name_by_id[u]['kind']])
    asset = name_by_id[uid]
    series = {'日前预出清': [r['power'][uid] for r in da]}
    if asset['kind'] == 'hydro':
        series['公司内部计划'] = [r[uid] for r in results['company']['internal_plan_mw']]
    elif asset['kind'] == 'fixed':
        series['非市场保供计划'] = [r[uid] for r in inputs['fixed_schedule']]
    else:
        series['可用出力上限'] = [row.get(uid, asset['capacity_mw']) for row in inputs['available']]
    _chart(series, 'MW')
    price = [_bus_value(row['raw_nodal_prices'], asset['bus']) for row in da]
    left, right = st.columns(2)
    with left:
        if asset['kind'] == 'fixed':
            st.caption('气电执行保供计划，没有竞价申报。右图为所在节点的市场参考价。')
        else:
            st.markdown('**申报与实际进入出清的报价**')
            raw = results['raw_offers'][uid]
            effective = results['effective_offers'][uid]
            curves = [('申报及有效', raw)] if raw == effective else [('原申报', raw), ('有效', effective)]
            resources = inputs['market_case']['resources']
            minimum = next((u['minimum_mw'] for u in resources if u['id']==uid),0.) if asset['kind']=='thermal' else 0.
            st.dataframe([dict(曲线=label, **row) for label,curve in curves for row in _submitted(curve, minimum)],
                         hide_index=True)
            if asset['kind'] == 'thermal':
                st.caption(f'分段申报从最低稳定出力 {minimum:g} MW 起算。最低出力以下的运行成本单独计入优化目标。')
    with right:
        _chart({'日前参考节点价（原始对偶）':price}, '元/MWh')
    st.caption('日前为 96 点预出清，电量和节点价供对照。研究收入使用实时执行量和相应的实时价格。')


def _realtime_tab(results, station, quarter):
    inputs, da, rt, money = results['inputs'], results['da'], results['rt'], results['money']
    uid, bus = station['id'], station['bus']
    ids = _asset_groups(inputs['assets'])
    diagnostics = reliability_rows(rt, money)
    if diagnostics:
        affected = next((r for r in diagnostics if r['时段末']==_end_label(quarter)),None)
        if affected:
            st.warning(f"所选时段需复核：{affected['暂停计价或需复核原因']}。系统失负荷 "
                       f"{_number(affected['系统失负荷 MW'])} MW，过剩 {_number(affected['系统过剩 MW'])} MW，"
                       f"备用缺额 {_number(affected['备用缺额 MW'])} MW。")
        with st.expander(f'实时异常时段与计价原因（{len(diagnostics)} 段）',expanded=bool(affected)):
            st.dataframe(diagnostics, hide_index=True, placeholder="—")
            st.caption('系统失负荷和过剩分别对所有节点求和；节点列单列当地最大值。'
                       '可靠性松弛时暂停该段研究计价，并影响所在小时的收入。')
    st.markdown('**实时各类型出力**')
    _chart({label:[_total(row, units) for row in rt] for label,units in ids.items()}, 'MW', area=True)
    generation = [row['power'][uid] for row in rt]
    plan = [row['power'][uid] for row in da[:len(rt)]]
    cols = st.columns(3)
    cols[0].metric(station['name']+'实发', _number(generation[quarter])+' MW')
    cols[1].metric('较日前偏差', _number(generation[quarter]-plan[quarter])+' MW')
    price = _bus_value(money['quarters'][quarter]['effective_price_by_bus'], bus)
    cols[2].metric('采用的实时节点价', _number(price)+' 元/MWh')
    _chart({'日前预出清': plan, '实时实发': generation}, 'MW')
    _chart({'原始实时节点价': [_bus_value(r['raw_price_by_bus'], bus) for r in money['quarters']],
            '采用的实时节点价': [_bus_value(r['effective_price_by_bus'], bus) for r in money['quarters']],
            '统一结算点电价': [r['uniform_price'] for r in money['quarters']]}, '元/MWh')
    with st.expander('气电非市场计划与保供'):
        gas = ids['气电保供']
        st.dataframe([{'时段末':_end_label(t), '机组':g, '计划 MW':inputs['fixed_schedule'][t][g],
                       '实发 MW':r['power'][g]} for t,r in enumerate(rt) for g in gas], hide_index=True)
        st.caption('气电按非市场计划注入，单列保供电量，不进入公司现货收入和统一价权重。')
    with st.expander('小时能量收入与价格质量'):
        values = [{'小时':r['hour'], '上网电量 MWh':r['energy_mwh'], '实时加权价 元/MWh':r['rt_nodal_price'],
                   '研究收入 元':r['revenue']} for r in money['hourly'] if r['resource_id']==uid]
        st.dataframe(values, hide_index=True, placeholder="—")
        st.caption('零电量时加权均价留空。收入受价格缺失或可靠性松弛影响时保留缺项。')
    with st.expander('线路潮流与运行边界'):
        flows = rt[quarter]['flows']
        limits = {line['id']:line['limit_mw'] for line in inputs['lines']}
        loading = [{'线路':key, '所选时段潮流 MW':round(value, 3), '限额 MW':round(limits[key], 3),
                    '使用率 %':round(100*abs(value)/limits[key], 2)} for key,value in flows.items()]
        st.dataframe(sorted(loading, key=lambda r:r['使用率 %'], reverse=True), hide_index=True, placeholder="—")


def _remember_focus(field, widget):
    st.session_state['dadu_focus_'+field] = st.session_state[widget]


def _set_quarter(name, quarter):
    st.session_state['dadu_q_'+name] = quarter
    st.session_state['dadu_focus_quarter'] = quarter


def _results(name, results, root, receipts, draft):
    from sichuan_v5_dadu_company_results import render_company_detail, render_cash_detail
    from sichuan_v5_dadu_grid_results import render_grid_detail
    from sichuan_v5_dadu_water_results import render_water_detail
    from sichuan_v5_dadu_overview import render_overview, render_node_prices, render_pair
    from sichuan_v5_dadu_portfolio import render_company_portfolio
    from sichuan_v5_dadu_bid_decision import render_bid_decision
    from sichuan_v5_dadu_market_stages import render_day_ahead, render_real_time
    from sichuan_v5_dadu_basin import render_basin
    inputs = results['inputs']
    rt = results['rt']
    n = len(rt)
    st.subheader('运行结果')
    st.caption(f"当前回放：{SCENARIOS[inputs['scenario']]} · {STRATEGIES[results['company']['strategy']]} · "
               f"种子 {inputs['seed']} · 实时 00:00–{_end_label(n-1)}（{n}/96 段） · {name}")
    differences = _draft_changes(inputs, draft)
    if differences and st.session_state.get('dadu_draft_modified'):
        st.caption('待运行设置与当前回放不同：'+'；'.join(differences)+'。可在运行设置中打开匹配的已存结果。')
    stations = {s['id']:s for s in inputs['stations']}
    names = list(stations)
    station_key, quarter_key = 'dadu_unit_'+name, 'dadu_q_'+name
    focus = st.session_state
    preferred = focus.get('dadu_focus_quarter',0)
    if focus.get('dadu_focus_run') != name:
        previous_station = focus.get('dadu_focus_station','D10')
        focus[station_key] = previous_station if previous_station in names else names[0]
        focus[quarter_key] = min(preferred, n-1)
        focus['dadu_focus_run'] = name
    left,right = st.columns(2)
    uid = left.selectbox('查看厂站', names, format_func=lambda s:stations[s]['name'], key=station_key,
                         help='可输入站名搜索。切换归档时保留所选厂站和时段。',
                         on_change=_remember_focus, args=('station', station_key))
    def period_label(t):
        return f"{'00:00' if t == 0 else _end_label(t-1)}–{_end_label(t)} · 第 {t+1} 段"

    quarter = right.selectbox('查看已执行时段（北京时间）', range(n),
                              format_func=period_label,
                              key=quarter_key, on_change=_remember_focus, args=('quarter',quarter_key))
    if preferred >= n:
        right.caption(f'原选时段超出本次已执行范围，显示最后一段（{_end_label(n-1)}）。')
    day_quarter = quarter
    day_count = min(len(results['da']), len(results['company']['internal_plan_mw']))
    if n < day_count and st.toggle('单独预览公司与日前时段', key='dadu_day_preview_'+name,
                                   help='实时出清、水账和收入继续跟随已执行时段。'):
        preview_key = 'dadu_day_q_'+name
        if preview_key not in focus:
            focus[preview_key] = quarter
        day_quarter = st.selectbox('公司与日前预览时段（北京时间）', range(day_count),
                                   format_func=period_label, key=preview_key)
    company, market, dispatch, water, cash = st.tabs(
        ('已冻结申报', '日前预出清', '实时出清与执行', '全流域水情', '收入与对照'), key='dadu_role_tabs')
    station = stations[uid]
    with company:
        render_company_portfolio(results, day_quarter)
        if results['company']['strategy'] != 'default':
            with st.expander('历史报价依据'):
                render_bid_decision(results, station, day_quarter)
                _company_tab(results, station)
    with market:
        render_day_ahead(results, station, day_quarter)
        with st.expander('机组曲线与节点参考价'):
            policy = results.get('policy', {})
            trigger = {'dry_routine': '枯水期按季节常态触发',
                       'wet_power': f"丰水期电力供需比 {_number(policy.get('power_ratio'), 3)} ≤ 1.01",
                       'wet_energy': f"丰水期电量供需比 {_number(policy.get('energy_ratio'), 3)} ≤ 1.25",
                       'none': '市场力缓解条件未触发'}.get(policy.get('trigger'), '触发原因待核')
            st.caption(f"有效报价依据为{trigger}。本日研究限价 {_number(policy.get('cap'))} 元/MWh。"
                       "分段表按功率起止点对照冻结申报与市场采用价，相同时合并显示。")
            _market_tab(results, station, day_quarter)
            with st.expander('39 节点日前参考价'):
                prices = results['da'][day_quarter]['raw_nodal_prices']
                st.caption(f'{period_label(day_quarter)} · 日前固定组合 SCED 节点参考价')
                st.dataframe([{'节点': int(bus), '日前节点参考价（元/MWh）': value}
                              for bus, value in sorted(prices.items(), key=lambda item: int(item[0]))],
                             hide_index=True, placeholder='待核')
    with dispatch:
        render_real_time(results, station, quarter)
        render_overview(results)
        worst = max(range(n),key=lambda q:fsum(rt[q]['shed'].values()))
        if fsum(rt[worst]['shed'].values())>1e-5:
            st.button('跳到最大失负荷时段', key='dadu_worst_'+name,
                         on_click=_set_quarter, args=(name,worst))
        render_grid_detail(results, quarter)
        with st.expander('分站实发、偏差、节点价与明细'):
            _realtime_tab(results, station, quarter)
            render_node_prices(results, station, quarter)
    with water:
        render_basin(results, quarter)
        st.subheader('逐站与河段水账')
        if station['model']=='reservoir':
            with st.expander(f"{station['name']}日前与实时库容曲线"):
                _chart({'日前计划库容':[r['hydro'][uid]['storage_m3']/1e6 for r in results['da']],
                        '实际库存':[r['hydro'][uid]['storage_m3']/1e6 for r in rt]}, '百万 m³')
        render_water_detail(results, station, quarter)
    with cash:
        st.subheader('实时电能量收入')
        st.caption('日前预出清只用于参照。收入按计量和采用的实时节点价核算；缺少有效价格的时段单列待核电量。')
        render_cash_detail(results)
        if results['company']['strategy'] != 'default':
            render_company_detail(results, station, quarter)
            render_pair(root, receipts, results, station, quarter)


def _mark_draft_modified():
    st.session_state['dadu_draft_modified'] = True


def _draft_changes(inputs, draft):
    labels = {'scenario':'场景','strategy':'报价','periods':'执行段数','seed':'种子',
              'water_value_scale':'水价值倍率','corridor_scale':'通道倍率','gas_plan_scale':'气电计划倍率'}
    result = []
    for key, value in draft.items():
        if key not in inputs or inputs[key]==value:
            continue
        translate = SCENARIOS if key=='scenario' else STRATEGIES if key=='strategy' else {}
        result.append(f"{labels[key]} {translate.get(inputs[key],inputs[key])} → {translate.get(value,value)}")
    return result


@st.cache_data(show_spinner=False)
def _load_sheet(scenario):
    from sichuan_v5_dadu_case import build_case
    from sichuan_v5_dadu_run import default_declaration
    case, meta = build_case(scenario, 39)
    company = default_declaration(case, meta)
    return dict(run='draft_'+scenario, company=company, inputs=dict(stations=meta['stations'], assets=meta['assets'],
                available=case.available, fixed_schedule=case.fixed_schedule,
                market_case={'cascade':company['hydraulic_declaration']}))


def render_dadu(root):
    from sichuan_v5_dadu_run import declaration_record, declaration_path, freeze_declaration, list_dadu, read_dadu, run_dadu
    from sichuan_v5_dadu_live import solve_visible
    from sichuan_v5_dadu_portfolio import render_company_portfolio
    from sichuan_v5_dadu_style import apply_dadu_style
    apply_dadu_style()
    st.title('大渡河梯级机组申报')
    page = st.radio('工作区', ['机组申报','市场结果'], horizontal=True, key='dadu_workspace')
    if page == '市场结果':
        names = list_dadu(root)
        receipts = {n:_receipt(root,n) for n in names}
        if not names:
            st.info('尚无出清记录。请先在机组申报页确认并运行。')
            return
        name = st.selectbox('查看已保存运行', names, key='dadu_saved',
                            format_func=lambda n:_archive_label(n,receipts[n]))
        _results(name,read_dadu(root,name),root,receipts,{})
        return
    st.caption('19 站按水电耦合方式申报。使用场景预置的量价、来水和参考计划，确认时一并冻结水库与河段参数。')
    left, right = st.columns(2)
    scenario = left.selectbox('申报场景', list(SCENARIOS), format_func=SCENARIOS.get, key='dadu_scenario')
    quarter = right.selectbox('核对时段（北京时间）', range(96),
                              format_func=lambda q:f'{q//4:02}:{15*(q%4):02}–{_end_label(q)}')
    try:
        sheet = _load_sheet(scenario)
    except (ValueError,OSError) as exc:
        st.error(f'申报单载入失败：{exc}')
        return
    company = sheet['company']
    coupling = company['hydraulic_declaration']
    names = {s['id']:s['name'] for s in sheet['inputs']['stations']}
    render_company_portfolio(sheet, quarter)
    st.subheader('耦合申报参数')
    reservoirs, reaches, capability = st.tabs(['水库与径流边界','河段与在途水量','机组持续出力'])
    with reservoirs:
        records = []
        for row in coupling['reservoirs']:
            records.append({'电站':names[row['id']], '调节方式':'库容式' if row['regulation']=='reservoir' else '径流式',
                '期初库容 m³':row['initial_storage_m3'], '最小库容 m³':row['storage_min_m3'],
                '最大库容 m³':row['storage_max_m3'], '期末下限 m³':row['terminal_min_m3'],
                '期末上限 m³':row['terminal_max_m3'], '最小总下泄 m³/s':row['min_release_m3s'],
                '最大总下泄 m³/s':row['max_release_m3s'], '最大弃水 m³/s':row['max_spill_m3s'],
                '最小旁泄 m³/s':row['min_bypass_m3s'], '耗水率 m³/kWh':company['offers'][row['id']]['consumption_m3_kwh']})
        st.dataframe(records, hide_index=True, width='stretch')
    with reaches:
        st.dataframe([{'上游':names[r['upstream']], '下游':names[r['downstream']],
                       '传播分钟':15*r['travel_quarters'],'期初在途合计 m³':sum(r['initial_arrivals_m3']),
                       '逐段预计到水 m³':list(r['initial_arrivals_m3'])} for r in coupling['reaches']],
                     hide_index=True, width='stretch')
    with capability:
        st.dataframe([{'电站':names[u], '日最大可发 MWh':o['daily_energy_mwh'],
                       '连续三小时出力 MW':o['sustained_3h_mw'],
                       '起始时刻':f"{o['sustained_start_quarter']//4:02}:{15*(o['sustained_start_quarter']%4):02}"}
                      for u,o in company['offers'].items()], hide_index=True, width='stretch')
        st.caption('逐站能力分别核定。能否同时实现取决于梯级水量和电网约束。')
    with st.expander('全网机组台账与当段可用容量'):
        owners = {s['id']:s.get('group') for s in sheet['inputs']['stations']}
        roles = {'hydro':'水电耦合申报','thermal':'煤电量价申报','renewable':'集中式光伏量价申报','fixed':'气电保供计划'}
        base_inputs = sheet['inputs']
        st.dataframe([{'机组':a['name'],'登记集团':owners.get(a['id']), '市场身份':roles[a['kind']],
                       'IEEE39 节点':a['bus'],'设计装机 MW':a['capacity_mw'],
                       '当段可用 MW':base_inputs['available'][quarter].get(a['id'],a['capacity_mw']),
                       '当段保供计划 MW':base_inputs['fixed_schedule'][quarter].get(a['id'])}
                      for a in base_inputs['assets']], hide_index=True,width='stretch')
        st.caption('19 站统一申报为研究设定，登记集团按厂站列示。其余电源沿用各自的模拟市场身份。')
    receipt = declaration_record(company)
    st.download_button('下载本次完整申报单', json.dumps(receipt,ensure_ascii=False), scenario+'-declaration.json','application/json')
    if st.button('确认并冻结 19 站申报', type='primary', key='dadu_freeze'):
        try:
            freeze_declaration(root,company)
        except (OSError,ValueError) as exc:
            st.error(f'申报保存失败：{exc}')
            return
    submitted = declaration_path(root,company).is_file()
    if submitted:
        st.success(f'默认申报已冻结 · 19 站 · 单号 {company["declaration_sha256"][:12]}。可离开页面，稍后执行市场出清。')
    else:
        st.caption('待确认 · 确认只保存本场景的机组申报单。')
    periods = st.selectbox('提交后实时执行范围', (4,96), format_func=lambda n:'首小时（4 段）' if n==4 else '全天（96 段）', key='dadu_periods')
    st.caption('运行市场时先执行日前 96 点 SCUC → SCED，再按选定范围滚动执行实时市场。')
    if st.button('使用已冻结申报运行市场', disabled=not submitted, key='dadu_run'):
        try:
            name,_ = solve_visible(run_dadu,root,dict(scenario=scenario, strategy='default', periods=periods,
                                   declaration_sha256=company['declaration_sha256']),st.container(),'市场计算')
        except (ValueError,RuntimeError,OSError):
            return
        st.session_state['dadu_saved'] = name
        st.success('已保存本次出清。切换至「市场结果」即可核对已冻结申报与中标量。')
