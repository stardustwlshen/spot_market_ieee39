"""第二轮J项「最终结果」：日前与实时的直接对比。

每张对比图/表同时给出 日前值、实时值 和 差值（实时−日前）；偏差最大的时段与主体排序列出。
只读本次运行已保存的结果；实时为部分执行时，日前也只取同一批已执行时段，不外推。
本文件的计算函数不依赖 Streamlit，便于测试；render_* 负责画图。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DA, RT, DIFF = '日前', '实时', '实时−日前'


def _dt(R) -> float:
    n = (R.get('summary') or {}).get('expected_periods') or 96
    return 24.0 / float(n)


def executed_periods(R) -> list[int]:
    rt = R.get('rt_prices')
    return sorted(int(t) for t in rt.period.unique()) if rt is not None and len(rt) else []


def _period_count(value):
    """Read scope counts without assuming missing metadata means a full day."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not np.isfinite(number) or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _execution_scope(R):
    periods = executed_periods(R)
    summary = R.get('summary') or {}
    expected = _period_count(summary.get('expected_periods'))
    executed = _period_count(summary.get('executed_periods'))
    prefix = bool(periods) and periods == list(range(periods[-1] + 1))
    full_day = (summary.get('completed_scope') == 'full_day' and expected is not None
                and executed == expected and periods == list(range(expected)))
    return dict(periods=periods, expected=expected, full_day=full_day, prefix=prefix)


def _state_end_label(scope):
    if scope.get('full_day'):
        return '日末'
    periods = scope.get('periods', [])
    if not periods:
        return '已执行末（时段未记录）'
    endpoint = f'第{periods[-1] + 1}段末'
    expected = scope.get('expected')
    if expected and 0 <= periods[-1] < expected:
        minutes = 24 * 60 * (periods[-1] + 1) / expected
        if float(minutes).is_integer():
            endpoint += f'，{int(minutes) // 60:02d}:{int(minutes) % 60:02d}'
    label = '已执行前缀末' if scope.get('prefix') else '已执行末'
    return f'{label}（{endpoint}）'


def _triple(da, rt, name=None):
    """Align two Series on the same index and return DA / RT / RT−DA columns."""
    out = pd.concat([da.rename(DA), rt.rename(RT)], axis=1)
    out[DIFF] = out[RT] - out[DA]
    if name:
        out.index.name = name
    return out


def rank_abs(frame, column=DIFF, n=10):
    """Largest |RT−DA| rows first (missing differences are kept out, not treated as zero)."""
    f = frame.dropna(subset=[column])
    return f.reindex(f[column].abs().sort_values(ascending=False).index).head(n)


# ---------------------------------------------------------------- 电价
def system_price_compare(R, buses=None):
    from common import load_weighted
    periods = executed_periods(R)
    da = load_weighted(R['da_nodal_prices'], buses)
    rt = load_weighted(R['rt_prices'], buses)
    return _triple(da.reindex(periods), rt.reindex(periods), 'period')


def node_price_compare(R):
    periods = executed_periods(R)
    da = R['da_nodal_prices'].pivot(index='period', columns='bus', values='lmp').reindex(periods)
    rt = R['rt_prices'].pivot(index='period', columns='bus', values='lmp').reindex(periods)
    long = pd.concat([da.stack().rename(DA), rt.stack().rename(RT)], axis=1)
    long[DIFF] = long[RT] - long[DA]
    long.index.names = ['period', 'bus']
    return long.reset_index()


def node_spread_ranking(node_long, n=10):
    g = node_long.groupby('bus')
    out = pd.DataFrame({'日前均价 元/MWh': g[DA].mean(), '实时均价 元/MWh': g[RT].mean(),
                        '价差均值 元/MWh': g[DIFF].mean(), '|价差|均值 元/MWh': g[DIFF].apply(lambda s: s.abs().mean()),
                        '|价差|最大 元/MWh': g[DIFF].apply(lambda s: s.abs().max())})
    return out.sort_values('|价差|均值 元/MWh', ascending=False).head(n).reset_index().rename(columns={'bus': '节点'})


