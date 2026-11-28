"""Independent lossless IEEE39 research core, no Guangdong pricing/AGC defaults."""
from dataclasses import dataclass
from math import isfinite
if __package__:
    from .sichuan_v5_hydro import STEP_SECONDS, Cascade, advance_cascade, attach_cascade_constraints, initial_cascade_state, validate_cascade
    from .sichuan_v5_intraday import amended_case
else:
    from sichuan_v5_hydro import STEP_SECONDS, Cascade, advance_cascade, attach_cascade_constraints, initial_cascade_state, validate_cascade
    from sichuan_v5_intraday import amended_case

DT = .25
PERIODS = 96
WINDOW = 8


@dataclass(frozen=True)
class Line:
    id: str
    source: int
    target: int
    reactance: float
    limit_mw: float


@dataclass(frozen=True)
class Resource:
    id: str
    bus: int
    role: str    # Settlement role, separate from physical technology.
    kind: str    # thermal, renewable, hydro, fixed, storage
    capacity_mw: float
    offer_yuan_mwh: float
    minimum_mw: float = 0.
    ramp_mw_15min: float | None = None
    initial_mw: float = 0.
    min_up_quarters: int = 1
    min_down_quarters: int = 1
    initial_state_age_quarters: int = PERIODS
    startup_yuan: float = 0.
    daily_water_budget_mwh: float | None = None
    energy_mwh: float | None = None
    initial_energy_mwh: float = 0.
    charge_efficiency: float = .9
    discharge_efficiency: float = .9
    segments: tuple = () # (upper segment width MW, energy offer yuan/MWh)
    online_cost_yuan_hour: float = 0.


@dataclass(frozen=True)
class Case:
    buses: tuple
    lines: tuple
    resources: tuple
    demand: tuple         # absolute-day indexed {node: MW}
    available: tuple      # absolute-day indexed {resource: MW}; omit dispatchable limit
    storage_plan: tuple = () # scheduled signed MW; positive discharge; zero if not spot
    reserve_mw: float = 0. # explicit research reliability requirement, not a service award
    unmet_load_yuan_mwh: float = 10000. # synthetic reliability proxy, not a market cap
    cascade: Cascade | None = None
    fixed_schedule: tuple = ()  # absolute-day indexed non-market injections (MW)
    deliverable_up_mw: float = 0. # D-1 forecast exposure distributed by nodal load


class ClearingError(ValueError):
    pass


def _num(x, low=0., high=None):
    return (type(x) in (int, float) and isfinite(x) and x >= low
            and (high is None or x <= high))


