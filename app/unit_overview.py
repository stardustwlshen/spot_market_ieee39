"""Read-only, same-subject DA/ID/RT and cash overview of one saved run."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from common import TYPE_CN, display_frame, df_download, period_labels, DA_COLOR, RT_COLOR, show_fig, style_fig
from chinese_labels import value_label
from settlement_validation import normalized_profit_table
from smm.time_grid import result_time_grid
from stage_results import intraday_inventory

POWER = {'da': '日前计划净出力 MW', 'id': '日内参考净出力 MW', 'rt': '实时实际净出力 MW'}
PRICE = {'da': '日前选用结算价 元/MWh', 'id': '日内指示价 元/MWh', 'rt': '实时选用结算价 元/MWh'}
STAGE = {'da': '日前计划', 'id': '日内参考（不执行、不结算）', 'rt': '实时实际执行'}
MONEY = ['da_cash_revenue_yuan', 'rt_deviation_cash_revenue_yuan', 'cash_revenue_yuan',
         'variable_cost_yuan', 'fixed_online_cost_yuan', 'startup_cost_yuan',
         'cash_operating_cost_yuan', 'simulated_operating_profit_yuan']


def frame(value):
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def unit_catalog(result):
    """Only saved participants; never borrow today's scenario or create missing rows."""
    rows = pd.concat([frame(result.get(k)) for k in ('da_unit_awards', 'rt_units')], ignore_index=True)
    if rows.empty or not {'unit', 'type'} <= set(rows):
        return pd.DataFrame(columns=['unit', 'type', 'bus'])
    cols = [c for c in ('unit', 'type', 'bus') if c in rows]
    catalog = rows[cols].drop_duplicates()
    if catalog.unit.duplicated().any() or catalog.unit.isna().any():
        raise ValueError('主体类型或节点在保存记录中不一致')
    return catalog.sort_values('unit').reset_index(drop=True)


def entity_names(uid, kind):
    return ([uid + '_放电', uid + '_充电'] if kind == 'storage' else
            [uid + '_发电', uid + '_抽水'] if kind == 'pumped' else [uid])


def selected_rows(table, identifier, uid, *, periods, numeric=()):
    data = frame(table)
    if data.empty:
        return data
    if identifier not in data or 'period' not in data:
        raise ValueError('缺少主体/时段字段')
    data = data.loc[data[identifier].eq(uid)].copy()
    if data.empty:
        return data
    p = pd.to_numeric(data.period, errors='coerce')
    if p.isna().any() or not p.isin(periods).all() or data.duplicated('period').any():
        raise ValueError('时段缺失、重复或超出本次日网格')
    data['period'] = p.astype(int)
    for col in numeric:
        if col not in data:
            raise ValueError('缺少字段 ' + col)
        values = pd.to_numeric(data[col], errors='coerce')
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(col + ' 含缺失或非有限数值')
        data[col] = values
    return data.sort_values('period')


def round_inventory(result, diagnostics=None):
    rows = intraday_inventory((result.get('summary') or {}).get('intraday', {}),
                              frame(result.get('id_rounds_summary')), diagnostics or {})
    if not rows.empty:
        n, _ = result_time_grid(result)
        if not rows.tau.isin(range(n)).all():
            raise ValueError('日内轮次起点超出本次日网格')
    return rows



def saved_price_schema(prices, stage):
    """Match the network view's saved-export contract; never apply current limits."""
    indicative = stage == 'id'
    explicit = 'lmp_clamped' in prices
    exported = ('lmp_raw' in prices or ('settlement_price_source' in prices and
                not prices.empty and prices.settlement_price_source.notna().all() and
                prices.settlement_price_source.astype(str).str.contains('clipped').all()))
    selected = 'lmp_clamped' if explicit else 'lmp'
    known = indicative or explicit or exported
    label = PRICE[stage] if known else ('日前' if stage == 'da' else '实时') + '保存价格（口径未记录）元/MWh'
    names = {selected: label}
    if explicit and 'lmp' in prices:
        names['lmp'] = '原始节点价格 元/MWh' if 'lmp_raw' not in prices else '保存lmp字段（非所选价）元/MWh'
    if 'lmp_raw' in prices:
        names['lmp_raw'] = '选定定价原始电价 元/MWh'
    return selected, label, names, known


