"""边界条件编辑：场景管理、负荷、风光、报价、机组/储能/水电、线路、市场参数、实时与滚动。"""
import copy
import json
import hashlib
import yaml

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from input_workflow import CURVE_CN, workflow_steps, business_error, cost_bid_comparison, basis_tables, friendly_diagnostic
from common import TYPE_CN, page_setup, period_labels, sc, scen_config, scenario_selector, style_fig

page_setup("输入设置", "选择场景 → 设置边界与报价 → 保存 → 到“运行”页启动仿真")
workflow_steps(1)
name = scenario_selector()
SP = sc.scenario_path(name)


def py(x):
    """numpy → python（ruamel 无法序列化 numpy 类型）。"""
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        v = float(x)
        return int(v) if v.is_integer() else v
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def saved(msg="已保存"):
    st.toast(msg, icon="✅")
    st.cache_data.clear()


st.info("发电侧：核对本主体设备参数、出力预测和报价。电网侧：核对系统负荷、网络限额和运行边界。此提示仅为操作指引，不是账户权限设置。")
st.caption("每个分区需单独保存；报价另存为新版本。场景保存不会启动出清，也不会改变历史运行结果。")
tabs = st.tabs(["场景", "负荷", "新能源", "主体报价", "设备参数", "电网限额", "模型规则", "运行设置", "抽水蓄能"])

# ============================================================================ 场景管理
with tabs[0]:
    meta = sc.scenario_meta(name)
    st.markdown(f"**当前场景：{name}**　来源：{meta.get('source', '')}　创建：{meta.get('created', '')}　修改：{meta.get('modified', '—')}")
    with st.expander("场景文件位置"):
        st.code(str(SP), language=None)
    note = st.text_input("场景说明", meta.get("note", ""))
    if st.button("保存说明"):
        sc.update_meta(name, note=note)
        saved()
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("新建 / 复制")
        new = st.text_input("新场景名", placeholder="如 高负荷_风小")
        src = st.selectbox("复制自", ["当前场景：" + name, "仓库默认 config/"])
        nnote = st.text_input("说明", key="new_note")
        if st.button("创建场景", type="primary"):
            try:
                sc.create_scenario(new, source=None if src.startswith("仓库") else name, note=nnote)
                st.session_state["scenario"] = new
                st.success(f"已创建 {new}")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                business_error("操作未完成，原场景未被覆盖", e, "请核对名称、文件与输入内容后重试。")
    with c2:
        st.subheader("删除")
        if name == sc.DEFAULT:
            st.info("默认场景不能删除。")
        else:
            also = st.checkbox("同时删除该场景的运行结果 runs/" + name)
            conf = st.text_input("输入场景名确认删除", key="del_conf")
            if st.button("删除当前场景", disabled=conf != name):
                sc.delete_scenario(name, delete_runs=also)
                st.session_state["scenario"] = sc.DEFAULT
                st.rerun()

cfg = scen_config(name)
uniform_startup = cfg.mode.get("startup_cost_mode", "state_dependent") == "uniform_per_unit"
base = sc.read_yaml(name, "base")
season_cn = {"wet": "丰水期", "dry": "枯水期"}
forecast_cn = {"wind": "风电", "pv": "光伏", "load": "负荷"}
forecast_key = {v: k for k, v in forecast_cn.items()}
T = cfg.T
labels = period_labels(T)
ov_path = SP / "data" / "profile_overrides.csv"


@st.cache_data(show_spinner=False)
def gen_profiles(name, mtime):
    from smm.forecast import apply_profile_overrides, generate_profiles
    c = scen_config(name)
    raw = generate_profiles(c, c.base["seed"])
    return raw, apply_profile_overrides(c, raw)


def _mt():
    fs = [SP / "config" / "base.yaml", SP / "config" / "units.yaml", ov_path]
    return sum(p.stat().st_mtime for p in fs if p.exists())


raw_prof, prof = gen_profiles(name, _mt())
overrides = pd.read_csv(ov_path) if ov_path.exists() else pd.DataFrame(index=range(T))


def write_overrides(cols: dict):
    """cols: 列名 → 96 点数组（None 表示移除该列覆盖）。"""
    df = pd.read_csv(ov_path) if ov_path.exists() else pd.DataFrame({"period": range(T)})
    try:
        for k, v in cols.items():
            if v is None:
                df = df.drop(columns=[k], errors="ignore")
            else:
                df[k] = np.round(np.asarray(v, float), 3)
        # Reuse model validation before writing: no new physical or market rules.
        from smm.forecast import apply_profile_overrides
        apply_profile_overrides(cfg, raw_prof, df)
    except (ValueError, TypeError) as exc:
        business_error("曲线未保存，原数据保持不变", exc, "请检查行数、缺失值、非负条件及场站装机上限。")
        st.stop()
    keep = [c for c in df.columns if c != "period"]
    if keep:
        ov_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(ov_path, index=False)
    elif ov_path.exists():
        ov_path.unlink()
    sc.update_meta(name)


def curve_fig(df, cols, names, title, ytitle="MW"):
    fig = go.Figure()
    for c, n in zip(cols, names):
        fig.add_scatter(x=labels, y=df[c], name=n, mode="lines")
    unit = {"MW": "功率（MW）"}.get(ytitle, ytitle)
    return style_fig(fig, title, "时刻（15 min）", unit, 340)


