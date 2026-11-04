"""Read-only network explorer. Saved quantities never borrow current scene inputs."""
from __future__ import annotations

from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from common import FONT_FAMILY, TYPE_CN

GROUPS = ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie')
STAGES = {'da': '日前 DA · 计划', 'rt': '实时 RT · 已执行'}


def number(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def fmt(value, unit='', digits=2):
    value = number(value)
    return f'{value:,.{digits}f}{unit}' if np.isfinite(value) else '未知'


def capacity(group, unit):
    if group == 'hydro':
        return number(unit.get('n_units')) * number(unit.get('unit_pmax'))
    if group == 'pumped':
        return number(unit.get('n_units')) * number(unit.get('unit_gen_max'))
    return number(unit.get('cap' if group == 'renewable' else 'pmax'))


def network_data(root, *, base=None, catalog=None):
    """Load exact scene/frozen files. No fallback to the repository defaults."""
    root = Path(root)
    buses = pd.read_csv(root / 'data/case39/buses.csv')
    branches = pd.read_csv(root / 'data/case39/branches.csv')
    if buses.bus.duplicated().any() or branches.branch_id.duplicated().any():
        raise ValueError('节点或支路编号重复，不能唯一对应拓扑')
    if not set(branches.from_bus) | set(branches.to_bus) <= set(buses.bus):
        raise ValueError('支路连接了节点表中不存在的节点')
    if base is None:
        path = root / 'config/base.yaml'
        base = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else None
    if catalog is None:
        path = root / 'config/units.yaml'
        units = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else None
        catalog = ([(group, unit) for group in GROUPS for unit in (units or {}).get(group, []) or []]
                   if isinstance(units, dict) else None)
    rows = []
    for group, unit in catalog or []:
        if group == 'tie' and (base or {}).get('tie_line_mode') == 'fixed_load':
            continue  # Converted demand is on load nodes, not a generator at its old bus.
        rows.append(dict(unit=unit['id'], bus=int(unit['bus']), type=unit.get('type', group),
                         enabled=unit.get('enabled', True), capacity_mw=capacity(group, unit)))
    resources = pd.DataFrame(rows, columns=['unit', 'bus', 'type', 'enabled', 'capacity_mw'])
    if resources.unit.duplicated().any():
        raise ValueError('机组编号重复，不能唯一对应节点')
    if not set(resources.bus) <= set(buses.bus):
        raise ValueError('机组连接了节点表中不存在的节点')
    branches = branches.rename(columns={'rate_mw': 'rate_orig_mw'}).copy()
    branches['scale'] = np.nan
    if isinstance(base, dict):
        factors = base.get('line_scale') or {}
        branches['scale'] = branches.branch_id.map(lambda line: number(factors.get(line, 1))) * number(base.get('line_capacity_multiplier', 1))
    branches['rate_eff'] = (branches.rate_orig_mw * branches.scale).where((branches.scale > 0) & np.isfinite(branches.scale))
    buses['fixed_load_weight'] = (buses.load_mw / buses.load_mw.sum() if (base or {}).get('tie_line_mode') == 'fixed_load' else 0.)
    return dict(buses=buses.rename(columns={'load_mw': 'base_load_mw'}), branches=branches,
                resources=resources, resource_catalog_known=catalog is not None, tie_line_mode=(base or {}).get("tie_line_mode", "supply"))


def frame(value):
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value or [])


def stage_tables(result, stage):
    names = (('units', 'da_unit_awards'), ('prices', 'da_nodal_prices'),
             ('lines', 'da_line_flows'), ('system', 'da_system')) if stage == 'da' else (
             ('units', 'rt_units'), ('prices', 'rt_prices'), ('lines', 'rt_lines'), ('system', 'rt_system'))
    return {kind: frame(result.get(name)) for kind, name in names}


def recorded_periods(tables):
    periods = set()
    for table in tables.values():
        if 'period' in table:
            values = pd.to_numeric(table.period, errors='coerce')
            periods.update(int(t) for t in values if np.isfinite(t) and t >= 0 and t == int(t))
    return sorted(periods)


def period_slice(table, period, identifier):
    if table.empty or 'period' not in table or identifier not in table:
        return pd.DataFrame()
    selected = table.loc[table.period == period].copy()
    if selected[identifier].duplicated().any():
        raise ValueError(f'所选时段的 {identifier} 记录重复，不能确定唯一运行值')
    return selected.set_index(identifier)


