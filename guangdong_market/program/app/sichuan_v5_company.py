"""D-1 price-taking cascade company. Only released company forecasts belong here."""

from collections import OrderedDict, deque
from dataclasses import asdict, replace
from hashlib import sha256
import json
from math import isfinite
from threading import Lock

import pyomo.environ as p

if __package__:
    from .sichuan_v5_dadu_progress import report
    from .sichuan_v5_energy import DT, PERIODS, ClearingError
    from .sichuan_v5_hydro import STEP_SECONDS, attach_cascade_constraints, validate_cascade
    from .sichuan_v5_segments import validate_curve
else:
    from sichuan_v5_dadu_progress import report
    from sichuan_v5_energy import DT, PERIODS, ClearingError
    from sichuan_v5_hydro import STEP_SECONDS, attach_cascade_constraints, validate_cascade
    from sichuan_v5_segments import validate_curve


_CAPABILITY_CACHE = OrderedDict()
_CACHE_LOCK = Lock()
_MW_TOL = 1.e-6


def _digest(data):
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _number(v):
    return type(v) in (int, float) and isfinite(v)


def _technology(reservoir):
    return ("run_of_river" if reservoir.storage_min_m3 == reservoir.storage_max_m3
            else "reservoir")


def _project_prices(targets):
    """Least-squares fit on the integer price grid with exact adjacent tick limits."""
    previous = [(value - targets[0]) ** 2 for value in range(801)]
    predecessors = []
    for target in targets[1:]:
        current = [float("inf")] * 801
        parent = [-1] * 801
        candidates = deque()
        for price in range(801):
            high = price - 20
            if high >= 0 and isfinite(previous[high]):
                while candidates and previous[candidates[-1]] > previous[high]:
                    candidates.pop()
                candidates.append(high)
            while candidates and candidates[0] < price - 150:
                candidates.popleft()
            if candidates:
                parent[price] = candidates[0]
                current[price] = previous[parent[price]] + (price - target) ** 2
        previous = current
        predecessors.append(parent)
    index = min(range(801), key=previous.__getitem__)
    prices = [index]
    for parent in reversed(predecessors):
        index = parent[index]
        prices.append(index)
    return prices[::-1]


def _price_blocks(maximum, technology, samples, strategy):
    # Same 10/5 block widths in both treatments, with the draft missing-bid
    # templates as the naive benchmark. Availability remains time-dependent.
    count = 10 if technology == "reservoir" else 5
    points = [round(maximum * k / count, 2) for k in range(count + 1)]
    widths = [round(b - a, 2) for a, b in zip(points, points[1:])]
    if strategy == "naive":
        targets = [20 * k for k in range(count)]
    else:
        # A daily curve cannot reproduce 96 opportunity costs. Sample the six
        # midpoint quantiles, then fit the nearest legally spaced daily curve.
        ordered = sorted(samples)
        targets = [ordered[min(len(ordered) - 1, int((k + .5) * len(ordered) / count))]
                   for k in range(count)]
    prices = _project_prices(targets)
    segments = [[width, price] for width, price in zip(widths, prices)]
    points = []
    start = 0.
    for width, price in segments:
        end = round(start + width, 2)
        points.append((start, end, price))
        start = end
    validate_curve(technology, maximum, points)
    return segments, targets