# ---------------------------------------------------------------- 电量
def entity_energy_compare(R):
    """Per-entity energy (MWh) over the executed periods: DA award vs RT executed."""
    dt, periods = _dt(R), executed_periods(R)
    da = R['da_unit_awards']; rt = R['rt_units']
    da = da[da.period.isin(periods)]
    e_da = da.groupby('unit').P_mw.sum() * dt
    e_rt = rt.groupby('unit').P_mw.sum() * dt
    out = _triple(e_da, e_rt.reindex(e_da.index), 'unit')
    out.insert(0, '类型', da.groupby('unit').type.first())
    out['|偏差| MWh'] = out[DIFF].abs()
    out['逐时段|偏差|合计 MWh'] = period_entity_deviation(R).groupby('unit')['|偏差| MW'].sum() * dt
    return out.sort_values('逐时段|偏差|合计 MWh', ascending=False)


def period_entity_deviation(R):
    periods = executed_periods(R)
    da = R['da_unit_awards'][['period', 'unit', 'type', 'P_mw']]
    rt = R['rt_units'][['period', 'unit', 'P_mw']]
    m = da[da.period.isin(periods)].merge(rt, on=['period', 'unit'], how='left', suffixes=('_da', '_rt'))
    m = m.rename(columns={'P_mw_da': '日前 MW', 'P_mw_rt': '实时 MW'})
    m['偏差 MW'] = m['实时 MW'] - m['日前 MW']
    m['|偏差| MW'] = m['偏差 MW'].abs()
    return m


# ---------------------------------------------------------------- SOC / 库容
STATE_SPECS = [('da_storage', 'rt_storage', 'unit', 'E_mwh', '储能电量', 'MWh'),
               ('da_pumped', 'rt_pumped', 'unit', 'V_up', '抽蓄上库库容', '万m³'),
               ('da_hydro', 'rt_hydro', 'station', 'V_1e4m3', '水电库容', '万m³')]


def state_compare(R):
    """Long table period×entity: DA / RT / RT−DA end-of-period state."""
    periods, rows = executed_periods(R), []
    for dk, rk, idc, col, label, unit in STATE_SPECS:
        d, r = R.get(dk), R.get(rk)
        if d is None or r is None or not len(d) or not len(r) or col not in d or col not in r:
            continue
        m = d[d.period.isin(periods)][['period', idc, col]].merge(r[['period', idc, col]], on=['period', idc], how='left')
        m.columns = ['period', 'entity', DA, RT]
        m[DIFF] = m[RT] - m[DA]
        m['kind'], m['单位'] = label, unit
        rows.append(m)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=['period', 'entity', DA, RT, DIFF, 'kind', '单位'])
    out.attrs['execution_scope'] = _execution_scope(R)
    return out


def state_ranking(states):
    if states.empty:
        return pd.DataFrame()
    g = states.assign(a=states[DIFF].abs()).groupby(['kind', 'entity', '单位'])
    out = g.agg(最大偏差=('a', 'max'), 平均偏差=('a', 'mean')).reset_index()
    # All-missing comparisons remain unknown, including the maximum's period.
    idx = g.a.agg(lambda values: values.idxmax() if values.notna().any() else np.nan)
    out['最大偏差时段'] = [states.loc[i, 'period'] if pd.notna(i) else np.nan for i in idx]
    scope = states.attrs.get('execution_scope', {'periods': sorted(states.period.unique())})
    label = _state_end_label(scope)
    endpoint = max(scope.get('periods', []), default=None)
    end = states[states.period == endpoint].set_index(['kind', 'entity'])
    keys = pd.MultiIndex.from_frame(out[['kind', 'entity']])
    # Never substitute an earlier recorded state for a missing endpoint.
    out[label + ' 日前'] = end[DA].reindex(keys).to_numpy()
    out[label + ' 实时'] = end[RT].reindex(keys).to_numpy()
    out[label + ' 实时−日前'] = out[label + ' 实时'] - out[label + ' 日前']
    return out.sort_values('最大偏差', ascending=False)