def result_snapshot(data, result, stage, period):
    """Only exact recorded periods. NaN means unknown, never inferred zero."""
    tables = stage_tables(result, stage)
    buses, resources, branches = (data[key].copy() for key in ('buses', 'resources', 'branches'))
    prices = period_slice(tables['prices'], period, 'bus')
    units = period_slice(tables['units'], period, 'unit')
    lines = period_slice(tables['lines'], period, 'line')
    for column in ('load_mw', 'shed_mw', 'lmp'):
        buses[column] = buses.bus.map(prices[column]).map(number) if column in prices else np.nan
    source = '本次保存的逐节点负荷需求（未扣失负荷）'
    if stage == 'da' and 'load_mw' not in prices and data.get('tie_line_mode', 'supply') == 'supply':
        system = tables['system']
        totals = system.loc[system.period == period, 'load_mw'] if {'period', 'load_mw'} <= set(system) else pd.Series(dtype=float)
        total = number(totals.iloc[0]) if len(totals) == 1 else np.nan
        base = pd.to_numeric(buses.base_load_mw, errors='coerce')
        # Same disaggregation as build_da_case, only when the whole frozen share is known.
        if base.notna().all() and (base >= 0).all() and base.sum() > 0 and np.isfinite(total) and total >= 0:
            buses['load_mw'] = total * base / base.sum()
        source = '本次保存的DA系统负荷 × 冻结节点基准负荷占比（模型分摊，未扣失负荷）'
    if stage == 'da' and 'load_mw' not in prices and data.get('tie_line_mode') == 'fixed_load':
        source = '固定节点负荷场景缺少逐节点记录，不能用全网占比反推（未知）'
    buses['load_source'] = source
    if 'bus' in units:
        recorded_bus = resources.unit.map(units.bus)
        if (recorded_bus.notna() & (recorded_bus != resources.bus)).any():
            raise ValueError('已保存机组接入节点与冻结配置冲突，不能合并展示')
        if not set(units.bus.dropna()) <= set(buses.bus):
            raise ValueError('已保存机组接入节点不在冻结网络中')
    resources['P_mw'] = resources.unit.map(units.P_mw).map(number) if 'P_mw' in units else np.nan
    # Include recorded resources absent from an incomplete archive, not fictitious defaults.
    if {'bus', 'type', 'P_mw'} <= set(units):
        extra = units.loc[~units.index.isin(resources.unit)].reset_index()
        if not extra.empty:
            extra = extra.assign(enabled=True, capacity_mw=np.nan)
            resources = pd.concat([resources, extra[resources.columns]], ignore_index=True)
    for column in ('flow_mw',):
        branches[column] = branches.branch_id.map(lines[column]).map(number) if column in lines else np.nan
    if 'rate_mw' in lines:
        # A saved but invalid rating is unknown; don't mask it with another source.
        present = branches.branch_id.isin(lines.index)
        branches.loc[present, 'rate_eff'] = branches.loc[present, 'branch_id'].map(pd.to_numeric(lines.rate_mw, errors='coerce'))
    elif stage == 'rt':
        da = frame(result.get('da_line_flows'))
        if {'line', 'rate_mw'} <= set(da):
            groups = da.groupby('line').rate_mw
            unique = groups.nunique(dropna=False)
            rates = groups.first().loc[unique == 1]
            present = branches.branch_id.isin(rates.index)
            branches.loc[present, 'rate_eff'] = branches.loc[present, 'branch_id'].map(rates)
    branches['rate_eff'] = branches.rate_eff.map(number).where(branches.rate_eff.map(number) > 0)
    branches['loading'] = branches.flow_mw.abs() / branches.rate_eff
    return {**data, 'buses': buses, 'resources': resources, 'branches': branches,
            'stage': stage, 'period': period, 'load_source': source}


@st.cache_data(show_spinner=False)
def layout(bus_ids, edges):
    """Deterministic electrical-connectivity layout, never geographic coordinates."""
    import networkx as nx
    graph = nx.Graph()
    graph.add_nodes_from(sorted(bus_ids))
    graph.add_edges_from(sorted(edges))
    positions = nx.kamada_kawai_layout(graph)
    return {int(bus): (float(xy[0]), float(xy[1])) for bus, xy in positions.items()}


