"""报价：缺省报价生成、JSON/CSV 读写（带版本号）、按省规则校验（方案第 5 节）。

报价价格单位 元/MWh，量单位 MW。缺省报价数值均为“假设值”。
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
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


def _thermal_default(u, rs, lim):
    pmin, pmax = u["pmin"], u["pmax"]
    if rs == "SC":
        n_rest = 5  # A2(煤电/燃气)=20%：剩余区间分 5 段，每段约 11% 装机
        base = {"coal": 300, "gas_ccgt": 520, "gas_gt": 650}[u["type"]]
        step = {"coal": [0, 20, 45, 75, 110], "gas_ccgt": [0, 20, 40, 60, 80], "gas_gt": [0, 20, 40, 70, 100]}[u["type"]]
        rest_prices = [base + s for s in step]
        first_price = lim["bid_min"] if u["type"] == "coal" else rest_prices[0] - 100
    else:
        n_rest = 3  # 每段 ≥10% 装机
        base = {"coal": 300, "gas_ccgt": 440, "gas_gt": 480}[u["type"]]
        step = {"coal": [0, 30, 60], "gas_ccgt": [0, 20, 40], "gas_gt": [0, 10, 20]}[u["type"]]
        rest_prices = [base + s for s in step]
        first_price = lim["bid_min"] if u["type"] == "coal" else rest_prices[0] - 50
    bounds = [0.0, pmin] + list(np.linspace(pmin, pmax, n_rest + 1)[1:])
    return dict(mode="price_quantity", segments=_segments(bounds, [first_price] + rest_prices),
                startup_cost=dict(u["startup"]), noload_cost=float(u["noload"]))


def default_bids(cfg: Config, version: int = 1) -> dict:
    rs, lim = cfg.rule_set, cfg.mode["price_limits"]
    units = {}
    for u in cfg.units["thermal"]:
        b = _thermal_default(u, rs, lim)
        if rs == "SC":
            b["startup_cost"].pop("very_hot", None)  # 四川三态
        units[u["id"]] = b
    for r in cfg.units["renewable"]:
        cap = r["cap"]
        if rs == "SC":
            bounds, prices = np.linspace(0, cap, 6), [0, 20, 40, 60, 80]
        else:
            bounds, prices = np.linspace(0, cap, 4), [40, 50, 60]
        units[r["id"]] = dict(mode="price_quantity", segments=_segments(bounds, prices), avail_pmax=None)
    for h in cfg.units["hydro"]:
        cap = h["n_units"] * h["unit_pmax"]
        if rs == "SC":
            units[h["id"]] = dict(mode="price_quantity", hydro_bid=dict(method="segments", season=cfg.base["season"]),
                                  segments=_segments(np.linspace(0, cap, 6), [100, 130, 160, 200, 240]))
        else:
            units[h["id"]] = dict(mode="planned", hydro_bid=dict(method="planned", season=cfg.base["season"]),
                                  segments=_segments([0, cap], [lim["bid_min"]]))
    for s in cfg.units["storage"]:
        n = 3
        bnd = np.linspace(0, s["pmax"], n + 1)
        if rs == "SC":
            dis, ch = [420, 450, 480], [300, 280, 260]
        else:
            dis, ch = [420, 440, 460], [200, 180, 160]
        units[s["id"]] = dict(mode="price_quantity", dis_segments=_segments(bnd, dis), ch_segments=_segments(bnd, ch),
                              soc_min=s["soc_min"], soc_max=s["soc_max"], soc_end=s["soc_end"], self_schedule=None)
    return dict(date=cfg.base["date"], rule_set=rs, version=version,
                created=_dt.datetime.now().isoformat(timespec="seconds"), note="缺省报价（假设值）", units=units)


# ----------------------------------------------------------------------------- 读写
def bid_dir(cfg: Config) -> Path:
    return cfg.root / "bids" / cfg.base["date"] / cfg.rule_set


def save_bids(cfg: Config, bids: dict, note: str | None = None, new_version: bool = True) -> Path:
    d = bid_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    bids = copy.deepcopy(bids)
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
    return json.loads(p.read_text(encoding="utf-8"))


def bids_to_csv(bids: dict, path: Path) -> None:
    rows = []
    for uid, b in bids["units"].items():
        for kind in ("segments", "dis_segments", "ch_segments"):
            for k, s in enumerate(b.get(kind) or [], start=1):
                rows.append(dict(unit_id=uid, mode=b.get("mode"), curve=kind, seg=k, **s))
    pd.DataFrame(rows).to_csv(path, index=False)


def bids_from_csv(base: dict, path: Path) -> dict:
    """用 CSV 中的分段覆盖 base 报价（批量编辑用），返回新报价（版本号由 save_bids 递增）。"""
    df = pd.read_csv(path)
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
    return meta


def _check_curve(uid, segs, cap, typ, cfg, issues, curve="segments", decreasing=False):
    rs, m, lim = cfg.rule_set, cfg.mode, cfg.mode["price_limits"]
    E = lambda rule, msg, lvl="error": issues.append(Issue(uid, lvl, rule, msg))
    if not segs:
        E("段数", f"{curve} 为空")
        return
    n = len(segs)
    is_sto = typ == "storage"
    nmin = m["segments"]["storage_min" if is_sto else "min"]
    nmax = m["segments"]["storage_max" if is_sto else "max"]
    # 计划/价格接受的水电不受段数下限约束
    if not (n >= nmin or typ == "hydro_planned") or n > nmax:
        E("段数", f"{curve} 共 {n} 段，要求 {nmin}–{nmax}")
    # 连续性与容量
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
    prices = [s["price"] for s in segs]
    for p in prices:
        if p < lim["bid_min"] - 1e-9 or p > lim["bid_max"] + 1e-9:
            E("申报限价", f"{curve} 价格 {p} 超出 [{lim['bid_min']}, {lim['bid_max']}]")
    diffs = np.diff(prices)
    sign = -1 if decreasing else 1
    if rs == "SC":
        if np.any(sign * diffs <= 0):
            E("价格严格单调", f"{curve} 价格须严格{'递减' if decreasing else '递增'}：{prices}")
        start = 1 if typ == "coal" else 0
        if typ == "coal" and n >= 2 and prices[1] - prices[0] < m["beta0"]:
            E("β0", f"第2段−首段 = {prices[1]-prices[0]} < β0={m['beta0']}")
        for d in (sign * diffs)[start:]:
            if d > 0 and (d < m["beta1"] or d > m["beta2"]):
                E("β1/β2", f"{curve} 相邻段价差 {d:.0f} 不在 [{m['beta1']}, {m['beta2']}]")
        a1 = 0.10 if is_sto else m["seg_len_A1"]
        a2 = m["seg_len_A2"].get("storage" if is_sto else typ, 1.0)
        for k, s in enumerate(segs):
            if typ in THERMAL_TYPES and k == 0:
                continue  # 首段 0~最小可调出力（燃气同样处理，四川原规则燃气为计划机组，假设做法）
            L = (s["p_end"] - s["p_start"]) / cap
            if L < a1 - 1e-9 or L > a2 + 1e-9:
                E("段长 A1/A2", f"{curve} 第{k+1}段长度 {L:.1%} 装机，不在 [{a1:.0%}, {a2:.0%}]")
    else:  # GS
        if np.any(sign * diffs < 0):
            E("价格单调", f"{curve} 价格须单调非{'增' if decreasing else '减'}：{prices}")
        step = m["price_step"]
        for p in prices:
            if abs(p / step - round(p / step)) > 1e-9:
                E("价格步长", f"{curve} 价格 {p} 不是 {step} 元的整数倍")
        for k, s in enumerate(segs):
            if typ == "coal" and k == 0:
                continue
            if (s["p_end"] - s["p_start"]) < m["min_seg_frac"] * cap - 1e-6:
                E("每段≥10%装机", f"{curve} 第{k+1}段 {s['p_end']-s['p_start']:.1f} MW < {m['min_seg_frac']:.0%}×{cap}")
    if typ == "coal":
        if abs(segs[0]["p_end"] - cfg_pmin(cfg, uid)) > 1e-6:
            E("煤电首段", f"首段须为 0~最小出力 {cfg_pmin(cfg, uid)}")
        if rs == "SC" and abs(prices[0] - lim["bid_min"]) > 1e-9:
            E("煤电首段", f"四川首段须按下限价 {lim['bid_min']} 报价")


def cfg_pmin(cfg, uid):
    for u in cfg.units["thermal"]:
        if u["id"] == uid:
            return u["pmin"]
    return 0.0


def validate_bids(cfg: Config, bids: dict) -> list[Issue]:
    issues: list[Issue] = []
    meta = _unit_meta(cfg)
    if bids.get("rule_set") != cfg.rule_set:
        issues.append(Issue("*", "error", "规则集", f"报价规则集 {bids.get('rule_set')} 与运行规则集 {cfg.rule_set} 不一致"))
    for uid in meta:
        if uid not in bids["units"]:
            issues.append(Issue(uid, "warn", "缺失报价", "将使用缺省报价"))
    for uid, b in bids["units"].items():
        if uid not in meta:
            issues.append(Issue(uid, "error", "未知资源", "配置中无此资源"))
            continue
        mt = meta[uid]
        typ = mt["type"]
        mode = b.get("mode", "price_quantity")
        if typ == "storage":
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
                    lvl = "error" if cfg.rule_set == "SC" else "warn"
                    issues.append(Issue(uid, lvl, "放电首段>充电末段", "放电最低价须高于充电最高价"))
            continue
        t2 = "hydro_planned" if (typ == "hydro" and mode == "planned") else typ
        _check_curve(uid, b.get("segments"), mt["cap"], t2 if t2 == "hydro_planned" else typ, cfg, issues)
        if typ in THERMAL_TYPES:
            su = b.get("startup_cost", {})
            for k, v in su.items():
                cap_v = mt["startup"].get(k)
                if cap_v is not None and v > cap_v + 1e-6:
                    issues.append(Issue(uid, "error", "启动费上限", f"{k} 启动费 {v} 超过核定/上限 {cap_v}"))
            need = set(cfg.mode["startup_states"]) | {"cold"}
            if not need.issubset(su):
                issues.append(Issue(uid, "error", "启动费状态", f"需申报 {sorted(need)}"))
    return issues


def raise_on_errors(issues: list[Issue]) -> None:
    errs = [i for i in issues if i.level == "error"]
    if errs:
        raise ValueError("报价校验未通过：\n" + "\n".join(map(str, errs)))
