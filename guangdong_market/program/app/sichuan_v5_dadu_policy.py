"""Dadu experiment: draft risk rules and provisional hourly spot-energy valuation.

See Sichuan V5 draft risk §4.2.2, spot §§3.4/4.4/7.7 and the 2027 overall
draft sections on prices and settlement. No regulated coal curve, plant bill,
contract, compensation or gas-plan remuneration is inferred here.
"""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import fsum

if __package__:
    from .sichuan_v5_hydro import attach_cascade_constraints
    from .sichuan_v5_rules import classify_seasons, spot_clear_bounds
    from .sichuan_v5_segments import validate_curve
    from .sichuan_v5_settlement import SettlementInput, UniformPriceInput, settle_quarter, uniform_price
else:
    from sichuan_v5_hydro import attach_cascade_constraints
    from sichuan_v5_rules import classify_seasons, spot_clear_bounds
    from sichuan_v5_segments import validate_curve
    from sichuan_v5_settlement import SettlementInput, UniformPriceInput, settle_quarter, uniform_price


D = Decimal
ZERO = D(0)
CENT = D(".01")
HOURS = D(".25")
CATEGORIES = {"reservoir": "provincial_hydro", "run_of_river": "provincial_hydro",
              "coal": "public_coal", "pv": "centralized_renewables"}
# Illustrative marginal fuel-consumption endpoints at 0% and 100% of rating
# (g standard coal/kWh). Official monthly price and curves require external input.
COAL_G_KWH = {300: (305., 430.), 600: (285., 395.), 1000: (260., 360.)}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() else None


def _nonnegative(value, name):
    number = _number(value)
    if number is None or number < 0:
        raise ValueError(f"{name} 必须是有限非负数")
    return float(number)


