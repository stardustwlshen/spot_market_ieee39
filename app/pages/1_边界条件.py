"""边界条件编辑：场景管理、负荷、风光、报价、机组/储能/水电、线路、市场参数、实时与滚动。"""
import copy
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import page_setup, period_labels, sc, scen_config, scenario_selector, style_fig

page_setup("边界条件", "场景 = 一组边界条件；在此修改负荷、风光预测、报价、机组/储能/水电参数、线路限额与市场参数，保存后到“运行”页出清")
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


tabs = st.tabs(["场景管理", "负荷曲线", "风/光预测", "报价", "机组/储能/水电", "线路限额", "市场限价与备用", "实时与滚动", "抽水蓄能"])

# ============================================================================ 场景管理
with tabs[0]:
    meta = sc.scenario_meta(name)
    st.markdown(f"**当前场景：{name}**　来源：{meta.get('source', '')}　创建：{meta.get('created', '')}　修改：{meta.get('modified', '—')}")
    st.caption(f"目录：{SP}（config/*.yaml、data/case39/*.csv、data/profile_overrides.csv、bids/…/bids_v*.json）")
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
                st.error(str(e))
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
base = sc.read_yaml(name, "base")
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
    for k, v in cols.items():
        if v is None:
            df = df.drop(columns=[k], errors="ignore")
        else:
            df[k] = np.round(np.asarray(v, float), 3)
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
    ls = c1.number_input("负荷总量缩放 load_scale（假设值）", 0.1, 1.5, float(base["load_scale"]), 0.01,
                         help=f"系统峰荷 ≈ load_scale × 6254 MW；当前 ≈ {float(base['load_scale']) * 6254.23:,.0f} MW")
    season = c2.selectbox("季节（决定水电来水）", ["wet", "dry"], index=["wet", "dry"].index(base["season"]),
                          format_func=lambda s: {"wet": "丰水期 wet", "dry": "枯水期 dry"}[s])
    seed = c3.number_input("随机种子（预测误差）", value=int(base["seed"]), step=1)
    if st.button("保存缩放/季节/种子"):
        base["load_scale"], base["season"], base["seed"] = py(ls), season, int(seed)
        sc.write_yaml(name, "base", base)
        saved()
        st.rerun()
    has_load_ov = any(c in overrides for c in ("load_actual", "load_da"))
    if has_load_ov:
        st.warning("本场景已有负荷 96 点覆盖（data/profile_overrides.csv），覆盖值优先于 load_scale 生成值。")
    st.plotly_chart(curve_fig(prof, ["load_actual", "load_da", "load_rt"], ["实际", "日前预测", "实时（超短期）预测"],
                              "系统负荷（当前生效）"), width="stretch")
    st.markdown("**96 点编辑**（修改“实际”时，日前/实时预测保持原相对误差；也可单独改“日前预测”）")
    k = st.number_input("整体乘以系数（作用于下表后保存）", 0.5, 1.5, 1.0, 0.01)
    ed = st.data_editor(pd.DataFrame({"时刻": labels, "实际 MW": prof.load_actual.round(1), "日前预测 MW": prof.load_da.round(1)}),
                        disabled=["时刻"], hide_index=True, height=300, width="stretch", key="load_ed")
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
    up = b3.file_uploader("上传 CSV（96 行；列 load_actual，可选 load_da）", type="csv", key="load_up")
    if up is not None and st.button("应用上传的负荷 CSV"):
        df = pd.read_csv(up)
        if len(df) != T or "load_actual" not in df:
            st.error(f"需要 {T} 行且含 load_actual 列，实际 {len(df)} 行、列 {list(df.columns)}")
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
    st.markdown("**预测误差参数**（假设值）：σ 为标准差（风光按装机占比、负荷按当时负荷占比），φ 为 AR(1) 自相关系数。日内/实时误差与日前误差相关（同一随机序列按 σ 比例缩放 + 30% 独立分量）。")
    pe = st.data_editor(pd.DataFrame([{"对象": k, "σ日前": sig[k]["da"], "σ日内": sig[k]["id"], "σ实时": sig[k]["rt"], "φ": phi[k]}
                                      for k in ("wind", "pv", "load")]), disabled=["对象"], hide_index=True, key="sig_ed")
    c1, c2 = st.columns(2)
    if c1.button("保存误差参数", type="primary"):
        base["forecast"] = {"sigma": {r["对象"]: {"da": py(r["σ日前"]), "id": py(r["σ日内"]), "rt": py(r["σ实时"])} for _, r in pe.iterrows()},
                            "phi": {r["对象"]: py(r["φ"]) for _, r in pe.iterrows()}}
        sc.write_yaml(name, "base", base)
        saved()
        st.rerun()
    if c2.button("恢复默认误差参数"):
        base.pop("forecast", None)
        sc.write_yaml(name, "base", base)
        saved()
        st.rerun()
    re_ids = [u["id"] for u in cfg.units["renewable"]]
    rid = st.selectbox("新能源场站", re_ids, format_func=lambda i: f"{i}（{next(u for u in cfg.units['renewable'] if u['id'] == i)['type']}，装机 {next(u for u in cfg.units['renewable'] if u['id'] == i)['cap']} MW）")
    st.plotly_chart(curve_fig(prof, [f"{rid}_actual", f"{rid}_da", f"{rid}_rt"], ["实际", "日前预测", "实时预测"], f"{rid} 出力"),
                    width="stretch")
    acc = []
    for c in ["load"] + re_ids:
        for stg in ("da", "rt"):
            e = prof[f"{c}_{stg}"] - prof[f"{c}_actual"]
            acc.append({"对象": c, "阶段": {"da": "日前", "rt": "实时"}[stg], "MAE MW": e.abs().mean(), "RMSE MW": np.sqrt((e ** 2).mean()),
                        "日电量偏差 MWh": e.sum() * cfg.dt})
    with st.expander("预测精度（当前生效曲线）"):
        st.dataframe(pd.DataFrame(acc).round(1), hide_index=True)
    st.markdown(f"**{rid} 96 点编辑**（实际与日前预测；保存后写入场景覆盖文件）")
    ed2 = st.data_editor(pd.DataFrame({"时刻": labels, "实际 MW": prof[f"{rid}_actual"].round(1), "日前预测 MW": prof[f"{rid}_da"].round(1)}),
                         disabled=["时刻"], hide_index=True, height=300, width="stretch", key=f"re_ed_{rid}")
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
    up2 = b3.file_uploader("上传 CSV（96 行；列名如 W1_actual、W1_da、PV1_actual、load_actual）", type="csv", key="re_up")
    if up2 is not None and st.button("应用上传的 CSV"):
        df = pd.read_csv(up2)
        ok = [c for c in df.columns if c.rsplit("_", 1)[0] in ["load"] + re_ids and c.rsplit("_", 1)[-1] in ("actual", "da")]
        if len(df) != T or not ok:
            st.error(f"需要 {T} 行，且列名为 <对象>_actual / <对象>_da")
        else:
            write_overrides({c: df[c] for c in ok})
            saved(f"已应用 {ok}")
            st.rerun()
    if ov_path.exists():
        st.caption(f"当前覆盖列：{[c for c in overrides.columns if c != 'period']}")