def build_unit_overview(result, uid, *, id_tau=None, diagnostics=None):
    """Pure presentation model. A broken optional source fails locally, never becomes zero."""
    catalog = unit_catalog(result)
    chosen = catalog[catalog.unit.eq(uid)]
    if len(chosen) != 1:
        raise ValueError('所选主体不在本次保存结果中')
    identity = chosen.iloc[0].to_dict()
    kind = identity['type']
    n, dt = result_time_grid(result)
    axis = pd.DataFrame({'period': range(n), '时刻': period_labels(n, dt)})
    out = dict(identity=identity, comparison=axis, notices=[], stage_rows={}, states={},
               price_sources={}, price_labels={}, cash=pd.DataFrame(), profit=pd.DataFrame(), profit_period=pd.DataFrame(),
               inventory=pd.DataFrame(), cost_parameters=pd.DataFrame(), cost_source=None, cost_hash=None)
    periods = set(range(n))

    def read(label, function):
        try:
            return function()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            out['notices'].append(label + '不可用：' + str(exc))
            return pd.DataFrame()

    raw = {'da': frame(result.get('da_unit_awards')), 'rt': frame(result.get('rt_units')), 'id': pd.DataFrame()}
    id_prices = pd.DataFrame()
    inv = read('日内轮次', lambda: round_inventory(result, diagnostics))
    round_no = None
    if id_tau is not None and not inv.empty and 'tau' in inv:
        selected = inv.loc[inv.tau.eq(id_tau)]
        if len(selected) == 1 and bool(selected.iloc[0]['success']):
            rounds = frame(result.get('id_rounds_summary'))
            row = rounds.loc[rounds.tau.eq(id_tau)] if 'tau' in rounds else pd.DataFrame()
            if len(row) == 1 and 'round' in row:
                round_no = row.iloc[0]['round']
                plans = frame(result.get('id_plans'))
                prices = frame(result.get('id_prices_indicative'))
                if 'round' in plans:
                    raw['id'] = plans.loc[plans['round'].eq(round_no)].copy()
                    if 'tau' in raw['id'] and not raw['id'].tau.eq(id_tau).all():
                        raw['id'] = pd.DataFrame()
                        out['notices'].append('日内轮次与起点不一致，参考计划未显示。')
                if 'round' in prices:
                    id_prices = prices.loc[prices['round'].eq(round_no)].copy()
            else:
                out['notices'].append('所选日内轮缺少唯一轮次编号，不拼接其他轮次作为本轮参考。')
        else:
            out['notices'].append('所选日内轮失败或未记录成功计划，不展示旧轮次或日前替代量。')
    out['id_round'] = round_no
    for stage in ('da', 'id', 'rt'):
        rows = read(STAGE[stage], lambda s=stage: selected_rows(raw[s], 'unit', uid, periods=periods, numeric=['P_mw']))
        out['stage_rows'][stage] = rows
        col = POWER[stage]
        if not rows.empty:
            out['comparison'] = out['comparison'].merge(rows[['period', 'P_mw']].rename(columns={'P_mw': col}), on='period', how='left', validate='one_to_one')
        else:
            out['comparison'][col] = np.nan
        out['comparison'][STAGE[stage] + '净电量 MWh'] = out['comparison'][col] * dt
        ptable = id_prices if stage == 'id' else frame(result.get('da_nodal_prices' if stage == 'da' else 'rt_prices'))
        price_field, price_label, price_names, known = saved_price_schema(ptable, stage)
        out['price_labels'][stage] = price_label
        prices = read(STAGE[stage] + '价格', lambda t=ptable, f=price_field: selected_rows(t, 'bus', identity.get('bus'), periods=periods, numeric=[f]))
        if not known and not prices.empty:
            out['notices'].append(STAGE[stage] + '仅保存lmp且缺少价格口径证据；按未知口径展示，不推定为限价后结算价。')
        if not prices.empty:
            # An ID price without the selected subject's reference quantity is not a subject result.
            if stage == 'id':
                prices = prices[prices.period.isin(rows.period if not rows.empty else [])]
            out['comparison'] = out['comparison'].merge(prices[['period', price_field]].rename(columns={price_field: price_label}), on='period', how='left', validate='one_to_one')
            keep = [c for c in ('period', 'bus', 'lmp', 'lmp_clamped', 'lmp_raw', 'dispatch_raw_lmp', 'pricing_source', 'raw_price_source', 'settlement_price_source') if c in prices]
            out['price_sources'][stage] = prices[keep].rename(columns=price_names).copy()
        else:
            out['comparison'][price_label] = np.nan
    out['comparison']['实时−日前 MW'] = out['comparison'][POWER['rt']] - out['comparison'][POWER['da']]

    # Resource-specific states are direct archived fields; hydro on is not rotor count.
    generic = ['period', 'on', 'startup', 'avail_mw', 'curtail_mw', 'r_up', 'r_dn']
    specialized = {
        'storage': ['period', 'P_ch', 'P_dis', 'u_ch', 'u_dis', 'E_start', 'E_mwh', 'soc'],
        'hydro': ['period', 'P_mw', 'units_on', 'P_start_mw', 'units_start', 'V_start', 'V_1e4m3', 'Q_in', 'Q_gen', 'Q_spill', 'Q_out'],
        'pumped': ['period', 'P_gen', 'P_pump', 'n_gen', 'n_pump', 'mode', 'V_start', 'V_up', 'V_dn'],
    }
    for stage in ('da', 'rt'):
        rows = out['stage_rows'][stage]
        if kind in specialized:
            table = read(STAGE[stage] + '状态', lambda s=stage: selected_rows(result.get(s + '_' + kind), 'station' if kind == 'hydro' else 'unit', uid, periods=periods))
            fields = specialized[kind]
        else:
            table, fields = rows, generic
        if not table.empty:
            out['states'][stage] = table[[c for c in fields if c in table]].copy()
    if round_no is not None:
        entry = next((r for r in (diagnostics or {}).get('id_rounds', []) if r.get('tau') == id_tau), {})
        stage = (entry.get('stages') or {}).get('energy_sced') or {}
        if kind == 'hydro':
            table = read('日内水电状态', lambda: selected_rows(pd.DataFrame(stage.get('hydro_states') or []), 'station', uid, periods=periods))
            fields = ['period', 'V_1e4m3', 'Qout_m3s']
        elif kind in ('storage', 'pumped'):
            table = read('日内库存状态', lambda: selected_rows(pd.DataFrame(stage.get('units') or []), 'unit', uid, periods=periods))
            fields = ['period', 'P_dis_mw', 'P_ch_mw', 'E_mwh'] if kind == 'storage' else ['period', 'P_gen_mw', 'P_pump_mw', 'V_up_1e4m3']
        else:
            table, fields = out['stage_rows']['id'], generic
        if not table.empty:
            out['states']['id'] = table[[c for c in fields if c in table]].copy()

    executed = set(out['stage_rows']['rt'].period) if not out['stage_rows']['rt'].empty else set()
    def cash_rows():
        all_cash = frame(result.get('settlement_detail'))
        if all_cash.empty or not executed:
            return pd.DataFrame()
        required = {'entity', 'period', 'da_amount', 'rt_amount', 'total'}
        if not required <= set(all_cash):
            raise ValueError('缺少结算主体、时段或金额')
        names = entity_names(uid, kind)
        cash = all_cash.loc[all_cash.entity.isin(names)].copy()
        if cash.duplicated(['entity', 'period']).any() or set(zip(cash.entity, cash.period)) != {(x, t) for x in names for t in executed}:
            raise ValueError('主体结算明细缺失、重复或与实际执行范围不符')
        for c in ('da_amount', 'rt_amount', 'total'):
            cash[c] = pd.to_numeric(cash[c], errors='coerce')
            if not np.isfinite(cash[c].to_numpy()).all():
                raise ValueError('现金字段缺失或非有限数值')
        if (cash.da_amount + cash.rt_amount - cash.total).abs().gt(.01).any():
            raise ValueError('日前与实时偏差现金之和不等于净现金')
        grouped = cash.groupby('period')[['da_amount', 'rt_amount', 'total']].sum().reset_index()
        grouped = grouped.rename(columns=dict(da_amount='da_cash_revenue_yuan', rt_amount='rt_deviation_cash_revenue_yuan', total='cash_revenue_yuan'))
        if kind in ('storage', 'pumped'):
            charging = cash[cash.entity.eq(names[1])][['period', 'total']].rename(columns={'total': 'charging_settlement_yuan'})
            grouped = grouped.merge(charging, on='period', validate='one_to_one')
        return grouped
    out['cash'] = read('主体现金结算', cash_rows)
    if kind != 'tie':
        def profit_rows(key, per_period=False):
            data = frame(result.get(key))
            if not executed:
                return pd.DataFrame()
            if data.empty:
                return data
            if 'unit' not in data:
                raise ValueError('缺少利润主体编号')
            data = data[data.unit.eq(uid)].copy()
            if data.empty:
                return data
            if not {'cash_revenue_yuan', 'cash_operating_cost_yuan', 'simulated_operating_profit_yuan', 'cost_available'} <= set(data):
                raise ValueError('利润关键字段未保存')
            if 'type' in data and not data.type.eq(kind).all():
                raise ValueError('利润主体类型与出清记录不一致')
            data = normalized_profit_table(data, per_period=per_period)
            if per_period and set(data.period) != executed:
                raise ValueError('逐时利润范围与已执行时段不一致')
            cash = out['cash']
            if not cash.empty:
                if per_period:
                    compared = data.merge(cash[['period', 'cash_revenue_yuan']], on='period', suffixes=('', '_saved'), validate='one_to_one')
                    error = (compared.cash_revenue_yuan - compared.cash_revenue_yuan_saved).abs().max()
                else:
                    error = abs(float(data.cash_revenue_yuan.iloc[0]) - cash.cash_revenue_yuan.sum())
                if error > .02:
                    raise ValueError('利润现金与独立结算记录不一致')
            return data
        out['profit'] = read('主体利润汇总', lambda: profit_rows('operating_profit_by_unit'))
        out['profit_period'] = read('逐时利润', lambda: profit_rows('operating_profit_by_period', True))
        if not out['profit'].empty and not out['profit_period'].empty:
            valid = out['profit'].cost_available.eq(True).all() and out['profit_period'].cost_available.eq(True).all()
            cols = [c for c in MONEY if c in out['profit'] and c in out['profit_period']]
            if valid and (out['profit'][cols].iloc[0] - out['profit_period'][cols].sum()).abs().gt(.02).any():
                out['notices'].append('利润汇总与逐时核算不一致，未展示成本与利润；独立现金结算保留。')
                out['profit'] = pd.DataFrame()
                out['profit_period'] = pd.DataFrame()
        if not out['profit'].empty:
            row = out['profit'].iloc[0]
            out['cost_source'] = row.get('cost_source_kind')
            out['cost_hash'] = row.get('cost_profile_sha256')
            keep = [c for c in ('inventory_start', 'inventory_end', 'inventory_change', 'inventory_unit') if c in out['profit']]
            out['inventory'] = out['profit'][keep].copy()
    return out