def validate(case):
    buses = set(case.buses)
    if not buses or len(buses) != len(case.buses):
        raise ClearingError('节点必须唯一且非空')
    if len(case.demand) != PERIODS or len(case.available) != PERIODS:
        raise ClearingError('场景必须包含96个完整15分钟段')
    if (not _num(case.reserve_mw) or not _num(case.unmet_load_yuan_mwh, 1.)
            or not _num(case.deliverable_up_mw, 0., case.reserve_mw)):
        raise ClearingError('可靠性设定无效')
    line_ids = {l.id for l in case.lines}
    if len(line_ids) != len(case.lines):
        raise ClearingError('支路编号重复')
    adj = {b: set() for b in buses}
    for l in case.lines:
        if l.source not in buses or l.target not in buses or l.source == l.target or not _num(l.reactance, 1e-12) or not _num(l.limit_mw, 1e-9):
            raise ClearingError('支路无效：' + l.id)
        adj[l.source].add(l.target)
        adj[l.target].add(l.source)
    seen = set()
    pending = [case.buses[0]]
    while pending:
        b = pending.pop()
        if b not in seen:
            seen.add(b)
            pending.extend(adj[b] - seen)
    if seen != buses:
        raise ClearingError('仅支持连通的直流网络')
    rs = {r.id: r for r in case.resources}
    if not rs or len(rs) != len(case.resources):
        raise ClearingError('资源必须唯一且非空')
    if case.cascade is not None:
        validate_cascade(case.cascade)
        hydro={r.id for r in case.resources if r.kind=='hydro'}
        hydraulic_ids={r.id for r in case.cascade.reservoirs}
        if (len(case.cascade.natural_inflow_m3s)!=PERIODS
                or not hydraulic_ids<=hydro or case.cascade.require_all_hydro and hydraulic_ids!=hydro):
            raise ClearingError('合成梯级水库与水电资源/96段边界不匹配')
    for r in case.resources:
        if (r.bus not in buses or r.kind not in ('thermal','renewable','hydro','fixed','storage')
            or not _num(r.capacity_mw, 1e-9) or not _num(r.offer_yuan_mwh, -case.unmet_load_yuan_mwh, case.unmet_load_yuan_mwh)
            or not _num(r.minimum_mw, 0., r.capacity_mw) or not _num(r.initial_mw, 0., r.capacity_mw)
            or r.ramp_mw_15min is not None and not _num(r.ramp_mw_15min, 1e-9)
            or not _num(r.startup_yuan)
            or not _num(r.online_cost_yuan_hour)
            or r.kind!='thermal' and r.online_cost_yuan_hour!=0.
            or not all(type(x) is int and x >= 1 for x in (r.min_up_quarters, r.min_down_quarters, r.initial_state_age_quarters))):
            raise ClearingError('资源参数无效：'+r.id)
        if r.kind == 'storage' and (not _num(r.energy_mwh, 1e-9)
            or not _num(r.initial_energy_mwh, 0., r.energy_mwh)
            or not _num(r.charge_efficiency, 1e-6, 1.)
            or not _num(r.discharge_efficiency, 1e-6, 1.)):
            raise ClearingError('储能能量参数无效：'+r.id)
        if r.daily_water_budget_mwh is not None and (r.kind != 'hydro' or not _num(r.daily_water_budget_mwh)):
            raise ClearingError('水量约束参数无效：'+r.id)
    if case.storage_plan and len(case.storage_plan) != PERIODS:
        raise ClearingError('储能申报计划必须包含96段')
    storage = {r.id:r for r in case.resources if r.kind == 'storage'}
    for t, schedule in enumerate(case.storage_plan):
        if set(schedule) != set(storage) or any(not _num(v, -storage[r].capacity_mw, storage[r].capacity_mw) for r,v in schedule.items()):
            raise ClearingError(f'储能申报计划缺项或功率越界：时段 {t+1}')
    if storage and not case.storage_plan:
        raise ClearingError('独立储能需要明示充放电计划，不能暗中按价格优化')
    for r in case.resources:
        if r.segments:
            current = r.offer_yuan_mwh
            if abs(sum(width for width,_ in r.segments)-r.capacity_mw)>1e-6:
                raise ClearingError('分段报价容量未覆盖装机：'+r.id)
            for width, price in r.segments:
                if not _num(width, 1e-9) or not _num(price, current, 800.):
                    raise ClearingError('分段报价无效：'+r.id)
                current=price
    for d, a in zip(case.demand, case.available):
        if set(d) != buses or not all(_num(v) for v in d.values()):
            raise ClearingError('节点负荷缺项或无效')
        if any(x not in rs or not _num(v, 0., rs[x].capacity_mw) for x, v in a.items()):
            raise ClearingError('资源可用功率越界')
        if case.cascade is not None and case.cascade.require_all_hydro and set(a)!=set(rs):
            raise ClearingError('完整梯级场景每时段必须显式列出每个电源的可用容量')
        if any(r.id not in a for r in case.resources if r.kind in ('renewable','fixed')):
            raise ClearingError('新能源预测或固定计划缺项')
    if case.fixed_schedule:
        fixed={r.id for r in case.resources if r.kind=='fixed'}
        if len(case.fixed_schedule)!=PERIODS:
            raise ClearingError('非市场计划须有96段')
        for t, row in enumerate(case.fixed_schedule):
            if set(row)!=fixed or any(not _num(v, 0., case.available[t][r]) for r,v in row.items()):
                raise ClearingError(f'非市场计划缺项或超过可用容量：时段 {t+1}')
    elif case.cascade is not None and case.cascade.require_all_hydro and any(r.kind=='fixed' for r in case.resources):
        raise ClearingError('完整梯级场景须分别提供非市场计划和可用容量')


def _delivery_test(model, case, thermal, times):
    """Require simultaneous D-1 forecast reserve activation on the same DC network."""
    import numpy as np
    import pyomo.environ as p
    buses = {b:i for i,b in enumerate(case.buses)}
    positions = {u.id:buses[u.bus] for u in case.resources if u.id in thermal}
    line_ids = [l.id for l in case.lines]
    incidence = np.zeros((len(case.lines), len(buses)))
    for i, edge in enumerate(case.lines):
        incidence[i, buses[edge.source]] = 1.
        incidence[i, buses[edge.target]] = -1.
    bline = np.array([1./l.reactance for l in case.lines])
    weighted = bline[:,None] * incidence
    shift = weighted @ np.linalg.pinv(incidence.T @ weighted)
    model.forecast_activation = p.Var(thermal, times, domain=p.NonNegativeReals)
    model.forecast_security = p.ConstraintList()
    model.forecast_stress_flow = p.Expression(line_ids, times)
    for t in times:
        load = np.array([case.demand[t][b] for b in case.buses])
        if load.sum() <= 0.:
            raise ClearingError('负荷为零，无法按节点负荷分摊日前预测裕度')
        sink = shift @ (load/load.sum())
        quantity = sum(model.forecast_activation[r,t] for r in thermal)
        # Any shortfall remains visible as the existing reliability slack.
        model.forecast_security.add(quantity+model.reserve_short[t]>=case.deliverable_up_mw)
        if thermal:
            model.forecast_security.add(quantity<=case.deliverable_up_mw)
        for r in thermal:
            model.forecast_security.add(model.forecast_activation[r,t]<=model.reserve[r,t])
        for i, edge in enumerate(case.lines):
            flow = model.flow[edge.id,t] + sum(float(shift[i,positions[r]]-sink[i]) *
                                                model.forecast_activation[r,t] for r in thermal)
            model.forecast_stress_flow[edge.id,t] = flow
            model.forecast_security.add(p.inequality(-edge.limit_mw, flow, edge.limit_mw))


