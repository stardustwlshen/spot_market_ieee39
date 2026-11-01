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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from smm import scenario as sc  # noqa: E402
from smm.config import load_config  # noqa: E402

TYPE_CN = {"coal": "燃煤", "gas_ccgt": "燃气CCGT", "gas_gt": "燃气GT", "hydro": "水电", "wind": "风电", "pv": "光伏",
           "tie": "区外来电", "storage": "储能", "storage_dis": "储能放电", "storage_ch": "储能充电", "load": "负荷",
           "slack": "平衡松弛"}
TYPE_COLOR = {"coal": "#5b5b5b", "gas_ccgt": "#e07b39", "gas_gt": "#f2b134", "hydro": "#2f7ed8", "wind": "#3cb371",
              "pv": "#ffd700", "tie": "#9b59b6", "storage": "#e74c3c", "storage_dis": "#e74c3c", "storage_ch": "#f1948a"}
MODE_CN = {"da": "仅日前", "full": "全流程"}
STATUS_CN = {"running": "运行中", "done": "完成", "failed": "失败", "stopped": "已停止"}


def page_setup(title: str):
    st.set_page_config(page_title=f"{title}｜IEEE39 现货出清", page_icon="⚡", layout="wide")
    st.title(title)


def period_labels(T: int = 96) -> list[str]:
    return [f"{t // 4:02d}:{(t % 4) * 15:02d}" for t in range(T)]


# ----------------------------------------------------------------------------- 场景选择
def scenario_selector(key: str = "scenario", sidebar: bool = True, label: str = "当前场景") -> str:
    names = sc.list_scenarios()
    cur = st.session_state.get("scenario", sc.DEFAULT)
    if cur not in names:
        cur = names[0]
    box = st.sidebar if sidebar else st
    name = box.selectbox(label, names, index=names.index(cur), key=f"_sel_{key}")
    st.session_state["scenario"] = name
    return name


def scen_config(name: str):
    return load_config(root=sc.scenario_path(name))


# ----------------------------------------------------------------------------- 运行管理
def start_run(scenario: str, mode: str, bid_version: int | None, periods: int | None) -> Path:
    d = sc.new_run_dir(scenario, mode)
    meta = dict(scenario=scenario, mode=mode, bid_version=bid_version, periods=periods, status="running", stage="启动")
    from datetime import datetime
    meta["started"] = datetime.now().isoformat(timespec="seconds")
    cmd = [sys.executable, "-u", str(ROOT / "scripts" / "run_scenario.py"), "--scenario", scenario, "--mode", mode,
           "--run-dir", str(d)]
    if bid_version:
        cmd += ["--bid-version", str(bid_version)]
    if periods:
        cmd += ["--periods", str(periods)]
    log = open(d / "run.log", "w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)   # 独立会话：关闭网页/刷新不影响后台运行
    meta["pid"] = p.pid
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    try:   # 已结束但未回收的僵尸进程
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        return stat.split()[2] != "Z"
    except OSError:
        return True


def run_status(m: dict) -> str:
    s = m.get("status", "running")
    if s == "running" and m.get("pid") and not pid_alive(m["pid"]):
        meta_p = Path(m["dir"]) / "meta.json"
        cur = json.loads(meta_p.read_text(encoding="utf-8"))
        s = cur.get("status", "running")
        if s == "running":
            s = "failed"
            cur.update(status="failed", stage="进程意外退出")
            meta_p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def stop_run(m: dict):
    pid = m.get("pid")
    if pid and pid_alive(pid):
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except OSError:
            os.kill(int(pid), signal.SIGTERM)
    meta_p = Path(m["dir"]) / "meta.json"
    cur = json.loads(meta_p.read_text(encoding="utf-8"))
    cur.update(status="stopped", stage="用户停止")
    meta_p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def run_progress(m: dict, log: str) -> float:
    """粗略进度：日前约占全流程 60%，实时按日志中的“时段 t”推算。"""
    if m.get("status") == "done":
        return 1.0
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
    return f"{m.get('scenario', '?')} / {tag}（{MODE_CN.get(m.get('mode'), m.get('mode'))}，{v}{extra}，{st_}）"


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

    for k in ("da_unit_awards", "da_nodal_prices", "da_storage", "da_line_flows", "da_system", "da_hydro"):
        R[k] = rd(da_dir / f"{k}.csv")
        if R[k] is None:
            R[k] = rd(d / f"{k}.csv")
    for k in ("rt_units", "rt_prices", "rt_lines", "rt_storage", "rt_system", "settlement_detail", "settlement_by_entity",
              "settlement_by_type", "settlement_line_rent", "settlement_reconciliation"):
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
    R["snapshot_root"] = str(d / "scenario_snapshot") if (d / "scenario_snapshot").exists() else str(ROOT)
    return R


def get_result(m: dict) -> dict:
    d = Path(m["dir"])
    mt = max((p.stat().st_mtime for p in d.glob("*.json")), default=0)
    return load_result(str(d), mt)


@st.cache_data(show_spinner=False)
def branches_with_rate(snapshot_root: str) -> pd.DataFrame:
    """支路表 + 生效限额（rate_mw × line_scale）。"""
    root = Path(snapshot_root)
    br = pd.read_csv(root / "data" / "case39" / "branches.csv")
    try:
        import yaml
        base = yaml.safe_load((root / "config" / "base.yaml").read_text(encoding="utf-8"))
        scale = base.get("line_scale") or {}
    except Exception:  # noqa: BLE001
        scale = {}
    br["scale"] = br.branch_id.map(lambda b: float(scale.get(b, 1.0)))
    br["rate_eff"] = br.rate_mw * br.scale
    return br


def line_loading(R: dict, stage: str) -> pd.DataFrame | None:
    """返回 period, line, flow_mw, rate_eff, loading, mu。"""
    br = branches_with_rate(R["snapshot_root"])[["branch_id", "rate_eff"]].rename(columns={"branch_id": "line"})
    if stage == "da":
        df = R.get("da_line_flows")
        if df is None:
            return None
        df = df.merge(br, on="line", how="left")
    else:
        df = R.get("rt_lines")
        if df is None:
            return None
        df = df.merge(br, on="line", how="left")
    df = df.copy()
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
    for grp in ("thermal", "tie", "renewable", "hydro", "storage"):
        for u in cfg.units.get(grp, []):
            out.setdefault(int(u["bus"]), []).append(u["id"])
    return out


def fmt_money(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x / 1e4:,.1f} 万元"


def df_download(df: pd.DataFrame, name: str, label: str = "下载 CSV", key=None):
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv", key=key)