def topology_figure(data, selected_bus=None, selected_branch=None):
    buses, branches, resources = (data[key] for key in ('buses', 'branches', 'resources'))
    pos = layout(tuple(buses.bus), tuple(zip(branches.from_bus, branches.to_bus)))
    fig = go.Figure()
    runtime = 'stage' in data
    shown = set()
    for r in branches.itertuples():
        x0, y0 = pos[r.from_bus]; x1, y1 = pos[r.to_bus]
        loading = number(getattr(r, 'loading', None))
        color = ('#a83e48' if loading >= 1 else '#d29333' if loading >= .8 else '#627f97') if np.isfinite(loading) else '#b8c4d0'
        selected = r.branch_id == selected_branch
        if selected and not runtime:
            color = '#176b79'
        kind = '变压器' if r.kind == 'trafo' else '输电线路'
        tip = (f'<b>{escape(str(r.branch_id))} · {kind}</b><br>参考方向 {r.from_bus} → {r.to_bus}'
               f'<br>电抗 {fmt(r.x_pu, " p.u.", 5)}<br>生效限额 {fmt(r.rate_eff, " MW")}')
        if runtime:
            tip += f'<br>潮流 {fmt(r.flow_mw, " MW")}<br>负载率 {fmt(loading * 100, "%")}'
        fig.add_scatter(x=[x0, (x0+x1)/2, x1], y=[y0, (y0+y1)/2, y1], mode='lines',
                        line=dict(color=color, width=4 if selected else 2,
                                  dash='dash' if r.kind == 'trafo' else 'solid'),
                        text=[tip]*3, hoverinfo='text', name=kind, legendgroup=kind, showlegend=kind not in shown)
        shown.add(kind)
        flow = number(getattr(r, 'flow_mw', None))
        if runtime and np.isfinite(flow) and abs(flow) > 1e-6:
            sign = 1 if flow > 0 else -1
            fig.add_annotation(x=(x0+x1)/2+sign*(x1-x0)*.05, y=(y0+y1)/2+sign*(y1-y0)*.05,
                               ax=(x0+x1)/2-sign*(x1-x0)*.05, ay=(y0+y1)/2-sign*(y1-y0)*.05,
                               xref='x', yref='y', axref='x', ayref='y', text='', showarrow=True,
                               arrowhead=2, arrowsize=1.2, arrowwidth=1.5, arrowcolor=color)
    active = resources[resources.enabled.astype(bool)]
    resource_buses = set(active.bus)
    hovers = []
    for bus in buses.itertuples():
        ids = active.loc[active.bus == bus.bus, 'unit'].tolist()
        resource_text = '、'.join(map(str, ids)) if ids else ('无启用资源' if data['resource_catalog_known'] else '资源档案未知')
        tip = f'<b>节点 {bus.bus}</b><br>接入：{escape(resource_text)}<br>基准负荷 {fmt(bus.base_load_mw, " MW")}'
        if getattr(bus, 'fixed_load_weight', 0) > 0:
            tip += f'<br>转换固定负荷分配权重 {bus.fixed_load_weight:.4%}'
        if runtime:
            tip += f'<br>本时段负荷需求 {fmt(bus.load_mw, " MW")}<br>节点电价 {fmt(bus.lmp, " 元/MWh")}'
            for resource in active[active.bus == bus.bus].itertuples():
                tip += f'<br>{escape(str(resource.unit))} 净出力 {fmt(resource.P_mw, " MW")}'
        hovers.append(tip)
    fig.add_scatter(x=[pos[b][0] for b in buses.bus], y=[pos[b][1] for b in buses.bus], mode='markers+text',
                    text=buses.bus.astype(str), textposition='middle center', textfont=dict(size=11, color='white'),
                    hovertext=hovers, hoverinfo='text', name='母线（编号）',
                    marker=dict(size=[31 if b == selected_bus else 25 for b in buses.bus],
                                color=['#176b79' if b in resource_buses else '#52718b' for b in buses.bus],
                                line=dict(width=[3 if b == selected_bus else 1 for b in buses.bus],
                                          color=['#e7ad41' if b == selected_bus else 'white' for b in buses.bus])))
    # One label per bus, all IDs stacked. Choose open space around each node
    # rather than placing every label in the same (often occupied) corner.
    used_labels = []
    for bus, group in active.groupby('bus'):
        if bus not in pos:
            continue
        bx, by = pos[bus]
        labels = [str(uid) for uid in group.unit]
        half_width = max(map(len, labels)) * .014 + .015
        half_height = .025 * len(labels)
        candidates = [(bx + .14*np.cos(angle), by + .14*np.sin(angle))
                      for angle in np.linspace(0, 2*np.pi, 16, endpoint=False)]
        def clearance(point):
            x, y = point
            distances = [np.hypot(max(abs(x-nx)-half_width, 0), max(abs(y-ny)-half_height, 0))
                         for nx, ny in list(pos.values()) + used_labels]
            return min(distances)
        x, y = max(candidates, key=clearance)
        used_labels.append((x, y))
        fig.add_scatter(x=[bx + (x-bx)*.30, x], y=[by + (y-by)*.30, y], mode='lines',
                        line=dict(color='#93b8bc', width=1, dash='dot'),
                        hoverinfo='skip', showlegend=False)
        fig.add_annotation(x=x, y=y, xanchor='center', yanchor='middle', showarrow=False,
                           text='<br>'.join(escape(uid) for uid in labels),
                           font=dict(size=11, color='#176b79'), bgcolor='rgba(255,255,255,0.9)')
    loads = buses[pd.to_numeric(buses.base_load_mw, errors='coerce') > 0]
    fig.add_scatter(x=[pos[b][0] for b in loads.bus], y=[pos[b][1]-.055 for b in loads.bus],
                    mode='markers', marker=dict(symbol='triangle-down', size=8, color='#d29333'),
                    name='基准有负荷节点', hoverinfo='skip')
    fig.update_layout(height=650, font=dict(family=FONT_FAMILY), margin=dict(l=15, r=90, t=10, b=25),
                      plot_bgcolor='white', paper_bgcolor='white', hovermode='closest',
                      xaxis=dict(visible=False, range=[-1.2, 1.35]),
                      yaxis=dict(visible=False, scaleanchor='x', scaleratio=1),
                      legend=dict(orientation='h', y=-.03, x=0), uirevision='network-layout')
    return fig


