"""Saved results organized by business question, with technical evidence on demand."""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (DA_COLOR, DIFF_SCALE, LMP_SCALE, LOAD_SCALE, PALETTE, RT_COLOR, TYPE_CN, TYPE_COLOR, TYPE_SYMBOL,
                    all_results, branches_with_rate, bus_resources, congestion_period_count, df_download, empty_state, get_result, line_loading,
                    load_weighted, metric_grid, page_setup, period_labels, result_label, show_df, show_fig, style_fig,
                    topo_layout, wan)
from smm.time_grid import result_time_grid, result_time_axis

page_setup("运行与出清结果")
from result_catalog import is_single_day_result
res = [m for m in all_results() if is_single_day_result(m)]
if not res:
    st.session_state.pop('result_sel', None)
    st.session_state.pop('_result_sel_widget', None)
    empty_state("还没有完成的运行结果。", "请先运行场景。")
    st.page_link("pages/6_单日模拟.py", label="前往单日模拟", icon="▶️")
    st.stop()
from result_navigation import select_saved_result, recorded_number
sel = select_saved_result(res)
selected_meta=next(m for m in res if m["dir"]==sel)
if selected_meta.get("demo_description"):st.info(selected_meta["demo_description"])
from input_version_notice import render_input_version_notice
render_input_version_notice(selected_meta)
try:
    with st.spinner("读取结果文件…"):
        R = get_result(selected_meta)
        from result_navigation import validate_display_result
        validate_display_result(R)
        frozen_buses = Path(R["snapshot_root"]) / "data/case39/buses.csv"
        buses = pd.read_csv(frozen_buses) if frozen_buses.is_file() else None
        ll_da = line_loading(R, "da")
        ll_rt = line_loading(R, "rt") if R['full'] else None
except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
    st.error("本次结果缺失或损坏，暂不能完整显示。原始存档未修改，也未用当前场景或零值补齐。")
    st.info("可在上方“选择结果”切换到其他运行；请核对或恢复这次运行的原始结果文件。")
    with st.expander("读取失败详情"):
        st.code(str(exc))
    st.stop()
for line_warning in R.get('line_scope_warnings', []):
    st.warning(line_warning)
S, DS = R["summary"], R["da_summary"]
FULL = R["full"]
if not R.get("input_provenance_known", False):
    st.warning("历史结果缺少冻结输入：仅使用已保存的限额与参数，缺失项记为未知；缺少冻结网络时不显示历史拓扑；缺少负荷权重时加权价格保留未知，不使用当前基准补齐。")
T_da = int(R["da_nodal_prices"].period.max()) + 1
T_rt = int(R["rt_prices"].period.max()) + 1 if FULL else 0
N, DT = result_time_grid(R)
from forecast_error_view import render_forecast_diagnostics
lbl = period_labels(N, DT)
# ---------------------------------------------------------------------------- 本次启用的阶段与规则（顶部一行）
def frozen_yaml(name):
    try:
        import yaml
        return yaml.safe_load((Path(R["snapshot_root"]) / "config" / name).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}

ALL_MODES = frozen_yaml("modes.yaml")
MODES = ALL_MODES.get(S.get('rule_set'), {})
MODES = MODES if isinstance(MODES, dict) else {}
from result_policy_notes import saved_stage_diagnostics, storage_policy_note, saved_object
DIAGNOSTICS, diagnostic_error = saved_stage_diagnostics(sel)
if diagnostic_error:
    st.warning('本次阶段诊断缺失或损坏，求解阶段质量暂不能完整核对；已保存的出力、价格和结算仍可查看。' + diagnostic_error)
UY = frozen_yaml("units.yaml")
RTS = (S.get("realtime") or {}) if FULL else {}
IDS = (S.get("intraday") or {}) if FULL else {}
ID_TABLE = R.get("id_rounds_summary")
INTRADAY = bool(FULL and (IDS.get("enabled") or IDS.get("failed_rounds") or DIAGNOSTICS.get("id_rounds") or (ID_TABLE is not None and len(ID_TABLE))))

if INTRADAY and ID_TABLE is not None and 'gap_target_met' in ID_TABLE:
    gap_misses = ID_TABLE[ID_TABLE.gap_target_met.eq(False)]
    if not gap_misses.empty:
        names = '、'.join(gap_misses['round'].astype(str)) if 'round' in gap_misses else '见日内逐轮记录'
        st.warning(f'日内 {len(gap_misses)} 轮已取得可行计划，但未达到目标 MIP 精度（{names}）；不是物理不可行回退，详见“日内轮次与交接”。')


def saved_reserve_rule_label(stage=None):
    """Describe the saved run only; absent mode is the historical formula."""
    da_rule = DS.get('reserve_rule') or {}
    full_rule = (S.get('reserve') or {}) if FULL else {}
    rule = da_rule if stage == 'da' or not FULL else full_rule
    if not rule and FULL:
        rule = full_rule  # Full-run summary records the rule shared by its stages.
    if not isinstance(rule, dict):
        return '上备用规则未记录'
    mode = rule.get('up_requirement_mode') or 'formula'
    if mode == 'fixed':
        value = rule.get('fixed_up_mw')
        if isinstance(value, (int, float)) and not isinstance(value, bool) and np.isfinite(value) and value >= 0:
            return f'固定总上备用 {value:g} MW'
        return '固定总上备用（数值未记录，不能核验）'
    if mode == 'formula':
        prefix = '历史' if not rule.get('up_requirement_mode') else ''
        return prefix + '比例＋事故项公式（需求以本次已保存结果为准）'
    return '上备用规则未识别（需求以本次已保存结果为准）'


def rules_line():
    lim = S.get("price_limits") or MODES.get("price_limits") or {}
    floor = (f"{lim['clear_min']:g}~{lim['clear_max']:g} 元/MWh" if isinstance(lim.get('clear_min'), (int, float))
             and isinstance(lim.get('clear_max'), (int, float)) else '未记录')
    pricing = {'bounded_auxiliary_pricing': '辅助定价（±3%带，独立罚因子）', 'sced_dual': 'SCED对偶'}.get(DS.get('pricing_source'), DS.get('pricing_source') or '未记录')
    gap = DS.get('scuc_gap')
    parts = [f"**日前** SCUC→SCED，定价 {pricing}，MIP间隙 {'未记录' if gap is None else f'{gap:.2%}'}"]
    if FULL:
        parts.append(f"**日内滚动** {'启用 '+str(IDS.get('rounds'))+' 轮（每 '+str(IDS.get('interval_h'))+' h）' if IDS.get('enabled') else '未启用'}")
        vm = RTS.get('value_method') or {}
        smode = RTS.get('storage_mode')
        if smode == 'value':
            sto = f"储能/抽蓄 价值法 δ0={vm.get('band_fraction')}（假设值）"
        elif smode == 'sced_anchor':
            sto = f"储能/抽蓄 锚点 {RTS.get('anchor')}" + (f" x={RTS.get('x')}" if RTS.get('anchor') == 'window_end_pct' else '')
        else:
            sto = {'fixed_da': '储能/抽蓄 执行日前曲线'}.get(smode, f"储能模式 {smode or '未记录'}")
        hyd = RTS.get('hydro')
        hyd_txt = f"水电 {'价值法 δ0='+str(vm.get('hydro_band_fraction'))+'（假设值）' if hyd == 'value' else (hyd or '未记录')}"
        parts.append(f"**实时** {DT * 60:g} min 滚动、{RTS.get('lookahead_h', '?')} h 前瞻，{sto}，{hyd_txt}，硬→软兜底 {RTS.get('fallback_count', '未记录')} 个窗口")
    parts.append(saved_reserve_rule_label())
    sres = (S.get('reserve') or {}).get('storage_reserve_enabled', (MODES.get('reserve') or {}).get('storage_reserve'))
    parts.append(f"限价 {floor}" + ("（川2026总体方案 五（二）2：下限−50）" if lim.get('clear_min') == -50 else "") +
                 f"；储能/抽蓄备用 {'开' if sres else '关' if sres is not None else '未记录'}；下备用 {'开' if (S.get('reserve') or {}).get('down_enabled') else '关'}")
    if FULL:
        parts.append("结算 两部制（日前电量×日前价 + 偏差×实时价）")
    return '　|　'.join(parts)

with st.expander('本次规则与方法', expanded=False):
    st.markdown(rules_line())
    st.caption(f'结果目录：`{sel}`')


