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
    # Branch kind is optional in the model CSV contract, never infer equipment type.
    if 'kind' not in branches:
        branches['kind'] = 'unknown'
    else:
        branches['kind'] = branches['kind'].fillna('unknown')
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
    for column in ('load_mw', 'shed_mw'):
        buses[column] = buses.bus.map(prices[column]).map(number) if column in prices else np.nan
    # Saved CSV contract: lmp is clipped settlement, lmp_raw is raw.
    # Older/internal tables may instead explicitly store lmp_clamped + raw lmp.
    # A lone legacy lmp without provenance has unknown price meaning.
    explicit = 'lmp_clamped' in prices
    exported = ('lmp_raw' in prices or ('settlement_price_source' in prices and
                prices.settlement_price_source.astype(str).str.contains('clipped').all()))
    raw = 'lmp_raw' if 'lmp_raw' in prices else ('lmp' if explicit else None)
    settled = 'lmp_clamped' if explicit else ('lmp' if exported else None)
    buses['lmp'] = buses.bus.map(prices[raw]).map(number) if raw in prices else np.nan
    buses['lmp_clamped'] = buses.bus.map(prices[settled]).map(number) if settled in prices else np.nan
    buses['legacy_lmp'] = (buses.bus.map(prices.lmp).map(number)
                           if not exported and not explicit and 'lmp' in prices else np.nan)
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
    branches['mu'] = (branches.branch_id.map(lines.mu_plus.abs() + lines.mu_minus.abs()).map(number)
                      if {'mu_plus', 'mu_minus'} <= set(lines) else np.nan)
    return {**data, 'buses': buses, 'resources': resources, 'branches': branches,
            'stage': stage, 'period': period, 'load_source': source}


@st.cache_data(show_spinner=False)
def layout(bus_ids, edges):
    """Topology-only coordinates; axes span the available canvas, never geography."""
    if set(bus_ids) == set(range(1, 40)):
        # Electrical schematic, arranged for the IEEE39 backbone and generator spurs.
        points = {1:(0,4), 2:(1,4), 3:(2,4), 4:(2,3), 5:(1,2.5), 6:(2,2),
                  7:(1,1.5), 8:(0,2), 9:(-1,2.5), 10:(3,2), 11:(2.5,2.6),
                  12:(3.5,2.7), 13:(3.5,3.3), 14:(3,3.7), 15:(4.4,4),
                  16:(5.3,4), 17:(4.7,5), 18:(3.3,5), 19:(5.3,2.5),
                  20:(6.3,2.5), 21:(6.4,4), 22:(7.4,4), 23:(7.4,5),
                  24:(6.3,5), 25:(1,5.5), 26:(2.5,6.5), 27:(4,6),
                  28:(3.5,7.5), 29:(5,7.5), 30:(1,3.3), 31:(2,.7),
                  32:(3.8,1), 33:(5,1), 34:(6.7,1), 35:(8.6,3.5),
                  36:(8.6,5.4), 37:(0,6.5), 38:(6.5,7.5), 39:(-1,4)}
    else:
        import networkx as nx
        graph = nx.Graph()
        graph.add_nodes_from(sorted(bus_ids)); graph.add_edges_from(sorted(edges))
        points = nx.kamada_kawai_layout(graph)
    values = np.array([points[b] for b in bus_ids], dtype=float)
    span = np.ptp(values, axis=0)
    scaled = (values - values.min(axis=0)) / np.where(span > 0, span, 1) * 2 - 1
    return {int(bus): tuple(map(float, xy)) for bus, xy in zip(bus_ids, scaled)}


LAYER_NAMES = {'loading': '线路负载率（%）', 'settlement': '结算节点价（限价后，元/MWh）',
               'raw_price': '原始节点边际价（元/MWh）', 'power': '设备净出力（MW）'}
TYPE_SHORT = {'coal':'煤', 'gas_ccgt':'燃机', 'gas_gt':'燃轮', 'hydro':'水', 'wind':'风',
              'pv':'光', 'storage':'储', 'pumped':'抽蓄', 'tie':'区外来电'}


