"""把配置、网络、预测曲线和报价组装成一个出清算例（日前）。"""
from __future__ import annotations

from dataclasses import dataclass, field
import copy

import numpy as np
import pandas as pd

from .config import Config
from .network import Network


@dataclass
class DACase:
    cfg: Config
    net: Network
    profiles: pd.DataFrame
    bids: dict
    load_bus: dict          # bus -> np.array(T) 日前短期预测负荷（MW）
    ren_fc: dict            # id -> np.array(T) 日前短期预测（出力上限）
    tie: dict               # id -> (bus, np.array(T))
    R_up: np.ndarray
    R_dn: np.ndarray
    reserve_info: dict
    # ---- 以下用于日内/实时滚动窗口（日前出清全部取默认值）
    T: int | None = None                 # 窗口时段数；None = cfg.T（全天）
    t0: int = 0                          # 窗口首时段在全天中的序号
    init: dict | None = None             # 初始状态：thermal{g:{online,p0,hours}}, storage{s:E0}, hydro{h:V0}, hydro_p{j:实际P0}, hydro_online{j:实际在线台数}, hydro_hist{h:出库流量历史}, pumped{p:{V,ng,np,xg,xp}}
    fixed: dict = field(default_factory=dict)   # 固定整数变量：{"u": {(g,t): 0/1}, "delta": {...}, "u_ch": ..., "u_dis": ...}
    hydro_e_budget: dict | None = None   # 水电剩余日电量上限 MWh；None = 季节 e_day
    terminal: dict | None = None         # None = 日前（申报期末 SOC 硬约束、V≥v_end）；实时：{storage_anchor:{s:[(t,lo,hi)]}, anchor_penalty, fix_power:{Ph/Pd/Pc:{idx:val}}, hydro_vend, hydro_window_end:{h:同钟点DA库容硬等值}}
    stage: str = "DA"


def reserve_requirement(cfg: Config, load_total: np.ndarray, ren_total: np.ndarray, extra_n1: float | None = None):
    """工程备用规则 (12)：R⁺ = ρ_L·D̂ + ρ_RE·P̂_RE + R_ctg；R⁻ = ρ⁻_L·D̂ + ρ⁻_RE·P̂_RE。"""
    r = cfg.mode["reserve"]
    units = [u["pmax"] for u in cfg.units["thermal"]] + [h["unit_pmax"] for h in cfg.units["hydro"]] \
        + [max(u["unit_gen_max"], u["unit_pump"]) for u in cfg.units.get("pumped", [])]   # 抽蓄单机发电/抽水失去同样构成功率缺额
    max_unit = float(max(units))
    n1 = float(cfg.base.get("n1_loss_mw") or 0.0) if extra_n1 is None else extra_n1
    r_ctg = max(n1, max_unit) if r["include_ctg"] else 0.0
    up = r["rho_L"] * load_total + r["rho_RE"] * ren_total + r_ctg
    # An explicitly upward-only research scenario procures no downward reserve.
    dn = (r["rho_L_dn"] * load_total + r["rho_RE_dn"] * ren_total
          if bool(r.get("down_enabled", True)) else np.zeros_like(load_total, dtype=float))
    return up, dn, dict(max_unit=max_unit, n1_loss=n1, R_ctg=r_ctg, **r)


def build_da_case(cfg: Config, net: Network, profiles: pd.DataFrame, bids: dict) -> DACase:
    # Public Python case-building entrypoint must enforce the same rules as file/UI input.
    from .bids import validate_bids, raise_on_errors, fill_missing_bids
    raise_on_errors(validate_bids(cfg, bids))
    bids = fill_missing_bids(cfg, copy.deepcopy(bids))
    raise_on_errors(validate_bids(cfg, bids))
    load_da = profiles["load_da"].to_numpy()
    load_bus = {b: net.load_share[b] * load_da for b in net.buses if net.load_share[b] > 0}
    ren_fc = {}
    for r in cfg.units["renewable"]:
        fc = profiles[f"{r['id']}_da"].to_numpy().copy()
        av = bids["units"].get(r["id"], {}).get("avail_pmax")
        if av:  # 报价中给出的 96 点申报预测优先
            fc = np.asarray(av, float).copy()
        ren_fc[r["id"]] = fc
    tie = {t["id"]: (t["bus"], profiles[f"{t['id']}_sched"].to_numpy()) for t in cfg.units.get("tie", [])}
    ren_total = sum(ren_fc.values())
    up, dn, info = reserve_requirement(cfg, load_da, ren_total)
    return DACase(cfg, net, profiles, bids, load_bus, ren_fc, tie, up, dn, info)