def recorded_tracking(unit, kind):
    path = Path(sel) / 'rt_tracking_windows.csv'
    if not path.exists():return pd.DataFrame()
    try:frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:return pd.DataFrame()
    if not {'unit','kind','period','target_lower','target_upper'} <= set(frame):return pd.DataFrame()
    return frame[(frame.unit == unit) & (frame.kind == kind)].drop_duplicates(['period','target_lower','target_upper'])

def add_tracking_markers(fig, unit, kind, *, axis=None):
    frame = recorded_tracking(unit, kind)
    if frame.empty:return False
    for bound, label, symbol in [('target_lower','下目标','triangle-up'),('target_upper','上目标','triangle-down')]:
        fig.add_scatter(x=[lbl[int(t)] for t in frame.period], y=frame[bound], mode='markers',
                        yaxis=axis, name='已记录前瞻跟踪'+label+'（非已执行末值）', marker=dict(symbol=symbol,size=6))
    return True

PRICE = "结算电价（元/MWh）"
TIME = result_time_axis(R)
from curtailment_views import curtailment_report, curtailment_overview, curtailment_dashboard
curtailment = curtailment_report(R)
from result_navigation import build_result_navigation, comparison_frame
from unit_overview import render_unit_overview
from result_story_view import render_credibility
try:
    render_credibility(R)
except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
    st.warning('可信度条暂不能汇总，其余已保存结果仍可查看。' + str(exc))
navigation = build_result_navigation(S, DS, directory=sel, full=FULL, intraday=INTRADAY)
with navigation['diagnostic'][0]:
    from hydro_daily_energy_views import render_hydro_daily_energy
    render_hydro_daily_energy(R)
from result_economics_view import prepare_economics
ECONOMICS = prepare_economics(R)
main_tabs = navigation['main']
advanced = navigation['advanced']
SNAME = {'da': '日前', 'rt': '实时'}


def stage_tables(k):
    rt = k == 'rt'
    return {'units': R.get('rt_units') if rt else R.get('da_unit_awards'),
            'prices': R.get('rt_prices') if rt else R.get('da_nodal_prices'),
            'storage': R.get('rt_storage') if rt else R.get('da_storage'),
            'pumped': R.get('rt_pumped') if rt else R.get('da_pumped'),
            'hydro': R.get('rt_hydro') if rt else R.get('da_hydro'),
            'reserve': R.get('rt_reserve') if rt else R.get('da_reserve'),
            'regulation': R.get('rt_regulation') if rt else R.get('da_regulation_indicative'),
            'regulation_system': R.get('rt_regulation_system') if rt else R.get('da_regulation_system_indicative'),
            'system': R.get('rt_system') if rt else R.get('da_system')}


def price_matrix(k):
    frame = R['rt_prices'] if k == 'rt' else R['da_nodal_prices']
    return frame.pivot(index='bus', columns='period', values='lmp')


# ============================================================================ 1 关键指标
def page_kpi(k):
    cong_da = congestion_period_count(ll_da)
    cong_rt = congestion_period_count(ll_rt)
    if k == 'da':
        if not FULL:
            metric_grid([('日前负荷电量加权结算价 · 元/MWh', recorded_number(DS.get('lmp_load_weighted_avg'))),
                         ('日前中标电量 · MWh',f"{R['da_unit_awards'].energy_mwh.sum():,.1f}"),
                         ('日前中标收入 · 万元',wan(R['da_unit_awards'].revenue_yuan.sum()))])
            curtailment_overview(curtailment)
        with st.expander('日前完整运行指标与计算记录', expanded=False):
            metric_grid([
                ("日前负荷电量加权均价（元/MWh）", recorded_number(DS.get('lmp_load_weighted_avg'))),
                ("节点电价 最低 / 最高（元/MWh）", ' / '.join(recorded_number(DS.get(x), '.0f') for x in ('lmp_min','lmp_max'))),
                ("日前调度目标（含罚，万元）", wan(DS.get("objective")), None, "正式能量调度目标：能量报价、启停空载及软约束罚项，不是社会真实成本"),
                ("启动费（万元）", wan(DS.get("startup_cost"), 2)),
                ("日前弃电（MWh）", '未记录' if DS.get('curtailment_mwh') is None else f"{DS['curtailment_mwh']:,.0f}"),
                ("日前阻塞时段", "—" if cong_da is None else f"{cong_da} / {T_da}"),
                ("日前用时（s）", recorded_number(DS['scuc_time_s'] + DS['sced_time_s'], '.0f') if DS.get('scuc_time_s') is not None and DS.get('sced_time_s') is not None else '未记录'),
            ], per_row=4)
        lw = load_weighted(R["da_nodal_prices"], buses)
        fig = go.Figure()
        fig.add_scatter(x=[lbl[t] for t in lw.index], y=lw.values, name="日前", line=dict(color=DA_COLOR, width=2.2))
        show_fig(style_fig(fig, "日前每时段节点负荷加权平均电价", TIME, PRICE, 340), key='da_kpi_lw')
        cl = pd.DataFrame(DS.get("congested_lines", []))
        with st.expander(f"阻塞线路（日前）· {len(cl)} 条", expanded=False):
            if len(cl):
                if "max_loading" in cl:
                    cl["max_loading"] = cl.max_loading * 100
                show_df(cl.rename(columns={"line": "线路", "periods": "阻塞时段数", "max_loading": "最大负载率 %",
                                           "max_mu": "最大影子价格 元/MWh"}), "%.1f")
            else:
                empty_state("日前无阻塞线路。", icon="✅")
        return
    complete_day = (S.get('completed_scope') == 'full_day' and S.get('executed_periods') == S.get('expected_periods') == T_rt)
    da_peak = R['da_system'].load_mw.max() if R.get('da_system') is not None else None
    rt_peak = R['rt_system'].load_mw.max() if R.get('rt_system') is not None else None
    def peak_label(value):
        return '未记录' if value is None or not np.isfinite(value) else f'{value:,.1f}'
    sp = S.get("spread") or {}
    metric_grid([
        ('日前 / 实时负荷电量加权价 · 元/MWh',' / '.join(recorded_number(sp.get(x)) for x in ('da_lw_avg','rt_lw_avg'))),
        ('日前 / 已执行实际负荷峰值 · MW',peak_label(da_peak)+' / '+peak_label(rt_peak)),
        ('实时执行范围',f"{T_rt} / {S.get('expected_periods', '未记录')} 段",None,'完整日' if complete_day else '仅已保存范围，不外推为全天'),
        ("逐时价差算术均值（元/MWh）", recorded_number(sp.get('lw_spread_mean'), '+.1f'), "绝对均值 " + recorded_number(sp.get('lw_spread_abs_mean')), "每个时段先算节点负荷加权价差（实时−日前），再按时段等权平均；不等于两阶段负荷电量加权均价相减。delta 为逐时价差绝对值的算术均值"),
    ], per_row=4)
    st.caption('两阶段电量加权均价均按本次已执行实时对应范围统计，不将截短运行当成全天。')
    with st.expander('实时完整运行指标', expanded=False):
        metric_grid([
            ("实时弃电（MWh）", recorded_number(RTS.get('curtail_mwh'), ',.0f')),
            ("实时阻塞时段", f"{int(cong_rt) if np.isfinite(cong_rt) else '未知'} / {T_rt}"),
            ("硬→软兜底窗口数", str(RTS.get('fallback_count', '未记录')), None, "价值法偏差带/日末/锚点硬约束不可行时改软约束的窗口数（记录在 rt_fallbacks.csv）"),
            ("新能源+负荷偏差损益（万元）", wan((S.get("forecast_error") or {}).get("dev_pnl")), None, "(实际−日前)×(实时价−日前价) 汇总；负值为偏差损失"),
            ("实时用时（s）", recorded_number((S.get('time_s') or {}).get('realtime'), '.0f')),
            ("实时节点价最高（元/MWh）", recorded_number(sp.get('rt_lmp_max'), '.1f')),
        ], per_row=3)
    lw = load_weighted(R["rt_prices"], buses)
    fig = go.Figure()
    fig.add_scatter(x=[lbl[t] for t in lw.index], y=lw.values, name="实时", line=dict(color=RT_COLOR, width=2, dash="dash"))
    show_fig(style_fig(fig, "实时每时段节点负荷加权平均电价", TIME, PRICE, 340), key='rt_kpi_lw')
    if S.get("checks"):
        with st.expander("一致性核查与技术记录"):
            check_names={'physical_feasible_checked_constraints':'已检查的物理约束通过','network_feasible':'线路潮流检查通过',
                         'hydro_window_constraints_satisfied':'水量与库容约束检查通过','rules_satisfied_no_slack':'全部窗口无松弛或软跟踪偏差',
                         'da_line_overload_count':'日前越限记录数','rt_line_overload_count':'实时越限记录数'}
            show_df(pd.DataFrame([{'检查项目':v,'保存结果':('是' if S['checks'].get(c) is True else '否' if S['checks'].get(c) is False else '未记录' if S['checks'].get(c) is None else str(S['checks'][c]))} for c,v in check_names.items()]))
            pass  # Result download controls removed.


