"""第二轮H/J项：价值法相关展示（只读本次运行保存的 CSV）。

* render_da_values：日前价值曲线（储能/抽蓄/水电状态约束对偶，元/MWh）叠加所在节点日前电价。
* render_rt_values：实时实际使用的窗口末价值、相对日前的状态偏差、价值定价时段。
* render_window_browser：实时窗口浏览（2h 计划、窗口末价值、ΔE/ΔV、可行带绑定、兜底记录）。
* render_aux_vs_dual：辅助定价价格 vs 对偶价格及差值。
"""
from __future__ import annotations

import pandas as pd
from smm.time_grid import result_time_axis, result_time_grid

SIGN_NOTE = ('价值 v 来自所选日前或日内调度 SCED：中间时段 v[t] = −dual(c_X[t+1])，表示多存 1 单位对后续报价目标的边际影响；'
             '正值=多存省成本）。抽蓄按 v/e_gen、水电按 v×0.36×耗水率 折算为 元/MWh。'
             '口径是“报价目标”：含储能/抽蓄自身报价，v = 系统其余成本节省（≈η_d·LMP 或 LMP/η_c，为正）− 自身报价边际；'
             '充电买价/放电报价高于电价时 v 为负，表示多余存量会挤掉一次买价高于电价的充电或迫使一次报价高于电价的放电。'
             '实时目标同样含自身报价，因此用这一口径（见 docs/实时价值法.md §符号核对）。')


def terminal_value_note(R):
    """Describe the saved semantic version, never relabel older value exports."""
    curves = R.get('da_value_curves')
    if curves is not None and 'continuation_value_scope' in curves and (
            curves.continuation_value_scope == 'no_cross_day_residual').any():
        return ('本次记录不设跨日残值：日末额外延续价值为 0；终态目标约束对偶另列为诊断，'
                '不代表对偶本身为 0。日前目标对偶与实时采用软目标还是硬目标是不同口径。')
    return ('此存档未明确记录日末延续价值口径；图表保持已保存数值，'
            '不把旧结果的终态对偶重新解释为当前模型的跨日残值。')


def _source_label(value):
    if pd.isna(value):
        return '未记录'
    if value == 'day_ahead':
        return '日前'
    if isinstance(value, str) and value.startswith('intraday@'):
        try:
            return f'日内（轮次起点：第{int(value.split("@", 1)[1]) + 1}段）'
        except ValueError:
            pass
    return str(value)


def reference_details(frame):
    """Expose the selected resource reference, preserving unknown old fields."""
    fields = {'window_start': '窗口起点时段', 'tau': '窗口末时段τ', 'kind_label': '类别', 'unit': '主体',
              'reference_source': '轨迹来源', 'value_source': '价值来源',
              'reference_round': '日内轮次起点时段', 'reference_fallback_reason': '参考回退原因',
              'state_reference': '窗口末参考状态', 'delta_from_reference': '实时−参考状态',
              'state_da': '窗口末日前状态', 'delta': '实时−日前状态'}
    out = pd.DataFrame(index=frame.index)
    for field, label in fields.items():
        if field in ('reference_source', 'value_source'):
            out[label] = frame[field].map(_source_label) if field in frame else '未记录'
        elif field == 'reference_fallback_reason':
            out[label] = frame[field].fillna('无（已记录）').replace('', '无（已记录）') if field in frame else '未记录'
        else:
            out[label] = frame[field] if field in frame else pd.NA
    return out


def terminal_policy_note(frame):
    """Describe recorded RT policies without reinterpreting old archives."""
    if frame is None or 'terminal_policy' not in frame:
        return '此存档未明确记录实时日末软/硬政策，不能根据软化标记或当前默认配置推断。'
    policies = set(frame.terminal_policy.dropna())
    notes = []
    if 'soft' in policies:
        notes.append('已记录的软政策从首次求解起使用计划日末软目标，不是硬目标失败后的应急兜底。'
                     '储能/抽蓄对日前日末目标的双侧偏差计成本，水电只对低于原日末下限的不足计成本；'
                     '物理上下界不放松。中间参考带仍按配置使用，软政策不再施加必须精确回到日末目标的可达包络，'
                     '日末也不额外保留±2%硬带。')
    if 'hard' in policies:
        notes.append('已记录的硬政策保留原日末硬目标及相应可达要求；不能用新软政策重新解释旧运行。')
    if frame.terminal_policy.isna().any() or policies - {'soft', 'hard'} or not policies:
        notes.append('部分记录的实时日末政策未识别，按原始记录展示，不推断软/硬。')
    return ' '.join(notes)


