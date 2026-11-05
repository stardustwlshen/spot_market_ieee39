"""单机报价模拟：机组信息、报价、市场运行与全机组参数。"""
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from input_workflow import business_error, launch_error
from common import (TYPE_CN, MODE_CN, STATUS_CN, page_setup, run_label, run_progress, run_status,
                    sc, scen_config, scenario_selector, start_run, stop_run)
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
st.caption(f"{cfg.base['date']} · { {'wet': '丰水期', 'dry': '枯水期'}.get(cfg.base['season'], cfg.base['season']) } · 选择一台机组改报价，观察整个市场的运行变化。")

quote_tab, network_tab = st.tabs(['机组与报价', '全网与其他机组'])
with quote_tab:
    draft, bid_errors = quotation_workspace(cfg, name, disabled=active)
    from unit_editor import render_unit_editor
    render_unit_editor(cfg, name, disabled=active)
with network_tab:
    from unit_input_view import render_network_and_units
    render_network_and_units(cfg, key_prefix='network_inputs:' + name)

from typical_scenario_controls import render_typical_scenarios
render_typical_scenarios(cfg, name, bids=draft, disabled=active or bool(bid_errors))

from forecast_controls import render_forecast_model_control
render_forecast_model_control(cfg, name, bids=draft, disabled=active)
from fixed_load_controls import render_fixed_load_policy
render_fixed_load_policy(cfg, name, disabled=active)
from anchor_policy_controls import render_anchor_policy
render_anchor_policy(cfg, name, disabled=active)

c1, c2 = st.columns(2)
mode = c1.radio("运行模式", ["full", "da"], format_func=lambda value: {"da": "仅日前", "full": "全天：日前→实时→结算"}[value], horizontal=True, disabled=active)
short_run = c2.checkbox("短时试跑", value=False, disabled=active or mode == 'da')
periods = (c2.number_input("实时试跑时段数（每段15分钟）", 1, 96, 8, disabled=active)
           if short_run and mode == 'full' else 0)
if short_run and mode == 'full':
    st.caption("短时试跑仍先计算全天日前计划；只截短实时，不是完整日结算。")
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


