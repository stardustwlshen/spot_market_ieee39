"""Explain frozen station bids from D-1 evidence, with a separate operator audit interface."""
from math import isfinite

import streamlit as st

from sichuan_v5_company import _price_blocks
from sichuan_v5_energy import PERIODS


def _clock(slot):
    minutes = slot * 15
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _fmt(value, digits=2):
    return '未归档' if value is None else f'{value:,.{digits}f}'


def _at(series, quarter, uid=None):
    if not isinstance(series, (list, tuple)) or not 0 <= quarter < len(series):
        return None
    value = series[quarter]
    if uid is not None:
        value = value.get(uid) if isinstance(value, dict) else None
    return value if type(value) in (float, int) and isfinite(value) else None


def _company_evidence(company, station, quarter):
    uid = station['id']
    strategy = company['strategy']
    offer = company['offers'][uid]
    plan = _at(company.get('internal_plan_mw'), quarter, uid)
    costs = company.get('marginal_water_cost_yuan_mwh') or []
    technical = offer.get('technical_available_mw') or []
    daily_costs = [_at(costs, q, uid) for q in range(PERIODS)]
    capacity = [_at(technical, q) for q in range(PERIODS)]
    samples = [cost for cost in daily_costs if cost is not None]
    outage = len(technical) == PERIODS and all(mw == 0 for mw in capacity)
    # A missing positive-capacity sample changes the daily quantiles. A recorded
    # zero-capacity quarter can legitimately have no marginal cost.
    complete = len(costs) == PERIODS and all(
        (cost is not None and mw != 0) or (cost is None and mw == 0)
        for cost, mw in zip(daily_costs, capacity))
    targets = (company.get('raw_daily_block_targets_yuan_mwh') or {}).get(uid)
    comparison = 'naive' if strategy == 'water_value' else 'water_value'
    alternative = []
    if comparison == 'naive' or (outage and not samples) or (complete and samples):
        alternative, _ = _price_blocks(station['capacity_mw'], station['model'],
                                       samples or [0.], comparison)
    bands = offer['segments']
    comparison_matches = (len(alternative) == len(bands)
                          and all(abs(a[0] - b[0]) <= 1e-6 for a, b in zip(alternative, bands)))
    blocks = []
    selected = None
    start = 0.
    for i, (width, submitted) in enumerate(bands):
        end = round(start + width, 2)
        if selected is None and plan is not None and 1e-8 < plan <= end + 1e-6:
            selected = i
        blocks.append(dict(start=start, end=end, target=_at(targets, i), submitted=submitted,
                           comparison=alternative[i][1] if comparison_matches else None))
        start = end
    return dict(blocks=blocks, selected=selected, sample_count=len(samples), plan=plan,
                forecast=_at(company.get('expected_price_yuan_mwh'), quarter, uid),
                marginal=daily_costs[quarter], complete_samples=complete, outage=outage,
                comparison=comparison)


def bid_evidence(results, station, quarter):
    """Join the operator's adopted curve to frozen bands for the market-stage audit."""
    company = results['company']
    evidence = _company_evidence(company, station, quarter)
    uid = station['id']
    bands = company['offers'][uid]['segments']
    effective = (results.get('effective_offers') or {}).get(uid) or []
    matched = (len(effective) == len(bands)
               and all(abs(a[0] - b[0]) <= 1e-6 for a, b in zip(effective, bands)))
    for index, block in enumerate(evidence['blocks']):
        block['effective'] = effective[index][1] if matched else None
    return evidence


