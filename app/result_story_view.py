"""Streamlit presentation for the result narrative. Calculations live in result_story."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from result_story import (
    coverage_note, credibility_summary, export_payload, handoff_audit, hydro_lookahead,
    issue_detail, narrative_tracks, reserve_balance, soft_terminal_rows, top_issues, value_audit,
)


def _show(frame, note):
    if frame is None or frame.empty:
        st.info(note)
        return
    st.dataframe(frame, hide_index=True, width="stretch")


def render_credibility(result):
    from common import metric_grid
    summary = credibility_summary(result)
    st.markdown("**可信度**")
    metric_grid([
        ("96段是否跑完", summary["periods"]["label"], summary["periods"]["detail"], "只有完整日 96/96 才为是。跑完不等于无松弛。"),
        ("上备用缺额", summary["reserve"]["label"], summary["reserve"]["detail"], "优先使用已执行备用表。缺记录不显示为零。"),
        ("软日末偏差", summary["terminal"]["headline"], "储能、抽蓄、水电分列见下方", "模型罚金不是结算付款。"),
    ], per_row=3)
    st.caption(summary["terminal"]["detail"])
    st.caption("跑完不等于无松弛，也不等于备用缺额或软日末偏差为零。")


def _axis(periods, labels):
    try:
        return [labels[int(period)] for period in periods]
    except (KeyError, TypeError, ValueError, IndexError):
        return [str(period) for period in periods]


def _track_figure(track, labels):
    frame = track.get("frame")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None, "未记录"
    figure = go.Figure()
    missing = []
    if track["kind"] == "wide":
        for stage, color, dash in (("日前", "#1f5fa8", "solid"), ("实时", "#d1495b", "dash")):
            if stage not in frame or not pd.to_numeric(frame[stage], errors="coerce").notna().any():
                missing.append(stage)
                continue
            figure.add_scatter(x=_axis(frame.index, labels), y=frame[stage], name=stage,
                               line=dict(color=color, width=2.2, dash=dash))
    else:
        present = set(frame["stage"].astype(str))
        missing = [stage for stage in ("日前", "实时") if stage not in present]
        for (name, stage), part in frame.groupby(["name", "stage"], sort=True):
            part = part.sort_values("period")
            figure.add_scatter(x=_axis(part["period"], labels), y=part["value"], name=f"{name} · {stage}",
                               line=dict(dash="dash" if stage == "实时" else "solid"))
    if not figure.data:
        return None, "未记录"
    note = "、".join(missing) + "未记录，未用另一侧或零填上。" if missing else ""
    return figure, note


def render_difference_narrative(result, *, buses, labels, curtailment):
    from common import show_fig, style_fig
    st.subheader("差异叙事")
    st.caption("切换一条关键轨迹。下面的问题清单仍按类别内绝对偏差排序，不同物理量不跨类比较。")
    tracks = narrative_tracks(result, buses=buses, curtailment=curtailment)
    labels_by_id = {track["label"]: track for track in tracks}
    selected = st.radio("关键轨迹", list(labels_by_id), horizontal=True, key="story_track:" + str(result.get("dir")))
    track = labels_by_id[selected]
    figure, note = _track_figure(track, labels)
    if figure is None:
        st.info(f"{track['label']}未记录，不补曲线。")
    else:
        if note:
            st.caption(note)
        show_fig(style_fig(figure, f"日前与实时 · {track['label']}", "时段", track["unit"], 320), key="story_track_fig:" + track["id"])
    issues = top_issues(result, buses=buses, curtailment=curtailment)
    st.caption(coverage_note(issues))
    if issues.empty:
        return
    visible = issues.drop(columns=[column for column in ("定位",) if column in issues.columns])
    st.dataframe(visible, hide_index=True, width="stretch")
    choice = st.selectbox("定位时段明细", list(issues["定位"]), key="story_locate:" + str(result.get("dir")))
    selected = issues.loc[issues["定位"].eq(choice)].iloc[0].to_dict()
    detail = issue_detail(result, selected)
    st.caption(selected.get("说明") or "")
    _show(detail, "该行没有可下钻的时段表。")


def render_mechanism_audit(result, *, labels):
    del labels
    st.caption("机制审计默认收起。展开后按单选查看；缺表写未记录，不影响其他块。")
    with st.expander("机制审计", expanded=False):
        page = st.radio(
            "机制审计页",
            ["价值法与软日末", "交接来源与回退", "备用需求供给缺额", "水电日电量与前瞻"],
            horizontal=True,
            key="mechanism_page:" + str(result.get("dir")),
        )
        if page == "价值法与软日末":
            st.caption("窗口末状态价值 v 来自已保存 rt_value_windows。软日末罚金是优化目标，不是结算。")
            windows = value_audit(result)
            _show(windows, "未保存价值窗口，不能显示窗口末 v。")
            touched = soft_terminal_rows(result)
            st.markdown("**软日末触碰**")
            _show(touched, "没有 planned_terminal_soft 记录。不能据此说日末没有偏差。")
        elif page == "交接来源与回退":
            st.caption("来源和回退原因以实时窗口记录为准。没有原因不等于交接成功。")
            counts, detail = handoff_audit(result)
            _show(counts, "未保存交接来源或回退原因。")
            if not detail.empty:
                st.dataframe(detail, hide_index=True, width="stretch")
        elif page == "备用需求供给缺额":
            stage, table = reserve_balance(result)
            st.caption(f"口径：{stage}。下备用列只在存档里有时显示。")
            _show(table, "未保存备用需求、供给或上备用缺额。")
            if not table.empty and {"period", "R_up_req", "R_up_supply", "R_up_short"} <= set(table.columns):
                melted = table.melt(id_vars="period", value_vars=["R_up_req", "R_up_supply", "R_up_short"],
                                    var_name="项目", value_name="MW")
                names = {"R_up_req": "需求", "R_up_supply": "供给", "R_up_short": "缺额"}
                melted["项目"] = melted["项目"].map(names)
                figure = go.Figure(data=go.Heatmap(
                    x=melted["period"], y=melted["项目"], z=melted["MW"], colorscale="YlOrRd",
                    hovertemplate="时段 %{x}<br>%{y} %{z:.3f} MW<extra></extra>",
                ))
                figure.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), title="上备用需求、供给与缺额")
                st.plotly_chart(figure, width="stretch", key="story_reserve_heat")
        else:
            from hydro_daily_energy_views import actual_daily_minimum
            status, actual, problems = actual_daily_minimum(result.get("summary") or {})
            messages = {
                "missing": "水电日电量下限：此结果未记录全阶段审计，不能推断未设下限或已经达标。",
                "inactive": "水电日电量下限：本次明确未启用非空下限。",
                "passed": "水电日电量下限：已保存的完整日审计通过。这不证明备用或全网无松弛。",
                "prefix": "水电日电量下限：只覆盖已执行前缀，最终日电量尚未验收。",
                "failed": "水电日电量下限：已保存审计未通过。",
                "unverified": "水电日电量下限：审计未核实。",
            }
            st.caption(messages.get(status, "水电日电量状态未识别。"))
            _show(actual, "没有逐站日电量进度表。")
            for problem in problems:
                st.caption(problem)
            st.markdown("**前瞻触发**")
            st.caption("参考续程只表达按选定出库估计的日末短缺偏好，不是最大回蓄，也不是全网可行证明。")
            _show(hydro_lookahead(result), "未保存水电前瞻表。")


def render_story_export(result, *, curtailment, buses):
    st.caption("导出是当前查看器生成的摘要，不改结果目录。")
    with st.expander("导出可信度与偏差", expanded=False):
        if st.button("生成导出文件", key="story_export_prepare:" + str(result.get("dir"))):
            st.session_state["story_export_ready"] = str(result.get("dir"))
        if st.session_state.get("story_export_ready") != str(result.get("dir")):
            st.caption("尚未生成。")
            return
        from experiment_contract import code_sha
        from result_story import saved_fingerprint
        saved = saved_fingerprint(result)["inputs"]
        fingerprint = saved[:12] if isinstance(saved, str) and saved not in ("未记录", "记录损坏") else "nofp"
        payload, name = export_payload(result, code_sha=code_sha(), input_fingerprint=fingerprint,
                                       curtailment=curtailment, buses=buses)
        st.download_button(
            "下载可信度与偏差 JSON",
            json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            file_name=name,
            mime="application/json",
            key="story_export_file:" + str(result.get("dir")),
        )
        st.caption("文件名包含当前界面短 SHA 和指纹缩写。冻结配置缺失时，假设值表写未记录。")


def render_experiment_matrix_guide():
    with st.expander("实验矩阵取向：丰枯 × 日内开闭", expanded=False):
        st.markdown(
            "建议四格对照，而不是只看一次运行：\n\n"
            "1. 丰水 · 日内关\n"
            "2. 丰水 · 日内开\n"
            "3. 枯水 · 日内关\n"
            "4. 枯水 · 日内开\n\n"
            "日内产品默认关闭（甘肃模式）。开日内要在单日模拟的高级参数里显式保存后再运行。"
            "本次不自动批跑这四格。请分别运行后，在本页任选两次存档比较。"
        )
        st.info("矩阵空态：没有一键四格批跑。上面的对比只覆盖你选中的存档；选不齐四格时，缺的格子保持未跑。")
