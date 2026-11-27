"""Station and river-reach water accounts derived from frozen Dadu archives."""

from math import fsum, isfinite
from numbers import Real

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart


SECONDS = 900
DAY_QUARTERS = 96


def _clock(boundary):
    day, minute = divmod(boundary * 15, 1440)
    if minute == 0 and day:
        return '24:00' if day == 1 else f'D+{day - 1} 24:00'
    return (f'D+{day} ' if day else '') + f'{minute // 60:02d}:{minute % 60:02d}'


def _number(value):
    return float(value) if isinstance(value, Real) and not isinstance(value, bool) and isfinite(value) else None


def _sum(*values):
    return fsum(values) if all(v is not None for v in values) else None


def _diff(a, b):
    return a - b if a is not None and b is not None else None


def _scale(value, factor):
    return value * factor if value is not None else None


def _cascade(result):
    return ((result.get('inputs', {}).get('market_case') or {}).get('cascade') or {})


def _stations(result):
    return {s['id']: s for s in result.get('inputs', {}).get('stations', [])}


def _period(row, index):
    return int(row.get('quarter', index))


def _stock(row, uid):
    if not row:
        return None
    value = _number(row.get('water_handoff', {}).get('storage_m3', {}).get(uid))
    return value if value is not None else _number(row.get('hydro', {}).get(uid, {}).get('storage_m3'))


def station_water_ledger(result, stage='rt'):
    """One station and 900-second interval per row. Missing archive values stay None.

    Storage residual = ending stock - starting stock - local inflow - arrivals
    + total release. Water already arriving is a volume, all discharge channels
    are rates. Dedicated bypass is independent of spill and turbine flow.
    """
    if stage not in ('rt', 'da'):
        raise ValueError('水账阶段应为 rt 或 da')
    rows = result.get(stage) or []
    sites = _stations(result)
    bounds = {r['id']: r for r in _cascade(result).get('reservoirs', [])}
    da = {_period(r, k): r for k, r in enumerate(result.get('da') or [])}
    previous, previous_quarter = None, None
    accounts = []
    for index, row in enumerate(rows):
        t = _period(row, index)
        hydro = row.get('hydro') or {}
        for uid in dict.fromkeys((*sites, *hydro)):
            source = hydro.get(uid, {})
            limit = bounds.get(uid, {})
            start = (_number(limit.get('initial_storage_m3')) if t == 0 else
                     _stock(previous, uid) if previous_quarter == t - 1 else None)
            end = _stock(row, uid)
            natural = _number(source.get('natural_inflow_m3s'))
            arriving = _number(source.get('upstream_arrival_m3'))
            turbine = _number(source.get('turbine_m3s'))
            bypass = _number(source.get('eco_bypass_m3s'))
            spill = _number(source.get('water_spill_m3s'))
            nonturbine = _number(source.get('non_turbine_m3s'))
            if nonturbine is None:
                nonturbine = _sum(bypass, spill)
            release = _number(source.get('release_m3s'))
            if release is None:
                release = _sum(turbine, nonturbine)
            release_volume = _scale(release, SECONDS)
            inflow_volume = _sum(_scale(natural, SECONDS), arriving)
            delta = _diff(end, start)
            da_end = _stock(da.get(t), uid)
            accounts.append({
                'stage': stage, 'quarter': t, 'interval_end': _clock(t + 1),
                'id': uid, 'name': sites.get(uid, {}).get('name', uid),
                'regulation': source.get('regulation') or limit.get('regulation') or sites.get(uid, {}).get('model'),
                'natural_inflow_m3s': natural, 'upstream_arrival_m3': arriving,
                'upstream_arrival_m3s': _scale(arriving, 1 / SECONDS),
                'turbine_m3s': turbine, 'eco_bypass_m3s': bypass, 'water_spill_m3s': spill,
                'non_turbine_m3s': nonturbine, 'release_m3s': release,
                'min_release_m3s': _number(limit.get('min_release_m3s')),
                'inflow_m3': inflow_volume, 'release_m3': release_volume,
                'storage_begin_m3': start, 'storage_end_m3': end, 'storage_change_m3': delta,
                'da_storage_end_m3': da_end, 'storage_vs_da_m3': _diff(end, da_end) if stage == 'rt' else None,
                'balance_residual_m3': _diff(delta, _diff(inflow_volume, release_volume)),
                'release_components_residual_m3s': _diff(release, _sum(turbine, bypass, spill)),
            })
        previous, previous_quarter = row, t
    return accounts


