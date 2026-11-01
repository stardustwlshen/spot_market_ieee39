"""运行：选择场景，后台运行“仅日前”或“全流程”，显示进度与日志。"""
from pathlib import Path

import pandas as pd
import streamlit as st

from common import (MODE_CN, STATUS_CN, page_setup, run_label, run_progress, run_status, sc, scen_config, scenario_selector,
                    start_run, stop_run)

page_setup("运行", "选择场景与模式，后台运行；可随时切换页面，进度与日志每 2 秒刷新")
name = scenario_selector()
cfg = scen_config(name)
from smm.bids import bid_dir  # noqa: E402

vers = sorted(int(p.stem.split("_v")[-1]) for p in bid_dir(cfg).glob("bids_v*.json"))
rt = cfg.mode["realtime"]
smode = "A 执行日前曲线" if rt["storage"]["mode"] == "fixed_da" else (
    "B1 窗口末 ±x" if rt["storage"]["anchor"] == "window_end_pct" else "B2 整点等值（默认）")
st.info(f"场景 **{name}**：{cfg.base['date']}，{ {'wet': '丰水期', 'dry': '枯水期'}[cfg.base['season']]}，load_scale {cfg.base['load_scale']}，"
            f"实时储能 {smode}，前瞻 {rt['lookahead_h']} h，独立日内 {'开' if cfg.mode['intraday']['enabled'] else '关'}", icon="🧩")

c1, c2, c3 = st.columns(3)
mode = c1.radio("运行模式", ["da", "full"], index=1, format_func=lambda m: {"da": "仅日前（约 2 min）", "full": "全流程：日前→实时滚动→结算（约 3 min）"}[m])
v = c2.selectbox("报价版本", ["最新"] + vers[::-1], help="“最新”= 场景中版本号最大的报价")
periods = c3.number_input("实时只滚动前 N 时段（0 = 全天 96，调试用）", 0, 96, 0, disabled=mode != "full")

running = [m for m in sc.list_runs() if run_status(m) == "running"]
if running:
    st.info(f"当前有 {len(running)} 个运行在后台进行。可以同时再启动，但会争用 CPU（HiGHS 默认 4 线程）。")
if st.button("▶ 开始运行", type="primary", width="stretch"):
    d = start_run(name, mode, None if v == "最新" else int(v), int(periods) or None)
    st.session_state["watch_run"] = str(d)
    st.success(f"已在后台启动：{d.relative_to(sc.RUNS_DIR.parent)}")


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
    logp = Path(sel) / "run.log"
    log = logp.read_text(encoding="utf-8", errors="replace") if logp.exists() else ""
    s = m["status"]
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.progress(run_progress(m, log), text=f"{STATUS_CN.get(s, s)} · 阶段：{m.get('stage', '')}")
    c2.metric("用时（s）", m.get("elapsed_s", "…"))
    if s == "running" and c3.button("停止该运行"):
        stop_run(m)
        st.rerun()
    if s == "failed":
        st.error(m.get("error", "运行失败，见日志"))
    if s == "done":
        st.success("完成，可到“结果”页查看。")
    with st.expander("运行日志（最后 40 行）", expanded=s != "done"):
        st.code("\n".join(log.splitlines()[-40:]) or "（等待输出…）", language="text")
    st.caption(f"结果目录：{sel}")
    st.markdown("**全部运行**")
    st.dataframe(pd.DataFrame([{"场景": x["scenario"], "模式": MODE_CN.get(x["mode"]), "报价": x.get("bid_version") or "最新",
                                "状态": STATUS_CN.get(x["status"], x["status"]), "开始": x.get("started"), "用时 s": x.get("elapsed_s"),
                                "目录": Path(x["dir"]).name} for x in runs]), hide_index=True, width="stretch")


monitor()
