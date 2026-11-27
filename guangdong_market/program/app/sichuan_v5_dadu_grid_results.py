"""Read-only operator review of Dadu D-1 and executed real-time archives."""
import math

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart


_LABELS = {"hydro": "水电", "thermal": "煤电", "renewable": "光伏", "fixed": "气电"}


def _time(t):
    h, m = divmod((t + 1) * 15, 60)
    return f"{h:02d}:{m:02d}"


def _sum(values, keys=None):
    if not isinstance(values, dict) or keys is not None and not keys:
        return None
    selected = values.values() if keys is None else [values.get(k) for k in keys]
    selected = list(selected)
    return math.fsum(selected) if all(isinstance(v, (int,float)) and math.isfinite(v)
                                      for v in selected) else None


def _diff(a, b):
    return None if a is None or b is None else a - b


def _row(table, t):
    return table[t] if 0 <= t < len(table) else {}


def _format(number, decimal=1):
    return "未记录" if number is None or pd.isna(number) else f"{number:,.{decimal}f}"


def grid_tables(result):
    """Use physical rows for MW and the archived price decision for valuation status."""
    inputs = result.get("inputs", {})
    assets = inputs.get("assets", ())
    groups = {label:[a["id"] for a in assets if a.get("kind")==kind] for kind,label in _LABELS.items()}
    technical = {r["id"]:r for r in inputs.get("market_case", {}).get("resources", ())}
    names = {a["id"]:a.get("name", a["id"]) for a in assets}
    da = {r["quarter"]:r for r in result.get("da", ())}
    rt = {r["quarter"]:r for r in result.get("rt", ())}
    money = {r["quarter"]:r for r in result.get("money", {}).get("quarters", ())}
    limit = {l["id"]:l["limit_mw"] for l in inputs.get("lines", ()) if l.get("limit_mw", 0.)>0}
    summary, coal, lines = [], [], []
    margin = inputs.get("assumptions", {}).get("forecast_margin", {})
    base_reserve = inputs.get("market_case", {}).get("reserve_mw")
    for t in sorted(da.keys() | rt.keys()):
        d, r = da.get(t, {}), rt.get(t, {})
        label = _time(t)
        demand_da = _sum(d.get("demand", _row(inputs.get("demand", ()), t) or None))
        demand_rt = _sum(r.get("demand"))
        gen = _sum(r.get("power"))
        shed, excess = _sum(r.get("shed")), _sum(r.get("spill"))
        scheduled = _row(inputs.get("fixed_schedule", ()), t)
        pv_da = _sum(_row(inputs.get("available", ()), t) or None, groups["光伏"])
        pv_gen = _sum(r.get("power"), groups["光伏"])
        curtailed = _sum(r.get("curtailed_mw"), groups["光伏"])
        pv_rt = pv_gen+curtailed if None not in (pv_gen, curtailed) else None
        row = {"时段末":label,"日前负荷":demand_da,"实时负荷":demand_rt,"实时总发电":gen,
               "负荷偏差":_diff(demand_rt,demand_da),"光伏可发偏差":_diff(pv_rt,pv_da),
               "实时失供":shed,"实时过剩":excess,"实际供电":_diff(demand_rt,shed),
               "日前总发电":_sum(d.get("power")),"日前上调要求":margin.get("da_requirement_mw",base_reserve),
               "日前已留备用":_sum(d.get("thermal_reliability_reserve_mw")),
               "日前模拟激活":_sum(d.get("forecast_activation_mw")),
               "实时备用要求":(margin.get("rt_requirement_mw",base_reserve) if r else None),
               "实时备用缺额":r.get("reliability_reserve_short_mw"),"保供计划":_sum(scheduled, groups["气电"]),
               "实时计价可靠":money.get(t,{}).get("reliable")}
        row["净负荷偏差（扣光伏可发）"] = _diff(row["负荷偏差"], row["光伏可发偏差"])
        row["功率平衡残差"] = (gen+shed-demand_rt-excess
                             if None not in (gen,shed,demand_rt,excess) else None)
        for kind, units in groups.items():
            row["日前"+kind] = _sum(d.get("power"), units)
            row["实发"+kind] = _sum(r.get("power"), units)
        row["气电计划偏差"] = _diff(row["实发气电"],row["保供计划"])
        for rid in groups["煤电"]:
            tech = technical.get(rid,{})
            dispatch, projected = r.get("power",{}).get(rid), d.get("power",{}).get(rid)
            on = r.get("online",{}).get(rid)
            prior = (tech.get("initial_mw") if t == 0 else rt.get(t-1,{}).get("power",{}).get(rid))
            previous_on = (int(prior>0) if t==0 and prior is not None else
                           rt.get(t-1,{}).get("online",{}).get(rid))
            change = _diff(dispatch,prior)
            ramp = tech.get("ramp_mw_15min")
            if None not in (ramp, change, on, previous_on) and on!=previous_on:
                ramp = ramp+tech["minimum_mw"] if tech.get("minimum_mw") is not None else None
            load_ratio = abs(change)/ramp*100 if change is not None and ramp and ramp>0 else None
            state = { (0,1):"启动",(1,0):"停机",(1,1):"在线",(0,0):"停运" }.get((previous_on,on))
            coal.append({"时段末":label,"机组":names.get(rid,rid),"日前在线":d.get("online",{}).get(rid),
                         "实时状态":state,"日前 MW":projected,"实发 MW":dispatch,
                         "较上段 MW":change,"本段允许变幅 MW":ramp if change is not None else None,
                         "爬坡使用率 %":load_ratio})
        highest = {}
        for lid, rating in limit.items():
            flows = {"日前基态":d.get("flows",{}).get(lid), "日前激活后":d.get("forecast_stress_flows_mw",{}).get(lid),
                     "实时基态":r.get("flows",{}).get(lid)}
            line = {"时段末":label,"支路":lid,"限额 MW":rating}
            for mode, flow in flows.items():
                ratio = abs(flow)/rating*100 if flow is not None else None
                line[mode+" MW"], line[mode+" %"] = flow,ratio
                if ratio is not None and (mode not in highest or ratio>highest[mode][0]):
                    highest[mode] = (ratio,lid)
            lines.append(line)
        for mode in ("日前基态","日前激活后","实时基态"):
            row[mode+"最高 %"],row[mode+"支路"] = highest.get(mode, (None,None))
        summary.append(row)
    return pd.DataFrame(summary), pd.DataFrame(coal), pd.DataFrame(lines)