@st.fragment(run_every=2)
def monitor():
    runs = sc.list_runs()
    if not runs:
        st.info("暂无运行记录。选择模式后点击“开始运行”。", icon="📭")
        return
    for m in runs:
        m["status"] = run_status(m)
    # A timer reruns only this fragment. Refresh page-level editor/button state
    # when a background job ends (or another session starts one).
    now_active = (any(m['status'] in ('running', 'starting', 'cancelling') or (m['status'] == 'invalid' and m['scenario'] == st.session_state.get('scenario')) for m in runs) or bool(st.session_state.get('runtime_needs_restart')))
    if now_active != active:
        st.rerun(scope='app')
    labels = {m["dir"]: run_label(m) for m in runs}
    keys = list(labels)
    w = st.session_state.get("watch_run")
    idx = keys.index(w) if w in keys else 0
    sel = st.selectbox("查看运行", keys, index=idx, format_func=labels.get, key="run_sel")
    st.session_state["watch_run"] = sel
    m = next(x for x in runs if x["dir"] == sel)
    if m.get('status') == 'invalid':
        st.error('这条运行的元数据损坏，状态、时间与完成范围未核实；不会标记为完成或尝试自动恢复。')
        st.caption(str(Path(sel) / 'meta.json') + '：' + m['metadata_error'])
        st.info('请从备份核对并恢复该文件。可在“查看运行”切换其他记录；原文件未改写。')
        return
    if m.get("demo_description"):st.info(m["demo_description"])
    st.caption(f"所看结果来自：{m['scenario']} · 报价 v{m.get('bid_version', '未记录')}。采用该次冻结输入，不随上方修改变化。")
    logp = Path(sel) / "run.log"
    log = logp.read_text(encoding="utf-8", errors="replace") if logp.exists() else ""
    s = m["status"]
    from run_recovery_controls import render_run_recovery
    render_run_recovery(m, sc.RUNS_DIR)
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.progress(run_progress(m, log), text=f"{STATUS_CN.get(s, s)} · 阶段：{m.get('stage', '')}")
    elapsed, estimated = elapsed_for_display(m)
    c2.metric("启动后约（s）" if estimated else "用时（s）",
              f"{elapsed:.0f}" if elapsed is not None else "未知",
              help="运行中按记录的启动时间估算墙钟时间，包含准备与等待；不是优化器纯求解时间。" if estimated else "已保存的运行耗时；缺失时保持未知。")
    if s in ("running", "starting") and c3.button("请求取消（窗口边界）"):
        stop_run(m)
        st.rerun()
    if s == "failed":
        failure_detail = m.get("error") or "运行失败，见日志"
        # Keep actionable resource context visible without presenting a solver
        # traceback as the business summary or asserting an unverified cause.
        failure_summary = ("运行失败，请核对水电状态与约束；不能视为完整日结果"
                           if "水电" in str(failure_detail)
                           else "运行失败，不能视为完整日结果")
        business_error(failure_summary, failure_detail, "请展开技术诊断查看原因；已执行前缀仍可查看。")
    if s == "done":
        st.info("所选运行范围计算结束，可到“结果”页查看；这不代表精度或物理检查通过。" + (" 注意：这是调试截断范围，不是完整日。" if m.get("periods") and m["periods"] < 96 else ""))
        render_open_saved_result(m)
    if (Path(sel)/"cancel.request").exists() and s in ("running", "starting", "cancelling"):
        st.warning("取消已请求；当前SCUC/SCED不强杀，完成后停止。已执行前缀不是完整日结算。")
    from smm.live_runner import read_live
    live = read_live(sel)
    if s == "done":
        from market_views import result_health_dashboard, _saved_json
        saved_summary = _saved_json(Path(sel)/'summary.json')
        saved_da = saved_summary.get('da_summary') or _saved_json(Path(sel)/'da/summary.json')
        if not saved_da and not (Path(sel)/'da').exists():saved_da = saved_summary
        result_health_dashboard(saved_summary, saved_da, directory=sel, live=live)
    if live.get('da_stages'):
        from market_views import stages_dashboard
        with st.expander('日前SCUC/SCED阶段诊断（不是实时执行）'):
            stages_dashboard(live['da_stages'],prefix='da_stage:'+sel)
    if live.get("executed"):
        st.subheader(f"实时已执行：{live['executed_periods']} / {live['total_periods']} 时段")
        st.caption("每个15分钟窗口结束立即刷新。下图/表只含已执行首时段；前瞻计划单独展示且会被下一窗口修订。")
        from common import display_frame
        executed=live["executed"]
        from market_views import system_metrics
        system_metrics(executed)
        from market_views import reserve_dashboard
        with st.expander('安全备用：系统与逐主体（非交易市场）'):
            reserve_dashboard(executed,prefix='live_reserve:'+sel)
        prices=pd.DataFrame(executed["prices"])
        st.line_chart(prices.groupby("period")[["lmp", "lmp_raw"]].mean().rename(columns={"lmp": "结算电价", "lmp_raw": "原始节点电价"}).rename_axis("时段"), y_label="节点算术均价（元/MWh）")
        units=pd.DataFrame(executed["units"])
        st.area_chart(units.groupby(["period","type"]).P_mw.sum().unstack(fill_value=0).rename(columns=TYPE_CN).rename_axis("时段"), y_label="已执行分类型净功率 MW")
        st.dataframe(display_frame(pd.DataFrame(executed["system"])), hide_index=True, width="stretch")
        with st.expander("当前前瞻窗口（非最终执行结果）"):
            st.dataframe(display_frame(pd.DataFrame(live.get("lookahead",[]))), hide_index=True, width="stretch")
        from market_views import unit_dashboard, stages_dashboard, frozen_bids, frozen_units
        from simulation_results import render_all_unit_parameters
        render_all_unit_parameters(executed, prefix="all_units:" + sel)
        unit_dashboard(executed, bids=frozen_bids(sel), prefix="live:" + sel, units_config=frozen_units(sel))
        with st.expander("选择滚动窗口查看SCUC/SCED阶段对照",expanded=False):
            history=live.get('stage_history',{})
            if history:
                window=st.selectbox('窗口起点',list(history),format_func=lambda x:f'第{int(x)}段',key='stage_window:'+sel)
                stages_dashboard(history[window],prefix='live_stage:'+sel)
            else:stages_dashboard(None,prefix='live_stage:'+sel)
        with st.expander('水电实际状态与窗口末跟踪目标',expanded=False):
            from market_views import hydro_window_dashboard
            hydro_window_dashboard(live.get('hydro_window_history',{}),prefix='live_hydro:'+sel)
        if not live.get("settled"):
            st.caption("滚动前缀仅用于观察，不包含完整日最终结算；失败/取消不会冒充完成结果。")
    elif live.get("phase"):
        phase_label = {"preparing": "准备输入", "da": "日前出清", "da_done": "日前完成", "realtime": "实时滚动", "done": "完成", "failed": "失败", "stopped": "停止"}.get(live["phase"], "处理中")
        st.info(f"当前阶段：{phase_label}；日前尚未完成时不会伪造实时曲线。")
    if not live.get("executed"):
        from simulation_results import saved_da_tables, render_all_unit_parameters
        da_tables = saved_da_tables(Path(sel))
        if not da_tables.get('units', pd.DataFrame()).empty:
            render_all_unit_parameters(da_tables, prefix='all_da:' + sel, stage='日前计划（非实时执行）')
            from market_views import unit_dashboard, frozen_bids, frozen_units
            unit_dashboard(da_tables, bids=frozen_bids(sel), prefix='da_units:' + sel, units_config=frozen_units(sel))
    with st.expander("技术详情：运行日志（最后 40 行）", expanded=False):
        st.code("\n".join(log.splitlines()[-40:]) or "（等待输出…）", language="text")
    with st.expander("技术详情：结果文件位置", expanded=False):
        st.code(sel, language=None)
    st.markdown("**全部运行**")
    st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"]), "报价": str(x.get("bid_version") or "最新"),
                                "状态": STATUS_CN.get(x["status"], x["status"]), "开始": x.get("started"), "用时 s": x.get("elapsed_s"),
                                "目录": Path(x["dir"]).name} for x in runs]), hide_index=True, width="stretch")


monitor()
