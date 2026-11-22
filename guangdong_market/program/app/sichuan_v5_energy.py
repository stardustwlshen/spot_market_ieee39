"""Independent lossless IEEE39 research core, no Guangdong pricing/AGC defaults."""
from dataclasses import dataclass
from math import isfinite

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
    if not _num(case.reserve_mw) or not _num(case.unmet_load_yuan_mwh, 1.):
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
    for r in case.resources:
        if (r.bus not in buses or r.kind not in ('thermal','renewable','hydro','fixed','storage')
            or not _num(r.capacity_mw, 1e-9) or not _num(r.offer_yuan_mwh, -case.unmet_load_yuan_mwh, case.unmet_load_yuan_mwh)
            or not _num(r.minimum_mw, 0., r.capacity_mw) or not _num(r.initial_mw, 0., r.capacity_mw)
            or r.ramp_mw_15min is not None and not _num(r.ramp_mw_15min, 1e-9)
            or not _num(r.startup_yuan)
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
        if any(r.id not in a for r in case.resources if r.kind in ('renewable','fixed')):
            raise ClearingError('新能源预测或固定计划缺项')


def _build(case, begin, stop, *, carry=None, da_mode=None, holds=(), spot_disabled=()):
    import pyomo.environ as p
    rs = {r.id: r for r in case.resources}
    ls = {l.id: l for l in case.lines}
    th = {r for r in rs if rs[r].kind == 'thermal'}
    st = {r for r in rs if rs[r].kind == 'storage'}
    hydro = {r for r in rs if rs[r].daily_water_budget_mwh is not None}
    times = range(begin, stop)
    by_bus = {b: [r for r,v in rs.items() if v.bus == b] for b in case.buses}
    m = p.ConcreteModel()
    m.ancillary_holds=holds or ({},)*PERIODS
    segments={(r,i):(width,price) for r in rs for i,(width,price) in enumerate(rs[r].segments)}
    m.segment = p.Var(segments,times,bounds=lambda _,r,i,t: (0,segments[r,i][0]))
    m.power = p.Var(rs, times, domain=p.NonNegativeReals)
    m.charge = p.Var(sorted(st), times, domain=p.NonNegativeReals)
    m.energy = p.Var(sorted(st), times, domain=p.NonNegativeReals)
    m.mode = p.Var(sorted(st), times, domain=p.Binary)
    m.online = p.Var(sorted(th), times, domain=p.Binary)
    m.started = p.Var(sorted(th), times, domain=p.Binary)
    m.stopped = p.Var(sorted(th), times, domain=p.Binary)
    m.reserve = p.Var(sorted(th), times, domain=p.NonNegativeReals)
    m.shed = p.Var(case.buses, times, domain=p.NonNegativeReals)
    m.spill = p.Var(case.buses, times, domain=p.NonNegativeReals)
    m.reserve_short = p.Var(times, domain=p.NonNegativeReals)
    m.angle = p.Var(case.buses, times)
    m.flow = p.Var(ls, times, bounds=lambda _, l, t: (-ls[l].limit_mw, ls[l].limit_mw))
    m.eq_flow = p.Constraint(ls,times, rule=lambda _,l,t:
       m.flow[l,t] == 100/ls[l].reactance*(m.angle[ls[l].source,t]-m.angle[ls[l].target,t]))
    out = {b:[l for l,v in ls.items() if v.source == b] for b in case.buses}
    into = {b:[l for l,v in ls.items() if v.target == b] for b in case.buses}
    m.balance = p.Constraint(case.buses,times,rule=lambda _,b,t:
        sum(m.power[r,t] for r in by_bus[b])-sum(m.charge[r,t] for r in by_bus[b] if r in st)
        +m.shed[b,t]-m.spill[b,t]-sum(m.flow[l,t] for l in out[b])
        +sum(m.flow[l,t] for l in into[b]) == case.demand[t][b])
    m.phys = p.ConstraintList()
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
                g.fix(cap)
            if r in th:
                u,y,z=m.online[r,t],m.started[r,t],m.stopped[r,t]
                initial = int(v.initial_mw > 0) if carry is None else carry['online'][r]
                prev_u=initial if t == begin else m.online[r,t-1]
                m.phys.add(u-prev_u == y-z)
                m.phys.add(y+z <= 1)
                if da_mode is not None:
                    u.fix(da_mode[t]['online'][r])
                elif t-begin < max(0,(v.min_up_quarters if initial else v.min_down_quarters)-v.initial_state_age_quarters):
                    u.fix(initial)
                m.phys.add(g >= v.minimum_mw*u)
                up,down = holds[t].get(r, (0.,0.)) if holds else (0.,0.)
                m.phys.add(g+m.reserve[r,t] <= cap*u-up)
                m.phys.add(g >= v.minimum_mw*u+down)
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
    m.objective = p.Objective(expr=DT*sum(
        sum(rs[r].offer_yuan_mwh*m.power[r,t] for r in rs if not rs[r].segments)
        +sum(price*m.segment[r,i,t] for (r,i),(_,price) in segments.items())
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
    result=solver.solve(model, load_solutions=False)
    condition=result.solver.termination_condition
    if condition != p.TerminationCondition.optimal:
        raise ClearingError(f'HiGHS 未完成可验收的{"机组组合" if mip else "定价"}：{condition}')
    model.solutions.load_from(result)


def _rows(model, case, begin, stop):
    import pyomo.environ as p
    rs={r.id:r for r in case.resources}
    th=[r for r in rs if rs[r].kind == 'thermal']
    st=[r for r in rs if rs[r].kind == 'storage']
    val=p.value
    q=lambda x: 0. if abs(val(x))<1e-9 else float(val(x))
    return [
        dict(quarter=t,
             power={r:q(model.power[r,t]) for r in rs},
             charge={r:q(model.charge[r,t]) for r in st},
             energy={r:q(model.energy[r,t]) for r in st},
             online={r:round(val(model.online[r,t])) for r in th},
             curtailed_mw={r:max(0.,case.available[t][r]-q(model.power[r,t]))
                           for r in rs if rs[r].kind=='renewable'},
             thermal_reliability_reserve_mw={r:q(model.reserve[r,t]) for r in th},
             ancillary_holds_mw={r:list(values) for r,values in model.ancillary_holds[t].items()},
             shed={b:q(model.shed[b,t]) for b in case.buses},
             demand={b:float(case.demand[t][b]) for b in case.buses},
             spill={b:q(model.spill[b,t]) for b in case.buses},
             flows={l.id:q(model.flow[l.id,t]) for l in case.lines},
             reliability_reserve_short_mw=q(model.reserve_short[t]),
             raw_nodal_prices={b:q(model.dual[model.balance[b,t]]/DT) for b in case.buses})
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



def day_ahead(case, *, fixed_commitment=None, holds=(), spot_disabled=(), time_limit_s=3600):
    """Return 96 DA reference quarters, never a monetary settlement."""
    validate(case)
    _validate_ancillary(case, holds, spot_disabled)
    m=_build(case,0,PERIODS,da_mode=fixed_commitment,holds=holds,spot_disabled=spot_disabled)
    # Fixed thermal commitment still leaves derived binary transition variables.
    _solve(m,mip=True,time_limit_s=time_limit_s)
    _pricing(m,time_limit_s=time_limit_s)
    return _rows(m,case,0,PERIODS)


def realtime(case, da, *, executed_periods=PERIODS, window_quarters=WINDOW,
             holds=(), spot_disabled=(), window_inputs=None, time_limit_s=3600):
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
    hydro={r.id:0. for r in case.resources if r.daily_water_budget_mwh is not None}
    carry=None
    results=[]
    for t in range(executed_periods):
        stop=min(PERIODS,t+window_quarters)
        current=case if window_inputs is None else window_inputs(case,t,stop)
        m=_build(current,t,stop,carry=carry,da_mode=da,holds=holds,spot_disabled=spot_disabled)
        # The U values are fixed; derive starts/stops solely from fixed commitments.
        for r in (v.id for v in case.resources if v.kind == 'thermal'):
            last=(int(next(v for v in case.resources if v.id==r).initial_mw>0) if carry is None else carry['online'][r])
            for k in range(t,stop):
                now=da[k]['online'][r]
                m.started[r,k].fix(max(0,now-last))
                m.stopped[r,k].fix(max(0,last-now))
                last=now
        _pricing(m,time_limit_s=time_limit_s)
        row=_rows(m,current,t,t+1)[0]
        results.append(row)
        for r in hydro:
            hydro[r]+=DT*row['power'][r]
        carry={**row,'hydro_used':hydro.copy()}
    return results