def reservoir_target_rows(result, quarter):
    """Daily targets are assessed only on a complete, contiguous executed day."""
    rt = result.get('rt') or []
    if not 0 <= quarter < len(rt):
        return []
    sites = _stations(result)
    bounds = {r['id']: r for r in _cascade(result).get('reservoirs', [])}
    completed = [_period(r, i) for i, r in enumerate(rt)] == list(range(DAY_QUARTERS))
    rows = []
    for uid in dict.fromkeys((*sites, *bounds, *(rt[quarter].get('hydro') or {}))):
        limit = bounds.get(uid, {})
        classification = (limit.get('regulation') or sites.get(uid, {}).get('model')
                          or rt[quarter].get('hydro', {}).get(uid, {}).get('regulation'))
        if classification != 'reservoir':
            continue
        current, last = _stock(rt[quarter], uid), _stock(rt[-1], uid)
        low, high = _number(limit.get('terminal_min_m3')), _number(limit.get('terminal_max_m3'))
        lower_margin, upper_margin = _diff(last, low), _diff(high, last)
        if not completed:
            status = '尚未执行至日末'
        elif last is None or (low is None and high is None):
            status = '归档缺少目标或库存'
        else:
            status = '满足已记录目标' if all(v is None or v >= -0.1 for v in
                                    (lower_margin, upper_margin)) else '超出目标'
        rows.append({
            'id': uid, 'name': sites.get(uid, {}).get('name', uid),
            'initial_storage_m3': _number(limit.get('initial_storage_m3')), 'selected_storage_m3': current,
            'last_executed_storage_m3': last, 'last_executed_end': _clock(_period(rt[-1], len(rt) - 1) + 1),
            'terminal_min_m3': low, 'terminal_max_m3': high,
            'available_drawdown_m3': _diff(current, _number(limit.get('storage_min_m3'))),
            'available_fill_m3': _diff(_number(limit.get('storage_max_m3')), current),
            'terminal_lower_margin_m3': lower_margin if completed else None,
            'terminal_upper_margin_m3': upper_margin if completed else None, 'status': status,
        })
    return rows


def _queue(values):
    if not isinstance(values, (list, tuple)) or not values:
        return None
    queue = tuple(_number(v) for v in values)
    return None if any(v is None for v in queue) else queue


def _queues(row):
    return {(link['upstream'], link['downstream']): _queue(link.get('next_arrivals_m3'))
            for link in (row or {}).get('water_handoff', {}).get('in_transit', [])}


def reach_water_ledger(result):
    """Executed arrivals are due from the prior queue. No forecast-tail execution."""
    specs = {(r['upstream'], r['downstream']): r for r in _cascade(result).get('reaches', [])}
    sites = _stations(result)
    previous, prev_t = {}, None
    records = []
    for index, row in enumerate(result.get('rt') or []):
        t = _period(row, index)
        current = _queues(row)
        hydro = row.get('hydro') or {}
        keys = tuple(dict.fromkeys((*specs, *current)))
        count_down, count_up = {}, {}
        for u, d in keys:
            count_down[d] = count_down.get(d, 0) + 1
            count_up[u] = count_up.get(u, 0) + 1
        for link in keys:
            u, d = link
            spec = specs.get(link, {})
            before = (_queue(spec.get('initial_arrivals_m3')) if t == 0 else
                      previous.get(link) if prev_t == t - 1 else None)
            after = current.get(link)
            lag = spec.get('travel_quarters')
            if lag is None and after is not None:
                lag = len(after)
            due = before[0] if before else None
            # This archive records arrival at station level. Only uniquely
            # attributable flow can be assigned to an individual reach.
            arrived = (_number(hydro.get(d, {}).get('upstream_arrival_m3'))
                       if count_down[d] == 1 else None)
            entered = (_scale(_number(hydro.get(u, {}).get('release_m3s')), SECONDS)
                       if count_up[u] == 1 else None)
            old_total = fsum(before) if before is not None else None
            new_total = fsum(after) if after is not None else None
            records.append({
                'quarter': t, 'interval_end': _clock(t + 1), 'upstream': u, 'downstream': d,
                'reach': sites.get(u, {}).get('name', u) + ' → ' + sites.get(d, {}).get('name', d),
                'lag_quarters': lag, 'lag_h': _scale(lag, .25), 'begin_transit_m3': old_total,
                'arriving_due_m3': due, 'arrival_recorded_m3': arrived, 'release_entered_m3': entered,
                'end_transit_m3': new_total, 'arrival_residual_m3': _diff(arrived, due),
                'balance_residual_m3': _diff(_diff(new_total, old_total), _diff(entered, arrived)),
                'next_arrivals_m3': after,
            })
        previous, prev_t = current, t
    return records