def _numeric_marker(values, title, *, diverging=False):
    finite = pd.to_numeric(values, errors='coerce')
    finite = finite[np.isfinite(finite)]
    lo, hi = ((float(finite.min()), float(finite.max())) if len(finite) else (0., 1.))
    if diverging:
        hi = max(abs(lo), abs(hi), 1.); lo = -hi
    elif lo == hi:
        lo -= 1.; hi += 1.
    return dict(color=values, colorscale='RdBu_r' if diverging else 'Viridis', cmin=lo, cmax=hi,
                showscale=True, colorbar=dict(title=title, thickness=12, len=.45, y=.7))



def contrast_text_color(rgb):
    """Black/white choice maximizes WCAG contrast for the actual plot RGB."""
    channels = [float(c.strip()) / 255 for c in rgb.removeprefix('rgb(').removesuffix(')').split(',')]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c*w for c,w in zip(linear, (.2126,.7152,.0722)))
    return '#000000' if (luminance+.05)/.05 >= 1.05/(luminance+.05) else '#ffffff'


def price_text_colors(values, low, high):
    from plotly.colors import sample_colorscale
    colors=[]
    for value in values:
        if not np.isfinite(value):
            colors.append('#000000')  # Unknown grey node.
        else:
            position=float(np.clip((value-low)/(high-low),0,1))
            colors.append(contrast_text_color(sample_colorscale('Viridis',[position])[0]))
    return colors

