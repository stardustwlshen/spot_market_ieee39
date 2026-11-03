"""网页应用公共函数：路径、场景选择、结果读取、拓扑布局、通用图表。"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from chinese_labels import display_frame, csv_bytes, column_label, localize_figure

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from smm import scenario as sc  # noqa: E402
from smm.config import load_config  # noqa: E402

TYPE_CN = {"coal": "燃煤", "gas_ccgt": "燃气联合循环", "gas_gt": "燃气轮机", "hydro": "水电", "wind": "风电", "pv": "光伏",
           "tie": "区外来电", "storage": "储能", "storage_dis": "储能放电", "storage_ch": "储能充电", "load": "负荷",
           "pumped": "抽水蓄能", "pumped_gen": "抽蓄发电", "pumped_pump": "抽蓄抽水",
           "slack": "平衡松弛"}
TYPE_COLOR = {"coal": "#5b5b5b", "gas_ccgt": "#e07b39", "gas_gt": "#f2b134", "hydro": "#2f7ed8", "wind": "#3cb371",
              "pv": "#ffd700", "tie": "#9b59b6", "storage": "#e74c3c", "storage_dis": "#e74c3c", "storage_ch": "#f1948a",
              "pumped": "#0e7c86", "pumped_gen": "#0e7c86", "pumped_pump": "#7fc8cf"}
MODE_CN = {"da": "仅日前", "full": "全流程"}
STATUS_CN = {"running": "运行中", "done": "完成", "failed": "失败", "stopped": "已停止", "starting": "准备中", "cancelling": "取消等待中"}


# ----------------------------------------------------------------------------- 统一视觉风格
FONT_FAMILY = "Noto Sans CJK SC, Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
PALETTE = ["#1f5fa8", "#d1495b", "#2a9d8f", "#e9a03b", "#6a4c93", "#8d99ae", "#3cb371", "#c97b63"]
DA_COLOR, RT_COLOR = "#1f5fa8", "#d1495b"           # 日前 / 实时 在所有图中固定配色
LMP_SCALE = "Viridis"                               # 节点电价热力图（顺序色阶，色盲友好）
LOAD_SCALE = [[0, "#f7fbff"], [0.5, "#fdd49e"], [0.8, "#fc8d59"], [1.0, "#b30000"]]   # 线路负载率
DIFF_SCALE = "RdBu_r"                               # 差值（以 0 为中心）
TYPE_SYMBOL = {"coal": "square", "gas_ccgt": "diamond", "gas_gt": "diamond-open", "hydro": "hexagon", "wind": "triangle-up",
               "pv": "star", "tie": "pentagon", "storage": "cross",
               "pumped": "hourglass"}
PAGES = [("pages/2_运行.py", "报价模拟", "▶️", "选择机组、改报价、运行市场"),
         ("pages/3_结果.py", "结果", "📊", "完整电价、出力、约束与结算"),
         ("pages/4_场景对比.py", "场景对比", "⚖️", "对比两次报价模拟结果")]


_CSS = """
<style>
/* 指标卡：边框卡片，数值不截断（自动换行、字号适中） */
div[data-testid="stMetric"] {background: var(--secondary-background-color, #f2f5f9); border: 1px solid #dde3ea;
    border-radius: 0.5rem; padding: 0.6rem 0.8rem; min-height: 96px;}
div[data-testid="stMetricValue"] > div {font-size: 1.45rem; white-space: normal !important; overflow: visible !important;
    text-overflow: clip !important; line-height: 1.25;}
div[data-testid="stMetricLabel"] p {font-size: 0.85rem; color: #4b5563; white-space: normal !important;}
div[data-testid="stMetricDelta"] {font-size: 0.8rem;}
/* 页面留白与标题 */
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;}
h1 {font-size: 1.75rem !important; font-weight: 700;}
h2, h3 {font-weight: 600;}
.smm-sub {color: #6b7280; margin-top: -0.6rem; margin-bottom: 0.8rem; font-size: 0.95rem;}
.smm-brand {font-weight: 700; font-size: 1.05rem; color: #1f5fa8; margin-bottom: 0.1rem;}
.smm-card {border: 1px solid #dde3ea; border-radius: 0.6rem; padding: 0.8rem 1rem; background: #fff; height: 100%;}
.smm-card b {color: #1f5fa8;}
.stTabs [data-baseweb="tab-list"] {gap: 0.2rem; flex-wrap: wrap;}
.stTabs [data-baseweb="tab"] {padding: 0.55rem 0.9rem; font-weight: 600;}
.stTabs [aria-selected="true"] {color:#1f5fa8; background:rgba(31,95,168,.07); border-radius:8px 8px 0 0;}
div[data-testid="stExpander"] {border-radius:10px;}
div[data-testid="stMetricLabel"] {line-height:1.5;}
/* 窄屏适配：列纵向堆叠、指标字号缩小、图表不溢出 */
@media (max-width: 760px) {
  .block-container {padding-left: 0.8rem; padding-right: 0.8rem; padding-top: 1.2rem;}
  div[data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: 0.5rem;}
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {min-width: calc(50% - 0.5rem) !important; flex: 1 1 calc(50% - 0.5rem) !important;}
  div[data-testid="stMetricValue"] > div {font-size: 1.15rem;}
  div[data-testid="stMetric"] {min-height: 0; padding: 0.45rem 0.7rem;}
  h1 {font-size: 1.35rem !important;}
}
@media (max-width: 480px) {
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {min-width: 100% !important;}
}
</style>
"""


def _register_plotly_template():
    import plotly.graph_objects as go
    import plotly.io as pio
    if "smm" in pio.templates:
        return
    t = go.layout.Template(pio.templates["plotly_white"])
    t.layout.update(
        font=dict(family=FONT_FAMILY, size=13, color="#1f2937"),
        title=dict(font=dict(size=15, color="#111827"), x=0.0, xanchor="left", y=0.97, yanchor="top"),
        colorway=PALETTE,
        margin=dict(l=60, r=20, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0, bgcolor="rgba(0,0,0,0)",
                    font=dict(size=12)),
        hoverlabel=dict(font=dict(family=FONT_FAMILY)),
        xaxis=dict(showgrid=True, gridcolor="#eef1f5", linecolor="#9ca3af", ticks="outside", title=dict(standoff=8)),
        yaxis=dict(showgrid=True, gridcolor="#eef1f5", linecolor="#9ca3af", zeroline=True, zerolinecolor="#d1d5db",
                   title=dict(standoff=8)),
        coloraxis=dict(colorbar=dict(thickness=14, outlinewidth=0)),
        hovermode="x unified",
    )
    pio.templates["smm"] = t
    pio.templates.default = "smm"
    import plotly.express as px
    px.defaults.template = "smm"
    px.defaults.color_discrete_sequence = PALETTE


_register_plotly_template()


def style_fig(fig, title: str | None = None, x: str | None = None, y: str | None = None, height: int | None = None,
              legend: str | None = None, hover: str | None = None):
    """统一补齐图表标题、坐标轴（含单位）、图例标题、高度。"""
    kw = {}
    if title is not None:
        kw["title_text"] = title
    if height:
        kw["height"] = height
    if legend is not None:
        kw["legend_title_text"] = legend
    if hover:
        kw["hovermode"] = hover
    fig.update_layout(**kw)
    if x is not None:
        fig.update_xaxes(title_text=x)
        if x.startswith("时刻"):                      # 96 个 15 min 时刻：只标约每 2 h 一个刻度，水平显示
            fig.update_xaxes(nticks=13, tickangle=0)
    if y is not None:
        fig.layout.yaxis.title.text = y  # Preserve explicitly labelled secondary-axis units.
    return fig


def show_fig(fig, key=None):
    st.plotly_chart(localize_figure(fig), width="stretch", key=key, config={"displaylogo": False, "responsive": True})


def sidebar_nav():
    with st.sidebar:
        st.markdown('<div class="smm-brand">⚡ IEEE39 现货市场出清仿真</div>', unsafe_allow_html=True)
        st.caption("单机改价 · 全市场模拟")
        for path, label, icon, helptext in PAGES:
            try:
                st.page_link(path, label=label, icon=icon, help=helptext)
            except Exception:  # noqa: BLE001  （测试环境未注册多页时跳过）
                pass
        st.divider()


def page_setup(title: str, subtitle: str | None = None):
    st.set_page_config(page_title=f"{title}｜IEEE39 现货出清", page_icon="⚡", layout="wide",
                       initial_sidebar_state="auto")   # 宽屏展开、窄屏自动收起
    st.markdown(_CSS, unsafe_allow_html=True)
    sidebar_nav()
    st.title(title)
    if subtitle:
        st.markdown(f'<div class="smm-sub">{subtitle}</div>', unsafe_allow_html=True)


def metric_grid(items: list[tuple], per_row: int = 4):
    """items: (标签, 数值字符串[, delta[, help]])，按行排布的带边框指标卡。"""
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for c, it in zip(cols, items[i:i + per_row]):
            lab, val = it[0], it[1]
            delta = it[2] if len(it) > 2 else None
            hlp = it[3] if len(it) > 3 else None
            plain = delta is not None and not str(delta).lstrip().startswith(("+", "-", "−"))
            c.metric(lab, val, delta=delta, help=hlp, delta_color="off" if plain else "normal",
                     delta_arrow="off" if plain else "auto")


def empty_state(msg: str, hint: str | None = None, icon: str = "📭"):
    st.info(f"{msg}" + (f"\n\n{hint}" if hint else ""), icon=icon)


def wan(x, nd: int = 1) -> str:
    """元 → 万元 字符串。"""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x / 1e4:,.{nd}f}"


def num_cols(df: pd.DataFrame, fmt: str = "%.1f", overrides: dict | None = None) -> dict:
    """为数值列生成统一的 column_config（右对齐、固定小数位）。"""
    cfg = {}
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            if pd.api.types.is_integer_dtype(df[c]):
                cfg[c] = st.column_config.NumberColumn(c, format="%d")
            else:
                cfg[c] = st.column_config.NumberColumn(c, format=fmt)
    for k, v in (overrides or {}).items():
        cfg[k] = st.column_config.NumberColumn(k, format=v)
    return cfg


def show_df(df: pd.DataFrame, fmt: str = "%.1f", overrides: dict | None = None, height=None, **kw):
    df = display_frame(df)
    overrides = {column_label(k):v for k,v in (overrides or {}).items()}
    st.dataframe(df, hide_index=True, width="stretch", column_config=num_cols(df, fmt, overrides),
                 height=height or "auto", **kw)


def period_labels(T: int = 96) -> list[str]:
    return [f"{t // 4:02d}:{(t % 4) * 15:02d}" for t in range(T)]


# ----------------------------------------------------------------------------- 场景选择
def scenario_selector(key: str = "scenario", sidebar: bool = True, label: str = "当前场景") -> str:
    names = sc.list_scenarios()
    recommended=next((n for n in ("水电窗口硬锚点_20261102","电能量完善版_20261102") if n in names),sc.DEFAULT)
    cur = st.session_state.get("scenario", recommended)
    if cur not in names:
        cur = names[0]
    box = st.sidebar if sidebar else st
    name = box.selectbox(label, names, index=names.index(cur), key=f"_sel_{key}")
    st.session_state["scenario"] = name
    return name


def scen_config(name: str):
    return load_config(root=sc.scenario_path(name))


# ----------------------------------------------------------------------------- 运行管理
def start_run(scenario: str, mode: str, bid_version: int | None, periods: int | None, bids=None, regulation=None) -> Path:
    """Serialize launches; never report a spawned worker as an unstarted run."""
    from datetime import datetime
    import traceback
    from smm.live_runner import atomic_json, freeze_inputs
    from smm.process_control import LaunchError, file_lock, process_identity
    requested = load_config(root=sc.scenario_path(scenario))
    if requested.mode.get("regulation", {}).get("enabled", False) or (regulation or {}).get("enabled", False):
        raise ValueError("当前仅支持纯电能量现货正常运行；调频场景仅作历史归档，不启动。")
    d = None
    meta = {}
    spawned = False
    stage = "检查启动环境"
    try:
        launcher = process_identity()
        sc.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stage = "取得启动锁"
        with file_lock(sc.RUNS_DIR / ".launch.lock"):
            stage = "检查已有运行"
            if any(run_status(m) in ("running", "starting", "cancelling") for m in sc.list_runs()):
                raise RuntimeError("已有运行或取消请求正在处理，或历史进程身份尚无法确认；请核对运行记录，避免重复求解。")
            stage = "建立运行记录"
            d = sc.new_run_dir(scenario, mode)
            meta = dict(scenario=scenario, mode=mode, bid_version=bid_version, periods=periods,
                        status="starting", stage="冻结输入", started=datetime.now().isoformat(timespec="seconds"),
                        launcher_identity=launcher)
            atomic_json(d / "meta.json", meta)
            stage = "冻结并校验输入"
            freeze_inputs(sc.scenario_path(scenario), d, bids=bids, regulation=regulation)
            cmd = [sys.executable, "-u", str(ROOT / "scripts" / "run_scenario.py"), "--scenario", scenario,
                   "--mode", mode, "--run-dir", str(d)]
            if bid_version is not None: cmd += ["--bid-version", str(bid_version)]
            if periods: cmd += ["--periods", str(periods)]
            stage = "创建后台进程"
            with open(d / "run.log", "w", encoding="utf-8") as log:
                options = {"start_new_session": True} if os.name != "nt" else {}
                worker_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
                process = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, env=worker_env, **options)
                spawned = True
                stage = "记录已启动进程"
                # The child writes its own identity to meta.json. Never overwrite
                # that file here: it may already contain a newer/terminal state.
                try:
                    identity = process_identity(process.pid)
                except Exception:
                    identity = None  # A very short-lived worker may already exit.
                if identity is not None:
                    atomic_json(d / "worker.json", identity)
                (d / "worker.pid").write_text(str(process.pid), encoding="utf-8")
                # If the child exited before an identity could be captured, its
                # Popen handle still proves that this exact child has ended.
                poll = getattr(process, "poll", None)
                exit_code = poll() if identity is None and poll is not None else None
                if exit_code is not None:
                    with file_lock(d / ".meta.lock", blocking=True):
                        current = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                        if current.get("status") in ("starting", "running"):
                            current.update(status="failed", stage="后台进程启动后退出", exit_code=exit_code,
                                           error=f"后台进程已退出（返回码 {exit_code}），请查看 run.log；未完成结算。")
                            atomic_json(d / "meta.json", current)
        return d
    except Exception as exc:
        detail = traceback.format_exc()
        if d is not None:
            try:
                (d / "launch_error.log").write_text(detail, encoding="utf-8")
                if not spawned:
                    atomic_json(d / "meta.json", {**meta, "status": "failed", "stage": stage,
                                "error": f"{type(exc).__name__}: {exc}"})
                else:
                    atomic_json(d / "launch_warning.json", {"stage": stage, "error": str(exc)})
            except OSError:
                pass  # Preserve the original failure, including disk/permission errors.
        raise LaunchError(stage, exc, directory=d, process_started=spawned) from exc


def run_status(m: dict) -> str:
    from smm.live_runner import atomic_json
    from smm.process_control import file_lock, process_state
    s = m.get("status", "running")
    if s not in ("running", "starting", "cancelling"):
        return s
    d = Path(m["dir"])
    identity = m.get("process_identity")
    if identity is None:
        try:
            identity = json.loads((d / "worker.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    # Bare legacy PIDs, foreign namespaces, incomplete launches and access denied
    # are unknown. Keep their reservation instead of risking a second solve.
    if process_state(identity) == "dead":
        try:
            with file_lock(d / ".meta.lock"):
                meta_p = d / "meta.json"
                cur = json.loads(meta_p.read_text(encoding="utf-8"))
                s = cur.get("status", "running")
                latest_identity = cur.get("process_identity", identity)
                if s in ("running", "starting", "cancelling") and process_state(latest_identity) == "dead":
                    cur.update(status="failed", stage="进程意外退出", error="后台进程退出，保留执行前缀；未完成结算")
                    atomic_json(meta_p, cur)
                    s = "failed"
        except (OSError, ValueError, RuntimeError):
            pass  # Cannot safely update now; preserve the reservation and retry later.
    if s in ("running", "starting") and (d / "cancel.request").exists():
        return "cancelling"
    return s


def stop_run(m: dict):
    """Cooperative stop: never kill an optimizer mid-write or settle a partial day."""
    from smm.live_runner import atomic_json
    d=Path(m["dir"])
    (d/"cancel.request").write_text("cancel at next solve boundary", encoding="utf-8")
    # Worker owns meta.json; do not race its completion write.


def run_progress(m: dict, log: str) -> float:
    """粗略进度：日前约占全流程 60%，实时按日志中的“时段 t”推算。"""
    if m.get("status") == "done":
        return 1.0
    from smm.live_runner import read_live
    live = read_live(m["dir"])
    if live.get("executed_periods"):
        return min(0.6 + 0.4 * live["executed_periods"] / live["total_periods"], 0.99)
    T = m.get("periods") or 96
    if "[DA]" not in log:
        return 0.03
    if m.get("mode") == "da":
        return 0.85 if "SCED" in log or "LP" in log else 0.4
    ts = [int(x) for x in re.findall(r"时段 (\d+)", log)]
    if ts:
        return min(0.6 + 0.4 * (max(ts) + 1) / T, 0.99)
    return 0.4 if "[RT]" not in log else 0.6


def run_label(m: dict) -> str:
    tag = Path(m["dir"]).name
    st_ = STATUS_CN.get(m.get("status", ""), m.get("status", ""))
    v = f"v{m['bid_version']}" if m.get("bid_version") else "最新报价"
    extra = f"，前 {m['periods']} 时段" if m.get("periods") else ""
    return f"{m.get('display_label',m.get('scenario', '?'))} / {tag}（{MODE_CN.get(m.get('mode'), m.get('mode'))}，{v}{extra}，{st_}）"


def all_results() -> list[dict]:
    """可展示的结果：runs/ 下完成的运行 + outputs/ 下命令行生成的历史结果（只读导入）。"""
    out = [m for m in sc.list_runs() if m.get("status") == "done"]
    od = ROOT / "outputs"
    if od.exists():
        for d in sorted(od.iterdir()):
            if d.is_dir() and (d / "summary.json").exists() and (d.name.startswith("full_") or d.name.startswith("da_")) and "_MKT_" in d.name:
                mode = "full" if d.name.startswith("full_") else "da"
                out.append(dict(scenario="命令行outputs", mode=mode, status="done", dir=str(d), started=""))
    return out


def result_label(m: dict) -> str:
    if m.get("scenario") == "命令行outputs":
        return f"[outputs] {Path(m['dir']).name}（{MODE_CN[m['mode']]}）"
    return run_label(m)


# ----------------------------------------------------------------------------- 结果读取
@st.cache_data(show_spinner=False)
def load_result(d: str, mtime: float) -> dict:  # mtime 只用于缓存失效
    d = Path(d)
    da_dir = d / "da" if (d / "da").exists() else d
    R: dict = {"dir": str(d)}

    def rd(p: Path):
        return pd.read_csv(p) if p.exists() else None

    for k in ("da_unit_awards", "da_nodal_prices", "da_storage", "da_line_flows", "da_system", "da_hydro", "da_pumped"):
        R[k] = rd(da_dir / f"{k}.csv")
        if R[k] is None:
            R[k] = rd(d / f"{k}.csv")
    for k in ("rt_units", "rt_prices", "rt_lines", "rt_storage", "rt_system", "rt_pumped", "rt_hydro", "settlement_detail", "settlement_by_entity",
              "settlement_by_type", "settlement_line_rent", "settlement_reconciliation", "rt_regulation", "rt_regulation_system", "da_regulation_indicative", "da_regulation_system_indicative",
              "operating_profit_by_unit", "operating_profit_by_period", "operating_profit_by_type"):
        R[k] = rd(d / f"{k}.csv")
    sp = d / "summary.json"
    R["summary"] = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    da_sp = da_dir / "summary.json"
    R["da_summary"] = (R["summary"].get("da_summary") or
                       (json.loads(da_sp.read_text(encoding="utf-8")) if da_sp.exists() else {}))
    R["full"] = R["rt_prices"] is not None
    prof = d / "profiles.csv"
    R["profiles"] = rd(prof)
    snap = d / "scenario_snapshot" / "data" / "case39"
    R["case_dir"] = str(snap if snap.exists() else ROOT / "data" / "case39")
    R["snapshot_root"] = str(d / "scenario_snapshot")
    R["input_provenance_known"] = (d / "scenario_snapshot" / "config" / "base.yaml").exists()
    return R


def get_result(m: dict) -> dict:
    d = Path(m["dir"])
    mt = max((p.stat().st_mtime for p in d.glob("*.json")), default=0)
    return load_result(str(d), mt)


@st.cache_data(show_spinner=False)
def branches_with_rate(snapshot_root: str) -> pd.DataFrame:
    """支路表 + 生效限额（rate_mw × line_scale）。"""
    root = Path(snapshot_root)
    branch_file = root / "data" / "case39" / "branches.csv"
    if not branch_file.exists():
        # Legacy topology is illustrative only; never borrow current operational limits.
        br = pd.read_csv(ROOT / "data" / "case39" / "branches.csv")
        br["rate_mw"] = np.nan
        br["scale"] = np.nan
        br["rate_eff"] = np.nan
        return br
    br = pd.read_csv(branch_file)
    try:
        import yaml
        base = yaml.safe_load((root / "config" / "base.yaml").read_text(encoding="utf-8"))
        scale = base.get("line_scale") or {}
    except Exception:  # noqa: BLE001
        br["scale"] = np.nan
        br["rate_eff"] = np.nan
        return br
    br["scale"] = br.branch_id.map(lambda b: float(scale.get(b, 1.0)))
    br["rate_eff"] = br.rate_mw * br.scale * float(base.get("line_capacity_multiplier", 1.0))
    return br


def line_loading(R: dict, stage: str) -> pd.DataFrame | None:
    """返回 period, line, flow_mw, rate_eff, loading, mu。"""
    df = R.get("da_line_flows" if stage == "da" else "rt_lines")
    if df is None:
        return None
    df = df.copy()
    if "rate_mw" in df:
        df["rate_eff"] = pd.to_numeric(df.rate_mw, errors="coerce")
    else:
        # Same run's persisted DA ratings may also label RT flows.
        da = R.get("da_line_flows")
        rates = pd.Series(dtype=float)
        if da is not None and "rate_mw" in da:
            unique = da.groupby("line").rate_mw.nunique()
            rates = da[da.line.isin(unique[unique == 1].index)].groupby("line").rate_mw.first()
        df["rate_eff"] = df.line.map(rates)
        frozen = branches_with_rate(R["snapshot_root"]).set_index("branch_id").rate_eff
        df["rate_eff"] = df.rate_eff.fillna(df.line.map(frozen))
    df["rate_eff"] = df.rate_eff.where(df.rate_eff > 0)
    df["loading"] = df.flow_mw.abs() / df.rate_eff
    df["mu"] = df.mu_plus.abs() + df.mu_minus.abs()
    df["congested"] = (df.loading >= 0.999) | (df.mu > 1e-6)
    return df


def lmp_pivot(df: pd.DataFrame) -> pd.DataFrame:
    return df.pivot(index="bus", columns="period", values="lmp")


def load_weighted(prices: pd.DataFrame, loads: pd.DataFrame | None = None) -> pd.Series:
    """按节点负荷加权的系统均价（每时段）。rt_prices 自带 load_mw；日前用 buses.csv 负荷分布。"""
    if "load_mw" in prices:
        w = prices.load_mw.clip(lower=0)
    else:
        w = prices.bus.map(loads.set_index("bus").load_mw).fillna(0) if loads is not None else pd.Series(1.0, index=prices.index)
    g = prices.assign(w=w, lw=prices.lmp * w).groupby("period")
    return g.lw.sum() / g.w.sum().replace(0, np.nan)


# ----------------------------------------------------------------------------- 拓扑布局
@st.cache_data(show_spinner=False)
def topo_layout(case_dir: str) -> dict:
    import networkx as nx
    br = pd.read_csv(Path(case_dir) / "branches.csv")
    G = nx.Graph()
    G.add_edges_from(zip(br.from_bus, br.to_bus))
    pos = nx.kamada_kawai_layout(G)
    return {int(k): (float(v[0]), float(v[1])) for k, v in pos.items()}


def unit_buses(cfg) -> dict:
    out = {}
    for grp in ("thermal", "tie", "renewable", "hydro", "storage", "pumped"):
        for u in cfg.units.get(grp, []):
            out.setdefault(int(u["bus"]), []).append(u["id"])
    return out


def fmt_money(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x / 1e4:,.1f} 万元"


def df_download(df: pd.DataFrame, name: str, label: str = "下载 CSV", key=None):
    readable = str(Path(name).with_name(Path(name).stem + "_中文.csv"))
    st.download_button("下载中文表格" if label == "下载 CSV" else label + "（中文）", csv_bytes(df), file_name=readable, mime="text/csv", key=key)
    with st.expander("原始字段兼容下载（技术用途）"):
        st.download_button("下载原字段表格", csv_bytes(df, chinese=False), file_name=name, mime="text/csv", key=None if key is None else key+":raw")


@st.cache_data(show_spinner=False)
def bus_resources(snapshot_root: str) -> dict:
    """{母线: [(机组id, 资源类型), ...]}；类型取 units.yaml 的 type，缺省按分组（tie/hydro/storage）。"""
    import yaml
    try:
        uy = yaml.safe_load((Path(snapshot_root) / "config" / "units.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for grp in ("thermal", "tie", "renewable", "hydro", "storage", "pumped"):
        for u in uy.get(grp, []) or []:
            if u.get("enabled", True) is False:
                continue
            out.setdefault(int(u["bus"]), []).append((u["id"], u.get("type") or grp))
    return out