def resource_table(resources, runtime=False):
    out = pd.DataFrame({'机组 / 主体': resources.unit, '节点': resources.bus,
                        '类型': resources.type.map(lambda v: TYPE_CN.get(v, v)),
                        '参与运行': resources.enabled.map({True: '启用', False: '未启用'}),
                        '额定发电 / 放电上限（MW）': resources.capacity_mw.map(fmt)})
    if runtime:
        out['本时段净出力（MW）'] = [fmt(p) if enabled else '不参与运行' for p, enabled in zip(resources.P_mw, resources.enabled)]
    return out


def branch_table(branches, runtime=False):
    out = pd.DataFrame({'支路': branches.branch_id, '起点': branches.from_bus, '终点': branches.to_bus,
                        '类型': branches.kind.map({'line': '线路', 'trafo': '变压器'}),
                        '电抗（p.u.）': branches.x_pu.map(lambda v: fmt(v, digits=5)),
                        '原始限额（MW）': branches.rate_orig_mw.map(fmt),
                        '场景总系数': branches.scale.map(lambda v: fmt(v, digits=3)),
                        '生效限额（MW）': branches.rate_eff.map(fmt)})
    if runtime:
        out['潮流（MW）'] = branches.flow_mw.map(fmt)
        out['负载率'] = (branches.loading * 100).map(lambda v: fmt(v, '%', 1))
    return out


