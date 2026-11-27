"""Reconcile the bidder's frozen expectations with its executed energy and water account."""
from collections import defaultdict
from decimal import Decimal
from math import fsum

import streamlit as st

from sichuan_v5_settlement import SettlementInput, settle_quarter


def _complete_sum(values):
    values = list(values)
    return fsum(values) if values and all(v is not None for v in values) else None


def _fmt(value):
    return '—' if value is None else f'{value:,.2f}'


def _end(q):
    minutes = (q + 1) * 15
    return f'{minutes//60:02d}:{minutes%60:02d}'


def _block_at(blocks, power):
    """Read the submitted/effective block containing this output, with closed upper bound."""
    if power <= 1e-8:
        return None
    upper = 0.
    for width, price in blocks:
        upper += width
        if power <= upper + 1e-6:
            return price
    return None


def station_reconciliation(results, station):
    uid, bus = station['id'], station['bus']
    company, money = results['company'], results['money']
    pricing = {q['quarter']:q for q in money['quarters']}
    da = {r['quarter']:r for r in results['da']}
    frozen = company['offers'][uid]['segments']
    effective = results['effective_offers'].get(uid, ())
    rows = []
    for frame in results['rt']:
        q = frame['quarter']
        forecast = company['expected_price_yuan_mwh'][q][uid]
        internal = company['internal_plan_mw'][q][uid]
        preclear = da[q]['power'][uid]
        actual = frame['power'][uid]
        meter = frame.get('metered_mw',frame['power']).get(uid)
        record = pricing.get(q,{})
        prices = record.get('effective_price_by_bus',{})
        price = prices.get(bus, prices.get(str(bus)))
        cash, price_effect = None, None
        energy = float(Decimal(str(meter))*Decimal('.25')) if meter is not None and meter>=0 else None
        if record.get('reliable') is True and energy is not None:
            cash_ledger = settle_quarter(SettlementInput(q,uid,'generator',Decimal(str(energy)),Decimal(0),
                                        None if price is None else Decimal(str(price))))
            if cash_ledger.net_amount is not None:
                cash = float(cash_ledger.net_amount)
                price_effect = cash - energy*forecast
        rows.append({'时段末': _end(q), '事前预期价':forecast,
                     '水机会成本':company['marginal_water_cost_yuan_mwh'][q][uid],
                     '内部计划 MW': internal, '日前 MW':preclear, '实发 MW':actual, '计量 MWh':energy,
                     '日前出力对应申报价':_block_at(frozen,preclear),
                     '日前出力对应有效报价':_block_at(effective,preclear),
                     '有效实时节点价':price if record.get('reliable') is True else None,
                     '预期现金 元':internal*.25*forecast,
                     '日前数量影响 元':(preclear-internal)*.25*forecast,
                     '实时数量影响 元':None if energy is None else (energy-preclear*.25)*forecast,
                     '实时价格影响 元':price_effect, '已执行现金 元':cash})
    station_hours = [h for h in money['hourly'] if h['resource_id']==uid]
    ledger_cash = _complete_sum(h['executed_revenue'] for h in station_hours)
    recomputed = _complete_sum(r['已执行现金 元'] for r in rows)
    residual = None if ledger_cash is None or recomputed is None else recomputed-ledger_cash
    return rows, residual


def _water_accounts(results):
    company = results['company']
    case = results['inputs'].get('market_case',{}).get('cascade')
    handoff = results['rt'][-1].get('water_handoff') if results['rt'] else None
    if not case or not handoff:
        return {}
    from_start = {(e['upstream'],e['downstream']):fsum(e['initial_arrivals_m3']) for e in case['reaches']}
    at_end = {(e['upstream'],e['downstream']):fsum(e['next_arrivals_m3']) for e in handoff['in_transit']}
    if from_start.keys() != at_end.keys():
        return {}
    incoming = defaultdict(float)
    for reach, volume in at_end.items():
        incoming[reach[1]] += volume-from_start[reach]
    values = company['water_value_yuan_m3']
    result = {}
    for r in case['reservoirs']:
        uid = r['id']
        if uid not in handoff['storage_m3'] or uid not in values:
            return {}
        delta = handoff['storage_m3'][uid]-r['initial_storage_m3']
        result[uid] = (delta*values[uid], incoming[uid]*values[uid])
    return result


