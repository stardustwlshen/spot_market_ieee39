"""Read-only daily input curves using the shared renewable chart and aggregation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from renewable_input_view import renewable_inputs, selected_curves, renewable_figure, _multiselect

RESOURCES = {"load": "负荷", "wind": "风电", "pv": "光伏", "renewable": "风光合计"}
STAGES = {"da": "日前", "id": "日内", "rt": "实时"}


def scenario_curves(cfg, profiles, *, resource, stages, unit_ids=None, bids=None):
    """Keep actuals fixed, respect current declarations, and never sum missing values."""
    if resource not in RESOURCES or set(stages) - STAGES.keys():
        raise ValueError("曲线选择无效。")
    data = renewable_inputs(cfg, profiles, bids)
    stages = list(dict.fromkeys(stages))
    if resource == "load":
        table, warnings = data.time.copy(), []
        for stage in ["actual", *stages]:
            name = "实际负荷（场景输入）" if stage == "actual" else STAGES[stage] + ("负荷预测" if stage == "da" else "负荷预测基线")
            raw = profiles.get("load_" + stage)
            if raw is None:
                values = pd.Series(np.nan, index=range(cfg.T))
            else:
                values = pd.to_numeric(raw.reset_index(drop=True), errors="coerce").astype(float)
                invalid = ~np.isfinite(values) | values.lt(0)
                invalid |= pd.Series([isinstance(v, (bool, np.bool_)) for v in raw])
                values = values.mask(invalid)
            table[name + "（MW）"] = values
            if values.isna().any():
                warnings.append(f"{name}：{int(values.isna().sum())} 个时段缺失或无效。")
        return table, warnings, "负荷（MW）", False
    kinds = [resource] if resource != "renewable" else ["wind", "pv"]
    units = [uid for uid, kind in data.units.items() if kind in kinds] if unit_ids is None else unit_ids
    quoted = any((uid, "declared") in data.curves for uid in units)
    actual_stages = ["actual", *("effective_da" if stage == "da" and data.quote_known else stage for stage in stages)]
    table, warnings = selected_curves(data, kinds=kinds, stages=actual_stages, unit_ids=units)
    names = {"actual": "实际可用功率（场景输入）", "da": "日前预测",
             "effective_da": "日前可用量（申报优先）" if quoted else "日前预测",
             "id": "日内预测基线", "rt": "实时预测基线"}
    table.columns = [*table.columns[:2], *[names[s] + "（MW）" for s in actual_stages]] if len(table.columns) > 2 else table.columns
    # Only warnings for selected resources matter here; unrelated data remains inspectable elsewhere.
    warnings = [message for message in data.warnings if any(message.startswith(uid + "：") or message.startswith(uid + " ·") for uid in units) or message.startswith("当前报价格式")] + warnings
    return table, warnings, "可用功率（MW）", quoted


def render_scenario_curves(cfg, profiles, *, bids=None, source_rows=None, key_prefix="scenario_curves"):
    st.markdown("**场景输入曲线**")
    data = renewable_inputs(cfg, profiles, bids)
    left, right = st.columns(2)
    resource = left.selectbox("查看资源", list(RESOURCES), format_func=RESOURCES.get, key=key_prefix + ":resource")
    units = [uid for uid, kind in data.units.items() if resource == "renewable" or kind == resource]
    options = [None, *units]
    unit_key = key_prefix + ":unit"
    if unit_key in st.session_state and st.session_state[unit_key] not in options:
        st.session_state[unit_key] = None
    selected = right.selectbox("查看机组", options, format_func=lambda uid: "合计" if uid is None else uid,
                               key=unit_key, disabled=resource == "load")
    selected_units = units if selected is None else [selected]
    quoted = resource != "load" and any((uid, "declared") in data.curves for uid in selected_units)
    labels = {"da": "日前可用量（申报优先）" if quoted else "日前预测", "id": "日内预测基线", "rt": "实时预测基线"}
    stages = _multiselect("对比阶段", list(STAGES), list(STAGES), key_prefix + ":stages", labels.get)
    if resource != "load" and not selected_units:
        st.info("当前场景没有启用的" + RESOURCES[resource] + "机组。")
        return
    table, warnings, yaxis_title, _ = scenario_curves(cfg, profiles, resource=resource, stages=stages,
                                                   unit_ids=selected_units, bids=bids)
    for message in warnings:
        st.warning(message)
    fig = renewable_figure(table, yaxis_title=yaxis_title)
    # One stable reference line makes differences visible without adding another chart.
    if fig.data:
        fig.data[0].update(line=dict(color="#303846", width=3))
    styles = {"日前": ("#d1495b", "dash"), "日内": ("#2a9d8f", "dot"), "实时": ("#1f5fa8", "solid")}
    for trace in fig.data[1:]:
        color, dash = styles[trace.name[:2]]
        trace.update(line=dict(color=color, dash=dash, width=2))
    st.plotly_chart(fig, width="stretch", key=key_prefix + ":chart", config={"displaylogo": False, "responsive": True})
    with st.expander("曲线数据与来源", expanded=False):
        st.caption("日内、实时为预测基线，滚动计算按提前量插值。")
        if resource == "load" and cfg.base.get("tie_line_mode") == "fixed_load":
            st.caption("负荷曲线为母线基础负荷，固定联络线需求另计。")
        if quoted:
            st.caption("日前可用量优先采用当前报价申报；未申报机组使用日前预测。")
        st.dataframe(table, hide_index=True, width="stretch", height=360)
        if source_rows:
            names = {"load"} if resource == "load" else set(selected_units)
            rows = [row for row in source_rows if row.get("列", "").rsplit("_", 1)[0] in names]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