def _build(case, begin, stop, *, carry=None, da_mode=None, holds=(), spot_disabled=()):
    import pyomo.environ as p
    rs = {r.id: r for r in case.resources}
    ls = {l.id: l for l in case.lines}
    # Pyomo can normalize dict index arguments in hash-dependent order. Stable
    # columns and constraints prevent intertemporal equal-cost hydro schedules
    # from changing between processes and shifting RT's remaining-water limits.
    resource_ids, line_ids = tuple(rs), tuple(ls)
    th = tuple(sorted(r for r in rs if rs[r].kind == 'thermal'))
    st = tuple(sorted(r for r in rs if rs[r].kind == 'storage'))
    hydro = {r for r in rs if rs[r].daily_water_budget_mwh is not None}
    times = range(begin, stop)
    by_bus = {b: [r for r,v in rs.items() if v.bus == b] for b in case.buses}
    m = p.ConcreteModel()
    m.ancillary_holds=holds or ({},)*PERIODS
    segments={(r,i):(width,price) for r in rs for i,(width,price) in enumerate(rs[r].segments)}
    m.segment = p.Var(tuple(segments),times,bounds=lambda _,r,i,t: (0,segments[r,i][0]))
    m.power = p.Var(resource_ids, times, domain=p.NonNegativeReals)
    m.charge = p.Var(st, times, domain=p.NonNegativeReals)
    m.energy = p.Var(st, times, domain=p.NonNegativeReals)
    m.mode = p.Var(st, times, domain=p.Binary)
    m.online = p.Var(th, times, domain=p.Binary)
    m.started = p.Var(th, times, domain=p.Binary)
    m.stopped = p.Var(th, times, domain=p.Binary)
    m.reserve = p.Var(th, times, domain=p.NonNegativeReals)
    m.shed = p.Var(case.buses, times, domain=p.NonNegativeReals)
    m.spill = p.Var(case.buses, times, domain=p.NonNegativeReals)
    m.reserve_short = p.Var(times, domain=p.NonNegativeReals)
    m.angle = p.Var(case.buses, times)
    m.flow = p.Var(line_ids, times, bounds=lambda _, l, t: (-ls[l].limit_mw, ls[l].limit_mw))
    m.eq_flow = p.Constraint(line_ids,times, rule=lambda _,l,t:
       m.flow[l,t] == 100/ls[l].reactance*(m.angle[ls[l].source,t]-m.angle[ls[l].target,t]))
    out = {b:[l for l,v in ls.items() if v.source == b] for b in case.buses}
    into = {b:[l for l,v in ls.items() if v.target == b] for b in case.buses}
    m.balance = p.Constraint(case.buses,times,rule=lambda _,b,t:
        sum(m.power[r,t] for r in by_bus[b])-sum(m.charge[r,t] for r in by_bus[b] if r in st)
        +m.shed[b,t]-m.spill[b,t]-sum(m.flow[l,t] for l in out[b])
        +sum(m.flow[l,t] for l in into[b]) == case.demand[t][b])
    m.phys = p.ConstraintList()
    if case.deliverable_up_mw:
        _delivery_test(m, case, th, times)
    for t in times:
        m.angle[case.buses[0],t].fix(0.)
        for b in case.buses:
            m.shed[b,t].setub(case.demand[t][b])
        m.phys.add(sum(m.reserve[r,t] for r in th)+m.reserve_short[t] >= case.reserve_mw)
        for r,v in rs.items():
            g=m.power[r,t]
            cap=case.available[t].get(r,v.capacity_mw)
            g.setub(cap)
            if v.segments:
                m.phys.add(g == sum(m.segment[r,i,t] for i in range(len(v.segments))))
            if v.kind == 'fixed':
                g.fix(case.fixed_schedule[t][r] if case.fixed_schedule else cap)
            if r in th:
                u,y,z=m.online[r,t],m.started[r,t],m.stopped[r,t]
                initial = int(v.initial_mw > 0) if carry is None else carry['online'][r]
                prev_u=initial if t == begin else m.online[r,t-1]
                m.phys.add(u-prev_u == y-z)
                m.phys.add(y+z <= 1)
                age=v.initial_state_age_quarters if carry is None else carry['online_age'][r]
                remaining=max(0,(v.min_up_quarters if initial else v.min_down_quarters)-age)
                if da_mode is not None:
                    wanted=da_mode[t]['online'][r]
                    if t-begin<remaining and wanted!=initial:
                        raise ClearingError(f'机组 {r} 在时段 {t+1} 的参考组合违反已执行启停时长')
                    u.fix(wanted)
                elif t-begin<remaining:
                    u.fix(initial)
                m.phys.add(g >= v.minimum_mw*u)
                up,down = holds[t].get(r, (0.,0.)) if holds else (0.,0.)
                m.phys.add(g+m.reserve[r,t] <= cap*u-up)
                m.phys.add(g >= v.minimum_mw*u+down)
                # When the opposite state's minimum duration covers the whole
                # horizon, a return to the initial state is physically impossible.
                # The transition equation and initial residual obligation above
                # then imply every minimum-time inequality, without dense sums.
                if initial and v.min_down_quarters>=stop-begin:
                    m.phys.add(y==0)
                elif not initial and v.min_up_quarters>=stop-begin:
                    m.phys.add(z==0)
                else:
                    m.phys.add(sum(m.started[r,k] for k in range(max(begin,t-v.min_up_quarters+1),t+1)) <= u)
                    m.phys.add(sum(m.stopped[r,k] for k in range(max(begin,t-v.min_down_quarters+1),t+1)) <= 1-u)
                if v.ramp_mw_15min is not None:
                    prev_g=(v.initial_mw if carry is None else carry['power'][r]) if t == begin else m.power[r,t-1]
                    m.phys.add(g-prev_g <= v.ramp_mw_15min+v.minimum_mw*y)
                    m.phys.add(prev_g-g <= v.ramp_mw_15min+v.minimum_mw*z)
                    m.reserve[r,t].setub(v.ramp_mw_15min)
            if v.kind == 'hydro' and holds:
                up,down=holds[t].get(r,(0.,0.))
                if up or down:
                    m.phys.add(g<=cap-up)
                    m.phys.add(g>=v.minimum_mw+down)
            if v.kind == 'hydro' and v.ramp_mw_15min is not None:
                previous=(v.initial_mw if carry is None else carry['power'][r]) if t==begin else m.power[r,t-1]
                m.phys.add(p.inequality(-v.ramp_mw_15min,g-previous,v.ramp_mw_15min))
            if r in st:
                ch,e,mode=m.charge[r,t],m.energy[r,t],m.mode[r,t]
                e.setub(v.energy_mwh)
                prescribed=case.storage_plan[t][r]
                if spot_disabled and r in spot_disabled[t] and prescribed != 0:
                    raise ClearingError(f'储能 {r} 时段 {t+1} 不参加现货，充放电计划必须为零')
                mode.fix(int(prescribed > 0))
                if prescribed>cap:
                    raise ClearingError(f'储能 {r} 时段 {t+1} 的放电计划超过可用出力')
                g.fix(max(0.,prescribed))
                ch.fix(max(0.,-prescribed))
                prev_e=(v.initial_energy_mwh if carry is None else carry['energy'][r]) if t == begin else m.energy[r,t-1]
                m.phys.add(e == prev_e+DT*(v.charge_efficiency*ch-g/v.discharge_efficiency))
                if t == stop-1:
                    e.fix(v.initial_energy_mwh if da_mode is None else da_mode[t]['energy'][r])
    for r in sorted(hydro):
        budget=rs[r].daily_water_budget_mwh-(0 if carry is None else carry['hydro_used'][r])
        # A rolling window cannot spend tomorrow's within-day water; preserve
        # the DA budget use outside the current look-ahead as a research anchor.
        after = 0. if da_mode is None else DT*sum(row['power'][r] for row in da_mode[stop:])
        m.phys.add(DT*sum(m.power[r,t] for t in times) <= max(0.,budget-after))
    spill_cost=0
    if case.cascade is not None:
        # A water-only continuation proves that the last physical stock target
        # can still be met. Its releases are neither executed nor priced.
        hydraulic_ids=tuple(s.id for s in case.cascade.reservoirs)
        has_target=any(s.terminal_min_m3 is not None or s.terminal_max_m3 is not None
                       for s in case.cascade.reservoirs)
        water_stop=PERIODS if has_target else stop
        tail=range(stop,water_stop)
        def tail_bounds(_,r,t):
            up,down=holds[t].get(r,(0.,0.)) if holds else (0.,0.)
            return (rs[r].minimum_mw+down if down else 0.,
                    case.available[t].get(r,rs[r].capacity_mw)-up)
        m.water_tail_power=p.Var(hydraulic_ids,tail,domain=p.NonNegativeReals,bounds=tail_bounds)
        def water_power(r,t):
            return m.power[r,t] if t<stop else m.water_tail_power[r,t]
        for r in hydraulic_ids:
            rate=rs[r].ramp_mw_15min
            if rate is not None:
                for t in tail:
                    m.phys.add(p.inequality(-rate,water_power(r,t)-water_power(r,t-1),rate))
        attach_cascade_constraints(m,case.cascade,begin,water_stop,water_power,
                                   state=None if carry is None else carry['cascade_state'])
        # Modest synthetic coefficient, not the official numerical water penalty.
        spill_cost=.02*sum(m.cascade_spill[r,t] for r in hydraulic_ids for t in times)
    m.objective = p.Objective(expr=spill_cost+DT*sum(
        sum(rs[r].offer_yuan_mwh*m.power[r,t] for r in rs if not rs[r].segments)
        +sum(price*m.segment[r,i,t] for (r,i),(_,price) in segments.items())
        +sum(rs[r].online_cost_yuan_hour*m.online[r,t] for r in th)
        +sum(.01*m.charge[r,t] for r in st)
        +case.unmet_load_yuan_mwh*(sum(m.shed[b,t]+m.spill[b,t] for b in case.buses)+m.reserve_short[t])
        for t in times)+sum(rs[r].startup_yuan*m.started[r,t] for r in th for t in times))
    return m