# ---------------------------------------------------------------- 价值曲线
def value_compare(R):
    """DA value curve at τ vs value actually used at each RT window end τ."""
    da, rt = R.get('da_value_curves'), R.get('rt_value_windows')
    if da is None or rt is None or not len(da) or not len(rt):
        return pd.DataFrame()
    m = rt.merge(da[['kind', 'unit', 'period', 'value_yuan_per_mwh']].rename(columns={'period': 'tau', 'value_yuan_per_mwh': 'da_v'}),
                 on=['kind', 'unit', 'tau'], how='left')
    out = pd.DataFrame({'窗口起点': m.window_start, '窗口末τ': m.tau, '类别': m.kind_label, '主体': m.unit,
                        '日前价值 元/MWh': m.da_v, '窗口末实际使用 元/MWh': m.value_yuan_per_mwh})
    out['实际使用−日前 元/MWh'] = out['窗口末实际使用 元/MWh'] - out['日前价值 元/MWh']
    out['状态单位'] = m.kind.map({'E': 'MWh', 'Vu': '万m³', 'V': '万m³'}).fillna('未记录')
    out['窗口末状态 实时'] = m.state
    out['窗口末状态 日前'] = m.state_da
    if 'state_id' in m and m.state_id.notna().any():   # 第三轮：开日内时给出日内参考（偏差带中心）
        out['窗口末状态 日内'] = m.state_id
        out['参考来源'] = m.reference_source
    out['ΔE/ΔV（实时−日前）'] = m.delta
    out['可行带绑定'] = m.band_binding
    out['软化'] = m.soft
    out['兜底'] = m.fallback
    return out


# ---------------------------------------------------------------- 弃电
def curtail_compare(report, dt=0.25):
    st = report.get('stages', {})
    if '日前' not in st or '实时' not in st:
        return pd.DataFrame(), pd.DataFrame()
    d, r = st['日前']['detail'], st['实时']['detail']
    periods = sorted(set(r.period))
    d = d[d.period.isin(periods)]
    by_t = _triple(d.groupby('period').curtail_mw.sum(min_count=1), r.groupby('period').curtail_mw.sum(min_count=1), 'period')
    by_u = _triple(d.groupby('unit').curtail_mw.sum(min_count=1) * dt, r.groupby('unit').curtail_mw.sum(min_count=1) * dt, 'unit')
    return by_t, by_u.reindex(by_u[DIFF].abs().sort_values(ascending=False).index)


# ---------------------------------------------------------------- 阻塞
def congestion_compare(ll_da, ll_rt):
    if ll_da is None or ll_rt is None:
        return pd.DataFrame()
    periods = sorted(set(ll_rt.period))
    d = ll_da[ll_da.period.isin(periods)]
    def agg(f):
        return f.groupby('line').agg(c=('congested', 'sum'), L=('loading', 'max'), mu=('mu', 'max'))
    a, b = agg(d), agg(ll_rt)
    out = pd.DataFrame({'阻塞时段 日前': a.c, '阻塞时段 实时': b.c.reindex(a.index)})
    out['阻塞时段 实时−日前'] = out['阻塞时段 实时'] - out['阻塞时段 日前']
    out['最大负载率% 日前'] = a.L * 100
    out['最大负载率% 实时'] = b.L.reindex(a.index) * 100
    out['最大负载率% 实时−日前'] = out['最大负载率% 实时'] - out['最大负载率% 日前']
    out['最大影子价格 日前 元/MWh'] = a.mu
    out['最大影子价格 实时 元/MWh'] = b.mu.reindex(a.index)
    out['最大影子价格 实时−日前 元/MWh'] = out['最大影子价格 实时 元/MWh'] - out['最大影子价格 日前 元/MWh']
    out = out[(out['阻塞时段 日前'] > 0) | (out['阻塞时段 实时'] > 0) | (out[['最大负载率% 日前', '最大负载率% 实时']].max(axis=1) >= 90)]
    key = out['阻塞时段 实时−日前'].abs() * 1000 + out['最大负载率% 实时−日前'].abs()
    return out.reindex(key.sort_values(ascending=False).index).reset_index().rename(columns={'line': '线路'})