def _company_model(case, rs, prices, water_values):
    reservoirs = validate_cascade(case.cascade)
    ids, times = tuple(sorted(rs)), range(PERIODS)
    model = p.ConcreteModel()
    model.power = p.Var(ids, times, bounds=lambda _, r, t: (
        0., case.available[t].get(r, rs[r].capacity_mw)))
    attach_cascade_constraints(model, case.cascade, 0, PERIODS, lambda r, t: model.power[r, t])
    model.ramp = p.ConstraintList()
    for r in ids:
        unit = rs[r]
        if unit.ramp_mw_15min is not None:
            for t in times:
                previous = model.power[r, t - 1] if t else unit.initial_mw
                model.ramp.add(p.inequality(-unit.ramp_mw_15min,
                                            model.power[r, t] - previous, unit.ramp_mw_15min))
    salvage = sum(water_values[r] * (model.cascade_volume[r, PERIODS - 1] -
                                     reservoirs[r].initial_storage_m3) for r in ids)
    for reach in case.cascade.reaches:
        # Value water at its receiving station at D+1, including carry beyond the
        # horizon. Pre-day queued volumes left after the day are constant here.
        delay = reach.travel_quarters
        terminal_queue = sum(reach.initial_arrivals_m3[PERIODS:]) + STEP_SECONDS * sum(
            model.cascade_release[reach.upstream, t] for t in range(max(0, PERIODS-delay), PERIODS))
        salvage += water_values[reach.downstream] * (terminal_queue - sum(reach.initial_arrivals_m3))
    model.revenue = p.Expression(expr=DT * sum(prices[t][r] * model.power[r, t]
                                              for t in times for r in ids))
    model.stock_value_change = p.Expression(expr=salvage)
    model.objective = p.Objective(expr=model.revenue + model.stock_value_change, sense=p.maximize)
    from pyomo.contrib.appsi.solvers import Highs
    from pyomo.contrib.appsi.base import TerminationCondition
    solver = Highs()
    solver.highs_options["threads"] = 1
    solver.config.load_solution = False
    result = solver.solve(model)
    if result.termination_condition != TerminationCondition.optimal:
        raise ClearingError(f"公司事前水量及库存约束不可解：{result.termination_condition}")
    solver.load_vars()
    return model, solver


def _physical_capabilities(model, solver, ids, available, physics_key, progress=None):
    """Exact marginal capability per station; individual extrema need not coincide.

    Joint LP witnesses certify many bounds in one solve. Unresolved intervals
    are split down to singleton LPs. The firm-power search only skips a window
    when a certified single-quarter maximum bounds it below the incumbent.
    """
    from pyomo.contrib.appsi.base import TerminationCondition

    with _CACHE_LOCK:
        cached = _CAPABILITY_CACHE.get(physics_key)
    if cached is not None:
        capabilities = json.loads(cached)
        report(progress, "company", len(capabilities), len(ids), "按相同水力边界复用已核定的逐站能力")
        return capabilities
    model.objective.deactivate()
    model.firm = p.Var(domain=p.NonNegativeReals)
    model.cap_objective = p.Objective(expr=model.firm, sense=p.maximize)
    solver.add_variables([model.firm])
    # All constraints remain exactly the same across the repeated LPs, except
    # the twelve explicitly installed for a sustainable plateau.
    for key in solver.update_config:
        if key != "treat_fixed_vars_as_params":
            solver.update_config[key] = False

    def optimize(expr, variables):
        model.cap_objective.set_value(expr)
        solver.set_objective(model.cap_objective)
        solution = solver.solve(model)
        if solution.termination_condition != TerminationCondition.optimal:
            raise ClearingError(f"公司物理能力校核失败：{solution.termination_condition}")
        values = solver.get_primals(variables)
        if progress is not None:
            nonlocal solved_lps
            solved_lps += 1
            if solved_lps == 1 or solved_lps % 8 == 0:
                report(progress, "company", len(capabilities), len(ids),
                       f"正在核定 {r}，本站已解 {solved_lps} 个能力线性规划")
        return [max(0., float(values[v])) for v in variables]

    def extrema(variables, bounds, witness, sign):
        result = list(witness)
        todo = [[t for t in range(PERIODS) if abs(result[t] - bounds[t]) > _MW_TOL]]
        while todo:
            group = todo.pop()
            if not group:
                continue
            sample = optimize(sign * sum(variables[t] for t in group), [variables[t] for t in group])
            unresolved = []
            for t, value in zip(group, sample):
                if sign * value > sign * result[t]:
                    result[t] = value
                if abs(result[t] - bounds[t]) > _MW_TOL:
                    unresolved.append(t)
            if len(group) > 1 and unresolved:
                if len(unresolved) == len(group):
                    mid = len(group) // 2
                    todo.extend((group[:mid], group[mid:]))
                else:
                    todo.append(unresolved)
        return result

    capabilities = {}
    for r in ids:
        solved_lps = 0
        report(progress, "company", len(capabilities), len(ids), f"正在核定 {r} 的逐段和连续3小时能力")
        variables = [model.power[r, t] for t in range(PERIODS)]
        high_day = optimize(sum(variables), variables)
        low_day = optimize(-sum(variables), variables)
        lower = extrema(variables, [0.] * PERIODS, low_day, -1)
        upper = extrema(variables, available[r], high_day, 1)
        windows = range(PERIODS - 11)
        best_start = max(windows, key=lambda t: min(high_day[t:t+12]))
        best = min(high_day[best_start:best_start+12])
        ceilings = [min(upper[t:t+12]) for t in windows]
        for start in sorted(windows, key=lambda t: (-ceilings[t], t)):
            if ceilings[start] <= best + _MW_TOL:
                break
            model.plateau = p.Constraint(range(12), rule=lambda _, k: model.firm <= variables[start + k])
            rows = list(model.plateau.values())
            solver.add_constraints(rows)
            value, = optimize(model.firm, [model.firm])
            solver.remove_constraints(rows)
            model.del_component(model.plateau)
            if value > best + _MW_TOL:
                best, best_start = value, start
        capabilities[r] = dict(minimum_mw=lower, available_mw=upper, daily_energy_mwh=DT * sum(high_day),
                               sustained_3h_mw=best, sustained_start_quarter=best_start)
        report(progress, "company", len(capabilities), len(ids), f"已核定 {r} 的96点能力、日能量与连续3小时能力")
    with _CACHE_LOCK:
        _CAPABILITY_CACHE[physics_key] = json.dumps(capabilities, allow_nan=False)
        if len(_CAPABILITY_CACHE) > 8:
            _CAPABILITY_CACHE.popitem(last=False)
    return capabilities


