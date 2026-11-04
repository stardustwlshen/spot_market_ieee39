"""报价：缺省报价生成、JSON/CSV 读写（带版本号）、按统一规则 MKT 校验（方案第 5 节；参数见 config/modes.yaml）。

报价价格单位 元/MWh，量单位 MW。缺省报价数值均为“假设值”。
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
from numbers import Real
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

THERMAL_TYPES = ("coal", "gas_ccgt", "gas_gt")


# ----------------------------------------------------------------------------- 缺省报价
def _segments(bounds, prices):
    return [dict(p_start=round(float(a), 3), p_end=round(float(b), 3), price=float(c))
            for (a, b), c in zip(zip(bounds[:-1], bounds[1:]), prices)]


def _thermal_default(u, lim):
    """煤电/燃气缺省报价（假设值）：首段 0~Pmin，其余 5 段等分（每段 ≥10% 装机），价格为 10 元整数倍且严格递增。"""
    pmin, pmax = u["pmin"], u["pmax"]
    n_rest = 5
    base = {"coal": 300, "gas_ccgt": 520, "gas_gt": 650}[u["type"]]
    step = {"coal": [0, 20, 40, 70, 110], "gas_ccgt": [0, 20, 40, 60, 80], "gas_gt": [0, 20, 40, 70, 100]}[u["type"]]
    rest_prices = [base + s for s in step]
    first_price = max(0.0, lim["bid_min"]) if u["type"] == "coal" else rest_prices[0] - 100
    bounds = [0.0, pmin] + list(np.linspace(pmin, pmax, n_rest + 1)[1:])
    return dict(mode="price_quantity", segments=_segments(bounds, [first_price] + rest_prices),
                startup_cost=dict(u["startup"]), noload_cost=float(u["noload"]))


def resolve_startup_bid(cfg, bid):
    """Per-unit fee; historical state bids migrate from their own hot value only."""
    mode = cfg.mode.get("startup_cost_mode", "state_dependent")
    if mode not in ("uniform_per_unit", "state_dependent"):
        raise ValueError("startup_cost_mode must be uniform_per_unit or state_dependent")
    if mode == "state_dependent":
        return dict(mode=mode, cost_yuan=None, source="state_dependent")
    if "startup_cost_yuan" in bid:
        value = bid["startup_cost_yuan"]
        source = ("legacy_hot_migration" if bid.get("startup_cost_source") == "legacy_hot_migration"
                  and value == bid.get("startup_cost", {}).get("hot") else "explicit_scalar")
    else:
        value = bid.get("startup_cost", {}).get("hot")
        source = "legacy_hot_migration"
    if not _finite_number(value) or value < 0:
        raise ValueError("startup_cost_yuan (or legacy hot migration) must be finite nonnegative yuan/start")
    return dict(mode=mode, cost_yuan=float(value), source=source)


def effective_bids(cfg, bids):
    """Detached effective input for exports; never rewrite archived bid versions."""
    raise_on_errors(validate_bids(cfg, bids, require_complete=True))
    out = copy.deepcopy(bids)
    for unit in cfg.units["thermal"]:
        bid = out["units"][unit["id"]]
        effective = resolve_startup_bid(cfg, bid)
        if effective["mode"] == "uniform_per_unit":
            bid["startup_cost_yuan"] = effective["cost_yuan"]
            bid["startup_cost_source"] = effective["source"]
        bid["startup_cost_mode"] = effective["mode"]
    return out


def default_bids(cfg: Config, version: int = 1) -> dict:
    lim = cfg.mode["price_limits"]
    states = set(cfg.mode["startup_states"]) | {"cold"}
    units = {}
    for u in cfg.units["thermal"]:
        b = _thermal_default(u, lim)
        b["startup_cost"] = {k: v for k, v in b["startup_cost"].items() if k in states}
        if cfg.mode.get("startup_cost_mode", "state_dependent") == "uniform_per_unit":
            b["startup_cost_yuan"] = float(b["startup_cost"]["hot"])
        units[u["id"]] = b
    for r in cfg.units["renewable"]:
        units[r["id"]] = dict(mode="price_quantity", segments=_segments(np.linspace(0, r["cap"], 6), [0, 20, 40, 60, 80]),
                              avail_pmax=None)
    for h in cfg.units["hydro"]:
        cap = h["n_units"] * h["unit_pmax"]
        # 径流式缺省“planned”（价格接受，0 价进入出清，按来水不弃电）：对应川V4.0 7.7.2 方式一（径流式水电可全容量报申报价格下限）
        units[h["id"]] = dict(mode="planned" if h.get("regulation") == "run_of_river" else "price_quantity",
                              hydro_bid=dict(method="segments", season=cfg.base["season"]),
                              segments=_segments(np.linspace(0, cap, 6), [100, 130, 160, 200, 240]))
    for s in cfg.units["storage"]:
        bnd = np.linspace(0, s["pmax"], 4)
        units[s["id"]] = dict(mode="price_quantity", dis_segments=_segments(bnd, [420, 450, 480]),
                              ch_segments=_segments(bnd, [300, 280, 260]),
                              soc_min=s["soc_min"], soc_max=s["soc_max"], soc_end=s.get("soc_end_bid"), self_schedule=None)
    for p in cfg.units.get("pumped", []):
        units[p["id"]] = pumped_default(p)
    return dict(date=cfg.base["date"], rule_set=cfg.rule_set, version=version,
                created=_dt.datetime.now().isoformat(timespec="seconds"), note="缺省报价（假设值）", units=units)


PUMPED_GEN_PRICES = [420, 450, 480]     # 假设值：发电三段（元/MWh），高于抽水报价并覆盖晚高峰价位
PUMPED_PUMP_PRICES = [300]              # 假设值：抽水状态单一报价（额定抽水功率对应价格，广东抽蓄交易细则征求意见稿 6.1）


def pumped_default(p) -> dict:
    """抽蓄缺省报价（假设值）：市场规则同储能——发电曲线 ≙ 放电曲线、抽水报价 ≙ 充电曲线，
    发电最低价 > 抽水最高价（同储能 storage_dis_gt_ch，川V4.0）；申报日末上库库容 vup_end ≙ 储能申报期末 SOC
    （缺省 = units.yaml vup_end_bid，null = 不申报、由出清决定）。抽水单一报价：定速机组抽水功率只能取台数×额定。"""
    N = int(p["n_units"])
    gb = np.linspace(0, N * p["unit_gen_max"], len(PUMPED_GEN_PRICES) + 1)
    pb = np.linspace(0, N * p["unit_pump"], len(PUMPED_PUMP_PRICES) + 1)
    return dict(mode="price_quantity", gen_segments=_segments(gb, PUMPED_GEN_PRICES),
                pump_segments=_segments(pb, PUMPED_PUMP_PRICES),
                vup_end=p.get("vup_end_bid", p["vup0"]))


def _archive_default_fill_provenance(bids: dict) -> None:
    """Move a prior operation's active warning to historical provenance only.

    Call on a detached loaded/saved copy. This never changes any resource bid.
    _filled_default describes this load's missing resources, not their ancestry.
    """
    previous = bids.pop("_filled_default", [])
    if previous:
        history = list(bids.get("_default_fill_history", []))
        for uid in previous:
            if uid not in history:
                history.append(uid)
        bids["_default_fill_history"] = history


def fill_missing_bids(cfg: Config, bids: dict) -> dict:
    """报价文件中缺少的资源（如新增的抽蓄 PS1、启用的 H2）用缺省报价补齐；已停用资源的报价忽略。"""
    _archive_default_fill_provenance(bids)
    # A disabled duplicate must never shadow an active resource with the same
    # id and replace its submitted bid with a generated default.
    active = _unit_meta(cfg)
    disabled = set(cfg.units.get("_disabled", [])) - set(active)
    dflt = None
    filled = []
    for uid in list(bids["units"]):
        if uid in disabled:
            bids["units"].pop(uid)
    for uid in active:
        if uid not in bids["units"]:
            dflt = dflt or default_bids(cfg)
            bids["units"][uid] = copy.deepcopy(dflt["units"][uid])
            filled.append(uid)
    if filled:
        bids["_filled_default"] = filled
    return bids


# ----------------------------------------------------------------------------- 读写
def bid_dir(cfg: Config) -> Path:
    return cfg.root / "bids" / cfg.base["date"] / cfg.rule_set


def save_bids(cfg: Config, bids: dict, note: str | None = None, new_version: bool = True) -> Path:
    # Validate before creating a version or writing anything; never clip user bids.
    raise_on_errors(validate_bids(cfg, bids))
    d = bid_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    bids = copy.deepcopy(bids)
    _archive_default_fill_provenance(bids)
    if new_version:
        existing = [int(p.stem.split("_v")[-1]) for p in d.glob("bids_v*.json")]
        bids["version"] = max(existing, default=0) + 1
    if note:
        bids["note"] = note
    bids["created"] = _dt.datetime.now().isoformat(timespec="seconds")
    p = d / f"bids_v{bids['version']}.json"
    p.write_text(json.dumps(bids, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_bids(cfg: Config, version: int | None = None) -> dict:
    d = bid_dir(cfg)
    files = sorted(d.glob("bids_v*.json"), key=lambda p: int(p.stem.split("_v")[-1]))
    if not files:
        raise FileNotFoundError(f"{d} 下没有报价文件，先运行 scripts/make_default_bids.py")
    p = files[-1] if version is None else d / f"bids_v{version}.json"
    bids = json.loads(p.read_text(encoding="utf-8"))
    # Check malformed/non-finite input before default filling can mask or crash on it.
    raise_on_errors(validate_bids(cfg, bids))
    bids = fill_missing_bids(cfg, bids)
    raise_on_errors(validate_bids(cfg, bids))
    return bids


def bids_to_csv(bids: dict, path: Path) -> None:
    rows = []
    for uid, b in bids["units"].items():
        for kind in ("segments", "dis_segments", "ch_segments", "gen_segments", "pump_segments"):
            for k, s in enumerate(b.get(kind) or [], start=1):
                rows.append(dict(unit_id=uid, mode=b.get("mode"), curve=kind, seg=k,
                                 startup_cost_mode=b.get("startup_cost_mode"), startup_cost_yuan=b.get("startup_cost_yuan"),
                                 startup_cost_source=b.get("startup_cost_source"), **s))
    pd.DataFrame(rows).to_csv(path, index=False)


def bids_from_csv(base: dict, path: Path) -> dict:
    """用 CSV 中的分段覆盖 base 报价（批量编辑用），返回新报价（版本号由 save_bids 递增）。"""
    from .input_validation import read_input_csv
    df = read_input_csv(path)
    out = copy.deepcopy(base)
    for (uid, curve), g in df.groupby(["unit_id", "curve"]):
        g = g.sort_values("seg")
        out["units"][uid][curve] = [dict(p_start=float(r.p_start), p_end=float(r.p_end), price=float(r.price))
                                    for r in g.itertuples()]
        if "mode" in g and isinstance(g["mode"].iloc[0], str):
            out["units"][uid]["mode"] = g["mode"].iloc[0]
    return out


# ----------------------------------------------------------------------------- 校验
@dataclass
class Issue:
    unit_id: str
    level: str  # error / warn
    rule: str
    msg: str

    def __str__(self):
        return f"[{self.level}] {self.unit_id} {self.rule}: {self.msg}"


def _unit_meta(cfg: Config) -> dict:
    meta = {}
    for u in cfg.units["thermal"]:
        meta[u["id"]] = dict(type=u["type"], cap=u["pmax"], pmin=u["pmin"], startup=u["startup"])
    for r in cfg.units["renewable"]:
        meta[r["id"]] = dict(type=r["type"], cap=r["cap"], pmin=0)
    for h in cfg.units["hydro"]:
        meta[h["id"]] = dict(type="hydro", cap=h["n_units"] * h["unit_pmax"], pmin=0)
    for s in cfg.units["storage"]:
        meta[s["id"]] = dict(type="storage", cap=s["pmax"], pmin=0)
    for p in cfg.units.get("pumped", []):
        meta[p["id"]] = dict(type="pumped", cap=p["n_units"] * p["unit_gen_max"], cap_pump=p["n_units"] * p["unit_pump"], pmin=0)
    return meta


def _finite_number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def bid_rules_for_type(cfg: Config, typ: str) -> dict:
    """Resolve optional bid-only overrides, never settlement clear_min/clear_max.

    `bid_rules.default` and `bid_rules.by_type.<type>` override these legacy
    defaults. No provincial numeric values are inferred here. Charge/pump curves
    retain their decreasing direction even when strictness is changed.
    """
    m = cfg.mode
    is_sto = typ in ("storage", "pumped")
    r = dict(price_min=m["price_limits"]["bid_min"], price_max=m["price_limits"]["bid_max"],
             segments_min=m["segments"]["storage_min" if is_sto else "min"],
             segments_max=m["segments"]["storage_max" if is_sto else "max"],
             strict_monotonic=m.get("price_strict_increasing", True),
             price_step=m.get("price_step"), max_adjacent_step=m.get("max_adjacent_step"),
             min_segment_fraction=m.get("min_seg_frac", 0.), min_segment_mw=0.,
             min_segment_basis="capacity", require_capacity_coverage=False)
    overrides = m.get("bid_rules", {})
    profile = overrides.get("profile", "generic")
    if profile not in ("generic", "gd_reference_thermal"):
        raise ValueError(f"未知报价限制 profile: {profile}")
    r["thermal_market_above_pmin"] = profile == "gd_reference_thermal" and typ in THERMAL_TYPES
    if r["thermal_market_above_pmin"]:
        # Optional thermal shape reference only. Bid price caps remain independent
        # research parameters; do not infer them from a settlement cap.
        r.update(segments_min=1, segments_max=10, strict_monotonic=False,
                 price_step=None, max_adjacent_step=None,
                 min_segment_fraction=.05, min_segment_mw=1.,
                 min_segment_basis="dispatchable", require_capacity_coverage=True)
    r.update(overrides.get("default", {}))
    r.update(overrides.get("by_type", {}).get("hydro" if typ == "hydro_planned" else typ, {}))
    for key in ("price_min", "price_max", "segments_min", "segments_max", "min_segment_fraction", "min_segment_mw"):
        if not _finite_number(r[key]):
            raise ValueError(f"报价限制配置 {typ}.{key} 必须为有限数值")
    if r["price_min"] > r["price_max"]:
        raise ValueError(f"报价限制配置 {typ} 下限不能大于上限")
    if not (1 <= r["segments_min"] <= r["segments_max"]) or any(int(r[k]) != r[k] for k in ("segments_min", "segments_max")):
        raise ValueError(f"报价限制配置 {typ} 段数必须为有序正整数")
    if r["min_segment_basis"] not in ("capacity", "dispatchable"):
        raise ValueError(f"报价限制配置 {typ}.min_segment_basis 必须为 capacity 或 dispatchable")
    for key in ("price_step", "max_adjacent_step", "min_segment_fraction", "min_segment_mw"):
        if r[key] is not None and (not _finite_number(r[key]) or r[key] < 0):
            raise ValueError(f"报价限制配置 {typ}.{key} 必须为非负有限数值")
    return r


def _numeric_structure_issues(uid, b, cfg, cap):
    """Fail closed before arithmetic, so bad JSON yields field-level errors."""
    issues = []
    def error(field, msg):
        issues.append(Issue(uid, "error", "输入格式", f"{field}: {msg}"))
    for curve in ("segments", "dis_segments", "ch_segments", "gen_segments", "pump_segments"):
        values = b.get(curve)
        if values is None:
            continue
        if not isinstance(values, list):
            error(curve, "须为分段列表"); continue
        for n, seg in enumerate(values, 1):
            if not isinstance(seg, dict):
                error(f"{curve}[{n}]", "须为包含 p_start/p_end/price 的对象"); continue
            for field in ("p_start", "p_end", "price"):
                if not _finite_number(seg.get(field)):
                    error(f"{curve}[{n}].{field}", "须为有限数值（禁止 NaN/Infinity/布尔/字符串）")
    for field in ("soc_min", "soc_max", "soc_end", "vup_end", "noload_cost"):
        if (field in b and field in ("soc_min", "soc_max", "noload_cost") and not _finite_number(b[field])) or (b.get(field) is not None and not _finite_number(b[field])):
            error(field, "须为有限数值")
    if "startup_cost_yuan" in b and (not _finite_number(b["startup_cost_yuan"]) or b["startup_cost_yuan"] < 0):
        error("startup_cost_yuan", "须为非负有限数值，单位元/次")
    su = b.get("startup_cost", {})
    if not isinstance(su, dict):
        error("startup_cost", "须为分状态数值对象")
    else:
        for state, value in su.items():
            if not _finite_number(value) or value < 0:
                error(f"startup_cost.{state}", "须为非负有限数值")
    if _finite_number(b.get("noload_cost")) and b["noload_cost"] < 0:
        error("noload_cost", "不得为负")
    for field in ("self_schedule", "avail_pmax"):
        values = b.get(field)
        if values is None:
            continue
        if not isinstance(values, list) or len(values) != cfg.T or not all(_finite_number(x) for x in values):
            error(field, f"须为 {cfg.T} 点有限数值列表")
        elif field == "avail_pmax" and any(x < 0 or x > cap + 1e-6 for x in values):
            error(field, f"各点须在 [0,{cap}] MW；不允许自动裁剪")
    return issues


def _check_curve(uid, segs, cap, typ, cfg, issues, curve="segments", decreasing=False):
    """按统一规则 MKT 校验一条量价曲线（参数见 config/modes.yaml，各项注明川/甘来源）。"""
    m = cfg.mode
    rules = bid_rules_for_type(cfg, typ)
    lim = {"bid_min": rules["price_min"], "bid_max": rules["price_max"]}
    E = lambda rule, msg, lvl="error": issues.append(Issue(uid, lvl, rule, msg))
    if not segs:
        E("段数", f"{curve} 为空")
        return
    internal_block = rules["thermal_market_above_pmin"]
    market_segs = segs[1:] if internal_block else segs
    if internal_block and (abs(segs[0]["p_start"]) > 1e-6 or abs(segs[0]["p_end"] - cfg_pmin(cfg, uid)) > 1e-6):
        E("内部最小出力块", "参考模式要求保留内部 0~Pmin 块；市场申报段从 Pmin 起，不改变模型段长")
    n = len(market_segs)
    is_sto = typ in ("storage", "pumped")   # 抽蓄量价曲线段数按储能规则（假设：两省未单列抽蓄段数）
    nmin = rules["segments_min"]
    nmax = rules["segments_max"]
    if not (n >= nmin or typ == "hydro_planned") or n > nmax:
        E("段数", f"{curve} 共 {n} 段，要求 {nmin}–{nmax}")
    if abs(segs[0]["p_start"]) > 1e-6:
        E("分段连续", f"{curve} 首段须从 0 开始")
    for a, b in zip(segs[:-1], segs[1:]):
        if abs(a["p_end"] - b["p_start"]) > 1e-6:
            E("分段连续", f"{curve} 段 {a['p_end']}→{b['p_start']} 不连续")
    for s in segs:
        if s["p_end"] <= s["p_start"]:
            E("段长", f"{curve} 段 [{s['p_start']},{s['p_end']}] 长度非正")
    if segs[-1]["p_end"] > cap + 1e-6:
        E("容量", f"{curve} 末段 {segs[-1]['p_end']} 超过装机 {cap}")
    if rules["require_capacity_coverage"] and abs(segs[-1]["p_end"] - cap) > 1e-6:
        E("容量覆盖", f"{curve} 须覆盖到额定容量 {cap} MW")
    # Segment variables are independently bounded in SCUC. The complete internal
    # curve (including 0~Pmin) must stay monotone; a negative market price must not
    # undercut an unchanged zero-price internal block. Never silently rewrite it.
    prices = [s["price"] for s in segs]
    for p in prices:
        if p < lim["bid_min"] - 1e-9 or p > lim["bid_max"] + 1e-9:
            E("申报限价", f"{curve} 价格 {p} 超出 [{lim['bid_min']}, {lim['bid_max']}]")
    sd = (-1 if decreasing else 1) * np.diff(prices)
    word = "递减" if decreasing else "递增"
    if rules["strict_monotonic"]:
        if np.any(sd <= 0):
            E("价格严格单调", f"{curve} 价格须严格{word}：{prices}")
    elif np.any(sd < 0):
        E("价格单调", f"{curve} 价格须单调{word}：{prices}")
    step = rules["price_step"]
    if step:
        for p in prices:
            if abs(p / step - round(p / step)) > 1e-9:
                E("价格步长", f"{curve} 价格 {p} 不是 {step} 元的整数倍")
    mx = rules["max_adjacent_step"]
    if mx:
        start = 1 if typ in THERMAL_TYPES and not internal_block else 0      # 火电首段（0~Pmin）到第 2 段的价差不限
        for d in sd[start:]:
            if d > mx + 1e-9:
                E("相邻段价差", f"{curve} 相邻段价差 {d:.0f} > {mx}")
    frac = rules["min_segment_fraction"]
    basis = cap - cfg_pmin(cfg, uid) if rules["min_segment_basis"] == "dispatchable" else cap
    minimum_width = max(frac * basis, rules["min_segment_mw"])
    if internal_block:
        minimum_width = min(basis, minimum_width)
    for k, s in enumerate(segs):
        if typ in THERMAL_TYPES and k == 0:
            continue                                   # 首段 0~最小技术出力
        if typ == "hydro_planned":
            continue
        if (s["p_end"] - s["p_start"]) < minimum_width - 1e-6:
            E("段长下限", f"{curve} 第{k+1}段 {s['p_end']-s['p_start']:.1f} MW < 最小段宽 {minimum_width:g} MW")
    if typ == "coal" and m.get("coal_first_segment") == "pmin":
        if abs(segs[0]["p_end"] - cfg_pmin(cfg, uid)) > 1e-6:
            E("煤电首段", f"首段须为 0~最小出力 {cfg_pmin(cfg, uid)}")


def cfg_pmin(cfg, uid):
    for u in cfg.units["thermal"]:
        if u["id"] == uid:
            return u["pmin"]
    return 0.0


def validate_bids(cfg: Config, bids: dict, *, require_complete: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    meta = _unit_meta(cfg)
    if not isinstance(bids, dict) or not isinstance(bids.get("units"), dict):
        return [Issue("*", "error", "输入格式", "报价须为包含 units 对象的 JSON 对象")]
    if bids.get("rule_set") != cfg.rule_set:
        issues.append(Issue("*", "error", "规则集", f"报价规则集 {bids.get('rule_set')} 与运行规则集 {cfg.rule_set} 不一致"))
    for uid in bids.get("_filled_default", []):
        issues.append(Issue(uid, "warn", "缺省报价补齐", "文件缺少此资源，已明确使用本配置生成的缺省报价"))
    for uid in meta:
        if uid not in bids["units"]:
            issues.append(Issue(uid, "error" if require_complete else "warn", "缺失报价",
                                "正式建模缺少该资源报价；请先通过读取/算例入口明确补齐" if require_complete else "将使用缺省报价"))
    for uid, b in bids["units"].items():
        if uid not in meta:
            if uid in set(cfg.units.get("_disabled", [])):
                continue
            issues.append(Issue(uid, "error", "未知资源", "配置中无此资源"))
            continue
        mt = meta[uid]
        typ = mt["type"]
        if not isinstance(b, dict):
            issues.append(Issue(uid, "error", "输入格式", "资源报价须为对象")); continue
        structural = _numeric_structure_issues(uid, b, cfg, mt["cap"])
        if structural:
            issues.extend(structural); continue
        mode = b.get("mode", "price_quantity")
        allowed = {"storage": {"price_quantity", "quantity_only", "self_schedule"},
                   "pumped": {"price_quantity", "quantity_only", "dispatch"},
                   "hydro": {"price_quantity", "planned"}}.get(typ, {"price_quantity"})
        if not isinstance(mode, str) or mode not in allowed:
            issues.append(Issue(uid, "error", "申报方式", f"{mode!r} 不在允许方式 {sorted(allowed)} 中")); continue
        if typ == "storage":
            su = next(x for x in cfg.units["storage"] if x["id"] == uid)
            lo, hi = b.get("soc_min", su["soc_min"]), b.get("soc_max", su["soc_max"])
            if not 0 <= lo <= hi <= 1 or lo < su["soc_min"] - 1e-9 or hi > su["soc_max"] + 1e-9:
                issues.append(Issue(uid, "error", "SOC边界", "申报SOC范围须有序且位于设备允许范围内"))
            se = b.get("soc_end")          # 申报的运行日末期望 SOC（川V4.0 7.7.4），可不报
            if se is not None:
                su = [x for x in cfg.units["storage"] if x["id"] == uid][0]
                lo, hi = b.get("soc_min", su["soc_min"]), b.get("soc_max", su["soc_max"])
                if not (lo - 1e-9 <= float(se) <= hi + 1e-9):
                    issues.append(Issue(uid, "error", "期末SOC", f"申报期末 SOC {se} 超出 [{lo}, {hi}]"))
            if mode == "self_schedule":
                sch = b.get("self_schedule")
                if not sch or len(sch) != cfg.T:
                    issues.append(Issue(uid, "error", "自调度曲线", f"需要 {cfg.T} 点 self_schedule"))
                elif max(abs(x) for x in sch) > mt["cap"] + 1e-6:
                    issues.append(Issue(uid, "error", "自调度曲线", "功率超过额定"))
                continue
            if mode == "quantity_only":
                continue
            _check_curve(uid, b.get("dis_segments"), mt["cap"], typ, cfg, issues, "dis_segments")
            _check_curve(uid, b.get("ch_segments"), mt["cap"], typ, cfg, issues, "ch_segments", decreasing=True)
            if b.get("dis_segments") and b.get("ch_segments"):
                if min(s["price"] for s in b["dis_segments"]) <= max(s["price"] for s in b["ch_segments"]):
                    lvl = cfg.mode.get("storage_dis_gt_ch", "error")
                    issues.append(Issue(uid, lvl, "放电首段>充电末段", "放电最低价须高于充电最高价"))
            continue
        if typ == "pumped":                # 规则同储能
            ve = b.get("vup_end")          # 申报日末上库库容（≙ 储能申报期末 SOC），可不报
            if ve is not None:
                pu = [x for x in cfg.units["pumped"] if x["id"] == uid][0]
                lo = max(float(pu["vup_min"]), float(pu["v_total"]) - float(pu["vdn_max"]))
                hi = min(float(pu["vup_max"]), float(pu["v_total"]) - float(pu["vdn_min"]))
                if not (lo - 1e-9 <= float(ve) <= hi + 1e-9):
                    issues.append(Issue(uid, "error", "日末库容", f"申报日末上库库容 {ve} 超出 [{lo}, {hi}]"))
            if mode in ("quantity_only", "dispatch"):
                continue
            _check_curve(uid, b.get("gen_segments"), mt["cap"], typ, cfg, issues, "gen_segments")
            _check_curve(uid, b.get("pump_segments"), mt["cap_pump"], typ, cfg, issues, "pump_segments", decreasing=True)
            if b.get("gen_segments") and b.get("pump_segments"):
                if min(s["price"] for s in b["gen_segments"]) <= max(s["price"] for s in b["pump_segments"]):
                    lvl = cfg.mode.get("storage_dis_gt_ch", "error")
                    issues.append(Issue(uid, lvl, "发电首段>抽水末段", "发电最低价须高于抽水最高价"))
            continue
        t2 = "hydro_planned" if (typ == "hydro" and mode == "planned") else typ
        _check_curve(uid, b.get("segments"), mt["cap"], t2 if t2 == "hydro_planned" else typ, cfg, issues)
        if typ in THERMAL_TYPES:
            su = b.get("startup_cost", {})
            need = set(cfg.mode["startup_states"]) | {"cold"}
            # Inactive legacy dictionaries remain structural input: reject typos
            # without requiring all states for an explicit uniform scalar bid.
            if set(su) - need:
                issues.append(Issue(uid, "error", "启动费状态", f"未知启动状态 {sorted(set(su) - need)}"))
            try:
                startup = resolve_startup_bid(cfg, b)
            except ValueError as exc:
                issues.append(Issue(uid, "error", "启动费", str(exc)))
                continue
            if startup["mode"] == "uniform_per_unit":
                cap_v = mt["startup"].get("hot")
                if not _finite_number(cap_v) or cap_v < 0:
                    issues.append(Issue(uid, "error", "启动费上限", "统一启动费技术hot上限须为非负有限数值"))
                elif startup["cost_yuan"] > cap_v + 1e-6:
                    issues.append(Issue(uid, "error", "启动费上限", f"统一启动费 {startup['cost_yuan']} 超过上限 {cap_v}"))
            else:
                su = b.get("startup_cost", {})
                for k, v in su.items():
                    cap_v = mt["startup"].get(k)
                    if cap_v is not None and v > cap_v + 1e-6:
                        issues.append(Issue(uid, "error", "启动费上限", f"{k} 启动费 {v} 超过核定/上限 {cap_v}"))
                if not need.issubset(su):
                    issues.append(Issue(uid, "error", "启动费状态", f"需申报 {sorted(need)}"))
    return issues


def raise_on_errors(issues: list[Issue]) -> None:
    errs = [i for i in issues if i.level == "error"]
    if errs:
        raise ValueError("报价校验未通过：\n" + "\n".join(map(str, errs)))