def portfolio_reconciliation(results):
    money = results['money']
    complete_day = [r['quarter'] for r in results['rt']] == list(range(96))
    reliable = (money.get('data_quality')=='verified' and results['summary']['max_slack_mw']<=1e-5
                and len(money['quarters'])==len(results['rt']) and all(q['reliable'] for q in money['quarters']))
    water = _water_accounts(results) if complete_day and reliable else {}
    hourly = defaultdict(list)
    for row in money['hourly']:
        hourly[row['resource_id']].append(row)
    names = {s['id']:s['name'] for s in results['inputs']['stations']}
    rows = []
    for uid in money['portfolio_members']:
        energy = _complete_sum(h['energy_mwh'] for h in hourly[uid])
        cash = _complete_sum(h['executed_revenue'] for h in hourly[uid])
        storage, travel = water.get(uid,(None,None))
        rows.append({'厂站':names[uid], '已执行计量 MWh':energy,
                     '实发加权价 元/MWh':cash/energy if energy and cash is not None else None,
                     '已执行现货毛收入 元':cash, '库水估值变化 元':storage, '在途水估值变化 元':travel,
                     '含水价值研究收益 元':_complete_sum((cash,storage,travel))})
    cash = _complete_sum(row['已执行现货毛收入 元'] for row in rows)
    water_change = _complete_sum(v for row in rows for v in (row['库水估值变化 元'],row['在途水估值变化 元']))
    return rows, {'complete_day':complete_day, 'reliable':reliable,
                  'energy_mwh':_complete_sum(row['已执行计量 MWh'] for row in rows), 'cash':cash,
                  'water_change':water_change, 'combined':_complete_sum((cash,water_change))}


def render_company_detail(results, station):
    company = results['company']
    uid = station['id']
    rows, residual = station_reconciliation(results,station)
    assumptions = results['inputs'].get('assumptions',{})
    stamp = assumptions.get('issued_at',{}).get('company_price_forecast','档案未记录')
    with st.expander(station['name']+'的事前判断与成交对账'):
        st.caption(f"预期价格签发于 {stamp}，报价冻结于 {company['as_of']}。"
                   "合成预期价由季节参考价、早晚峰加价、白天光照折减及节点扰动构成。"
                   f"本站期末水价值 {company['water_value_yuan_m3'][uid]:.6f} 元/m³，"
                   f"本次水价值倍率 {results['inputs'].get('water_value_scale',1.):g}。"
                   + assumptions.get('water_value_basis',''))
        if company['strategy']=='water_value':
            st.caption('水价值策略将逐段联合调度的边际水机会成本按分位数整理为日曲线，再按申报段规则调整。')
        else:
            st.caption('朴素策略采用固定分段报价。公司内部水电计划仍受来水、库存、时滞和期末边界约束。')
        columns = ('预期现金 元','日前数量影响 元','实时数量影响 元','实时价格影响 元','已执行现金 元')
        st.dataframe([{k:_complete_sum(row[k] for row in rows) for k in columns}], hide_index=True)
        st.caption('同一执行范围内，内部计划按预期价估值，先加日前与内部计划的数量差，再加实时计量与日前的'
                   '数量差，最后加实发电量按实时价与预期价的价差。后面三项用于解释现金变化。日前预出清不结算。')
        if len(rows)<96:
            st.info(f'部分日，仅对账已执行的 {len(rows)}/96 个时段。')
        if residual is None:
            st.warning('部分计量或结算价格无效，现金对账留空。可在下表定位缺项。')
        elif abs(residual)>0.01:
            st.warning(f'逐时段复算与小时现金账相差 {_fmt(residual)} 元。')
        else:
            st.caption('逐时段复算与小时现金账差额 '+_fmt(residual)+' 元。')
        st.dataframe(rows, hide_index=True)
        st.caption('表中报价和价格单位为元/MWh。按日前出力定位报价段；零出力时不显示对应段价。'
                   '现金使用实发对应的合成计量和有效实时节点价。')


def render_cash_detail(results):
    rows, totals = portfolio_reconciliation(results)
    n = len(results['rt'])
    with st.expander('各站现货现金与期末存水价值'):
        if not totals['complete_day']:
            st.info(f'当前仅执行 {n}/96 段。现金列限于已执行量，日末存水估值和全天研究收益留空。')
        elif not totals['reliable']:
            st.warning('本日存在可靠性松弛或价格缺项。受影响的现金留空，暂停全天含水估值比较。')
        st.dataframe(rows, hide_index=True)
        st.caption('组合只纳入模拟报价水电站。加权价用本站已执行现金除以上网电量。库水按本站事前水价值'
                   '估值，在途水按到达站的事前水价值估值，均扣除期初价值。库水及在途估值属于研究收益，'
                   '分别列在现货毛收入之外。机组成本、中长期合约差价、辅助服务及补偿费用另有口径。')
        summary = results['summary']
        reference = (('现货毛收入',totals['cash'],summary.get('company_revenue')),
                     ('存水及在途估值变化',totals['water_change'],summary.get('water_stock_value_change')),
                     ('含水价值研究收益',totals['combined'],summary.get('water_adjusted_value')))
        checks = [{'项目':label,'各站合计 元':observed,'组合账 元':expected if observed is not None else None,
                   '差额 元':observed-expected if observed is not None and expected is not None else None}
                  for label,observed,expected in reference]
        st.dataframe(checks,hide_index=True)
        if any(r['差额 元'] is not None and abs(r['差额 元'])>.01 for r in checks):
            st.warning('各站复核合计与组合账相差超过 0.01 元，请复核归档。')
