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
    st.caption(f"风光自动生成版本：{MODEL_LABELS.get(current, current)}。自动生成部分为离线合成情景，不代表实测或真实预测效果。")
    with st.expander("风光波动与预测误差：预览并采用新版", expanded=False):
        st.write("新版保留日前、日内、超短期预测误差，用持续偏差和缓慢变化代替过多相邻跳变，并加入15分钟实际风光波动。不会对出清结果做平滑。")
        st.caption("采用后会改变该场景未来运行的合成输入；已有运行结果、报价和上传曲线文件均不改写。上传列仍优先，未上传的预测列可能按新误差重新生成。")
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
            st.line_chart(frame, x_label="时刻（每15分钟）", y_label="可用功率（MW）")
            st.caption("上图是出清前输入，不是调度结果；出清功率仍受报价、送出容量与系统约束影响。")
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