# ---------------------------------------------------------------- 两部制结算恒等式
def settlement_identity(R):
    dt = _dt(R)
    sd, be = R.get('settlement_detail'), R.get('settlement_by_entity')
    out, unavailable = {}, []

    def numeric(frame, columns, label):
        if frame is None or frame.empty:
            unavailable.append(label + '未记录')
            return None
        missing = set(columns) - set(frame)
        if missing:
            unavailable.append(label + '缺少字段：' + '、'.join(sorted(missing)))
            return None
        values = frame[list(columns)].apply(pd.to_numeric, errors='coerce')
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            unavailable.append(label + '含缺失或非有限数值')
            return None
        return values

    detail = numeric(sd, ['q_da', 'q_rt', 'lmp_da', 'lmp_rt', 'total'], '逐时段结算')
    if detail is not None:
        e = detail.q_da * dt * detail.lmp_da + (detail.q_rt - detail.q_da) * dt * detail.lmp_rt - detail.total
        out['逐时段最大误差 元'] = float(e.abs().max())
        f = sd.assign(误差=e)
        columns = ['entity', 'period', 'q_da', 'q_rt', 'lmp_da', 'lmp_rt', 'da_amount', 'rt_amount', 'total', '误差']
        out['worst'] = f.reindex(f.误差.abs().sort_values(ascending=False).index).head(5)[[c for c in columns if c in f]]
    entities = numeric(be, ['total', 'da_amount', 'rt_amount'], '逐主体结算')
    if entities is not None:
        out['逐主体最大误差 元'] = float((entities.total - entities.da_amount - entities.rt_amount).abs().max())
    reconciliation = numeric(R.get('settlement_reconciliation'), ['residual'], '资金平衡对账')
    if reconciliation is not None:
        out['资金平衡残差 元'] = float(reconciliation.residual.abs().max())
    if unavailable:
        out['未验证项'] = '；'.join(unavailable)
    errors = [v for k, v in out.items() if k.endswith('元')]
    # Known failures remain failures; incomplete evidence can never pass overall.
    out['通过（<1 元）'] = False if any(v >= 1 for v in errors) else (None if unavailable else True)
    return out


