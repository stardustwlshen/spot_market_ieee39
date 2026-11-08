"""Read-only renewable input explorer, separate from dispatched generation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

KINDS = {'wind': '风电', 'pv': '光伏'}
STAGES = {
    'actual': '实际可用功率（场景输入）',
    'da': '日前预测（原始输入）',
    'id': '日内预测基线',
    'rt': '实时预测基线',
    'declared': '报价申报可用量',
    'effective_da': '日前有效可用量（报价优先）',
}
VIEWS = {'total': '总和', 'type': '按风电 / 光伏汇总', 'unit': '分机组'}


@dataclass
class RenewableInputs:
    time: pd.DataFrame
    units: dict[str, str]
    curves: dict[tuple[str, str], pd.Series]
    warnings: list[str]
    quote_known: bool


def renewable_inputs(cfg, profiles, bids=None):
    """Keep user curves and current quote detached; missing data stays unknown.

    The caller supplies the current working quote, never an implicit latest bid.
    A missing declaration uses DA only in effective_da, never in declared.
    """
    if not profiles.columns.is_unique:
        raise ValueError('新能源曲线列名重复，不能确定数据来源。')
    if len(profiles) != cfg.T:
        raise ValueError(f'新能源曲线需 {cfg.T} 个时段，当前为 {len(profiles)}。')
    if 'time' not in profiles or profiles['time'].isna().any() or profiles['time'].duplicated().any():
        raise ValueError('新能源曲线缺少唯一、完整的时间列。')
    period = profiles['period'] if 'period' in profiles else pd.Series(range(1, cfg.T + 1))
    if not np.array_equal(period.to_numpy(), np.arange(1, cfg.T + 1)):
        raise ValueError('新能源时段须按 1..T 排列，不能重复或缺失。')
    time = pd.DataFrame({'时段': period.to_numpy(), '时间': profiles['time'].astype(str).to_numpy()})
    units, specs, curves, warnings = {}, {}, {}, []
    seen = set()
    for unit in cfg.units.get('renewable', []):
        uid = unit.get('id')
        if not isinstance(uid, str) or not uid or uid in seen:
            raise ValueError(f'新能源机组编号缺失或重复：{uid}，不能计算总和。')
        seen.add(uid)
        if not unit.get('enabled', True):
            continue
        kind = unit.get('type')
        if kind not in KINDS:
            raise ValueError(f'{uid} 的新能源类型 {kind!r} 未识别，不能漏计到总和。')
        units[uid], specs[uid] = kind, unit

    def values(raw, uid, stage):
        label = f'{uid} · {STAGES[stage]}'
        if not isinstance(raw, (list, tuple, np.ndarray, pd.Series)) or len(raw) != cfg.T:
            warnings.append(f'{label}：缺失或长度不是 {cfg.T}，不补零。')
            return pd.Series(np.nan, index=range(cfg.T))
        result = pd.to_numeric(pd.Series(list(raw)), errors='coerce').astype(float)
        invalid = ~np.isfinite(result) | result.lt(0) | result.gt(float(specs[uid]['cap']) + 1e-6)
        invalid |= pd.Series([isinstance(x, (bool, np.bool_)) for x in raw])
        if invalid.any():
            warnings.append(f'{label}：{int(invalid.sum())} 个时段缺失或无效，显示空缺，不补零。')
        return result.mask(invalid)

    quotes = bids.get('units') if isinstance(bids, dict) else None
    if bids is not None and not isinstance(quotes, dict):
        warnings.append('当前报价格式不能读取；不显示申报量或日前有效可用量，不改用默认报价。')
    for uid in units:
        for stage in ('actual', 'da', 'id', 'rt'):
            curves[uid, stage] = values(profiles.get(f'{uid}_{stage}'), uid, stage)
        if not isinstance(quotes, dict):
            continue
        bid = quotes.get(uid)
        if not isinstance(bid, dict):
            warnings.append(f'{uid}：当前报价缺失，日前有效可用量未知，不代入其他报价。')
            curves[uid, 'effective_da'] = pd.Series(np.nan, index=range(cfg.T))
            continue
        declared = bid.get('avail_pmax')
        if declared is None:
            curves[uid, 'effective_da'] = curves[uid, 'da'].copy()
        else:
            curves[uid, 'declared'] = values(declared, uid, 'declared')
            curves[uid, 'effective_da'] = curves[uid, 'declared'].copy()
    return RenewableInputs(time, units, curves, warnings, isinstance(quotes, dict))


def available_stages(data, unit_ids):
    return [stage for stage in STAGES if any(
        (uid, stage) in data.curves and data.curves[uid, stage].notna().any() for uid in unit_ids)]


def selected_curves(data, *, kinds, stages, view='total', unit_ids=None):
    """Sum each period once and require every selected member at that period."""
    if view not in VIEWS or set(kinds) - KINDS.keys() or set(stages) - STAGES.keys():
        raise ValueError('新能源曲线选择无效。')
    allowed = [uid for uid, kind in data.units.items() if kind in kinds]
    if unit_ids is not None and set(unit_ids) - set(allowed):
        raise ValueError('所选机组不在当前资源类型范围内。')
    selected = allowed if unit_ids is None else list(dict.fromkeys(unit_ids))
    stages = list(dict.fromkeys(stages))
    if view == 'unit':
        groups = [(f'{uid} · {KINDS[data.units[uid]]}', [uid]) for uid in selected]
    elif view == 'type':
        groups = [(f'{label}合计', [uid for uid in selected if data.units[uid] == kind])
                  for kind, label in KINDS.items()]
    else:
        groups = [('所选新能源合计', selected)]
    table, warnings = data.time.copy(), []
    for label, members in groups:
        if not members:
            continue
        for stage in stages:
            columns = [data.curves.get((uid, stage), pd.Series(np.nan, index=data.time.index)) for uid in members]
            # min_count prevents partial sums being presented as a complete total.
            total = pd.concat(columns, axis=1).sum(axis=1, min_count=len(members))
            name = f'{label} · {STAGES[stage]}（MW）'
            table[name] = total
            if total.isna().any():
                warnings.append(f'{label} · {STAGES[stage]}：{int(total.isna().sum())} 个时段数据不完整，未计算部分合计。')
    return table, warnings


def renewable_figure(table, *, yaxis_title='可用功率（MW）'):
    fig = go.Figure()
    for name in table.columns[2:]:
        fig.add_trace(go.Scatter(
            x=table['时间'].tolist(), y=[None if pd.isna(v) else float(v) for v in table[name]],
            name=name.removesuffix('（MW）'), mode='lines', connectgaps=False,
            hovertemplate='%{fullData.name}：%{y:.3f} MW<extra></extra>',
        ))
    fig.update_layout(
        hovermode='x unified', hoverdistance=-1, height=430,
        hoverlabel=dict(namelength=-1), margin=dict(l=55, r=20, t=20, b=95),
        xaxis_title='时间', yaxis_title=yaxis_title,
        legend=dict(orientation='h', x=0, y=-.2, yanchor='top'),
    )
    times = table['时间'].tolist()
    fig.update_xaxes(type='category', categoryorder='array', categoryarray=times,
                     tickmode='array', tickvals=times[::max(1, len(times) // 12)],
                     showspikes=True, spikemode='across', spikesnap='cursor')
    return fig


def _multiselect(label, options, defaults, key, format_func):
    # Remove stale choices after a type/roster change; never repopulate an empty selection.
    if key in st.session_state:
        st.session_state[key] = [value for value in st.session_state[key] if value in options]
    return st.multiselect(label, options, default=defaults, format_func=format_func, key=key)


def render_renewable_inputs(cfg, profiles, *, bids=None, key_prefix='renewable_inputs'):
    st.markdown('**新能源输入曲线**')
    st.caption('实际可用功率是场景输入，不是实际发电或出清结果；日内、实时为预测基线，滚动预测还按提前量插值，不代表每轮实际使用曲线。')
    try:
        data = renewable_inputs(cfg, profiles, bids)
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f'新能源曲线暂不能读取：{exc}')
        return
    if not data.units:
        st.info('当前场景没有启用的风电或光伏机组。')
        return
    left, right = st.columns(2)
    with left:
        kind_options = [kind for kind in KINDS if kind in data.units.values()]
        kinds = _multiselect('资源类型', kind_options, kind_options, key_prefix + ':kinds', KINDS.get)
    with right:
        view = st.radio('查看方式', list(VIEWS), format_func=VIEWS.get, horizontal=True, key=key_prefix + ':view')
    units = [uid for uid, kind in data.units.items() if kind in kinds]
    if view == 'unit':
        units = _multiselect('查看机组', units, units[:2], key_prefix + ':units',
                             lambda uid: f'{uid} · {KINDS[data.units[uid]]}')
    options = available_stages(data, units)
    defaults = [s for s in ('actual', 'effective_da' if data.quote_known else 'da') if s in options]
    stages = _multiselect('查看曲线', options, defaults, key_prefix + ':stages', STAGES.get)
    if not data.quote_known:
        st.caption('当前工作报价未提供，申报量和日前有效可用量未展示。')
    else:
        st.caption('日前有效可用量逐机组优先采用当前工作报价的申报值；未申报的机组沿用日前预测。')
        undeclared = [uid for uid in units if (uid, 'declared') not in data.curves]
        if undeclared:
            st.caption('未提供独立申报量：' + '、'.join(undeclared) + '。申报量总和不把未申报机组补为零。')
    if data.warnings:
        with st.expander('输入数据缺项或异常', expanded=True):
            for warning in data.warnings:
                st.warning(warning)
    if not units or not stages:
        st.info('请选择资源类型、机组和要查看的曲线。')
        return
    table, warnings = selected_curves(data, kinds=kinds, stages=stages, view=view, unit_ids=units)
    for warning in warnings:
        st.warning(warning)
    st.caption(f'当前范围：{len(units)} 台机组（' + '、'.join(units) + f'），{len(table)} 个时段。悬停同一时刻可同时查看所有已选曲线。')
    st.plotly_chart(renewable_figure(table), width='stretch', key=key_prefix + ':chart',
                    config={'displaylogo': False, 'responsive': True})
    if st.checkbox(f'显示全部 {len(table)} 个时段数据', key=key_prefix + ':table'):
        st.caption('表格包含当前选择的全部时段和曲线，空缺表示未提供或无效，不代表零。')
        st.dataframe(table, hide_index=True, width='stretch', height=430)