def terminal_policy_details(frame):
    """Keep native/energy/cost quantities separate; do not sum rolling forecasts."""
    fields = {'window_start': '窗口起点时段', 'tau': '窗口末时段τ', 'unit': '主体',
              'terminal_policy': '实时日末政策', 'planned_terminal_soft': '计划日末软目标',
              'terminal_penalty_yuan_per_discharge_mwh': '罚价 元/可放电MWh',
              'discharge_energy_factor': '可放电MWh/原生状态单位',
              'deviation_discharge_mwh': '计罚偏差 可放电MWh',
              'terminal_penalty_cost': '日末偏差优化成本 元'}
    out = pd.DataFrame(index=frame.index)
    for field, label in fields.items():
        out[label] = frame[field] if field in frame else pd.NA
    out['实时日末政策'] = out['实时日末政策'].map(
        lambda value: '未记录' if pd.isna(value) else {'soft': '计划软目标', 'hard': '硬目标'}.get(value, str(value)))
    return out


def _planned_terminal_soft_mask(frame):
    """Only an explicit saved flag changes the presentation of legacy bands."""
    flags = frame.get('planned_terminal_soft', pd.Series(False, index=frame.index))
    return flags.map(lambda value: str(value).strip().lower() == 'true')


def _render_terminal_policy(frame):
    import streamlit as st
    from common import show_df
    st.caption(terminal_policy_note(frame))
    if frame is None or 'terminal_policy' not in frame:
        return
    with st.expander('日末政策、等价能量与优化成本（逐窗口）', expanded=False):
        st.caption('计罚偏差和成本仅对应窗口已包含日末的记录；中间参考带行不代表后缀终态预测。'
                   '除最后执行窗口外，这些都是预测日末快照，不跨窗口累加为全天费用。'
                   '罚价为可配置研究假设，不是市场规则；优化成本不是实际结算付款。'
                   '电池按ηd、抽蓄按e_gen、水电按1/(0.36×耗水率)换成可放电MWh，存量不再乘时段长度。'
                   '原生状态单位为电池MWh、抽蓄/水电万m³。'
                   '未保存的字段保持缺失，不补零；计划软目标合规、偏差是否为零、物理缺额和应急带软化须分别检查。')
        show_df(terminal_policy_details(frame), '%.3f')


def _render_intraday_failures(R):
    import streamlit as st
    from common import show_df
    intraday = (R.get('summary') or {}).get('intraday') or {}
    failures = intraday.get('failed_rounds')
    if isinstance(failures, list) and failures:
        st.warning(f'已保存 {len(failures)} 个日内失败轮次，未计为成功轮次。'
                   '明确的日内物理不可行会使后续参考回退日前，而不是沿用旧日内轮；实际来源以逐资源记录为准。'
                   '这与计划日末软目标和实时应急带软化不同。')
        show_df(pd.DataFrame(failures))
    elif intraday.get('failure_count'):
        st.warning(f'日内汇总记录失败 {intraday["failure_count"]} 轮，但未保存可用的逐轮明细；不能据此补造阶段或原因。')


def hydro_lookahead_details(frame):
    """Show saved scalar forecasts, not reconstructed dispatch or total costs."""
    fields = {'window_start': '窗口起点时段', 'tau': '窗口末时段τ', 'unit': '水电主体',
              'reference_source': '参考来源', 'reference_round': '日内轮次起点时段',
              'reference_fallback_reason': '参考回退原因',
              'state': '窗口末计划库容 万m³', 'reference_recovery_volume': '参考续程净回蓄 万m³',
              'minimum_window_end_volume': '避免预计短缺的软阈值 万m³',
              'terminal_target': '日末下限目标 万m³',
              'projected_terminal_volume': '参考续程预计日末库容 万m³',
              'actual_projected_shortfall': '按预计库容计算的不足 万m³',
              'projected_terminal_shortfall': '模型前瞻短缺松弛 万m³',
              'discharge_mwh_per_native_unit': '可放电MWh/万m³',
              'projected_terminal_shortfall_mwh': '模型前瞻短缺 可放电MWh',
              'penalty_yuan_per_discharge_mwh': '前瞻罚价 元/可放电MWh',
              'penalty_cost': '本窗口前瞻优化成本 元', 'forecast_only': '仅参考预测',
              'active': '前瞻项启用', 'slack_zero_means_target_met': '零松弛可判预测目标满足'}
    out = pd.DataFrame(index=frame.index)
    for field, label in fields.items():
        if field == 'reference_source':
            source = frame[field] if field in frame else pd.Series(pd.NA, index=frame.index)
            if 'reference_source_label' in frame:
                source = frame.reference_source_label.combine_first(source)
            out[label] = source.map(lambda v: '日内（轮次见记录）' if isinstance(v, str) and v == 'intraday' else _source_label(v))
        elif field == 'reference_fallback_reason':
            out[label] = frame[field].fillna('无（已记录）').replace('', '无（已记录）') if field in frame else '未记录'
        else:
            out[label] = frame[field] if field in frame else pd.NA
    return out


