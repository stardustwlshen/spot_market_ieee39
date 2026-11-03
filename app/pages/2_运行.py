"""运行：选择场景，后台运行“仅日前”或“全流程”，显示进度与日志。"""
from pathlib import Path
import json
import copy

import pandas as pd
import streamlit as st

from input_workflow import CURVE_CN, workflow_steps, business_error, cost_bid_comparison
from common import (TYPE_CN, MODE_CN, STATUS_CN, page_setup, run_label, run_progress, run_status, sc, scen_config, scenario_selector,
                    start_run, stop_run)

page_setup("运行", "选择场景与模式，后台运行；可随时切换页面，进度与日志每 2 秒刷新")
workflow_steps(4)
name = scenario_selector()
cfg = scen_config(name)
st.caption(f"待启动场景 {name} 的参数（不代表下方所查看的历史运行）：线路罚 {cfg.mode['penalties']['line']} 元/MWh；全网容量倍率 {cfg.base.get('line_capacity_multiplier', 1.0)}（另乘逐线倍率）。既有场景不会随仓库参数自动更新。")
reserve_cfg=cfg.mode.get('reserve',{})
st.caption(f"待启动场景新能源装机 {sum(float(x['cap']) for x in cfg.units.get('renewable',[]) if x.get('enabled',True)):g} MW；安全备用方向 {'上、下备用' if reserve_cfg.get('down_enabled',True) else '仅上备用'}；储能/抽蓄备用 {'开启' if reserve_cfg.get('storage_reserve',False) else '关闭'}；增量备用筛选时长 {reserve_cfg.get('T_dur_h', '未记录')} h。当前模型以当前时段初 SOC/库容（首段为实际初态）及功率余量筛选增量备用，不新增备用资格整数，不代表基准出力＋备用在全持续时长内可交付。安全备用不新增交易、报价或付款。")
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
        business_error("操作未完成，未启动新的有效运行", exc, "请核对场景名称、输入及运行状态。")
from smm.bids import bid_dir, load_bids, validate_bids, raise_on_errors, default_bids  # noqa: E402

vers = sorted(int(p.stem.split("_v")[-1]) for p in bid_dir(cfg).glob("bids_v*.json"))
rt = cfg.mode["realtime"]
hydro_label={"window_end_da":"研究硬窗口末库容目标：固定日前在线台数、功率可调", "fixed_da":"历史对照：台数与功率固定日前值", "sced":"历史经济调度策略（请核对边界）"}.get(rt.get("hydro"),str(rt.get("hydro")))
tracking = rt.get("tracking_policy") or {}
if tracking.get("mode") == "soft_deliverable_energy":
    hydro_label = f"窗口末库容软跟踪日前值，统一 {tracking.get('penalty_yuan_per_discharge_mwh')} 元/可放电等价MWh"
st.caption(f"水电策略：{hydro_label}。新增爬坡为电站聚合端点包络，非逐台动态证明。")
smode = "A 执行日前曲线" if rt["storage"]["mode"] == "fixed_da" else (
    "B1 窗口末 ±x" if rt["storage"]["anchor"] == "window_end_pct" else "B2 整点带罚软锚点（默认）")