def render_explorer(data, prefix):
    buses, resources, branches = (data[key] for key in ('buses', 'resources', 'branches'))
    runtime = 'stage' in data
    st.caption('电气连接示意图，不是真实地理位置。绿色节点接有启用资源，橙色小三角表示基准有负荷；未启用机组只列在节点详情。可缩放、悬停，下方可按节点查表。')
    left, right = st.columns([1, 1.5])
    bus = left.selectbox('查看节点', buses.bus.astype(int).tolist(), key=prefix+':bus',
                         format_func=lambda b: f'节点 {b}')
    connected = branches[(branches.from_bus == bus) | (branches.to_bus == bus)]
    branch = right.selectbox('查看相连支路', connected.branch_id.tolist(), key=prefix+f':branch:{bus}')
    st.plotly_chart(topology_figure(data, bus, branch), width='stretch', key=prefix+':graph',
                    config={'displaylogo': False, 'scrollZoom': False})
    if runtime:
        st.caption('箭头为本时段潮流方向；橙色 ≥80%，红色 ≥100% 额定限额，灰色表示未知。负载率不等同于定价阻塞。')
    bus_row = buses[buses.bus == bus].iloc[0]
    cols = st.columns(3 if runtime else 2)
    cols[0].metric(f'节点 {bus} · 基准负荷', fmt(bus_row.base_load_mw, ' MW'))
    if runtime:
        cols[1].metric('本时段负荷需求', fmt(bus_row.load_mw, ' MW'))
        cols[2].metric('本时段节点电价', fmt(bus_row.lmp, ' 元/MWh'))
        st.caption(data['load_source'] + '。未知不是零；基准负荷仅用于节点分配，不是本时段值。')
        if np.isfinite(number(bus_row.shed_mw)) and abs(bus_row.shed_mw) > 1e-6:
            st.warning(f'该节点本时段有失负荷 {fmt(bus_row.shed_mw, " MW")}，负荷需求不代表全部供电。')
    else:
        total = pd.to_numeric(buses.base_load_mw, errors='coerce')
        share = bus_row.base_load_mw / total.sum() if total.notna().all() and total.sum() > 0 else np.nan
        cols[1].metric('节点负荷分配占比', fmt(share * 100, '%'))
        st.caption('基准负荷来自场景 buses.csv；运行时系统负荷按此占比分配，负荷缩放与逐时曲线另见下方。')
    st.markdown('**接入机组与资源**')
    local = resources[resources.bus == bus]
    if local.empty:
        st.info('该节点未配置机组或区外来电。' if data['resource_catalog_known'] else '缺少机组配置，接入资源未知。')
    else:
        st.dataframe(resource_table(local, runtime), hide_index=True, width='stretch')
    if runtime:
        st.caption('净出力为正表示向电网送电；储能充电/抽蓄抽水为负，区外来电单列为资源，不是本地机组。')
    st.markdown('**相连支路参数**')
    st.dataframe(branch_table(connected, runtime), hide_index=True, width='stretch')
    st.caption('潮流正方向为起点 → 终点；生效限额包含单支路与全网容量系数。运行结果优先使用该次保存的线路限额。')
    with st.expander('查看全网节点、机组与支路清单'):
        nodes = buses[['bus', 'base_load_mw']].rename(columns={'bus': '节点', 'base_load_mw': '基准负荷（MW）'})
        nodes['基准负荷（MW）'] = nodes['基准负荷（MW）'].map(fmt)
        if runtime:
            nodes['本时段负荷需求（MW）'] = buses.load_mw.map(fmt)
            nodes['本时段节点电价（元/MWh）'] = buses.lmp.map(fmt)
        st.dataframe(nodes, hide_index=True, width='stretch')
        st.dataframe(resource_table(resources, runtime), hide_index=True, width='stretch')
        st.dataframe(branch_table(branches, runtime), hide_index=True, width='stretch')


def render_input_network(cfg, prefix):
    from unit_input_view import scenario_unit_catalog
    try:
        data = network_data(cfg.root, base=cfg.base, catalog=scenario_unit_catalog(cfg))
        st.markdown('**电网拓扑与节点详情**')
        enabled = data['resources'].enabled.sum()
        counts = st.columns(3)
        counts[0].metric('节点数量', len(data['buses']))
        counts[1].metric('支路数量', len(data['branches']))
        counts[2].metric('启用资源', int(enabled))
        st.caption(f"{len(data['buses'])} 节点 · {sum(data['branches'].kind == 'line')} 线路 · "
                   f"{sum(data['branches'].kind == 'trafo')} 变压器 · {enabled} 个启用资源（含区外来电）")
        render_explorer(data, prefix)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        st.warning(f'拓扑暂不能读取：{exc}。未替换为默认网络。')


def render_result_network(result, prefix='result_network'):
    root = Path(result['snapshot_root']) if result.get('snapshot_root') else None
    if root is None or not (root / 'data/case39/buses.csv').is_file() or not (root / 'data/case39/branches.csv').is_file():
        st.info('本次结果缺少冻结网络，无法核实历史连接关系；不使用当前场景冒充该次拓扑。')
        return
    try:
        data = network_data(root)
        options = [stage for stage in ('da', 'rt') if recorded_periods(stage_tables(result, stage))]
        if not options:
            st.info('尚无保存的DA计划或RT已执行时段。')
            return
        left, right = st.columns([1, 2])
        stage = left.radio('拓扑阶段', options, format_func=STAGES.get, horizontal=True, key=prefix+':stage')
        periods = recorded_periods(stage_tables(result, stage))
        period = right.select_slider('拓扑时段', options=periods, value=periods[-1], key=prefix+':period:'+stage,
                                     format_func=lambda t: f'{t*15//60:02d}:{t*15%60:02d} · 第{t+1}段')
        snapshot = result_snapshot(data, result, stage, period)
        st.caption(f'{STAGES[stage]} · 仅使用本次冻结网络及保存的正式调度量；未来未执行时段不补齐。')
        missing = snapshot['resources'].enabled & snapshot['resources'].P_mw.isna()
        if missing.any() or snapshot['branches'].flow_mw.isna().any() or snapshot['buses'].load_mw.isna().any():
            st.info('所选时段有未保存的出力、负荷或潮流，详情中标为“未知”，不按零处理。')
        render_explorer(snapshot, prefix+':'+stage)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        st.warning(f'本次拓扑数据暂不能读取：{exc}。未使用当前场景补齐。')