def render_hydro_terminal_lookahead(R, key, window_start=None):
    import streamlit as st
    from common import show_df, df_download
    st.markdown('**水电参考续程预计日末短缺（独立前瞻项）**')
    frame = R.get('rt_hydro_terminal_lookahead')
    if frame is None:
        return st.caption('本存档未记录水电参考续程前瞻，不能把未记录解释为预计短缺或成本为0。')
    if window_start is not None and len(frame):
        if 'window_start' not in frame:
            st.warning('前瞻记录缺少窗口起点，无法筛出当前窗口；以下保留全部已保存记录，不推断所属窗口。')
        else:
            frame = frame[frame.window_start == window_start]
    if frame.empty:
        scope = '本窗口' if window_start is not None else '该文件'
        return st.caption(f'前瞻文件已保存，但{scope}没有对应记录；不补造零短缺或零成本。'
                          '该项仅用于尚未覆盖日末的软政策窗口；进入日末窗口后由原日末目标接管。')
    st.caption('各行是在窗口末计划库容基础上，沿本轮选定日前/日内参考出库继续运行的预计日末短缺。'
               '这是参考预测，不是已执行末态，也不是未来全网、水电爬坡或梯级联合可达性的证明。'
               '上游已执行到水取实际历史，尚未执行部分取参考预测，不代表与当前窗口新出库完全联立。'
               '软阈值不是硬物理库容下限，物理界独立存在；短缺仅对不足计成本，日末延续价值仍为0。')
    st.caption('这里只显示已保存数值，不跨窗口加总前瞻优化成本，不作为实际成本或市场结算付款。'
               '库容和短缺单位为万m³；等价可放电MWh按1/(0.36×耗水率)转换，存量不再乘时段长度。'
               '模型松弛与按预计库容计算的不足分列；未启用的前瞻项不能仅凭零松弛认定预计目标满足。'
               '进入日末窗口后不重复添加该项，改由上述日末软目标计罚；它也不属于紧急回退。'
               '时段序号从0起，缺字段保持未记录或缺失。')
    show_df(hydro_lookahead_details(frame), '%.3f', height=300)
    pass  # Result download controls removed.


def _bus_of(R, unit):
    for key in ('da_storage', 'da_pumped', 'da_unit_awards'):
        t = R.get(key)
        if t is not None and 'unit' in t and 'bus' in t and (t.unit == unit).any():
            return int(t[t.unit == unit].bus.iloc[0])
    return None


def _empty(st, what):
    st.info(f'本次结果没有{what}（旧结果，或实时未使用价值法）。不以零补齐。')


