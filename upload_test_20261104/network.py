"""IEEE39 DC 网络与 PTDF。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config

REF_BUS = 31  # MATPOWER case39 平衡节点（G2/C1 所在母线）


@dataclass
class Network:
    buses: list[int]
    branches: pd.DataFrame       # branch_id, from_bus, to_bus, x_pu, rate_mw(压缩后)
    ptdf: np.ndarray             # (n_branch, n_bus)，参考节点列为 0
    load_share: pd.Series        # 各母线负荷占比（按 case39 节点负荷）
    base_load_mw: float
    ref_bus: int = REF_BUS

    def bus_index(self, bus: int) -> int:
        return self.buses.index(bus)


def compute_ptdf(buses: list[int], br: pd.DataFrame, ref_bus: int) -> np.ndarray:
    n, m = len(buses), len(br)
    idx = {b: i for i, b in enumerate(buses)}
    A = np.zeros((m, n))
    for k, r in enumerate(br.itertuples()):
        A[k, idx[r.from_bus]] = 1.0
        A[k, idx[r.to_bus]] = -1.0
    Bd = np.diag(1.0 / br.x_pu.to_numpy())
    Bbus = A.T @ Bd @ A
    keep = [i for i in range(n) if buses[i] != ref_bus]
    X = np.zeros((n, n))
    X[np.ix_(keep, keep)] = np.linalg.inv(Bbus[np.ix_(keep, keep)])
    # PTDF 以 MW 计：flow = PTDF · inj（标幺基准相同，可直接用 MW）
    return Bd @ A @ X


def build_network(cfg: Config) -> Network:
    d = cfg.root / "data" / "case39"
    from .input_validation import number, _integer, read_input_csv
    br = read_input_csv(d / "branches.csv")
    bus = read_input_csv(d / "buses.csv")
    if not {'bus', 'load_mw'} <= set(bus.columns) or not {'branch_id', 'from_bus', 'to_bus', 'x_pu', 'rate_mw'} <= set(br.columns):
        raise ValueError('网络CSV缺少母线/支路必要列')
    if not bus.columns.is_unique or not br.columns.is_unique:
        raise ValueError('网络CSV列名重复')
    buses = [_integer(value, 'buses.csv bus', positive=True) for value in bus.bus]
    if not buses or len(set(buses)) != len(buses) or REF_BUS not in buses:
        raise ValueError('母线编号须唯一且包含参考母线31')
    loads = [number(value, f'bus {bus_id} load_mw') for bus_id, value in zip(buses, bus.load_mw)]
    if any(value < 0 for value in loads) or sum(loads) <= 0:
        raise ValueError('基准节点负荷须非负且总负荷为正')
    ids = list(br.branch_id)
    if len(ids) != len(set(ids)) or any(not isinstance(uid, str) or not uid.strip() for uid in ids):
        raise ValueError('支路branch_id须为唯一非空字符串')
    for row in br.itertuples():
        source = _integer(row.from_bus, f'{row.branch_id}.from_bus', positive=True)
        target = _integer(row.to_bus, f'{row.branch_id}.to_bus', positive=True)
        if source not in buses or target not in buses or source == target:
            raise ValueError(f'{row.branch_id}支路端点须为两个不同的有效母线')
        if number(row.x_pu, f'{row.branch_id}.x_pu') == 0:
            raise ValueError(f'{row.branch_id}.x_pu不得为0')
        number(row.rate_mw, f'{row.branch_id}.rate_mw', positive=True)
    br["rate_orig_mw"] = br.rate_mw
    for bid, s in (cfg.base.get("line_scale") or {}).items():
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not np.isfinite(s) or s <= 0:
            raise ValueError(f"line_scale {bid} 必须是有限正数")
        if bid not in set(br.branch_id):
            raise KeyError(f"line_scale 中的支路 {bid} 不存在")
        br.loc[br.branch_id == bid, "rate_mw"] = br.loc[br.branch_id == bid, "rate_orig_mw"] * float(s)
    # Overall research-capacity multiplier, applied AFTER individual bottleneck factors.
    global_scale = number(cfg.base.get("line_capacity_multiplier", 1.0),
                          'line_capacity_multiplier', positive=True)
    br["rate_mw"] *= global_scale
    ptdf = compute_ptdf(buses, br, REF_BUS)
    total = bus.load_mw.sum()
    share = pd.Series(bus.load_mw.to_numpy() / total, index=buses)
    return Network(buses=buses, branches=br.reset_index(drop=True), ptdf=ptdf, load_share=share, base_load_mw=float(total))