STATION_LABELS = {
    'interval_end':'时段末', 'id':'站号', 'name':'厂站', 'natural_inflow_m3s':'区间来水 m³/s',
    'upstream_arrival_m3s':'上游到水 m³/s', 'turbine_m3s':'过机 m³/s', 'eco_bypass_m3s':'专用生态旁泄 m³/s',
    'water_spill_m3s':'弃水 m³/s', 'non_turbine_m3s':'非过机合计 m³/s', 'release_m3s':'总下泄 m³/s',
    'min_release_m3s':'最小总下泄 m³/s', 'storage_begin_m3':'期初库容 m³', 'storage_end_m3':'期末库容 m³',
    'storage_change_m3':'本段库容变化 m³', 'storage_vs_da_m3':'较日前库容偏差 m³',
    'balance_residual_m3':'守恒残差 m³', 'release_components_residual_m3s':'下泄分项残差 m³/s',
    'upstream_arrival_m3':'上游到水 m³', 'inflow_m3':'总入流量 m³', 'release_m3':'总下泄量 m³',
}

REACH_LABELS = {
    'interval_end':'时段末', 'reach':'河段', 'lag_quarters':'时滞/刻', 'lag_h':'时滞 h',
    'begin_transit_m3':'期初在途 m³', 'release_entered_m3':'本段入河 m³', 'arriving_due_m3':'队列应到 m³',
    'arrival_recorded_m3':'下游实到 m³', 'end_transit_m3':'期末在途 m³',
    'arrival_residual_m3':'实到减应到 m³', 'balance_residual_m3':'守恒残差 m³',
}


def _frame(rows, labels):
    return pd.DataFrame(rows).reindex(columns=labels).rename(columns=labels)


def _download(frame, label, filename, key):
    st.download_button(label, frame.to_csv(index=False).encode('utf-8-sig'),
                       file_name=filename, mime='text/csv', key=key)


def _reservoir_view(result, quarter):
    rows = reservoir_target_rows(result, quarter)
    if not rows:
        st.caption('本档案没有可辨识的库容式电站记录。')
        return
    targets = {'name':'厂站', 'initial_storage_m3':'日初库容', 'selected_storage_m3':'所选期末库容',
               'available_drawdown_m3':'所选时刻可降库容', 'available_fill_m3':'所选时刻可蓄空间',
               'last_executed_storage_m3':'已执行末库容', 'terminal_min_m3':'日末目标下限',
               'terminal_max_m3':'日末目标上限', 'terminal_lower_margin_m3':'日末高于下限',
               'terminal_upper_margin_m3':'日末距上限', 'status':'日末校核'}
    frame = _frame(rows, targets)
    for key in list(targets.values())[1:-1]:
        frame[key] = pd.to_numeric(frame[key], errors='coerce') / 1e6
    st.caption(f'库容单位为百万 m³。已执行末库容对应 {rows[0]["last_executed_end"]}。'
               '可降、可蓄空间相对当前季节运行边界；日末目标余度仅在完整日执行后计算。')
    st.dataframe(frame, hide_index=True)