def _plot(frame, names, unit):
    valid = [key for key in names if key in frame and frame[key].notna().any()]
    if valid:
        time_chart(frame.set_index("时段末")[valid], unit, height=255)


def _download(frame, suffix, result):
    name = str(result.get("run","dadu19"))
    st.download_button("下载 CSV",frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{name}-{suffix}.csv",mime="text/csv",key=f"grid_csv_{suffix}_{name}")


def _only_observed(review, columns):
    return any(pd.notna(review.get(c)) and review[c] > 1.e-5 for c in columns)


def dispatch_period_evidence(result, main, lines, quarter):
    """Put the same operating instant on one column; omit unexecuted neighbors."""
    executed = {r["quarter"]:r for r in result.get("rt", ())}
    if quarter not in executed:
        return pd.DataFrame()
    inputs = result.get("inputs", {})
    coal_ids = [a["id"] for a in inputs.get("assets", ()) if a.get("kind") == "thermal"]
    system = main.set_index("时段末")
    columns = {}
    for t, stage in ((quarter-1,"上一段"),(quarter,"选中"),(quarter+1,"下一段")):
        if t not in executed:
            continue
        rt = executed[t]
        at = _time(t)
        row = system.loc[at]
        def pair(a, b):
            return f"{_format(row[a])} → {_format(row[b])}"
        active = rt.get("online", {})
        on = _sum(active, coal_ids)
        available = _row(inputs.get("available", ()), t)
        online_cap = (math.fsum(available[r] for r in coal_ids if active[r])
                      if on is not None and all(r in available for r in coal_ids) else None)
        limit_lines = lines.loc[lines["时段末"]==at].dropna(subset=["实时基态 %"]) if not lines.empty else lines
        cap_lines = sorted(limit_lines.loc[limit_lines["实时基态 %"]>=99.9999, "支路"]) if not limit_lines.empty else []
        if limit_lines.empty:
            stress = "未记录"
        elif cap_lines:
            stress = f"{len(cap_lines)} 条 · " + "、".join(cap_lines)
        else:
            stress = f"0 条 · 最高 {row['实时基态支路']} {_format(row['实时基态最高 %'])}%"
        lost = rt.get("shed")
        if lost is None:
            locations = "未记录"
        else:
            victims = sorted(((str(b),v) for b,v in lost.items() if v>1.e-5),key=lambda item:(-item[1],item[0]))
            locations = "、".join(f"{b}（{v:,.1f} MW）" for b,v in victims) or "无"
        reliable = row["实时计价可靠"]
        price_status = "未记录" if pd.isna(reliable) else ("可靠性校核通过" if reliable else "可靠性校核未通过")
        columns[f"{stage} · {at}"] = {
            "系统负荷（日前 → 实时，MW）": pair("日前负荷","实时负荷"),
            "煤电出力（日前 → 实发，MW）": pair("日前煤电","实发煤电"),
            "煤机在线台数 / 按日前能力合计 MW": f"{_format(on,0)} / {_format(online_cap)}",
            "水电出力（日前 → 实发，MW）": pair("日前水电","实发水电"),
            "光伏出力（日前 → 实发，MW）": pair("日前光伏","实发光伏"),
            "保供气电（计划 → 实发，MW）": pair("保供计划","实发气电"),
            "日前备用送达校验（MW）": _format(row["日前模拟激活"]),
            "实时备用要求 / 缺额（MW）": f"{_format(row['实时备用要求'])} / {_format(row['实时备用缺额'])}",
            "全网失供 / 过剩（MW）": f"{_format(row['实时失供'])} / {_format(row['实时过剩'])}",
            "失供节点与当地失供量": locations,
            "达到限额的实时线路": stress,
            "实时价格可靠性标记": price_status,
        }
    return pd.DataFrame(columns).rename_axis("核查项").reset_index()


def _period_review(result, main, lines, quarter):
    evidence = dispatch_period_evidence(result, main, lines, quarter)
    if evidence.empty:
        return
    key = str(result.get("run","dadu19"))
    by_time = main.set_index("时段末")
    issue = ["实时失供","实时过剩","实时备用缺额"]
    affected = sorted(r["quarter"] for r in result["rt"] if _only_observed(by_time.loc[_time(r["quarter"])], issue))
    with st.expander(f"{_time(quarter)} 系统运行对照 · 当段与前后 15 分钟", expanded=quarter in affected):
        st.dataframe(evidence,hide_index=True, placeholder="—",height="content")
        st.caption("日前送达校验使用日前运行点，实时备用缺额按当段要求计算。达到线限只说明该断面的潮流已到边界；归因需结合调度约束及反事实复算。")
        shared_key = "dadu_q_"+key
        if len(affected)>1 and shared_key in st.session_state:
            before = [q for q in affected if q < quarter]
            after = [q for q in affected if q > quarter]
            earlier, later = st.columns(2)
            for container, caption, token, target in (
                    (earlier,"上一个异常时段","prev",before[-1] if before else None),
                    (later,"下一个异常时段","next",after[0] if after else None)):
                container.button(caption if target is None else f"{caption} · {_time(target)}",
                                 key=f"grid_issue_{token}_{key}",disabled=target is None,
                                 on_click=st.session_state.update,args=({shared_key:target, "dadu_focus_quarter":target},))


def render_grid_detail(result, quarter):
    """Render the operator drilldown for the zero-based quarter selected on the results page."""
    main, coal, lines = grid_tables(result)
    if main.empty:
        st.caption("当前档案尚无日前或实时出力记录。")
        return
    key = str(result.get("run","dadu19"))
    label = _time(quarter)
    selected = main.loc[main["时段末"]==label]
    _period_review(result, main, lines, quarter)
    area = st.radio("调度复核", ("供需与偏差","煤机与气电","线路与备用"),
                    horizontal=True,key="grid_area_"+key)
    if area == "供需与偏差":
        _plot(main,["日前负荷","实时负荷","日前总发电","实时总发电"],"MW")
        source = st.selectbox("比较电源出力", list(_LABELS.values()),key="grid_fleet_"+key)
        _plot(main,["日前"+source,"实发"+source],"MW")
        with st.expander("预测偏差与供电平衡"):
            _plot(main,["负荷偏差","光伏可发偏差","净负荷偏差（扣光伏可发）","实时失供"],"MW")
            st.caption("偏差为实时段初边界减日前预测。光伏可发等于实发加弃光。功率平衡残差为总发电加失供，减去负荷与过剩。")
            values = main[["时段末","负荷偏差","光伏可发偏差","净负荷偏差（扣光伏可发）","实时失供","实时过剩","功率平衡残差","实时计价可靠"]]
            st.dataframe(values,hide_index=True, placeholder="—")
            _download(main,"system",result)
    elif area == "煤机与气电":
        _plot(main,["保供计划","实发气电","气电计划偏差"],"MW")
        st.caption("气电执行非市场保供计划。保供偏差按两台机组合计的实发减计划计算。")
        if not coal.empty:
            unit = st.selectbox("查看煤机",coal["机组"].unique(),key="grid_coal_"+key)
            one = coal[coal["机组"]==unit]
            _plot(one,["日前 MW","实发 MW"],"MW")
            st.dataframe(coal[coal["时段末"]==label],hide_index=True, placeholder="—")
            with st.expander("煤机逐段开停与爬坡明细"):
                st.caption("变幅按连续执行段计算，首段与初始出力比较。启停段允许变幅含模型采用的最小稳定出力修正。")
                st.dataframe(one,hide_index=True, placeholder="—")
                _download(coal,"coal",result)
    else:
        _plot(main,["日前基态最高 %","日前激活后最高 %","实时基态最高 %"],"线路使用率 %")
        if main["日前模拟激活"].isna().all():
            st.caption("本档案没有备用网络激活记录，激活后潮流留空。")
        else:
            _plot(main,["日前上调要求","日前已留备用","日前模拟激活","实时备用要求","实时备用缺额"],"MW")
            st.caption("日前激活按当期负荷占比同步增加节点需求，检查在线煤机能否送达预留功率。图中激活量是日前校验工况。实时失供还受当时负荷、来水、在途水量和机组出力影响。")
        if not lines.empty:
            snap = lines[lines["时段末"]==label].sort_values("实时基态 %",ascending=False)
            with st.expander(f"{label} 各支路潮流与利用率",expanded=True):
                st.dataframe(snap,hide_index=True, placeholder="—",height=235)
            with st.expander("全天负载最高时段与支路"):
                ranking = main.dropna(subset=["实时基态最高 %"]).sort_values("实时基态最高 %",ascending=False)
                st.dataframe(ranking[["时段末","实时基态支路","实时基态最高 %","日前激活后支路","日前激活后最高 %","实时失供","实时备用缺额"]],hide_index=True, placeholder="—",height=235)
                st.caption("使用率按潮流绝对值除以限额，潮流正负沿档案支路方向。高使用率时段用于定位复核，价格及失供原因需要结合约束和反事实试算判断。")
                _download(lines,"lines",result)
    if not selected.empty:
        row = selected.iloc[0]
        if any(pd.notna(row[k]) and row[k]>1.e-5 for k in ("实时失供","实时过剩","实时备用缺额")):
            st.warning(f"{label} 全网失供 {_format(row['实时失供'])} MW，过剩 {_format(row['实时过剩'])} MW，"
                       f"备用缺额 {_format(row['实时备用缺额'])} MW。该时段的价格质量和小时收入请按归档结算标记核对。")
    st.caption(f"已执行 {len(result.get('rt',()))}/96 段。实时曲线仅覆盖已执行段，未记录项留空。")