def _frozen_curve(evidence, technical_mw):
    """Draw each signed price from its actual band start through its band end."""
    blocks = evidence['blocks']
    if not blocks or any(b['submitted'] is None for b in blocks):
        st.caption('冻结分段报价未完整归档。')
        return
    points = [{'mw': b['start'], 'price': b['submitted']} for b in blocks]
    points.append({'mw': blocks[-1]['end'], 'price': blocks[-1]['submitted']})
    layers = [{'data': {'values': points},
               'mark': {'type': 'line', 'interpolate': 'step-after', 'color': '#1769aa',
                        'strokeWidth': 2.7}, 'encoding': {'order': {'field': 'mw', 'type': 'quantitative'}}}]
    selected = evidence['selected']
    show_plan = selected is not None and technical_mw != 0 and not evidence['outage']
    if show_plan:
        point = {'mw': evidence['plan'], 'price': blocks[selected]['submitted']}
        layers.append({'data': {'values': [point]},
                       'mark': {'type': 'point', 'filled': True, 'size': 90, 'color': '#ad530f'},
                       'encoding': {'tooltip': [{'field': 'mw', 'title': '内部计划 MW'},
                                               {'field': 'price', 'title': '对应冻结段价 元/MWh'}]}})
    spec = {'height': 175, 'layer': layers, 'encoding': {
        'x': {'field': 'mw', 'type': 'quantitative', 'title': '申报出力（MW）',
              'scale': {'domain': [blocks[0]['start'], blocks[-1]['end']], 'nice': False}},
        'y': {'field': 'price', 'type': 'quantitative', 'title': '冻结申报价（元/MWh）',
              'scale': {'zero': True}}}}
    st.vega_lite_chart(spec, width='stretch')
    if evidence['outage']:
        st.caption('冻结曲线为申报模板。96 个时段技术可用上限均为零。')
    elif show_plan:
        st.caption('圆点标出所选时段的内部计划及其对应的冻结段价。')