def topology_figure(data, selected_bus=None, selected_branch=None, layer='loading'):
    from common import TYPE_COLOR, TYPE_SYMBOL
    buses, branches, resources = (data[key] for key in ('buses', 'branches', 'resources'))
    pos = layout(tuple(buses.bus), tuple(zip(branches.from_bus, branches.to_bus)))
    fig = go.Figure()
    runtime = 'stage' in data
    timed_input = 'input_stage' in data
    shown = set()
    for r in branches.itertuples():
        x0, y0 = pos[r.from_bus]; x1, y1 = pos[r.to_bus]
        loading = number(getattr(r, 'loading', None))
        color = (('#b52f3c' if loading >= 1 else '#c47b13' if loading >= .8 else '#647c94')
                 if np.isfinite(loading) else '#acb6c2') if runtime and layer == 'loading' else '#78899b'
        selected = r.branch_id == selected_branch
        if selected:
            color = '#006b70' if not runtime or layer != 'loading' else color
        kind = {'trafo': '变压器（虚线）', 'line': '输电线路（实线）'}.get(r.kind, '支路（类型未记录，点线）')
        tip = (f'<b>{escape(str(r.branch_id))} · {kind}</b><br>参考方向 {r.from_bus} → {r.to_bus}'
               f'<br>电抗 {fmt(r.x_pu, " p.u.", 5)}<br>生效限额 {fmt(r.rate_eff, " MW")}')
        if runtime:
            tip += (f'<br>潮流 {fmt(r.flow_mw, " MW")}<br>负载率 {fmt(loading * 100, "%")}'
                    f'<br>约束影子价 {fmt(getattr(r, "mu", np.nan), " 元/MWh")}')
        fig.add_scatter(x=[x0, (x0+x1)/2, x1], y=[y0, (y0+y1)/2, y1], mode='lines',
                        line=dict(color=color, width=4 if selected else 2.2,
                                  dash={'trafo': 'dash', 'line': 'solid'}.get(r.kind, 'dot')),
                        text=[tip]*3, hoverinfo='text', name=kind, legendgroup=kind,
                        showlegend=kind not in shown)
        shown.add(kind)
        flow = number(getattr(r, 'flow_mw', None))
        if runtime and np.isfinite(flow) and abs(flow) > 1e-6:
            sign = 1 if flow > 0 else -1
            fig.add_annotation(x=(x0+x1)/2+sign*(x1-x0)*.06, y=(y0+y1)/2+sign*(y1-y0)*.06,
                               ax=(x0+x1)/2-sign*(x1-x0)*.06, ay=(y0+y1)/2-sign*(y1-y0)*.06,
                               xref='x', yref='y', axref='x', ayref='y', text='', showarrow=True,
                               arrowhead=2, arrowsize=1.1, arrowwidth=1.4, arrowcolor=color)
    hovers = []
    for bus in buses.itertuples():
        local = resources[resources.bus == bus.bus]
        tip = f'<b>节点 {bus.bus}</b><br>基准分配负荷 {fmt(bus.base_load_mw, " MW")}'
        for resource in local.itertuples():
            tip += (f'<br>{escape(str(resource.unit))} · {TYPE_CN.get(resource.type, resource.type)} · '
                    + ('停用' if not resource.enabled else f'额定 {fmt(resource.capacity_mw, " MW")}'))
        if runtime or timed_input:
            tip += f'<br>本时段负荷需求 {fmt(bus.load_mw, " MW")}'
        if runtime:
            tip += (f'<br>原始节点边际价 {fmt(bus.lmp, " 元/MWh")}'
                    f'<br>结算节点价（限价后） {fmt(bus.lmp_clamped, " 元/MWh")}')
        hovers.append(tip)
    marker = dict(size=[32 if b == selected_bus else 25 for b in buses.bus],
                  color='#36566e', line=dict(width=[3 if b == selected_bus else 1 for b in buses.bus],
                  color=['#edab38' if b == selected_bus else 'white' for b in buses.bus]))
    text_colors = 'white'
    if runtime and layer in ('settlement', 'raw_price'):
        values = buses.lmp_clamped if layer == 'settlement' else buses.lmp
        # Unknown prices have an explicit grey base marker and no invented number.
        marker['color'] = '#aab4bf'
        present = np.isfinite(values)
        text_colors = ['#000000'] * len(values)
        if present.any():
            price_marker = _numeric_marker(values[present], '结算价<br>元/MWh' if layer == 'settlement' else '原始价<br>元/MWh')
            text_colors = price_text_colors(values, price_marker['cmin'], price_marker['cmax'])
            fig.add_scatter(x=[pos[b][0] for b in buses.bus[present]], y=[pos[b][1] for b in buses.bus[present]],
                            mode='markers', marker={**marker, 'size':29, 'line':dict(
                                width=[3 if b==selected_bus else 1 for b in buses.bus[present]],
                                color=['#edab38' if b==selected_bus else 'white' for b in buses.bus[present]]),
                            **price_marker},
                            hoverinfo='skip', showlegend=False)
            marker['opacity'] = [0 if known else 1 for known in present]
    fig.add_scatter(x=[pos[b][0] for b in buses.bus], y=[pos[b][1] for b in buses.bus], mode='markers+text',
                    text=buses.bus.astype(str), textposition='middle center', textfont=dict(size=11, color=text_colors),
                    hovertext=hovers, hoverinfo='text', name='母线编号', marker=marker)
    # Compact type-specific badges. Same-bus devices occupy one stacked card;
    # very long groups collapse to a count and remain complete in node details.
    used_boxes = []
    resource_pos = {}
    edge_points = [((1-t)*pos[a][0]+t*pos[b][0], (1-t)*pos[a][1]+t*pos[b][1])
                   for a,b in zip(branches.from_bus,branches.to_bus) for t in np.linspace(0,1,20)]
    for bus, group in resources.groupby('bus', sort=True):
        bx, by = pos[bus]
        lines = []
        for r in group.head(3).itertuples():
            label = f'{TYPE_SHORT.get(r.type, r.type)} {escape(str(r.unit))}'
            if not r.enabled:
                label += '（停用）'
            elif runtime and layer == 'power':
                label += f' {fmt(r.P_mw, " MW", 1)}'
            lines.append(label)
        if len(group) > 3:
            lines.append(f'+{len(group)-3} 个资源，见节点详情')
        hw = min(.26, .015 * max(map(len, lines)))
        hh = .032 * len(lines)
        candidates = [(bx+radius*np.cos(angle),by+radius*np.sin(angle))
                      for radius in (.16,.24,.31) for angle in np.linspace(0,2*np.pi,16,endpoint=False)]
        def clearance(point):
            x,y=point
            penalty = max(abs(x)+hw-1.18, 0) + max(abs(y)+hh-1.17, 0)
            distance = min(np.hypot(max(abs(x-nx)-hw,0), max(abs(y-ny)-hh,0)) for nx,ny in list(pos.values())+used_boxes)
            line_overlap = any(abs(x-ex)<hw+.015 and abs(y-ey)<hh+.012 for ex,ey in edge_points)
            return distance - penalty*4 - .12*line_overlap
        x,y=max(candidates,key=clearance); used_boxes.append((x,y))
        for index, r in enumerate(group.head(3).itertuples()):
            resource_pos[r.unit] = (x-hw-.02, y+(len(lines)-1)*.028-index*.056)
        fig.add_scatter(x=[bx,x], y=[by,y], mode='lines', line=dict(color='#b2bbc3',width=1,dash='dot'),
                        hoverinfo='skip', showlegend=False)
        fig.add_annotation(x=x,y=y,xanchor='center',yanchor='middle',showarrow=False,
                           text='<br>'.join(lines), font=dict(size=11,color='#354d60' if group.enabled.any() else '#8b939c'),
                           bgcolor='rgba(255,255,255,.94)',borderpad=2)
    for typ, group in resources.groupby('type', sort=False):
        group = group[group.unit.isin(resource_pos)]
        color = TYPE_COLOR.get(typ, '#6c8091')
        fig.add_scatter(x=[resource_pos[u][0] for u in group.unit], y=[resource_pos[u][1] for u in group.unit],
                        mode='markers', marker=dict(symbol=TYPE_SYMBOL.get(typ,'circle'),size=10,
                        color=[color if r.enabled and not (runtime and layer=='power' and not np.isfinite(r.P_mw)) else '#b2b7bd'
                               for r in group.itertuples()], line=dict(color='white',width=1)),
                        name=TYPE_CN.get(typ,typ), legendgroup='resource:'+typ,
                        text=[f'{escape(str(r.unit))} · {TYPE_CN.get(r.type,r.type)}<br>' +
                              ('停用' if not r.enabled else f'额定 {fmt(r.capacity_mw," MW")}') +
                              (f'<br>净出力 {fmt(r.P_mw," MW")}' if runtime and r.enabled else '') for r in group.itertuples()],
                        hoverinfo='text')
    if runtime and layer == 'power':
        known = resources.enabled & np.isfinite(resources.P_mw) & resources.unit.isin(resource_pos)
        if known.any():
            fig.add_scatter(x=[resource_pos[u][0] for u in resources.unit[known]],
                            y=[resource_pos[u][1] for u in resources.unit[known]], mode='markers',
                            marker=dict(size=13, **_numeric_marker(resources.P_mw[known], '净出力<br>MW', diverging=True)),
                            hoverinfo='skip', showlegend=False)
    loads = buses[pd.to_numeric(buses.base_load_mw, errors='coerce') > 0]
    if runtime or timed_input:
        loads = buses[(pd.to_numeric(buses.load_mw, errors='coerce') > 0) | (pd.to_numeric(buses.base_load_mw, errors='coerce') > 0)]
    fig.add_scatter(x=[pos[b][0] for b in loads.bus], y=[pos[b][1]-.055 for b in loads.bus],
                    mode='markers', marker=dict(symbol='triangle-down', size=10, color='#ba6715'),
                    name='负荷接入点', text=[f'节点 {r.bus} · '+
                    (f'本时段负荷 {fmt(r.load_mw," MW")}' if runtime or timed_input else f'基准负荷 {fmt(r.base_load_mw," MW")}')
                    for r in loads.itertuples()], hoverinfo='text')
    fig.update_layout(height=720, font=dict(family=FONT_FAMILY), margin=dict(l=12,r=75 if runtime and layer!='loading' else 12,t=15,b=115,autoexpand=False),
                      plot_bgcolor='#fafcfe', paper_bgcolor='white', hovermode='closest',
                      xaxis=dict(visible=False, domain=[0,1], range=[-1.25,1.25], fixedrange=False, automargin=False),
                      yaxis=dict(visible=False, domain=[0,1], range=[-1.24,1.24], fixedrange=False, automargin=False),
                      legend=dict(orientation='h', y=-.05,x=0,xanchor='left',yanchor='top',font=dict(size=11)),
                      uirevision='network:'+str(tuple(buses.bus))+str(tuple(branches.branch_id)))
    return fig