# ---------------------------------------------------------------- 关键指标
def kpi_compare(R, buses=None, ll_da=None, ll_rt=None, report=None):
    dt, periods = _dt(R), executed_periods(R)
    S = R.get('summary') or {}
    sp = S.get('spread') or {}
    rows = []
    def add(name, unit, a, b):
        rows.append({'指标': name, '单位': unit, DA: a, RT: b,
                     DIFF: (b - a) if a is not None and b is not None and np.isfinite(a) and np.isfinite(b) else np.nan})
    def energy(frame, reference, direction=0):
        if frame.empty or reference.empty or not {'period', 'unit', 'P_mw'} <= set(frame):
            return np.nan
        keys = ['period', 'unit']
        if frame.duplicated(keys).any() or set(map(tuple, frame[keys].to_numpy())) != set(map(tuple, reference[keys].to_numpy())):
            return np.nan
        power = pd.to_numeric(frame.P_mw, errors='coerce')
        if not np.isfinite(power.to_numpy(dtype=float)).all():
            return np.nan
        if direction > 0:
            power = power.clip(lower=0)
        elif direction < 0:
            power = -power.clip(upper=0)
        return float(power.sum(min_count=1) * dt)

    add('负荷电量加权均价', '元/MWh', sp.get('da_lw_avg'), sp.get('rt_lw_avg'))
    dp = R['da_nodal_prices'][R['da_nodal_prices'].period.isin(periods)].lmp
    rp = R['rt_prices'].lmp
    add('节点电价最高', '元/MWh', float(dp.max()), float(rp.max()))
    add('节点电价最低', '元/MWh', float(dp.min()), float(rp.min()))
    sys_d, sys_r = R.get('da_system'), R.get('rt_system')
    if sys_d is not None and sys_r is not None:
        add('负荷电量', 'MWh', float(sys_d[sys_d.period.isin(periods)].load_mw.sum() * dt), float(sys_r.load_mw.sum() * dt))
    ua, ur = R['da_unit_awards'], R['rt_units']
    ua = ua[ua.period.isin(periods)]
    for typ, name in [('coal', '燃煤发电'), ('gas_ccgt', '燃气联合循环发电'), ('gas_gt', '燃气轮机发电'), ('hydro', '水电发电'),
                      ('wind', '风电发电'), ('pv', '光伏发电'), ('tie', '区外来电')]:
        if (ua.type == typ).any():
            a, b = ua[ua.type == typ], ur[ur.type == typ]
            add(name, 'MWh', energy(a, a), energy(b, a))
    for typ, name in [('storage', '储能'), ('pumped', '抽蓄')]:
        if (ua.type == typ).any():
            a, b = ua[ua.type == typ], ur[ur.type == typ]
            add(name + '放电/发电', 'MWh', energy(a, a, 1), energy(b, a, 1))
            add(name + '充电/抽水', 'MWh', energy(a, a, -1), energy(b, a, -1))
    if report is not None:
        by_t, _ = curtail_compare(report, dt)
        if len(by_t):
            add('弃风弃光', 'MWh', float(by_t[DA].sum() * dt), float(by_t[RT].sum() * dt))
    if ll_da is not None and ll_rt is not None:
        from common import congestion_period_count
        add('阻塞时段数', '段', congestion_period_count(ll_da[ll_da.period.isin(periods)]),
            congestion_period_count(ll_rt))
    rd, rr = R.get('da_reserve_system'), R.get('rt_reserve_system')
    if rd is not None and rr is not None and 'R_up_short' in rd:
        add('上备用缺额', 'MWh', float(rd[rd.period.isin(periods)].R_up_short.sum() * dt), float(rr.R_up_short.sum() * dt))
    st = state_compare(R)
    if len(st):
        label = _state_end_label(st.attrs['execution_scope'])
        end = st[st.period == max(periods)].set_index(['kind', 'entity', '单位'])
        keys = pd.MultiIndex.from_frame(st[['kind', 'entity', '单位']].drop_duplicates())
        for (kind, entity, unit), row in end.reindex(keys).iterrows():
            add(f'{entity} {label}{kind}', unit, float(row[DA]), float(row[RT]))
    return pd.DataFrame(rows)