def _solve(model, *, mip, time_limit_s):
    import pyomo.environ as p
    solver=p.SolverFactory('highs')
    if not solver.available(exception_flag=False):
        raise ClearingError('缺少 HiGHS 求解器')
    solver.options.update({'time_limit':time_limit_s,'threads':1})
    if mip:
        solver.options['mip_rel_gap']=.01
    # Eager import is needed for HiGHS to populate the reduced-cost suffix.
    load_suffixes = hasattr(model, 'rc')
    from pyomo.contrib.solver.common.util import NoFeasibleSolutionError
    try:
        result=solver.solve(model, load_solutions=load_suffixes)
    except NoFeasibleSolutionError as exc:
        raise ClearingError('求解器未找到可行解，请核对申报和运行边界') from exc
    condition=result.solver.termination_condition
    if condition != p.TerminationCondition.optimal:
        raise ClearingError(f'HiGHS 未完成可验收的{"机组组合" if mip else "定价"}：{condition}')
    if not load_suffixes:
        model.solutions.load_from(result)


def _rows(model, case, begin, stop, *, prices=True, segment_dispatch=False):
    import pyomo.environ as p
    rs={r.id:r for r in case.resources}
    th=[r for r in rs if rs[r].kind == 'thermal']
    st=[r for r in rs if rs[r].kind == 'storage']
    val=p.value
    q=lambda x: 0. if abs(val(x))<1e-9 else float(val(x))
    return [
        dict(quarter=t,
             power={r:q(model.power[r,t]) for r in rs},
             **({'accepted_segments_mw':{r:[q(model.segment[r,i,t]) for i in range(len(u.segments))]
                                         for r,u in rs.items() if u.segments}} if segment_dispatch else {}),
             charge={r:q(model.charge[r,t]) for r in st},
             energy={r:q(model.energy[r,t]) for r in st},
             online={r:round(val(model.online[r,t])) for r in th},
             curtailed_mw={r:max(0.,case.available[t][r]-q(model.power[r,t]))
                           for r in rs if rs[r].kind=='renewable'},
             thermal_reliability_reserve_mw={r:q(model.reserve[r,t]) for r in th},
             **(dict(forecast_activation_mw={r:q(model.forecast_activation[r,t]) for r in th},
                     forecast_stress_flows_mw={l.id:q(model.forecast_stress_flow[l.id,t]) for l in case.lines})
                if case.deliverable_up_mw else {}),
             ancillary_holds_mw={r:list(values) for r,values in model.ancillary_holds[t].items()},
             shed={b:q(model.shed[b,t]) for b in case.buses},
             demand={b:float(case.demand[t][b]) for b in case.buses},
             spill={b:q(model.spill[b,t]) for b in case.buses},
             flows={l.id:q(model.flow[l.id,t]) for l in case.lines},
             reliability_reserve_short_mw=q(model.reserve_short[t]),
             **({'hydro':{r.id:{'storage_m3':q(model.cascade_volume[r.id,t]),
                                  'turbine_m3s':q(model.cascade_turbine[r.id,t]),
                                  'water_spill_m3s':q(model.cascade_spill[r.id,t]),
                                  'eco_bypass_m3s':r.min_bypass_m3s,
                                  'non_turbine_m3s':r.min_bypass_m3s+q(model.cascade_spill[r.id,t]),
                                  'release_m3s':q(model.cascade_release[r.id,t]),
                                  'regulation':r.regulation,
                                  'upstream_arrival_m3':q(model.cascade_arrival[r.id,t]),
                                  'natural_inflow_m3s':q(model.cascade_natural[r.id,t])}
                           for r in case.cascade.reservoirs}} if case.cascade is not None else {}),
             **({'raw_nodal_prices':{b:q(model.dual[model.balance[b,t]]/DT) for b in case.buses}} if prices else {}))
        for t in range(begin,stop)]