def _cent(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _marginal_cost(size, fraction, coal_price):
    low, high = COAL_G_KWH[size]
    return (low + (high - low) * fraction) * coal_price / 1000.


def operator_hydro_envelope(case):
    """Independent DA hydraulic capability for the 96 power ratios and daily energy.

    Each MW value maximizes all stations' *simultaneous* output in its quarter
    over feasible full-day trajectories. These 96 individual maxima generally
    cannot form a single schedule. The MWh maximum is a separate full-day LP.
    This supply-side screen excludes electric network, load and economic bids.
    """
    import pyomo.environ as p
    from pyomo.contrib.appsi.base import TerminationCondition
    from pyomo.contrib.appsi.solvers import Highs

    hydro = {r.id: r for r in case.resources if r.kind == "hydro"}
    if (case.cascade is None or set(hydro) != {s.id for s in case.cascade.reservoirs}
            or not hydro or len(case.available) != 96 or len(case.cascade.natural_inflow_m3s) != 96):
        raise ValueError("水电能力校核需要覆盖所有水电站的独立日前96点边界")
    model = p.ConcreteModel()
    ticks = range(96)
    model.mw = p.Var(tuple(hydro), ticks, bounds=lambda _, key, t: (
        0., case.available[t].get(key, hydro[key].capacity_mw)))
    attach_cascade_constraints(model, case.cascade, 0, 96, lambda key, t: model.mw[key, t])
    model.unit_bounds = p.ConstraintList()
    for key, unit in hydro.items():
        if unit.daily_water_budget_mwh is not None:
            model.unit_bounds.add(.25 * p.quicksum(model.mw[key, t] for t in ticks)
                                  <= unit.daily_water_budget_mwh)
        if unit.ramp_mw_15min is not None:
            for t in ticks:
                before = model.mw[key, t-1] if t else unit.initial_mw
                ramp = unit.ramp_mw_15min
                model.unit_bounds.add(p.inequality(-ramp, model.mw[key, t] - before, ramp))
    total = [p.quicksum(model.mw[key, t] for key in hydro) for t in ticks]
    model.criterion = p.Objective(expr=.25 * p.quicksum(total), sense=p.maximize)
    solver = Highs()
    solver.highs_options["threads"] = 1
    solver.config.load_solution = False
    # The matrix, bounds, parameters and variable set are immutable. APPSI
    # retains the native model and LP basis; only its objective changes.
    for flag in ("check_for_new_or_removed_constraints", "check_for_new_or_removed_vars",
                 "check_for_new_or_removed_params", "check_for_new_objective", "update_constraints",
                 "update_vars", "update_params", "update_named_expressions"):
        setattr(solver.update_config, flag, False)

    def maximum(label):
        solved = solver.solve(model)
        if solved.termination_condition != TerminationCondition.optimal:
            raise ValueError(f"市场侧水电{label}无法核定：{solved.termination_condition}")
        return float(solved.best_feasible_objective)

    energy = maximum("全日可发电量")
    envelope = []
    for t in ticks:
        model.criterion.set_value(total[t])
        envelope.append(maximum(f"第{t + 1}段可达出力"))
    return envelope, energy


def market_power_policy(month, market_capacity_mw, net_demand_mw, positive_reserve_mw,
                        market_energy_mwh, *, coal_price_yuan_t=850., k1=1.25, k2=1.55):
    """Build a day-ahead risk decision independently of the company's bid and RT.

    Energy is independently feasible daily marketable energy after reservoir
    limits. Net demand has already removed non-market plans and net imports.
    For this teaching scenario the triggered day uses the corresponding
    reference for all quarters; no intra-day tight-period calendar is inferred.
    """
    season = classify_seasons(month).market_power
    cap_mw, demand, reserve = [
        [_nonnegative(v, name) for v in values]
        for name, values in (("市场可用能力", market_capacity_mw), ("净需求", net_demand_mw),
                             ("正备用", positive_reserve_mw))]
    if any(len(values) != 96 for values in (cap_mw, demand, reserve)):
        raise ValueError("供需比需要日前全部 96 个时段")
    if any(net + up <= 0 for net, up in zip(demand, reserve)) or not sum(demand):
        raise ValueError("供需比的分母必须大于零")
    power_ratio = fsum(p / (net + up) for p, net, up in zip(cap_mw, demand, reserve)) / 96.
    energy_ratio = _nonnegative(market_energy_mwh, "日可供电量") / (fsum(demand) * .25)
    coal_price = _nonnegative(coal_price_yuan_t, "合成标煤单价")
    k1, k2 = _nonnegative(k1, "K1"), _nonnegative(k2, "K2")
    if min(coal_price, k1, k2) <= 0:
        raise ValueError("参考曲线参数必须大于零")
    if season == "dry":
        trigger, k = "dry_routine", k1
    elif power_ratio <= 1.01:
        trigger, k = "wet_power", k2
    elif energy_ratio <= 1.25:
        trigger, k = "wet_energy", k1
    else:
        trigger, k = "none", 1.
    active = trigger != "none"
    upper = _marginal_cost(300, 1., coal_price) * k if active else spot_clear_bounds()[1]
    return {
        "active": active, "trigger": trigger, "season": season, "cap": float(_cent(D(str(upper)))),
        "power_ratio": power_ratio, "energy_ratio": energy_ratio, "k": k, "k1": k1, "k2": k2,
        "coal_price_yuan_t": coal_price, "fuel_curve_g_kwh": dict(COAL_G_KWH),
        "reference_source": "合成边际煤耗曲线、标煤价与 K 值；用于演示，不代表核定参考价。",
        "period_assumption": "模拟按日前全日比值确定触发日，触发后各时段使用同一规则。",
    }


def effective_offer(raw_blocks, kind, rated_mw, policy):
    """Validate a frozen declaration and return a separate effective curve.

    Clipping to a reference is a market operation. The resulting price gaps
    need not satisfy the original declaration's bid-segment rules.
    """
    if kind not in CATEGORIES:
        raise ValueError("仅市场化水电、煤电和集中式光伏具备竞价曲线")
    rows = list(raw_blocks)
    tech = "renewable" if kind == "pv" else kind
    coal = {"coal_min_adjustable_mw": rows[0][0], "coal_min_stable_mw": rows[0][0]} if kind == "coal" and rows else {}
    curve = validate_curve(tech, rated_mw, rows, **coal)
    rating = float(rated_mw)
    size = 1000 if kind != "coal" or rating >= 1000 else (600 if rating >= 600 else 300)
    factor = policy["k"] if kind in ("coal", "reservoir") else 1.
    effective = []
    for band in curve:
        lo, hi, price = float(band.start_mw), float(band.end_mw), float(band.price_yuan_mwh)
        if policy["active"]:
            # This synthetic marginal fuel curve is linear, so its endpoint
            # average is exactly its value at the middle of the offered band.
            midpoint = (lo + hi) / (2. * rating)
            ceiling = _marginal_cost(size, midpoint, policy["coal_price_yuan_t"]) * factor
            price = min(price, float(_cent(D(str(ceiling)))))
        effective.append((lo, hi, price))
    return effective


def _optional_float(value):
    return None if value is None else float(value)


def _sum_or_missing(values):
    data = tuple(values)
    return None if any(v is None for v in data) else sum(data, ZERO)


def _read_mwh(mapping, key):
    value = _number(mapping.get(key))
    return value * HOURS if value is not None and value >= 0 else None


def _bus_prices(row, reliable, cap):
    raw, effective, sources = {}, {}, {}
    for key, value in row.get("price_by_bus", {}).items():
        if isinstance(key, bool) or not str(key).isdigit():
            raise ValueError("母线编号需要是整数")
        bus = int(key)
        if bus in raw:
            raise ValueError("同一时段母线编号重复")
        val = _number(value)
        raw[bus] = _optional_float(val)
        effective[bus] = (_cent(max(ZERO, min(cap, val)))
                          if reliable and cap is not None and val is not None else None)
        if effective[bus] is None:
            sources[bus] = "withheld"
        elif val < 0:
            sources[bus] = "rt_sced_at_floor"
        elif val > cap:
            sources[bus] = "rt_sced_at_cap"
        else:
            sources[bus] = "rt_sced"
    return raw, effective, sources


def _evaluate_quarter(q, row, market):
    missing = row is None
    row = {} if missing else row
    cap = _number(row.get("cap"))
    cap = cap if cap is not None and cap >= 0 else None
    reliable = row.get("reliable") is True
    issues = set()
    if missing:
        issues.add("missing_quarter")
    elif not reliable:
        issues.add("dispatch_not_verified")
    if cap is None:
        issues.add("missing_price_cap")
    raw, prices, sources = _bus_prices(row, reliable, cap)
    legs, weights = {}, []
    cleared, metered = row.get("cleared_mw", {}), row.get("metered_mw", {})
    for resource_id, resource in market.items():
        bus, kind = int(resource["bus"]), resource["kind"]
        price = prices.get(bus)
        energy = _read_mwh(metered, resource_id)
        weight = _read_mwh(cleared, resource_id)
        if weight is not None:
            weights.append(UniformPriceInput(resource_id, CATEGORIES[kind], weight, price))
        else:
            issues.add("missing_cleared_quantity")
        amount = None
        leg_issues = set()
        if not reliable:
            leg_issues.add("missing_quarter" if missing else "dispatch_not_verified")
        if energy is None:
            leg_issues.add("missing_metered_quantity")
        elif reliable:
            result = settle_quarter(SettlementInput(q, resource_id, "generator", energy, ZERO, price))
            amount = result.net_amount
            leg_issues.update(result.issues)
        issues.update(leg_issues)
        legs[resource_id] = (energy, amount, leg_issues)
    known_weights = len(weights) == len(market)
    weight_total = sum((w.cleared_delivered_mwh for w in weights), ZERO) if known_weights else None
    uniform = uniform_price(weights) if known_weights and reliable else None
    # All market-cleared energy is eligible in the default closed-area case.
    grid_weight = _number(row.get("grid_cleared_mwh", weight_total))
    if grid_weight is not None and grid_weight < 0:
        grid_weight = None
    if uniform is None and weight_total is not None and weight_total > 0:
        issues.add("unavailable_uniform_price")
    output = {"quarter": q, "reliable": reliable, "raw_price_by_bus": raw,
              "effective_price_by_bus": {k: _optional_float(v) for k, v in prices.items()},
              "cap": _optional_float(cap), "uniform_price": _optional_float(uniform),
              "uniform_weight_mwh": _optional_float(weight_total),
              "price_source": "rt_sced" if any(p is not None for p in prices.values()) else "withheld",
              "price_source_by_bus": sources,
              "issues": sorted(issues)}
    return output, legs, (uniform, grid_weight)


def _hour_row(hour, legs, full):
    energy = _sum_or_missing(leg[0] for leg in legs)
    amount = _sum_or_missing(leg[1] for leg in legs)
    issues = set().union(*(leg[2] for leg in legs))
    if not full:
        issues.add("incomplete_hour")
    gross = amount if full else None
    average = gross / energy if gross is not None and energy else None
    return {"hour": hour, "energy_mwh": _optional_float(energy),
            "rt_nodal_price": _optional_float(average), "revenue": _optional_float(gross),
            "executed_revenue": _optional_float(amount), "issues": sorted(issues)}


def _total(rows, field):
    return _optional_float(_sum_or_missing(_number(row[field]) for row in rows))


def settle_realtime(quarters, resources):
    """Price executed quarters only. MW inputs use the grid connection reference.

    `cleared_mw` weights the uniform price, while independent `metered_mw`
    values settlement. A synthetic meter may equal dispatch by explicit choice
    of the caller. Incomplete-hour revenue is withheld; its executed-quarter
    diagnostic is exposed separately. An interior missing quarter is flagged.
    """
    market, seen = {}, set()
    for resource in resources:
        rid, kind = resource["id"], resource["kind"]
        if not isinstance(rid, str) or not rid.strip() or rid in seen:
            raise ValueError("资源编号空缺或重复")
        seen.add(rid)
        if kind in CATEGORIES:
            market[rid] = resource
        elif kind != "gas":
            raise ValueError(f"未知结算身份 {kind}")
    if not market:
        raise ValueError("至少需要一个市场化资源")
    by_q = {}
    for row in quarters:
        if not isinstance(row, Mapping):
            raise ValueError("时段记录格式无效")
        q = row.get("quarter")
        if type(q) is not int or not 0 <= q < 96 or q in by_q:
            raise ValueError("时段必须是唯一的 0 至 95 整数")
        by_q[q] = row
    evaluated = [_evaluate_quarter(q, by_q.get(q), market) for q in range(max(by_q, default=-1) + 1)]
    members = {rid for rid, r in market.items()
               if r.get("portfolio_member", r["kind"] in ("reservoir", "run_of_river"))}
    hours, portfolio, uniform_hours = [], [], []
    for start in range(0, len(evaluated), 4):
        window = evaluated[start:start + 4]
        hour, full = start // 4 + 1, len(window) == 4
        company_legs = []
        for rid in market:
            legs = [item[1][rid] for item in window]
            hours.append({"resource_id": rid, **_hour_row(hour, legs, full)})
            if rid in members:
                company_legs.extend(legs)
        portfolio.append(_hour_row(hour, company_legs, full))
        weighted = [(p * w if p is not None and w is not None else (ZERO if w == 0 else None))
                    for _, _, (p, w) in window]
        energy = _sum_or_missing(w for _, _, (_, w) in window)
        numerator = _sum_or_missing(weighted)
        verified_hours = full and all(item[0]["reliable"] for item in window)
        price = numerator / energy if verified_hours and energy and numerator is not None else None
        uniform_hours.append({"hour": hour, "uniform_price": _optional_float(price),
                              "grid_cleared_mwh": _optional_float(energy)})
    issues = sorted({i for q, _, _ in evaluated for i in q["issues"]}
                    | {i for h in hours for i in h["issues"]})
    return {
        "quarters": [q for q, _, _ in evaluated], "hourly": hours, "uniform_hourly": uniform_hours,
        "portfolio": portfolio, "eligible": list(market), "portfolio_members": sorted(members),
        "total_revenue": _total(portfolio, "revenue") if portfolio else None,
        "executed_portfolio_revenue": _total(portfolio, "executed_revenue") if portfolio else None,
        "market_total_revenue": _total(hours, "revenue") if hours else None,
        "executed_quarters": len(by_q), "complete_day": len(by_q) == 96,
        "data_quality": "incomplete" if issues or not evaluated else "verified",
        "issues": issues, "scope": "模拟上网电量的实时现货毛收入；未含中长期差价、运行补偿、辅助服务与燃气计划费用。",
    }