def input_snapshot(cfg, data, stage, period):
    """Saved scenario profiles, exactly the model's nodal demand disaggregation."""
    from smm.forecast import generate_profiles, apply_profile_overrides
    from smm.fixed_load import nodal_demand
    from smm.network import build_network
    if stage not in ('actual','da','id','rt') or period not in range(cfg.T):
        raise ValueError('输入曲线阶段或时段无效')
    profiles=apply_profile_overrides(cfg,generate_profiles(cfg))
    demand,_=nodal_demand(cfg,build_network(cfg),profiles,profiles['load_'+stage],stage=stage)
    buses=data['buses'].copy()
    buses['load_mw']=[float(demand[b][period]) if b in demand else 0. for b in buses.bus]
    return {**data,'buses':buses,'input_stage':stage,'period':period}


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
                        '类型': branches.kind.map({'line': '线路', 'trafo': '变压器'}).fillna('类型未记录'),
                        '电抗（p.u.）': branches.x_pu.map(lambda v: fmt(v, digits=5)),
                        '原始限额（MW）': branches.rate_orig_mw.map(fmt),
                        '场景总系数': branches.scale.map(lambda v: fmt(v, digits=3)),
                        '生效限额（MW）': branches.rate_eff.map(fmt)})
    if runtime:
        out['潮流（MW）'] = branches.flow_mw.map(fmt)
        out['负载率'] = (branches.loading * 100).map(lambda v: fmt(v, '%', 1))
        out['约束影子价（元/MWh）'] = branches.mu.map(fmt)
    return out