def _pricing(model, *, time_limit_s):
    import pyomo.environ as p
    for idx in ('online','started','stopped','mode'):
        for v in getattr(model,idx).values():
            v.fix(round(p.value(v)))
    # Fixing alone is insufficient: the native LP must have no integer type.
    p.TransformationFactory('core.relax_integer_vars').apply_to(model)
    model.dual=p.Suffix(direction=p.Suffix.IMPORT)
    _solve(model,mip=False,time_limit_s=time_limit_s)


def _validate_ancillary(case, holds, spot_disabled):
    supported={r.id for r in case.resources if r.kind in ('thermal','hydro')}
    storage={r.id for r in case.resources if r.kind=='storage'}
    if spot_disabled and (len(spot_disabled)!=PERIODS or any(set(q)-storage for q in spot_disabled)):
        raise ClearingError('现货排他计划无效')
    if holds:
        if len(holds)!=PERIODS or any(r not in supported|storage or len(values)!=2
                    or any(not _num(v) for v in values)
                    for quarter in holds for r,values in quarter.items()):
            raise ClearingError('辅助服务授予边界无效；仅演示火电和水电可验证占位')
        if any(r in storage and any(q[r]) for q in holds for r in q):
            raise ClearingError('储能辅助服务需另行核验持续时间和荷电状态，本原型不接受非零授予')