# ============================================================================ 2 节点电价
def page_prices(k):
    from value_views import render_aux_vs_dual
    name = SNAME[k]
    mat = price_matrix(k)
    lo, hi = float(np.nanmin(mat.values)), float(np.nanmax(mat.values))
    fig = px.imshow(mat.values, x=[lbl[t] for t in mat.columns], y=[str(b) for b in mat.index], aspect="auto",
                    color_continuous_scale=LMP_SCALE, zmin=lo, zmax=hi, labels=dict(x=TIME, y="节点编号", color="元/MWh"))
    fig.update_coloraxes(colorbar_title_text="节点电价<br>元/MWh")
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(tickfont=dict(size=10), dtick=1, autorange="reversed", type="category")
    show_fig(style_fig(fig, f"{name}节点电价热力图（{mat.shape[0]} 节点 × {mat.shape[1]} 时段）", TIME, "节点编号", 640), key=f'{k}_heat')
    default_b = [b for b in [16, 19, 25, 30] if b in mat.index] or list(mat.index[:3])
    bsel = st.multiselect(f"{name}节点电价曲线", list(mat.index), default=default_b, format_func=lambda b: f"节点{b}", key=f'{k}_buses')
    if bsel:
        fig = go.Figure()
        for i, b in enumerate(bsel):
            fig.add_scatter(x=[lbl[t] for t in mat.columns], y=mat.loc[b], name=f"节点{b}", line=dict(color=PALETTE[i % len(PALETTE)], dash='dash' if k == 'rt' else None))
        show_fig(style_fig(fig, f"{name}选中节点电价曲线", TIME, PRICE, 380), key=f'{k}_bus_curves')
    else:
        empty_state("请选择至少一个节点。", icon="👆")
    if k == 'da':
        with st.expander("电价分解（日前）：原始节点电价 = 能量分量 + 阻塞分量；结算价另列", expanded=False):
            dd = R["da_nodal_prices"][R["da_nodal_prices"].bus.isin(bsel or default_b)][["period", "time", "bus", "lmp_raw", "lmp", "energy", "congestion"]]
            dd = dd.rename(columns={"period": "时段", "time": "时刻", "bus": "节点", "lmp_raw": "原始节点电价 元/MWh", "lmp": "限价后结算价 元/MWh", "energy": "能量分量 元/MWh",
                                    "congestion": "阻塞分量 元/MWh"})
            show_df(dd, "%.2f", height=300)
            pass  # Result download controls removed.
    with st.expander('对偶与辅助定价对比'):
        render_aux_vs_dual(R, 'DA' if k == 'da' else 'RT', lbl, key=f'{k}_auxdual')
    st.subheader(f'{name}结算电价明细')
    market_prices = R['rt_prices'] if k == 'rt' else R['da_nodal_prices']
    business_prices = market_prices[[c for c in ('period','time','bus','lmp') if c in market_prices]].copy()
    show_df(business_prices,'%.2f',height=280)
    pass  # Result download controls removed.


# ============================================================================ 3 发电结构与机组出力
def page_generation(k, *, compact=False):
    name = SNAME[k]
    ua = R["da_unit_awards"]
    if k == 'da':
        u = ua
    else:
        u = R["rt_units"]
        if (ua.type == "tie").any() and not (u.type == "tie").any():
            st.warning("该历史实时出力表未保存区外来电；下图只汇总已保存主体，不用日前计划补作实时执行量。全网供需请结合保存的系统表核对。")
    u = u.copy()
    u["typ"] = np.where(u.type == "storage", np.where(u.P_mw >= 0, "storage_dis", "storage_ch"),
                        np.where(u.type == "pumped", np.where(u.P_mw >= 0, "pumped_gen", "pumped_pump"), u.type))
    g = u.groupby(["period", "typ"]).P_mw.sum().unstack(fill_value=0)
    order = [t for t in ["tie", "coal", "hydro", "gas_ccgt", "gas_gt", "wind", "pv", "storage_dis", "pumped_gen", "storage_ch", "pumped_pump"] if t in g]
    fig = go.Figure()
    x = [lbl[t] for t in g.index]
    for t in order:
        fig.add_scatter(x=x, y=g[t].clip(lower=0) if t not in ("storage_ch", "pumped_pump") else g[t], name=TYPE_CN.get(t, t),
                        stackgroup="neg" if t in ("storage_ch", "pumped_pump") else "pos", mode="lines",
                        line=dict(width=0.5, color=TYPE_COLOR.get(t)), fillcolor=TYPE_COLOR.get(t))
    system = R.get("da_system") if k == 'da' else R.get("rt_system")
    if system is not None:
        fig.add_scatter(x=[lbl[t] for t in system.period], y=system.load_mw, name="负荷" if k == 'da' else "实际负荷", line=dict(color="#111", width=2))
    show_fig(style_fig(fig, f"{name}分类型出力堆叠（储能充电、抽蓄抽水为负）", TIME, "出力（MW）", 460, legend="资源类型"), key=f'{k}_stack')
    from result_comparison import type_energy_comparison
    energy = type_energy_comparison(R)
    energy['type'] = energy.type.map(lambda t: TYPE_CN.get(t, t))
    st.subheader('分类型电量 · 日前 / 实时')
    st.caption('按相同已执行时段比较；充电、抽水为负。空白表示未记录，不按零处理。' if FULL else '仅日前计划；实时尚未执行。')
    show_df(energy.rename(columns={'type': '资源类型'}), '%.2f')
    if compact:
        with st.expander('全部机组出力明细'):
            from dispatch_detail import dispatch_detail_table
            dispatch_detail_table(u, name, result_key=sel, period_hours=DT, prices=R['rt_prices' if k == 'rt' else 'da_nodal_prices'])
        return
    usel = st.multiselect(f"{name}单机出力曲线" + ("（细灰线 = 日前参照）" if k == 'rt' else ""), sorted(ua.unit.unique()),
                          default=[x for x in ["C1", "CCGT", "GT", "W1"] if x in set(ua.unit)], key=f'{k}_units')
    if usel:
        fig = go.Figure()
        for i, un in enumerate(usel):
            col = PALETTE[i % len(PALETTE)]
            a = u[u.unit == un]
            fig.add_scatter(x=[lbl[t] for t in a.period], y=a.P_mw, name=f"{un} {name}", line=dict(color=col, dash='dash' if k == 'rt' else None), legendgroup=un)
            if k == 'rt':
                b = ua[(ua.unit == un) & ua.period.isin(a.period)]
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.P_mw, name=f"{un} 日前参照", line=dict(color=col, width=1), opacity=0.5, legendgroup=un)
        show_fig(style_fig(fig, f"{name}单机出力曲线", TIME, "出力（MW）", 360), key=f'{k}_unit_curves')
    else:
        empty_state("请选择至少一台机组。", icon="👆")
    from dispatch_detail import dispatch_detail_table
    dispatch_detail_table(u, name, result_key=sel, period_hours=DT, prices=R['rt_prices' if k == 'rt' else 'da_nodal_prices'])


