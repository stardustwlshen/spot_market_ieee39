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
STRATEGIES = {'water_value': '水价值报价', 'naive': '朴素报价'}
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
    complete = len(result['rt']) == 96 and summary['max_slack_mw']<=1e-5
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
    left, right = st.columns(2)
    with left:
        st.markdown('**冻结分段申报**')
        st.dataframe(_submitted(offer['segments']), hide_index=True)
    with right:
        st.markdown('**报价依据**')
        _chart({'事前预期节点价': [r[uid] for r in company['expected_price_yuan_mwh']],
                '水的边际机会成本': [r[uid] for r in company['marginal_water_cost_yuan_mwh']]},
               '元/MWh')
    st.caption('申报按站提交。内部计划用于生成报价，调度按冻结申报和独立运行边界出清。')
    with st.expander('核对申报版本和站名'):
        st.write({'签发时刻':company.get('as_of'), '申报摘要':results['summary']['bid_hash'],
                  '工程名':station.get('engineering_name'), '名称匹配':station.get('match'),
                  '工程调节性能':station.get('regulation'),
                  '本场景建模':'库容式' if station.get('model')=='reservoir' else '径流式近似',
                  '可用性依据':station.get('availability_status'), '市场资格':station.get('qualification_status'),
                  '期末水价值（元/m³）':company.get('water_value_yuan_m3',{}).get(uid)})


def _market_tab(results, station, quarter):
    inputs, da = results['inputs'], results['da']
    groups = _asset_groups(inputs['assets'])
    st.markdown('**日前各类型出力**')
    _chart({kind: [_total(row, ids) for row in da] for kind, ids in groups.items()}, 'MW', area=True)
    st.caption(f"所选时段系统负荷：日前 {_number(sum(da[quarter]['demand'].values()))} MW，"
               f"实时 {_number(sum(results['rt'][quarter]['demand'].values()))} MW。")
    name_by_id = {a['id']:a for a in inputs['assets']}
    snapshot = []
    for kind, ids in groups.items():
        snapshot.append({'类型':kind,
                         '设计装机 MW':sum(name_by_id[a]['capacity_mw'] for a in ids),
                         '日前可用 MW':sum(inputs['available'][quarter].get(a, name_by_id[a]['capacity_mw']) for a in ids),
                         '日前预出清 MW':_total(da[quarter], ids),
                         '实时实发 MW':_total(results['rt'][quarter], ids)})
    st.dataframe(snapshot, hide_index=True)
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
            st.dataframe(diagnostics, hide_index=True)
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
        st.dataframe(values, hide_index=True)
        st.caption('零电量时加权均价留空。收入受价格缺失或可靠性松弛影响时保留缺项。')
    with st.expander('线路潮流与运行边界'):
        flows = rt[quarter]['flows']
        limits = {line['id']:line['limit_mw'] for line in inputs['lines']}
        loading = [{'线路':key, '所选时段潮流 MW':round(value, 3), '限额 MW':round(limits[key], 3),
                    '使用率 %':round(100*abs(value)/limits[key], 2)} for key,value in flows.items()]
        st.dataframe(sorted(loading, key=lambda r:r['使用率 %'], reverse=True), hide_index=True)


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
    inputs, summary = results['inputs'], results['summary']
    rt = results['rt']
    n = len(rt)
    st.subheader('运行结果')
    st.caption(f"当前回放：{SCENARIOS[inputs['scenario']]} · {STRATEGIES[results['company']['strategy']]} · "
               f"种子 {inputs['seed']} · 实时 00:00–{_end_label(n-1)}（{n}/96 段） · {name}")
    differences = _draft_changes(inputs, draft)
    if differences and st.session_state.get('dadu_draft_modified'):
        st.caption('待运行设置与当前回放不同：'+'；'.join(differences)+'。可在运行设置中打开匹配的已存结果。')
    if summary.get('risk_active'):
        trigger = {'dry_routine':'枯水期常态缓解','wet_power':'丰水期电力供需比触发',
                   'wet_energy':'丰水期电量供需比触发'}.get(results['policy']['trigger'],'研究缓解')
        st.caption(f"本场景触发市场力缓解研究条件，出清使用经处理的有效报价，研究限价 "
                   f"{_number(results['policy']['cap'])} 元/MWh（{trigger}）。")
    render_overview(results)
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
    worst = max(range(n),key=lambda q:fsum(rt[q]['shed'].values()))
    if fsum(rt[worst]['shed'].values())>1e-5:
        right.button('跳到最大失负荷时段', key='dadu_worst_'+name,
                     on_click=_set_quarter, args=(name,worst))
    quarter = right.selectbox('查看已执行时段（北京时间）', range(n),
                              format_func=lambda t:f"{'00:00' if t==0 else _end_label(t-1)}–{_end_label(t)} · 第 {t+1} 段",
                              key=quarter_key, on_change=_remember_focus, args=('quarter',quarter_key))
    if preferred >= n:
        right.caption(f'原选时段超出本次已执行范围，显示最后一段（{_end_label(n-1)}）。')
    company, market, dispatch, water, cash = st.tabs(('公司报价', '市场中标', '实时实发', '梯级水量', '收入与对照'), key='dadu_role_tabs')
    with company:
        _company_tab(results, stations[uid])
        render_company_detail(results, stations[uid], quarter)
    with market:
        _market_tab(results, stations[uid], quarter)
        render_node_prices(results, stations[uid], quarter)
    with dispatch:
        render_grid_detail(results, quarter)
        _realtime_tab(results, stations[uid], quarter)
    with water:
        if stations[uid]['model']=='reservoir':
            _chart({'日前计划库容':[r['hydro'][uid]['storage_m3']/1e6 for r in results['da']],
                    '实际库存':[r['hydro'][uid]['storage_m3']/1e6 for r in rt]}, '百万 m³')
        render_water_detail(results, stations[uid], quarter)
    with cash:
        st.caption('日前预出清没有结算现金。这里以实时计量估算电能量收入；当前档案未给出完整合同、补偿和企业实际成本，利润留空。')
        render_cash_detail(results)
        render_pair(root, receipts, results, stations[uid], quarter)


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


