"""Saved physical renewable curtailment, with explicit scope and missing-data handling.

No current scenario inputs, summary totals, or overlapping rolling windows are used
as substitutes for saved per-unit physical quantities.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

TYPE_NAMES = {'wind': '风电', 'pv': '光伏'}
VALUE_COLUMNS = ['avail_mw', 'P_mw', 'curtail_mw']
# CSV outputs round MW to four decimal places. Do not hide physical negatives.
ROUNDING_TOLERANCE = 0.0002


def _snapshot(R):
    root = R.get('snapshot_root')
    config = Path(root) / 'config' if root else None
    def read(name):
        if config is None:
            return {}
        try:
            obj = yaml.safe_load((config / name).read_text(encoding='utf-8'))
            return obj if isinstance(obj, dict) else {}
        except (OSError, ValueError, yaml.YAMLError):
            return {}
    base, units, modes = read('base.yaml'), read('units.yaml'), read('modes.yaml')
    mode = modes.get(base.get('rule_set'), {})
    mode = mode if isinstance(mode, dict) else {}
    roster = {str(u['id']): u['type'] for u in (units.get('renewable') or [])
              if u.get('enabled', True) and u.get('type') in TYPE_NAMES and u.get('id') is not None}
    try:
        dt = float(base['dt_h'])
        if not np.isfinite(dt) or dt <= 0:
            dt = None
    except (KeyError, TypeError, ValueError):
        dt = None
    return base, roster, 'renewable' in units, dt, mode.get('realtime', {}).get('first_period_actual')


def _periods(frame):
    if frame is None or 'period' not in frame:
        return []
    numbers = pd.to_numeric(frame['period'], errors='coerce')
    return sorted({int(v) for v in numbers if np.isfinite(v) and v >= 0 and v == int(v)})


def _aggregate(frame, dt):
    known = not frame.empty and bool(frame['valid'].all())
    out = {'valid': known, 'record_count': len(frame), 'valid_count': int(frame['valid'].sum())}
    for name in VALUE_COLUMNS:
        out[name.replace('_mw', '_mwh')] = float(frame[name].sum() * dt) if known and dt is not None else np.nan
    # Rate is a ratio of energy over the same scope, never the mean of period rates.
    available = float(frame.avail_mw.sum()) if known else np.nan
    out['rate_pct'] = float(frame.curtail_mw.sum() / available * 100) if available > 0 else np.nan
    out['status'] = ('完整' if dt is not None else '时段长度未记录') if known else '数据缺失或异常'
    return out


def curtailment_report(R):
    """Return validated DA/RT rows and separate wind/solar energy, using only this run."""
    base, roster, roster_known, dt, actual = _snapshot(R)
    # Saved unit types can identify historical resources, but do not prove an absent
    # technology was not configured. Include either stage to detect missing stations.
    for key in ('da_unit_awards', 'rt_units'):
        frame = R.get(key)
        if frame is not None and {'unit', 'type'} <= set(frame):
            for row in frame.loc[frame.type.isin(TYPE_NAMES), ['unit', 'type']].itertuples(index=False):
                if pd.notna(row.unit):
                    roster.setdefault(str(row.unit), row.type)
    report = {'dt_h': dt, 'stages': {}}
    stages = [('日前', 'da_unit_awards', 'da_nodal_prices')]
    if R.get('full') or R.get('rt_units') is not None:
        stages.append(('实时', 'rt_units', 'rt_prices'))
    for stage, key, price_key in stages:
        raw = R.get(key)
        periods = _periods(R.get(price_key))
        if stage == '日前' and isinstance(base.get('periods'), int) and base['periods'] > 0:
            periods = sorted(set(periods) | set(range(base['periods'])))
        if stage == '实时':
            count = R.get('summary', {}).get('executed_periods')
            if isinstance(count, int) and count > 0:
                periods = sorted(set(periods) | set(range(count)))
        periods = sorted(set(periods) | set(_periods(raw)))
        rows = []
        for unit, typ in sorted(roster.items()):
            selected = raw[raw.unit.astype(str) == unit] if raw is not None and 'unit' in raw else pd.DataFrame()
            for period in periods:
                matches = selected[pd.to_numeric(selected.period, errors='coerce') == period] if 'period' in selected else pd.DataFrame()
                row = dict(period=period, unit=unit, type=typ, bus=np.nan, avail_mw=np.nan,
                           P_mw=np.nan, curtail_mw=np.nan, valid=False, reason='缺少场站时段记录')
                if len(matches) == 1:
                    saved = matches.iloc[0]
                    row['bus'] = saved.get('bus', np.nan)
                    values = pd.to_numeric(saved.reindex(VALUE_COLUMNS), errors='coerce')
                    row.update(values.to_dict())
                    if saved.get('type') != typ:
                        row['reason'] = '资源类型缺失或不一致'
                    elif not np.isfinite(values[['avail_mw', 'P_mw']]).all():
                        row['reason'] = '可用量或出清量未记录'
                    else:
                        difference = values.avail_mw - values.P_mw
                        if min(values.avail_mw, values.P_mw, difference) < -ROUNDING_TOLERANCE:
                            row['reason'] = '可用量、出清量或弃电差额为负'
                        elif 'curtail_mw' in matches and not np.isfinite(values.curtail_mw):
                            row['reason'] = '已保存弃电量缺失'
                        elif np.isfinite(values.curtail_mw) and abs(values.curtail_mw - difference) > ROUNDING_TOLERANCE:
                            row['reason'] = '弃电量与可用量减出清量不一致'
                        else:
                            # If the old export omits the entire curtailment column,
                            # it is recoverable from its own saved available/dispatch.
                            row['curtail_mw'] = max(0., float(values.curtail_mw if np.isfinite(values.curtail_mw) else difference))
                            row['avail_mw'] = max(0., row['avail_mw'])
                            row['P_mw'] = max(0., row['P_mw'])
                            row.update(valid=True, reason='')
                elif len(matches) > 1:
                    row['reason'] = '同一场站时段重复，未累加'
                if not row['valid']:
                    # An invalid row must not leak a plausible partial total/chart.
                    row.update({name: np.nan for name in VALUE_COLUMNS})
                row['curtail_mwh'] = row['curtail_mw'] * dt if dt is not None else np.nan
                rows.append(row)
        detail = pd.DataFrame(rows, columns=['period', 'unit', 'type', 'bus', *VALUE_COLUMNS, 'valid', 'reason', 'curtail_mwh'])
        summary = []
        for typ in TYPE_NAMES:
            part = detail[detail.type == typ]
            aggregate = _aggregate(part, dt)
            if not any(t == typ for t in roster.values()) and roster_known:
                aggregate['status'] = '未配置'
            summary.append(dict(type=typ, **aggregate))
        stations = [dict(unit=unit, type=part.type.iloc[0], **_aggregate(part, dt))
                    for unit, part in detail.groupby('unit', sort=True)]
        basis = ('日前申报可用预测（未申报时采用日前预测）' if stage == '日前' else
                 '实时首时段模拟实际可用量' if actual is True else
                 '实时首时段超短期预测可用量' if actual is False else
                 '实时已保存可用量（实际/预测口径未记录）')
        scope = f'{stage}计划 {len(periods)} 段' if stage == '日前' else f'实时已执行 {len(periods)} 段'
        report['stages'][stage] = dict(detail=detail, summary=pd.DataFrame(summary),
                                      stations=pd.DataFrame(stations), periods=periods,
                                      basis=basis, scope=scope)
    return report


def _energy_label(row):
    if row['status'] == '未配置':
        return '未配置'
    value = row['curtail_mwh']
    return f'{value:,.2f}' if pd.notna(value) else '未记录'


def curtailment_overview(report):
    import streamlit as st
    from common import metric_grid
    st.subheader('弃风与弃光')
    items = []
    for stage, data in report['stages'].items():
        for row in data['summary'].to_dict('records'):
            name = '弃风' if row['type'] == 'wind' else '弃光'
            items.append((f'{stage}{name} · MWh', _energy_label(row), data['scope'],
                          '仅汇总已保存的正式物理出清。缺少场站、时段、可用量或时长时不报零。'))
    metric_grid(items)
    st.caption('日前按预测可用量计算，实时仅统计已执行时段；两者不可相加。分时、分场站和弃电率见“出力计划与实际 → 弃风弃光”。')


def _summary_table(frame):
    names = {'unit': '场站', 'type': '资源类型', 'avail_mwh': '可用电量 MWh', 'P_mwh': '出清电量 MWh',
             'curtail_mwh': '弃电量 MWh', 'rate_pct': '弃电率 %', 'status': '数据状态'}
    return frame[[key for key in names if key in frame]].rename(columns=names)


def curtailment_dashboard(report, stage=None, key_prefix=''):
    """stage=None 保留原阶段单选；第二轮J项结果页按阶段分块时直接传入 '日前'/'实时'。"""
    import plotly.graph_objects as go
    import streamlit as st
    from common import PALETTE, TYPE_COLOR, df_download, show_df, show_fig, style_fig
    st.subheader('风光可用量、出清量与弃电')
    if stage is None:
        stage = st.radio('弃风弃光阶段', list(report['stages']), horizontal=True, key=key_prefix+'curtailment_stage')
    elif stage not in report['stages']:
        st.info(f'本次结果没有{stage}阶段的风光弃电记录。')
        return
    data = report['stages'][stage]
    dt = report['dt_h']
    st.caption(f"统计范围：{data['scope']}。可用量口径：{data['basis']}。")
    st.caption('弃电功率 = 保存的可用功率 − 正式物理出清功率；弃电量 = 各段弃电功率 × 时段小时数。弃电率 = 弃电量 / 同范围可用电量；可用量为零时不定义弃电率。不是日前与实时出力偏差，也不含弃水。')
    st.caption('“模拟实际”是本仿真的实际曲线，不代表电站实测数据。实时只用每个窗口首个已执行时段，不重复累计前瞻窗口。')
    if dt is None:
        st.warning('冻结输入未记录有效时段长度，电量显示未记录；仍可查看保存的功率。未借用当前场景的时长。')
    else:
        st.caption(f'每段 {dt:g} 小时（{dt * 60:g} 分钟），只读取本次运行的冻结输入。')
    detail = data['detail']
    if not detail.empty and not detail.valid.all():
        st.warning('部分场站或时段数据缺失、重复或异常。受影响的分项总量显示未记录，曲线保留缺口，不以零填补。')
    st.caption('以下汇总为全网；场站筛选只影响下方曲线和分时下载。')
    show_df(_summary_table(data['summary']), '%.2f')
    if detail.empty:
        st.info('没有可核查的风光场站逐时段记录，不能推断弃电为零。')
        return
    show_df(_summary_table(data['stations']), '%.2f')
    selected = st.multiselect('风光场站', sorted(detail.unit.unique()), default=sorted(detail.unit.unique()),
                              key=f'{key_prefix}curtailment_units_{stage}')
    if not selected:
        st.info('请选择至少一个风光场站。')
        return
    chosen = detail[detail.unit.isin(selected)].copy()
    def labels(periods):
        if dt is None:
            return [f'时段 {int(t)}' for t in periods]
        minutes = [round(float(t) * dt * 60) for t in periods]
        return [f'{t // 60:02d}:{t % 60:02d}' for t in minutes]
    fig = go.Figure()
    for typ, name in TYPE_NAMES.items():
        part = chosen[chosen.type == typ]
        if part.empty:
            continue
        aggregate = part.groupby('period').curtail_mw.sum(min_count=part.unit.nunique())
        fig.add_bar(x=labels(aggregate.index), y=aggregate.values, name='弃风' if typ == 'wind' else '弃光',
                    marker_color=TYPE_COLOR[typ])
    fig.update_layout(barmode='stack')
    show_fig(style_fig(fig, f'{stage}所选场站分时弃风弃光', '时刻' if dt else '时段序号', '弃电功率（MW）', 350), key=key_prefix+'curtailment_by_type')
    fig = go.Figure()
    for index, (unit, part) in enumerate(chosen.groupby('unit')):
        color = PALETTE[index % len(PALETTE)]
        for column, label, dash in [('avail_mw', '可用', 'dot'), ('P_mw', '出清', 'solid')]:
            fig.add_scatter(x=labels(part.period), y=part[column], name=f'{unit} {label}',
                            line=dict(color=color, dash=dash), connectgaps=False)
    show_fig(style_fig(fig, f'{stage}所选场站可用量与正式出清', '时刻' if dt else '时段序号', '功率（MW）', 380), key=key_prefix+'curtailment_available_dispatch')
    chosen['time'] = labels(chosen.period)
    export = chosen[['period', 'time', 'unit', 'type', 'bus', *VALUE_COLUMNS, 'curtail_mwh', 'reason']].rename(
        columns={'curtail_mwh': '分时弃电量 MWh', 'reason': '数据说明'})
    show_df(export, '%.4f', height=320)
    df_download(export, f'{"da" if stage == "日前" else "rt"}_curtailment.csv', label='下载所选场站分时弃电', key=key_prefix+'curtailment_detail')