def render_explorer(data, prefix):
    buses, resources, branches = (data[key] for key in ('buses', 'resources', 'branches'))
    runtime = 'stage' in data
    st.caption('电气接线示意，不是地理位置。母线保留编号，资源按类型标记，倒三角是负荷接入点；停用设备为灰色。同节点资源清单见图下详情。')
    layer = (st.radio('结果图层', list(LAYER_NAMES), format_func=LAYER_NAMES.get, horizontal=True, key=prefix+':layer') if runtime else 'loading')
    left, right = st.columns([1, 1.5])
    bus_options = buses.bus.astype(int).tolist()
    if prefix+':bus' in st.session_state and st.session_state[prefix+':bus'] not in bus_options:
        del st.session_state[prefix+':bus']
        st.caption('原节点已不存在，已切回有效节点。')
    bus = left.selectbox('查看节点', bus_options, key=prefix+':bus',
                         format_func=lambda b: f'节点 {b}')
    connected = branches[(branches.from_bus == bus) | (branches.to_bus == bus)]
    branch_key = prefix+f':branch:{bus}'
    if branch_key in st.session_state and st.session_state[branch_key] not in connected.branch_id.tolist():
        del st.session_state[branch_key]
    branch = right.selectbox('查看相连支路', connected.branch_id.tolist(), key=branch_key) if len(connected) else None
    st.plotly_chart(topology_figure(data, bus, branch, layer), width='stretch', key=prefix+':graph',
                    config={'displaylogo': False, 'scrollZoom': False})
    if runtime:
        if layer == 'loading':
            st.caption('箭头为本时段潮流方向；线路橙色 ≥80%，红色 ≥100% 额定限额，灰色线路表示未知。负载率不等同于定价阻塞，约束影子价见支路详情。')
        elif layer == 'power':
            st.caption('资源颜色为净出力（MW），负值表示充电 / 抽水；灰色为未知或停用。箭头仍为潮流方向，线路颜色不编码负载率。')
        else:
            st.caption('节点颜色为所选价格（元/MWh），灰色节点表示该价格未知。箭头仍为潮流方向，线路颜色不编码负载率。')
    bus_row = buses[buses.bus == bus].iloc[0]
    cols = st.columns(4 if runtime else 2)
    cols[0].metric(f'节点 {bus} · 基准负荷', fmt(bus_row.base_load_mw, ' MW'))
    if runtime:
        cols[1].metric('本时段负荷需求', fmt(bus_row.load_mw, ' MW'))
        cols[2].metric('结算节点价（限价后）', fmt(bus_row.lmp_clamped, ' 元/MWh'))
        cols[3].metric('原始节点边际价', fmt(bus_row.lmp, ' 元/MWh'))
        st.caption('原始价与限价后结算价分别读取该次保存字段，不用当前限价推算。旧档单列价格若缺来源，口径保留未知。')
        if np.isfinite(bus_row.legacy_lmp):
            st.info(f'旧档保存节点价：{fmt(bus_row.legacy_lmp, " 元/MWh")}，未记录原始 / 结算口径。')
        st.caption(data['load_source'] + '。未知不是零；基准负荷仅用于节点分配，不是本时段值。')
        if np.isfinite(number(bus_row.shed_mw)) and abs(bus_row.shed_mw) > 1e-6:
            st.warning(f'该节点本时段有失负荷 {fmt(bus_row.shed_mw, " MW")}，负荷需求不代表全部供电。')
    else:
        total = pd.to_numeric(buses.base_load_mw, errors='coerce')
        share = bus_row.base_load_mw / total.sum() if total.notna().all() and total.sum() > 0 else np.nan
        cols[1].metric('节点负荷分配占比', fmt(share * 100, '%'))
        if 'input_stage' in data:
            st.metric('所选输入时段 · 节点负荷需求', fmt(bus_row.load_mw, ' MW'))
        st.caption('基准负荷来自已保存 buses.csv，仅用于分配；装机不是出力，时段输入不是出清结果。')
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
            nodes['结算节点价（限价后，元/MWh）'] = buses.lmp_clamped.map(fmt)
            nodes['原始节点边际价（元/MWh）'] = buses.lmp.map(fmt)
        st.dataframe(nodes, hide_index=True, width='stretch')
        st.dataframe(resource_table(resources, runtime), hide_index=True, width='stretch')
        st.dataframe(branch_table(branches, runtime), hide_index=True, width='stretch')


