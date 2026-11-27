"""Synthetic cascade hydraulic slice of Sichuan V5, not a real river model.

Draft §§4.2–4.3, 7.5.9–10, 8.2.8–9, 9.2.8–9; Annexes 8–10.
Each 15-min slot is modeled at constant average flow. A fixed conversion
factor is a research approximation, separate from a grid-set daily MWh cap.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from numbers import Real


STEP_SECONDS = 900
VOLUME_TOL = 1e-5


@dataclass(frozen=True)
class Reservoir:
    id: str
    storage_min_m3: float
    storage_max_m3: float
    initial_storage_m3: float
    generation_m3s_per_mw: float
    max_spill_m3s: float
    min_release_m3s: float = 0.0
    max_release_m3s: float | None = None
    regulation: str = 'reservoir'
    terminal_min_m3: float | None = None
    terminal_max_m3: float | None = None
    min_bypass_m3s: float = 0.0  # separate mandatory non-turbine release


@dataclass(frozen=True)
class Reach:
    upstream: str
    downstream: str
    travel_quarters: int
    # At day start, explicit volumes already traveling from the preceding day,
    # indexed by arrival quarter (m³, not departure time or a rate).
    initial_arrivals_m3: tuple


@dataclass(frozen=True)
class Cascade:
    reservoirs: tuple
    reaches: tuple
    natural_inflow_m3s: tuple  # absolute-day indexed, per reservoir
    require_all_hydro: bool = False


@dataclass(frozen=True)
class CascadeState:
    period: int  # start of the next period to commit
    volume_m3: Mapping
    in_transit_m3: Mapping  # (upstream, downstream) -> next quarters' arrivals

    @property
    def total_water_m3(self):
        return sum(self.volume_m3.values()) + sum(sum(q) for q in self.in_transit_m3.values())


@dataclass(frozen=True)
class CascadeStep:
    state: CascadeState
    turbine_m3s: Mapping
    spill_m3s: Mapping
    upstream_arrival_m3: Mapping
    downstream_exit_m3: Mapping  # river exits this modeled subgraph


def _number(x, message, *, low=0.0, high=None):
    try:
        valid = (not isinstance(x, bool) and isinstance(x, Real) and isfinite(x)
                 and x >= low and (high is None or x <= high))
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        raise ValueError(message)
    return float(x)


def _rate_map(data, ids, message):
    if not isinstance(data, Mapping) or set(data) != set(ids):
        raise ValueError(message + "：水库键不完整或含未知水库")
    return {r: _number(data[r], message + "：" + r) for r in ids}


def _link(reach):
    return (reach.upstream, reach.downstream)


def validate_cascade(cascade):
    if not isinstance(cascade, Cascade):
        raise ValueError("梯级水力输入类型无效")
    rs = {u.id: u for u in cascade.reservoirs}
    if not rs or len(rs) != len(cascade.reservoirs) or any(
        not isinstance(key, str) or not key.strip() for key in rs
    ):
        raise ValueError("须提供非空且唯一的水库编号")
    for r in rs.values():
        lower = _number(r.storage_min_m3, r.id + " 库容下限无效")
        upper = _number(r.storage_max_m3, r.id + " 库容上限无效", low=lower)
        _number(r.initial_storage_m3, r.id + " 初始库容越界", low=lower, high=upper)
        if r.regulation not in ('run_of_river', 'reservoir'):
            raise ValueError(r.id + ' 水电调节方式无效')
        if r.regulation == 'run_of_river' and lower != upper:
            raise ValueError(r.id + ' 径流式近似须固定库存，逐时水量守恒')
        terminal_min = lower if r.terminal_min_m3 is None else _number(
            r.terminal_min_m3, r.id + ' 日末库容下限无效', low=lower, high=upper)
        if r.terminal_max_m3 is not None:
            _number(r.terminal_max_m3, r.id + ' 日末库容上限无效', low=terminal_min, high=upper)
        factor = _number(r.generation_m3s_per_mw, r.id + " 发电耗水系数无效")
        if factor == 0:
            raise ValueError(r.id + " 发电耗水系数须为正")
        _number(r.max_spill_m3s, r.id + " 最大弃水流量无效")
        min_release = _number(r.min_release_m3s, r.id + " 总出流下限无效")
        mandatory = _number(r.min_bypass_m3s, r.id + ' 专用生态泄放量无效')
        if r.max_release_m3s is not None:
            _number(r.max_release_m3s, r.id + " 总出流上限无效", low=max(min_release, mandatory))
    if not cascade.natural_inflow_m3s:
        raise ValueError("须提供显式分时区间来水")
    for row in cascade.natural_inflow_m3s:
        _rate_map(row, rs, "区间来水无效")
    outgoing = {}
    for z in cascade.reaches:
        if z.upstream not in rs or z.downstream not in rs or z.upstream == z.downstream:
            raise ValueError("水力河段上下游编号无效")
        if z.upstream in outgoing:
            raise ValueError("当前合成梯级不支持同一水库的分流支路")
        outgoing[z.upstream] = z.downstream
        if type(z.travel_quarters) is not int or z.travel_quarters < 1:
            raise ValueError("河段传播时滞须为正整数 15 分钟段")
        if not isinstance(z.initial_arrivals_m3, (tuple, list)) or len(z.initial_arrivals_m3) != z.travel_quarters:
            raise ValueError("须逐段给出期初在途到达水量，不可由零隐式填充")
        for v in z.initial_arrivals_m3:
            _number(v, "期初在途水量无效")
    for start in rs:
        seen = set()
        here = start
        while here in outgoing:
            if here in seen:
                raise ValueError("梯级拓扑不可成环")
            seen.add(here)
            here = outgoing[here]
    return rs


def initial_cascade_state(cascade):
    rs = validate_cascade(cascade)
    return CascadeState(0, {r: v.initial_storage_m3 for r, v in rs.items()},
                        {_link(z): tuple(z.initial_arrivals_m3) for z in cascade.reaches})


def _check_state(cascade, rs, state, begin):
    if not isinstance(state, CascadeState) or type(state.period) is not int or state.period != begin:
        raise ValueError("必须提供本窗口起点的已执行库容及在途水量")
    if set(state.volume_m3) != set(rs) or set(state.in_transit_m3) != {_link(z) for z in cascade.reaches}:
        raise ValueError("期初水力状态与水库或河段不一致")
    for r, v in rs.items():
        _number(state.volume_m3[r], r + " 执行期初库容无效",
                low=v.storage_min_m3 - VOLUME_TOL, high=v.storage_max_m3 + VOLUME_TOL)
    for z in cascade.reaches:
        values = state.in_transit_m3[_link(z)]
        if len(values) != z.travel_quarters:
            raise ValueError("执行期初在途队列长度不等于传播时滞")
        for v in values:
            _number(v, "执行期初在途水量无效")


def advance_cascade(cascade, state, power_mw, spill_m3s):
    """Commit exactly one executed period. Never carry a predicted tail.

    The caller must supply the actual spill selected in this solve, or externally
    observed actual spill; turbine flow follows the explicit synthetic factor.
    """
    rs = validate_cascade(cascade)
    if not isinstance(state, CascadeState) or not 0 <= state.period < len(cascade.natural_inflow_m3s):
        raise ValueError("已执行水力状态超出时域")
    _check_state(cascade, rs, state, state.period)
    power = _rate_map(power_mw, rs, "发电功率无效")
    spill = _rate_map(spill_m3s, rs, "弃水流量无效")
    inflow = cascade.natural_inflow_m3s[state.period]
    turbine = {r: power[r] * rs[r].generation_m3s_per_mw for r in rs}
    release = {r: turbine[r] + rs[r].min_bypass_m3s + spill[r] for r in rs}
    arrival = dict.fromkeys(rs, 0.0)
    queue = {}
    for z in cascade.reaches:
        k = _link(z)
        pending = state.in_transit_m3[k]
        arrival[z.downstream] += pending[0]
        queue[k] = tuple(pending[1:]) + (STEP_SECONDS * release[z.upstream],)
    volume = {}
    for r, v in rs.items():
        _number(spill[r], r + " 弃水上限越界", high=v.max_spill_m3s + VOLUME_TOL)
        _number(release[r], r + " 水库总出流约束不满足", low=v.min_release_m3s - VOLUME_TOL,
                high=None if v.max_release_m3s is None else v.max_release_m3s + VOLUME_TOL)
        volume[r] = state.volume_m3[r] + STEP_SECONDS * (inflow[r] - release[r]) + arrival[r]
        _number(volume[r], r + " 期末库容超限，不能提交此水力状态",
                low=v.storage_min_m3 - VOLUME_TOL, high=v.storage_max_m3 + VOLUME_TOL)
        if state.period + 1 == len(cascade.natural_inflow_m3s):
            lower = v.storage_min_m3 if v.terminal_min_m3 is None else v.terminal_min_m3
            upper = v.storage_max_m3 if v.terminal_max_m3 is None else v.terminal_max_m3
            _number(volume[r], r + ' 日末库存越界',
                    low=lower - VOLUME_TOL, high=upper + VOLUME_TOL)
    exits = set(rs) - {z.upstream for z in cascade.reaches}
    return CascadeStep(CascadeState(state.period + 1, volume, queue), turbine, spill, arrival,
                       {r: STEP_SECONDS * release[r] for r in sorted(exits)})


def attach_cascade_constraints(model, cascade, begin, stop, power_at, *, state=None):
    """Add linear stocks, flows and exact delay, on absolute range [begin, stop).

    `power_at(id, quarter)` returns the caller's non-negative MW variable.
    At any window whose start is nonzero, the supplied carry must come from the
    actually executed periods. Forecast inflow outside this horizon never enters
    a constraint. In-transit releases at its far edge stay in transit.
    """
    import pyomo.environ as p

    rs = validate_cascade(cascade)
    if not all(type(x) is int for x in (begin, stop)) or not 0 <= begin < stop <= len(cascade.natural_inflow_m3s):
        raise ValueError("水力调度窗口无效")
    if state is None:
        if begin != 0:
            raise ValueError("滚动窗口不能沿用零时初值")
        state = initial_cascade_state(cascade)
    _check_state(cascade, rs, state, begin)
    times = range(begin, stop)
    model.cascade_volume = p.Var(tuple(rs), times, bounds=lambda _, r, t: (
        rs[r].storage_min_m3, rs[r].storage_max_m3))
    model.cascade_spill = p.Var(tuple(rs), times, bounds=lambda _, r, t: (0.0, rs[r].max_spill_m3s))
    model.cascade_turbine = p.Expression(tuple(rs), times,
        rule=lambda _, r, t: rs[r].generation_m3s_per_mw * power_at(r, t))
    model.cascade_release = p.Expression(tuple(rs), times,
        rule=lambda m, r, t: m.cascade_turbine[r, t] + rs[r].min_bypass_m3s + m.cascade_spill[r, t])

    inbound = {r: [z for z in cascade.reaches if z.downstream == r] for r in rs}

    def arrival_rule(m, r, t):
        return sum(state.in_transit_m3[_link(z)][t - begin]
                   if t - z.travel_quarters < begin
                   else STEP_SECONDS * m.cascade_release[z.upstream, t - z.travel_quarters]
                   for z in inbound[r])

    model.cascade_arrival = p.Expression(tuple(rs), times, rule=arrival_rule)
    model.cascade_natural = p.Param(tuple(rs), times,
        initialize=lambda _, r, t: cascade.natural_inflow_m3s[t][r])

    def balance(m, r, t):
        prior = state.volume_m3[r] if t == begin else m.cascade_volume[r, t - 1]
        return m.cascade_volume[r, t] == prior + m.cascade_arrival[r, t] + STEP_SECONDS * (
            m.cascade_natural[r, t] - m.cascade_release[r, t])

    model.cascade_balance = p.Constraint(tuple(rs), times, rule=balance)
    model.cascade_release_bounds = p.Constraint(tuple(rs), times,
        rule=lambda m, r, t: p.inequality(rs[r].min_release_m3s, m.cascade_release[r, t],
                                         rs[r].max_release_m3s))
    if stop == len(cascade.natural_inflow_m3s):
        model.cascade_terminal = p.ConstraintList()
        for r, station in rs.items():
            final = model.cascade_volume[r, stop - 1]
            if station.terminal_min_m3 is not None:
                model.cascade_terminal.add(final >= station.terminal_min_m3)
            if station.terminal_max_m3 is not None:
                model.cascade_terminal.add(final <= station.terminal_max_m3)