# ============================================================================ 4 储能·抽蓄·水电
def page_storage(k):
    from value_views import render_da_values, render_rt_values
    name = SNAME[k]
    rt = k == 'rt'
    anchor = RTS.get("anchor") if FULL else None
    smode = RTS.get("storage_mode") if FULL else None
    xx = RTS.get("x", 0.05) if FULL else 0
    ecap = {u["id"]: u for u in (UY.get("storage") or [])}
    st.caption("横轴标示本次冻结步长的时段起点；电量/库容曲线是相应时段结束状态，功率为该时段值。")
    if rt:
        tracking = RTS.get("tracking_policy") or {}
        if RTS.get('hydro') == 'window_end_da' and tracking.get("mode") == "soft_deliverable_energy":
            st.caption(f"实时水电跟踪为带罚软目标：{tracking.get('penalty_yuan_per_discharge_mwh', '未知')} 元/可交付放电 MWh；罚项属于前瞻窗口调度目标，不是已执行结算收费。")
        st.info(storage_policy_note(R, MODES), icon='🔋')
    st.subheader(f"{name}储能")
    sd = R["rt_storage"] if rt else R["da_storage"]
    sda = R["da_storage"]
    if sd is None or not len(sd):
        empty_state("该结果没有储能数据。")
    for i, un in enumerate(sd.unit.unique() if sd is not None else []):
        a = sd[sd.unit == un]
        info = ecap.get(un, {})
        with st.expander(f"{un}（节点 {info.get('bus', '?')}，{info.get('pmax', '?')} MW / {info.get('ecap', '?')} MWh）", expanded=i == 0):
            fig = go.Figure()
            x = [lbl[t] for t in a.period]
            if {'P_dis', 'P_ch'} <= set(a):
                fig.add_bar(x=x, y=a.P_dis - a.P_ch, name=f"{name} 放电−充电（MW，右轴）", marker_color="#9ca3af", yaxis="y2", opacity=0.45)
            fig.add_scatter(x=x, y=a.E_mwh, name=f"{name} 电量", line=dict(color=RT_COLOR if rt else DA_COLOR, dash='dash' if rt else None, width=2))
            if rt and sda is not None:
                b = sda[(sda.unit == un) & sda.period.isin(a.period)]
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.E_mwh, name="日前 电量（参照）", line=dict(color=DA_COLOR, width=1))
                if smode == 'sced_anchor' and anchor in ("hourly_eq", "two_hour_eq"):
                    h = b[(b.period + 1) % (8 if anchor == "two_hour_eq" else 4) == 0]
                    fig.add_scatter(x=[lbl[t] for t in h.period], y=h.E_mwh, mode="markers", name="日前锚点",
                                    marker=dict(symbol="diamond", size=7, color=DA_COLOR))
                elif smode == 'sced_anchor':
                    fig.add_scatter(x=[lbl[t] for t in b.period], y=b.E_mwh * (1 + xx), line=dict(width=0), showlegend=False, hoverinfo="skip")
                    fig.add_scatter(x=[lbl[t] for t in b.period], y=b.E_mwh * (1 - xx), fill="tonexty", line=dict(width=0), name=f"±{xx:.0%} 带",
                                    fillcolor="rgba(31,95,168,0.15)")
            if info:
                for v, nm in ((info["soc_min"] * info["ecap"], "荷电状态下限"), (info["soc_max"] * info["ecap"], "荷电状态上限")):
                    fig.add_hline(y=v, line_dash="dot", line_color="#6b7280", annotation_text=nm, annotation_position="top left")
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="充放电功率（MW）", showgrid=False))
            show_fig(style_fig(fig, f"{un} {name}电量与充放电", TIME, "电量（MWh）", 320), key=f"{k}_st_{un}")

    st.subheader(f"{name}抽水蓄能")
    st.caption("库容为各时段结束值；横轴为该时段起点。最后一段对应日末终态，不回绕至初态。")
    pcfg = {u["id"]: u for u in (UY.get("pumped") or []) if u.get("enabled", True)}
    pdd = R.get("rt_pumped") if rt else R.get("da_pumped")
    if pdd is None or not len(pdd):
        empty_state("该结果没有抽蓄数据（旧结果或抽蓄未启用）。", "可在“报价模拟 → 编辑或新增机组”核对抽蓄参数；不要用当前配置补造旧结果。")
    else:
        PSs = S.get("pumped") or {}
        for un in pdd.unit.unique():
            a = pdd[pdd.unit == un]
            info = pcfg.get(un, {})
            ps = PSs.get(un) or {}
            dsum = (DS.get("pumped") or {}).get(un, {})
            head = (f"{un}（节点 {info.get('bus', '?')}，{info.get('n_units', '?')}×{info.get('unit_gen_max', '?')} MW，"
                    + ("仅日前计划；未包含实时执行）" if not FULL else f"{name}，规则同储能）"))
            st.markdown(f"**{head}**")
            if not rt:
                items = [("日前 发电 / 抽水（MWh）", ' / '.join(recorded_number(dsum.get(c), ',.0f') for c in ('gen_mwh','pump_mwh'))),
                         ("日前 发电 / 抽水小时", ' / '.join(recorded_number(dsum.get(c), '.2f') for c in ('gen_hours','pump_hours'))),
                         ("循环效率（日前）", "—" if dsum.get("cycle_eff") is None else f"{dsum['cycle_eff']:.1%}"),
                         ("日前 发电均价 / 抽水均价（元/MWh）", ' / '.join(recorded_number(dsum.get(c), '.0f') for c in ('avg_gen_price','avg_pump_price')))]
            elif ps.get('rt'):
                items = [("实时 发电 / 抽水（MWh）", ' / '.join(recorded_number(ps['rt'].get(c), ',.0f') for c in ('gen_mwh', 'pump_mwh'))),
                         ("电能量结算净收入（万元）", wan(ps.get("energy_net_revenue")), None, "日前 + 实时偏差两部制结算，发电收入 − 抽水电费"),
                         ("实时 已执行末时段上库库容（万m³）", recorded_number(ps['rt'].get('V_up_end'), '.0f'))]
            else:
                items = []
            if items:
                metric_grid(items, per_row=4)
            x = [lbl[t] for t in a.period]
            fig = go.Figure()
            fig.add_bar(x=x, y=a.P_gen, name=f"{name} 发电 MW", marker_color=TYPE_COLOR["pumped_gen"])
            fig.add_bar(x=x, y=-a.P_pump, name=f"{name} 抽水 MW（负）", marker_color=TYPE_COLOR["pumped_pump"])
            fig.update_layout(barmode="relative")
            show_fig(style_fig(fig, f"{un} {name}发电 / 抽水功率", TIME, "功率（MW，抽水为负）", 300), key=f"{k}_ps_p_{un}")
            fig = go.Figure()
            fig.add_scatter(x=x, y=a.V_up, name=f"{name} 上库库容", line=dict(color=RT_COLOR if rt else DA_COLOR, dash='dash' if rt else None, width=2))
            if rt and R.get('da_pumped') is not None:
                b = R['da_pumped'][(R['da_pumped'].unit == un) & R['da_pumped'].period.isin(a.period)]
                fig.add_scatter(x=[lbl[t] for t in b.period], y=b.V_up, name="日前 上库库容（参照）", line=dict(color=DA_COLOR, width=1))
                if smode == 'sced_anchor' and not add_tracking_markers(fig, un, 'Vu'):
                    st.caption('抽蓄前瞻跟踪目标：未记录（未保存逐窗口目标，不从当前配置反推）。')
            if info:
                for v, nm in ((max(info["vup_min"],info["v_total"]-info["vdn_max"]), "有效上库下限"), (min(info["vup_max"],info["v_total"]-info["vdn_min"]), "有效上库上限")):
                    fig.add_hline(y=v, line_dash="dot", line_color="#6b7280", annotation_text=nm, annotation_position="top left")
            show_fig(style_fig(fig, f"{un} 上库库容（万m³）", TIME, "库容（万m³）", 300), key=f"{k}_ps_v_{un}")

    st.subheader(f"{name}水电")
    hd = R.get('rt_hydro') if rt else R.get("da_hydro")
    if hd is None or not len(hd):
        empty_state("该结果没有水电数据。")
    else:
        hcfg = {u["id"]: u for u in (UY.get("hydro") or [])}
        idcol = "station" if "station" in hd else "unit"
        vc = "V_1e4m3" if "V_1e4m3" in hd else ("V" if "V" in hd else None)
        for hn in hd[idcol].unique():
            a = hd[hd[idcol] == hn]
            hspec = hcfg.get(hn, {})
            reg = hspec.get("regulation", "reservoir")
            fig = go.Figure()
            pc = "P_mw" if "P_mw" in a else a.columns[2]
            fig.add_scatter(x=[lbl[int(t)] for t in a.period], y=a[pc], name=f"{name} 出力 MW", line=dict(color=TYPE_COLOR["hydro"]))
            if vc:
                fig.add_scatter(x=[lbl[int(t)] for t in a.period], y=a[vc], name=f"{name} 库容 万m³", yaxis="y2", line=dict(color=RT_COLOR if rt else "#6a4c93", dash="dot"))
            if rt and R.get('da_hydro') is not None and vc in R['da_hydro']:
                dh = R['da_hydro']
                b = dh[(dh[idcol] == hn) & dh.period.isin(a.period)]
                fig.add_scatter(x=[lbl[int(t)] for t in b.period], y=b[vc], name="日前 库容 万m³（参照）", yaxis="y2", line=dict(color=DA_COLOR, width=1))
            season_spec = hspec.get('seasons', {}).get(S.get('season', DS.get('season')), {})
            if season_spec:
                lo, hi = season_spec.get('vmin'), season_spec.get('vmax')
                if reg == 'run_of_river':
                    hi = season_spec.get('v0'); lo = None if hi is None else hi - float(hspec.get('pondage', 0) or 0)
                for value, nm in ((lo, '物理库容下限（冻结输入）'), (hi, '物理库容上限（冻结输入）')):
                    if value is not None:
                        fig.add_scatter(x=[lbl[int(a.period.min())], lbl[int(a.period.max())]], y=[value, value], yaxis='y2',
                                        name=nm, mode='lines', line=dict(dash='dot', color='#888'))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="库容（万m³）", showgrid=False))
            show_fig(style_fig(fig, f"{hn}（{'库容型' if reg == 'reservoir' else '径流式'}）{name}出力与库容", TIME, "出力（MW）", 320), key=f"{k}_hy_{hn}")
            qcol = next((c for c in ('qout_m3s', 'Q_out') if c in a), None)
            if qcol:
                wf = go.Figure()
                wf.add_scatter(x=[lbl[int(t)] for t in a.period], y=a[qcol], name=name + '总下泄', mode='lines')
                if hspec.get('qout_max') is not None:
                    wf.add_hline(y=float(hspec['qout_max']), line_dash='dot', annotation_text='下泄上限（冻结研究参数）')
                if season_spec.get('qeco') is not None:
                    wf.add_hline(y=float(season_spec['qeco']), line_dash='dot', annotation_text='生态最小下泄')
                show_fig(style_fig(wf, f'{hn} {name}总下泄（发电用水 + 弃水）', TIME, '流量（m³/s）', 260), key=f'{k}_hy_q_{hn}')
            if hspec.get('qout_max') is None:
                st.caption(f'{hn} 总下泄工程上限未知/本次未声明；不以装机功率反推。')

    st.subheader(f"{name}价值曲线")
    if rt:
        render_rt_values(R, lbl, key='rt_values')
    else:
        render_da_values(R, lbl, key='da_values')