def render_da_values(R, lbl, key):
    import plotly.graph_objects as go
    import streamlit as st
    from common import DA_COLOR, PALETTE, show_df, show_fig, style_fig, df_download
    vc = R.get('da_value_curves')
    if vc is None or vc.empty:
        return _empty(st, '日前价值曲线')
    st.markdown('**日前价值曲线（叠加所在节点日前电价）**')
    st.caption(SIGN_NOTE)
    st.caption(terminal_value_note(R))
    prices = R['da_nodal_prices']
    for i, (unit, f) in enumerate(vc.groupby('unit', sort=False)):
        f = f.sort_values('period')
        fig = go.Figure()
        fig.add_scatter(x=[lbl[int(t)] for t in f.period], y=f.value_yuan_per_mwh, name=f'{unit} 价值（元/MWh）',
                        line=dict(color=PALETTE[(i + 2) % len(PALETTE)], width=2.4))
        bus = _bus_of(R, unit)
        if bus is not None:
            p = prices[prices.bus == bus].sort_values('period')
            fig.add_scatter(x=[lbl[int(t)] for t in p.period], y=p.lmp, name=f'节点{bus} 日前电价', line=dict(color=DA_COLOR, dash='dot'))
        kind = f.kind_label.iloc[0]
        extra = '' if f.native_unit.iloc[0] == '元/MWh' else f'；原生单位 {f.native_unit.iloc[0]}，图中已折算为 元/MWh'
        show_fig(style_fig(fig, f'{unit} {kind}价值 vs 节点电价{extra}', result_time_axis(R), '元/MWh', 320), key=f'{key}:{unit}')
    summary = vc.groupby(['kind_label', 'unit']).value_yuan_per_mwh.agg(['min', 'mean', 'max']).reset_index()
    summary.columns = ['类别', '主体', '最小 元/MWh', '平均 元/MWh', '最大 元/MWh']
    show_df(summary, '%.2f')
    if 'terminal_target_dual_native' in vc and 'continuation_value_scope' in vc:
        terminal = vc[vc.continuation_value_scope == 'no_cross_day_residual']
        columns = {'unit': '主体', 'value_native': '日末延续价值（原生单位）', 'native_unit': '原生价值单位',
                   'terminal_target_constraint': '终态目标约束', 'terminal_target_dual_native': '终态目标对偶（原生单位）',
                   'terminal_target_dual_yuan_per_mwh': '终态目标对偶 元/MWh',
                   'terminal_target_dual_available': '目标对偶是否已记录'}
        if len(terminal):
            with st.expander('终态目标对偶与日末延续价值（不同口径）', expanded=False):
                st.caption('终态目标对偶衡量改变终态目标的边际影响；不是实时日末的额外留能奖励。未记录的对偶不补零。')
                show_df(terminal[[c for c in columns if c in terminal]].rename(columns=columns), '%.3f')
    pass  # Result download controls removed.


def render_rt_values(R, lbl, key):
    import plotly.graph_objects as go
    import streamlit as st
    from common import DA_COLOR, RT_COLOR, show_df, show_fig, style_fig, df_download
    w = R.get('rt_value_windows')
    if w is None or w.empty:
        return _empty(st, '实时窗口末价值记录')
    st.markdown('**实时实际使用的价值（每窗口只在窗口末 τ 计入 −v·X）**')
    st.caption('“实际使用值”直接读取每个窗口保存的入模价值；轨迹和价值可能来自日前、最新日内轮，或缺失后的日前回退。'
               '每个资源的实际来源与回退原因见下表，不能由“已开启日内”推断全部资源都用了日内。'
               '中间偏差带围绕实际选中的参考状态，并受物理边界限制；日末软/硬要求按保存政策区分，'
               '带绑定本身不能证明由价值项单独造成。')
    st.caption(terminal_value_note(R))
    _render_terminal_policy(w)
    render_hydro_terminal_lookahead(R, key)
    _render_intraday_failures(R)
    with st.expander('参考来源与回退记录（逐窗口、逐资源）', expanded=False):
        st.caption('时段序号按存档从 0 起；储能状态为 MWh，抽蓄/水电状态为万m³。未知旧字段保持未记录。')
        show_df(reference_details(w), '%.3f', height=300)
    for unit, f in w.groupby('unit', sort=False):
        f = f.sort_values('tau')
        x = [lbl[int(t)] for t in f.tau]
        fig = go.Figure()
        fig.add_scatter(x=x, y=f.value_yuan_per_mwh, name='窗口末使用价值（元/MWh）', mode='lines+markers', line=dict(color=RT_COLOR))
        reference_known = 'delta_from_reference' in f and f.delta_from_reference.notna().all()
        delta_column = 'delta_from_reference' if reference_known else 'delta'
        delta_label = 'ΔE/ΔV 实时−参考（右轴）' if reference_known else 'ΔE/ΔV 实时−日前（旧记录，右轴）'
        fig.add_bar(x=x, y=f[delta_column], name=delta_label, yaxis='y2', marker_color='#9ca3af', opacity=0.6)
        b = f[f.band_binding.astype(bool) & ~_planned_terminal_soft_mask(f)]
        if len(b):
            fig.add_scatter(x=[lbl[int(t)] for t in b.tau], y=b[delta_column], yaxis='y2', mode='markers', name='可行带绑定',
                            marker=dict(symbol='x', size=9, color='#e9a03b'))
        fig.update_layout(yaxis2=dict(overlaying='y', side='right', title='ΔE(MWh) / ΔV(万m³)', showgrid=False))
        show_fig(style_fig(fig, f'{unit} {f.kind_label.iloc[0]}：实际使用价值与窗口末偏差', '窗口末时刻 τ', '价值（元/MWh）', 300),
                 key=f'{key}:{unit}')
    ps = R.get('rt_value_price_setting')
    if ps is not None and len(ps):
        st.markdown('**价值可能参与定价的时段（启发式筛查）**')
        st.caption('以出力位于区间内部、节点电价未贴近报价段等条件筛选；这是定位线索，不是价值项决定边际电价的严格证明。')
        hit = ps[ps.value_sets_price.astype(bool)]
        tab = hit.groupby('unit').agg(候选时段数=('period', 'count'), 首时段=('period', 'min'), 末时段=('period', 'max')).reset_index()
        show_df(tab.rename(columns={'unit': '主体'}))
        if len(hit):
            show = hit.copy(); show.insert(1, '时刻', [lbl[int(t)] for t in show.period])
            with st.expander(f'候选时段明细（{len(hit)} 行）'):
                show_df(show, '%.2f', height=300)
        pass  # Result download controls removed.
    pass  # Result download controls removed.


