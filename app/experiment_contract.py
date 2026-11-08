"""Layered single-day experiment summary. Reads the saved scene; does not solve or save."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ASSUMPTION = "假设值"

# Paths are relative to cfg.mode except base.* which reads cfg.base.
_ASSUMPTIONS = (
    ("mode", "reserve.fixed_up_mw", "上备用固定总量（MW）", "2026-11-08 研究设定，不是省级规则或 N-1 认证；仅 fixed 模式作为需求"),
    ("mode", "reserve.rho_L", "负荷备用系数 ρ_L", "DL/T 5429 只给 2%–5% 区间，系数本身无唯一原文"),
    ("mode", "reserve.rho_RE", "新能源备用系数 ρ_RE", "两省规则未给数值；仅 formula 模式进入需求"),
    ("mode", "reserve.rho_L_dn", "下备用负荷系数", "下备用默认关闭；系数无唯一原文"),
    ("mode", "reserve.rho_RE_dn", "下备用新能源系数", "下备用默认关闭"),
    ("mode", "reserve.mu_RE", "风光计入备用的置信因子", "甘V3.2 有该项但未给数值"),
    ("mode", "reserve.T_dur_h", "备用能量持续（h）", "研究设定：时段末库存折 15 min，不是响应速度认证"),
    ("mode", "intraday.interval_h", "日内滚动间隔（h）", "仅日内开启时使用；川规则为事件触发，未规定固定周期"),
    ("mode", "intraday.lead_issue_periods", "日内提前下发时段数", "时序参数，无规则原文"),
    ("mode", "intraday.mip_rel_gap", "日内 MIP 相对间隙", "求解参数"),
    ("base", "solver.mip_rel_gap", "日前 MIP 相对间隙", "求解参数"),
    ("mode", "realtime.mip_rel_gap", "实时 MIP 相对间隙", "求解参数"),
    ("mode", "realtime.value_method.band_fraction", "储能/抽蓄中间偏差带", "相对额定能量或可调库容；soft 日末不再强制回归该包络"),
    ("mode", "realtime.value_method.hydro_band_fraction", "水电中间偏差带", "相对调节库容"),
    ("mode", "realtime.value_method.terminal_policy.penalty_yuan_per_discharge_mwh", "软日末罚价（元/可放电MWh）", "模型目标罚金，不是市场结算付款"),
    ("mode", "realtime.value_method.fallback_penalty", "硬约束转软罚因子", "仅明确 SCUC/正式 SCED 物理不可行时使用"),
    ("mode", "realtime.storage.x", "锚点浮动比例 x", "仅 anchor=window_end_pct；甘肃细则未给 x"),
    ("mode", "realtime.storage.anchor_penalty", "锚点软罚因子（元/MWh）", "仅储能模式为 sced_anchor 时进入目标"),
    ("mode", "realtime.tracking_policy.penalty_yuan_per_discharge_mwh", "窗口末软跟踪罚价（元/可放电MWh）", "仅旧的窗口末跟踪水电策略使用"),
)


def _walk(root, path):
    current = root
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _mode(cfg):
    return cfg.mode if isinstance(getattr(cfg, "mode", None), dict) else {}


def _base(cfg):
    return cfg.base if isinstance(getattr(cfg, "base", None), dict) else {}


def _text(value):
    if isinstance(value, float):
        return format(value, "g")
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) if value else "（空）"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return "未记录"
    return str(value)


def _error_labels(cfg):
    from smm.resource_error_scenarios import RESOURCE_LABELS, SCENARIO_LABELS
    forecast = _base(cfg).get("forecast") or {}
    selected = forecast.get("error_scenarios") if isinstance(forecast, dict) else None
    if not isinstance(selected, dict):
        return "误差档未记录"
    parts = []
    for kind in ("load", "wind", "pv"):
        recipe = selected.get(kind)
        label = SCENARIO_LABELS.get(kind, {}).get(recipe, _text(recipe))
        parts.append(f"{RESOURCE_LABELS.get(kind, kind)} {label}")
    return "；".join(parts)


def scenario_facts(cfg):
    """Saved scenario identity. Pending selectors are not this record."""
    intraday = _mode(cfg).get("intraday") or {}
    enabled = bool(intraday.get("enabled", False))
    season = _base(cfg).get("season")
    from smm.reserve_policy import reserve_policy_label
    return {
        "date": _base(cfg).get("date", "未记录"),
        "season": season,
        "season_label": {"wet": "丰水期", "dry": "枯水期"}.get(season, "水文未记录"),
        "periods": getattr(cfg, "T", None),
        "minutes": None if getattr(cfg, "dt", None) is None else getattr(cfg, "dt", 0) * 60,
        "intraday_enabled": enabled,
        "intraday_label": "开启" if enabled else "关闭",
        "intraday_note": (
            "当前场景已显式打开独立日内；只更新参考计划，不单独结算。产品默认仍是关闭。"
            if enabled else
            "关闭（甘肃模式，默认不设独立日内市场）。"
        ),
        "errors": _error_labels(cfg),
        "reserve": reserve_policy_label(cfg),
    }


def _research_value(cfg, namespace, path):
    root = _base(cfg) if namespace == "base" else _mode(cfg)
    return _walk(root, path)


def assumption_rows(cfg):
    rows = []
    for namespace, path, label, reason in _ASSUMPTIONS:
        rows.append({
            "项目": label,
            "当前值": _text(_research_value(cfg, namespace, path)),
            "标记": ASSUMPTION,
            "理由": reason,
            "配置路径": ("base." if namespace == "base" else "") + path,
        })
    return rows


def research_rows(cfg):
    """Read-only research switches, including items that are not assumed numbers."""
    rt = _mode(cfg).get("realtime") or {}
    storage = rt.get("storage") or {}
    vm = rt.get("value_method") or {}
    terminal = vm.get("terminal_policy") or {}
    intraday = _mode(cfg).get("intraday") or {}
    handoff = rt.get("handoff") or {}
    rows = [
        {"项目": "日内市场", "当前值": "开启" if intraday.get("enabled") else "关闭", "标记": "甘肃模式默认关闭", "说明": "不是假设值；开启只影响参考与交接"},
        {"项目": "价值法 · 储能/抽蓄", "当前值": _text(storage.get("mode")), "标记": "研究设定", "说明": "value 为当前默认；不改价值公式"},
        {"项目": "价值法 · 水电", "当前值": _text(rt.get("hydro")), "标记": "研究设定", "说明": "value 为当前默认"},
        {"项目": "水电前瞻", "当前值": _text(vm.get("hydro_lookahead")), "标记": "研究设定", "说明": "reference_continuation 是软偏好，不是可行证明"},
        {"项目": "软日末", "当前值": _text(terminal.get("mode")), "标记": "研究设定", "说明": "soft 允许偏差并计模型罚金"},
        {"项目": "软日末罚价（元/可放电MWh）", "当前值": _text(terminal.get("penalty_yuan_per_discharge_mwh")), "标记": ASSUMPTION, "说明": "见假设值一览"},
        {"项目": "锚点", "当前值": _text(storage.get("anchor")), "标记": "仅 sced_anchor 生效", "说明": "价值法默认不使用该锚点"},
        {"项目": "锚点比例 x", "当前值": _text(storage.get("x")), "标记": ASSUMPTION, "说明": "仅 window_end_pct"},
        {"项目": "交接来源", "当前值": _text(rt.get("handoff_source")), "标记": "研究设定", "说明": "日内关闭或交接失败时回日前"},
        {"项目": "交接资源", "当前值": _text(handoff), "标记": "研究设定", "说明": "false 的资源改取日前"},
        {"项目": "GT 实时策略", "当前值": _text(rt.get("intraday_commit_rt")), "标记": "研究设定", "说明": "inherit 继承有效日内启停；free 为窗口内再决策"},
        {"项目": "实时锁定类型", "当前值": _text(rt.get("lock_types")), "标记": "规则/研究设定", "说明": "煤电锁定有甘肃依据；CCGT 锁定为研究设定"},
        {"项目": "实时自由类型", "当前值": _text(rt.get("free_types")), "标记": "研究设定", "说明": "无有效日内交接时 GT 在实时 MIP 内决策"},
        {"项目": "日前 MIP 相对间隙", "当前值": _text(_walk(_base(cfg), "solver.mip_rel_gap")), "标记": ASSUMPTION, "说明": "求解参数"},
        {"项目": "日内 MIP 相对间隙", "当前值": _text(intraday.get("mip_rel_gap")), "标记": ASSUMPTION, "说明": "仅日内开启时使用"},
        {"项目": "实时 MIP 相对间隙", "当前值": _text(rt.get("mip_rel_gap")), "标记": ASSUMPTION, "说明": "求解参数"},
    ]
    return rows


def gt_strategy(cfg):
    rt = _mode(cfg).get("realtime") or {}
    choice = rt.get("intraday_commit_rt")
    if choice == "inherit":
        meaning = "inherit：日内开启且该轮交接有效时，实时继承日内 GT 启停；否则不把旧日内轮当成新决策。"
    elif choice == "free":
        meaning = "free：实时窗口内可再调 GT。"
    else:
        meaning = "未识别的 GT 策略，不能当成继承或自由。"
    return f"{meaning} 锁定 {_text(rt.get('lock_types'))}；自由类型 {_text(rt.get('free_types'))}。"


def value_strategy(cfg):
    rt = _mode(cfg).get("realtime") or {}
    storage = rt.get("storage") or {}
    vm = rt.get("value_method") or {}
    terminal = vm.get("terminal_policy") or {}
    penalty = terminal.get("penalty_yuan_per_discharge_mwh")
    return (
        f"储能/抽蓄 {_text(storage.get('mode'))}，水电 {_text(rt.get('hydro'))}，"
        f"软日末 {_text(terminal.get('mode'))}，罚价 {_text(penalty)} 元/可放电MWh（{ASSUMPTION}）。"
        f"锚点 {_text(storage.get('anchor'))}，x={_text(storage.get('x'))}（仅对应模式生效）。"
    )


def quote_facts(draft, info):
    if not isinstance(draft, dict):
        return {"版本": "未记录", "来源": "报价不能读取", "主体数": "未记录", "模型": "未记录", "缺省补齐": "未记录"}
    units = draft.get("units") if isinstance(draft.get("units"), dict) else {}
    filled = draft.get("_filled_default") or []
    return {
        "版本": draft.get("version", "未记录"),
        "来源": (info or {}).get("basis_label") or "未记录",
        "主体数": len(units),
        "模型": draft.get("default_bid_model") or "未记录",
        "缺省补齐": "、".join(str(item) for item in filled) if filled else "无",
    }


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def contract_material(cfg, draft):
    """Stable identity of the saved scene and quote. Display labels are excluded."""
    return {
        "date": _base(cfg).get("date"),
        "season": _base(cfg).get("season"),
        "errors": (_base(cfg).get("forecast") or {}).get("error_scenarios"),
        "intraday_enabled": bool((_mode(cfg).get("intraday") or {}).get("enabled", False)),
        "reserve": _mode(cfg).get("reserve"),
        "realtime": _mode(cfg).get("realtime"),
        "solver": _base(cfg).get("solver"),
        "quote": draft,
    }


def input_fingerprint(cfg, draft):
    digest = hashlib.sha256(_canonical(contract_material(cfg, draft)).encode("utf-8")).hexdigest()
    return digest[:12]


def code_sha():
    """Short SHA of the checkout that is showing this page, not a frozen run."""
    try:
        root = Path(__file__).resolve().parents[1]
        text = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True, timeout=5, stderr=subprocess.DEVNULL)
        text = text.strip()
        return text or "未知"
    except (OSError, subprocess.SubprocessError):
        return "未知"


def contract_rows(cfg, draft, info, *, mode="full", periods=None):
    facts = scenario_facts(cfg)
    quote = quote_facts(draft, info)
    if mode == "da":
        scope = "仅日前计划，无实时与结算"
    elif periods is not None and facts["periods"] is not None and int(periods) < int(facts["periods"]):
        scope = f"实时前 {int(periods)} / {facts['periods']} 段，不是完整日"
    else:
        scope = f"完整单日 · {facts['periods']} 段" if facts["periods"] is not None else "运行范围未记录"
    fingerprint = input_fingerprint(cfg, draft)
    return [
        {"项": "运行范围", "内容": scope},
        {"项": "日期 / 水文", "内容": f"{facts['date']} · {facts['season_label']}"},
        {"项": "误差档", "内容": facts["errors"]},
        {"项": "日内市场", "内容": f"{facts['intraday_label']}。{facts['intraday_note']}"},
        {"项": "GT 策略", "内容": gt_strategy(cfg)},
        {"项": "上备用", "内容": facts["reserve"]},
        {"项": "价值法 / 软日末", "内容": value_strategy(cfg)},
        {"项": "报价包", "内容": f"版本 {quote['版本']} · {quote['来源']} · {quote['主体数']} 个主体 · {quote['模型']} · 缺省补齐 {quote['缺省补齐']}"},
        {"项": "输入指纹", "内容": fingerprint},
        {"项": "当前代码", "内容": code_sha() + "（本次界面；启动后另冻结运行当时的源码）"},
    ], fingerprint


def render_intraday_status(facts):
    import streamlit as st
    if facts["intraday_enabled"]:
        st.warning("日内市场：开启。" + facts["intraday_note"])
    else:
        st.info("日内市场：关闭。" + facts["intraday_note"])


def render_experiment_layers(cfg, name, *, pending=False):
    """Scenario strip is always visible. Resource and research blocks start closed."""
    import pandas as pd
    import streamlit as st
    facts = scenario_facts(cfg)
    st.markdown("**场景层 · 已保存**")
    render_intraday_status(facts)
    minutes = "未记录" if facts["minutes"] is None else f"{facts['minutes']:g} 分钟"
    periods = "未记录" if facts["periods"] is None else f"{facts['periods']} 段"
    st.caption(
        f"日期 {facts['date']} · {facts['season_label']} · {periods} × {minutes}"
        f" · 误差 {facts['errors']} · 备用 {facts['reserve']}"
    )
    if pending:
        st.caption("场景选择尚未应用。上面是已保存场景，不是待启动条件。")
    else:
        st.caption("日内与备用显示已保存配置。要修改请到运行步骤的高级参数；这里不另存一份。")
    with st.expander("资源层（按需）", expanded=False):
        st.caption("编辑仍在「2 机组」。这里只读容量摘要，不改参与标志，也不保存。")
        if st.checkbox("加载资源容量摘要", key="experiment_resources:" + name):
            try:
                from unit_input_view import resource_capacity_rows
                st.dataframe(pd.DataFrame(resource_capacity_rows(cfg)), hide_index=True, width="stretch")
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                st.warning("资源摘要暂不能读取，原场景未改。" + str(exc))
    with st.expander("研究开关（默认折叠）", expanded=False):
        st.caption("只读。保存仍只发生在运行步骤的高级参数，下次启动才进入合约卡。")
        st.dataframe(pd.DataFrame(research_rows(cfg)), hide_index=True, width="stretch")
    with st.expander("假设值一览", expanded=False):
        st.caption("下表都是假设值：没有省级规则原文中的唯一数值。改变配置后这里跟着已保存值走，不新增默认。")
        st.dataframe(pd.DataFrame(assumption_rows(cfg)), hide_index=True, width="stretch")


def render_contract_card(cfg, name, draft, info, *, mode="full", periods=None, pending=False):
    """Return whether the operator has confirmed this exact card."""
    import pandas as pd
    import streamlit as st
    facts = scenario_facts(cfg)
    st.subheader("将要生效的合约卡")
    st.caption("只读摘要。勾选确认后才能开始；开始仍走原来的校验和冻结，不在这里改模型。")
    if pending:
        st.warning("场景条件尚未应用，这张卡不是待启动条件。")
    render_intraday_status(facts)
    rows, fingerprint = contract_rows(cfg, draft, info, mode=mode, periods=periods)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander("合约卡中的假设值", expanded=False):
        st.dataframe(pd.DataFrame(assumption_rows(cfg)), hide_index=True, width="stretch")
    confirmed = st.checkbox(
        "已核对合约卡，确认按此启动",
        key=f"contract_ok:{name}:{fingerprint}:{mode}:{periods}",
    )
    return confirmed