# ============================================================================ 5 备用
def page_reserve(k):
    from market_views import reserve_dashboard
    st.caption(saved_reserve_rule_label(k) + '。图表直接使用本次已保存的需求，不套用当前设置。')
    rt = k == 'rt'
    system = R.get('rt_reserve_system') if rt else R.get('da_reserve_system')
    if system is not None and len(system):
        cols = [c for c in system if c not in ('period', 'time')]
        need = next((c for c in ('R_up_req', 'req_up_mw', 'R_up_required') if c in system), None)
        supply = next((c for c in ('R_up_supply', 'R_up', 'R_up_mw', 'up_mw', 'R_up_total') if c in system), None)
        short = next((c for c in ('R_up_short', 'up_short_mw') if c in system), None)
        fig = go.Figure()
        x = [lbl[int(t)] for t in system.period]
        for c, nm, col in ((need, '向上备用需求 MW', '#111'), (supply, '向上备用提供 MW', DA_COLOR if not rt else RT_COLOR)):
            if c:
                fig.add_scatter(x=x, y=system[c], name=nm, line=dict(color=col))
        if short:
            fig.add_bar(x=x, y=system[short], name='备用缺额 MW', marker_color='#d62828')
        if fig.data:
            show_fig(style_fig(fig, f'{SNAME[k]}系统向上备用', TIME, 'MW', 320), key=f'{k}_res_sys')
        if short:
            n = int((system[short] > 1e-6).sum())
            st.caption(f"备用缺额：{n} 个时段，合计 {system[short].clip(lower=0).sum() * DT:.2f} MWh。罚因子以本次冻结配置为准，不是备用结算付款。")
        with st.expander('系统备用原始表'):
            show_df(system[['period'] + [c for c in cols if c != 'period']], '%.2f', height=260)
    reserve_dashboard(stage_tables(k), prefix='result_reserve:' + sel + k, indicative=not rt)


# ============================================================================ 6 电网与阻塞
def page_network(k):
    from network_view import render_result_network
    name = SNAME[k]
    st.subheader(f'{name}系统接线图')
    render_result_network(R, prefix=f'result_network:{k}:' + sel, stages=(k,))
    with st.expander('线路负载、阻塞与潮流明细'):
        st.subheader(f'{name}线路负载与阻塞')
        ll = ll_da if k == 'da' else ll_rt
        if ll is None:
            empty_state("该结果没有线路潮流文件。", "命令行全流程旧输出不含 da_line_flows.csv；请在网页“运行”页重新运行。")
        else:
            mat = ll.pivot(index="line", columns="period", values="loading")
            top = mat.max(axis=1).sort_values(ascending=False)
            mat = mat.loc[top.index]
            nshow = st.slider("热力图显示负载最高的线路数", 10, len(mat), min(20, len(mat)), 5, key=f'{k}_nlines')
            m2 = mat.iloc[:nshow]
            fig = px.imshow(m2.values * 100, x=[lbl[t] for t in m2.columns], y=list(m2.index), aspect="auto", zmin=0, zmax=100,
                            color_continuous_scale=LOAD_SCALE, labels=dict(color="负载率 %", x=TIME, y="线路"))
            fig.update_coloraxes(colorbar_title_text="负载率<br>%")
            fig.update_layout(hovermode="closest")
            show_fig(style_fig(fig, f"{name}线路负载率热力图（按最大负载率排序，前 {nshow} 条）", TIME, "线路", max(360, 22 * nshow + 120)), key=f'{k}_line_heat')
            stat = ll.groupby("line").agg(最大负载率=("loading", "max"), 平均负载率=("loading", "mean"), 阻塞时段数=("congested", "sum"),
                                          最大影子价格=("mu", "max"), 限额MW=("rate_eff", "first")).sort_values("最大负载率", ascending=False)
            stat = stat.reset_index().rename(columns={"line": "线路", "最大影子价格": "最大影子价格 元/MWh", "限额MW": "限额 MW"})
            stat["最大负载率"] *= 100
            stat["平均负载率"] *= 100
            stat = stat.rename(columns={"最大负载率": "最大负载率 %", "平均负载率": "平均负载率 %"})
            with st.expander("线路统计表", expanded=False):
                show_df(stat, "%.1f", height=360)
            lsel = st.multiselect("线路潮流曲线（虚线 = ±限额）", list(top.index), default=list(top.index[:3]), key=f'{k}_lines')
            if lsel:
                fig = go.Figure()
                for i, ln in enumerate(lsel):
                    col = PALETTE[i % len(PALETTE)]
                    a = ll[ll.line == ln]
                    fig.add_scatter(x=[lbl[t] for t in a.period], y=a.flow_mw, name=ln, line=dict(color=col))
                    fig.add_hline(y=a.rate_eff.iloc[0], line_dash="dot", line_color=col)
                    fig.add_hline(y=-a.rate_eff.iloc[0], line_dash="dot", line_color=col)
                show_fig(style_fig(fig, f"{name}线路潮流", TIME, "潮流（MW）", 360, legend="线路"), key=f'{k}_line_flows')


# ============================================================================ 7 弃风弃光
def page_curtailment(k):
    curtailment_dashboard(curtailment, stage=SNAME[k], key_prefix=f'{k}_curt:')


# ============================================================================ 8 该阶段诊断
def page_diagnostics(k):
    from market_views import unit_dashboard, stages_dashboard, frozen_bids, system_metrics, frozen_units
    import json
    rt = k == 'rt'
    tables = stage_tables(k)
    diagnostics = DIAGNOSTICS
    if not rt:
        gap = DS.get('scuc_gap')
        st.caption('日前混合整数相对间隙（目标精度见本次冻结配置与质量诊断）：' + ('未记录' if gap is None else f'{gap:.2%}') +
                   '；日前调度目标（含罚）：' + wan(DS.get('objective')) + ' 万元')
        stages_dashboard(diagnostics.get('da'), prefix='result_da_stage:' + sel)
        return
    from smm.live_runner import read_live
    fb = R.get('rt_fallbacks')
    st.caption(f"硬→软兜底：{'未记录' if fb is None else len(fb)} 条记录（详见“窗口浏览”）。")
    history = {str(r['t']): r['stages'] for r in diagnostics.get('rt_windows', [])} or read_live(sel).get('stage_history', {})
    if history:
        window = st.selectbox('窗口起点', list(history), key='result_window:' + sel)
        stages_dashboard(history[window], prefix='result_stage:' + sel, market_stage='RT', window_start=int(window))
    else:
        stages_dashboard(None, prefix='result_stage:' + sel)