def _fix_optimal_face(model):
    """Preserve the LP objective via complementary slackness without a dense cost row."""
    import pyomo.environ as p
    # Nonzero reduced costs pin variable bounds; nonzero duals pin row bounds.
    # Equality rows already hold throughout the feasible region.
    for var, cost in model.rc.items():
        if not var.fixed and cost != 0.:
            bounds = [x for x in (var.lb, var.ub) if x is not None]
            var.fix(min(bounds, key=lambda x: abs(x-var.value)))
    for con, dual in list(model.dual.items()):
        if not con.equality and dual != 0.:
            bounds = [p.value(x) for x in (con.lower, con.upper) if x is not None]
            bound = min(bounds, key=lambda x: abs(x-p.value(con.body)))
            con.set_value((bound, con.body, bound))


def _tie_allocate(model, case, time_limit_s):
    """Apply optional DA lexicographic allocation on a clone of the priced LP.

    Scope: only explicit offer blocks from public coal, hydro and renewables.
    At a clipped unit/line/horizon the feasible minimum L1 quota departure is
    a research convention, not a draft-specified redistribution algorithm.
    """
    import pyomo.environ as p

    by_price={}
    for r in case.resources:
        if r.kind not in ('hydro','renewable') and not (r.kind=='thermal' and r.role=='public_coal'):
            continue
        for i,(width,price) in enumerate(r.segments):
            by_price.setdefault(price,[]).append((r.id,i,width,r.kind,r.role))
    same_type=[]
    clean=[]
    for price,blocks in sorted(by_price.items()):
        if any(c=='thermal' for _,_,_,c,_ in blocks) and any(c!='thermal' for _,_,_,c,_ in blocks):
            clean.extend((r,i) for r,i,_,c,_ in blocks if c!='thermal')
        # Don't blend competitive PV with a different registered resource type
        # merely because both share the renewable physical implementation.
        for kind,role in sorted({(k,role) for _,_,_,k,role in blocks}):
            group=[(r,i,w) for r,i,w,k,category in blocks if (k,category)==(kind,role)]
            if len({r for r,_,_ in group})>1:
                same_type.append((price,kind,role,group))
    if not same_type and not clean:
        return None

    opt=float(p.value(model.objective))
    lp=model.clone()
    _fix_optimal_face(lp)
    lp.tie_constraints=p.ConstraintList()
    lp.objective.deactivate()
    # Primary model represents emergency shortfalls via finite penalties;
    # a secondary solve must not introduce or relocate emergency slack.
    for name in ('shed','spill','reserve_short'):
        for idx,var in getattr(lp,name).items():
            var.fix(max(0.,float(p.value(getattr(model,name)[idx]))))
    if clean:
        expr=sum(lp.segment[r,i,t] for r,i in clean for t in range(PERIODS))
        lp.clean_priority=p.Objective(expr=expr,sense=p.maximize)
        _solve(lp,mip=False,time_limit_s=time_limit_s)
        best_clean=float(p.value(expr))
        _fix_optimal_face(lp)
        lp.clean_priority.deactivate()
    if same_type:
        from collections import defaultdict
        quotas={}
        for g,(_,_,_,blocks) in enumerate(same_type):
            weights=defaultdict(float)
            for r,_,w in blocks:
                weights[r]+=w
            for r,w in weights.items():
                quotas[g,r]=w/sum(weights.values())
        lp.quota_error=p.Var([(g,r,t) for g,r in quotas for t in range(PERIODS)],domain=p.NonNegativeReals)
        for (g,r),share in quotas.items():
            _,_,_,group=same_type[g]
            for t in range(PERIODS):
                total=sum(lp.segment[u,i,t] for u,i,_ in group)
                own=sum(lp.segment[u,i,t] for u,i,_ in group if u==r)
                delta=own-share*total
                lp.tie_constraints.add(delta<=lp.quota_error[g,r,t])
                lp.tie_constraints.add(-delta<=lp.quota_error[g,r,t])
        lp.share_priority=p.Objective(expr=sum(lp.quota_error.values()))
        _solve(lp,mip=False,time_limit_s=time_limit_s)
    if clean and p.value(expr) < best_clean-1e-5:
        raise ClearingError('同价分配未保持清洁能源优先目标')
    cost_difference = float(p.value(lp.objective.expr)) - opt
    if abs(cost_difference) > 1e-5:
        raise ClearingError(f'同价分配未保持原始经济目标，差额 {cost_difference:.9g} 元')
    rows=_rows(lp,case,0,PERIODS,prices=False,segment_dispatch=True)
    for t,row in enumerate(rows):
        deviations=[]
        for price,kind,role,group in same_type:
            segments=[{'unit':r,'index':i,'width_mw':w,'accepted_mw':float(p.value(lp.segment[r,i,t]))}
                      for r,i,w in group]
            deviations.append({'price':price,'kind':kind,'role':role,'segments':segments})
        row['equal_offer_dispatch']={
            'status':'secondary_dispatch',
            'pricing_source':'original_fixed_commitment_lp',
            'allocation':'maximize_equal_offer_clean_mw_then_minimize_feasible_segment_share_l1',
            'whole_day_primary_cost_difference_cny':float(p.value(lp.objective.expr))-opt,
            'share_groups':deviations,
        }
    return rows