def prepare_company_submission(company_case, expected_price_yuan_mwh, water_value_yuan_m3,
                               *, strategy="water_value", as_of="D-1", progress=None):
    """Produce a signed public offer and separate private D-1 operating analysis.

    Prices are exogenous, indexed by quarter then station. Water values in
    yuan/m³ are the value of a unit at that location including downstream uses.
    Nothing in this interface receives DA/RT prices, dispatch or realized water.
    """
    if strategy not in ("naive", "water_value") or not isinstance(as_of, str) or not as_of.strip():
        raise ClearingError("公司策略或申报时点无效")
    case = company_case
    reservoirs = validate_cascade(case.cascade)
    rs = {u.id: u for u in case.resources if u.kind == "hydro"}
    ids = tuple(sorted(rs))
    if not ids or set(ids) != set(reservoirs) or len(case.cascade.natural_inflow_m3s) != PERIODS:
        raise ClearingError("公司水文输入、运行日与电站不一致")
    prices = expected_price_yuan_mwh
    if (len(prices) != PERIODS or len(case.available) != PERIODS
            or any(set(row) != set(ids) or not all(_number(v) for v in row.values()) for row in prices)):
        raise ClearingError("公司需要各站96点独立事前预期价与能力边界")
    if (set(water_value_yuan_m3) != set(ids) or
            not all(_number(v) and v >= 0 for v in water_value_yuan_m3.values())):
        raise ClearingError("各站位置期末水价值缺失或无效")
    availability = {r: [row.get(r, rs[r].capacity_mw) for row in case.available] for r in ids}
    if any(not _number(v) or not 0 <= v <= rs[r].capacity_mw for r in ids for v in availability[r]):
        raise ClearingError("公司可用容量须在额定容量范围内")
    report(progress, "company", 0, len(ids), "正在求解全梯级事前用水计划")
    model, solver = _company_model(case, rs, prices, water_value_yuan_m3)
    reduced_costs = solver.get_reduced_costs(list(model.power.values()))
    plan = [{r: max(0., float(p.value(model.power[r, t]))) for r in ids} for t in range(PERIODS)]
    # At optimum the reduced cost (yuan per MW for this 15-minute period)
    # equals own marginal revenue less physical opportunity cost. This includes
    # the downstream effect, binding flow constraints, storage and travel time.
    marginal = [{r: float(prices[t][r] - reduced_costs[model.power[r, t]] / DT)
                 if availability[r][t] > 0 else None for r in ids} for t in range(PERIODS)]
    physical_hash = _digest(dict(hydraulics=asdict(case.cascade),
                        units=[(r, rs[r].capacity_mw, rs[r].initial_mw, rs[r].ramp_mw_15min) for r in ids],
                        available=availability))
    capabilities = _physical_capabilities(model, solver, ids, availability, physical_hash, progress)
    offers = {}
    targets = {}
    for r in ids:
        samples = [row[r] for row in marginal if row[r] is not None]
        # With an all-day outage no MW can clear. A complete curve is retained
        # for audit; the market boundary, not a fictitious sale, enforces zero.
        segments, targets[r] = _price_blocks(rs[r].capacity_mw, _technology(reservoirs[r]),
                                             samples or [0.], strategy)
        offers[r] = dict(segments=segments, **capabilities[r], technical_available_mw=availability[r],
                         consumption_m3_kwh=3.6 * reservoirs[r].generation_m3s_per_mw,
                         natural_inflow_m3s=[row[r] for row in case.cascade.natural_inflow_m3s])
    payload = dict(schema="ieee39.dadu.company.submission.v1", strategy=strategy,
                   as_of=as_of, offers=offers)
    return dict(**payload, submission_sha256=_digest(payload), internal_plan_mw=plan,
                expected_price_yuan_mwh=[dict(row) for row in prices],
                water_value_yuan_m3=dict(water_value_yuan_m3), marginal_water_cost_yuan_mwh=marginal,
                raw_daily_block_targets_yuan_mwh=targets,
                expected_energy_revenue_yuan=float(p.value(model.revenue)),
                inventory_and_transit_value_change_yuan=float(p.value(model.stock_value_change)),
                company_physical_forecast_sha256=physical_hash,
                company_forecast_sha256=_digest(dict(physical_forecast=physical_hash, prices=prices,
                                                     terminal_values=water_value_yuan_m3)),
                quantity_basis="固定期初/末库容边界、来水与在途条件；逐站独立求96点可达上下限、日最大可发电量及连续12段可持续最大出力。各极值不保证可同时实现。")


def apply_company_submission(market_case, submission):
    """Install public offers only. Physical limits and forecasts belong to the market."""
    payload = {k: submission[k] for k in ("schema", "strategy", "as_of", "offers")}
    if payload["schema"] != "ieee39.dadu.company.submission.v1" or _digest(payload) != submission.get("submission_sha256"):
        raise ClearingError("冻结公司申报摘要不匹配")
    reservoirs = validate_cascade(market_case.cascade)
    rs = {u.id: u for u in market_case.resources if u.kind == "hydro"}
    if set(submission["offers"]) != set(rs) or set(rs) != set(reservoirs):
        raise ClearingError("冻结申报与市场水电站集合不一致")
    units = []
    for unit in market_case.resources:
        if unit.id in rs:
            blocks = submission["offers"][unit.id]["segments"]
            start, points = 0., []
            for width, price in blocks:
                end = round(start + width, 2)
                points.append((start, end, price))
                start = end
            validate_curve(_technology(reservoirs[unit.id]), unit.capacity_mw, points)
            unit = replace(unit, offer_yuan_mwh=float(blocks[0][1]),
                           segments=tuple((float(w), float(p)) for w, p in blocks))
        units.append(unit)
    return replace(market_case, resources=tuple(units))
