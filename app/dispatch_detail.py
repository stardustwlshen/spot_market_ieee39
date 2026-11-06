"""Filter the dispatch detail view without changing saved results or model inputs."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence

import pandas as pd
import streamlit as st

from chinese_labels import column_label, csv_bytes, display_frame, value_label
from common import show_df
from smm.time_grid import period_label

PLAN_COLUMNS = ("period", "time", "unit", "type", "bus", "P_mw", "energy_mwh")
_ALIASES = {
    "period": ("时段序号", "时段"),
    "time": ("时刻",),
    "unit": ("主体", "机组"),
    "type": ("资源类型",),
    "bus": ("节点",),
    "P_mw": ("净出力 MW", "日前计划净出力 MW", "实时执行净出力 MW"),
    "energy_mwh": ("净电量 MWh", "中标电量 MWh", "日前中标电量 MWh", "实时执行电量 MWh"),
}


def dispatch_plan_frame(source: pd.DataFrame | None, *, period_hours: float = 0.25) -> pd.DataFrame:
    """Project known English/Chinese columns; keep recorded energy and missing values.

    A missing energy column can be calculated from signed MW at the caller's
    period length. An existing but incomplete column is evidence, not zero or a
    request to replace it. The price-component column ``energy`` is never energy.
    """
    source = source if source is not None else pd.DataFrame()
    columns = {}
    for name in PLAN_COLUMNS:
        actual = next((c for c in (name, *_ALIASES[name]) if c in source), None)
        if actual is not None:
            columns[name] = source[actual].copy()
    frame = pd.DataFrame(columns, index=source.index)
    if "energy_mwh" not in frame:
        frame["energy_mwh"] = (
            pd.to_numeric(frame.P_mw, errors="coerce") * period_hours
            if "P_mw" in frame else float("nan")
        )
    return frame


def dispatch_options(frame: pd.DataFrame) -> dict[str, list]:
    """Only offer recorded identities and valid, recorded period indices."""
    options = {
        name: sorted(frame[name].dropna().astype(str).unique()) if name in frame else []
        for name in ("unit", "type")
    }
    periods = pd.to_numeric(frame.get("period", pd.Series(dtype=float)), errors="coerce")
    valid = periods[(periods >= 0) & (periods % 1 == 0)].dropna()
    options["period"] = sorted(int(p) for p in valid.unique())
    return options


def filter_dispatch_plan(
    frame: pd.DataFrame,
    *,
    units: Sequence[str] = (),
    resource_types: Sequence[str] = (),
    period_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """Empty category selections mean all; combine active filters with AND."""
    mask = pd.Series(True, index=frame.index)
    for column, selected in (("unit", units), ("type", resource_types)):
        if selected:
            mask &= frame[column].astype("string").isin(selected) if column in frame else False
    if period_range is not None:
        if "period" in frame:
            periods = pd.to_numeric(frame.period, errors="coerce")
            mask &= periods.between(*period_range)
        else:
            mask &= False
    return frame.loc[mask].copy()


def dispatch_display_frame(frame: pd.DataFrame, stage: str) -> pd.DataFrame:
    """Use stage-specific quantity labels only in the display/Chinese export."""
    result = display_frame(frame)
    return result.rename(columns={
        column_label("P_mw"): f"{stage}{'执行' if stage == '实时' else '计划'}净出力 MW",
        column_label("energy_mwh"): f"{stage}{'执行' if stage == '实时' else '中标'}电量 MWh",
    })


def dispatch_csv_bytes(frame: pd.DataFrame, stage: str, *, chinese: bool = True) -> bytes:
    return csv_bytes(dispatch_display_frame(frame, stage) if chinese else frame, chinese=False)


def _period_label(period: int, period_hours: float = 0.25) -> str:
    return f"{period_label(period, period_hours)}（序号 {period}）"


def _reset_filters(keys: dict[str, str]):
    for key in keys.values():
        st.session_state.pop(key, None)


def dispatch_detail_table(source: pd.DataFrame | None, stage: str, *, result_key: str, period_hours: float = 0.25) -> pd.DataFrame:
    """Render one searchable table; both downloads use exactly its filtered rows."""
    frame = dispatch_plan_frame(source, period_hours=period_hours)
    st.subheader("逐主体实际出力与执行电量" if stage == "实时" else "逐主体出力与中标电量")
    st.caption(
        ("实时仅显示已执行时段；区外来电沿用同一时段的日前固定计划。" if stage == "实时"
         else "日前显示中标计划，不代表实时实际执行。")
        + f" 电量按每段{period_hours * 60:g}分钟核算，充电/抽水的净出力与净电量为负。"
    )
    if frame.empty:
        st.info("当前阶段没有逐主体出力记录。")
        return frame

    options = dispatch_options(frame)
    prefix = "dispatch_detail:" + hashlib.sha256(f"{result_key}|{stage}".encode()).hexdigest()[:16]
    keys = {name: f"{prefix}:{name}" for name in ("unit", "type", "period")}
    periods = options["period"]
    # A refreshed result can have a different schema/options with the same key.
    for name in ("unit", "type"):
        if keys[name] in st.session_state:
            current = st.session_state[keys[name]]
            valid = [v for v in current if v in options[name]]
            if valid != current:
                st.session_state[keys[name]] = valid
    if periods and keys["period"] in st.session_state:
        if any(v not in periods for v in st.session_state[keys["period"]]):
            st.session_state.pop(keys["period"], None)

    st.button("显示全部", key=f"{prefix}:reset", on_click=_reset_filters, args=(keys,),
              help="清除主体、资源类型和时段筛选。")
    cols = st.columns([1, 1, 1.6])
    with cols[0]:
        units = st.multiselect("主体（不选即全部）", options["unit"], key=keys["unit"],
                               placeholder="全部主体，可输入名称搜索", disabled=not options["unit"])
    with cols[1]:
        types = st.multiselect("资源类型（不选即全部）", options["type"], key=keys["type"],
                               format_func=value_label, placeholder="全部资源类型", disabled=not options["type"])
    with cols[2]:
        period_range = st.select_slider("时段范围（含起止时段）", periods, value=(periods[0], periods[-1]),
                                        format_func=lambda p: _period_label(p, period_hours), key=keys["period"], disabled=len(periods) == 1) if periods else None
        if not periods:
            st.caption("未记录有效时段，无法按时段筛选。")
    # An untouched full range includes rows with an unknown period instead of
    # silently hiding incomplete historical records on the default view.
    active_range = tuple(period_range) if periods and tuple(period_range) != (periods[0], periods[-1]) else None
    filtered = filter_dispatch_plan(frame, units=units, resource_types=types, period_range=active_range)
    st.caption(f"当前显示 {len(filtered):,} / {len(frame):,} 条 · {stage} · 下载仅含下表筛选结果")
    missing = [column_label(c) for c in ("unit", "type", "period", "P_mw") if c not in frame]
    if missing:
        st.caption("未记录字段：" + "、".join(missing) + "；缺失值保持未知。")
    if filtered.empty:
        st.info("没有符合条件的记录。可调整筛选，或点击“显示全部”。")
    show_df(dispatch_display_frame(filtered, stage), "%.2f", height=280)
    basename = f"dispatch_{'rt_executed' if stage == '实时' else 'da_awarded'}_filtered"
    st.download_button("下载当前筛选结果（中文 CSV）", dispatch_csv_bytes(filtered, stage),
                       file_name=basename + "_中文.csv", mime="text/csv", key=f"{prefix}:download",
                       disabled=filtered.empty)
    with st.expander("原始字段兼容下载（同一筛选范围）"):
        st.download_button("下载当前筛选结果（原字段 CSV）", dispatch_csv_bytes(filtered, stage, chinese=False),
                           file_name=basename + ".csv", mime="text/csv", key=f"{prefix}:download_raw",
                           disabled=filtered.empty)
    return filtered