def render_window_browser(R, lbl, key):
    import plotly.graph_objects as go
    import streamlit as st
    from common import DA_COLOR, RT_COLOR, show_df, show_fig, style_fig
    w = R.get('rt_value_windows')
    fb = R.get('rt_fallbacks')
    st.caption('下列记录用于追踪实时应急处理或原有锚点偏差，不把计划日末软目标视为不可行兜底。'
               '参考选取回退、计划末态偏差和应急带软化分别核对。')
    if fb is None:
        st.caption('兜底/状态偏差记录未保存，无法据此判断是否发生回退或硬约束是否可行。')
    elif len(fb):
        count = int(fb.t.nunique()) if 't' in fb else None
        st.warning(f'已保存 {len(fb)} 条兜底/状态偏差记录' + (f'，涉及 {count} 个窗口。' if count is not None else '。') + '具体原因见明细。')
        show_df(fb, '%.3f')
    else:
        st.caption('已保存范围内未记录兜底/状态偏差事件；这不等于完整物理可行性证明。')
    _render_intraday_failures(R)
    if w is None or w.empty:
        return _empty(st, '窗口记录')
    starts = sorted(w.window_start.unique())
    t0 = st.select_slider('窗口起点', options=starts, value=starts[0], key=key + ':start',
                          format_func=lambda t: f'{lbl[int(t)]} · 第{int(t)+1}段')
    f = w[w.window_start == t0]
    tau = int(f.tau.iloc[0])
    count = tau - int(t0) + 1
    horizon = ((R.get('summary') or {}).get('realtime') or {}).get('lookahead_h')
    configured = f'配置前瞻 {horizon:g} 小时；' if isinstance(horizon, (int, float)) and not isinstance(horizon, bool) else '配置前瞻未记录；'
    st.caption(f'窗口 {lbl[int(t0)]} 起，末时段 τ 的起点为 {lbl[tau]}；保存范围共 {count} 段、{count * result_time_grid(R)[1] * 60:g} 分钟'
               f'（{configured}日末窗口按剩余时段截断，正式只执行首段）。')
    _render_terminal_policy(f)
    render_hydro_terminal_lookahead(R, key, window_start=t0)
    if int(t0) == tau == len(lbl) - 1:
        st.caption('本窗口仅含最后执行段；其末状态和日末偏差对应已执行日末，不再是此前窗口的日末预测。')
    view = f[['kind_label', 'unit', 'value_native', 'value_yuan_per_mwh', 'state', 'state_da', 'delta', 'lower', 'upper',
              'band_binding', 'band_slack', 'soft', 'fallback']].rename(columns={
        'kind_label': '类别', 'unit': '主体', 'value_native': '价值（原生单位）', 'value_yuan_per_mwh': '价值 元/MWh',
        'state': '窗口末计划状态', 'state_da': '窗口末状态 日前', 'delta': 'ΔE/ΔV', 'lower': '保存的带/目标下界', 'upper': '保存的带/目标上界',
        'band_binding': '带绑定', 'band_slack': '带/目标松弛', 'soft': '求解软约束标记', 'fallback': '应急重求解记录'})
    planned_soft = _planned_terminal_soft_mask(f)
    if planned_soft.any():
        view['带绑定'] = view['带绑定'].astype(object)
        view.loc[planned_soft, '带绑定'] = '不适用（计划日末软目标）'
        st.caption('计划日末软目标不按可行带绑定判读；其偏差见带/目标松弛和日末优化成本，不等于应急重求解。')
    show_df(view, '%.3f')
    st.markdown('**本窗口实际参考（逐资源）**')
    st.caption('参考来源回退、计划日末软目标与应急带软化是不同事件；下表只描述参考选取。状态单位同上表。')
    show_df(reference_details(f), '%.3f')
    # 2h plan: executed path inside the window vs DA, from saved executed states.
    units = st.multiselect('窗口内状态轨迹（实时已执行 vs 日前）', list(f.unit), default=list(f.unit[:2]), key=key + ':units')
    rng = range(int(t0), tau + 1)
    for unit in units:
        kind = f[f.unit == unit].kind.iloc[0]
        dk, rk, idc, col = {'E': ('da_storage', 'rt_storage', 'unit', 'E_mwh'), 'Vu': ('da_pumped', 'rt_pumped', 'unit', 'V_up'),
                            'V': ('da_hydro', 'rt_hydro', 'station', 'V_1e4m3')}.get(kind, (None,) * 4)
        if dk is None or R.get(dk) is None or R.get(rk) is None:
            continue
        d = R[dk][(R[dk][idc] == unit) & R[dk].period.isin(rng)]
        r = R[rk][(R[rk][idc] == unit) & R[rk].period.isin(rng)]
        fig = go.Figure()
        fig.add_scatter(x=[lbl[int(t)] for t in d.period], y=d[col], name='日前', line=dict(color=DA_COLOR))
        fig.add_scatter(x=[lbl[int(t)] for t in r.period], y=r[col], name='实时已执行', line=dict(color=RT_COLOR, dash='dash'))
        row = f[f.unit == unit].iloc[0]
        planned_soft = row.get('planned_terminal_soft') is True or str(row.get('planned_terminal_soft')).lower() == 'true'
        bound_label = 'τ 日末计罚目标（非硬等式）' if planned_soft else 'τ 保存的带/目标'
        fig.add_scatter(x=[lbl[tau]] * 2, y=[row.lower, row.upper], mode='lines+markers', name=bound_label, line=dict(color='#e9a03b', width=6))
        show_fig(style_fig(fig, f'{unit} 窗口内状态（黄色：{bound_label}）', '时刻', col, 280), key=f'{key}:{unit}:{t0}')
    st.caption('窗口内曲线为各时段实际执行后的状态（每个窗口只执行首段，后续段来自后续窗口）；本图不把未来状态作为执行；已保存的逐窗口 SCUC/SCED 前瞻数量可在“SCUC/SCED与定价结果”查看，缺失字段不补画。')