# ============================================================================ 报价
with tabs[3]:
    from smm.bids import bid_dir, bids_to_csv, load_bids, save_bids, validate_bids
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
        v = c1.selectbox("编辑基于版本", vers[::-1])
        key = f"draft_{name}_{v}"
        if key not in st.session_state or c1.button("放弃修改，重新载入"):
            st.session_state[key] = load_bids(cfg, v)
        draft = st.session_state[key]
        uids = list(draft["units"])
        tmap = {u["id"]: u["type"] for g in ("thermal", "renewable") for u in cfg.units[g]}
        tmap.update({u["id"]: "hydro" for u in cfg.units["hydro"]})
        tmap.update({u["id"]: "storage" for u in cfg.units["storage"]})
        tmap.update({u["id"]: "pumped" for u in cfg.units.get("pumped", [])})
        uid = c2.selectbox("主体（机组/场站/储能）", uids, format_func=lambda u: f"{u}（{tmap.get(u, '?')}）")
        ub = draft["units"][uid]
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
            sue = c1.data_editor(pd.DataFrame([su]), hide_index=True, key=f"su_{key}_{uid}")
            ub["startup_cost"] = {k: float(v) for k, v in sue.iloc[0].items()}
            ub["noload_cost"] = float(c2.number_input("空载费 元/h", 0.0, 1e6, float(ub.get("noload_cost", 0.0)), key=f"nl_{key}_{uid}"))
        if tmap.get(uid) in ("wind", "pv"):
            ap = ub.get("avail_pmax")
            st.caption(f"avail_pmax：{ap if ap is not None else '未申报（按预测出力）'}")
        st.divider()
        issues = validate_bids(cfg, draft)
        errs = [i for i in issues if i.level == "error"]
        if issues:
            st.markdown(f"**校验结果（MKT 规则）：{len(errs)} 项违规（error）、{len(issues) - len(errs)} 项提示（warn）**")
            st.dataframe(pd.DataFrame([{"主体": i.unit_id, "级别": i.level, "规则": i.rule, "说明": i.msg} for i in issues]),
                         hide_index=True, width="stretch")
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
        st.download_button("下载该版本 JSON", json.dumps(load_bids(cfg, v), ensure_ascii=False, indent=2), f"bids_v{v}.json")

