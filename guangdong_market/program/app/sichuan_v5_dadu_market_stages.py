"""Read archived decisions at each stage of the Dadu energy-market simulation."""
from math import fsum, isfinite

import streamlit as st


GROUPS = (
    ("hydro", "水电", "按站申报"),
    ("thermal", "煤电", "机组分段申报"),
    ("renewable", "集中式光伏", "市场申报"),
    ("fixed", "气电", "非市场保供计划"),
)
TOL = 1e-5


def _period(rows, quarter):
    return next((row for row in rows if row.get("quarter") == quarter), {})


def _finite(value):
    return isinstance(value, (float, int)) and isfinite(value)


def _sum(values):
    values = list(values)
    return fsum(values) if values and all(_finite(v) for v in values) else None


def _fmt(value, suffix=""):
    return f"{value:,.2f}{suffix}" if _finite(value) else "待核"


def _clock(point):
    hour, minute = divmod(point * 15, 60)
    return f"{hour:02d}:{minute:02d}"


def _sequence(rows, quarter):
    return rows[quarter] if 0 <= quarter < len(rows) else {}


def _nodal(values, bus):
    return values.get(bus, values.get(str(bus)))


def _price_range(curves, units):
    if not all(curves.get(uid) for uid in units):
        return "待核"
    prices = [price for uid in units for _, price in curves[uid]]
    if not prices or not all(_finite(price) for price in prices):
        return "待核"
    low, high = min(prices), max(prices)
    return f"{low:g}" if low == high else f"{low:g}–{high:g}"


def _group_rows(result, quarter, da, rt, realtime):
    inputs = result.get("inputs", {})
    assets = inputs.get("assets", ())
    available = _sequence(inputs.get("available", ()), quarter)
    fixed = _sequence(inputs.get("fixed_schedule", ()), quarter)
    rows = []
    for kind, name, treatment in GROUPS:
        units = [a["id"] for a in assets if a.get("kind") == kind]
        if not units:
            continue
        planned = _sum(da.get("power", {}).get(uid) for uid in units)
        row = {"电源": name, "参与方式": treatment}
        if realtime:
            actual = _sum(rt.get("power", {}).get(uid) for uid in units)
            row.update({"日前预出清 MW": planned, "实时实发 MW": actual,
                        "偏差 MW": actual - planned if None not in (actual, planned) else None})
        else:
            row.update({"设计装机 MW": _sum(a.get("capacity_mw") for a in assets if a["id"] in units),
                        "日前可用 MW": _sum(available.get(uid) for uid in units),
                        "有效报价范围 元/MWh": ("计划" if kind == "fixed" else
                                          _price_range(result.get("effective_offers", {}), units)),
                        "日前预出清 MW": planned})
        if kind == "fixed":
            row["气电保供计划 MW"] = _sum(fixed.get(uid) for uid in units)
        rows.append(row)
    return rows


def _reliability(row):
    shed = _sum(row.get("shed", {}).values())
    excess = _sum(row.get("spill", {}).values())
    reserve = row.get("reliability_reserve_short_mw")
    terms = (("失负荷", shed), ("过剩注入", excess), ("备用缺额", reserve))
    issues = [f"{label} {_fmt(v, ' MW')}" for label, v in terms if _finite(v) and v > TOL]
    if not all(_finite(v) for _, v in terms):
        issues.append("可靠性记录不全")
    return "，".join(issues) if issues else "失负荷、过剩注入、备用缺额均为零"


def _station(result, selected):
    uid = selected["id"] if isinstance(selected, dict) else selected
    return next((a for a in result.get("inputs", {}).get("assets", ()) if a["id"] == uid),
                selected if isinstance(selected, dict) else {"id": uid, "name": uid})


def _commitment(inputs, da):
    coal = [a for a in inputs.get("assets", ()) if a.get("kind") == "thermal"]
    state = da.get("online", {})
    groups = {1: [], 0: [], None: []}
    for unit in coal:
        code = state.get(unit["id"])
        groups[code if code in (0, 1) else None].append(unit.get("name", unit["id"]))
    parts = [label + " " + "、".join(groups[key]) for key, label in
             ((1, "开机"), (0, "停机"), (None, "状态待核")) if groups[key]]
    return "。".join(parts) if parts else "煤机组合记录待核"