def render_aux_vs_dual(R, stage, lbl, key):
    import plotly.express as px
    import streamlit as st
    from common import DIFF_SCALE, show_df, show_fig, style_fig
    a = R.get('price_aux_vs_dual')
    if a is None or a.empty:
        return st.caption('本次未保存辅助定价与对偶价格对照。')
    a = a[a.stage == stage]
    if a.empty:
        return st.caption(f'{stage} 阶段没有辅助定价与对偶价格对照。')
    piv = a.pivot(index='bus', columns='period', values='diff')
    fig = px.imshow(piv.values, x=[lbl[int(t)] for t in piv.columns], y=[str(b) for b in piv.index], aspect='auto',
                    color_continuous_scale=DIFF_SCALE, color_continuous_midpoint=0, labels=dict(color='元/MWh'))
    fig.update_yaxes(type='category', autorange='reversed', dtick=1, tickfont=dict(size=9))
    show_fig(style_fig(fig, '辅助定价原始价 − 正式调度对偶价（元/MWh）', result_time_axis(R), '节点', 460), key=key)
    st.caption(f'差值绝对值：最大 {a["diff"].abs().max():.2f}，平均 {a["diff"].abs().mean():.2f} 元/MWh；'
               f'{int((a["diff"].abs() > 0.01).sum())} / {len(a)} 个 节点×时段 差值超过 0.01。辅助定价只定价、数量不执行。')
    top = a.reindex(a['diff'].abs().sort_values(ascending=False).index).head(10).rename(
        columns={'period': '时段', 'bus': '节点', 'aux_lmp_raw': '辅助定价 元/MWh', 'dual_lmp_raw': '对偶价 元/MWh', 'diff': '差值 元/MWh'})
    show_df(top.drop(columns='stage'), '%.2f')