# ============================================================================ 机组/储能/水电
units = sc.read_yaml(name, "units")
with tabs[4]:
    st.caption("技术与成本参数均为假设值。修改后保存到场景 config/units.yaml（保留注释）。")
    st.markdown("**火电/燃气机组**（ramp MW/min，ut/dt 小时，启动费 元/次，空载费 元/h）")
    th = pd.DataFrame([{"id": u["id"], "类型": u["type"], "节点": u["bus"], "pmax": u["pmax"], "pmin": u["pmin"], "ramp": u["ramp"],
                        "ut": u["ut"], "dt": u["dt"], "noload": u["noload"], "启动费_冷": u["startup"]["cold"], "启动费_温": u["startup"]["warm"],
                        "启动费_热": u["startup"]["hot"], "初始并网": bool(u["init"]["online"]), "初始出力": u["init"]["p0"],
                        "已持续h": u["init"]["hours"]} for u in units["thermal"]])
    the = st.data_editor(th, disabled=["id", "类型", "节点"], hide_index=True, width="stretch", key="th_ed")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**新能源装机 / 区外来电**")
        re_t = pd.DataFrame([{"id": u["id"], "类型": u["type"], "节点": u["bus"], "装机 MW": u["cap"]} for u in units["renewable"]] +
                            [{"id": u["id"], "类型": "tie", "节点": u["bus"], "装机 MW": u["pmax"]} for u in units["tie"]])
        ree = st.data_editor(re_t, disabled=["id", "类型", "节点"], hide_index=True, key="re_ed")
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
        st.markdown(f"**水电 {hy['id']}**（{base['season']} 季参数；来水 qin m³/s，库容 万m³）")
        hs = hy["seasons"]
        hs = hy["seasons"]
        hye = st.data_editor(pd.DataFrame([{"季节": s, "来水qin": hs[s]["qin"], "初库容v0": hs[s]["v0"], "vmin": hs[s]["vmin"], "vmax": hs[s]["vmax"],
                                           "期末库容v_end": hs[s]["v_end"], "生态流量qeco": hs[s]["qeco"], "日电量e_day": hs[s]["e_day"]} for s in hs]),
                             disabled=["季节"], hide_index=True, key=f"hy_ed_{hid}")
        hn = st.number_input("台数", 1, 8, int(hy["n_units"]), key=f"hy_n_{hid}")
        hp = st.number_input("单台容量 MW", 10.0, 500.0, float(hy["unit_pmax"]), key=f"hy_p_{hid}")
    st.markdown("**储能**（soc0 = 初始 SOC；“期末申报SOC(最新报价)”为日前硬约束，修改后会另存为新报价版本；soc_end_bid 仅用于生成缺省报价）")
    try:
        from smm.bids import load_bids as _lb
        lb = _lb(cfg)
    except FileNotFoundError:
        lb = None
    ss = pd.DataFrame([{"id": u["id"], "节点": u["bus"], "pmax": u["pmax"], "ecap MWh": u["ecap"], "eta_c": u["eta_c"], "eta_d": u["eta_d"],
                        "soc_min": u["soc_min"], "soc_max": u["soc_max"], "初始SOC soc0": u["soc0"], "soc_end_bid": u["soc_end_bid"],
                        "期末申报SOC(最新报价)": (lb["units"].get(u["id"], {}).get("soc_end") if lb else None)} for u in units["storage"]])
    sse = st.data_editor(ss, disabled=["id", "节点"], hide_index=True, width="stretch", key="ss_ed")
    if st.button("保存机组/储能/水电参数", type="primary"):
        for u, (_, r) in zip(units["thermal"], the.iterrows()):
            for k in ("pmax", "pmin", "ramp", "ut", "dt", "noload"):
                u[k] = py(r[k])
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
            s = hs[r["季节"]]
            for a, b in (("qin", "来水qin"), ("v0", "初库容v0"), ("vmin", "vmin"), ("vmax", "vmax"), ("v_end", "期末库容v_end"),
                         ("qeco", "生态流量qeco"), ("e_day", "日电量e_day")):
                s[a] = py(r[b])
        hy["n_units"], hy["unit_pmax"] = int(hn), py(hp)
        hy["enabled"], hy["regulation"] = bool(hen), hreg
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
                    st.error("期末 SOC 违规，未生成新报价：" + "；".join(map(str, iss)))
                else:
                    p = save_bids(cfg2, nb, note=f"网页修改储能期末申报 SOC（基于 v{lb['version']}）")
                    bids_to_csv(json.loads(p.read_text(encoding="utf-8")), p.with_suffix(".csv"))
                    msg += f"，期末 SOC 另存为报价 {p.stem}"
        saved(msg)