def render_bid_decision(results, station, quarter):
    uid = station['id']
    inputs, company = results.get('inputs', {}), results['company']
    offer = company['offers'][uid]
    evidence = _company_evidence(company, station, quarter)
    strategy = company['strategy']
    assumptions = inputs.get('assumptions', {})
    issued = assumptions.get('issued_at', {})
    stamp = f'{_clock(quarter)}–{_clock(quarter + 1)}'
    st.markdown(f"**{station['name']} · {stamp} 为何这样报**")
    if strategy == 'naive':
        st.write('公司先用预测来水和预期价格安排全日发电，再按固定阶梯冻结各段报价。'
                 '边际水机会成本仅用于解释内部用水计划。')
    else:
        st.write('公司先用预测来水和预期价格安排全日发电，再将各时段的边际水机会成本整理为一条全日分段曲线，按站冻结报价。')
    st.write(f"该时段公司预计节点价 {_fmt(evidence['forecast'])} 元/MWh，"
             f"联合调度得到的边际水机会成本为 {_fmt(evidence['marginal'])} 元/MWh。"
             f"按预测区间来水 {_fmt(_at(offer.get('natural_inflow_m3s'), quarter))} m³/s，"
             f"内部计划发电 {_fmt(evidence['plan'])} MW，单站可行范围为 "
             f"{_fmt(_at(offer.get('minimum_mw'), quarter))}–{_fmt(_at(offer.get('available_mw'), quarter))} MW，"
             f"机组技术可用上限为 {_fmt(_at(offer.get('technical_available_mw'), quarter))} MW。")
    selected = evidence['selected']
    if evidence['plan'] is None:
        st.caption('该时段内部计划未归档，无法定位对应的报价段。')
    elif selected is None:
        st.caption('本时段内部计划未落入正出力报价段。分段曲线在运行日内固定。')
    else:
        block = evidence['blocks'][selected]
        basis = '按朴素策略的固定阶梯' if strategy == 'naive' else '按水价值拟合'
        st.write(f"内部计划落在第 {selected + 1} 报价段（{_fmt(block['start'])}–{_fmt(block['end'])} MW）。"
                 f"{basis}，该段原始目标 {_fmt(block['target'])} 元/MWh，公司冻结申报 {_fmt(block['submitted'])} 元/MWh。")
    _frozen_curve(evidence, _at(offer.get('technical_available_mw'), quarter))

    with st.expander('逐段核对报价依据与冻结申报'):
        count = len(evidence['blocks'])
        title = '朴素模板' if evidence['comparison'] == 'naive' else '水价值拟合'
        if evidence['outage'] and not evidence['sample_count']:
            st.write('96 个时段的技术可用上限全部为零。水价值策略使用零目标生成停机模板，全日可成交量为零。')
        elif not evidence['complete_samples']:
            st.write(f"本站仅能核实 {evidence['sample_count']} 个机会成本样本。完整有效样本或相应技术边界未归档，"
                     "无法从档案复算完整的水价值报价。")
        if strategy == 'water_value':
            if evidence['complete_samples'] and evidence['sample_count']:
                st.write(f"水价值策略按大小排列本站 {evidence['sample_count']} 个有效时段的边际水机会成本，"
                         f"将排序区间等分为 {count} 份，在每份中点位置取一个样本作为该报价段的原始目标。")
        else:
            st.write('朴素策略按段使用 0、20、40… 元/MWh 的固定原始目标，保留与水价值策略相同的事前水量优化和容量边界。')
        st.write('公司在 0–800 元/MWh 的整数价格上，选择与原始目标总平方误差最小的一组段价，'
                 '同时使相邻段递增 20–150 元/MWh。各段功率范围按设计装机等分。')
        rows = [{'报价段': i + 1, '起点 MW': b['start'], '终点 MW': b['end'],
                 '原始目标': b['target'], '公司冻结申报': b['submitted'],
                 title + '对照': b['comparison']} for i, b in enumerate(evidence['blocks'])]
        st.dataframe(rows, hide_index=True, placeholder='—',
                     column_config={k: st.column_config.NumberColumn(format='%.2f')
                                    for k in ('原始目标', '公司冻结申报', title + '对照')})
        st.caption(f"段价单位为元/MWh。最右列按同一组事前资料回放{title}报价，供策略对照。")

    with st.expander('公司用哪些信息决定发电量'):
        group = station.get('group') or '待核'
        owner = station.get('company') or '待核'
        match = station.get('match') or '待核'
        st.write(f"台账主体为{owner}，所属集团为{group}，名称匹配为“{match}”。"
                 "本算例将 19 站放入同一模拟组合联合优化、逐站申报，未按股权比例归集。")
        st.write(f"事前信息签发于 {issued.get('company_information', '未归档')}，"
                 f"价格预期签发于 {issued.get('company_price_forecast', '未归档')}，"
                 f"公司冻结申报于 {company['as_of']}。合成预期价由季节参考价、早晚峰、午间光伏和节点扰动构成。")
        st.write('公司在预测的 96 个时段内最大化预计售电收入加期末水资产增值。'
                 '预计售电收入累加各站的内部计划 MW × 0.25 h × 事前预期价。'
                 '库内存水按本站估值，尚在河道的水按接收站估值，两者均扣除期初价值。')
        value = company.get('water_value_yuan_m3', {}).get(uid)
        st.write(f"本站期末存水估值为 {_fmt(value, 6)} 元/m³。模型由当前及各下游站的耗水率和季节参考价"
                 f"估算位置水价值，采用合成兑现系数 0.78，场景水价值倍率为 {_fmt(inputs.get('water_value_scale'))}。"
                 "逐时边际水机会成本由事前预期价减去优化器给出的出力约化成本除以 0.25 小时得到。")
        st.caption('水量平衡逐段核对本地来水、延时上游到水、过机流量、弃水和生态旁路。'
                   '还约束库存、总下泄、发电能力及爬坡。径流式站固定库存，来水可在本站发电或向下游传递。'
                   '用同一组物理约束另求每站逐段上下限、最大发电量和最佳连续 12 段的可持续出力，各极值可对应不同运行计划。')