def _find_draft_archive(root, receipts, draft):
    from sichuan_v5_dadu_run import read_dadu, source_hashes
    model_sources = source_hashes()
    for name, receipt in receipts.items():
        if any(receipt.get(k)!=v for k,v in draft.items()):
            continue
        try:
            archived = read_dadu(root, name)['inputs']
        except (ValueError, OSError, KeyError):
            continue
        if (archived.get('source_hashes')==model_sources and not archived.get('awards')
                and all(archived.get(k)==v for k,v in draft.items())):
            return name
    return None


def _comparison(root, seed, periods, sensitivity, receipts):
    from sichuan_v5_dadu_overview import matched_pair
    from sichuan_v5_dadu_run import read_dadu, run_dadu, source_hashes

    def candidate(result, scenario, strategy):
        # Keep only the pairing evidence and scores when multiple archives share settings.
        inputs, company, summary = result['inputs'], result['company'], result['summary']
        evidence = {'inputs': {k:inputs.get(k) for k in ('scenario','seed','source_hashes')},
                    'company': {k:company.get(k) for k in ('strategy','company_forecast_sha256')},
                    'summary': {k:summary.get(k) for k in ('boundary_hash','issued_boundary_hash','bid_hash')},
                    'rt': range(len(result['rt']))}
        return evidence, comparison_row(scenario, strategy, result)

    with st.expander('三类场景下的两种报价策略'):
        st.caption('六组采用当前种子、执行范围和灵敏度。场景内核对模型版本、日前边界、公司预测及实时预测签发后再比较。'
                   '完整单日且实时无可靠性松弛时，列示计入库存和在途水价值变化的研究收益。')
        if st.button('查看或补算六个对照算例', key='dadu_compare'):
            st.session_state.pop('dadu_pair_results', None)
            rows = []
            try:
                sources = source_hashes()
                with st.status('正在生成对照', expanded=False) as status:
                    for scenario in SCENARIOS:
                        alternatives = {}
                        for strategy in STRATEGIES:
                            status.update(label=f'{SCENARIOS[scenario]} · {STRATEGIES[strategy]}')
                            settings = dict(scenario=scenario, strategy=strategy, seed=seed, periods=periods, **sensitivity)
                            found = []
                            for name, receipt in receipts.items():
                                if not all(receipt.get(k, v)==v for k,v in settings.items()):
                                    continue
                                try:
                                    result = read_dadu(root, name)
                                    inputs = result['inputs']
                                    if (inputs.get('source_hashes')==sources and not inputs.get('awards')
                                            and all(inputs.get(k)==v for k,v in settings.items())):
                                        found.append(candidate(result, scenario, strategy))
                                except (ValueError, OSError, KeyError):
                                    continue
                            if not found:
                                _, result = run_dadu(root, **settings)
                                found.append(candidate(result, scenario, strategy))
                            alternatives[strategy] = found
                        pair = next(((w[1], n[1]) for w in alternatives['water_value'] for n in alternatives['naive']
                                     if matched_pair(w[0], n[0])), None)
                        if pair is None:
                            raise ValueError(f'{SCENARIOS[scenario]} 缺少同边界、同预测签发且分别冻结报价的可比档案。')
                        rows.extend(pair)
                    status.update(label='六个对照运行完成', state='complete')
                st.session_state['dadu_pair_results'] = (seed, periods, dict(sensitivity), rows)
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                st.error(f'对照未完成：{error}')
        saved = st.session_state.get('dadu_pair_results')
        if saved:
            pair_seed, pair_periods, settings, rows = saved
            if (pair_seed, pair_periods, settings) != (seed, periods, sensitivity):
                st.info('当前参数已改变。点击「查看或补算六个对照算例」生成对应的对照表。')
                return
            st.caption(f'已完成对照 · 种子 {pair_seed} · 每个算例执行 {pair_periods}/96 段 · '
                       f"水价值 ×{settings['water_value_scale']:g} · 通道 ×{settings['corridor_scale']:g} · "
                       f"气电计划 ×{settings['gas_plan_scale']:g}")
            st.dataframe([{k:v for k,v in row.items() if k!='边界摘要'} for row in rows], hide_index=True)