# ============================================================================ 9 窗口浏览（仅实时）
def page_windows():
    from value_views import render_window_browser
    from market_views import hydro_window_dashboard
    from smm.live_runner import read_live
    render_window_browser(R, lbl, key='rt_windows')
    target_file = Path(sel) / 'hydro_window_targets.csv'
    if target_file.exists():
        try:
            target_table = pd.read_csv(target_file)
        except pd.errors.EmptyDataError:
            target_table = pd.DataFrame()
        target_history = {str(t): g.to_dict('records') for t, g in target_table.groupby('window_start')} if len(target_table) else {}
    else:
        target_history = read_live(sel).get('hydro_window_history', {})
    hydro_window_dashboard(target_history, prefix='result_hydro:' + sel)


# ============================================================================ 日内滚动（仅 --intraday）
def page_intraday(tabs):
    summ, plans, prices, sto = (R.get(x) for x in ('id_rounds_summary', 'id_plans', 'id_prices_indicative', 'id_storage'))
    from stage_results import intraday_inventory, intraday_state_rows
    inventory = intraday_inventory(IDS, summ, DIAGNOSTICS)
    rounds = list(summ['round']) if summ is not None and 'round' in summ else []
    tau = None
    with tabs[0]:
        if not inventory.empty:
            st.dataframe(inventory, hide_index=True, width='stretch')
            tau = st.selectbox('日内轮次（成功/失败均可选）', list(inventory.tau),
                               format_func=lambda x:f'{lbl[int(x)]} · {inventory.loc[inventory.tau==x,"status"].iloc[0]}', key='id_tau')
            chosen = inventory[inventory.tau == tau].iloc[0]
            if not chosen['success']:
                st.warning('所选日内轮失败，无成功计划可展示。实时回退日前参考，不能把旧日内计划冒充本轮。')
        else:
            st.info('未保存日内轮次详情，不能把缺记录视为成功。')
        known_failures = int((~inventory['success']).sum()) if not inventory.empty else 0
        if (IDS.get('failure_count') or 0) > known_failures:
            st.warning(f'本次记录日内失败 {IDS["failure_count"]} 轮，但轮次明细缺失。')
    selected_success = tau is not None and bool(inventory.loc[inventory.tau==tau,'success'].iloc[0])
    rsel = (summ.loc[summ.tau == tau, 'round'].iloc[0] if selected_success and summ is not None and 'tau' in summ and any(summ.tau == tau) else None)
    with tabs[0]:
        st.caption('日内滚动为可选环节（默认关闭）：每轮锁定煤电/CCGT启停，窗口至日末；电价为指示性，不结算；正式执行仍以实时首段为准。')
        metric_grid([('日内滚动轮数', str(len(rounds))),
                     ('平均求解用时（s）', recorded_number(summ['mip_s'].mean() if rounds and 'mip_s' in summ else None, '.1f')),
                     ('最大 MIP 间隙', recorded_number(summ['gap'].max() if rounds and 'gap' in summ else None, '.3%'))])
        if rounds:
            show_df(summ, '%.3f', height=300)
    with tabs[1]:
        if rsel is None or prices is None or not len(prices):
            empty_state('未保存日内指示性电价。')
        else:
            p = prices[prices['round'] == rsel].pivot(index='bus', columns='period', values='lmp')
            fig = px.imshow(p.values, x=[lbl[int(t)] for t in p.columns], y=[str(b) for b in p.index], aspect='auto',
                            color_continuous_scale=LMP_SCALE, labels=dict(x=TIME, y='节点编号', color='元/MWh'))
            fig.update_yaxes(dtick=1, autorange='reversed', type='category')
            show_fig(style_fig(fig, f'日内轮次 {rsel} 指示性节点电价（不结算）', TIME, '节点编号', 600), key='id_heat')
    with tabs[2]:
        if rsel is None or plans is None or not len(plans):
            empty_state('未保存日内计划。')
        else:
            a = plans[plans['round'] == rsel] if rsel is not None else plans
            show_df(a, '%.3f', height=250)
            pass  # Result download controls removed.
            g = a.groupby(['period', 'type']).P_mw.sum().unstack(fill_value=0)
            fig = go.Figure()
            for t in g:
                fig.add_scatter(x=[lbl[int(i)] for i in g.index], y=g[t], name=TYPE_CN.get(t, t), stackgroup='neg' if (g[t] < 0).all() else 'pos',
                                mode='lines', line=dict(width=0.5, color=TYPE_COLOR.get(t)))
            show_fig(style_fig(fig, f'日内轮次 {rsel} 分类型计划出力（储能/抽蓄为净值）', TIME, '出力（MW）', 420), key='id_stack')
    with tabs[3]:
        if rsel is None or sto is None or not len(sto):
            empty_state('未保存日内储能计划。')
        else:
            fig = go.Figure()
            for i, (un, b) in enumerate(sto[sto['round'] == rsel].groupby('unit')):
                col = PALETTE[i % len(PALETTE)]
                fig.add_scatter(x=[lbl[int(t)] for t in b.period], y=b.E_mwh, name=f'{un} 日内', line=dict(color=col, dash='dot'))
                da_sto = R.get('da_storage')
                d = da_sto[da_sto.unit == un] if da_sto is not None else pd.DataFrame(columns=['period','E_mwh'])
                fig.add_scatter(x=[lbl[int(t)] for t in d.period], y=d.E_mwh, name=f'{un} 日前', line=dict(color=col, width=1))
            show_fig(style_fig(fig, f'日内轮次 {rsel} 储能电量 vs 日前', TIME, '电量（MWh）', 360), key='id_sto')
        evidence, state_error = saved_object(Path(sel)/'hydro_window_evidence.json')
        if state_error:st.warning(state_error)
        state_rows = intraday_state_rows(evidence, tau) if selected_success else pd.DataFrame()
        if not state_rows.empty:
            st.markdown('**日内储能、抽蓄、水电状态计划（未执行）**')
            show_df(state_rows, '%.4f', height=300)
            pass  # Result download controls removed.
        else:
            st.info('所选轮次未保存储能/水电/抽蓄状态证据，不补造轨迹。')
    with tabs[4]:
        from market_views import stages_dashboard
        saved = next((r.get('stages') for r in DIAGNOSTICS.get('id_rounds',[]) if r.get('tau') == tau), None) if selected_success else None
        if not selected_success:
            saved=next((r.get('stages') for r in DIAGNOSTICS.get('id_failed_stages',[]) if r.get('tau') == tau), None)
            if saved:st.warning('本轮失败：以下仅为失败前已完成的阶段，不是成功日内计划，不能用于执行或结算。')
        stages_dashboard(saved, prefix='result_id_stage:'+sel, market_stage='ID', window_start=tau)
        st.markdown('**日内到实时的交接记录**')
        references=R.get('rt_value_windows')
        if references is not None and not references.empty:
            cols=[c for c in references if c in ('window_start','t','unit','kind') or 'source' in c or 'round' in c or 'fallback' in c or 'version' in c]
            if cols:show_df(references[cols], '%.3f', height=300)
        else:st.info('未保存逐资源交接记录，不能仅凭日内成功推断实时采用了本轮参考。')
        st.caption('逐资源参考来源、日内轮次版本与失败回退以实时窗口原始记录为准；日内不产生独立结算。')


# Four business views share the selected immutable run.
from final_views import render_final, system_price_compare, node_price_compare, kpi_compare