def render_water_detail(result, station, quarter):
    """Render the executed water accounts for the selected station and quarter."""
    rt = result.get('rt') or []
    if not 0 <= quarter < len(rt):
        st.info('本档案没有可查看的已执行水量时段。')
        return
    selected = _period(rt[quarter], quarter)
    tag = str(result.get('run', 'dadu'))
    uid = station.get('id') if isinstance(station, dict) else station
    accounts = station_water_ledger(result)
    if not accounts or not any(r['inflow_m3'] is not None or r['storage_end_m3'] is not None for r in accounts):
        st.info('本档案未保存可复核的分站水账。')
        return
    station_tab, reserve_tab, channel_tab = st.tabs(('全站水账', '四库目标与余度', '河段时滞与在途'))
    with station_tab:
        subset = [r for r in accounts if r['quarter'] == selected]
        residuals = [abs(r['balance_residual_m3']) for r in subset if r['balance_residual_m3'] is not None]
        residual = f'{max(residuals):.3g} m³' if residuals else '缺记录'
        st.caption(f'{_clock(selected + 1)} · 可复核 {len(residuals)}/{len(subset)} 站 · '
                   f'本段最大绝对守恒残差 {residual}。总下泄由过机、专用生态旁泄和弃水组成。')
        chosen = [r for r in accounts if r['id'] == uid]
        if chosen:
            trace = _frame(chosen, {'interval_end':'时段末','natural_inflow_m3s':'执行区间来水',
                'upstream_arrival_m3s':'上游到水','release_m3s':'总下泄'})
            firm = ((result.get('company') or {}).get('offers') or {}).get(uid, {}).get('natural_inflow_m3s', [])
            market = _cascade(result).get('natural_inflow_m3s', [])
            if firm:
                trace['公司申报时区间预测'] = [firm[q['quarter']] if q['quarter'] < len(firm) else None for q in chosen]
            if market:
                trace['市场日前区间预测'] = [market[q['quarter']].get(uid) if q['quarter'] < len(market) else None
                                            for q in chosen]
            st.markdown(f'**{chosen[0]["name"]}来水与下泄**')
            time_chart(trace.set_index('时段末'), 'm³/s', height=225)
        st.caption('守恒残差＝期末库容－期初库容－区间来水量－上游到达水量＋总下泄量。'
                   '每段 900 秒，缺少原始字段时留空。')
        with st.expander(f'所选时段全部 {len(subset)} 站水账'):
            st.dataframe(_frame(subset, STATION_LABELS), hide_index=True)
        _download(_frame(accounts, STATION_LABELS), '下载全部已执行厂站水账',
                  f'{tag}-station-water.csv', 'dadu_water_station_csv_' + tag)
    with reserve_tab:
        _reservoir_view(result, quarter)
    with channel_tab:
        rows = reach_water_ledger(result)
        current = [r for r in rows if r['quarter'] == selected]
        if not current:
            st.caption('本档案未保存河段或交接队列。')
            return
        st.caption('时滞以 15 分钟为一刻。入河量来自上游本段下泄，实到量取自下游本段水账。')
        names = [r['reach'] for r in current]
        index = next((i for i, r in enumerate(current) if r['upstream'] == uid), 0)
        if not any(r['upstream'] == uid for r in current):
            index = next((i for i, r in enumerate(current) if r['downstream'] == uid), 0)
        chosen = st.selectbox('查看河段', names, index=index, key=f'dadu_water_reach_{tag}_{uid}')
        link = next(r for r in current if r['reach'] == chosen)
        lag = link['lag_quarters']
        if isinstance(lag, int) and lag >= 0:
            arrival_q = selected + lag
            arrival_index = next((i for i, row in enumerate(rt) if _period(row, i) == arrival_q), None)
            volume = link['release_entered_m3']
            amount = f'{volume/1e4:,.2f} 万 m³' if volume is not None else '水量待核'
            reach_name = _stations(result).get(link['downstream'], {}).get('name',link['downstream'])
            st.caption(f"本段入河 {amount}，河段时滞 {lag} 段，预计于 {_clock(arrival_q)}–{_clock(arrival_q+1)} 到达{reach_name}。")
            unit_key, time_key = 'dadu_unit_'+tag, 'dadu_q_'+tag
            if arrival_index is None:
                st.caption('到达时段超出本档案的执行范围，暂保留预计到时和队列。')
            elif unit_key in st.session_state and time_key in st.session_state:
                destination = {unit_key:link['downstream'], time_key:arrival_index,
                               f"dadu_water_reach_{tag}_{link['downstream']}":chosen,
                               'dadu_focus_station':link['downstream'], 'dadu_focus_quarter':arrival_index}
                st.button(f'查看{reach_name}到水时段', key='dadu_arrival_'+tag,
                          on_click=st.session_state.update, args=(destination,))
        history = _frame([r for r in rows if r['reach'] == chosen],
                         {'interval_end':'时段末', 'release_entered_m3':'上游送入量',
                          'arrival_recorded_m3':'下游实到量'})
        time_chart(history.set_index('时段末'), 'm³/时段', height=190)
        queue = link['next_arrivals_m3']
        if queue is not None:
            st.markdown('**所选时段末仍在河道中的水量**')
            bars = pd.DataFrame({'到达时段末': [_clock(selected + k + 2) for k in range(len(queue))],
                                 '按已执行下泄排定的到达量': queue})
            st.bar_chart(bars.set_index('到达时段末'), sort=False, x_label='预计到达时段末',
                         y_label='m³', height=180)
            st.caption('队列来自已执行的上游下泄。首格在下一时段到达，超过 24:00 的水量继续计入次日在途。')
        else:
            st.caption('缺少本时段末的逐格在途队列。')
        with st.expander(f'所选时段全部 {len(current)} 条河段水账'):
            st.dataframe(_frame(current, REACH_LABELS), hide_index=True)
        _download(_frame(rows, REACH_LABELS), '下载全部已执行河段水账',
                  f'{tag}-reach-water.csv', 'dadu_water_reach_csv_' + tag)
