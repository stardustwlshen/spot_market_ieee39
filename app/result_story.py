"""Credibility, difference narrative and mechanism tables from one saved run.

Missing files stay unknown. Nothing here writes a result directory or fills gaps with zero.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

KIND_LABEL = {"E": "储能", "Vu": "抽蓄", "V": "水电"}
KIND_ORDER = ("储能", "抽蓄", "水电")
ISSUE_ORDER = ("价差", "弃电", "备用缺口", "SOC/库容偏离", "交接回退")


def _count(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame(value):
    return value if isinstance(value, pd.DataFrame) else None


def _summary(result):
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else {}


def period_status(result):
    """Whether the saved run finished a 96-period real-time day."""
    summary = _summary(result)
    expected = _count(summary.get("expected_periods"))
    executed = _count(summary.get("executed_periods"))
    if executed is None:
        executed = _count(summary.get("periods"))
    scope = summary.get("completed_scope")
    full = bool(result.get("full"))
    if not full and scope not in ("full_day", "debug_prefix"):
        return {"done_96": False, "label": "否", "detail": "仅日前计划，没有 96 段实时执行。"}
    if expected == 96 and executed == 96 and scope == "full_day":
        return {"done_96": True, "label": "是", "detail": "96/96 段已保存，完成范围记为完整日。这不等于无松弛。"}
    if expected is None and executed is None:
        return {"done_96": False, "label": "未记录", "detail": "摘要没有预期段数或已执行段数。"}
    saved = "未记录" if executed is None else str(executed)
    target = "未记录" if expected is None else str(expected)
    scope_text = scope if isinstance(scope, str) and scope else "未记录"
    return {"done_96": False, "label": "否", "detail": f"已保存 {saved}/{target} 段，完成范围 {scope_text}。"}


def _dt_hours(result):
    try:
        from smm.time_grid import result_time_grid
        hours = float(result_time_grid(result)[1])
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return hours if math.isfinite(hours) and hours > 0 else None


def _period_clock(result, period):
    number = _count(period)
    if number is None:
        return "时段未记录"
    hours = _dt_hours(result)
    if hours is None:
        return f"第 {number + 1} 段"
    minutes = int(round(hours * 60 * number))
    return f"{minutes // 60:02d}:{minutes % 60:02d}（第 {number + 1} 段）"


def reserve_shortage(result):
    """Up-reserve shortfall from the executed table, else an explicit summary-only note."""
    summary = _summary(result)
    system = _frame(result.get("rt_reserve_system")) if result.get("full") else None
    stage = "实时已执行"
    if system is None or "R_up_short" not in system.columns:
        system = _frame(result.get("da_reserve_system"))
        stage = "日前"
    if system is not None and "R_up_short" in system.columns and not system.empty:
        short = pd.to_numeric(system["R_up_short"], errors="coerce")
        periods = pd.to_numeric(system["period"], errors="coerce") if "period" in system.columns else pd.Series(range(len(system)))
        valid = short.notna() & periods.notna()
        if valid.any():
            chosen = short[valid].abs().idxmax()
            maximum = float(short.loc[chosen])
            hours = _dt_hours(result)
            total = float(short[valid].clip(lower=0).sum() * hours) if hours is not None else None
            total_text = "时长未记录，未把 MW 缺额换成 MWh" if total is None else f"范围内累计 {total:.3f} MWh"
            return {
                "known": True, "stage": stage, "max_mw": maximum, "total_mwh": total,
                "worst_period": int(periods.loc[chosen]),
                "label": f"{maximum:.3f} MW",
                "detail": f"{stage}最严重 {_period_clock(result, periods.loc[chosen])}；{total_text}。未执行时段不外推。",
            }
    checks = summary.get("checks") if isinstance(summary.get("checks"), dict) else {}
    realtime = summary.get("realtime") if isinstance(summary.get("realtime"), dict) else {}
    maximum = _finite(checks.get("rt_executed_reserve_up_short_max_mw"))
    if maximum is None:
        maximum = _finite(realtime.get("reserve_up_short_max"))
    if maximum is None:
        return {"known": False, "stage": None, "max_mw": None, "total_mwh": None, "worst_period": None,
                "label": "未记录", "detail": "没有逐时段上备用缺额，摘要里也没有最大缺额。"}
    return {"known": True, "stage": "摘要", "max_mw": maximum, "total_mwh": None, "worst_period": None,
            "label": f"{maximum:.3f} MW",
            "detail": "只有摘要最大缺额，没有最严重时段，不能据此画热力或累计电量。"}


def _blank_terminal():
    return {name: {"known": False, "text": "未记录"} for name in KIND_ORDER}


def _terminal_from_records(records):
    grouped = {name: [] for name in KIND_ORDER}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = KIND_LABEL.get(record.get("kind"))
        if name is None:
            continue
        discharge = _finite(record.get("deviation_discharge_mwh"))
        native = _finite(record.get("deviation_native"))
        penalty = _finite(record.get("penalty_cost"))
        if discharge is None and native is None:
            continue
        grouped[name].append({
            "unit": record.get("unit") or "未命名",
            "discharge": discharge, "native": native, "penalty": penalty,
        })
    out = _blank_terminal()
    for name, rows in grouped.items():
        if not rows:
            continue
        parts = []
        touched = False
        for row in rows:
            magnitude = row["discharge"] if row["discharge"] is not None else row["native"]
            if magnitude is not None and abs(magnitude) > 1e-6:
                touched = True
            if row["discharge"] is not None:
                text = f"{row['unit']} {row['discharge']:.4g} 可放电MWh"
            else:
                text = f"{row['unit']} 原生偏差 {row['native']:.4g}（未折可放电MWh）"
            if row["penalty"] is not None:
                text += f"，模型罚金 {row['penalty']:.4g} 元"
            parts.append(text)
        out[name] = {"known": True, "text": "；".join(parts), "touched": touched}
    return out


def _terminal_from_windows(frame):
    """Use an explicit day-end mark. Untagged window rows are not day-end deviations."""
    if frame is None or frame.empty or "kind" not in frame.columns:
        return None
    if "label" in frame.columns:
        day = frame[frame["label"].astype(str).eq("day_end")]
    elif "planned_terminal_soft" in frame.columns:
        day = frame[frame["planned_terminal_soft"].fillna(False).astype(bool)]
    else:
        return None
    if day.empty:
        return _blank_terminal()
    records = []
    for row in day.to_dict("records"):
        native = row.get("deviation_native")
        if native is None and "label" in row and str(row.get("label")) == "day_end":
            native = row.get("delta")
        records.append({
            "kind": row.get("kind"), "unit": row.get("unit"),
            "deviation_discharge_mwh": row.get("deviation_discharge_mwh"),
            "deviation_native": native,
            "penalty_cost": row.get("terminal_penalty_cost"),
        })
    return _terminal_from_records(records)


def terminal_deviations(result):
    """Storage, pumped storage and hydro listed separately."""
    realtime = _summary(result).get("realtime")
    records = realtime.get("executed_terminal_deviation_records") if isinstance(realtime, dict) else None
    if isinstance(records, list) and records:
        groups = _terminal_from_records(records)
        source = "已执行日末记录"
    else:
        parsed = _terminal_from_windows(_frame(result.get("rt_value_windows")))
        if parsed is None:
            groups = _blank_terminal()
            source = "未保存日末标记"
        else:
            groups = parsed
            source = "价值窗口日末行（无单独的已执行日末清单）"
    known = [name for name in KIND_ORDER if groups[name]["known"]]
    if not known:
        headline = "未记录"
    elif len(known) < len(KIND_ORDER):
        headline = "部分记录"
    elif any(groups[name].get("touched") for name in known):
        headline = "有偏差"
    else:
        headline = "已保存为 0"
    detail = "；".join(f"{name} {groups[name]['text']}" for name in KIND_ORDER)
    return {"headline": headline, "detail": detail + f"。来源：{source}。模型罚金不是结算付款。", "groups": groups, "source": source}


def credibility_summary(result):
    periods = period_status(result)
    reserve = reserve_shortage(result)
    terminal = terminal_deviations(result)
    return {"periods": periods, "reserve": reserve, "terminal": terminal}


def _issue(category, subject, period, before, after, delta, note):
    magnitude = None if delta is None or (isinstance(delta, float) and math.isnan(delta)) else abs(float(delta))
    clock = period
    return {
        "类别": category, "对象": subject, "时段": period, "时段标签": clock,
        "日前或需求": before, "实时或实际": after, "差值": delta, "绝对值": magnitude, "说明": note,
    }


_STATE_SPECS = (
    ("storage", "da_storage", "rt_storage", "unit", "E_mwh", "储能电量", "MWh"),
    ("pumped", "da_pumped", "rt_pumped", "unit", "V_up", "抽蓄上库", "万m³"),
    ("hydro", "da_hydro", "rt_hydro", "station", "V_1e4m3", "水电库容", "万m³"),
)


def price_series(result, buses=None):
    """Load-weighted DA/RT prices. None means the curve is unrecorded, not zero."""
    if not result.get("full"):
        return None
    if not isinstance(result.get("da_nodal_prices"), pd.DataFrame) or not isinstance(result.get("rt_prices"), pd.DataFrame):
        return None
    try:
        from final_views import system_price_compare
        frame = system_price_compare(result, buses)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    keep = [column for column in ("日前", "实时") if column in frame and pd.to_numeric(frame[column], errors="coerce").notna().any()]
    return None if not keep else frame.loc[:, keep]


def curtail_series(curtailment):
    """Period curtailment (MWh) from saved valid rows. A missing stage stays out of the frame."""
    stages = (curtailment or {}).get("stages") or {}
    columns = {}
    for stage in ("日前", "实时"):
        detail = (stages.get(stage) or {}).get("detail")
        if not isinstance(detail, pd.DataFrame) or not {"period", "curtail_mwh", "valid"} <= set(detail):
            continue
        valid = detail[detail["valid"].fillna(False).astype(bool)]
        if valid.empty:
            continue
        values = pd.to_numeric(valid["curtail_mwh"], errors="coerce")
        periods = pd.to_numeric(valid["period"], errors="coerce")
        usable = valid.assign(_mwh=values, _period=periods).dropna(subset=["_mwh", "_period"])
        if usable.empty:
            continue
        columns[stage] = usable.groupby("_period")["_mwh"].sum()
    if not columns:
        return None
    return pd.DataFrame(columns).sort_index()


def state_series(result, kind):
    """One saved state family. None when neither stage has a finite series, so the option is omitted."""
    spec = next((row for row in _STATE_SPECS if row[0] == kind), None)
    if spec is None:
        return None
    _, da_key, rt_key, identifier, column, _label, _unit = spec
    pieces = []
    for stage, key in (("日前", da_key), ("实时", rt_key)):
        frame = _frame(result.get(key))
        if frame is None or not {identifier, "period", column} <= set(frame):
            continue
        part = frame[[identifier, "period", column]].copy()
        part["value"] = pd.to_numeric(part[column], errors="coerce")
        part["period"] = pd.to_numeric(part["period"], errors="coerce")
        part = part.dropna(subset=["value", "period", identifier])
        if part.empty:
            continue
        part = part.rename(columns={identifier: "name"})
        part["stage"] = stage
        pieces.append(part[["period", "name", "stage", "value"]])
    if not pieces:
        return None
    return pd.concat(pieces, ignore_index=True)


def narrative_tracks(result, *, buses=None, curtailment=None):
    """Price and curtailment are always offered. State tracks appear only when saved."""
    tracks = [
        {"id": "price", "label": "负荷加权电价", "unit": "元/MWh", "kind": "wide", "frame": price_series(result, buses)},
        {"id": "curtail", "label": "弃电", "unit": "MWh", "kind": "wide", "frame": curtail_series(curtailment)},
    ]
    for kind, _da, _rt, _identifier, _column, label, unit in _STATE_SPECS:
        frame = state_series(result, kind)
        if frame is not None:
            tracks.append({"id": kind, "label": label, "unit": unit, "kind": "state", "frame": frame})
    return tracks


def _price_issues(result, buses):
    if not result.get("full"):
        return []
    try:
        from final_views import system_price_compare
        frame = system_price_compare(result, buses)
    except (KeyError, ValueError, TypeError, AttributeError):
        return []
    if not isinstance(frame, pd.DataFrame) or frame.empty or "实时−日前" not in frame.columns:
        return []
    ranked = frame.dropna(subset=["实时−日前"]).copy()
    if ranked.empty:
        return []
    ranked["绝对值"] = ranked["实时−日前"].abs()
    ranked = ranked.sort_values("绝对值", ascending=False).head(3)
    rows = []
    for period, row in ranked.iterrows():
        rows.append(_issue("价差", "负荷加权电价 元/MWh", int(period), row.get("日前"), row.get("实时"), row.get("实时−日前"), "实时减日前"))
    return rows


def _curtail_issues(curtailment):
    stages = (curtailment or {}).get("stages") or {}
    day = (stages.get("日前") or {}).get("detail")
    live = (stages.get("实时") or {}).get("detail")
    if not isinstance(day, pd.DataFrame) or not isinstance(live, pd.DataFrame):
        return []
    if "curtail_mwh" not in day.columns or "valid" not in day.columns:
        return []

    def totals(frame):
        valid = frame[frame["valid"].fillna(False).astype(bool)]
        if valid.empty:
            return pd.Series(dtype=float)
        return valid.groupby("period")["curtail_mwh"].sum()

    before, after = totals(day), totals(live)
    if before.empty and after.empty:
        return []
    aligned = pd.concat([before.rename("日前"), after.rename("实时")], axis=1)
    aligned["差值"] = aligned["实时"] - aligned["日前"]
    aligned = aligned.dropna(subset=["差值"])
    if aligned.empty:
        return []
    aligned["绝对值"] = aligned["差值"].abs()
    ranked = aligned.sort_values("绝对值", ascending=False).head(3)
    return [_issue("弃电", "弃风弃光合计 MWh", int(period), row["日前"], row["实时"], row["差值"], "同时段已保存弃电，负值表示实时更少")
            for period, row in ranked.iterrows()]


def _reserve_issues(result):
    system = _frame(result.get("rt_reserve_system")) if result.get("full") else None
    stage = "实时"
    if system is None or "R_up_short" not in system.columns:
        system = _frame(result.get("da_reserve_system"))
        stage = "日前"
    if system is None or system.empty or "R_up_short" not in system.columns or "period" not in system.columns:
        return []
    frame = system.copy()
    frame["缺额"] = pd.to_numeric(frame["R_up_short"], errors="coerce")
    frame = frame.dropna(subset=["缺额", "period"])
    if frame.empty:
        return []
    frame["绝对值"] = frame["缺额"].abs()
    ranked = frame.sort_values("绝对值", ascending=False).head(3)
    rows = []
    for _, row in ranked.iterrows():
        demand = row["R_up_req"] if "R_up_req" in frame.columns else None
        supply = row["R_up_supply"] if "R_up_supply" in frame.columns else None
        rows.append(_issue("备用缺口", f"{stage}上备用 MW", int(row["period"]), demand, supply, row["缺额"], "差值列是缺额，不是供给减需求的另一种定义"))
    return rows


def _state_pair(result, da_key, rt_key, identifier, column, label, unit):
    if not result.get("full"):
        return []
    before, after = _frame(result.get(da_key)), _frame(result.get(rt_key))
    if before is None or after is None or not {identifier, "period", column} <= set(before) or not {identifier, "period", column} <= set(after):
        return []
    rows = []
    ids = sorted(set(before[identifier].dropna().astype(str)) & set(after[identifier].dropna().astype(str)))
    for name in ids:
        left = before[before[identifier].astype(str).eq(name)].drop_duplicates("period").set_index("period")[column]
        right = after[after[identifier].astype(str).eq(name)].drop_duplicates("period").set_index("period")[column]
        both = pd.concat([pd.to_numeric(left, errors="coerce").rename("日前"), pd.to_numeric(right, errors="coerce").rename("实时")], axis=1).dropna()
        if both.empty:
            continue
        both["差值"] = both["实时"] - both["日前"]
        period = both["差值"].abs().idxmax()
        rows.append(_issue("SOC/库容偏离", f"{label} {name} {unit}", int(period), both.at[period, "日前"], both.at[period, "实时"], both.at[period, "差值"], "重叠时段中绝对偏差最大的一点"))
    rows.sort(key=lambda row: row["绝对值"] or 0, reverse=True)
    return rows[:3]


def _handoff_issues(result):
    windows = _frame(result.get("rt_value_windows"))
    if windows is None or windows.empty or "reference_fallback_reason" not in windows.columns:
        fallbacks = _frame(result.get("rt_fallbacks"))
        if fallbacks is None or "reason" not in getattr(fallbacks, "columns", []):
            return []
        if fallbacks.empty:
            return [_issue("交接回退", "未发现硬到软回退", None, None, None, 0, "rt_fallbacks 已保存且为空。0 不是缺记录；日内整包回退不一定写在这张表。")]
        counted = fallbacks["reason"].fillna("未写原因").astype(str).value_counts()
        return [_issue("交接回退", reason, None, None, None, int(count), "硬到软或锚点记录，不一定是日内整包回日前")
                for reason, count in counted.items()]
    reasons = windows["reference_fallback_reason"]
    present = reasons.notna() & reasons.astype(str).str.strip().ne("") & reasons.astype(str).ne("None")
    if not present.any():
        return [_issue("交接回退", "未发现回退", None, None, None, 0, "已保存交接原因字段，没有回退原因。0 不是缺记录。")]
    subset = windows.loc[present]
    rows = []
    for reason, part in subset.groupby(subset["reference_fallback_reason"].astype(str)):
        period = int(part["window_start"].dropna().iloc[0]) if "window_start" in part.columns and part["window_start"].notna().any() else None
        rows.append(_issue("交接回退", reason, period, None, None, int(len(part)), "同一原因的窗口条数；差值列是条数"))
    rows.sort(key=lambda row: row["绝对值"] or 0, reverse=True)
    return rows


def top_issues(result, *, buses=None, curtailment=None):
    """One table. Magnitude is ranked inside each category, never across units."""
    rows = []
    rows.extend(_price_issues(result, buses))
    rows.extend(_curtail_issues(curtailment))
    rows.extend(_reserve_issues(result))
    rows.extend(_state_pair(result, "da_storage", "rt_storage", "unit", "E_mwh", "储能电量", "MWh"))
    rows.extend(_state_pair(result, "da_pumped", "rt_pumped", "unit", "V_up", "抽蓄上库", "万m³"))
    rows.extend(_state_pair(result, "da_hydro", "rt_hydro", "station", "V_1e4m3", "水电库容", "万m³"))
    rows.extend(_handoff_issues(result))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["类序"] = frame["类别"].map({name: index for index, name in enumerate(ISSUE_ORDER)}).fillna(len(ISSUE_ORDER))
    frame = frame.sort_values(["类序", "绝对值"], ascending=[True, False], na_position="last")
    frame["时段标签"] = frame["时段"].map(lambda period: _period_clock(result, period) if _count(period) is not None else "时段未记录")
    frame["定位"] = frame.apply(lambda row: f"{row['类别']} · {row['对象']} · {row['时段标签']}", axis=1)
    return frame.drop(columns=["类序"]).reset_index(drop=True)


def issue_detail(result, issue):
    """Saved rows for the selected issue. Unknown scope returns an empty frame."""
    if not isinstance(issue, dict):
        return pd.DataFrame()
    period = _count(issue.get("时段"))
    category = issue.get("类别")
    if category == "备用缺口":
        table = _frame(result.get("rt_reserve_system"))
        if table is None:
            table = _frame(result.get("da_reserve_system"))
        if table is None or period is None or "period" not in table.columns:
            return pd.DataFrame()
        return table[pd.to_numeric(table["period"], errors="coerce").eq(period)]
    if category in ("价差", "SOC/库容偏离", "弃电"):
        pieces = []
        for key in ("da_system", "rt_system", "da_storage", "rt_storage", "da_pumped", "rt_pumped", "da_hydro", "rt_hydro"):
            table = _frame(result.get(key))
            if table is None or period is None or "period" not in table.columns:
                continue
            part = table[pd.to_numeric(table["period"], errors="coerce").eq(period)]
            if not part.empty:
                copied = part.copy()
                copied.insert(0, "结果表", key)
                pieces.append(copied)
        return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if category == "交接回退":
        windows = _frame(result.get("rt_value_windows"))
        if windows is None or "reference_fallback_reason" not in windows.columns:
            return _frame(result.get("rt_fallbacks")) or pd.DataFrame()
        reason = str(issue.get("对象"))
        return windows[windows["reference_fallback_reason"].astype(str).eq(reason)]
    return pd.DataFrame()


def _keep(frame, columns):
    if frame is None or frame.empty:
        return pd.DataFrame()
    existing = [column for column in columns if column in frame.columns]
    return frame[existing].copy() if existing else frame.copy()


def value_audit(result):
    frame = _keep(_frame(result.get("rt_value_windows")), [
        "window_start", "tau", "unit", "kind", "label", "value_native", "value_yuan_per_mwh",
        "state", "lower", "upper", "band_binding", "soft", "terminal_policy", "planned_terminal_soft",
        "deviation_discharge_mwh", "terminal_penalty_cost", "terminal_penalty_yuan_per_discharge_mwh",
    ])
    return frame


def soft_terminal_rows(result):
    frame = value_audit(result)
    if frame.empty or "planned_terminal_soft" not in frame.columns:
        return pd.DataFrame()
    return frame[frame["planned_terminal_soft"].fillna(False).astype(bool)].copy()


def handoff_audit(result):
    frame = _frame(result.get("rt_value_windows"))
    if frame is None or frame.empty:
        return pd.DataFrame(), pd.DataFrame()
    source_column = next((column for column in ("reference_source", "value_source") if column in frame.columns), None)
    if source_column is None and "reference_fallback_reason" not in frame.columns:
        return pd.DataFrame(), frame.head(0)
    rows = []
    if source_column is not None:
        for source, count in frame[source_column].fillna("未记录").astype(str).value_counts().items():
            rows.append({"来源字段": source_column, "取值": source, "条数": int(count)})
    if "reference_fallback_reason" in frame.columns:
        reasons = frame["reference_fallback_reason"].fillna("（无回退原因）").astype(str)
        for reason, count in reasons.value_counts().items():
            rows.append({"来源字段": "reference_fallback_reason", "取值": reason, "条数": int(count)})
    detail_columns = [column for column in ("window_start", "unit", "kind", "reference_source", "value_source", "reference_round", "reference_fallback_reason") if column in frame.columns]
    return pd.DataFrame(rows), frame[detail_columns].copy()


def reserve_balance(result):
    system = _frame(result.get("rt_reserve_system")) if result.get("full") else None
    stage = "实时已执行"
    if system is None or system.empty:
        system = _frame(result.get("da_reserve_system"))
        stage = "日前"
    if system is None or system.empty:
        return stage, pd.DataFrame()
    columns = [column for column in ("period", "R_up_req", "R_up_supply", "R_up_short", "R_dn_req", "R_dn_supply", "R_dn_short") if column in system.columns]
    if "R_up_short" not in columns:
        return stage, pd.DataFrame()
    return stage, system[columns].copy()


def hydro_lookahead(result):
    return _keep(_frame(result.get("rt_hydro_terminal_lookahead")), [
        "window_start", "tau", "unit", "reference_source", "projected_terminal_volume",
        "projected_terminal_shortfall", "projected_terminal_shortfall_mwh", "penalty_cost", "active",
    ])


def saved_fingerprint(result):
    directory = result.get("dir")
    if not directory:
        return {"source": "未记录", "inputs": "未记录"}
    path = Path(directory) / "run_provenance.json"
    if not path.is_file():
        return {"source": "未记录", "inputs": "未记录"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {"source": "记录损坏", "inputs": "记录损坏"}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    return {
        "source": source.get("content_fingerprint") or "未记录",
        "inputs": inputs.get("content_fingerprint") or "未记录",
    }


def frozen_assumption_rows(result):
    """Assumptions from the run snapshot only. Current modes.yaml is not a substitute."""
    directory = result.get("snapshot_root")
    path = Path(directory) / "config" / "modes.yaml" if directory else None
    base_path = Path(directory) / "config" / "base.yaml" if directory else None
    if path is None or not path.is_file():
        return [{"项目": "冻结配置", "当前值": "未记录", "标记": "假设值", "理由": "历史结果没有冻结 modes.yaml，不用当前仓库默认补齐", "配置路径": ""}]
    try:
        import yaml
        from types import SimpleNamespace
        modes = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base = yaml.safe_load(base_path.read_text(encoding="utf-8")) if base_path is not None and base_path.is_file() else {}
    except (OSError, ValueError, UnicodeError):
        return [{"项目": "冻结配置", "当前值": "不能读取", "标记": "假设值", "理由": "冻结文件损坏，未用当前配置代替", "配置路径": ""}]
    if not isinstance(modes, dict) or not isinstance(base, dict):
        return [{"项目": "冻结配置", "当前值": "格式无效", "标记": "假设值", "理由": "不用当前配置代替", "配置路径": ""}]
    rule = base.get("rule_set")
    mode = modes.get(rule) if isinstance(rule, str) else None
    if not isinstance(mode, dict):
        mode = next((value for value in modes.values() if isinstance(value, dict)), {})
    fake = SimpleNamespace(mode=mode, base=base)
    return assumption_rows_from_dicts(fake)


def assumption_rows_from_dicts(cfg):
    from experiment_contract import assumption_rows
    return assumption_rows(cfg)


def export_payload(result, *, code_sha, input_fingerprint, curtailment=None, buses=None):
    issues = top_issues(result, buses=buses, curtailment=curtailment)
    payload = {
        "kind": "ieee39-result-story",
        "viewer_code_sha": code_sha,
        "viewer_input_fingerprint": input_fingerprint,
        "saved_fingerprints": saved_fingerprint(result),
        "credibility": credibility_summary(result),
        "assumptions": frozen_assumption_rows(result),
        "top_issues": json.loads(issues.to_json(orient="records", force_ascii=False)) if not issues.empty else [],
        "note": "查看器导出。跑完不等于无松弛。假设值没有省级规则原文唯一数值。模型罚金不是结算。",
    }
    saved = saved_fingerprint(result)["inputs"]
    stamp = input_fingerprint or (saved[:12] if isinstance(saved, str) and saved not in ("未记录", "记录损坏") else "nofp")
    sha = code_sha or "nosha"
    name = f"credibility_{sha}_{stamp}.json"
    return payload, name


def coverage_note(frame):
    present = set(frame["类别"]) if isinstance(frame, pd.DataFrame) and not frame.empty and "类别" in frame.columns else set()
    missing = [name for name in ISSUE_ORDER if name not in present]
    if not missing:
        return "五类都有可排序的已保存偏差。"
    if not present:
        return "五类都没有可排序的保存记录：价差、弃电、备用缺口、SOC/库容偏离、交接回退。缺记录不是零。"
    return "已列出：" + "、".join(sorted(present, key=ISSUE_ORDER.index)) + "。没有可排序记录：" + "、".join(missing) + "。"
