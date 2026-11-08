"""Common daily conditions: read-only selection, explicit copy on Next."""
from __future__ import annotations

from datetime import datetime
import json
import math

import pandas as pd
import streamlit as st

from input_workflow import business_error


# Display-only labels: preview dictionaries retain the model's field names and values.
_HYDRO_PREVIEW_COLUMNS = {
    "qin": "来水（m³/s）", "v0": "初始库容（万m³）",
    "vmin": "最小库容（万m³）", "vmax": "最大库容（万m³）",
    "v_end": "日末库容下限（万m³）", "qeco": "生态流量（m³/s）",
    "e_day": "日电量上限（MWh）",
}
_ASSESSMENT_PREVIEW_COLUMNS = {
    "stage": "边界阶段", "demand_peak_mw": "峰荷（MW）",
    "demand_mwh": "需求电量（MWh）", "renewable_mwh": "风光可用电量（MWh）",
    "renewable_available_to_demand_ratio": "风光可用电量/负荷（非实发）",
    "net_load_peak_mw": "净负荷峰值（MW）", "net_load_min_mw": "净负荷谷值（MW）",
    "net_ramp_max_abs_mw": "最大相邻净负荷变化（MW）",
    "optimistic_supply_energy_upper_mwh": "乐观供能上界（MWh）",
    "unavoidable_energy_shortfall_lower_mwh": "电量缺口下界（MWh）",
    "instantaneous_shortfall_lower_mw": "功率缺口下界（MW）",
    "energy_or_up_reserve_shortfall_lower_mw": "电能量或上备用缺口下界（MW）",
    "energy_or_up_reserve_deficient_periods": "电能量或上备用不足时段数",
}


def _conditions(cfg):
    from smm.typical_scenarios import MANIFEST
    path = cfg.root / MANIFEST
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if path.exists() and (not isinstance(prior, dict) or prior.get("schema") != 1 or not isinstance(prior.get("baseline_availability"), dict)):
        raise ValueError("场景基准记录不完整，请在完整工作台中核对")
    current = dict(season=cfg.base["season"],
        wind_multiplier=float(prior.get("wind_multiplier", prior.get("renewable_multiplier", 1.))) if prior else 1.,
        pv_multiplier=float(prior.get("pv_multiplier", prior.get("renewable_multiplier", 1.))) if prior else 1.,
        load_multiplier=float(prior.get("load_multiplier", 1.)) if prior else 1.)
    if any(not math.isfinite(current[k]) or not .1 <= current[k] <= 2 for k in ("wind_multiplier", "pv_multiplier", "load_multiplier")):
        raise ValueError("场景基准倍率无效，请在完整工作台中核对")
    return prior, current


def _error_choices(cfg, key, *, disabled=False):
    from smm.resource_error_scenarios import normalized_scenarios, SCENARIO_LABELS
    current = normalized_scenarios(cfg)
    selected = dict(current)
    for column, kind, label in zip(st.columns(3), ("load", "wind", "pv"),
                                   ("负荷预测误差", "风电预测误差", "光伏预测误差")):
        names = SCENARIO_LABELS[kind]
        selected[kind] = column.selectbox(label, list(names), index=list(names).index(current[kind]),
            format_func=lambda value, names=names: names[value], key=key+":error:"+kind,
            disabled=disabled, help="仅重新生成系统预测；上传预测和实际曲线保持不变。")
    return current, selected