def day_ahead(case, *, fixed_commitment=None, holds=(), spot_disabled=(), time_limit_s=3600,
              equal_offer_priority=False, on_commitment_solved=None, hydro_bounds=None,
              on_scuc_dispatch=None):
    """Return 96 DA reference quarters, never a monetary settlement."""
    validate(case)
    _validate_ancillary(case, holds, spot_disabled)
    m=_build(case,0,PERIODS,da_mode=fixed_commitment,holds=holds,spot_disabled=spot_disabled)
    if hydro_bounds is not None:
        hydro = {r.id for r in case.resources if r.kind == 'hydro'}
        if len(hydro_bounds) != PERIODS:
            raise ClearingError('水电审核边界须覆盖日前96段')
        for t, bounds in enumerate(hydro_bounds):
            if set(bounds) != hydro:
                raise ClearingError(f'第 {t+1} 段水电审核机组不完整')
            for uid, (low, high) in bounds.items():
                g = m.power[uid, t]
                if not _num(low) or not _num(high, low, g.ub):
                    raise ClearingError(f'{uid} 第 {t+1} 段水电审核边界无效')
                up, down = holds[t].get(uid,(0.,0.)) if holds else (0.,0.)
                if low + down > high - up:
                    raise ClearingError(f'{uid} 第 {t+1} 段辅助服务占位超过核准水电调节区间')
                g.setlb(low + down)
                g.setub(high - up)
    # Fixed thermal commitment still leaves derived binary transition variables.
    _solve(m,mip=True,time_limit_s=time_limit_s)
    if on_commitment_solved is not None:
        on_commitment_solved()
    if on_scuc_dispatch is not None:
        on_scuc_dispatch(_rows(m,case,0,PERIODS,prices=False))
    if equal_offer_priority:
        import pyomo.environ as p
        m.rc = p.Suffix(direction=p.Suffix.IMPORT)
    _pricing(m,time_limit_s=time_limit_s)
    priced=_rows(m,case,0,PERIODS,segment_dispatch=equal_offer_priority)
    if not equal_offer_priority:
        return priced
    allocated=_tie_allocate(m,case,time_limit_s)
    if allocated is None:
        for row in priced:
            row['equal_offer_dispatch']={'status':'no_supported_equal_offer_group',
                                         'pricing_source':'original_fixed_commitment_lp'}
        return priced
    for row,baseline in zip(allocated,priced):
        row['raw_nodal_prices']=baseline['raw_nodal_prices']
    return allocated


def intraday(case, begin, carry, *, holds=(), spot_disabled=(), time_limit_s=3600):
    """Physical planning for the remaining day. No LP prices or ledger."""
    if type(begin) is not int or not 0<=begin<PERIODS:
        raise ClearingError('日内生效时段无效')
    if (begin==0 and carry is not None or begin>0
            and (not isinstance(carry,dict) or carry.get('quarter')!=begin-1)):
        raise ClearingError('日内优化需要从已执行的上一时段状态开始')
    validate(case)
    _validate_ancillary(case, holds, spot_disabled)
    model=_build(case,begin,PERIODS,carry=carry,holds=holds,spot_disabled=spot_disabled)
    _solve(model,mip=True,time_limit_s=time_limit_s)
    rows=_rows(model,case,begin,PERIODS,prices=False)
    rejected=next((r for r in rows if max((*r['shed'].values(),*r['spill'].values(),
                                           r['reliability_reserve_short_mw']))>1.e-6),None)
    if rejected:
        raise ClearingError(f'日内参考计划第 {rejected["quarter"]+1} 段有可靠性松弛，不采用；请检查边界')
    return rows