# ============================================================================ 负荷
with tabs[1]:
    c1, c2, c3 = st.columns(3)
    ls = c1.number_input("负荷缩放系数（倍）", 0.1, 1.5, float(base["load_scale"]), 0.01,
                         help=f"系统峰荷 ≈ load_scale × 6254 MW；当前 ≈ {float(base['load_scale']) * 6254.23:,.0f} MW")
    season = c2.selectbox("季节（决定水电来水）", ["wet", "dry"], index=["wet", "dry"].index(base["season"]),
                          format_func=lambda s: {"wet": "丰水期", "dry": "枯水期"}[s])
    seed = c3.number_input("预测误差随机种子", value=int(base["seed"]), step=1, help="相同种子与参数可复现相同的模拟曲线，不代表预测精度。")
    if st.button("保存负荷生成设置"):
        base["load_scale"], base["season"], base["seed"] = py(ls), season, int(seed)
        sc.write_yaml(name, "base", base)
        saved()
        st.rerun()
    has_load_ov = any(c in overrides for c in ("load_actual", "load_da"))
    if has_load_ov:
        st.warning("已有逐时段负荷数据，优先于缩放系数生成的曲线。若需恢复自动生成，请清除负荷覆盖。")
    st.plotly_chart(curve_fig(prof, ["load_actual", "load_da", "load_rt"], ["实际", "日前预测", "实时（超短期）预测"],
                              "系统负荷（当前生效）"), width="stretch")
    st.markdown("**96 点编辑**（MW）")
    st.caption("负荷数据必须为有限非负数值。修改实际负荷而不单独覆盖预测时，模型按原相对误差推导预测；已有显式预测覆盖保持优先。")
    k = st.number_input("整体乘以系数（作用于下表后保存）", 0.5, 1.5, 1.0, 0.01)
    ed = st.data_editor(pd.DataFrame({"时刻": labels, "实际 MW": prof.load_actual.round(1), "日前预测 MW": prof.load_da.round(1)}),
                        disabled=["时刻"], hide_index=True, height=300, width="stretch", key="load_ed", column_config={k: st.column_config.NumberColumn(k, min_value=0.0, required=True) for k in ("实际 MW", "日前预测 MW")})
    b1, b2, b3 = st.columns(3)
    if b1.button("保存为负荷覆盖", type="primary"):
        a = ed["实际 MW"].to_numpy(float) * k
        d = ed["日前预测 MW"].to_numpy(float) * k
        changed_da = not np.allclose(ed["日前预测 MW"], prof.load_da.round(1)) or k != 1
        write_overrides({"load_actual": a, "load_da": d if changed_da or "load_da" in overrides else None})
        saved("负荷覆盖已保存")
        st.rerun()
    if b2.button("清除负荷覆盖"):
        write_overrides({"load_actual": None, "load_da": None})
        saved("已清除")
        st.rerun()
    up = b3.file_uploader("上传负荷曲线表（CSV，96 行）", type="csv", key="load_up", help="原始列名：load_actual 为实际负荷；load_da 为可选日前预测。单位MW。")
    if up is not None and st.button("应用上传的负荷 CSV"):
        try:
            df = pd.read_csv(up)
        except (ValueError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            business_error("负荷文件无法读取，原曲线未改变", exc, "请上传格式完整的CSV文本表。")
            st.stop()
        if len(df) != T or "load_actual" not in df:
            business_error(f"负荷文件结构不符合要求：需要 {T} 行及实际负荷列", f"实际行数={len(df)}；实际列={list(df.columns)}；必需列=load_actual", "列名要求见上传控件说明，未应用此文件。")
        else:
            write_overrides({"load_actual": df.load_actual, "load_da": df["load_da"] if "load_da" in df else None})
            saved("已应用上传曲线")
            st.rerun()

# ============================================================================ 风光
with tabs[2]:
    from smm.forecast import PHI, SIGMA
    fo = base.get("forecast") or {}
    sig = {k: {**v, **((fo.get("sigma") or {}).get(k) or {})} for k, v in SIGMA.items()}
    phi = {**PHI, **(fo.get("phi") or {})}
    with st.expander("高级：模拟预测误差（一般无需修改）"):
        st.markdown("**预测误差参数**（假设值）：σ 为标准差（风光按装机占比、负荷按当时负荷占比），φ 为 AR(1) 自相关系数。日内/实时误差与日前误差相关（同一随机序列按 σ 比例缩放 + 30% 独立分量）。")
        pe = st.data_editor(pd.DataFrame([{"对象": forecast_cn[k], "σ日前": sig[k]["da"], "σ日内": sig[k]["id"], "σ实时": sig[k]["rt"], "φ": phi[k]}
                                          for k in ("wind", "pv", "load")]), disabled=["对象"], hide_index=True, key="sig_ed")
        c1, c2 = st.columns(2)
        if c1.button("保存误差参数", type="primary"):
            base["forecast"] = {"sigma": {forecast_key[r["对象"]]: {"da": py(r["σ日前"]), "id": py(r["σ日内"]), "rt": py(r["σ实时"])} for _, r in pe.iterrows()},
                                "phi": {forecast_key[r["对象"]]: py(r["φ"]) for _, r in pe.iterrows()}}
            sc.write_yaml(name, "base", base)
            saved()
            st.rerun()
        if c2.button("恢复默认误差参数"):
            base.pop("forecast", None)
            sc.write_yaml(name, "base", base)
            saved()
            st.rerun()
    re_ids = [u["id"] for u in cfg.units["renewable"]]
    rid = st.selectbox("新能源场站", re_ids, format_func=lambda i: f"{i}（{TYPE_CN.get(next(u for u in cfg.units['renewable'] if u['id'] == i)['type'], '新能源')}，装机 {next(u for u in cfg.units['renewable'] if u['id'] == i)['cap']} MW）")
    st.plotly_chart(curve_fig(prof, [f"{rid}_actual", f"{rid}_da", f"{rid}_rt"], ["实际", "日前预测", "实时预测"], f"{rid} 出力"),
                    width="stretch")
    acc = []
    for c in ["load"] + re_ids:
        for stg in ("da", "rt"):
            e = prof[f"{c}_{stg}"] - prof[f"{c}_actual"]
            acc.append({"对象": "负荷" if c == "load" else c, "阶段": {"da": "日前", "rt": "实时"}[stg], "平均绝对误差（MW）": e.abs().mean(), "均方根误差（MW）": np.sqrt((e ** 2).mean()),
                        "日电量偏差 MWh": e.sum() * cfg.dt})
    with st.expander("预测精度（当前生效曲线）"):
        st.dataframe(pd.DataFrame(acc).round(1), hide_index=True)
    st.markdown(f"**{rid} 96 点编辑**（MW）")
    st.caption("出力必须为有限非负数值且不超过场站装机。修改实际出力而不覆盖预测时，保留原绝对误差并按装机截断；已有显式预测覆盖保持优先。")
    ed2 = st.data_editor(pd.DataFrame({"时刻": labels, "实际 MW": prof[f"{rid}_actual"].round(1), "日前预测 MW": prof[f"{rid}_da"].round(1)}),
                         disabled=["时刻"], hide_index=True, height=300, width="stretch", key=f"re_ed_{rid}", column_config={k: st.column_config.NumberColumn(k, min_value=0.0, max_value=float(next(u["cap"] for u in cfg.units["renewable"] if u["id"] == rid)), required=True) for k in ("实际 MW", "日前预测 MW")})
    b1, b2, b3 = st.columns(3)
    if b1.button(f"保存 {rid} 覆盖", type="primary"):
        changed_da = not np.allclose(ed2["日前预测 MW"], prof[f"{rid}_da"].round(1))
        write_overrides({f"{rid}_actual": ed2["实际 MW"], f"{rid}_da": ed2["日前预测 MW"] if changed_da or f"{rid}_da" in overrides else None})
        saved()
        st.rerun()
    if b2.button(f"清除 {rid} 覆盖"):
        write_overrides({f"{rid}_actual": None, f"{rid}_da": None})
        saved()
        st.rerun()
    up2 = b3.file_uploader("上传新能源曲线表（CSV，96 行）", type="csv", key="re_up", help="列名格式：场站编号_actual（实际）或场站编号_da（日前预测），如W1_actual。单位MW。")
    if up2 is not None and st.button("应用上传的 CSV"):
        try:
            df = pd.read_csv(up2)
        except (ValueError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            business_error("新能源文件无法读取，原曲线未改变", exc, "请上传格式完整的CSV文本表。")
            st.stop()
        ok = [c for c in df.columns if c.rsplit("_", 1)[0] in ["load"] + re_ids and c.rsplit("_", 1)[-1] in ("actual", "da")]
        if len(df) != T or not ok:
            business_error(f"新能源文件需 {T} 行，并包含已配置场站的实际或日前预测列", f"列名格式=<对象>_actual / <对象>_da；实际列={list(df.columns)}", "请按上传控件说明核对列名，未应用此文件。")
        else:
            write_overrides({c: df[c] for c in ok})
            saved(f"已应用 {ok}")
            st.rerun()
    if ov_path.exists():
        st.caption("当前已有显式曲线覆盖；保存后的覆盖优先于自动生成曲线。")
        with st.expander("技术详情：已覆盖的原始字段", expanded=False):
            st.write([c for c in overrides.columns if c != "period"])

# ============================================================================ 报价
with tabs[3]:
    from smm.bids import bid_dir, bids_to_csv, fill_missing_bids, resolve_startup_bid, save_bids, validate_bids

    def editable_bids(version=None):
        """Read an editable draft even when changed equipment invalidates old bids.

        Execution and saving still use strict validation. Never rewrite the source.
        """
        directory = bid_dir(cfg)
        versions = sorted(int(p.stem.split("_v")[-1]) for p in directory.glob("bids_v*.json"))
        if not versions:
            raise FileNotFoundError("本场景没有报价文件")
        selected = versions[-1] if version is None else version
        raw = json.loads((directory / f"bids_v{selected}.json").read_text(encoding="utf-8"))
        return fill_missing_bids(cfg, raw)

    bd = bid_dir(cfg)
    vers = sorted(int(p.stem.split("_v")[-1]) for p in bd.glob("bids_v*.json"))
    if not vers:
        st.warning("本场景还没有报价文件，运行一次会自动生成缺省报价 v1。")
    else:
        rows = []
        for v in vers:
            b = json.loads((bd / f"bids_v{v}.json").read_text(encoding="utf-8"))
            rows.append({"版本": v, "创建": b.get("created"), "说明": b.get("note")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        c1, c2 = st.columns([1, 3])
        v = c1.selectbox("基于报价版本", vers[::-1])
        key = f"draft_{name}_{v}"
        discard = c1.button("放弃修改，重新载入")
        if discard or st.session_state.pop(key + ":reset_widgets", False):
            for widget_key in list(st.session_state):
                if any(widget_key.startswith(f"{prefix}_{key}_") for prefix in ("seg", "se_on", "se", "ve_on", "ve", "su", "nl")):
                    del st.session_state[widget_key]
        if key not in st.session_state or discard:
            st.session_state[key] = editable_bids(v)
            st.session_state[key + ":manual"] = []
            st.session_state.pop(key + ":preview", None)
        draft = st.session_state[key]
        uids = list(draft["units"])
        tmap = {u["id"]: u["type"] for g in ("thermal", "renewable") for u in cfg.units[g]}
        tmap.update({u["id"]: "hydro" for u in cfg.units["hydro"]})
        tmap.update({u["id"]: "storage" for u in cfg.units["storage"]})
        tmap.update({u["id"]: "pumped" for u in cfg.units.get("pumped", [])})
        uid = c2.selectbox("选择报价主体", uids, format_func=lambda u: f"{u}（{TYPE_CN.get(tmap.get(u), tmap.get(u, '未知类型'))}）")
        ub = draft["units"][uid]
        before_edit = copy.deepcopy(ub)
        st.caption("逐个选择机组、场站或储能编辑；提交会保存本次草稿中的全部主体，不会覆盖旧版本。")
        st.caption("段价单位 元/MWh，段量 MW；各段首尾相接、价格递增（储能充电曲线递减）。市场规则约束见 config/modes.yaml（MKT）。")
        curves = (["dis_segments", "ch_segments"] if "dis_segments" in ub else
                  ["gen_segments", "pump_segments"] if "gen_segments" in ub else ["segments"])
        cn = {"segments": "报价曲线", "dis_segments": "放电曲线", "ch_segments": "充电曲线",
              "gen_segments": "抽蓄发电曲线", "pump_segments": "抽蓄抽水报价"}
        if tmap.get(uid) == "pumped":
            st.caption("抽蓄报价规则同储能：发电曲线 ≙ 放电曲线、抽水报价 ≙ 充电曲线（段数按储能 1–5 段），"
                       "发电最低价须高于抽水最高价（同储能，川V4.0）；申报日末上库库容 ≙ 储能申报期末 SOC（日前硬约束）。")
        cols = st.columns(len(curves))
        for c, cv in zip(cols, curves):
            with c:
                st.markdown(f"**{cn[cv]}**")
                df = pd.DataFrame(ub.get(cv) or [], columns=["p_start", "p_end", "price"])
                e = st.data_editor(df.rename(columns={"p_start": "起点 MW", "p_end": "终点 MW", "price": "价格 元/MWh"}),
                                   num_rows="dynamic", hide_index=True, key=f"seg_{key}_{uid}_{cv}", width="stretch")
                e = e.dropna()
                ub[cv] = [dict(p_start=float(r["起点 MW"]), p_end=float(r["终点 MW"]), price=float(r["价格 元/MWh"])) for _, r in e.iterrows()]
        if tmap.get(uid) == "storage":
            c1, c2, c3 = st.columns(3)
            dec = c1.checkbox("申报期末 SOC", value=ub.get("soc_end") is not None, key=f"se_on_{key}_{uid}")
            se = c2.number_input("期末申报 SOC（日前硬约束）", 0.0, 1.0, float(ub.get("soc_end") or 0.5), 0.05, key=f"se_{key}_{uid}")
            ub["soc_end"] = float(se) if dec else None
            c3.caption("不申报则由出清决定期末 SOC（川V4.0 7.7.4）")
        if tmap.get(uid) == "pumped":
            pu = [x for x in cfg.units.get("pumped", []) if x["id"] == uid][0]
            c1, c2, c3 = st.columns(3)
            dec = c1.checkbox("申报日末上库库容", value=ub.get("vup_end") is not None, key=f"ve_on_{key}_{uid}")
            ve = c2.number_input("日末上库库容 万m³（日前硬约束）", float(pu["vup_min"]), float(pu["vup_max"]),
                                 float(ub.get("vup_end") if ub.get("vup_end") is not None else pu["vup0"]), 10.0, key=f"ve_{key}_{uid}")
            ub["vup_end"] = float(ve) if dec else None
            c3.caption("同储能申报期末 SOC：不申报则由出清决定（川V4.0 7.7.4 类推）")
        if tmap.get(uid) in ("coal", "gas_ccgt", "gas_gt"):
            c1, c2 = st.columns(2)
            su = ub.get("startup_cost", {})
            if uniform_startup:
                try:
                    scalar = resolve_startup_bid(cfg, ub)["cost_yuan"]
                except ValueError as exc:
                    scalar = None
                    with c1:
                        business_error("启动报价待修复", exc, "请填写有限、非负的单次启动金额。")
                if "startup_cost_yuan" not in ub:
                    c1.caption("旧报价未含单次启动费：当前显示本机组旧热态报价作为迁移基准；原版本不改写。")
                startup_value = c1.number_input("单次启动报价（元/次）", min_value=0.0,
                    value=None if scalar is None else float(scalar), key=f"su_{key}_{uid}_uniform")
                if startup_value is not None and ("startup_cost_yuan" in ub or startup_value != scalar):
                    ub["startup_cost_yuan"] = float(startup_value)
                c1.caption("同一机组不再区分冷/温/热启动；不同机组、不同类型仍各自报价，不统一成同一个价格。")
            else:
                sue = c1.data_editor(pd.DataFrame([su]), hide_index=True, key=f"su_{key}_{uid}",
                                      column_config={k: st.column_config.NumberColumn(label, min_value=0.0) for k, label in
                                                     {"cold": "冷态启动（元/次）", "warm": "温态启动（元/次）", "hot": "热态启动（元/次）", "very_hot": "极热态启动（元/次）"}.items()})
                ub["startup_cost"] = {k: float(v) for k, v in sue.iloc[0].items()}
            ub["noload_cost"] = float(c2.number_input("空载费 元/h", 0.0, 1e6, float(ub.get("noload_cost", 0.0)), key=f"nl_{key}_{uid}"))
        if tmap.get(uid) in ("wind", "pv"):
            ap = ub.get("avail_pmax")
            st.caption(f"申报可用出力上限（MW）：{ap if ap is not None else '未申报，按预测出力'}")
        cost_bid_comparison(cfg, draft, [uid])
        if ub != before_edit:
            st.session_state[key + ":manual"] = sorted(set(st.session_state.get(key + ":manual", [])) | {uid})
        with st.expander("研究成本基础报价 / 策略平移预览"):
            from smm.bid_simulation import research_cost_bid_preview, simulate_bids
            method = st.radio("报价生成依据", ["研究机组档案生成基础报价", "seed策略平移（非成本依据）"],
                              format_func=lambda value: "随机种子策略平移（非成本依据）" if value.startswith("seed") else value, key=key + ":method")
            selected = st.multiselect("生成报价的主体", uids, key=key + ":selected")
            protected = st.session_state.get(key + ":manual", [])
            st.warning("报价依据与运行成本档案分开保存。修改成本不自动重建报价，修改报价或随机种子不改变利润核算成本；两者数值可能不同，请分别核对。")
            st.caption("研究档案为合成算例假设，不是真实电厂成本。基础报价读取固定运行成本，再叠加报价策略或机会成本。燃料价×热耗+变动运维仅说明原研究边际成本依据；分段加价不改变利润核算成本。水价值、储能保留价值属于报价依据，不当作额外现金费用。")
            st.caption(f"已手工修改、禁止覆盖的主体：{', '.join(protected) or '无'}。生成仅预览；采用后仍需提交为新报价版本。")
            use_strategy = st.checkbox("基础报价另叠加随机种子策略平移（可选，非成本估计）", value=False,
                                       disabled=method.startswith("seed"), key=key + ":strategy")
            needs_seed = method.startswith("seed") or use_strategy
            strategy_seed = st.number_input("策略平移随机种子", min_value=0, max_value=2147483647,
                                            value=int(cfg.base["seed"]), disabled=not needs_seed, key=key + ":seed")
            cost_inputs = {filename: (hashlib.sha256((SP / "config" / filename).read_bytes()).hexdigest()
                            if (SP / "config" / filename).exists() else None)
                           for filename in ("fixed_costs.yaml", "bid_cost_basis.yaml")}
            request = dict(method=method, selected=sorted(selected), seed=int(strategy_seed) if needs_seed else None,
                           cost_inputs=cost_inputs if method.startswith("研究") else None)
            if st.button("生成报价预览（不采用、不保存）", key=key + ":generate"):
                st.session_state.pop(key + ":preview", None)
                try:
                    if method.startswith("研究"):
                        candidate = research_cost_bid_preview(cfg, selected_unit_ids=selected, base_bids=draft,
                                                              protected_unit_ids=protected, strategy_seed=request["seed"])
                    else:
                        candidate = simulate_bids(cfg, selected_unit_ids=selected, base_bids=draft,
                                                  protected_unit_ids=protected, seed=request["seed"])
                    st.session_state[key + ":preview"] = dict(candidate=candidate, baseline=copy.deepcopy(draft), request=request,
                        config=copy.deepcopy(dict(base=cfg.base, mode=cfg.mode, units=cfg.units)))
                except (ValueError, FileNotFoundError, KeyError) as exc:
                    business_error("未生成预览，当前草稿未改变", exc, "请核对所选主体、受保护手工报价及本场景成本与报价依据档案；缺失时不回退到全局档案。")
            preview = st.session_state.get(key + ":preview")
            if preview:
                candidate = preview["candidate"]
                rows = []
                for selected_uid in preview["request"]["selected"]:
                    for curve in ("segments", "dis_segments", "ch_segments", "gen_segments", "pump_segments"):
                        for idx, segment in enumerate(candidate["units"][selected_uid].get(curve, []), 1):
                            rows.append({"主体": selected_uid, "曲线": CURVE_CN.get(curve, "其他报价"), "段": idx,
                                         "起点 MW": segment["p_start"], "终点 MW": segment["p_end"], "预览价格 元/MWh": segment["price"]})
                st.dataframe(pd.DataFrame(rows), hide_index=True)
                basis = candidate.get("_cost_basis")
                if basis:
                    basis_tables(basis)
                cost_bid_comparison(cfg, candidate, preview["request"]["selected"])
                stale = (preview["baseline"] != draft or preview["request"] != request or
                         preview["config"] != dict(base=cfg.base, mode=cfg.mode, units=cfg.units))
                if stale:
                    st.warning("草稿、配置或生成选项已变化，预览已过期；请重新生成，不会覆盖后续编辑。")
                if st.button("采用此预览为草稿（仍未保存）", disabled=stale, key=key + ":accept"):
                    st.session_state[key] = copy.deepcopy(candidate)
                    st.session_state[key + ":reset_widgets"] = True
                    st.session_state.pop(key + ":preview", None)
                    st.rerun()
        st.divider()
        issues = validate_bids(cfg, draft)
        errs = [i for i in issues if i.level == "error"]
        if issues:
            st.markdown(f"**校验结果（MKT 规则）：{len(errs)} 项违规、{len(issues) - len(errs)} 项提示**")
            st.dataframe(pd.DataFrame([{"主体": i.unit_id, "级别": {"error": "违规", "warn": "提示"}.get(i.level, "提示"), "规则": i.rule, "说明": friendly_diagnostic(i.msg)} for i in issues]),
                         hide_index=True, width="stretch")
            with st.expander("技术详情：完整报价校验诊断", expanded=False):
                st.code("\n".join(map(str, issues)), language=None)
        else:
            st.success("校验通过（0 条提示）")
        c1, c2 = st.columns([3, 1])
        bnote = c1.text_input("新版本说明", f"网页编辑，基于 v{v}")
        if c2.button("提交为新版本", type="primary", disabled=bool(errs)):
            p = save_bids(cfg, draft, note=bnote, new_version=True)
            bids_to_csv(json.loads(p.read_text(encoding="utf-8")), p.with_suffix(".csv"))
            sc.update_meta(name)
            del st.session_state[key]
            saved(f"已保存 {p.name}")
            st.rerun()
        if errs:
            st.error("存在违规项，修正后才能提交。")
        st.download_button("下载该版本 JSON", json.dumps(editable_bids(v), ensure_ascii=False, indent=2), f"bids_v{v}.json")

# ============================================================================ 机组/储能/水电
units = sc.read_yaml(name, "units")
with tabs[4]:
    st.caption("此处设置设备能力与初始状态，参数为算例假设值；交易申报请到“主体报价”。保存后请重新校验报价是否符合设备容量。")
    st.markdown("**火电 / 燃气机组**（启动成本：元/次）")
    if uniform_startup:
        st.caption("当前每台机组采用单次启动费，不按冷/温/热状态分档；技术上限沿用该机组原热态基准。不同机组保留各自数值，旧冷/温态技术档保留供历史模式使用。")
    th = pd.DataFrame([{"id": u["id"], "类型": TYPE_CN.get(u["type"], u["type"]), "节点": u["bus"], "pmax": u["pmax"], "pmin": u["pmin"], "ramp": u["ramp"],
                        "ut": u["ut"], "dt": u["dt"], "noload": u["noload"],
                        **({"单次启动费上限（元/次）": u["startup"]["hot"]} if uniform_startup else
                           {"启动费_冷": u["startup"]["cold"], "启动费_温": u["startup"]["warm"], "启动费_热": u["startup"]["hot"]}),
                        "初始并网": bool(u["init"]["online"]), "初始出力": u["init"]["p0"],
                        "已持续h": u["init"]["hours"]} for u in units["thermal"]])
    the = st.data_editor(th, disabled=["id", "类型", "节点"], hide_index=True, width="stretch", key="th_ed", column_config={k: label for k, label in {
        "id": "机组编号", "pmax": "最大出力（MW）", "pmin": "最小出力（MW）", "ramp": "爬坡速率（MW/min）",
        "ut": "最小开机时间（h）", "dt": "最小停机时间（h）", "noload": "空载成本（元/h）",
        "初始出力": "初始出力（MW）", "已持续h": "初始状态持续时间（h）"}.items()})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**新能源装机 / 区外来电**")
        re_t = pd.DataFrame([{"id": u["id"], "类型": TYPE_CN.get(u["type"], u["type"]), "节点": u["bus"], "装机 MW": u["cap"]} for u in units["renewable"]] +
                            [{"id": u["id"], "类型": "区外来电", "节点": u["bus"], "装机 MW": u["pmax"]} for u in units["tie"]])
        ree = st.data_editor(re_t, disabled=["id", "类型", "节点"], hide_index=True, key="re_ed", column_config={"id": "主体编号"})
    with c2:
        hid = st.selectbox("水电站", [u["id"] for u in units["hydro"]],
                           format_func=lambda i: next(f"{i}（节点 {u['bus']}，{'库容型' if u.get('regulation', 'reservoir') == 'reservoir' else '径流式'}"
                                                      f"{'' if u.get('enabled', True) else '，未启用'}）" for u in units["hydro"] if u["id"] == i))
        hy = next(u for u in units["hydro"] if u["id"] == hid)
        cc = st.columns(2)
        hen = cc[0].checkbox("启用", bool(hy.get("enabled", True)), key=f"hy_en_{hid}")
        hreg = cc[1].selectbox("调节类型", ["reservoir", "run_of_river"], index=0 if hy.get("regulation", "reservoir") == "reservoir" else 1,
                               format_func={"reservoir": "库容型（日电量+期末库容约束，川V4.0 附件8 约束(16)(17)）",
                                            "run_of_river": "径流式（来水即发，川V4.0 7.7.2 方式一）"}.get, key=f"hy_reg_{hid}")
        st.markdown(f"**水电 {hy['id']}**（{season_cn[base['season']]}参数；来水 m³/s，库容 万m³）")
        hs = hy["seasons"]
        hs = hy["seasons"]
        hye = st.data_editor(pd.DataFrame([{"季节": season_cn.get(s, s), "来水qin": hs[s]["qin"], "初库容v0": hs[s]["v0"], "vmin": hs[s]["vmin"], "vmax": hs[s]["vmax"],
                                           "期末库容v_end": hs[s]["v_end"], "生态流量qeco": hs[s]["qeco"], "日电量e_day": hs[s]["e_day"]} for s in hs]),
                             disabled=["季节"], hide_index=True, key=f"hy_ed_{hid}", column_config={"来水qin": "来水流量（m³/s）", "初库容v0": "初始库容（万m³）",
                             "vmin": "最小库容（万m³）", "vmax": "最大库容（万m³）", "期末库容v_end": "期末库容（万m³）",
                             "生态流量qeco": "生态流量（m³/s）", "日电量e_day": "日电量上限（MWh）"})
        hn = st.number_input("台数", 1, 8, int(hy["n_units"]), key=f"hy_n_{hid}")
        hp = st.number_input("单台容量 MW", 10.0, 500.0, float(hy["unit_pmax"]), key=f"hy_p_{hid}")
        qout_saved = hy.get("qout_max")
        st.caption("总出库流量 = 发电过机流量 + 弃水流量，不是单独的弃水上限。留空（None）表示未建模此工程上限；0 表示总出库不得大于0，不等于未设置。")
        st.caption(f"已保存总出库上限：{'未建模（None）' if qout_saved is None else f'{qout_saved:g} m³/s'}。来源：{hy.get('qout_max_source') or '未记录，请核实工程资料或明确研究假设'}")
        qout_key = f"hy_qout_{name}_{hid}"
        if qout_key not in st.session_state:
            st.session_state[qout_key] = None if qout_saved is None else float(qout_saved)
        # An explicitly empty default permits clearing a previously numeric limit.
        hqout = st.number_input("总出库流量上限（m³/s；留空=未建模）", min_value=0.0,
                               value=None, step=1.0, placeholder="未建模（None）", key=qout_key)
        hqout_source = st.text_input("总出库上限来源 / 研究假设说明", value=str(hy.get("qout_max_source") or ""),
                                    key=f"hy_qout_source_{name}_{hid}",
                                    help="没有工程资料时请明确标注合成研究假设及推导；不将算例值称为真实电站能力或省级规则。")
    st.markdown("**储能**（SOC 为荷电状态，0–1；最新报价的期末 SOC 是日前硬约束，修改后另存报价版本。缺省报价期末 SOC 仅用于生成初始报价。）")
    try:
        lb = editable_bids()
    except FileNotFoundError:
        lb = None
    ss = pd.DataFrame([{"id": u["id"], "节点": u["bus"], "pmax": u["pmax"], "ecap MWh": u["ecap"], "eta_c": u["eta_c"], "eta_d": u["eta_d"],
                        "soc_min": u["soc_min"], "soc_max": u["soc_max"], "初始SOC soc0": u["soc0"], "soc_end_bid": u["soc_end_bid"],
                        "期末申报SOC(最新报价)": (lb["units"].get(u["id"], {}).get("soc_end") if lb else None)} for u in units["storage"]])
    sse = st.data_editor(ss, disabled=["id", "节点"], hide_index=True, width="stretch", key="ss_ed", column_config={"id": "储能编号", "pmax": "额定功率（MW）",
                         "ecap MWh": "额定能量（MWh）", "eta_c": "充电效率（0–1）", "eta_d": "放电效率（0–1）",
                         "soc_min": "SOC 下限（0–1）", "soc_max": "SOC 上限（0–1）", "初始SOC soc0": "初始 SOC（0–1）",
                         "soc_end_bid": "缺省报价期末 SOC（0–1）", "期末申报SOC(最新报价)": "最新报价期末 SOC（0–1）"})
    if st.button("保存机组/储能/水电参数", type="primary"):
        try:
            from smm.input_validation import number
            if hqout is not None and number(hqout, f"{hid}.qout_max") < 0:
                raise ValueError("总出库流量上限须为非负有限数值或留空")
        except ValueError as exc:
            business_error("未保存，原参数保持不变", exc, "请检查有限数值、非负条件与参数边界。")
            st.stop()
        for u, (_, r) in zip(units["thermal"], the.iterrows()):
            for k in ("pmax", "pmin", "ramp", "ut", "dt", "noload"):
                u[k] = py(r[k])
            if uniform_startup:
                u["startup"]["hot"] = py(r["单次启动费上限（元/次）"])
            else:
                u["startup"]["cold"], u["startup"]["warm"], u["startup"]["hot"] = py(r["启动费_冷"]), py(r["启动费_温"]), py(r["启动费_热"])
            u["init"]["online"], u["init"]["p0"], u["init"]["hours"] = bool(r["初始并网"]), py(r["初始出力"]), py(r["已持续h"])
        for _, r in ree.iterrows():
            for u in units["renewable"]:
                if u["id"] == r["id"]:
                    u["cap"] = py(r["装机 MW"])
            for u in units["tie"]:
                if u["id"] == r["id"]:
                    u["pmax"] = py(r["装机 MW"])
        for _, r in hye.iterrows():
            s = hs[{v: k for k, v in season_cn.items()}.get(r["季节"], r["季节"])]
            for a, b in (("qin", "来水qin"), ("v0", "初库容v0"), ("vmin", "vmin"), ("vmax", "vmax"), ("v_end", "期末库容v_end"),
                         ("qeco", "生态流量qeco"), ("e_day", "日电量e_day")):
                s[a] = py(r[b])
        hy["n_units"], hy["unit_pmax"] = int(hn), py(hp)
        hy["enabled"], hy["regulation"] = bool(hen), hreg
        hy["qout_max"] = None if hqout is None else py(hqout)
        hy["qout_max_source"] = hqout_source.strip()
        for u, (_, r) in zip(units["storage"], sse.iterrows()):
            for a, b in (("pmax", "pmax"), ("ecap", "ecap MWh"), ("eta_c", "eta_c"), ("eta_d", "eta_d"), ("soc_min", "soc_min"),
                         ("soc_max", "soc_max"), ("soc0", "初始SOC soc0"), ("soc_end_bid", "soc_end_bid")):
                u[a] = py(r[b])
        sc.write_yaml(name, "units", units)
        msg = "已保存 units.yaml"
        if lb is not None:
            nb = copy.deepcopy(lb)
            ch = False
            for _, r in sse.iterrows():
                val = r["期末申报SOC(最新报价)"]
                val = None if val is None or (isinstance(val, float) and np.isnan(val)) else float(val)
                if r["id"] in nb["units"] and nb["units"][r["id"]].get("soc_end") != val:
                    nb["units"][r["id"]]["soc_end"] = val
                    ch = True
            if ch:
                cfg2 = scen_config(name)
                iss = [i for i in validate_bids(cfg2, nb) if i.level == "error"]
                if iss:
                    business_error("期末荷电状态或报价校验未通过，未生成新报价", "；".join(map(str, iss)), "设备参数已保存，请修复报价后另存新版本。")
                else:
                    p = save_bids(cfg2, nb, note=f"网页修改储能期末申报 SOC（基于 v{lb['version']}）")
                    bids_to_csv(json.loads(p.read_text(encoding="utf-8")), p.with_suffix(".csv"))
                    msg += f"，期末 SOC 另存为报价 {p.stem}"
        saved(msg)

# 独立核算参数：不读取报价作成本、不自动生成或修改报价。
with tabs[4]:
    st.divider()
    st.subheader("运行成本档案（利润核算，不随随机报价改变）")
    st.caption("仅用于模拟现金运营利润核算，不是报价、出清目标或真实电站审计成本。固定表示参数不随报价随机种子变化，不表示全部费用均为固定费用。充电购电已计入净结算收入；不在此重复计费。")
    st.warning("运行成本与报价依据独立；保存成本不会更新报价、报价研究档案或历史运行。利润核算只对之后使用该成本快照的运行生效。")
    from smm.profit import REQUIRED, load_profile, validate_profile
    cost_path = SP / "config" / "fixed_costs.yaml"
    try:
        costs = load_profile(SP)
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        costs = None
        business_error("运行成本档案格式无效，未按零处理", exc, "请核对档案结构和主体参数。")
    if costs is not None:
        if not costs["available"]:
            st.info("本场景没有固定运行成本档案，成本及利润为未知，不是0。可在下方明确填写某主体参数并保存；其他缺失主体仍为未知。不从报价或全局档案补值。")
        cost_types = {u["id"]: u.get("type", family) for family in ("thermal", "renewable", "hydro", "storage", "pumped") for u in cfg.units.get(family, [])}
        cost_uid = st.selectbox("运行成本主体", list(cost_types), key=f"cost_unit_{name}")
        cost_type = cost_types[cost_uid]
        record = costs.get("units", {}).get(cost_uid, {})
        labels_cost = {
            "scalar_marginal_cost_yuan_per_mwh": "固定边际成本参数（元/MWh发电）",
            "fixed_online_cost_yuan_per_h": "固定在线成本（元/h）",
            "fixed_startup_cost_yuan_per_start": "固定单次启动成本（元/次）",
            "discharge_degradation_yuan_per_mwh": "放电退化成本（元/MWh放电）",
            "generation_vom_yuan_per_mwh": "抽蓄发电变动运维（元/MWh发电）",
            "gen_start_yuan_per_unit_start": "抽蓄发电启动成本（元/台次）",
            "pump_start_yuan_per_unit_start": "抽蓄抽水启动成本（元/台次）",
        }
        if not record:
            st.caption(f"{cost_uid} 尚无成本参数；留空表示未知，保存前需完整填写本主体字段。")
        edits_cost = {}
        for field in REQUIRED[cost_type]:
            value = record.get(field)
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and np.isfinite(value) and value >= 0
            cost_key = f"fixed_cost_{name}_{cost_uid}_{field}"
            if cost_key not in st.session_state:
                st.session_state[cost_key] = float(value) if valid else None
            edits_cost[field] = st.number_input(labels_cost[field], min_value=0.0, value=None, key=cost_key,
                                                placeholder="未知（请显式填写）")
        source_cost = st.text_area("运行成本来源 / 假设说明", value=costs.get("source", ""), key=f"cost_source_{name}")
        with st.expander("技术详情：成本档案版本与文件指纹", expanded=False):
            st.caption(f"版本：{costs.get('version', '未建立')}；指纹：{costs.get('sha256') or '无'}")
        if st.button("仅保存该主体运行成本（不改报价）", key=f"save_cost_{name}"):
            candidate = {k: copy.deepcopy(v) for k, v in costs.items() if k not in ("available", "missing_reason", "sha256", "input_file")}
            candidate.setdefault("version", "explicit-operating-costs-v1")
            candidate.setdefault("source_kind", "user_explicit_cost_parameters")
            candidate["source"] = source_cost.strip()
            candidate.setdefault("units", {})[cost_uid] = dict(type=cost_type, **edits_cost)
            try:
                validate_profile(candidate)
                content = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False)
                temporary = cost_path.with_suffix(".yaml.tmp")
                temporary.write_text(content, encoding="utf-8")
                temporary.replace(cost_path)
                sc.update_meta(name)
                saved("运行成本已保存；报价未修改")
                st.rerun()
            except (ValueError, TypeError) as exc:
                business_error("运行成本未保存", exc, "各项须为有限非负数值，并填写来源说明。")

# ============================================================================ 线路
with tabs[5]:
    brp = SP / "data" / "case39" / "branches.csv"
    br = pd.read_csv(brp)
    lsc = base.get("line_scale") or {}
    br["支路类别"] = br["kind"].map({"line": "线路", "transformer": "变压器", "trafo": "变压器"}).fillna("其他支路")
    br["压缩系数"] = br.branch_id.map(lambda b: float(lsc.get(b, 1.0)))
    br["生效限额 MW"] = (br.rate_mw * br["压缩系数"] * float(base.get("line_capacity_multiplier", 1.0))).round(1)
    st.caption(f"生效限额 = 原始限额 × 线路系数 × 全网容量系数（当前 {float(base.get('line_capacity_multiplier', 1.0)):g} 倍）。右侧生效限额为已保存值，保存后更新。")
    bre = st.data_editor(br, disabled=["branch_id", "from_bus", "to_bus", "x_pu", "kind", "支路类别", "生效限额 MW"], hide_index=True,
                         width="stretch", height=500, key="br_ed", column_config={"branch_id": "线路编号", "from_bus": "起点节点", "to_bus": "终点节点", "x_pu": "电抗（标幺）", "rate_mw": "原始限额（MW）", "kind": None, "压缩系数": "线路容量系数（倍）"})
    if st.button("保存线路限额", type="primary"):
        br2 = bre[["branch_id", "from_bus", "to_bus", "x_pu", "rate_mw", "kind"]]
        br2.to_csv(brp, index=False)
        base["line_scale"] = {r.branch_id: py(r.压缩系数) for r in bre.itertuples() if abs(r.压缩系数 - 1) > 1e-9}
        sc.write_yaml(name, "base", base)
        saved()
        st.rerun()

# ============================================================================ 市场参数
modes = sc.read_yaml(name, "modes")
mk = modes[cfg.rule_set]
with tabs[6]:
    st.warning("模型规则适用于当前场景的所有主体，是研究假设，不是单个主体的申报。通常保持不变；修改后需重新校验全部报价并重新运行。")
    st.caption("MKT 为本算例组合规则，并非某一地区的完整市场规则。出处见 config/modes.yaml；假设值不代表规则原文。备用为安全约束，不是独立备用市场或新增结算。")
    pl = mk["price_limits"]
    c = st.columns(4)
    v_bmin = c[0].number_input("申报下限 元/MWh", -1000.0, 5000.0, float(pl["bid_min"]))
    v_bmax = c[1].number_input("申报上限 元/MWh（研究参数）", -1000.0, 5000.0, float(pl["bid_max"]))
    v_cmin = c[2].number_input("出清下限 元/MWh", -1000.0, 5000.0, float(pl["clear_min"]))
    v_cmax = c[3].number_input("出清上限 元/MWh（川V4.0 附件3）", -1000.0, 5000.0, float(pl["clear_max"]))
    rs = mk["reserve"]
    st.markdown("**备用系数**（R⁺ = ρ_L·负荷 + ρ_RE·新能源 + 故障备用；R⁻ = ρ⁻_L·负荷 + ρ⁻_RE·新能源）")
    c = st.columns(5)
    rl = c[0].number_input("ρ_L 正备用-负荷（假设值）", 0.0, 0.5, float(rs["rho_L"]), 0.005, format="%.3f")
    rre = c[1].number_input("ρ_RE 正备用-新能源（假设值）", 0.0, 0.5, float(rs["rho_RE"]), 0.01, format="%.3f")
    rld = c[2].number_input("ρ⁻_L 负备用-负荷（假设值）", 0.0, 0.5, float(rs["rho_L_dn"]), 0.005, format="%.3f")
    rred = c[3].number_input("ρ⁻_RE 负备用-新能源（假设值）", 0.0, 0.5, float(rs["rho_RE_dn"]), 0.01, format="%.3f")
    ctg = c[4].checkbox("计入故障备用（甘V3.2）", bool(rs["include_ctg"]))
    if st.button("保存市场参数", type="primary"):
        if v_bmin > v_bmax or v_cmin > v_cmax:
            st.error("下限不能大于上限")
        else:
            pl["bid_min"], pl["bid_max"], pl["clear_min"], pl["clear_max"] = py(v_bmin), py(v_bmax), py(v_cmin), py(v_cmax)
            rs["rho_L"], rs["rho_RE"], rs["rho_L_dn"], rs["rho_RE_dn"], rs["include_ctg"] = py(rl), py(rre), py(rld), py(rred), bool(ctg)
            sc.write_yaml(name, "modes", modes)
            saved("已保存（若报价超出新限价，请到“主体报价”页校验）")

# ============================================================================ 实时与滚动
with tabs[7]:
    rt = mk["realtime"]
    stg = rt["storage"]
    cur = "A" if stg["mode"] == "fixed_da" else ("B1" if stg["anchor"] == "window_end_pct" else "B2")
    tracking = rt.get("tracking_policy") or {}
    soft_tracking = tracking.get("mode") == "soft_deliverable_energy"
    opts = {"B2": "整点软跟踪日前能量（B2）",
            "B1": "窗口末能量允许偏差（B1）",
            "A": "执行日前充放电计划（A）"}
    choices = ["B2", "B1"] if soft_tracking else list(opts)
    m = st.radio("实时储能模式", choices, index=choices.index(cur), format_func=opts.get)
    st.caption("同时适用于储能与抽蓄。B2：窗口内各整点能量软跟踪日前值；B1：窗口末软跟踪日前值的 ±x 区间；偏离目标均可能产生罚费，不是硬等式。A：实时沿用日前计划。B2 为本算例默认假设，地区条款号未核实。")
    if soft_tracking:
        st.info(f"当前采用可放电等价能量软跟踪：水电窗口末库容、电池与抽蓄日前轨迹偏差按 {tracking.get('penalty_yuan_per_discharge_mwh')} 元/可放电等价MWh计罚。保留物理边界和日前申报硬终端；此政策不支持A固定出力模式。")
    x = st.number_input("窗口末允许偏差比例（B1，0–1）", 0.0, 0.5, float(stg["x"]), 0.01, disabled=m != "B1")
    la = st.selectbox("实时前瞻窗口（小时）", [0.25, 0.5, 1, 1.5, 2, 3, 4],
                        index=[0.25, 0.5, 1, 1.5, 2, 3, 4].index(float(rt["lookahead_h"])) if float(rt["lookahead_h"]) in [0.25, 0.5, 1, 1.5, 2, 3, 4] else 4)
    st.caption(f"实时间隔 {rt['interval_min']} min（川V4.0 9.1、甘V3.2 第六十三条），只执行首时段。")
    idc = mk["intraday"]
    c = st.columns(3)
    ide = c[0].checkbox("历史实验：独立日内滚动（不属当前标准流程）", bool(idc["enabled"]))
    st.caption("当前标准流程为日前→实时→结算，不设独立日内市场。上方仅保留历史实验开关；不会自动修改已有场景。")
    idi = c[1].number_input("日内间隔 h（假设值）", 0.25, 6.0, float(idc["interval_h"]), 0.25, disabled=not ide)
    sv = base["solver"]
    with st.expander("高级：求解精度与时限（一般无需修改）"):
        st.caption("当前默认日前及实时混合整数相对间隙为1%（输入0.01），只影响MIP停止条件，不放宽正式SCED或辅助定价LP的可行性/最优性容差。相对间隙越小可能越慢；达到时限不等于达到目标间隙。结果页需结合实际状态、gap和松弛风险判断。")
        c = st.columns(2)
        rgap = c[0].number_input("实时相对最优间隙（0–1）", 0.0, 0.05, float(rt["mip_rel_gap"]), 1e-4, format="%.4f")
        rtl = c[1].number_input("实时每窗口时限（秒）", 5, 600, int(rt["time_limit_s"]), help="同一窗口SCUC、正式SCED与辅助算价共享的求解器调用耗时预算；不含建模及输出。180秒时为后两阶段预留10秒；预算耗尽不执行该窗口。")
        st.markdown("**日前求解参数**")
        c = st.columns(2)
        dgap = c[0].number_input("日前相对最优间隙（0–1）", 0.0, 0.05, float(sv["mip_rel_gap"]), 0.001, format="%.4f")
        dtl = c[1].number_input("日前求解时限（秒）", 10, 3600, int(sv["time_limit_s"]), help="日前SCUC主MIP限时；正式SCED及辅助算价另计，非整个日前流程墙钟截止时间。")
    if st.button("保存实时与滚动参数", type="primary"):
        stg["mode"] = "fixed_da" if m == "A" else "sced_anchor"
        if m != "A":
            stg["anchor"] = "window_end_pct" if m == "B1" else "hourly_eq"
        stg["x"] = py(x)
        rt["lookahead_h"], rt["mip_rel_gap"], rt["time_limit_s"] = py(la), py(rgap), int(rtl)
        idc["enabled"], idc["interval_h"] = bool(ide), py(idi)
        sc.write_yaml(name, "modes", modes)
        sv["mip_rel_gap"], sv["time_limit_s"] = py(dgap), int(dtl)
        sc.write_yaml(name, "base", base)
        saved()

# ============================================================================ 抽水蓄能
with tabs[8]:
    pss = units.get("pumped") or []
    pm = mk.setdefault("pumped", {})
    if not pss:
        st.info("本场景 units.yaml 没有抽蓄电站（pumped）。")
    else:
        ps = pss[0]
        st.caption("参数均为假设值（300 MW 级定速机组典型值），依据与条款见 docs/开发记录.md“抽水蓄能”。库容单位 万m³，转换系数 MWh/万m³。")
        c = st.columns(4)
        p_en = c[0].checkbox("启用抽蓄 " + ps["id"], bool(ps.get("enabled", True)))
        bus_opts = list(range(1, 40))
        p_bus = c[1].selectbox("接入节点（默认 2：W1 风电送出节点）", bus_opts, index=bus_opts.index(int(ps["bus"])))
        p_n = c[2].number_input("台数", 1, 8, int(ps["n_units"]))
        p_vs = c[3].checkbox("变速机组（抽水功率可调）", bool(ps.get("variable_speed", False)))
        keys = [("unit_gen_max", "单机发电额定 MW"), ("unit_gen_min", "单机发电最小出力 MW"), ("unit_pump", "单机抽水功率 MW"),
                ("unit_pump_min", "变速机抽水下限 MW"), ("e_gen", "发电转换系数 MWh/万m³"), ("e_pump", "抽水转换系数 MWh/万m³"),
                ("v_total", "上下库总水量 万m³"), ("vup_min", "上库下限 万m³"), ("vup_max", "上库上限 万m³"), ("vdn_min", "下库下限 万m³"),
                ("vdn_max", "下库上限 万m³"), ("vup0", "日初上库库容 万m³"), ("max_gen_starts", "每台日发电启动上限"),
                ("max_pump_starts", "每台日抽水启动上限"), ("start_cost_gen", "发电启动费 元/台次"), ("start_cost_pump", "抽水启动费 元/台次")]
        pdf = pd.DataFrame([{"参数": lab, "键": k, "值": float(ps.get(k) or 0)} for k, lab in keys])
        pe = st.data_editor(pdf, disabled=["参数", "键"], hide_index=True, width="stretch", key="ps_ed", column_order=["参数", "值"])
        eff = float(pe.loc[pe["键"] == "e_gen", "值"].iloc[0]) / max(float(pe.loc[pe["键"] == "e_pump", "值"].iloc[0]), 1e-9)
        st.caption(f"综合循环效率（发电转换系数 ÷ 抽水转换系数）≈ {eff:.3f}")
        c = st.columns(2)
        veb = c[0].number_input("缺省报价日末上库库容（万m³）", 0.0, 2000.0,
                                float(ps.get("vup_end_bid") if ps.get("vup_end_bid") is not None else ps["vup0"]))
        c[1].caption("实际生效的是报价中的“申报日末上库库容”（报价页可改或不申报）；此处只用于生成缺省报价。")
        with st.expander("抽蓄适用的模型规则"):
            st.markdown(
                "- 报价：发电对应放电、抽水对应充电；发电最低申报价高于抽水最高申报价。\n"
                "- 日前：申报的日末上库库容为硬约束；参与出清和节点电价形成。\n"
                "- 实时：与储能共用“运行设置”中的模式，抽水与发电互斥。\n"
                "- 安全备用：是否计入由场景模型配置决定；本页不设置备用报价或付款。\n"
                "- 结算：日前、实时两级结算，发电与抽水分列。")
        mstep = st.selectbox("抽/发台数决策步长（抽蓄工程简化，非市场规则）", [1, 2, 4], index=[1, 2, 4].index(int(pm.get("mode_step_periods", 4))),
                             format_func=lambda v: {1: "15 min", 2: "30 min", 4: "1 h（默认）"}[v])
        if st.button("保存抽蓄参数", type="primary"):
            ps["enabled"], ps["bus"], ps["n_units"], ps["variable_speed"] = bool(p_en), int(p_bus), int(p_n), bool(p_vs)
            for _, r in pe.iterrows():
                v = py(r["值"])
                ps[r["键"]] = int(v) if r["键"] in ("max_gen_starts", "max_pump_starts") else v
            ps["vup_end_bid"] = py(veb)
            ps.pop("vup_end", None)
            bad = []
            if ps["vup_min"] >= ps["vup_max"] or not (ps["vup_min"] <= ps["vup0"] <= ps["vup_max"]):
                bad.append("上库下限 ≤ 初始库容 ≤ 上库上限")
            if ps["unit_gen_min"] > ps["unit_gen_max"]:
                bad.append("发电最小出力 ≤ 额定")
            if ps["e_gen"] > ps["e_pump"]:
                bad.append("发电转换系数 ≤ 抽水转换系数（循环效率 ≤ 1）")
            if bad:
                st.error("参数不合理：" + "；".join(bad))
            else:
                sc.write_yaml(name, "units", units)
                for k in ("da_mode", "rt_mode", "pricing", "rt_band_1e4m3", "pricing_eps", "reserve", "anchor_penalty"):
                    pm.pop(k, None)                       # 旧规则键（已弃用）
                pm["rules"], pm["mode_step_periods"] = "storage", int(mstep)
                sc.write_yaml(name, "modes", modes)
                saved("已保存 units.yaml 与 modes.yaml（台数改变时请到“主体报价”页按新容量校验/重建抽蓄报价）")