def _render_saved_overview():
    if FULL:
        sp = S.get('spread') or {}
        metric_grid([
            ('日前 / 实时加权价 · 元/MWh', ' / '.join(recorded_number(sp.get(x)) for x in ('da_lw_avg', 'rt_lw_avg'))),
            ('实时已执行时段', f'{T_rt} / {N}'),
            ('日前 / 实时负荷峰值 · MW', ' / '.join(recorded_number(R[key].loc[R[key].period.isin(R['rt_prices'].period.unique()), 'load_mw'].max() if isinstance(R.get(key), pd.DataFrame) and 'load_mw' in R[key] else None) for key in ('da_system', 'rt_system'))),
        ], per_row=3)
    else:
        metric_grid([
            ('日前加权价 · 元/MWh', recorded_number(DS.get('lmp_load_weighted_avg'))),
            ('日前中标电量 · MWh', f"{R['da_unit_awards'].energy_mwh.sum():,.1f}"),
            ('日前中标收入 · 万元', wan(R['da_unit_awards'].revenue_yuan.sum())),
        ], per_row=3)
    if FULL:
        st.caption('以上价格与峰值按已执行实时对应时段比较。')
    overview_stage = st.radio('供需阶段', ['da', 'rt'] if FULL else ['da'], format_func=SNAME.get, horizontal=True, key='overview_stage')
    page_generation(overview_stage, compact=True)
    with st.expander('弃风弃光'):
        curtailment_overview(curtailment)
        page_curtailment(overview_stage)
    with st.expander('系统指标与逐时对比'):
        if FULL:
            st.caption('日前与实时指标按相同已执行范围统计；空白为缺记录。')
            show_df(kpi_compare(R, buses, ll_da, ll_rt, curtailment), '%.2f')
        else:
            page_kpi('da')
        comparison = comparison_frame(R)
        show_df(comparison, '%.2f')
        pass  # Result download controls removed.
    render_forecast_diagnostics(sel)
    from result_economics_view import render_economics
    render_economics(R, prefix='all_economics:' + sel, prepared=ECONOMICS)


with main_tabs[0]:
    from result_story_view import render_difference_narrative, render_mechanism_audit, render_story_export
    try:
        render_difference_narrative(R, buses=buses, labels=lbl, curtailment=curtailment)
        render_mechanism_audit(R, labels=lbl)
        render_story_export(R, curtailment=curtailment, buses=buses)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        st.warning('差异叙事暂不能汇总，阶段结果仍可在下方查看。' + str(exc))
    with st.expander('阶段结果与主体经济', expanded=False):
        _render_saved_overview()

with main_tabs[1]:
    render_unit_overview(R, diagnostics=DIAGNOSTICS, prefix='unit_overview:' + sel, economics=ECONOMICS)
    with st.expander('高级状态与价值图表'):
        kind = st.selectbox('图表', ['库存轨迹对比', '价值与前瞻', '日前完整资源状态'] + (['实时完整资源状态'] if FULL else []), key='advanced_unit_chart')
        if kind in ('日前完整资源状态', '实时完整资源状态'):
            page_storage('da' if kind.startswith('日前') else 'rt')
        elif FULL:
            render_final(R, buses=buses, ll_da=ll_da, ll_rt=ll_rt, report=curtailment, lbl=lbl, sel=sel, sections={3 if kind == '库存轨迹对比' else 4})
        else:
            st.info('仅日前结果，没有实时状态可对比。')

with main_tabs[2]:
    from result_comparison import reserve_price_comparison
    reserve_prices = reserve_price_comparison(R, buses)
    if not reserve_prices.empty:
        st.subheader('备用缺额时段：同段电价对比')
        primary = [c for c in reserve_prices if '调度对偶' not in c]
        show_df(reserve_prices[primary], '%.2f')
        st.caption('比较同一时段，不能用全天均价判断缺额时段是否低价。原始定价与限价后结算价分列；辅助模型只定价，不改变正式出力。')
        if any('调度对偶' in c for c in reserve_prices):
            with st.expander('正式调度对偶与辅助定价证据'):
                show_df(reserve_prices, '%.2f')
                st.caption('调度对偶原始价、所选定价原始价和结算价是不同口径，不互相替代。')
    choices = (['compare', 'da'] + (['id'] if INTRADAY else []) + ['rt']) if FULL else ['da']
    price_stage = st.radio('价格阶段', choices, format_func=lambda x: {'compare': '日前 / 实时对比', 'id': '日内参考（不结算）', **SNAME}[x], horizontal=True, key='price_stage')
    if price_stage == 'id':
        from unit_overview import round_inventory, saved_price_schema
        inventory = round_inventory(R, DIAGNOSTICS)
        if inventory.empty:
            st.info('未保存日内轮次，不补造指示性价格。')
        else:
            tau = st.selectbox('价格参考轮次', list(inventory.tau),
                               format_func=lambda t: f'{lbl[int(t)]} · {inventory.loc[inventory.tau.eq(t), "status"].iloc[0]}', key='price_id_tau')
            item = inventory.loc[inventory.tau.eq(tau)].iloc[0]
            rounds = R.get('id_rounds_summary')
            selected = rounds.loc[rounds.tau.eq(tau)] if isinstance(rounds, pd.DataFrame) and 'tau' in rounds else pd.DataFrame()
            prices = R.get('id_prices_indicative')
            if not item['success']:
                st.warning('所选轮次失败，无成功日内价格，不使用旧轮次替代。')
            elif len(selected) != 1 or 'round' not in selected or not isinstance(prices, pd.DataFrame) or 'round' not in prices:
                st.info('所选轮次价格记录缺失。')
            else:
                prices = prices.loc[prices['round'].eq(selected.iloc[0]['round'])]
                field, price_label, names, _known = saved_price_schema(prices, 'id')
                if prices.empty or not {'bus', 'period', field} <= set(prices):
                    st.info('所选轮次价格记录缺失。')
                else:
                    nodes = sorted(prices.bus.unique())
                    chosen = st.multiselect('日内节点', nodes, default=nodes[:1], key='compact_id_price_nodes')
                    fig = go.Figure()
                    for i, node in enumerate(chosen):
                        rows = prices.loc[prices.bus.eq(node)].sort_values('period')
                        fig.add_scatter(x=[lbl[int(t)] for t in rows.period], y=rows[field], name=f'节点{node}', connectgaps=False,
                                        line=dict(color=PALETTE[i % len(PALETTE)], dash='dot'))
                    show_fig(style_fig(fig, '日内参考价，不参与结算', TIME, price_label, 340), key='compact_id_prices')
                    with st.expander('日内价格明细'):
                        show_df(prices[['period', 'bus', field]].rename(columns=names), '%.2f')
                        pass  # Result download controls removed.
    else:
        fig = go.Figure()
        stages = ['da', 'rt'] if price_stage == 'compare' else [price_stage]
        for stage in stages:
            values = load_weighted(R['da_nodal_prices' if stage == 'da' else 'rt_prices'], buses)
            fig.add_scatter(x=[lbl[int(t)] for t in values.index], y=values.values, name=SNAME[stage], connectgaps=False,
                            line=dict(color=DA_COLOR if stage == 'da' else RT_COLOR, dash='solid' if stage == 'da' else 'dash'))
        show_fig(style_fig(fig, '全网负荷加权电价', TIME, PRICE, 320), key='compact_system_prices')
        nodes = sorted(R['da_nodal_prices'].bus.unique())
        chosen_nodes = st.multiselect('节点', nodes, default=nodes[:1], key='compact_price_nodes')
        fig = go.Figure()
        for i, node in enumerate(chosen_nodes):
            for stage in stages:
                rows = R['da_nodal_prices' if stage == 'da' else 'rt_prices']
                rows = rows[rows.bus.eq(node)].sort_values('period')
                fig.add_scatter(x=[lbl[int(t)] for t in rows.period], y=rows.lmp, name=f'节点{node} · {SNAME[stage]}', connectgaps=False,
                                line=dict(color=PALETTE[i % len(PALETTE)], dash='solid' if stage == 'da' else 'dash'))
        if fig.data:
            show_fig(style_fig(fig, '节点电价', TIME, PRICE, 340), key='compact_node_prices')
        with st.expander('价格明细'):
            for stage in stages:
                prices = R['da_nodal_prices' if stage == 'da' else 'rt_prices']
                business = prices[[c for c in ('period', 'time', 'bus', 'lmp') if c in prices]]
                st.markdown('**' + SNAME[stage] + '**')
                show_df(business, '%.2f', height=280)
                pass  # Result download controls removed.
        with st.expander('高级价格图表与定价来源'):
            advanced_price = st.selectbox('价格图表', ['日前'] + (['实时', '价差分布与排名'] if FULL else []), key='advanced_price_chart')
            if advanced_price == '价差分布与排名':
                render_final(R, buses=buses, ll_da=ll_da, ll_rt=ll_rt, report=curtailment, lbl=lbl, sel=sel, sections={1})
            else:
                page_prices('da' if advanced_price == '日前' else 'rt')