def render_day_ahead(result, station, quarter):
    """Show the thermal commitment and the priced fixed-commitment dispatch."""
    da_rows = result.get("da", ())
    da = _period(da_rows, quarter)
    if not da:
        st.info("所选时段缺少日前归档。")
        return
    inputs = result.get("inputs", {})
    asset = _station(result, station)
    uid, bus = asset["id"], asset.get("bus")
    st.markdown("**日前 SCUC · 煤机开停**")
    st.write(_commitment(inputs, da))
    st.caption(f"优化覆盖 {len(da_rows)} 个时段。当前显示 {_clock(quarter)}–{_clock(quarter + 1)} 的组合。")
    st.markdown("**日前 SCED · 固定煤机组合后预出清**")
    columns = st.columns(3)
    columns[0].metric("系统预测负荷", _fmt(_sum(da.get("demand", {}).values()), " MW"))
    columns[1].metric(asset.get("name", uid) + "预出清", _fmt(da.get("power", {}).get(uid), " MW"))
    columns[2].metric(f"节点 {bus} 日前参考价",
                      _fmt(_nodal(da.get("raw_nodal_prices", {}), bus), " 元/MWh"))
    st.caption(f"日前量价仅供参考，日前预出清不结算。所选时段{_reliability(da)}。")
    st.dataframe(_group_rows(result, quarter, da, {}, False), hide_index=True, placeholder="—")
    raw, effective = result.get("raw_offers", {}), result.get("effective_offers", {})
    if asset.get("kind") == "fixed":
        st.caption("气电按签发的保供计划注入。")
    else:
        st.caption(f"{asset.get('name', uid)}冻结申报价 {_price_range(raw, [uid])} 元/MWh，"
                   f"进入出清的有效报价 {_price_range(effective, [uid])} 元/MWh。"
                   "区间取该资源全日曲线的最低和最高段价。")


def render_real_time(result, station, quarter):
    """Show just the first executed quarter of the issued real-time window."""
    rt = _period(result.get("rt", ()), quarter)
    if not rt:
        st.info("所选时段尚无实时执行记录。")
        return
    da = _period(result.get("da", ()), quarter)
    asset = _station(result, station)
    uid, bus = asset["id"], asset.get("bus")
    issued = _period(result.get("issued_forecasts", ()), quarter)
    stop = issued.get("stop")
    st.markdown("**实时滚动 SCED · 每次执行首段**")
    executed = f"执行 {_clock(quarter)}–{_clock(quarter + 1)}"
    if isinstance(stop, int) and quarter < stop <= 96:
        next_window = ("收盘时段执行完毕。" if quarter == 95 else
                       "继续运行时，下一段读取新预测和上一段执行状态。")
        st.caption(f"本次预测窗口 {_clock(quarter)}–{_clock(stop)}（{stop - quarter} 段），"
                   f"{executed}。{next_window}")
    else:
        st.caption(f"{executed}。归档未提供本次预测窗口终点。")
    columns = st.columns(3)
    actual, planned = rt.get("power", {}).get(uid), da.get("power", {}).get(uid)
    difference = actual - planned if _finite(actual) and _finite(planned) else None
    money = _period(result.get("money", {}).get("quarters", ()), quarter)
    price = _nodal(money.get("effective_price_by_bus", {}), bus)
    columns[0].metric("系统实时负荷", _fmt(_sum(rt.get("demand", {}).values()), " MW"))
    columns[1].metric(asset.get("name", uid) + "实发", _fmt(actual, " MW"),
                      delta=f"{difference:+,.2f} MW 较日前" if difference is not None else None,
                      delta_color="off")
    market_unit = asset.get("kind") != "fixed"
    label = "实时采用价" if market_unit else "实时市场参考价"
    columns[2].metric(f"节点 {bus} {label}", _fmt(price, " 元/MWh"))
    raw_price = _nodal(rt.get("raw_nodal_prices", {}), bus)
    physical = _reliability(rt)
    status = f"节点原始实时价 {_fmt(raw_price, ' 元/MWh')}。本段{physical}。"
    if not market_unit:
        st.caption(status + "该气电机组执行保供计划，研究账本未计入其现货收入。")
    elif not _finite(price):
        st.warning(status + "该节点采用价待核，本段收入暂停计价。")
    else:
        st.caption(status)
    st.dataframe(_group_rows(result, quarter, da, rt, True), hide_index=True, placeholder="—")