# ============================================================================ 线路
with tabs[5]:
    brp = SP / "data" / "case39" / "branches.csv"
    br = pd.read_csv(brp)
    lsc = base.get("line_scale") or {}
    br["压缩系数"] = br.branch_id.map(lambda b: float(lsc.get(b, 1.0)))
    br["生效限额 MW"] = (br.rate_mw * br["压缩系数"]).round(1)
    st.caption("生效限额 = 原始限额 rate_mw × 压缩系数（base.yaml line_scale，方案第 4 节压缩 16–19、2–25 等一带）。")
    bre = st.data_editor(br, disabled=["branch_id", "from_bus", "to_bus", "x_pu", "kind", "生效限额 MW"], hide_index=True,
                         width="stretch", height=500, key="br_ed")
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
    st.caption("规则出处见 config/modes.yaml 行内注释；标“假设值”的不是规则原文数值。")
    pl = mk["price_limits"]
    c = st.columns(4)
    v_bmin = c[0].number_input("申报下限 元/MWh", -1000.0, 5000.0, float(pl["bid_min"]))
    v_bmax = c[1].number_input("申报上限 元/MWh（川V4.0 附件3）", -1000.0, 5000.0, float(pl["bid_max"]))
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
            saved("已保存（若报价超出新限价，请到“报价”页校验）")