def realtime(case, da, *, executed_periods=PERIODS, window_quarters=WINDOW,
             holds=(), spot_disabled=(), window_inputs=None, intraday_update=None,
             intraday_result=None, time_limit_s=3600):
    """Execute only first cell; forecast callback may supply scheduled window inputs.

    Without callback, this is a deterministic perfect-information synthetic replay.
    """
    validate(case)
    _validate_ancillary(case, holds, spot_disabled)
    if type(executed_periods) is not int or not 1<=executed_periods<=PERIODS:
        raise ClearingError('实时执行段数须为 1–96')
    if type(window_quarters) is not int or not 1<=window_quarters<=16:
        raise ClearingError('实时研究窗口须为1–16段')
    if len(da)!=PERIODS or any(row.get('quarter')!=t for t,row in enumerate(da)):
        raise ClearingError('实时需要同源连续96段日前组合')
    if intraday_update is not None and (type(intraday_update.effective_quarter) is not int
                                        or not 0<=intraday_update.effective_quarter<PERIODS):
        raise ClearingError('日内边界更新时段无效')
    base_case=case
    schedule=da
    online={r.id:int(r.initial_mw>0) for r in case.resources if r.kind=='thermal'}
    ages={r.id:r.initial_state_age_quarters for r in case.resources if r.kind=='thermal'}
    hydro={r.id:0. for r in case.resources if r.daily_water_budget_mwh is not None}
    carry=None
    water=initial_cascade_state(case.cascade) if case.cascade is not None else None
    results=[]
    for t in range(executed_periods):
        if intraday_update is not None and t==intraday_update.effective_quarter:
            base_case=amended_case(case,intraday_update)
            planned=intraday(base_case,t,carry,holds=holds,spot_disabled=spot_disabled,time_limit_s=time_limit_s)
            schedule=[*da[:t],*planned]
            if intraday_result is not None:
                intraday_result(t,carry,planned)
        stop=min(PERIODS,t+window_quarters)
        current=base_case if window_inputs is None else window_inputs(base_case,t,stop)
        if current is not base_case:
            if (current.resources!=base_case.resources or current.lines!=base_case.lines
                    or current.buses!=base_case.buses or current.fixed_schedule!=base_case.fixed_schedule
                    or current.reserve_mw!=base_case.reserve_mw or current.deliverable_up_mw!=base_case.deliverable_up_mw
                    or (current.cascade is None)!=(base_case.cascade is None)
                    or current.cascade is not None and
                        (current.cascade.reservoirs!=base_case.cascade.reservoirs
                         or current.cascade.reaches!=base_case.cascade.reaches
                         or current.cascade.require_all_hydro!=base_case.cascade.require_all_hydro)):
                raise ClearingError('实时预测更新不可替换已冻结的报价、固定计划或物理网络')
            validate(current)
        m=_build(current,t,stop,carry=carry,da_mode=schedule,holds=holds,spot_disabled=spot_disabled)
        # The U values are fixed; derive starts/stops solely from fixed commitments.
        for r in (v.id for v in case.resources if v.kind == 'thermal'):
            last=(int(next(v for v in case.resources if v.id==r).initial_mw>0) if carry is None else carry['online'][r])
            for k in range(t,stop):
                now=schedule[k]['online'][r]
                m.started[r,k].fix(max(0,now-last))
                m.stopped[r,k].fix(max(0,last-now))
                last=now
        _pricing(m,time_limit_s=time_limit_s)
        row=_rows(m,current,t,t+1)[0]
        results.append(row)
        for r in hydro:
            hydro[r]+=DT*row['power'][r]
        for r,state in row['online'].items():
            ages[r]=ages[r]+1 if state==online[r] else 1
        online=row['online']
        carry={**row,'online_age':ages.copy(),'hydro_used':hydro.copy()}
        if water is not None:
            reservoirs=row['hydro']
            flow={r:row['power'][r] for r in reservoirs}
            spill={r:z['water_spill_m3s'] for r,z in reservoirs.items()}
            for values in (flow,spill):
                if any(x < -1e-8 for x in values.values()):
                    raise ClearingError('水力模型求解出现超出数值容差的负出力/弃水')
            solved=advance_cascade(current.cascade,water,{r:max(0.,v) for r,v in flow.items()},
                                   {r:max(0.,v) for r,v in spill.items()})
            for r,z in reservoirs.items():
                if (abs(solved.state.volume_m3[r]-z['storage_m3'])>1e-5
                        or abs(solved.upstream_arrival_m3[r]-z['upstream_arrival_m3'])>1e-5):
                    raise ClearingError('实时水量状态与独立守恒核验不一致')
            prior_water=water
            water=solved.state
            carry['cascade_state']=water
            if case.cascade.require_all_hydro:
                # Close the whole modeled basin, including water still moving
                # between stations. Downstream passage is counted only on exit.
                net_input=STEP_SECONDS*sum(z['natural_inflow_m3s'] for z in reservoirs.values())-sum(solved.downstream_exit_m3.values())
                basin_error=water.total_water_m3-prior_water.total_water_m3-net_input
                if abs(basin_error)>1e-4:
                    raise ClearingError('含在途水量的流域执行总账不守恒')
                row['water_handoff']={'next_quarter':water.period,'storage_m3':dict(water.volume_m3),
                    'in_transit':[{'upstream':u,'downstream':d,'next_arrivals_m3':list(queue)}
                                  for (u,d),queue in water.in_transit_m3.items()],
                    'basin_exit_m3':dict(solved.downstream_exit_m3),'basin_residual_m3':basin_error}
    return results