st.info(f"场景 **{name}**：{cfg.base['date']}，{ {'wet': '丰水期', 'dry': '枯水期'}[cfg.base['season']]}，负荷缩放 {cfg.base['load_scale']} 倍，"
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
source_version = (vers[-1] if vers else None) if v == "最新" else int(v)
draft_key = f"bid_draft:{name}:{source_version if source_version is not None else 'default'}"
# Preserve pre-upgrade “latest” drafts under their actual version, without
# overwriting any newer version-specific edits or changing disk input.
legacy_key = f"bid_draft:{name}:最新"
if legacy_key in st.session_state:
    legacy = st.session_state[legacy_key]
    retained_key = f"bid_draft:{name}:{legacy.get('version', 1)}"
    if retained_key not in st.session_state:
        st.session_state[retained_key] = copy.deepcopy(legacy)
        for suffix, fallback in ((":manual", []), (":revision", 0)):
            st.session_state[retained_key + suffix] = copy.deepcopy(st.session_state.get(legacy_key + suffix, fallback))
if draft_key not in st.session_state:
    try:
        st.session_state[draft_key] = load_bids(cfg, source_version) if vers else default_bids(cfg)
        st.session_state[draft_key + ":manual"] = []
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        business_error("当前报价暂不能用于运行", exc, "请到输入设置的主体报价区修复并另存新版本；不会替换成默认报价。")
        st.stop()
draft = st.session_state[draft_key]
st.caption(f"本次报价草稿基于 v{draft.get('version', 1)}；选择“最新”时按当前磁盘版本加载，各版本草稿分别保留。")
st.subheader("选择主体报价（运行前）")
st.caption("当前模型按机组/电站编号出清，不虚构多机电厂组合。修改仅保存在本次运行草稿，原报价版本文件不变；提交运行后使用冻结副本。")
manual_units = st.session_state.get(draft_key + ":manual", [])
st.caption("手工保护主体：" + ("、".join(manual_units) if manual_units else "无") + "；策略生成不会覆盖已手工保存主体。")
if draft.get("_cost_basis"):
    st.info("该报价含研究成本依据与策略记录；运行采用当前草稿，不会因成本档案或随机种子变化而自动重建报价。")
unit_types={u["id"]:u.get("type",group) for group in ("thermal","renewable","hydro","storage","pumped") for u in cfg.units.get(group,[])}
unit = st.selectbox("报价机组/电站", list(draft["units"]), format_func=lambda u:f"{u} · {TYPE_CN.get(unit_types.get(u), '主体')}", disabled=active)
cost_bid_comparison(cfg, draft, [unit])
quote_rows = [{"曲线": label, "段": i, "起点（MW）": seg["p_start"], "终点（MW）": seg["p_end"], "报价（元/MWh）": seg["price"]}
              for curve, label in CURVE_CN.items() for i, seg in enumerate(draft["units"][unit].get(curve, []), 1)]
st.dataframe(pd.DataFrame(quote_rows), hide_index=True, width="stretch")
st.caption("通常在输入设置页完成表格编辑并另存报价版本；此处核对当前运行草稿。高级文本修改仅影响本次运行草稿。")
with st.expander("技术详情：高级报价文本编辑", expanded=False):
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
            business_error("报价未保存，原草稿保持不变", exc, "请检查文本格式、段量段价及设备边界；也可到输入设置使用表格编辑。")

with st.expander("技术详情：随机策略平移（非成本生成，不改固定成本）", expanded=False):
    from smm.bid_simulation import simulate_bids
    seed=st.number_input("报价情景随机种子", min_value=0, max_value=2147483647, value=int(cfg.base['seed']), disabled=active)
    chosen=st.multiselect("需要模拟的主体编号", list(draft['units']), disabled=active)
    st.caption("不同电源采用不同变化规则；仅改变报价，不改变技术容量。整场运行只生成一次，实时窗口不随机改价；已手工保存主体受保护。")
    if st.button("生成、验证并采用模拟报价", disabled=active):
        try:
            candidate=simulate_bids(cfg,selected_unit_ids=chosen,seed=int(seed),base_bids=draft,
                                    protected_unit_ids=st.session_state[draft_key+':manual'])
            st.session_state[draft_key]=candidate
            st.session_state[draft_key+':revision']=st.session_state.get(draft_key+':revision',0)+1
            st.rerun()
        except ValueError as exc:
            business_error("模拟报价未采用，草稿保持不变", exc, "请核对所选主体及手工保护状态。")
    if draft.get('_simulation'):
        st.caption('已采用策略平移报价。固定成本不变，手工主体受保护。')
        with st.expander('技术详情：策略生成记录', expanded=False):
            st.json(draft['_simulation'])

historical_regulation=bool(cfg.mode.get('regulation',{}).get('enabled',False))
if historical_regulation:
    st.error('历史调频实验场景：当前版本只做电能量现货，不纳入调频。正常启动已禁用；请选择新纯电能量场景，不会暗改旧场景。')
else:
    st.caption('当前范围仅电能量现货：标准SCUC→固定整数、相同目标约束的SCED；不含调频或容量市场。')

st.markdown("**启动前确认**")
st.caption(f"将冻结当前场景参数、报价草稿与成本档案。范围：{'仅日前出清（不含实时利润）' if mode == 'da' else ('完整运行日' if not periods else f'前 {int(periods)} 个时段（调试前缀，非完整日）')}。保存输入不等于启动，点击下方按钮才启动新运行。")
if st.button("▶ 开始运行", type="primary", width="stretch", disabled=active or historical_regulation):
    try:
        d = start_run(name, mode, int(draft.get("version",1)), int(periods) or None, bids=draft)
        st.session_state["watch_run"] = str(d)
        st.success(f"已启动冻结报价：{d.relative_to(sc.RUNS_DIR.parent)}")
        st.rerun()
    except Exception as exc:
        business_error("操作未完成，未启动新的有效运行", exc, "请核对场景名称、输入及运行状态。")


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
        failure_detail = m.get("error") or "运行失败，见日志"
        # Keep actionable resource context visible without presenting a solver
        # traceback as the business summary or asserting an unverified cause.
        failure_summary = ("运行失败，请核对水电状态与约束；不能视为完整日结果"
                           if "水电" in str(failure_detail)
                           else "运行失败，不能视为完整日结果")
        business_error(failure_summary, failure_detail, "请展开技术诊断查看原因；已执行前缀仍可查看。")
    if s == "done":
        st.info("所选运行范围计算结束，可到“结果”页查看；这不代表精度或物理检查通过。" + (" 注意：这是调试截断范围，不是完整日。" if m.get("periods") and m["periods"] < 96 else ""))
    if (Path(sel)/"cancel.request").exists() and s in ("running", "starting", "cancelling"):
        st.warning("取消已请求；当前SCUC/SCED不强杀，完成后停止。已执行前缀不是完整日结算。")
    from smm.live_runner import read_live
    live = read_live(sel)
    if s == "done":
        from market_views import result_health_dashboard, _saved_json
        saved_summary = _saved_json(Path(sel)/'summary.json')
        saved_da = saved_summary.get('da_summary') or _saved_json(Path(sel)/'da/summary.json')
        if not saved_da and not (Path(sel)/'da').exists():saved_da = saved_summary
        result_health_dashboard(saved_summary, saved_da, directory=sel, live=live)
    if live.get('da_stages'):
        from market_views import stages_dashboard
        with st.expander('日前SCUC/SCED阶段诊断（不是实时执行）'):
            stages_dashboard(live['da_stages'],prefix='da_stage:'+sel)
    if live.get("executed"):
        st.subheader(f"实时已执行：{live['executed_periods']} / {live['total_periods']} 时段")
        st.caption("每个15分钟窗口结束立即刷新。下图/表只含已执行首时段；前瞻计划单独展示且会被下一窗口修订。")
        from common import display_frame
        executed=live["executed"]
        from market_views import system_metrics
        system_metrics(executed)
        from market_views import reserve_dashboard
        with st.expander('安全备用：系统与逐主体（非交易市场）'):
            reserve_dashboard(executed,prefix='live_reserve:'+sel)
        prices=pd.DataFrame(executed["prices"])
        st.line_chart(prices.groupby("period")[["lmp", "lmp_raw"]].mean().rename(columns={"lmp": "结算电价", "lmp_raw": "原始节点电价"}).rename_axis("时段"), y_label="节点算术均价（元/MWh）")
        units=pd.DataFrame(executed["units"])
        st.area_chart(units.groupby(["period","type"]).P_mw.sum().unstack(fill_value=0).rename(columns=TYPE_CN).rename_axis("时段"), y_label="已执行分类型净功率 MW")
        st.dataframe(display_frame(pd.DataFrame(executed["system"])), hide_index=True, width="stretch")
        with st.expander("当前前瞻窗口（非最终执行结果）"):
            st.dataframe(display_frame(pd.DataFrame(live.get("lookahead",[]))), hide_index=True, width="stretch")
        from market_views import unit_dashboard, stages_dashboard, frozen_bids, frozen_units
        with st.expander("全部主体详细报价 / 运行 / 能量收益",expanded=False):
            unit_dashboard(executed,bids=frozen_bids(sel),prefix='live:'+sel,units_config=frozen_units(sel))
        with st.expander("选择滚动窗口查看SCUC/SCED阶段对照",expanded=False):
            history=live.get('stage_history',{})
            if history:
                window=st.selectbox('窗口起点',list(history),format_func=lambda x:f'第{int(x)}段',key='stage_window:'+sel)
                stages_dashboard(history[window],prefix='live_stage:'+sel)
            else:stages_dashboard(None,prefix='live_stage:'+sel)
        with st.expander('水电实际状态与窗口末跟踪目标',expanded=False):
            from market_views import hydro_window_dashboard
            hydro_window_dashboard(live.get('hydro_window_history',{}),prefix='live_hydro:'+sel)
        if not live.get("settled"):
            st.caption("滚动前缀仅用于观察，不包含完整日最终结算；失败/取消不会冒充完成结果。")
    elif live.get("phase"):
        phase_label = {"preparing": "准备输入", "da": "日前出清", "da_done": "日前完成", "realtime": "实时滚动", "done": "完成", "failed": "失败", "stopped": "停止"}.get(live["phase"], "处理中")
        st.info(f"当前阶段：{phase_label}；日前尚未完成时不会伪造实时曲线。")
    with st.expander("技术详情：运行日志（最后 40 行）", expanded=False):
        st.code("\n".join(log.splitlines()[-40:]) or "（等待输出…）", language="text")
    with st.expander("技术详情：结果文件位置", expanded=False):
        st.code(sel, language=None)
    st.markdown("**全部运行**")
    st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"]), "报价": str(x.get("bid_version") or "最新"),
                                "状态": STATUS_CN.get(x["status"], x["status"]), "开始": x.get("started"), "用时 s": x.get("elapsed_s"),
                                "目录": Path(x["dir"]).name} for x in runs]), hide_index=True, width="stretch")


monitor()