def render_input_network(cfg, prefix):
    from unit_input_view import scenario_unit_catalog
    try:
        data = network_data(cfg.root, base=cfg.base, catalog=scenario_unit_catalog(cfg))
        st.markdown('**系统接线图 · 已保存输入**')
        st.caption('图只读已保存参数。编辑框中的未保存内容不进入图，也不改写历史结果。')
        if 'unit_editor:' + Path(cfg.root).name in st.session_state:
            st.info('机组编辑草稿尚未提交：本图保持已保存输入；保存成功后同步更新。')
        view = st.radio('输入图显示', ['结构与容量', '时段负荷输入'], horizontal=True, key=prefix+':input_view')
        if view == '时段负荷输入':
            from smm.time_grid import period_label
            left, right = st.columns([1,2])
            stage = left.selectbox('输入曲线口径', ['actual','da','id','rt'], key=prefix+':input_stage',
                                   format_func={'actual':'实际（模拟输入）','da':'日前预测','id':'日内预测基准','rt':'实时预测基准'}.get)
            period = right.select_slider('输入时段', options=list(range(cfg.T)), key=prefix+':input_period', format_func=lambda t:period_label(t,cfg.dt))
            data = input_snapshot(cfg,data,stage,period)
            st.caption('日内 / 实时显示已保存场景生成的预测基准曲线，不是滚动窗口更新后的前瞻，也不是已执行结果。')
        enabled = data['resources'].enabled.sum()
        counts = st.columns(3)
        counts[0].metric('节点数量', len(data['buses']))
        counts[1].metric('支路数量', len(data['branches']))
        counts[2].metric('启用资源', int(enabled))
        st.caption(f"{len(data['buses'])} 节点 · {sum(data['branches'].kind == 'line')} 线路 · "
                   f"{sum(data['branches'].kind == 'trafo')} 变压器 · {enabled} 个启用资源（含区外来电）")
        unknown_kind = int((~data['branches'].kind.isin(['line', 'trafo'])).sum())
        if unknown_kind:
            st.caption(f'{unknown_kind} 条支路未记录设备类型，图中以点线表示。')
        render_explorer(data, prefix)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        st.warning(f'拓扑暂不能读取：{exc}。未替换为默认网络。')


