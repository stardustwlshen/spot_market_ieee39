"""Exact convex energy-curve add-on, without altering frozen physical cores.

The add-on replaces only selected, complete named scalar-bid cost families.
It does not solve, add settlement payments, or implement an LMP market. See
../docs/17_多段能量报价迁移.md. All quantities are MW and prices yuan/MWh.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from numbers import Real
from typing import Literal

import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn
from pyomo.core.expr.visitor import identify_variables, replace_expressions

from .gd_horizon import EnergyHorizon


class PiecewiseBidInputError(ValueError):
    """An explicit curve, resource mapping, or replacement contract is invalid."""


@dataclass(frozen=True)
class PiecewiseEnergyBidCurve:
    """Incremental integral curve, with MW breakpoints starting at exactly zero.

    ``unpriced`` requires prices=None, not an inferred zero bid. A flat bid has
    exactly one segment; piecewise requires at least two. Price series cover
    every horizon period, even when that period's availability is zero.
    """

    quantity_breakpoints_mw: tuple[float, ...]
    price_yuan_per_mwh: dict[int, tuple[float, ...]] | None
    direction: Literal["supply", "demand"]
    pricing: Literal["piecewise", "flat", "unpriced"]
    quantity_unit: Literal["MW"]
    price_unit: Literal["yuan/MWh"]
    quantity_source: str
    price_source: str


@dataclass(frozen=True)
class ResourceEnergyBid:
    """A source curve mapped once to a renewable resource or storage direction.

    Renewable target_id is a bus; resource_id identifies the original resource.
    Storage target_id and resource_id both identify the battery or pumped plant.
    A plant bid must never be replicated onto its constituent physical units.
    ``replaced_cost_term`` is the exact fully qualified Pyomo Expression name.
    """

    bid_id: str
    resource_id: str
    target_id: str
    kind: Literal["renewable", "battery_charge", "battery_discharge",
                  "pumped_plant_generation", "pumped_plant_pumping"]
    curve: PiecewiseEnergyBidCurve
    availability_mw: dict[int, float]
    availability_source: str
    mapping_source: str
    replaced_cost_term: str


_KIND = {
    "renewable": ("renewable", "supply"),
    "battery_charge": ("battery", "demand"),
    "battery_discharge": ("battery", "supply"),
    "pumped_plant_generation": ("pumped", "supply"),
    "pumped_plant_pumping": ("pumped", "demand"),
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise PiecewiseBidInputError(f"{name}: explicit nonempty identifier/source required")
    return value


def _number(value, name, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise PiecewiseBidInputError(f"{name}: finite numeric value required")
    if nonnegative and value < 0:
        raise PiecewiseBidInputError(f"{name}: nonnegative value required")
    return float(value)


def _series(value, ids, name):
    if not isinstance(value, dict) or set(value) != set(ids) or any(type(t) is not int for t in value):
        raise PiecewiseBidInputError(f"{name}: exact integer period coverage required")
    return {t: _number(value[t], name, nonnegative=True) for t in ids}


def _equal(left, right):
    return abs(left-right) <= 1e-8 + 1e-10*max(abs(left), abs(right))


def _curve(curve, ids, expected_direction, availability, name):
    if not isinstance(curve, PiecewiseEnergyBidCurve):
        raise PiecewiseBidInputError(f"{name}: typed PiecewiseEnergyBidCurve required")
    if curve.quantity_unit != "MW" or curve.price_unit != "yuan/MWh":
        raise PiecewiseBidInputError(f"{name}: explicit MW and yuan/MWh units required")
    _text(curve.quantity_source, name+".quantity_source")
    _text(curve.price_source, name+".price_source")
    if curve.direction != expected_direction:
        raise PiecewiseBidInputError(f"{name}: source direction does not match resource kind")
    if curve.pricing not in {"piecewise", "flat", "unpriced"}:
        raise PiecewiseBidInputError(f"{name}: explicit pricing classification required")
    if not isinstance(curve.quantity_breakpoints_mw, tuple) or len(curve.quantity_breakpoints_mw) < 2:
        raise PiecewiseBidInputError(f"{name}: immutable nonempty breakpoint tuple required")
    points = tuple(_number(v, name+".breakpoint", nonnegative=True) for v in curve.quantity_breakpoints_mw)
    if points[0] != 0 or any(hi <= lo for lo, hi in zip(points, points[1:])):
        raise PiecewiseBidInputError(f"{name}: strictly increasing breakpoints from zero required")
    widths = tuple(hi-lo for lo, hi in zip(points, points[1:]))
    n = len(widths)
    if (curve.pricing == "flat" and n != 1) or (curve.pricing == "piecewise" and n < 2):
        raise PiecewiseBidInputError(f"{name}: flat/piecewise segment count mismatch")
    if curve.pricing == "unpriced":
        if curve.price_yuan_per_mwh is not None:
            raise PiecewiseBidInputError(f"{name}: explicit unpriced curve must have prices=None")
        prices = {t: (0.,)*n for t in ids}
    else:
        raw = curve.price_yuan_per_mwh
        if not isinstance(raw, dict) or set(raw) != set(ids) or any(type(t) is not int for t in raw):
            raise PiecewiseBidInputError(f"{name}: exact price period coverage required")
        prices = {}
        for t in ids:
            if not isinstance(raw[t], tuple) or len(raw[t]) != n:
                raise PiecewiseBidInputError(f"{name}[{t}]: exactly one explicit price per segment required")
            values = tuple(_number(v, name+".price") for v in raw[t])
            if (expected_direction == "supply" and any(a > b for a, b in zip(values, values[1:]))) or (
                    expected_direction == "demand" and any(a < b for a, b in zip(values, values[1:]))):
                raise PiecewiseBidInputError(f"{name}[{t}]: nonconvex curve unsupported; supply must be nondecreasing and demand nonincreasing")
            prices[t] = values
    # No source quantity is scaled or reordered as availability varies.
    for t in ids:
        if availability[t] > points[-1]:
            raise PiecewiseBidInputError(f"{name}[{t}]: curve width does not cover available MW")
        for price in prices[t]:
            if not math.isfinite(price*points[-1]):
                raise PiecewiseBidInputError(f"{name}: finite full-curve cost required")
    clipped = {t: tuple(min(width, max(0., availability[t]-lo)) for width, lo in zip(widths, points)) for t in ids}
    return {"breakpoints_mw": points, "widths_mw": widths, "prices_yuan_per_mwh": prices,
            "clipped_widths_mw": clipped, "availability_mw": availability}


def _occurrences(expression, target):
    """Count live named references, rather than merely equal flattened algebra."""
    if expression is target:
        return 1
    return sum(_occurrences(arg, target) for arg in getattr(expression, "args", ()))


def _find_reference(expression, identities, *, stop_at=None):
    """Find object identity in the raw graph, without evaluating fixed/zero terms.

    Named Expressions are traversed, except the one explicitly allowed hook.
    This intentionally does not use a standard representation: coefficient
    cancellation, fixed-zero variables, and zero-valued mutable parameters must
    not hide an unsafe second reference before a later dispatch/unfix/update.
    """
    stack, seen = [expression], set()
    while stack:
        node = stack.pop()
        if node is stop_at:
            continue
        identity = id(node)
        if identity in identities:
            return node
        if identity not in seen:
            seen.add(identity)
            stack.extend(getattr(node, "args", ()))
    return None


def _check_hook_isolation(model, sources):
    """Cost mutation must not alter any existing row or variable bound.

    Inspect the raw relational constraint, which includes both sides and any
    ranged bounds. Reading canonical body/lower/upper can itself fail for a
    currently variable ranged bound, or lose the original expression shape.
    Inactive constraints are checked too: later activation cannot safely change
    the meaning of a supposedly cost-only attachment. Symbolic variable lower/
    upper properties, unlike numeric lb/ub, retain bound dependencies.
    """
    hooks = {id(expression) for expression in sources.values()}
    for constraint in model.model().component_data_objects(pyo.Constraint, active=None, descend_into=True):
        found = _find_reference(constraint.expr, hooks)
        if found is not None:
            raise PiecewiseBidInputError(
                f"{found.name}: source cost hook is referenced by existing constraint {constraint.name}")
    for variable in model.model().component_data_objects(pyo.Var, active=None, descend_into=True):
        for side in ("lower", "upper"):
            try:
                bound = getattr(variable, side)
            except Exception as exc:
                raise PiecewiseBidInputError(
                    f"{variable.name}: unsupported symbolic variable {side} bound") from exc
            found = _find_reference(bound, hooks)
            if found is not None:
                raise PiecewiseBidInputError(
                    f"{found.name}: source cost hook is referenced by variable {variable.name} {side} bound")


def _linear_signature(expression):
    # Fixed values are a dispatch state, not evidence of the original bid
    # coefficients. Replace only fixed variables by unfixed symbolic probes,
    # without changing the original model or losing the original identities.
    substitutions, original_ids = {}, {}
    for variable in identify_variables(expression, include_fixed=True):
        if variable.fixed:
            marker = pyo.Var()
            marker.construct()
            substitutions[id(variable)] = marker
            original_ids[id(marker)] = id(variable)
    symbolic = replace_expressions(expression, substitutions) if substitutions else expression
    repn = generate_standard_repn(symbolic, compute_values=True)
    if not repn.is_linear():
        raise PiecewiseBidInputError("source cost must retain its original linear scalar-bid expression")
    coeffs = {original_ids.get(id(v), id(v)): float(c) for v, c in zip(repn.linear_vars, repn.linear_coefs) if c != 0}
    constant = float(repn.constant or 0.)
    if not all(math.isfinite(v) for v in (constant, *coeffs.values())):
        raise PiecewiseBidInputError("source cost has nonfinite coefficients")
    return constant, coeffs


def _check_source(expression, expected, objective, physical_powers):
    if expression.ctype is not pyo.Expression or expression.is_indexed() or not expression.active:
        raise PiecewiseBidInputError("replacement target must be an active scalar Expression")
    if _occurrences(objective.expr, expression) != 1:
        raise PiecewiseBidInputError(f"{expression.name}: exactly one live objective reference required; no flattened/de-duplicated cost hook")
    outside = _find_reference(objective.expr, {id(power) for power in physical_powers}, stop_at=expression)
    if outside is not None:
        raise PiecewiseBidInputError(
            f"{expression.name}: physical power {outside.name} occurs outside its sole named cost hook; "
            "flattened duplicate or additional power-dependent objective unsupported")
    marker = pyo.Var()
    marker.construct()
    substituted = replace_expressions(objective.expr, {id(expression): marker})
    repn = generate_standard_repn(substituted, compute_values=True)
    weight = next((float(c) for v, c in zip(repn.linear_vars, repn.linear_coefs) if v is marker), None)
    if not repn.is_linear() or weight != 1.:
        raise PiecewiseBidInputError(f"{expression.name}: objective must include the named source cost with coefficient +1")
    if _linear_signature(expression.expr) != _linear_signature(expected):
        raise PiecewiseBidInputError(f"{expression.name}: source scalar cost was changed or mismatched")


def attach_piecewise_energy_bids(model, *, core, horizon, storage=None,
                                 renewable_bids=None, battery_bids=None,
                                 pumped_plant_bids=None, block_name="gd_piecewise_bids"):
    """Atomically replace complete cost families, returning an unsolved Block.

    None leaves that family untouched; a tuple opts in and must cover every
    existing resource/direction. No physical constraints or injection ports are
    mutated. Monetary terms replace legacy scalar costs, never add to them.
    """
    if not isinstance(horizon, EnergyHorizon):
        raise PiecewiseBidInputError("explicit EnergyHorizon required")
    horizon.validate()
    if horizon != getattr(core, "_energy_horizon", None):
        raise PiecewiseBidInputError("complete horizon contract must match energy core")
    if core.model() is not model.model():
        raise PiecewiseBidInputError("energy core and target must belong to the same model")
    if not isinstance(block_name, str) or not block_name or hasattr(model, block_name):
        raise PiecewiseBidInputError("block name invalid or already attached")
    families = {name: values for name, values in (("renewable", renewable_bids), ("battery", battery_bids),
                                                ("pumped", pumped_plant_bids)) if values is not None}
    if not families:
        raise PiecewiseBidInputError("at least one explicit bid family required")
    if any(not isinstance(records, tuple) for records in families.values()):
        raise PiecewiseBidInputError("bid families must be explicit immutable tuples")
    if any(name in families for name in ("battery", "pumped")):
        if storage is None or storage.model() is not model.model() or getattr(storage, "_storage_horizon", None) != horizon:
            raise PiecewiseBidInputError("attached storage with identical complete horizon required")
        if not hasattr(storage, "_storage_inputs"):
            raise PiecewiseBidInputError("storage must expose original explicit physical inputs")
    objectives = list(model.model().component_data_objects(pyo.Objective, active=True, descend_into=True))
    if len(objectives) != 1 or objectives[0].sense != pyo.minimize:
        raise PiecewiseBidInputError("exactly one active minimization objective required")
    objective = objectives[0]
    ids, duration = horizon.ids, horizon.duration_h
    sources, expected, scalar_inputs, physical_powers = {}, {}, {}, {}
    if "renewable" in families:
        if not hasattr(core, "_energy_inputs") or not hasattr(core, "renewable_bid_cost"):
            raise PiecewiseBidInputError("renewable family needs explicit original energy-core inputs")
        sources["renewable"] = core.renewable_bid_cost
        physical_powers["renewable"] = tuple(core.renewable_power.values())
        scalar_inputs["renewable"] = deepcopy(core._energy_inputs["renewable_prices"]["bid"])
        expected["renewable"] = sum(duration[t]*scalar_inputs["renewable"][b, t]*core.renewable_power[b, t] for b in core.B for t in ids)
    if "battery" in families:
        sources["battery"] = storage.battery_bid_cost
        physical_powers["battery"] = (*storage.charge.values(), *storage.discharge.values())
        scalar_inputs["battery"] = {u: {k: deepcopy(r[k]) for k in ("charge_bid_yuan_per_mwh", "discharge_bid_yuan_per_mwh")}
                                    for u, r in storage._storage_inputs["batteries"].items()}
        expected["battery"] = sum(duration[t]*(r["discharge_bid_yuan_per_mwh"][t]*storage.discharge[u, t]
                                               - r["charge_bid_yuan_per_mwh"][t]*storage.charge[u, t])
                                  for u, r in scalar_inputs["battery"].items() for t in ids)
    if "pumped" in families:
        sources["pumped"] = storage.pumped_bid_cost
        physical_powers["pumped"] = (*storage.generate.values(), *storage.pump.values())
        scalar_inputs["pumped"] = {u: {k: deepcopy(r[k]) for k in ("generation_bid_yuan_per_mwh", "pump_bid_yuan_per_mwh")}
                                   for u, r in storage._storage_inputs["units"].items()}
        expected["pumped"] = sum(duration[t]*(r["generation_bid_yuan_per_mwh"][t]*storage.generate[u, t]
                                              - r["pump_bid_yuan_per_mwh"][t]*storage.pump[u, t])
                                 for u, r in scalar_inputs["pumped"].items() for t in ids)
    previous = set(getattr(core, "_gd_piecewise_replaced_costs", set()))
    if previous & {term.name for term in sources.values()}:
        raise PiecewiseBidInputError("source cost family already replaced; duplicate attachment rejected")
    _check_hook_isolation(model, sources)
    for family, expression in sources.items():
        _check_source(expression, expected[family], objective, physical_powers[family])

    previous_bid_ids = set(getattr(core, "_gd_piecewise_bid_ids", set()))
    previous_resource_families = dict(getattr(core, "_gd_piecewise_resource_families", {}))
    resource_families = dict(previous_resource_families)
    raw_records, records, resource_keys, targets = [], {}, set(), {}
    family_ids = {family: [] for family in families}
    for family, originals in families.items():
        for original in originals:
            if not isinstance(original, ResourceEnergyBid):
                raise PiecewiseBidInputError("typed ResourceEnergyBid records required")
            record = deepcopy(original)
            for attr in ("bid_id", "resource_id", "target_id", "availability_source", "mapping_source", "replaced_cost_term"):
                _text(getattr(record, attr), attr)
            if record.kind not in _KIND or _KIND[record.kind][0] != family:
                raise PiecewiseBidInputError("resource kind/family mismatch; pumped-unit bids are unsupported")
            if record.bid_id in previous_bid_ids or record.bid_id in records or (record.kind, record.resource_id) in resource_keys:
                raise PiecewiseBidInputError("duplicate bid or resource/direction ID")
            if record.resource_id in resource_families and resource_families[record.resource_id] != family:
                raise PiecewiseBidInputError("duplicate resource ID across different physical resource families")
            resource_families[record.resource_id] = family
            if record.replaced_cost_term != sources[family].name:
                raise PiecewiseBidInputError("replaced_cost_term does not match the named source Expression")
            if family != "renewable" and record.resource_id != record.target_id:
                raise PiecewiseBidInputError("storage resource_id must equal physical battery/plant target_id")
            available = _series(record.availability_mw, ids, record.bid_id+".availability")
            normalized = _curve(record.curve, ids, _KIND[record.kind][1], available, record.bid_id)
            if any(not math.isfinite(duration[t]*price*normalized["breakpoints_mw"][-1])
                   for t in ids for price in normalized["prices_yuan_per_mwh"][t]):
                raise PiecewiseBidInputError("finite duration-weighted curve cost required")
            target = record.target_id
            if family == "renewable":
                if target not in core.B:
                    raise PiecewiseBidInputError("renewable target bus absent from energy core")
                target_refs = {t: core.renewable_power[target, t] for t in ids}
            elif family == "battery":
                data = storage._storage_inputs["batteries"].get(target)
                if data is None:
                    raise PiecewiseBidInputError("battery target absent from storage")
                direction = "charge" if record.kind == "battery_charge" else "discharge"
                if any(not _equal(available[t], data[direction+"_max_mw"][t]) for t in ids):
                    raise PiecewiseBidInputError("battery availability must equal original physical direction maximum")
                target_refs = {t: getattr(storage, direction)[target, t] for t in ids}
            else:
                data = storage._storage_inputs["plants"].get(target)
                if data is None:
                    raise PiecewiseBidInputError("pumped target must identify a whole plant, never a physical unit")
                units = storage._storage_inputs["units"]
                generating = record.kind == "pumped_plant_generation"
                first = units[data["members"][0]]
                for t in ids:
                    count = min(data["max_generating_units" if generating else "max_pumping_units"][t],
                                data["max_online_units"][t], sum(units[u]["generation_available" if generating else "pump_available"][t] for u in data["members"]))
                    physical = count*(first["generation_max_mw"][t] if generating else first["pump_power_mw"])
                    if not _equal(available[t], physical):
                        raise PiecewiseBidInputError("pumped availability must equal original whole-plant physical maximum")
                variable = storage.plant_generation if generating else storage.plant_pumping
                target_refs = {t: variable[target, t] for t in ids}
            normalized.update(record=record, family=family, objective_sign=1 if record.curve.direction == "supply" else -1)
            records[record.bid_id], targets[record.bid_id] = normalized, target_refs
            resource_keys.add((record.kind, record.resource_id))
            family_ids[family].append(record.bid_id)
            raw_records.append(record)

    if "renewable" in families:
        for bus in core.B:
            for t in ids:
                total = math.fsum(records[k]["availability_mw"][t] for k in family_ids["renewable"] if records[k]["record"].target_id == bus)
                if not _equal(total, float(pyo.value(core.renewable_available[bus, t]))):
                    raise PiecewiseBidInputError("renewable resource availability must exactly cover each original bus total")
    for family, keys, kinds in (("battery", "batteries", ("battery_charge", "battery_discharge")),
                                ("pumped", "plants", ("pumped_plant_generation", "pumped_plant_pumping"))):
        if family in families:
            wanted = {(kind, uid) for uid in storage._storage_inputs[keys] for kind in kinds}
            actual = {(records[k]["record"].kind, records[k]["record"].target_id) for k in family_ids[family]}
            if actual != wanted:
                raise PiecewiseBidInputError(f"{family}: every physical resource and both directions must be covered")

    # Detached construction: no external mutation until every validation passes.
    block = pyo.Block(concrete=True)
    block.T = pyo.Set(initialize=ids, ordered=True)
    block.BID = pyo.Set(initialize=tuple(records), ordered=True)
    block.FAMILY = pyo.Set(initialize=tuple(families), ordered=True)
    index = [(k, s, t) for k, r in records.items() for s in range(len(r["widths_mw"])) for t in ids]
    block.SEGMENT = pyo.Set(dimen=3, initialize=index, ordered=True)
    block.segment_power = pyo.Var(block.SEGMENT, domain=pyo.NonNegativeReals,
                                 bounds=lambda _, k, s, t: (0., records[k]["clipped_widths_mw"][t][s]), initialize=0.)
    block.resource_power = pyo.Expression(block.BID, block.T,
        rule=lambda _, k, t: sum(block.segment_power[k, s, t] for s in range(len(records[k]["widths_mw"]))))
    block.bid_term = pyo.Expression(block.BID, block.T,
        rule=lambda _, k, t: records[k]["objective_sign"]*duration[t]*sum(
            records[k]["prices_yuan_per_mwh"][t][s]*block.segment_power[k, s, t] for s in range(len(records[k]["widths_mw"]))))
    block.family_cost = pyo.Expression(block.FAMILY,
        rule=lambda _, f: sum(block.bid_term[k, t] for k in family_ids[f] for t in ids))
    block.cost_expression = pyo.Expression(expr=sum(block.family_cost[f] for f in families))
    block.c_power_link = pyo.ConstraintList()
    for k, r in records.items():
        if r["family"] != "renewable":
            for t in ids:
                block.c_power_link.add(block.resource_power[k, t] == targets[k][t])
    if "renewable" in families:
        for bus in core.B:
            for t in ids:
                block.c_power_link.add(sum(block.resource_power[k, t] for k in family_ids["renewable"]
                                          if records[k]["record"].target_id == bus) == core.renewable_power[bus, t])
    block.power_refs = {k: {t: block.resource_power[k, t] for t in ids} for k in records}
    block.target_power_refs = targets
    block._piecewise_horizon = horizon
    block._piecewise_raw_bids = tuple(raw_records)
    block.audit = {
        "formulation": "convex_incremental_LP_blocks", "cost_exact_at_minimum": True,
        "arbitrary_segment_allocations_are_not_certified": True,
        "raw_bids": [asdict(r) for r in raw_records],
        "normalized_curves": {k: {key: deepcopy(value) for key, value in r.items() if key != "record"} for k, r in records.items()},
        "horizon": horizon.audit(), "duration_h": dict(duration), "cost_unit": "yuan",
        "replaced_cost_terms": {family: expression.name for family, expression in sources.items()},
        "original_scalar_prices": {family: ({bus: {t: scalar_inputs[family][bus, t] for t in ids} for bus in core.B}
                                            if family == "renewable" else scalar_inputs[family]) for family in families},
        "target_power_references": {k: {t: expression.name for t, expression in refs.items()} for k, refs in targets.items()},
        "pumped_mapping": {k: {"plant_id": r["record"].target_id,
            "physical_units": tuple(storage._storage_inputs["plants"][r["record"].target_id]["members"]),
            "curve_attached_once_at_plant": True} for k, r in records.items() if r["family"] == "pumped"},
        "scalar_costs_replaced_not_added": True, "physical_constraints_changed": False,
        "cost_hook_isolation": {"raw_expression_graph_checked": True,
            "fixed_variables_and_zero_terms_not_evaluated_away": True,
            "physical_power_objective_references_exclusive_to_named_hook": True,
            "existing_constraints_including_inactive_and_variable_bounds_hook_free": True},
        "settlement_or_service_payments_in_cost": False, "full_guangdong_equivalence_verified": False,
        "lmp_integration_implemented": False, "thermal_bids_changed": False,
    }
    snapshots = {family: expression.expr for family, expression in sources.items()}
    registry_snapshots = {name: (hasattr(core, name), deepcopy(getattr(core, name, None))) for name in (
        "_gd_piecewise_replaced_costs", "_gd_piecewise_bid_ids", "_gd_piecewise_resource_families")}
    try:
        model.add_component(block_name, block)
        for family, expression in sources.items():
            expression.set_value(block.family_cost[family])
        core._gd_piecewise_replaced_costs = previous | {expression.name for expression in sources.values()}
        core._gd_piecewise_bid_ids = previous_bid_ids | set(records)
        core._gd_piecewise_resource_families = resource_families
    except Exception:
        for family, expression in sources.items():
            if expression.expr is not snapshots[family]:
                expression.set_value(snapshots[family])
        if model.component(block_name) is block:
            model.del_component(block_name)
        for name, (existed, value) in registry_snapshots.items():
            if existed:
                setattr(core, name, value)
            elif hasattr(core, name):
                delattr(core, name)
        raise
    return block