# ============================================================================ 实时与滚动
with tabs[7]:
    rt = mk["realtime"]
    stg = rt["storage"]
    cur = "A" if stg["mode"] == "fixed_da" else ("B1" if stg["anchor"] == "window_end_pct" else "B2")
    opts = {"B2": "B2（默认）：窗口内各整点 E_RT = E_DA（广东式，条款号未核实）",
            "B1": "B1：窗口末 (1−x)E_DA ≤ E_RT ≤ (1+x)E_DA（甘V3.2 运营细则附录5 第9项）",
            "A": "A：执行日前充放电曲线（价格接受者）"}
    m = st.radio("实时储能模式", list(opts), index=list(opts).index(cur), format_func=opts.get)
    x = st.number_input("B1 偏差带 x（假设值，甘肃细则未给出）", 0.0, 0.5, float(stg["x"]), 0.01, disabled=m != "B1")
    c = st.columns(3)
    la = c[0].selectbox("实时前瞻窗口 h（甘V3.2 第六十三条：2 h）", [0.25, 0.5, 1, 1.5, 2, 3, 4],
                        index=[0.25, 0.5, 1, 1.5, 2, 3, 4].index(float(rt["lookahead_h"])) if float(rt["lookahead_h"]) in [0.25, 0.5, 1, 1.5, 2, 3, 4] else 4)
    rgap = c[1].number_input("实时 MIP gap", 0.0, 0.05, float(rt["mip_rel_gap"]), 1e-4, format="%.4f")
    rtl = c[2].number_input("实时每窗口求解时限 s", 5, 600, int(rt["time_limit_s"]))
    st.caption(f"实时间隔 {rt['interval_min']} min（川V4.0 9.1、甘V3.2 第六十三条），只执行首时段。")
    idc = mk["intraday"]
    c = st.columns(3)
    ide = c[0].checkbox("打开独立日内滚动（MKT 默认关闭，仅作对比）", bool(idc["enabled"]))
    idi = c[1].number_input("日内间隔 h（假设值）", 0.25, 6.0, float(idc["interval_h"]), 0.25, disabled=not ide)
    sv = base["solver"]
    st.markdown("**日前求解参数**")
    c = st.columns(2)
    dgap = c[0].number_input("日前 SCUC MIP gap", 0.0, 0.05, float(sv["mip_rel_gap"]), 0.001, format="%.4f")
    dtl = c[1].number_input("日前 SCUC 时限 s", 10, 3600, int(sv["time_limit_s"]))
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
        st.caption("参数均为假设值（300 MW 级定速机组典型值），依据与条款见 README“抽水蓄能”。库容单位 万m³，转换系数 MWh/万m³。")
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
                ("max_pump_starts", "每台日抽水启动上限"), ("start_cost_gen", "发电启动费 元/台次"), ("start_cost_pump", "抽水启动费 元/台次"),
                ("capacity_price", "容量电价 元/kW·年（仅展示）")]
        pdf = pd.DataFrame([{"参数": lab, "键": k, "值": float(ps.get(k) or 0)} for k, lab in keys])
        pe = st.data_editor(pdf, disabled=["参数", "键"], hide_index=True, width="stretch", key="ps_ed")
        eff = float(pe.loc[pe["键"] == "e_gen", "值"].iloc[0]) / max(float(pe.loc[pe["键"] == "e_pump", "值"].iloc[0]), 1e-9)
        st.caption(f"综合循环效率 = e_gen / e_pump ≈ {eff:.3f}")
        c = st.columns(2)
        veb = c[0].number_input("缺省报价申报的日末上库库容 万m³（vup_end_bid，≙ 储能 soc_end_bid）", 0.0, 2000.0,
                                float(ps.get("vup_end_bid") if ps.get("vup_end_bid") is not None else ps["vup0"]))
        c[1].caption("实际生效的是报价中的“申报日末上库库容”（报价页可改或不申报）；此处只用于生成缺省报价。")
        st.markdown("**市场规则：同电化学储能**（MKT.pumped.rules = storage，不另设一套）")
        stc = (mk.get("realtime") or {}).get("storage") or {}
        st.markdown(
            f"- 报价：报量报价，发电曲线 ≙ 放电、抽水报价 ≙ 充电；发电最低价 > 抽水最高价（storage_dis_gt_ch = {mk.get('storage_dis_gt_ch', 'error')}）\n"
            f"- 定价：正常参与出清与节点电价形成（已删除旧的“不可定价 fixed / 可定价 band / free”处理）\n"
            f"- 日前日末库容：申报即硬约束（≙ 申报期末 SOC）\n"
            f"- 实时：{stc.get('mode', 'sced_anchor')} / 锚点 {stc.get('anchor', 'hourly_eq')}（x = {stc.get('x', 0.05)}），抽/发 0-1 互斥在窗口 MIP 内决策 —— 改动请到“实时与滚动”页的储能设置，二者同时生效\n"
            f"- 备用：reserve.storage_reserve = {(mk.get('reserve') or {}).get('storage_reserve', False)}（“市场限价与备用”页）\n"
            f"- 结算：两级结算，发电/抽水分列主体\n"
            f"- 已弃用并删除：实时库容带宽 ±20 万m³（band）、不可定价/可定价处理、安全调控 dispatch（改为报价 mode=quantity_only，同储能）")
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
                bad.append("上库库容 vup_min ≤ vup0 ≤ vup_max")
            if ps["unit_gen_min"] > ps["unit_gen_max"]:
                bad.append("发电最小出力 ≤ 额定")
            if ps["e_gen"] > ps["e_pump"]:
                bad.append("e_gen ≤ e_pump（循环效率 ≤ 1）")
            if bad:
                st.error("参数不合理：" + "；".join(bad))
            else:
                sc.write_yaml(name, "units", units)
                for k in ("da_mode", "rt_mode", "pricing", "rt_band_1e4m3", "pricing_eps", "reserve", "anchor_penalty"):
                    pm.pop(k, None)                       # 旧规则键（已弃用）
                pm["rules"], pm["mode_step_periods"] = "storage", int(mstep)
                sc.write_yaml(name, "modes", modes)
                saved("已保存 units.yaml 与 modes.yaml（台数改变时请到“报价”页按新容量校验/重建抽蓄报价）")
