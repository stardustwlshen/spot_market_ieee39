"""运行：选择场景，后台运行“仅日前”或“全流程”，显示进度与日志。"""
from pathlib import Path
import json
import copy

import pandas as pd
import streamlit as st

from common import (MODE_CN, STATUS_CN, page_setup, run_label, run_progress, run_status, sc, scen_config, scenario_selector,
                    start_run, stop_run)

page_setup("运行", "选择场景与模式，后台运行；可随时切换页面，进度与日志每 2 秒刷新")
name = scenario_selector()
cfg = scen_config(name)
st.caption(f"待启动场景 {name} 的参数（不代表下方所查看的历史运行）：线路罚 {cfg.mode['penalties']['line']} 元/MWh；全网容量倍率 {cfg.base.get('line_capacity_multiplier', 1.0)}（另乘逐线倍率）。既有场景不会随仓库参数自动更新。")
from smm.config import load_config
current_defaults=load_config()
if (cfg.mode['penalties']['line'] != current_defaults.mode['penalties']['line'] or
        cfg.base.get('line_capacity_multiplier',1.) != current_defaults.base.get('line_capacity_multiplier',1.) or
        cfg.mode['realtime'].get('hydro') != current_defaults.mode['realtime'].get('hydro')):
    st.warning('这是保留的旧参数场景。开始运行会采用其自身参数，不是当前仓库研究基准；如需当前设置，请新建下方副本。不会静默覆盖已有场景。')
new_name=st.text_input("从当前仓库参数新建场景名称", value="当前研究场景")
if st.button("新建当前参数副本（不覆盖旧场景）"):
    try:
        sc.create_scenario(new_name)
        st.session_state["scenario"]=new_name
        st.rerun()
    except (ValueError, FileExistsError) as exc:
        st.error(str(exc))
from smm.bids import bid_dir, load_bids, validate_bids, raise_on_errors, default_bids  # noqa: E402

vers = sorted(int(p.stem.split("_v")[-1]) for p in bid_dir(cfg).glob("bids_v*.json"))
rt = cfg.mode["realtime"]
hydro_label={"window_end_da":"研究硬窗口末库容目标：固定DA台数δ、功率可调", "fixed_da":"历史对照：台数与功率固定DA", "sced":"旧sced策略（请核对边界）"}.get(rt.get("hydro"),str(rt.get("hydro")))
st.caption(f"水电策略：{hydro_label}。新增爬坡为电站聚合端点包络，非逐台动态证明。")
smode = "A 执行日前曲线" if rt["storage"]["mode"] == "fixed_da" else (
    "B1 窗口末 ±x" if rt["storage"]["anchor"] == "window_end_pct" else "B2 整点带罚软锚点（默认）")
st.info(f"场景 **{name}**：{cfg.base['date']}，{ {'wet': '丰水期', 'dry': '枯水期'}[cfg.base['season']]}，load_scale {cfg.base['load_scale']}，"
            f"实时储能 {smode}，前瞻 {rt['lookahead_h']} h，独立日内 {'开' if cfg.mode['intraday']['enabled'] else '关'}", icon="🧩")

c1, c2, c3 = st.columns(3)
mode = c1.radio("运行模式", ["da", "full"], index=1, format_func=lambda m: {"da": "仅日前", "full": "全流程：日前→实时滚动→结算"}[m])
v = c2.selectbox("报价版本", ["最新"] + vers[::-1], help="“最新”= 场景中版本号最大的报价")
periods = c3.number_input("实时只滚动前 N 时段（0 = 全天 96，调试用）", 0, 96, 0, disabled=mode != "full")

running = [m for m in sc.list_runs() if run_status(m) in ("running", "starting", "cancelling")]
active = bool(running)
if active:
    st.info("已有后台运行。输入已冻结；报价修改与新运行暂禁用。取消在当前求解完成后的窗口边界生效。")

# Draft is scenario/version-specific; editing one actual unit never overwrites other units.
source_version = None if v == "最新" else int(v)
draft_key = f"bid_draft:{name}:{v}"
if draft_key not in st.session_state:
    st.session_state[draft_key] = load_bids(cfg, source_version) if vers else default_bids(cfg)
    st.session_state[draft_key + ":manual"] = []