def frozen_network_usable(root):
    """New CLI evidence can explicitly disprove source-CSV/actual-case identity."""
    import json
    path = Path(root) / 'provenance.json'
    if not path.exists():
        return True  # Web freezes use a different manifest; keep legacy behavior.
    evidence = json.loads(path.read_text(encoding='utf-8'))
    return ('frozen_network_verified' not in evidence or evidence['frozen_network_verified'] is True)


def render_result_network(result, prefix='result_network', stages=None):
    """stages 限定可选阶段（第二轮J项：日前块只看 da，实时块只看 rt）。"""
    from smm.time_grid import period_label, result_time_grid
    dt = result_time_grid(result)[1]
    root = Path(result['snapshot_root']) if result.get('snapshot_root') else None
    if root is None or not (root / 'data/case39/buses.csv').is_file() or not (root / 'data/case39/branches.csv').is_file():
        st.info('本次结果缺少冻结网络，无法核实历史连接关系；不使用当前场景冒充该次拓扑。')
        return
    try:
        if not frozen_network_usable(root):
            st.warning('本次CLI有效网络无法由冻结配置和源网络重建，冻结网络证据不足；不绘制可能错误的历史连接关系。已保存数值表仍保留。')
            return
        data = network_data(root)
        options = [stage for stage in (stages or ('da', 'rt')) if recorded_periods(stage_tables(result, stage))]
        if not options:
            st.info('尚无保存的DA计划或RT已执行时段。')
            return
        left, right = st.columns([1, 2])
        stage = left.radio('拓扑阶段', options, format_func=STAGES.get, horizontal=True, key=prefix+':stage')
        periods = recorded_periods(stage_tables(result, stage))
        period = right.select_slider('拓扑时段', options=periods, value=periods[-1], key=prefix+':period:'+stage,
                                     format_func=lambda t: f'{period_label(t, dt)} · 第{t+1}段')
        snapshot = result_snapshot(data, result, stage, period)
        st.caption(f"运行 {Path(result.get('dir') or root.parent).name} · {STAGES[stage]} · {period_label(period, dt)} · 第{period+1}段 / 已保存{len(periods)}段；仅使用冻结网络和正式保存量，未来未执行时段不补齐。")
        missing = snapshot['resources'].enabled & snapshot['resources'].P_mw.isna()
        if missing.any() or snapshot['branches'].flow_mw.isna().any() or snapshot['buses'].load_mw.isna().any():
            st.info('所选时段有未保存的出力、负荷或潮流，详情中标为“未知”，不按零处理。')
        render_explorer(snapshot, prefix+':'+stage)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        st.warning(f'本次拓扑数据暂不能读取：{exc}。未使用当前场景补齐。')
