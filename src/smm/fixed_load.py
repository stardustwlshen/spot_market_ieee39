"""Versioned conversion of external injections to distributed fixed demand.

Missing mode is historical supply. Reading/building never rewrites inputs.
"""
from __future__ import annotations
import numpy as np

STAGES = ('actual', 'da', 'id', 'rt')


def tie_mode(cfg):
    mode = cfg.base.get('tie_line_mode', 'supply')
    if mode not in ('supply', 'fixed_load'):
        raise ValueError('tie_line_mode须为supply（历史来电）或fixed_load（按基准占比分配固定负荷）')
    if mode == "fixed_load" and cfg.base.get("tie_load_allocation", "base_load_share") != "base_load_share":
        raise ValueError("tie_load_allocation须为base_load_share（冻结基准负荷占比）")
    return mode


def tie_profiles(cfg, profiles, unit):
    """All four explicit stages or one historical schedule; no partial fallback."""
    uid = unit['id']
    cols = [f'{uid}_{s}' for s in STAGES]
    present = [c in profiles for c in cols]
    if any(present) and not all(present):
        raise ValueError(f'{uid}固定负荷须同时提供actual/da/id/rt四套曲线')
    source = cols if all(present) else [f'{uid}_sched'] * 4
    out = {}
    for stage, col in zip(STAGES, source):
        if col not in profiles:
            raise ValueError(f'固定负荷缺少{col}')
        value = profiles[col].to_numpy(dtype=float).copy()
        if len(value) != cfg.T or not np.isfinite(value).all() or (value < 0).any():
            raise ValueError(f'{col}须为{cfg.T}段有限非负固定负荷')
        out[f'{uid}_{stage}'] = value
    return out


def nodal_demand(cfg, net, profiles, distributed, *, stage='da', periods=None,
                 issue_t=None, first_actual=False):
    """Keep source curves independent; distribute both by frozen baseline weights."""
    load = {b: net.load_share[b] * np.asarray(distributed, float)
            for b in net.buses if net.load_share[b] > 0}
    fixed = {}
    if tie_mode(cfg) == 'fixed_load':
        from .forecast import forecast_view
        import pandas as pd
        for unit in cfg.units.get('tie', []):
            uid = unit['id']
            frame = pd.DataFrame(tie_profiles(cfg, profiles, unit))
            values = (frame[f'{uid}_{stage}'].to_numpy().copy() if issue_t is None else
                      forecast_view(frame, uid, issue_t, periods, first_actual))
            weights = allocation_weights(net)
            for bus, weight in weights.items():
                allocated = values * weight
                fixed[f"{uid}@{bus}"] = (bus, allocated)
                load[bus] = load.get(bus, np.zeros_like(values)) + allocated
    return load, fixed


def allocation_weights(net):
    weights = {int(b): float(net.load_share[b]) for b in net.buses if net.load_share[b] > 0}
    if not weights or not all(np.isfinite(v) and v >= 0 for v in net.load_share) or not np.isclose(sum(weights.values()), 1., atol=1e-12, rtol=0.):
        raise ValueError('固定负荷分配须有非负有限且总和为1的冻结基准负荷占比')
    return weights


def allocation_evidence(case):
    """Serializable source and allocation evidence, including all four input stages."""
    cfg = case.cfg
    mode = tie_mode(cfg)
    result = dict(mode=mode, demand_response=False, supply_settlement=(mode == 'supply'))
    if mode == 'fixed_load':
        result.update(allocation='base_load_share', weights=allocation_weights(case.net),
                      weights_source='frozen data/case39/buses.csv baseline load_mw',
                      settlement='aggregate node load payment after actual shed; no TIE supply entity',
                      sources=[dict(id=u['id'], original_bus=u['bus'],
                                    original_schedule_mw=(case.profiles[f"{u['id']}_sched"].to_numpy(float).tolist() if f"{u['id']}_sched" in case.profiles else None),
                                    input_columns=('explicit_four_stages' if f"{u['id']}_actual" in case.profiles else 'historical_sched_repeated'),
                                    curves_mw={k:v.tolist() for k,v in tie_profiles(cfg, case.profiles, u).items()})
                               for u in cfg.units.get('tie', [])])
    return result