# ================================================================= render
def render_final(R, *, buses, ll_da, ll_rt, report, lbl, sel, tabs=None):
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
    from common import DA_COLOR, DIFF_SCALE, PALETTE, RT_COLOR, df_download, show_df, show_fig, style_fig
    TIME = '时刻（每15分钟）'
    dt = _dt(R)
    periods = executed_periods(R)
    if not periods:
        if tabs is not None:
            with tabs[0]:
                st.info('本次为仅日前结果，没有实时执行，日前与实时的对比不适用。')
        else:
            st.info('本次为仅日前结果，没有实时执行，日前与实时的对比不适用。')
        return tabs
    if tabs is None:
        from result_navigation import FINAL_SECTIONS
        tabs = st.tabs(FINAL_SECTIONS)
    with tabs[0]:
        scope = _execution_scope(R)
        if not scope['full_day']:
            expected = scope['expected'] if scope['expected'] is not None else '未记录'
            st.warning(f'实时已保存 {len(periods)} 段 / 预期 {expected} 段，未核实为完整日；'
                       f"状态比较截至{_state_end_label(scope)}。以下日前值也只取同一批时段，不外推全天。")
        st.caption('最终结果以日前与实时的直接对比为核心：每张图表同时给出 日前、实时、实时−日前；偏差最大的时段和主体排序列出，便于定位。')

    def diff_fig(frame, title, ylab, x=None, key=None, height=380, xlab=TIME):
        x = x if x is not None else [lbl[int(t)] for t in frame.index]
        fig = go.Figure()
        fig.add_scatter(x=x, y=frame[DA], name='日前', line=dict(color=DA_COLOR, width=2))
        fig.add_scatter(x=x, y=frame[RT], name='实时', line=dict(color=RT_COLOR, dash='dash', width=2))
        fig.add_bar(x=x, y=frame[DIFF], name='实时−日前（右轴）', yaxis='y2', marker_color='#9ca3af', opacity=0.55)
        fig.update_layout(yaxis2=dict(overlaying='y', side='right', title=f'实时−日前 · {ylab}', showgrid=False, zeroline=True))
        show_fig(style_fig(fig, title, xlab, ylab, height), key=key)

    with tabs[0]:
        k = kpi_compare(R, buses, ll_da, ll_rt, report)
        if k[[DA, RT]].isna().any().any():
            st.caption('空白值表示未记录或证据不完整，不按零值计算偏差。')
        show_df(k, '%.2f')
        df_download(k, 'final_kpi_da_rt.csv', key='final_kpi')
    with tabs[1]:
        sp = system_price_compare(R, buses)
        diff_fig(sp, '全网节点负荷加权电价：日前、实时与价差', '电价（元/MWh）', key='final_sys_price')
        c1, c2 = st.columns(2)
        with c1:
            fig = px.histogram(sp.dropna(), x=DIFF, nbins=40, labels={DIFF: '价差 实时−日前（元/MWh）'})
            fig.add_vline(x=0, line_dash='dot')
            show_fig(style_fig(fig, '全网价差分布（逐时段）', '价差（元/MWh）', '时段数', 320), key='final_sys_hist')
        with c2:
            st.markdown('**全网价差最大的时段**')
            top = rank_abs(sp).reset_index()
            top.insert(1, '时刻', [lbl[int(t)] for t in top.period])
            show_df(top.rename(columns={'period': '时段', DA: '日前 元/MWh', RT: '实时 元/MWh', DIFF: '实时−日前 元/MWh'}), '%.1f')
        nl = node_price_compare(R)
        buses_all = sorted(nl.bus.unique())
        default = [b for b in (16, 19, 25, 30) if b in buses_all][:2] or buses_all[:2]
        bsel = st.multiselect('选定节点（日前实线 / 实时虚线 / 价差柱）', buses_all, default=default, format_func=lambda b: f'节点{b}', key='final_nodes')
        for i, b in enumerate(bsel):
            f = nl[nl.bus == b].set_index('period')[[DA, RT, DIFF]]
            diff_fig(f, f'节点{b} 电价：日前、实时与价差', '电价（元/MWh）', key=f'final_node_{b}', height=320)
        if bsel:
            sub = nl[nl.bus.isin(bsel)]
            fig = px.box(sub, x=sub.bus.map(lambda b: f'节点{b}'), y=DIFF, points='outliers', labels={'x': '节点', DIFF: '价差（元/MWh）'})
            show_fig(style_fig(fig, '选定节点价差分布', '节点', '价差 实时−日前（元/MWh）', 320), key='final_node_box')
        piv = nl.pivot(index='bus', columns='period', values=DIFF)
        fig = px.imshow(piv.values, x=[lbl[int(t)] for t in piv.columns], y=[str(b) for b in piv.index], aspect='auto',
                        color_continuous_scale=DIFF_SCALE, color_continuous_midpoint=0, labels=dict(color='元/MWh'))
        fig.update_yaxes(type='category', autorange='reversed', dtick=1, tickfont=dict(size=9))
        show_fig(style_fig(fig, '节点价差热力图（实时−日前）', TIME, '节点编号', 560), key='final_node_heat')
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('**节点按 |价差| 均值排序**')
            show_df(node_spread_ranking(nl), '%.1f')
        with c2:
            st.markdown('**价差最大的 时段×节点**')
            t = rank_abs(nl, n=15)
            t.insert(1, '时刻', [lbl[int(p)] for p in t.period])
            show_df(t.rename(columns={'period': '时段', 'bus': '节点', DA: '日前 元/MWh', RT: '实时 元/MWh', DIFF: '实时−日前 元/MWh'}), '%.1f')
        df_download(nl, 'final_node_price_da_rt_diff.csv', key='final_nl')
    with tabs[2]:
        ee = entity_energy_compare(R)
        st.markdown('**逐主体电量（已执行时段）：日前中标 / 实时执行 / 偏差，按逐时段|偏差|合计排序**')
        show_df(ee.reset_index().rename(columns={'unit': '主体', DA: '日前 MWh', RT: '实时 MWh', DIFF: '实时−日前 MWh'}), '%.1f')
        top = ee.head(12)
        fig = go.Figure()
        fig.add_bar(x=top.index, y=top[DA], name='日前', marker_color=DA_COLOR)
        fig.add_bar(x=top.index, y=top[RT], name='实时', marker_color=RT_COLOR)
        fig.add_bar(x=top.index, y=top[DIFF], name='实时−日前', marker_color='#9ca3af')
        fig.update_layout(barmode='group')
        show_fig(style_fig(fig, '偏差最大的主体电量对比', '主体', '电量（MWh）', 360), key='final_energy_bar')
        pe = period_entity_deviation(R)
        u = st.selectbox('查看主体逐时段出力（日前/实时/偏差）', list(ee.index), key='final_energy_unit')
        f = pe[pe.unit == u].set_index('period').rename(columns={'日前 MW': DA, '实时 MW': RT, '偏差 MW': DIFF})
        diff_fig(f, f'{u} 出力：日前、实时与偏差', '出力（MW）', key='final_energy_curve', height=320)
        st.markdown('**偏差最大的 时段×主体**')
        t = pe.sort_values('|偏差| MW', ascending=False).head(15).copy()
        t.insert(1, '时刻', [lbl[int(p)] for p in t.period])
        show_df(t.rename(columns={'period': '时段', 'unit': '主体', 'type': '类型'}), '%.1f')
        df_download(pe, 'final_entity_period_deviation.csv', key='final_pe')
    with tabs[3]:
        stt = state_compare(R)
        if stt.empty:
            st.info('本次没有储能、抽蓄或水电状态记录。')
        else:
            st.markdown('**各主体状态偏差排序（时段末状态）**')
            show_df(state_ranking(stt).rename(columns={'kind': '类别', 'entity': '主体'}), '%.2f')
            for (kind, ent), f in stt.groupby(['kind', 'entity']):
                diff_fig(f.set_index('period'), f'{ent} {kind}（{f.单位.iloc[0]}）：日前、实时与偏差', f'{kind}（{f.单位.iloc[0]}）',
                         key=f'final_state_{ent}', height=300)
            df_download(stt, 'final_state_da_rt_diff.csv', key='final_st')
    with tabs[4]:
        vc = value_compare(R)
        if vc.empty:
            st.info('本次实时未使用价值法（或为旧结果），没有窗口末价值记录。')
        else:
            st.caption('日前价值曲线来自日前 SCED 状态约束对偶；实时每个窗口只在窗口末 τ 计入 −v·X。'
                       '实时实际使用值直接读取本次保存的窗口记录；轨迹与价值参考按各资源、各窗口的实际来源区分，'
                       '可为日前、日内轮次或回退，不能仅因开启日内就认定全部取最新日内轮次。'
                       '请核对本次 rt_value_windows.csv：reference_source 为轨迹来源，value_source 为价值来源，'
                       'reference_round 为日内轮次起点时段，reference_fallback_reason 为参考回退原因；缺失字段为未记录，不自行推断。'
                       '正值=多存省报价目标；负值不是符号反了，而是自身充电买价/放电报价高于电价'
                       '（报价目标口径，已有限差分核对），见方法说明。')
            for (kind, ent), f in vc.groupby(['类别', '主体']):
                f = f.sort_values('窗口末τ')
                x = [lbl[int(t)] for t in f['窗口末τ']]
                fig = go.Figure()
                curve = R['da_value_curves']; curve = curve[curve.unit == ent].sort_values('period')
                fig.add_scatter(x=[lbl[int(t)] for t in curve.period], y=curve.value_yuan_per_mwh, name='日前价值曲线', line=dict(color=DA_COLOR))
                fig.add_scatter(x=x, y=f['窗口末实际使用 元/MWh'], name='实时窗口末实际使用', mode='markers', marker=dict(color=RT_COLOR, size=7))
                fig.add_bar(x=x, y=f['实际使用−日前 元/MWh'], name='价值差 实际使用−日前（元/MWh，右轴）', yaxis='y2', marker_color='#9ca3af')
                fig.update_layout(yaxis2=dict(overlaying='y', side='right', title='价值差（元/MWh）', showgrid=False))
                window_time = '窗口末时段τ（时段起点）'
                show_fig(style_fig(fig, f'{ent} {kind} 价值：日前 vs 窗口末实际使用', window_time, '价值（元/MWh）', 320), key=f'final_value_{ent}')
                unit = f['状态单位'].iloc[0]
                if unit == '未记录':
                    st.info(f'{ent} 状态单位未记录，不把库存偏差画到价值轴上；保存数值见下表。')
                else:
                    state = f.rename(columns={'窗口末状态 日前': DA, '窗口末状态 实时': RT, 'ΔE/ΔV（实时−日前）': DIFF})
                    diff_fig(state, f'{ent} {kind} 窗口末状态：日前 vs 实时前瞻计划', f'状态（{unit}）',
                             x=x, key=f'final_value_state_{ent}', height=300, xlab=window_time)
            st.caption('窗口末状态来自各窗口前瞻计划，不等于该时段已经执行；价值差为元/MWh，状态及状态差按各类原生单位另图展示。')
            for unit, f in vc.groupby('状态单位', sort=False):
                st.markdown(f'**|窗口末状态偏差| 最大的窗口（{unit}）**')
                show_df(rank_abs(f, 'ΔE/ΔV（实时−日前）', 15), '%.2f')
            df_download(vc, 'final_value_da_vs_used.csv', key='final_vc')
    with tabs[5]:
        by_t, by_u = curtail_compare(report, dt)
        if by_t.empty:
            st.info('没有可对比的风光弃电记录。')
        else:
            diff_fig(by_t, '分时弃风弃光功率：日前、实时与差值', '弃电功率（MW）', key='final_curt')
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('**分场站弃电量（MWh），按 |差值| 排序**')
                show_df(by_u.reset_index().rename(columns={'unit': '场站'}), '%.1f')
            with c2:
                st.markdown('**弃电差值最大的时段（MW）**')
                t = rank_abs(by_t).reset_index(); t.insert(1, '时刻', [lbl[int(p)] for p in t.period])
                show_df(t.rename(columns={'period': '时段'}), '%.1f')
    with tabs[6]:
        cc = congestion_compare(ll_da, ll_rt)
        if cc.empty:
            st.info('日前与实时均无阻塞或高负载线路（≥90%）。')
        else:
            st.markdown('**阻塞或高负载线路：日前 / 实时 / 差值，按阻塞时段差排序**')
            show_df(cc, '%.1f')
            lsel = st.multiselect('线路负载率曲线', list(cc['线路']), default=list(cc['线路'][:2]), key='final_lines')
            for ln in lsel:
                a = ll_da[ll_da.line == ln].set_index('period').loading * 100
                b = ll_rt[ll_rt.line == ln].set_index('period').loading * 100
                diff_fig(_triple(a.reindex(periods), b.reindex(periods)), f'{ln} 负载率：日前、实时与差值', '负载率（%）', key=f'final_line_{ln}', height=300)
    with tabs[7]:
        ident = settlement_identity(R)
        st.caption('两部制：日前电费 = 日前电量×日前价；偏差电费 = (实际−日前)×实时价；合计 = 二者之和。下列误差应 < 1 元（CSV 保留4位小数造成的舍入误差约 0.01 元）。')
        if '未验证项' in ident:
            st.warning('结算恒等式未完整验证：' + ident['未验证项'] + '。缺失项不按零误差或通过处理。')
        show_df(pd.DataFrame([{'检查项': k, '值': '未验证' if v is None else v} for k, v in ident.items() if k != 'worst']).astype(str))
        if 'worst' in ident:
            st.markdown('**误差最大的 主体×时段**')
            show_df(ident['worst'], '%.4f')
        rc = R.get('settlement_reconciliation')
        if rc is not None:
            st.markdown('**资金平衡校核（元）**')
            show_df(rc, '%.2f')
    return tabs
