"""Progressive single-day UI over the existing scenario, quotation and run services.

Browsing does not initialize a series task or persist a quotation. Explicit edits
retain their existing transactional saves; launch freezes the exact current draft.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from common import page_setup, scenario_selector, scen_config, sc, run_status, start_run
from input_workflow import business_error, launch_error
from bid_workspace import get_scene_working_draft, persist_scene_working_draft

STEPS = ['1 场景', '2 机组', '3 报价', '4 运行', '5 结果']
STEP_KEY = 'single_day_step'


def _go(step):
    st.session_state[STEP_KEY] = step


def read_quote(cfg, name):
    """Read the same durable working quote as the advanced workbench, fail closed."""
    from smm.bids import validate_bids
    _, draft, _, info = get_scene_working_draft(st.session_state, name, cfg)
    issues = validate_bids(cfg, draft, require_complete=True)
    return draft, info, [i for i in issues if i.level == 'error']


def validate_day(cfg):
    """Input consistency only, never a claim that the optimization is feasible."""
    from smm.input_validation import validate_physical_inputs, validate_production_day
    from smm.spot_costs import load_profile
    validate_production_day(cfg)
    validate_physical_inputs(cfg)
    load_profile(cfg)  # Invalid cost archives block; missing costs remain unknown.


def initial_rows(cfg):
    """Display configured initial state without manufacturing measured provenance."""
    rows = []
    for u in cfg.units.get('thermal', []):
        v = u['init']
        rows.append({'主体': u['id'], '类型': '火电', '初始状态':
                     f"{'开机' if v['online'] else '停机'} · {v['p0']:g} MW · 已持续 {v['hours']:g} h"})
    for u in cfg.units.get('hydro', []):
        v = u['seasons'][cfg.base['season']]['v0']
        rows.append({'主体': u['id'], '类型': '水电', '初始状态':
                     f"库容 {v:g} 万m³ · 在线 {u['init_units_on']} 台 · 单机 {u['init_p_unit']:g} MW"})
    for u in cfg.units.get('storage', []):
        rows.append({'主体': u['id'], '类型': '电池', '初始状态': f"SOC {u['soc0']:.1%} · 电量 {u['soc0'] * u['ecap']:g} MWh"})
    from smm.rolling import initial_state
    pumped = initial_state(cfg).pumped
    for u in cfg.units.get('pumped', []):
        state = pumped[u['id']]
        rows.append({'主体': u['id'], '类型': '抽蓄', '初始状态':
                     f"上库 {u['vup0']:g} 万m³ · 发电 {state['ng']} 台 / 抽水 {state['np']} 台"})
    return rows


def quote_rows(draft):
    from input_workflow import CURVE_CN
    rows = []
    for uid, bid in draft['units'].items():
        for curve, label in CURVE_CN.items():
            values = [s.get('price') for s in bid.get(curve, []) if isinstance(s, dict)]
            if values:
                from numbers import Real
                import math
                valid = all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v) for v in values)
                rows.append({'主体': uid, '申报项目': label, '段数': len(values),
                             '价格范围（元/MWh）': f'{min(values):g} ～ {max(values):g}' if valid else '有无效值，请修正'})
    return rows


def render_quote_source(draft, info, errors, *, details=True):
    if details:
        st.caption('报价来源：' + info['basis_label'])
    if details and draft.get('default_bid_model') == 'cost_basis_v2':
        st.caption('模拟成本报价，非真实市场申报。')
    if draft.get('_filled_default'):
        st.warning('以下主体曾以缺省报价补齐，请在报价步骤核对：' + '、'.join(draft['_filled_default']))
    if info.get('newer_basis'):
        st.warning('外部已保存报价已变化；仍保留当前工作报价，不自动替换。请在报价步骤核对来源。')
    if info.get('latest_error'):
        st.warning('外部最新报价不能读取；当前工作报价未被替换。')
    if errors:
        st.error('报价校验未通过，不能启动。请进入报价步骤修正。')
        with st.expander('报价校验详情'):
            st.write([getattr(e, 'msg', str(e)) for e in errors])


def stage_flow(cfg, mode='full', periods=None):
    if mode == 'da':
        st.markdown('**日前计划**')
        return
    enabled = bool(cfg.mode.get('intraday', {}).get('enabled', False))
    stages = ['日前计划'] + (['日内修正'] if enabled else []) + ['实时执行']
    stages += ['偏差结算' if periods is None or periods >= cfg.T else '已执行前缀（非完整日）']
    st.markdown('**' + ' → '.join(stages) + '**')
    if enabled:
        st.caption('日内已启用，仅更新参考计划，不单独结算。')
    else:
        st.caption('日内修正未启用，可在高级设置中调整。')


def _navigation(step, locked=False):
    index = STEPS.index(step)
    left, right = st.columns(2)
    if index:
        left.button('上一步', on_click=_go, args=(STEPS[index-1],), disabled=locked)
    if 0 < index < 3:
        right.button('下一步', on_click=_go, args=(STEPS[index+1],), disabled=locked, type='primary')


def render_single_day():
    page_setup('单日市场出清模拟')
    for notice_key in ('typical_created_notice',):
        notice = st.session_state.pop(notice_key, None)
        if notice:
            st.success(notice)
    pending = st.session_state.pop('typical_switch_to', None)
    if pending is not None:
        st.session_state['scenario'] = pending
        st.session_state['_sel_single_day'] = pending
    current = st.session_state.get('scenario', sc.DEFAULT)
    locked = any(key in st.session_state for key in ('unit_editor:' + current, 'advanced_parameters:' + current))
    records = sc.list_runs()
    active_records = [m for m in records if run_status(m) in ('running', 'starting', 'cancelling')]
    running = bool(active_records)
    # Restore only a fresh session. Never override an existing user's navigation.
    if STEP_KEY not in st.session_state and active_records:
        watched = active_records[0]
        st.session_state[STEP_KEY] = STEPS[4]
        st.session_state['watch_run'] = watched['dir']
        st.session_state['run_sel'] = watched['dir']
        if watched.get('scenario') in sc.list_scenarios():
            st.session_state['scenario'] = watched['scenario']
            st.session_state['_sel_single_day'] = watched['scenario']
    needs_restart = bool(st.session_state.get('runtime_needs_restart'))
    # Preserve widget values across optional panels; quotation data itself has a durable archive.
    for key in list(st.session_state):
        if key.startswith(('quote_unit:', 'single_day_edit_quote:', 'single_day_mode:', 'single_day_short:', 'single_day_periods:', 'day_condition:')):
            st.session_state[key] = st.session_state[key]
    if running:
        st.info('已有运行或进程状态待核实，暂不能编辑或重复启动；可在结果步骤查看进度。')
    if locked:
        st.session_state[STEP_KEY] = STEPS[1] if 'unit_editor:' + current in st.session_state else STEPS[3]
        st.info('请先保存或取消正在编辑的参数，再切换步骤或场景。未保存内容不会用于运行。')
    requested_step = st.session_state.pop('single_day_requested_step', None)
    if requested_step is not None and not locked:
        st.session_state[STEP_KEY] = requested_step
    if st.session_state.pop('single_day_started', False):
        st.session_state[STEP_KEY] = STEPS[4]
        st.success('已启动，场景、日初状态和当前报价已冻结。')
    step = st.radio('执行流程', STEPS, horizontal=True, key=STEP_KEY, disabled=locked, label_visibility='collapsed')
    name = scenario_selector(key='single_day', sidebar=step != STEPS[0], label='单日场景',
                             disabled=running or needs_restart or locked, manage=False)
    invalid = [m for m in records if m.get('status') == 'invalid' and m.get('scenario') == name]
    active = running or needs_restart or bool(invalid)
    if invalid:
        st.error('当前场景有损坏的运行记录，不能确认运行状态；请保留原文件并核对恢复。')
        for m in invalid:
            st.caption(str(Path(m['dir'])/'meta.json') + '：' + m['metadata_error'])
    if step == STEPS[4]:
        st.subheader('进度与结果')
        from daily_run_monitor import render_run_monitor
        render_run_monitor(active, compact=True)
        _navigation(step)
        return
    try:
        cfg = scen_config(name)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        business_error('场景配置不能读取，原文件保留', exc, '请切换场景，或在完整报价工作台恢复未完成的机组保存。')
        st.page_link('pages/2_运行.py', label='打开完整报价工作台')
        return
    conditions_pending = bool(st.session_state.get('day_condition_pending:' + name))
    if step != STEPS[0] and conditions_pending:
        st.warning('场景条件尚未应用，不能按旧条件启动。请返回场景点击下一步应用，或恢复原选项。')
        st.button('返回场景', on_click=_go, args=(STEPS[0],), disabled=locked)
    if step != STEPS[0]:
        st.caption(f"{name} · {cfg.base['date']} · {cfg.T} × {cfg.dt*60:g} 分钟")
    draft, info, bid_errors = None, {}, []
    try:
        draft, info, bid_errors = read_quote(cfg, name)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        business_error('当前报价不能读取，暂不能运行', exc, '不会改用默认报价；可在报价步骤查看恢复入口。')
    input_error = None
    try:
        validate_day(cfg)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        input_error = exc
        business_error('场景参数检查未通过，暂不能运行', exc, '请核对机组初态和运行设置；不会自动补成可用状态。')

    if step == STEPS[0]:
        from single_day_scenario import render_conditions, apply_conditions
        preview, changed, blocked = render_conditions(cfg, name, bids=draft, disabled=active or locked)
        if st.button('下一步', type='primary', disabled=locked or (changed and (active or blocked or bool(bid_errors)))):
            if changed:
                try:
                    path = apply_conditions(name, preview, draft)
                except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                    business_error('场景条件未应用，原场景保留', exc, '请重新选择条件后继续。')
                else:
                    st.session_state['typical_switch_to'] = path.name
                    st.session_state['typical_created_notice'] = '已应用所选场景条件，原场景保留。'
                    st.session_state['single_day_requested_step'] = STEPS[1]
                    st.rerun()
            else:
                st.session_state['single_day_requested_step'] = STEPS[1]
                st.rerun()
    elif step == STEPS[1]:
        st.subheader('机组')
        from unit_input_view import resource_capacity_rows, render_network_and_units
        energy_columns = ('储能额定能量（MWh）', '抽蓄可用发电能量（MWh）')
        capacities = pd.DataFrame([
            {key: ('不适用' if value is None else str(value)) if key in energy_columns else value
             for key, value in row.items()}
            for row in resource_capacity_rows(cfg)
        ])
        st.dataframe(capacities, hide_index=True, width='stretch',
                     column_config={'状态': st.column_config.TextColumn(width='medium')})
        with st.expander('查看日初状态明细'):
            st.caption('初态来自场景配置，未核验为实测状态，不自动接续前一天。')
            if input_error is None:
                st.dataframe(pd.DataFrame(initial_rows(cfg)), hide_index=True, width='stretch')
            else:
                st.warning('初态尚未通过检查，暂不显示可用状态汇总。请先修复原始参数。')
            st.caption('初态在下方“编辑或新增机组”的技术参数中修改，检查后保存才生效。')
        from unit_editor import render_unit_editor
        render_unit_editor(cfg, name, disabled=active, compact=True)
        with st.expander('系统接线图、曲线与完整机组参数'):
            if st.checkbox('加载系统与机组详情', key='single_day_network:' + name):
                render_network_and_units(cfg, key_prefix='daily_network:' + name, bids=draft)
    elif step == STEPS[2]:
        st.subheader('报价')
        from quote_simulation import quotation_workspace
        current_quote, _ = quotation_workspace(cfg, name, disabled=active, compact=True)
        if current_quote is not None:
            with st.expander('全部机组报价概览', expanded=False):
                draft = current_quote
                st.dataframe(pd.DataFrame(quote_rows(draft)), hide_index=True, width='stretch')
    elif step == STEPS[3]:
        st.subheader('运行')
        if active_records:
            from run_progress_view import render_active_progress
            render_active_progress(active_records[0]['dir'])
        if draft is not None:
            render_quote_source(draft, info, bid_errors, details=False)
        mode, periods = 'full', None
        primary_run = st.container()
        with st.expander('高级设置', expanded='advanced_parameters:' + name in st.session_state):
            st.caption('通常保持全天全流程。仅日前与短时试跑用于调试，不生成完整日结算。')
            mode = st.radio('运行范围', ['full', 'da'], horizontal=True, key='single_day_mode:' + name,
                            format_func=lambda v: '完整单日' if v == 'full' else '仅日前计划', disabled=active)
            short = st.checkbox('仅试跑前几个实时时段', key='single_day_short:' + name, disabled=active or mode == 'da')
            if short and mode == 'full':
                periods = st.number_input('实时时段数', min_value=1, max_value=cfg.T, value=min(8, cfg.T),
                                          key='single_day_periods:' + name, disabled=active)
            from advanced_parameters import render_advanced_parameters, render_effective_parameters
            render_advanced_parameters(cfg, name, disabled=active)
            render_effective_parameters(cfg)
            from anchor_policy_controls import render_anchor_policy
            render_anchor_policy(cfg, name, disabled=active or locked)
            from fixed_load_controls import render_fixed_load_policy
            render_fixed_load_policy(cfg, name, disabled=active or locked)
            from forecast_controls import render_forecast_model_control
            render_forecast_model_control(cfg, name, bids=draft, disabled=active or locked)
            with st.expander('运行与输入说明'):
                st.caption('初态来自场景配置；启动冻结配置与当前报价。参数检查不代表求解可行或精度达标。')
                st.caption('实时每窗只执行首段，再传递机组状态、SOC与库容；前瞻计划不计作执行结果。')
        with primary_run:
            st.caption('实际执行阶段')
            stage_flow(cfg, mode, periods)
            if mode == 'da':
                st.warning('本次仅计算日前计划，没有实时执行与偏差结算。')
            elif periods is not None and periods < cfg.T:
                st.warning(f'本次只执行前 {int(periods)} / {cfg.T} 个实时时段，仍先求全天日前计划；不是完整日结算。')
            else:
                st.caption(f'完整单日 · {cfg.T} 个实时时段')
            incompatible = bool(cfg.mode.get('regulation', {}).get('enabled', False)) or cfg.base.get('tie_line_mode', 'supply') == 'fixed_load'
            if incompatible:
                st.error('历史调频或联络线转负荷场景不能按当前电能量主流程启动。请切换兼容场景或核对运行设置，旧输入不自动修改。')
            from smm.input_version import run_options
            if not active or 'network_run_options:' + name in st.session_state:
                st.session_state['network_run_options:' + name] = run_options(mode, periods)
            if st.button('开始单日出清', type='primary', width='stretch', disabled=active or incompatible or locked or conditions_pending or bool(input_error) or bool(bid_errors) or draft is None):
                try:
                    # Detect concurrent quote writes before launch; never freeze a stale unpersisted edit.
                    persist_scene_working_draft(st.session_state, name, cfg)
                    directory = start_run(name, mode, int(draft['version']), int(periods) if periods is not None else None, bids=draft)
                except Exception as exc:
                    if getattr(exc, 'process_started', False) and getattr(exc, 'directory', None) is not None:
                        st.session_state['watch_run'] = str(exc.directory)
                        st.session_state['run_sel'] = str(exc.directory)
                    launch_error(exc)
                else:
                    st.session_state['watch_run'] = str(directory)
                    st.session_state['run_sel'] = str(directory)
                    st.session_state['single_day_started'] = True
                    st.rerun()
    _navigation(step, locked=locked)