def render_dadu(root):
    from sichuan_v5_dadu_run import list_dadu, read_dadu, run_dadu
    st.title('大渡河梯级与四川 V5.0 现货市场')
    st.caption('19 座水电站按厂站独立申报，由同一模拟公司优化。台账业主单独保留。市场运行在合成 IEEE39 电网上。')
    saved_names = list_dadu(root)
    receipts = {key:_receipt(root, key) for key in saved_names}
    selected = None
    if 'dadu_initial_controls' not in st.session_state:
        initial = receipts.get(st.session_state.get('dadu_saved') or (saved_names[0] if saved_names else None), {})
        valid = {}
        for field, choices in (('scenario', SCENARIOS), ('strategy', STRATEGIES), ('periods', (4, 96))):
            if initial.get(field) in choices:
                valid[field] = initial[field]
        for field, lower, upper in (('seed', 0, 2**31-1), ('water_value_scale', .5, 2.),
                                    ('corridor_scale', .6, 1.2), ('gas_plan_scale', 0., 1.)):
            value = initial.get(field)
            if isinstance(value, (int, float)) and lower <= value <= upper:
                valid[field] = int(value) if field=='seed' else float(value)
        st.session_state['dadu_initial_controls'] = valid
    defaults = st.session_state['dadu_initial_controls']
    with st.expander('运行设置与新算例', expanded=not saved_names):
        left, mid, right = st.columns(3)
        scenario = left.selectbox('市场场景', list(SCENARIOS), index=list(SCENARIOS).index(defaults.get('scenario','wet_low')),
                                  format_func=SCENARIOS.get, key='dadu_scenario', on_change=_mark_draft_modified)
        strategy = mid.selectbox('公司报价策略', list(STRATEGIES), index=list(STRATEGIES).index(defaults.get('strategy','water_value')),
                                 format_func=STRATEGIES.get, key='dadu_strategy', on_change=_mark_draft_modified)
        periods = right.selectbox('实时执行范围', (96,4), index=(96,4).index(defaults.get('periods',96)), key='dadu_periods', on_change=_mark_draft_modified,
                                 format_func=lambda n:'全天（96 段）' if n==96 else '首小时（4 段）')
        with st.expander('装机与灵敏度设置'):
            seed = int(st.number_input('合成数据种子', min_value=0, max_value=2**31-1, value=defaults.get('seed',39), key='dadu_seed', on_change=_mark_draft_modified))
            st.caption('设计装机合计 35,276 MW。水电 22,056 MW，煤电 3,200 MW，气电 1,200 MW，光伏 8,820 MW。'
                       '光伏按普通集中式项目报量报价；气电按非市场保供计划运行。')
            first, second, third = st.columns(3)
            sensitivity = {
                'water_value_scale':first.slider('公司期末水价值倍率', .5, 2., defaults.get('water_value_scale',1.), .05, key='dadu_water_scale', on_change=_mark_draft_modified),
                'corridor_scale':second.slider('T25-37 通道限额倍率', .6, 1.2, defaults.get('corridor_scale',1.), .05, key='dadu_line_scale', on_change=_mark_draft_modified),
                'gas_plan_scale':third.slider('气电保供计划倍率', 0., 1., defaults.get('gas_plan_scale',1.), .05, key='dadu_gas_scale', on_change=_mark_draft_modified),
            }
        draft = dict(scenario=scenario,strategy=strategy,periods=periods,seed=seed,**sensitivity)
        open_result, new_run = st.columns(2)
        if open_result.button('打开匹配的已有结果', key='dadu_open_existing', disabled=not bool(receipts)):
            selected = _find_draft_archive(root, receipts, draft)
            if selected is None:
                st.info('尚无同版本、同设置的归档。可点击「生成公司报价并运行市场」计算这一组。')
        clicked = new_run.button('生成公司报价并运行市场', type='primary', key='dadu_run')
    if clicked:
        try:
            with st.status('正在优化公司报价', expanded=False) as status:
                selected, _ = run_dadu(root, scenario=scenario, strategy=strategy, seed=seed, periods=periods,
                                       progress=lambda message:status.update(label=message), **sensitivity)
                status.update(label='已保存本次运行', state='complete')
        except (ValueError, RuntimeError, OSError) as error:
            st.error(f'本次运行未完成：{error}')
    names = list_dadu(root) if selected else saved_names
    if selected and selected not in receipts:
        receipts[selected] = _receipt(root, selected)
    if selected and selected in names:
        st.session_state['dadu_saved'] = selected
    name = st.selectbox('查看大渡河运行记录（北京时间）', [None,*names], index=1 if names else 0,
                        key='dadu_saved', format_func=lambda x:_archive_label(x,receipts.get(x,{})))
    if name:
        try:
            _results(name, read_dadu(root, name), root, receipts, draft)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            st.error(f'无法完整读取运行结果：{error}')
    else:
        st.caption('公司报价使用事前信息；日前形成 96 点参考计划；实时每 15 分钟滚动并执行首段。')
    _comparison(root, seed, periods, sensitivity, receipts)
    source_id = os.environ.get('SICHUAN_UI_SOURCE_ID')
    if source_id:
        st.caption(f'结果视图版本 · {source_id}')