def render_value_compare(RA, RB, lbl, key, names=('方案甲', '方案乙')):
    """第二轮J项：场景对比页的价值曲线对比（日前价值曲线 + 实时窗口末实际使用值），B−A 差值单列。"""
    import plotly.graph_objects as go
    import streamlit as st
    from common import DA_COLOR, RT_COLOR, show_df, show_fig, style_fig, df_download
    va, vb = RA.get('da_value_curves'), RB.get('da_value_curves')
    if va is None or vb is None or va.empty or vb.empty:
        return st.info('至少一个方案没有保存日前价值曲线（旧结果或未启用价值法），不对比价值曲线，不以零补齐。')
    st.caption(SIGN_NOTE)
    st.caption(f'{names[0]}：{terminal_value_note(RA)}')
    st.caption(f'{names[1]}：{terminal_value_note(RB)}')
    st.caption(f'{names[0]}：{terminal_policy_note(RA.get("rt_value_windows"))}')
    st.caption(f'{names[1]}：{terminal_policy_note(RB.get("rt_value_windows"))}')
    units = [u for u in dict.fromkeys(list(va.unit) + list(vb.unit)) if u in set(va.unit) and u in set(vb.unit)]
    only = sorted(set(va.unit) ^ set(vb.unit))
    if only:
        st.caption('仅一个方案存在的主体不对比：' + '、'.join(only))
    if not units:
        return st.info('两个方案没有共同的价值曲线主体。')
    unit = st.selectbox('价值曲线主体', units, key=key + ':unit')
    wa, wb = RA.get('rt_value_windows'), RB.get('rt_value_windows')
    fig = go.Figure()
    rows = None
    for R, v, w, n, dash in ((RA, va, wa, names[0], 'solid'), (RB, vb, wb, names[1], 'dash')):
        f = v[v.unit == unit].sort_values('period')
        fig.add_scatter(x=[lbl[int(t)] for t in f.period], y=f.value_yuan_per_mwh, name=f'{n} 日前价值',
                        line=dict(color=DA_COLOR, dash=dash))
        if w is not None and not w.empty and (w.unit == unit).any():
            g = w[w.unit == unit].sort_values('tau')
            fig.add_scatter(x=[lbl[int(t)] for t in g.tau], y=g.value_yuan_per_mwh, name=f'{n} 实时窗口末使用值',
                            mode='markers', marker=dict(color=RT_COLOR, symbol='circle' if dash == 'solid' else 'x'))
        s = f.set_index('period').value_yuan_per_mwh.rename(n)
        rows = s.to_frame() if rows is None else rows.join(s, how='outer')
    show_fig(style_fig(fig, f'{unit} 价值曲线对比（{names[0]}实线 / {names[1]}虚线；蓝=日前，红点=实时窗口末使用值）',
                       result_time_axis(RA), '价值（元/MWh）', 360), key=key + ':fig')
    rows[f'{names[1]}−{names[0]}'] = rows[names[1]] - rows[names[0]]
    rows = rows.reset_index().rename(columns={'period': '时段序号'})
    rows.insert(1, '时刻', [lbl[int(t)] for t in rows['时段序号']])
    diff = f'{names[1]}−{names[0]}'
    top = rows.reindex(rows[diff].abs().sort_values(ascending=False).index).head(10)
    st.markdown(f'**价值差最大的 10 个时段（{diff}，元/MWh）**')
    show_df(top, '%.2f')
    summary = []
    for u in units:
        a = va[va.unit == u].set_index('period').value_yuan_per_mwh
        b = vb[vb.unit == u].set_index('period').value_yuan_per_mwh
        d = (b - a).dropna()
        summary.append({'主体': u, f'{names[0]} 均值 元/MWh': a.mean(), f'{names[1]} 均值 元/MWh': b.mean(),
                        '差值均值 元/MWh': d.mean(), '最大绝对差 元/MWh': d.abs().max()})
    show_df(pd.DataFrame(summary), '%.2f')
    pass  # Result download controls removed.
