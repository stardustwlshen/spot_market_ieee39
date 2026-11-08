"""单机报价模拟：机组信息、报价、市场运行与全机组参数。"""
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from input_workflow import business_error, launch_error
from common import (TYPE_CN, MODE_CN, STATUS_CN, page_setup, run_label, run_progress, run_status,
                    sc, scen_config, scenario_selector, start_run, stop_run)
from smm.time_grid import result_time_grid
from quote_simulation import quotation_workspace
from run_record_actions import elapsed_for_display, render_open_saved_result

page_setup("单机报价模拟")
pending_scene = st.session_state.pop('typical_switch_to', None)
if pending_scene is not None:
    st.session_state['scenario'] = pending_scene
    st.session_state['_sel_scenario'] = pending_scene
name = scenario_selector()
try:
    cfg = scen_config(name)
except (ValueError, KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
    business_error('当前场景配置不能读取或校验未通过，原文件已保留', exc,
                   '可在侧边栏切换其他场景，或按技术详情修复此场景后重试；不会自动重置。')
    from smm.unit_edit_transaction import JOURNAL, recover_scene_unit_edit
    from reserve_duration_ui import render_duration_repair
    render_duration_repair(name)
    if (sc.scenario_path(name) / JOURNAL).exists():
        st.caption('上次机组保存意外中断时，可恢复到保存前的完整输入。外部修改冲突不会被覆盖。')
        if st.button('恢复未完成的机组保存'):
            try:
                recover_scene_unit_edit(sc.scenario_path(name))
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as recovery_error:
                business_error('恢复未完成，现有文件仍保留，请核对备份', recovery_error)
            else:
                st.rerun()
    st.stop()
notice = st.session_state.pop('typical_created_notice', None)
if notice:
    st.success(notice)
run_records = sc.list_runs()
running = [m for m in run_records if run_status(m) in ("running", "starting", "cancelling")]
invalid_current = [m for m in run_records if m.get('status') == 'invalid' and m.get('scenario') == name]
needs_restart = bool(st.session_state.get('runtime_needs_restart'))
active = bool(running) or needs_restart or bool(invalid_current)
if invalid_current:
    st.error('当前场景有损坏的运行元数据，无法确认历史运行状态，暂不能改价或启动。请保留原文件，核对并从备份恢复下列meta.json；也可切换其他场景查看。')
    for invalid_record in invalid_current:
        st.caption(str(Path(invalid_record['dir']) / 'meta.json') + '：' + invalid_record['metadata_error'])
if running:
    st.info("已有后台运行，或历史进程身份尚待核实。暂禁用改价和启动，避免重复求解。")
elif needs_restart:
    st.info("请先重启应用，再编辑报价、机组或开始运行，避免混用更新前后的代码。")
st.caption(f"{cfg.base['date']} · { {'wet': '丰水期', 'dry': '枯水期'}.get(cfg.base['season'], cfg.base['season']) }")

quote_tab, network_tab = st.tabs(['机组与报价', '系统接线图与全网'])
with quote_tab:
    draft, bid_errors = quotation_workspace(cfg, name, disabled=active)
    from unit_editor import render_unit_editor
    render_unit_editor(cfg, name, disabled=active)
with network_tab:
    from unit_input_view import render_network_and_units
    render_network_and_units(cfg, key_prefix='network_inputs:' + name, bids=draft)

from typical_scenario_controls import render_typical_scenarios
render_typical_scenarios(cfg, name, bids=draft, disabled=active or bool(bid_errors))

from forecast_controls import render_forecast_model_control
render_forecast_model_control(cfg, name, bids=draft, disabled=active)
from fixed_load_controls import render_fixed_load_policy
render_fixed_load_policy(cfg, name, disabled=active)
from anchor_policy_controls import render_anchor_policy
render_anchor_policy(cfg, name, disabled=active)

from advanced_parameters import render_advanced_parameters, render_effective_parameters
render_advanced_parameters(cfg, name, disabled=active)
render_effective_parameters(cfg)

from reserve_policy_controls import render_reserve_policy
render_reserve_policy(cfg)

c1, c2 = st.columns(2)
mode = c1.radio("运行模式", ["full", "da"], format_func=lambda value: {"da": "仅日前", "full": "全天：日前→实时→结算"}[value], horizontal=True, disabled=active)
short_run = c2.checkbox("短时试跑", value=False, disabled=active or mode == 'da')
periods = (c2.number_input(f"实时试跑时段数（每段{cfg.dt * 60:g}分钟）", 1, cfg.T, min(8, cfg.T), disabled=active)
           if short_run and mode == 'full' else 0)
if short_run and mode == 'full':
    st.caption("短时试跑仍先计算全天日前计划；只截短实时，不是完整日结算。")
from smm.input_version import run_options
st.session_state['network_run_options:' + name] = run_options(mode, periods)
historical_regulation = bool(cfg.mode.get('regulation', {}).get('enabled', False))
historical_fixed_load = cfg.base.get('tie_line_mode', 'supply') == 'fixed_load'
if historical_regulation:
    st.error("此场景启用历史调频实验，不属于当前电能量模拟。请选择兼容场景；原参数不会被改写。")
if st.button("▶ 开始运行", type="primary", width="stretch", disabled=active or historical_regulation or historical_fixed_load or bool(bid_errors) or draft is None):
    try:
        d = start_run(name, mode, int(draft.get("version", 1)), int(periods) or None, bids=draft)
    except Exception as exc:
        if getattr(exc, "process_started", False) and getattr(exc, "directory", None) is not None:
            st.session_state["watch_run"] = str(exc.directory)
            st.session_state["run_sel"] = str(exc.directory)
        launch_error(exc)
    else:
        st.session_state["watch_run"] = str(d)
        st.session_state["run_sel"] = str(d)
        st.success("已启动；本次场景参数与报价已冻结。")
        st.rerun()

st.divider()
st.subheader("市场运行情况")


from daily_run_monitor import render_run_monitor
render_run_monitor(active)