def _error_details(cfg, selected, *, preview=None):
    from smm.resource_error_scenarios import scenario_metadata, RESOURCE_LABELS
    forecast = dict(cfg.base.get("forecast") or {})
    forecast["error_scenarios"] = selected
    metadata = scenario_metadata({"forecast": forecast})
    with st.expander("误差方法与参数", expanded=False):
        st.caption("离线合成误差，未按历史数据标定。误差＝预测－实际，正值为高估。")
        st.write("负荷误差按同期负荷缩放，风电按装机缩放并包含场站共同变化，光伏夜间为零。误差具有持续性；滞后项分别对应负荷峰谷、风电爬坡和光伏云团变化。")
        st.write("日前、日内、实时共享部分误差，精度不保证逐阶段改善。上传预测与报价申报可用量优先。")
        rows = []
        for kind, item in metadata["scenarios"].items():
            rows.append({"对象": RESOURCE_LABELS[kind], "情景": item["label"],
                         "日前幅度基准": item["sigma_baseline"]["da"],
                         "日内幅度基准": item["sigma_baseline"]["id"],
                         "实时幅度基准": item["sigma_baseline"]["rt"],
                         "15分钟持续性基准": item["phi_baseline_15min"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        day = metadata["pv_daylight"]
        st.caption(f"随机种子：{cfg.base.get('seed', '沿用配置')}；光伏日照假设：{day['sunrise']:g}–{day['sunset']:g} 时。限幅后的实际误差可能与上表幅度不同。")
        st.caption("幅度、持续性与种子在运行页“高级设置”中调整。")
        if preview is not None:
            if preview.get("error_scenario_summary"):
                st.dataframe(pd.DataFrame(preview["error_scenario_summary"]), hide_index=True, width="stretch")


def _curve_preview(cfg, key, bids, *, preview=None):
    from scenario_curve_view import render_scenario_curves
    from smm.forecast import generate_profiles, apply_profile_overrides
    from smm.profile_sources import override_sources, column_source_rows
    try:
        if preview is None:
            profiles = apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base.get("seed", 42)))
            sources = override_sources(cfg)
            quote_columns = [f"{uid}_da" for uid, bid in (bids or {}).get("units", {}).items()
                             if isinstance(bid, dict) and bid.get("avail_pmax") is not None]
            rows = column_source_rows(cfg, profiles.columns, sources["protected_columns"], quote_columns,
                                      limited=sources["status"] == "unverified_preserve_all")
        else:
            profiles, bids = preview["profiles"], preview["bids"]
            rows = preview.get("column_sources", [])
        render_scenario_curves(cfg, profiles, bids=bids, source_rows=rows, key_prefix=key + ":curves")
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as exc:
        business_error("曲线暂不能显示", exc)


def _curve_warning(cfg, message):
    names = {"load": "负荷", **{unit["id"]: unit["id"] for unit in cfg.units.get("renewable", [])}}
    labels = {"actual": "实际", "da": "日前", "id": "日内", "rt": "实时"}
    replacements = {f"{name}_{stage}": f"{label}·{stage_label}" for name, label in names.items()
                    for stage, stage_label in labels.items()}
    text = str(message).replace("avail_pmax", "申报可用功率")
    for column in sorted(replacements, key=len, reverse=True):
        text = text.replace(column, replacements[column])
    return text


def _current_error_limits(cfg, selected, bids):
    """Read-only disclosure when a saved error recipe is already selected."""
    active = {kind for kind in ("load", "wind", "pv") if selected[kind] != "configured"}
    if not active:
        return
    from smm.profile_sources import override_sources
    source = override_sources(cfg)
    for message in source["warnings"]:
        st.warning(_curve_warning(cfg, message))
    quoted = [u["id"] for u in cfg.units["renewable"] if u["type"] in active
              and bids and bids.get("units", {}).get(u["id"], {}).get("avail_pmax") is not None]
    if quoted:
        st.warning("这些机组的日前出清仍以报价申报可用量为准，误差选择不会替换报价：" + "、".join(quoted))


def render_conditions(cfg, name, *, bids=None, disabled=False):
    """No writes here, including when selections change or validation fails."""
    from smm.typical_scenarios import prepare_typical
    key = "day_condition:" + name
    try:
        prior, current = _conditions(cfg)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        business_error("场景条件暂不能调整", exc, "现有场景保留，可继续查看机组与报价。")
        return None, False, False
    # Never execute an obsolete, now-hidden baseline control from an open session.
    if st.session_state.pop(key+":new_baseline", False):
        st.warning("旧版尚未应用的新基准选择未执行；当前继续使用已保存基准，原输入未改变。")
    new_baseline = False
    selection_key = key + ":saved_basis"
    columns = st.columns(4)
    season = columns[0].selectbox("水文", ["wet", "dry"], index=["wet", "dry"].index(current["season"]),
        format_func=lambda v: "丰水期" if v == "wet" else "枯水期", key=selection_key+":season", disabled=disabled)
    labels = ({1.: "基准", 1.2: "大风", .8: "小风"},
              {1.: "基准", 1.2: "大光", .8: "小光"},
              {1.: "基准", 1.1: "高负荷", .9: "低负荷"})
    selected = dict(season=season)
    for column, field, label, names in zip(columns[1:], ("wind_multiplier", "pv_multiplier", "load_multiplier"), ("风电", "光伏", "负荷"), labels):
        values = list(names)
        if current[field] not in values:
            values.append(current[field])
        selected[field] = column.selectbox(label, values, index=values.index(current[field]),
            format_func=lambda v, names=names: f"{names.get(v, '自定义')} ×{v:g}", key=selection_key+":"+field, disabled=disabled,
            help="相对该场景保存的基准；不会在上次倍率上叠乘。")
    try:
        current_errors, selected_errors = _error_choices(cfg, selection_key, disabled=disabled)
    except (ValueError, TypeError, KeyError) as exc:
        business_error("预测误差配置不能读取", exc, "原配置保留，请在高级设置中核对后再应用。")
        st.session_state["day_condition_pending:" + name] = True
        return None, True, True
    changed = selected != current or selected_errors != current_errors
    st.session_state["day_condition_pending:" + name] = changed
    season_label = "丰水期" if season == "wet" else "枯水期"
    st.caption(f"{cfg.base['date']} · {season_label} · {cfg.T} × {cfg.dt*60:g} 分钟")
    if not changed:
        try:
            _current_error_limits(cfg, selected_errors, bids)
            _curve_preview(cfg, key, bids)
            _error_details(cfg, selected_errors)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            business_error("预测误差来源暂不能核验", exc, "原输入保留，请核对后再运行。")
            st.session_state["day_condition_pending:" + name] = True
            return None, True, True
        return None, False, False
    preview = None
    try:
        if bids is None:
            raise ValueError("当前报价不能读取，不能建立场景副本")
        preview = prepare_typical(cfg.root, bids=bids, **selected,
            error_scenarios=selected_errors,
            use_current_as_baseline=new_baseline,
            wind_event=prior.get("wind_event", "none") if prior and not new_baseline else "none")
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        business_error("所选条件暂不能应用", exc, "原场景未改变；可恢复当前选项，或在完整工作台中核对场景基准。")
        return None, True, True
    st.caption("点击下一步应用所选条件并另存场景副本；原场景与报价保留。")
    for message in preview.get("error_scenario_warnings", []):
        st.warning(_curve_warning(cfg, message))
    _curve_preview(cfg, key, bids, preview=preview)
    _error_details(cfg, selected_errors, preview=preview)
    if preview["availability_changes"]:
        st.warning("将同步这些机组的日前申报可用出力：" + "、".join(preview["availability_changes"]) + "。价格与分段量不变；点击下一步即采用此调整。")
    assessment = preview["assessment"]
    if assessment["tier"] == "pressure_required":
        st.warning("所选条件存在供需缺口风险，应作为压力场景；尚未通过全天出清验证。")
    elif assessment["tier"] == "designed_stress":
        st.warning("当前包含持续低风压力事件，尚未通过全天出清验证。")
    clipped = sum(row["触及装机上限点数"] for row in preview["clipping"])
    if clipped:
        st.warning(f"风光可用出力有 {clipped} 个点触及装机上限，实际增幅会低于设定倍率。")
    with st.expander("查看调整明细"):
        st.caption("合成场景；供需表为出清前初筛。")
        st.dataframe(preview["summary"], hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame(preview["hydro"]).rename(columns=_HYDRO_PREVIEW_COLUMNS),
                     hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame(assessment["stages"]).rename(columns=_ASSESSMENT_PREVIEW_COLUMNS),
                     hide_index=True, width="stretch")
    return preview, True, False


def apply_conditions(name, preview, bids):
    """Called only after the single explicit Next action, never by a selector."""
    from smm.typical_scenarios import create_typical
    season = "丰水" if preview["season"] == "wet" else "枯水"
    destination = name[:20] + "_" + season + "_" + datetime.now().strftime("%m%d_%H%M%S_%f")
    return create_typical(destination, preview, bids=bids,
                          confirm_availability=bool(preview["availability_changes"]))


