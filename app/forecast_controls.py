"""Visible, opt-in future-input migration; never edits uploaded curves or history."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def render_forecast_model_control(cfg, name, *, bids=None, disabled=False):
    from smm.forecast_settings import (MODEL_LABELS, apply_model_change, input_fingerprint,
                                       model_name, prepare_model_change)
    from input_workflow import business_error
    try:
        current = model_name(cfg)
    except (ValueError, TypeError) as exc:
        business_error("当前曲线配置不能读取", exc)
        return
    st.caption(f"风光模型：{MODEL_LABELS.get(current, current)} · 离线合成，非实测")
    with st.expander("风光波动与预测误差：预览并采用新版", expanded=False):
        st.caption("新版采用持续偏差与缓慢波动，分别生成日前、日内、实时预测。")
        st.caption("重新生成未上传的合成输入；保留报价、上传列和历史结果。")
        models = list(MODEL_LABELS)
        target = st.selectbox("未来运行的风光曲线模型", models, index=1,
                              format_func=MODEL_LABELS.get, key=f"forecast_target:{name}", disabled=disabled)
        key = f"forecast_preview:{name}"
        if st.button("预览风光曲线变更", key=f"forecast_preview_button:{name}", disabled=disabled or target == current):
            st.session_state.pop(key, None)
            st.session_state[f"forecast_confirm:{name}"] = False
            try:
                st.session_state[key] = prepare_model_change(cfg.root, target)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                business_error("曲线预览失败，场景保持不变", exc)
        preview = st.session_state.get(key)
        if preview is None:
            return
        stale = target != preview["model"] or input_fingerprint(cfg.root) != preview["fingerprint"]
        st.dataframe(pd.DataFrame(preview["summary"]), hide_index=True, width="stretch")
        units = [unit["id"] for unit in cfg.units["renewable"]]
        if units:
            uid = st.selectbox("预览风光机组", units, key=f"forecast_preview_unit:{name}")
            frame = pd.DataFrame({label: source[f"{uid}_{stage}"].to_numpy()
                                  for label, source, stage in (
                                      ("当前实际", preview["previous_profiles"], "actual"),
                                      ("当前日前", preview["previous_profiles"], "da"),
                                      ("预览实际", preview["profiles"], "actual"),
                                      ("预览日前", preview["profiles"], "da"))},
                                 index=preview["profiles"].time)
            st.line_chart(frame, x_label=f"时刻（每{cfg.dt * 60:g}分钟）", y_label="可用功率（MW）")
        if preview["explicit_override_columns"]:
            st.info("这些已上传列仍逐值保留：" + "、".join(preview["explicit_override_columns"]))
        declared = [uid for uid, bid in ((bids or {}).get("units") or {}).items()
                    if isinstance(bid.get("avail_pmax"), (list, tuple)) and len(bid["avail_pmax"]) > 0]
        if declared:
            st.warning("以下机组另有日前申报可用功率，正式出清仍优先使用申报值：" + "、".join(declared))
        if stale:
            st.warning("曲线选择或输入已变化，请重新预览。")
        confirmed = st.checkbox("我确认改变该场景未来运行的合成风光输入", key=f"forecast_confirm:{name}", disabled=disabled or stale)
        if st.button("采用预览曲线模型", key=f"forecast_apply:{name}", disabled=disabled or stale or not confirmed):
            try:
                apply_model_change(cfg.root, preview)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                business_error("未采用曲线变更，请重新预览", exc)
            else:
                st.session_state.pop(key, None)
                st.rerun()