draft = st.session_state[draft_key]
st.subheader("选择主体报价（运行前）")
st.caption("当前模型按机组/电站ID出清，不虚构多机电厂组合。修改仅保存在本次运行草稿，原v2文件不变；提交运行后使用冻结副本。")
unit_types={u["id"]:u.get("type",group) for group in ("thermal","renewable","hydro","storage","pumped") for u in cfg.units.get(group,[])}
unit = st.selectbox("报价机组/电站", list(draft["units"]), format_func=lambda u:f"{u} · {unit_types.get(u,'主体')}", disabled=active)
entry_key = f"entry:{draft_key}:{unit}:{st.session_state.get(draft_key+':revision',0)}"
entry = st.text_area("所选主体报价JSON（保留技术参数与曲线结构，保存时完整校验）",
                     json.dumps(draft["units"][unit], ensure_ascii=False, indent=2), height=230,
                     key=entry_key, disabled=active)
if st.button("校验并保存所选主体草稿", disabled=active):
    try:
        candidate=copy.deepcopy(draft);candidate["units"][unit]=json.loads(entry)
        raise_on_errors(validate_bids(cfg,candidate,require_complete=True))
        st.session_state[draft_key]=candidate
        st.session_state[draft_key+":manual"]=sorted(set(st.session_state[draft_key+":manual"]+[unit]))
        draft=candidate;st.success(f"{unit} 报价已验证，将用于下一次运行。")
    except (ValueError,TypeError,KeyError) as exc:
        st.error(f"报价未保存：{exc}")

with st.expander("按类型生成可复现模拟报价（显式替换所选非手工主体）"):
    from smm.bid_simulation import simulate_bids
    seed=st.number_input("报价情景seed", min_value=0, max_value=2147483647, value=int(cfg.base['seed']), disabled=active)
    chosen=st.multiselect("需要模拟的主体ID", list(draft['units']), disabled=active)
    st.caption("不同电源采用不同变化规则；仅改变报价，不改变技术容量。整场运行只生成一次，RT窗口不随机改价；已手工保存主体受保护。")
    if st.button("生成、验证并采用模拟报价", disabled=active):
        try:
            candidate=simulate_bids(cfg,selected_unit_ids=chosen,seed=int(seed),base_bids=draft,
                                    protected_unit_ids=st.session_state[draft_key+':manual'])
            st.session_state[draft_key]=candidate
            st.session_state[draft_key+':revision']=st.session_state.get(draft_key+':revision',0)+1
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if draft.get('_simulation'):
        st.json(draft['_simulation'])

historical_regulation=bool(cfg.mode.get('regulation',{}).get('enabled',False))
if historical_regulation:
    st.error('历史调频实验场景：当前版本只做电能量现货，不纳入调频。正常启动已禁用；请选择新纯电能量场景，不会暗改旧场景。')
else:
    st.caption('当前范围仅电能量现货：标准SCUC→固定整数、相同目标约束的SCED；不含调频或容量市场。')

if st.button("▶ 开始运行", type="primary", width="stretch", disabled=active or historical_regulation):
    try:
        d = start_run(name, mode, int(draft.get("version",1)), int(periods) or None, bids=draft)
        st.session_state["watch_run"] = str(d)
        st.success(f"已启动冻结报价：{d.relative_to(sc.RUNS_DIR.parent)}")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