with main_tabs[3]:
    network_stage = st.radio('电网阶段', ['da', 'rt'] if FULL else ['da'], format_func=SNAME.get, horizontal=True, key='network_stage')
    page_network(network_stage)
    with st.expander('备用供需与缺额'):
        page_reserve(network_stage)
    if FULL:
        with st.expander('实时窗口与前瞻约束'):
            page_windows()
        with st.expander('结算恒等式与资金核对'):
            render_final(R, buses=buses, ll_da=ll_da, ll_rt=ll_rt, report=curtailment, lbl=lbl, sel=sel, sections={7})
    with advanced:
        options = ['DA'] + (['ID'] if INTRADAY else []) + (['RT'] if FULL else [])
        chosen_stage = st.radio('完整出清阶段', options, horizontal=True, key='advanced_stage')
        if chosen_stage == 'ID':
            id_sections = [st.expander(label) for label in ['日内轮次与交接', '指示性价格', '计划出力', '库存状态', 'SCUC/SCED与定价结果']]
            page_intraday(id_sections)
        else:
            page_diagnostics(chosen_stage.lower())

# ============================================================================ 结算
with main_tabs[3], st.expander('系统资金核对与结算明细', expanded=False):
    if not FULL:
        st.info("仅日前结果：显示日前中标收入（电量 × 日前节点电价）。全流程结果才有偏差结算。")
        ua = R["da_unit_awards"]
        t1 = ua.groupby(["unit", "type"]).agg(日前电量MWh=("energy_mwh", "sum"), 日前收入元=("revenue_yuan", "sum")).reset_index()
        t1["均价 元/MWh"] = t1.日前收入元 / t1.日前电量MWh.replace(0, np.nan)
        t1["日前收入 万元"] = t1.pop("日前收入元") / 1e4
        t1["type"] = t1.type.map(lambda x: TYPE_CN.get(x, x))
        show_df(t1.rename(columns={"unit": "机组", "type": "类型", "日前电量MWh": "日前电量 MWh"}), "%.1f",
                overrides={"日前收入 万元": "%.2f"})
        pass  # Result download controls removed.
    else:
        money = ["da_amount", "rt_amount", "total", "dev_pnl"]

        def to_wan(df):
            df = df.copy()
            for c in money:
                if c in df:
                    df[c] = df[c] / 1e4
            return df

        cn = {"entity": "主体", "type": "类型", "q_da": "日前电量 MWh", "q_rt": "实际电量 MWh", "dev_mwh": "偏差 MWh",
              "dev_abs_mwh": "偏差绝对值 MWh", "da_amount": "日前电费 万元", "rt_amount": "实时偏差电费 万元", "total": "合计 万元",
              "dev_pnl": "偏差损益 万元", "avg_price": "结算均价 元/MWh"}
        wfmt = {v: "%.2f" for k, v in cn.items() if k in money}
        bt = R["settlement_by_type"].copy()
        bt = bt[~((bt.type == "slack") & (bt.total.abs() < 1e-6) & (bt.q_rt.abs() < 1e-6))]   # 平衡松弛为 0 时不显示
        bt["type"] = bt.type.map(lambda x: TYPE_CN.get(x, x))
        st.subheader("按资源类型")
        st.caption("发电为正（收入），负荷为负（支付）；金额单位：万元")
        show_df(to_wan(bt).rename(columns=cn), "%.1f", overrides=wfmt)
        pass  # Result download controls removed.
        b2 = bt[bt.type != TYPE_CN["slack"]]
        fig = go.Figure()
        fig.add_bar(x=b2.type, y=b2.da_amount / 1e4, name="日前电费", marker_color=DA_COLOR)
        fig.add_bar(x=b2.type, y=b2.rt_amount / 1e4, name="实时偏差电费", marker_color=RT_COLOR)
        fig.add_bar(x=b2.type, y=b2.dev_pnl / 1e4, name="偏差损益", marker_color="#2a9d8f")
        fig.update_layout(barmode="group", hovermode="x unified")
        show_fig(style_fig(fig, "分类型结算金额", "资源类型", "金额（万元）", 380))

        with st.expander("按主体结算", expanded=False):
            be = R["settlement_by_entity"]
            tsel = st.multiselect("类型筛选", sorted(be.type.unique()), default=[t for t in be.type.unique() if t != "load"],
                                  format_func=lambda x: TYPE_CN.get(x, x))
            bb = to_wan(be[be.type.isin(tsel)])
            bb["type"] = bb.type.map(lambda x: TYPE_CN.get(x, x))
            show_df(bb.rename(columns=cn), "%.1f", overrides=wfmt)
            pass  # Result download controls removed.
        with st.expander("资金平衡校核（盈余 = 阻塞项 + 限价修正 + 残差）与线路阻塞项", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                rc = R["settlement_reconciliation"].copy()
                st.markdown("**资金平衡校核**（元）")
                show_df(rc, "%.2f")
            with c2:
                st.markdown("**线路阻塞租金**（元）")
                show_df(R["settlement_line_rent"], "%.1f")
        with st.expander("逐时段结算明细"):
            ent = st.selectbox("主体", sorted(R["settlement_detail"].entity.unique()))
            show_df(R["settlement_detail"][R["settlement_detail"].entity == ent], "%.2f", height=320)
            pass  # Result download controls removed.


# Historical regulation outputs remain on disk, not a current market feature.
if S.get('regulation',{}).get('enabled',False):
    st.warning('历史调频实验结果：不属于当前纯电能量版本，不应作为当前默认研究结果。')
    with st.expander('历史调频实验只读原始表（不纳入当前能量收益）'):
        if R.get('rt_regulation_system') is not None:show_df(R['rt_regulation_system'])
        if R.get('rt_regulation') is not None:show_df(R['rt_regulation'])

with navigation['diagnostic'][1]:
    sw = R.get('solver_windows') if isinstance(R.get('solver_windows'), pd.DataFrame) else None
    if sw is None and (Path(sel) / 'solver_windows.csv').exists():
        try:
            sw = pd.read_csv(Path(sel) / 'solver_windows.csv')
        except pd.errors.EmptyDataError:
            sw = None
    if sw is not None and len(sw):
        show_df(sw, '%.3f', height=300)
    fb = R.get('rt_fallbacks')
    if FULL:
        st.markdown(f"**硬→软兜底记录**：{'未记录' if fb is None else len(fb)} 条")
        if fb is not None and len(fb):
            show_df(fb, '%.3f')


with navigation['diagnostic'][2]:
    # Separate the immutable run evidence from the current, independently revised UI.
    with st.expander('运行来源与界面版本'):
        import hashlib, json
        provenance_path=Path(sel)/'run_provenance.json'
        if provenance_path.exists():
            provenance, provenance_error = saved_object(provenance_path)
            if provenance_error:
                st.warning('运行来源记录损坏，不能确认历史源码指纹；原始结果不改写。' + provenance_error)
            else:
                source = provenance.get('source') or {}
                fingerprint = source.get('content_fingerprint', '未知') if isinstance(source, dict) else '未知'
                st.caption('运行时源码指纹（含当时界面）：'+str(fingerprint))
                st.caption('采集时间：'+str(provenance.get('captured_at_utc','未知'))+'；未提交工作区：'+str(provenance.get('git_dirty','未知')))
        else:
            cli_source = Path(sel) / 'scenario_snapshot/source_fingerprint.json'
            if cli_source.is_file():
                provenance, provenance_error = saved_object(cli_source)
                hashes = provenance.get('sha256')
                if provenance_error or not isinstance(hashes, dict):
                    st.warning('CLI冻结源码校验表损坏，历史版本未核实；不借用当前版本。')
                else:
                    st.caption('本次CLI运行前已冻结源码逐文件校验表：' + str(len(hashes)) + '个文件；采集时间：' +
                               str(provenance.get('recorded_at_utc', '未知')) + '。这是运行前存档，不表示当前界面版本已重新求解。')
                    pass  # Result download controls removed.
            else:
                st.caption('历史运行未保存源码指纹，不借用当前版本作为来源。')
        ui_root=Path(__file__).parents[1]
        ui_files=sorted(ui_root.rglob('*.py'))
        ui_hash=hashlib.sha256(b''.join(str(p.relative_to(ui_root)).encode()+b'\0'+p.read_bytes() for p in ui_files)).hexdigest()
        st.caption('当前结果界面指纹：'+ui_hash+'；界面更新不表示重新求解。')