def comparison_figure(comparison):
    """Keep the same stage colors/line styles as the rest of the result page."""
    import plotly.graph_objects as go
    figure = go.Figure()
    styles = {'da': (DA_COLOR, 'solid'), 'id': ('#2e8b80', 'dot'), 'rt': (RT_COLOR, 'dash')}
    for stage, label in POWER.items():
        color, dash = styles[stage]
        figure.add_scatter(x=comparison['时刻'], y=comparison[label], name=label,
                           mode='lines', connectgaps=False, line=dict(color=color, dash=dash, width=2))
    return style_fig(figure, x='时刻', y='净出力 MW', height=330, hover='x unified')


def render_unit_overview(result, *, diagnostics=None, prefix='unit_overview'):
    """Add one independent entry without changing any existing stage tabs or outputs."""
    try:
        catalog = unit_catalog(result)
    except (ValueError, TypeError, KeyError) as exc:
        st.warning('逐主体总览不可用：' + str(exc))
        return
    if catalog.empty:
        return
    with st.expander('机组/电站总览：日前、日内、实时与利润', expanded=True):
        st.caption('一次选择同一主体，查看本次保存的跨阶段出清与最终现金核算。日前是计划，日内是参考，实时仅含已执行首段。')
        uid = st.selectbox('总览机组/电站', catalog.unit.tolist(), key=prefix + ':unit')
        try:
            inv = round_inventory(result, diagnostics)
        except (ValueError, TypeError, KeyError, AttributeError):
            inv = pd.DataFrame()
            st.warning('日内轮次清单无法核对；不猜测参考轮次。')
        tau = None
        if not inv.empty:
            n, dt = result_time_grid(result)
            time_labels = period_labels(n, dt)
            taus = list(inv.tau)
            tau = st.selectbox('总览日内轮次（参考，不结算）', taus,
                               format_func=lambda t: f'{time_labels[int(t)]} · {inv.loc[inv.tau.eq(t), "status"].iloc[0]}', key=prefix + ':id_tau')
        else:
            st.caption('日内未启用或未保存轮次；对应列保持空白。')
        try:
            data = build_unit_overview(result, uid, id_tau=tau, diagnostics=diagnostics)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            st.warning('所选主体总览不可用，其他结果入口仍可查看：' + str(exc))
            return
        identity = data['identity']; kind = identity['type']
        st.markdown(f'**{uid} · {TYPE_CN.get(kind, kind)} · 节点 {identity.get("bus", "未记录")}**')
        if kind in ('hydro', 'pumped'):
            st.info('这是电站聚合主体：台数是工况信息，出清和利润不是逐台转子结果。')
        if kind == 'tie':
            st.info('区外来电为固定外生计划；可看保存的出力和结算，不参加SCUC/SCED主体优化，不纳入当前运营成本与利润核算。')
        for notice in data['notices']:
            st.warning(notice)
        scope = []
        for stage, rows in data['stage_rows'].items():
            scope.append(f'{STAGE[stage]}：' + (f'{len(rows)} 段，序号 {int(rows.period.min())}–{int(rows.period.max())}' if not rows.empty else '未提供'))
        st.caption('；'.join(scope) + '。空白不等于0，日内轮次不累加，实时前瞻量不冒充已执行量。')
        tabs = st.tabs(['同主体出清对照', '同主体状态明细', '同主体结算与利润', '同主体数据来源'])
        with tabs[0]:
            comparison = data['comparison']
            show_fig(comparison_figure(comparison), key=prefix + ':comparison_chart')
            st.dataframe(display_frame(comparison), hide_index=True, width='stretch')
            df_download(comparison, f'{uid}_DA_ID_RT_comparison.csv', label='下载所选主体跨阶段CSV', key=prefix + ':comparison')
            st.caption('功率和电量均为带符号净值，充电/抽水为负。ID价格是指示价；日前与实时仅在已保存字段/来源可确认时标为选用结算价，否则明确标注口径未记录。价格和出力不同于利润。')
        with tabs[1]:
            for stage in ('da', 'id', 'rt'):
                st.markdown('**' + STAGE[stage] + '**')
                states = data['states'].get(stage)
                if states is None or states.empty:
                    st.caption('该主体本阶段状态明细未提供，不反推或补造。')
                else:
                    names = {'units_on': '水电开机台数', 'units_start': '水电期初开机台数', 'P_start_mw': '水电时段初出力 MW'}
                    st.dataframe(display_frame(states.rename(columns=names)), hide_index=True, width='stretch')
            st.caption('储能电量单位MWh，水电/抽蓄库容单位万m³，流量单位m³/s。日内状态是参考计划，不是实际执行轨迹。')
        with tabs[2]:
            cash = data['cash']; profit = data['profit']; detail = data['profit_period']
            st.caption('模拟运营利润 = 日前现金结算 + 实时偏差现金结算 − 按实际RT执行量核算的运营成本。ID、SCUC及辅助定价没有独立实际利润。')
            if cash.empty:
                st.info('本主体现金结算未提供或尚未执行结算；不以现价×功率推算实际收入。')
                da = data['stage_rows']['da']
                if not da.empty and 'revenue_yuan' in da:
                    values = pd.to_numeric(da.revenue_yuan, errors='coerce')
                    if np.isfinite(values.to_numpy()).all():
                        st.metric('日前中标计划金额（非实际利润）· 元', f'{values.sum():,.2f}')
            else:
                columns = st.columns(3)
                for col, field, title in zip(columns, ('da_cash_revenue_yuan', 'rt_deviation_cash_revenue_yuan', 'cash_revenue_yuan'), ('日前现金结算 · 元', '实时偏差结算 · 元', '净能量现金结算 · 元')):
                    col.metric(title, f'{cash[field].sum():,.2f}')
            if kind == 'tie':
                st.caption('TIE运营成本与利润：不纳入当前核算，不显示为0。')
            elif profit.empty:
                st.info('本次未提供可核实的主体运营成本与利润；不能用报价或零成本代替。原有“收入与成本”页可查看历史补算入口。')
            else:
                row = profit.iloc[0]
                cols = st.columns(2)
                for col, field, label in zip(cols, ('cash_operating_cost_yuan', 'simulated_operating_profit_yuan'), ('现金运营成本 · 元', '模拟运营利润 · 元')):
                    value = row.get(field)
                    col.metric(label, '未提供' if pd.isna(value) else f'{value:,.2f}')
                if not profit.cost_available.eq(True).all():
                    st.warning('成本参数不完整，成本和利润未提供；现金结算仍可查看。')
                show = [c for c in ('unit', 'type', *MONEY, 'charging_settlement_yuan', 'cost_available', 'cost_missing_reason') if c in profit]
                st.dataframe(display_frame(profit[show]), hide_index=True, width='stretch')
                if not data['inventory'].empty:
                    st.dataframe(display_frame(data['inventory']), hide_index=True, width='stretch')
            st.caption('充电/抽水结算已在净现金内，不重复扣除；库存变化单列且不货币化。成本独立于报价，不含调度松弛罚值、水机会价、资本、融资、税费、容量/调频收益和补偿，不是企业净利润。')
            if not detail.empty:
                st.dataframe(display_frame(detail), hide_index=True, width='stretch')
                df_download(detail, f'{uid}_operating_profit_by_period.csv', label='下载所选主体逐时利润CSV', key=prefix + ':profit')
            elif not cash.empty:
                st.dataframe(display_frame(cash), hide_index=True, width='stretch')
            if not cash.empty:
                df_download(cash, f'{uid}_cash_by_period.csv', label='下载所选主体逐时现金CSV', key=prefix + ':cash')
        with tabs[3]:
            st.caption('仅使用当前选定结果的存档；界面更新不表示重新出清。')
            for stage in ('da', 'id', 'rt'):
                st.markdown('**' + STAGE[stage] + '价格与来源**')
                prices = data['price_sources'].get(stage)
                if prices is None or prices.empty:
                    st.caption('未保存可核对的本主体节点价格。')
                else:
                    if 'pricing_source' not in prices:
                        st.caption('定价来源字段未记录，不推断原始对偶或辅助定价。')
                    st.dataframe(display_frame(prices), hide_index=True, width='stretch')
            if kind != 'tie':
                source = data['cost_source']
                st.caption('成本数据来源：' + (str(value_label(source)) if isinstance(source, str) and source else '未提供'))
                cost_hash = data['cost_hash']
                st.caption('存档成本校验指纹：' + (str(cost_hash) if isinstance(cost_hash, str) and cost_hash else '未提供'))
                directory = Path(result.get('dir', ''))
                path = directory / 'fixed_cost_profile.json'
                if result.get('dir') and path.is_file():
                    try:
                        profile = json.loads(path.read_text(encoding='utf-8'))
                        params = profile.get('units', {}).get(uid)
                        if params and profile.get('sha256') == cost_hash:
                            labels = {'type': '资源类型', 'scalar_marginal_cost_yuan_per_mwh': '固定单位运营成本 元/MWh', 'fixed_online_cost_yuan_per_h': '在线成本 元/h', 'fixed_startup_cost_yuan_per_start': '启动成本 元/次', 'discharge_degradation_yuan_per_mwh': '放电损耗成本 元/MWh', 'generation_vom_yuan_per_mwh': '抽蓄发电运维成本 元/MWh', 'gen_start_yuan_per_unit_start': '抽蓄发电启动成本 元/台次', 'pump_start_yuan_per_unit_start': '抽蓄抽水启动成本 元/台次'}
                            st.dataframe(pd.DataFrame([{'参数': labels.get(k, k), '值': str(value_label(v))} for k, v in params.items()]), hide_index=True, width='stretch')
                            st.caption('参数来自本次保存的解析后固定成本副本；指纹一致性不等同于重新验证原始成本文件字节。')
                        else:
                            st.caption('解析后成本副本缺本主体或指纹不一致，不作为该利润的核算参数展示。')
                    except (OSError, ValueError, TypeError, AttributeError):
                        st.caption('解析后成本副本无法读取；不借用当前场景成本。')
                else:
                    st.caption('未保存解析后成本参数副本，不借用当前场景成本。')
