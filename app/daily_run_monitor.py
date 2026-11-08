"""Shared live monitor for the single-day and advanced quotation workbenches."""
from contextlib import nullcontext
from pathlib import Path
import pandas as pd
import streamlit as st
from common import (TYPE_CN, MODE_CN, STATUS_CN, sc, run_label, run_status, stop_run)
from input_workflow import business_error
from run_record_actions import elapsed_for_display, render_open_saved_result
from smm.time_grid import result_time_grid


def render_run_monitor(active, compact=False, retry_renderer=None):
    @st.fragment(run_every=2)
    def monitor():
        runs = sc.list_runs()
        if not runs:
            st.info("暂无运行记录。", icon="📭")
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
        st.caption(f"{m['scenario']} · 报价 v{m.get('bid_version', '未记录')} · 本次冻结输入")
        from input_version_notice import render_input_version_notice
        render_input_version_notice(m)
        from run_progress_view import render_progress_card
        evidence = render_progress_card(m)
        log = evidence['log']
        s = m["status"]
        from run_recovery_controls import render_run_recovery
        render_run_recovery(m, sc.RUNS_DIR)
        if s in ("running", "starting") and st.button("请求取消（安全边界）"):
            stop_run(m)
            st.rerun()
        if s == "failed":
            failure_detail = m.get("error") or "运行失败，见日志"
            admission_failed = 'smm.run_admission.RunDirectoryError:' in log
            if admission_failed:
                cause = next((line for line in reversed(log.splitlines()) if line.startswith('smm.run_admission.RunDirectoryError:')), '')
                failure_detail = '后台启动检查拒绝了本次运行，尚未进入出清求解。' + cause.split(':', 1)[-1]
            # Keep actionable resource context visible without presenting a solver
            # traceback as the business summary or asserting an unverified cause.
            failure_summary = ("启动检查未通过，尚未进入出清求解" if admission_failed else
                               "运行失败，请核对水电状态与约束；不能视为完整日结果"
                               if "水电" in str(failure_detail)
                               else "运行失败，不能视为完整日结果")
            business_error(failure_summary, failure_detail, "请展开技术诊断查看原因；已执行前缀仍可查看。")
        if retry_renderer is not None:
            retry_renderer(m, active=now_active)
        if s == "done":
            st.info("所选范围计算结束，不代表精度或物理检查通过。" + (" 注意：这是调试截断范围，不是完整日。" if m.get("periods") and m["periods"] < result_time_grid({"snapshot_root": str(Path(sel) / "scenario_snapshot")})[0] else ""))
            render_open_saved_result(m)
        if (Path(sel)/"cancel.request").exists() and s in ("running", "starting", "cancelling"):
            st.warning("取消已请求；当前SCUC/SCED不强杀，完成后停止。已执行前缀不是完整日结算。")
        live = evidence['live']
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
        from stage_results import live_intraday_view
        live_intraday_view(live, prefix='live_id:'+sel)
        if live.get("executed"):
            st.subheader(f"实时已执行：{live['executed_periods']} / {live['total_periods']} 时段")
            st.caption("以下仅含实时已执行首段；前瞻计划另列。")
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
            with st.expander("系统逐时明细") if compact else nullcontext():
                st.dataframe(display_frame(pd.DataFrame(executed["system"])), hide_index=True, width="stretch")
            with st.expander("当前前瞻窗口（非最终执行结果）"):
                st.dataframe(display_frame(pd.DataFrame(live.get("lookahead",[]))), hide_index=True, width="stretch")
            from market_views import unit_dashboard, stages_dashboard, frozen_bids, frozen_units
            from simulation_results import render_all_unit_parameters
            with st.expander("机组执行明细") if compact else nullcontext():
                render_all_unit_parameters(executed, prefix="all_units:" + sel)
                unit_dashboard(executed, bids=frozen_bids(sel), prefix="live:" + sel, units_config=frozen_units(sel))
            with st.expander("选择滚动窗口查看SCUC/SCED阶段对照",expanded=False):
                history=live.get('stage_history',{})
                if history:
                    window=st.selectbox('窗口起点',list(history),format_func=lambda x:f'第{int(x)}段',key='stage_window:'+sel)
                    stages_dashboard(history[window],prefix='live_stage:'+sel,market_stage='RT',window_start=int(window))
                else:stages_dashboard(None,prefix='live_stage:'+sel)
            with st.expander('水电实际状态与窗口末跟踪目标',expanded=False):
                from market_views import hydro_window_dashboard
                hydro_window_dashboard(live.get('hydro_window_history',{}),prefix='live_hydro:'+sel)
            if not live.get("settled"):
                st.caption("已执行前缀，不是完整日结算。")
        elif live.get("phase"):
            phase_label = {"preparing": "准备输入", "da": "日前出清", "da_done": "日前完成", "da_stage": "日前求解阶段完成", "id_round": "日内轮完成（参考计划）", "rt_window": "实时首段已执行", "realtime": "实时滚动", "done": "完成", "failed": "失败", "stopped": "停止"}.get(live["phase"], "处理中")
            st.info(f"当前阶段：{phase_label}")
        if not live.get("executed"):
            from simulation_results import saved_da_tables, render_all_unit_parameters
            da_tables = saved_da_tables(Path(sel))
            if not da_tables.get('units', pd.DataFrame()).empty:
                with st.expander('日前机组计划明细') if compact else nullcontext():
                    render_all_unit_parameters(da_tables, prefix='all_da:' + sel, stage='日前计划（非实时执行）')
                    from market_views import unit_dashboard, frozen_bids, frozen_units
                    unit_dashboard(da_tables, bids=frozen_bids(sel), prefix='da_units:' + sel, units_config=frozen_units(sel))
        with st.expander("技术详情：运行日志（最后 40 行）", expanded=False):
            st.code("\n".join(log.splitlines()[-40:]) or "（等待输出…）", language="text")
        with st.expander("技术详情：结果文件位置", expanded=False):
            st.code(sel, language=None)
        with st.expander('全部运行记录') if compact else nullcontext():
            st.markdown("**全部运行**")
            st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"]), "报价": str(x.get("bid_version") or "最新"),
                                        "状态": STATUS_CN.get(x["status"], x["status"]), "开始": x.get("started"), "用时 s": x.get("elapsed_s"),
                                        "目录": Path(x["dir"]).name} for x in runs]), hide_index=True, width="stretch")

    monitor()