@st.fragment(run_every=2)
def monitor():
    runs = sc.list_runs()
    if not runs:
        st.info("暂无运行记录。选择模式后点击“开始运行”。", icon="📭")
        return
    for m in runs:
        m["status"] = run_status(m)
    labels = {m["dir"]: run_label(m) for m in runs}
    keys = list(labels)
    w = st.session_state.get("watch_run")
    idx = keys.index(w) if w in keys else 0
    sel = st.selectbox("查看运行", keys, index=idx, format_func=labels.get, key="run_sel")
    st.session_state["watch_run"] = sel
    m = next(x for x in runs if x["dir"] == sel)
    if m.get("demo_description"):st.info(m["demo_description"])
    snap=Path(sel)/"scenario_snapshot"
    if (snap/"config/base.yaml").exists():
        from smm.config import load_config
        frozen=load_config(root=snap)
        st.info(f"所查看运行的冻结参数：{m['scenario']}；线路罚 {frozen.mode['penalties']['line']} 元/MWh；全网倍率 {frozen.base.get('line_capacity_multiplier',1.0)}；范围 {'历史调频实验（不属当前范围）' if frozen.mode.get('regulation',{}).get('enabled',False) else '纯电能量现货'}。上方场景编辑器不改变该运行。")
    logp = Path(sel) / "run.log"
    log = logp.read_text(encoding="utf-8", errors="replace") if logp.exists() else ""
    s = m["status"]
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.progress(run_progress(m, log), text=f"{STATUS_CN.get(s, s)} · 阶段：{m.get('stage', '')}")
    c2.metric("用时（s）", m.get("elapsed_s", "…"))
    if s in ("running", "starting") and c3.button("请求取消（窗口边界）"):
        stop_run(m)
        st.rerun()
    if s == "failed":
        st.error(m.get("error", "运行失败，见日志"))
    if s == "done":
        st.success("所选运行范围完成，可到“结果”页查看。" + (" 注意：这是调试截断范围，不是完整日。" if m.get("periods") and m["periods"] < 96 else ""))
    if (Path(sel)/"cancel.request").exists() and s in ("running", "starting", "cancelling"):
        st.warning("取消已请求；当前SCUC/SCED不强杀，完成后停止。已执行前缀不是完整日结算。")
    from smm.live_runner import read_live
    live = read_live(sel)
    if live.get('da_stages'):
        from market_views import stages_dashboard
        with st.expander('日前SCUC/SCED阶段诊断（不是实时执行）'):
            stages_dashboard(live['da_stages'],prefix='da_stage:'+sel)
    if live.get("executed"):
        st.subheader(f"实时已执行：{live['executed_periods']} / {live['total_periods']} 时段")
        st.caption("每个15分钟窗口结束立即刷新。下图/表只含已执行首时段；前瞻计划单独展示且会被下一窗口修订。")
        executed=live["executed"]
        from market_views import system_metrics
        system_metrics(executed)
        prices=pd.DataFrame(executed["prices"])
        st.line_chart(prices.groupby("period")[["lmp", "lmp_raw"]].mean(), y_label="节点算术均价 元/MWh")
        units=pd.DataFrame(executed["units"])
        st.area_chart(units.groupby(["period","type"]).P_mw.sum().unstack(fill_value=0), y_label="已执行分类型净功率 MW")
        st.dataframe(pd.DataFrame(executed["system"]), hide_index=True, width="stretch")
        with st.expander("当前前瞻窗口（非最终执行结果）"):
            st.dataframe(pd.DataFrame(live.get("lookahead",[])), hide_index=True, width="stretch")
        from market_views import unit_dashboard, stages_dashboard, frozen_bids, frozen_units
        with st.expander("全部主体详细报价 / 运行 / 能量收益",expanded=False):
            unit_dashboard(executed,bids=frozen_bids(sel),prefix='live:'+sel,units_config=frozen_units(sel))
        with st.expander("选择滚动窗口查看SCUC/SCED阶段对照",expanded=False):
            history=live.get('stage_history',{})
            if history:
                window=st.selectbox('窗口起点',list(history),format_func=lambda x:f'第{int(x)}段',key='stage_window:'+sel)
                stages_dashboard(history[window],prefix='live_stage:'+sel)
            else:stages_dashboard(None,prefix='live_stage:'+sel)
        with st.expander('水电实际状态与硬窗口末目标',expanded=False):
            from market_views import hydro_window_dashboard
            hydro_window_dashboard(live.get('hydro_window_history',{}),prefix='live_hydro:'+sel)
        if not live.get("settled"):
            st.caption("滚动前缀仅用于观察，不包含完整日最终结算；失败/取消不会冒充完成结果。")
    elif live.get("phase"):
        st.info(f"当前阶段：{live['phase']}；日前尚未完成时不会伪造实时曲线。")
    with st.expander("运行日志（最后 40 行）", expanded=s != "done"):
        st.code("\n".join(log.splitlines()[-40:]) or "（等待输出…）", language="text")
    st.caption(f"结果目录：{sel}")
    st.markdown("**全部运行**")
    st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"]), "报价": str(x.get("bid_version") or "最新"),
                                "状态": STATUS_CN.get(x["status"], x["status"]), "开始": x.get("started"), "用时 s": x.get("elapsed_s"),
                                "目录": Path(x["dir"]).name} for x in runs]), hide_index=True, width="stretch")


monitor()
