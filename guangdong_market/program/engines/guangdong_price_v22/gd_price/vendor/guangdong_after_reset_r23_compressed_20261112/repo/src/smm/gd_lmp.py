"""Evidence-explicit, isolated Guangdong-reference LMP LP transformation.

This is a component, NOT a complete Guangdong market or an adapter automatically
wired to the legacy SCED. See rebuild docs/06_广东独立LMP适配器数学规范.md.
The published LMP formulation still contains storage/pumped binary states.
Fixing selected states and taking LP duals is a research implementation; the
complete official integer-pricing algorithm remains unresolved.
Signed eligible charging/pumping is rejected by default. An explicit, audited
signed-magnitude research interpretation is optional, not official clarification.
No price clipping occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping

import pyomo.environ as pyo
from pyomo.core.base.componentuid import ComponentUID
from pyomo.core.expr.visitor import identify_mutable_parameters, identify_variables, replace_expressions
from pyomo.repn import generate_standard_repn

from .highs_solver import HighsSolver


class PricingInputError(ValueError):
    """Missing, inconsistent, unsupported or unreviewed pricing contract."""


class UnsupportedPricingCase(PricingInputError):
    """The component deliberately has no verified implementation for this case."""


@dataclass(frozen=True)
class Period:
    key: str
    duration_h: float
    source: str


@dataclass(frozen=True)
class PowerPoint:
    resource: str
    period: str
    kind: str
    power_ref: str
    eligible: bool
    online: bool
    sced_lower_mw: float
    sced_upper_mw: float
    boundary_source: str
    eligibility_source: str
    boundary_basis: str
    zero_output_policy: str | None = None
    zero_output_source: str | None = None
    negative_output_policy: str | None = None
    negative_output_source: str | None = None


@dataclass(frozen=True)
class BidTerm:
    power_ref: str
    period: str
    yuan_per_mwh: float
    source: str


@dataclass(frozen=True)
class PenaltyParameter:
    category: str
    yuan_per_mwh: float
    source: str


@dataclass(frozen=True)
class PenaltyTerm:
    slack_ref: str
    period: str
    category: str
    source: str
    # A line/section and its two directional MW slacks must be distinct.
    family: str | None = None
    element: str | None = None
    direction: str | None = None


@dataclass(frozen=True)
class PriceRow:
    node: str
    period: str
    constraint_ref: str
    load_parameter_ref: str


@dataclass(frozen=True)
class ConstraintReview:
    constraint_ref: str
    role: str
    source: str


@dataclass(frozen=True)
class FixedVariableSource:
    variable_ref: str
    source: str


@dataclass(frozen=True)
class OmittedDispatchRow:
    constraint_ref: str
    kind: str
    source: str


@dataclass(frozen=True)
class RTFixedInventory:
    """Selected ED four-direction inventory slack, in m3 (not an MW penalty).

    This fixes variables in the clone only. It does not replace the upstream
    band geometry or certify that upstream rows match a later rule edition.
    """
    slack_ref: str
    period: str
    selected_value_m3: float
    source: str


@dataclass(frozen=True)
class PricingSpec:
    stage: str
    delta: float
    delta_source: str
    periods: tuple[Period, ...]
    powers: tuple[PowerPoint, ...]
    # This upstream inventory must be the COMPLETE dispatch injection inventory.
    # We can check exact mapping, not certify the caller's physical completeness.
    dispatch_inventory: tuple[str, ...]
    bid_terms: tuple[BidTerm, ...]
    penalty_parameters: tuple[PenaltyParameter, ...]
    penalty_terms: tuple[PenaltyTerm, ...]
    price_rows: tuple[PriceRow, ...]
    constraint_reviews: tuple[ConstraintReview, ...]
    fixed_variable_sources: tuple[FixedVariableSource, ...]
    source_scope: str
    coverage_limitations: tuple[str, ...]
    omitted_dispatch_rows: tuple[OmittedDispatchRow, ...] = ()
    rt_fixed_inventory: tuple[RTFixedInventory, ...] = ()

    def validate_source_snapshot(self, source):
        """Optional pure adapter validation; base specs retain existing behavior.

        Specialized specs may reject stale source identity. They must not change
        the source or refresh an earlier snapshot to hide changes.
        """
        return None


@dataclass
class PricingLP:
    model: Any
    spec: PricingSpec
    power_bounds: tuple[dict, ...]
    # d(body-rhs)/d(load) of the one allowed load-dependent equality.
    load_residual_signs: dict[tuple[str, str], int]


@dataclass(frozen=True)
class PricingResult:
    raw_lmp_yuan_per_mwh: dict[tuple[str, str], float]
    row_dual_yuan_per_mw: dict[tuple[str, str], float]
    objective_yuan: float
    termination_condition: str
    backend: str
    power_bounds: tuple[dict, ...]
    audit: dict
    final_lmp_yuan_per_mwh: None = None
    final_price_status: str = "not_implemented"


_ROLES = {"physical", "market_boundary", "operating_state", "nodal_balance"}
_KINDS = {"thermal", "renewable", "storage", "pumped"}
_BASE_LIMITATIONS = (
    "component_only_not_complete_Guangdong_dispatch_or_settlement",
    "fixed_state_pricing_is_research_implementation_full_integer_pricing_algorithm_unresolved",
    "upstream_constraint_and_inventory_reviews_are_declarations_not_certification",
    "eligible_negative_charging_pumping_uses_only_explicit_unverified_research_interpretation",
    "DA_98_point_representative_duration_and_RT_reference_wording_not_resolved",
    "scarcity_increment_special_node_assignment_and_final_price_rules_not_implemented",
)


def pricing_algorithm_audit(stage: str) -> dict:
    """Read-only provenance, available without constructing or solving a model.

    The published DA/RT LMP equations retain binary storage/pumped modes. This
    component's selected-state LP is a technical research implementation, not
    evidence that the original official pricing formulation is a pure LP.
    """
    if stage not in {"DA", "RT"}:
        raise PricingInputError("stage must explicitly be DA or RT")
    return dict(
        fixed_state_pricing_algorithm_status="research_implementation",
        full_integer_pricing_algorithm_status="unresolved",
        pricing_algorithm="fixed_selected_dispatch_states_then_independent_linear_pricing",
        lp_dual_interpretation="conditional_marginal_given_selected_integer_states",
        official_full_pricing_algorithm_claimed=False,
        dispatch_to_pricing_stage_note=(
            "upstream_dispatch_MIP_and_same_state_continuous_refinement_are_distinct; "
            "refinement_optimality_does_not_certify_global_dispatch_MIP_optimality"),
        pricing_algorithm_sources=[dict(
            document="Guangdong spot energy trading implementation rules (2026 revision)",
            section="5.8.2.3" if stage == "DA" else "6.6.2.2",
            printed_pages=[75, 77, 78] if stage == "DA" else [121, 122],
            locator="https://nxepta.com/attached/file/20260205/20260205011253766.pdf",
            reviewed_observation="LMP retains storage alpha/beta and pumped Pump/Drain binary states",
            unresolved_question="published text does not specify the complete fixed-integer/dual extraction algorithm",
        )],
    )


def _number(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PricingInputError(f"{label}: finite numeric value required, not a string/bool")
    try:
        val = float(value)
    except (TypeError, ValueError) as exc:
        raise PricingInputError(f"{label}: finite numeric value required") from exc
    if not math.isfinite(val) or (minimum is not None and val < minimum):
        raise PricingInputError(f"{label}: finite value >= {minimum} required")
    return val


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PricingInputError(f"{label}: explicit nonempty source/identifier required")
    return value


def _unique(items, key, label):
    result = {}
    for item in items:
        k = key(item)
        if k in result:
            raise PricingInputError(f"{label}: duplicate {k}")
        result[k] = item
    return result


def _ref(model, reference, ctype=None):
    _text(reference, "component reference")
    try:
        item = ComponentUID(reference).find_component_on(model)
    except Exception as exc:
        raise PricingInputError(f"invalid component reference {reference}") from exc
    if item is None or item.is_indexed() or (ctype is not None and item.ctype is not ctype):
        raise PricingInputError(f"missing/wrong scalar component: {reference}")
    return item


def _linear_quantity(model, reference):
    item = _ref(model, reference)
    if item.ctype not in (pyo.Var, pyo.Expression):
        raise PricingInputError(f"power reference must identify a Var/Expression: {reference}")
    repn = generate_standard_repn(item, compute_values=True)
    if not repn.is_linear():
        raise UnsupportedPricingCase(f"nonlinear power expression: {reference}")
    return item


def _param_ids(expr):
    return {id(p) for p in identify_mutable_parameters(expr)}


def _assert_supported_structure(model):
    """HiGHS's row serializer must never silently omit an active constraint type."""
    supported = {pyo.Block, pyo.Set, pyo.RangeSet, pyo.Param, pyo.Var,
                 pyo.Constraint, pyo.Expression, pyo.Objective, pyo.Suffix}
    for component in model.component_objects(active=True, descend_into=True):
        if component.ctype not in supported:
            raise UnsupportedPricingCase(
                f"unsupported active LP structure {component.ctype.__name__}: {component.name}; "
                "SOS/logical/GDP/complementarity structures must not be silently omitted")


def _source_power_bound_audit(quantity, declared_lower, declared_upper):
    """Check ALL resource branches against affine source-variable bound ranges.

    Fixed values are separately audited, not substituted for original physical
    bounds. Constraint-defined tighter ranges still need upstream row review.
    """
    variables = list(identify_variables(quantity, include_fixed=True))
    substitutions = {}
    details = []
    for var in variables:
        lb = None if var.lb is None else _number(pyo.value(var.lb), "source variable lower")
        ub = None if var.ub is None else _number(pyo.value(var.ub), "source variable upper")
        details.append(dict(variable_ref=str(ComponentUID(var)), lower_mw=lb, upper_mw=ub,
                            fixed=var.fixed, fixed_value_mw=pyo.value(var) if var.fixed else None))
        if var.fixed:
            # Do not let generate_standard_repn absorb this physical variable
            # into a constant, hiding its lb/ub behind its current fixed value.
            symbolic = pyo.Var(bounds=(lb, ub), initialize=pyo.value(var))
            symbolic.construct()
            substitutions[id(var)] = symbolic
    expression = replace_expressions(quantity, substitutions) if substitutions else quantity
    repn = generate_standard_repn(expression, compute_values=True)
    if not repn.is_linear():
        raise UnsupportedPricingCase("power expression must remain affine when fixed power variables are exposed")
    lower = upper = _number(pyo.value(repn.constant), "power expression constant")
    for var, coef in zip(repn.linear_vars, repn.linear_coefs):
        coef = _number(pyo.value(coef), "power expression coefficient")
        if coef == 0:
            continue
        lb = -math.inf if var.lb is None else pyo.value(var.lb)
        ub = math.inf if var.ub is None else pyo.value(var.ub)
        lower += coef*(lb if coef > 0 else ub)
        upper += coef*(ub if coef > 0 else lb)
    if declared_lower < lower-1e-7 or declared_upper > upper+1e-7:
        raise PricingInputError("declared effective SCED bounds exceed source power variable bounds")
    return dict(source_variable_bound_lower_mw=None if math.isinf(lower) else lower,
                source_variable_bound_upper_mw=None if math.isinf(upper) else upper,
                source_power_variable_bounds=details,
                bound_check_scope="affine_variable_bound_outer_range_not_constraint_projection")


def _check_source(source, spec, result):
    _assert_supported_structure(source)
    status = str(getattr(getattr(result, "solver", None), "termination_condition", ""))
    if status != "optimal":
        raise PricingInputError(f"input fixed-state SCED refinement must have an optimal result, received {status!r}")
    objectives = list(source.component_data_objects(pyo.Objective, active=True, descend_into=True))
    if len(objectives) != 1 or objectives[0].sense != pyo.minimize:
        raise PricingInputError("input fixed-state SCED refinement must have exactly one active minimization objective")
    if not generate_standard_repn(objectives[0].expr).is_linear():
        raise UnsupportedPricingCase("nonlinear source objective")
    objective = _number(pyo.value(objectives[0]), "SCED objective")
    problem = getattr(result, "problem", None)
    for bound in ("lower_bound", "upper_bound"):
        reported = _number(getattr(problem, bound, None), f"SCED result {bound}")
        if not math.isclose(objective, reported, rel_tol=1e-7, abs_tol=1e-5):
            raise PricingInputError("SCED objective does not match its optimal solve result")
    reviews = _unique(spec.constraint_reviews, lambda r: r.constraint_ref, "constraint reviews")
    constraints = list(source.component_data_objects(pyo.Constraint, active=True, descend_into=True))
    actual = {str(ComponentUID(c)): c for c in constraints}
    if set(reviews) != set(actual):
        raise PricingInputError("constraint reviews must exactly cover every active SCED row")
    for name, review in reviews.items():
        if review.role not in _ROLES:
            raise UnsupportedPricingCase(f"unreviewed/temporary constraint role {review.role}: {name}")
        _text(review.source, f"constraint source {name}")
    fixed_sources = _unique(spec.fixed_variable_sources, lambda r: r.variable_ref, "fixed variable sources")
    fixed_actual = set()
    tol = 1e-5
    for var in source.component_data_objects(pyo.Var, descend_into=True):
        name = str(ComponentUID(var))
        value = _number(var.value, f"SCED primal {name}")
        if var.is_integer() and not var.fixed:
            raise UnsupportedPricingCase(f"unfixed integer state {name}; no implicit fixing or LP relaxation")
        if var.is_integer() and abs(value-round(value)) > tol:
            raise PricingInputError(f"nonintegral fixed SCED state {name}")
        if var.fixed:
            fixed_actual.add(name)
        if var.lb is not None and value < pyo.value(var.lb)-tol:
            raise PricingInputError(f"infeasible SCED variable lower bound {name}")
        if var.ub is not None and value > pyo.value(var.ub)+tol:
            raise PricingInputError(f"infeasible SCED variable upper bound {name}")
    if set(fixed_sources) != fixed_actual:
        raise PricingInputError("fixed_variable_sources must exactly cover all fixed SCED variables")
    for row in fixed_sources.values():
        _text(row.source, f"fixed variable source {row.variable_ref}")
    for con in constraints:
        if not generate_standard_repn(con.body).is_linear():
            raise UnsupportedPricingCase(f"nonlinear source constraint {con.name}")
        body = _number(pyo.value(con.body), f"SCED row {con.name}")
        if con.lower is not None and body < pyo.value(con.lower)-tol:
            raise PricingInputError(f"infeasible SCED constraint {con.name}")
        if con.upper is not None and body > pyo.value(con.upper)+tol:
            raise PricingInputError(f"infeasible SCED constraint {con.name}")


@dataclass(frozen=True)
class _LoadParameterUses:
    constraint_ids: frozenset[int]
    variable_bounds: frozenset[tuple[int, str]]
    objective_ids: frozenset[int]


@dataclass(frozen=True)
class _LoadDependencyIndex:
    # A private snapshot for one structurally unchanged pricing clone. Never
    # cache this on a model/PricingLP or accept it through a public build API.
    model: Any
    uses: Mapping[int, _LoadParameterUses]


def _index_load_dependencies(model):
    """Inspect every original symbolic scope once, without evaluating Params.

    Keep mutable-parameter support even in zero terms, named expressions, and
    fixed-variable terms. In particular, var.bounds would evaluate away support:
    inspect var.lower/upper, just as the per-row validator does.
    """
    uses = {}

    def record(expr, kind, reference):
        if expr is not None:
            for pid in _param_ids(expr):
                uses.setdefault(pid, (set(), set(), set()))[kind].add(reference)

    for con in model.component_data_objects(pyo.Constraint, active=True, descend_into=True):
        for expr in (con.body, con.lower, con.upper):
            record(expr, 0, id(con))
    for var in model.component_data_objects(pyo.Var, descend_into=True):
        for side, expr in (("lower", var.lower), ("upper", var.upper)):
            record(expr, 1, (id(var), side))
    for obj in model.component_data_objects(pyo.Objective, active=True, descend_into=True):
        record(obj.expr, 2, id(obj))
    return _LoadDependencyIndex(model, MappingProxyType({
        pid: _LoadParameterUses(*(frozenset(refs) for refs in groups))
        for pid, groups in uses.items()
    }))


def _price_row_sign(model, row, *, _dependencies=None):
    con = _ref(model, row.constraint_ref, pyo.Constraint)
    param = _ref(model, row.load_parameter_ref, pyo.Param)
    if not con.active or not con.equality or not param.parent_component().mutable:
        raise PricingInputError("price row requires an active equality and a mutable scalar load Param")
    pid = id(param)
    if _dependencies is None:
        # Preserve the original uncached path for standalone calls, including
        # calls after a model edit. Only build_pricing_lp supplies a local index.
        for other in model.component_data_objects(pyo.Constraint, active=True, descend_into=True):
            if other is con:
                continue
            expressions = [other.body, other.lower, other.upper]
            if any(expr is not None and pid in _param_ids(expr) for expr in expressions):
                raise UnsupportedPricingCase(f"load {param.name} occurs outside its unique nodal balance")
        for var in model.component_data_objects(pyo.Var, descend_into=True):
            # bounds is evaluated by Pyomo, so inspect its symbolic lower/upper too.
            if any(expr is not None and pid in _param_ids(expr) for expr in (var.lower, var.upper)):
                raise UnsupportedPricingCase("load-dependent variable bounds need a total derivative, not one row dual")
        for obj in model.component_data_objects(pyo.Objective, active=True, descend_into=True):
            if pid in _param_ids(obj.expr):
                raise UnsupportedPricingCase("load-dependent objective needs a total derivative, not one row dual")
    else:
        if _dependencies.model is not model:
            raise PricingInputError("load dependency index belongs to a different pricing model")
        uses = _dependencies.uses.get(pid)
        if uses is not None and uses.constraint_ids - {id(con)}:
            raise UnsupportedPricingCase(f"load {param.name} occurs outside its unique nodal balance")
        if uses is not None and uses.variable_bounds:
            raise UnsupportedPricingCase("load-dependent variable bounds need a total derivative, not one row dual")
        if uses is not None and uses.objective_ids:
            raise UnsupportedPricingCase("load-dependent objective needs a total derivative, not one row dual")
    initial = _number(pyo.value(param), f"load parameter {param.name}")
    # Temporarily represent the parameter as an UNFIXED symbolic variable, not
    # a numeric constant. This catches load*dispatch and nonlinear load terms
    # that a derivative probe at one incumbent could accidentally miss.
    symbolic_load = pyo.Var(initialize=initial)
    symbolic_load.construct()
    symbolic_residual = replace_expressions(con.body-con.lower, {pid: symbolic_load})
    repn = generate_standard_repn(symbolic_residual, compute_values=True)
    if not repn.is_linear():
        raise UnsupportedPricingCase("load must be affine and additive, not coupled with dispatch or nonlinear")
    coefficient = sum(float(c) for v, c in zip(repn.linear_vars, repn.linear_coefs)
                      if v is symbolic_load)
    if not any(math.isclose(coefficient, sign, rel_tol=0., abs_tol=1e-12) for sign in (-1, 1)):
        raise UnsupportedPricingCase("load must have constant coefficient +1 or -1")
    step = max(1e-3, abs(initial)*1e-5)
    try:
        # Forward probes also support nonnegative-load Param domains at D=0.
        samples = []
        for offset in (0., step, 2*step):
            param.set_value(initial + offset)
            samples.append(_number(pyo.value(con.body-con.lower), "load row residual"))
    finally:
        param.set_value(initial)
    slopes = [(samples[j+1]-samples[j])/step for j in (0, 1)]
    sign = 1 if slopes[0] > 0 else -1
    if any(not math.isclose(s, sign, rel_tol=1e-7, abs_tol=1e-7) for s in slopes):
        raise UnsupportedPricingCase("load must enter its nodal balance linearly with coefficient +1 or -1")
    return sign


def _validate_omitted_rows(source, spec):
    """Narrow, explicit DA operating-reserve exclusion, never arbitrary rows."""
    if not isinstance(spec.omitted_dispatch_rows,tuple):
        raise PricingInputError('omitted dispatch rows must be immutable explicit records')
    if spec.omitted_dispatch_rows and spec.stage!='DA':
        raise PricingInputError('operating reserve row exclusion is DA only')
    records=_unique(spec.omitted_dispatch_rows,lambda r:r.constraint_ref,'omitted rows')
    expected=set()
    for reference,record in records.items():
        if not isinstance(record,OmittedDispatchRow) or record.kind not in {'DA_operating_up','DA_operating_down'}:
            raise PricingInputError('only documented DA system operating-reserve rows may be omitted')
        _text(record.source,'omitted row rule source')
        row=_ref(source,reference,pyo.Constraint)
        parent=row.parent_block()
        component='c_up' if record.kind=='DA_operating_up' else 'c_down'
        if (not row.active or getattr(parent,'_policy_scope',{}).get('stage')!='DA_SCED'
            or parent.component(component) is not row.parent_component()):
            raise PricingInputError('omission must reference a constructed DA_SCED operating reserve component')
        if getattr(parent,'_lmp_omission_fingerprints',{}).get(row.name)!=str(row.expr):
            raise PricingInputError('constructed operating reserve row changed or has no registered structural fingerprint')
        for name in ('c_up','c_down'):
            part=parent.component(name)
            if part is None:raise PricingInputError('both operating-reserve components required')
            expected.update(str(ComponentUID(c)) for c in part.values() if c.active)
    if set(records)!=expected:
        raise PricingInputError('omit the complete reviewed DA up/down pair for every period, not a partial selection')
    return records


def _validate_rt_fixed_inventory(source, spec):
    records = spec.rt_fixed_inventory
    if not isinstance(records, tuple):
        raise PricingInputError("RT fixed inventory must be immutable typed records")
    if not records:
        storage = getattr(source, "gd_storage", None)
        if (spec.stage == "RT" and storage is not None
                and getattr(storage, "audit", {}).get("stage") == "RT_SCED"
                and len(storage.band_deviation)):
            raise PricingInputError("fixed inventory must exactly cover all plants/periods/four directions")
        return {}
    if spec.stage != "RT":
        raise PricingInputError("fixed ED inventory is RT only")
    if any(not isinstance(r, RTFixedInventory) for r in records):
        raise PricingInputError("RTFixedInventory records required")
    storage = getattr(source, "gd_storage", None)
    if storage is None or getattr(storage, "audit", {}).get("stage") != "RT_SCED":
        raise PricingInputError("fixed inventory requires the selected RT_SCED storage block")
    if storage.audit.get("band_penalty_unit") != "yuan/m3/step":
        raise PricingInputError("source inventory must explicitly have m3 units")
    if set(storage.BAND) != {"upper_low", "upper_high", "lower_low", "lower_high"}:
        raise PricingInputError("exact four-direction inventory required")
    expected = {str(ComponentUID(v)): str(key[1]) for key, v in storage.band_deviation.items()}
    cartesian = {(plant, t, side) for plant in storage.PLANT for t in storage.T for side in storage.BAND}
    if set(storage.band_deviation) != cartesian:
        raise PricingInputError("source inventory must exactly cover all plants/periods/four directions")
    indexed = _unique(records, lambda r: r.slack_ref, "RT fixed inventory")
    if set(indexed) != set(expected):
        raise PricingInputError("fixed inventory must exactly cover all plants/periods/four directions")
    objective_support = set()
    for term in (*spec.bid_terms, *spec.penalty_terms):
        reference = term.power_ref if isinstance(term, BidTerm) else term.slack_ref
        objective_support.update(id(v) for v in identify_variables(_ref(source, reference), include_fixed=True))
    periods = {p.key for p in spec.periods}
    for reference, row in indexed.items():
        _text(row.source, "fixed ED inventory source")
        if row.period not in periods or row.period != expected[reference]:
            raise PricingInputError("fixed inventory period does not match source component")
        value = _number(row.selected_value_m3, "selected inventory m3", minimum=0.)
        variable = _ref(source, reference, pyo.Var)
        if variable.lb is None or pyo.value(variable.lb) < 0 or variable.is_integer():
            raise PricingInputError("fixed inventory must be a nonnegative continuous m3 variable")
        if value != _number(variable.value, "current selected ED inventory m3", minimum=0.):
            raise PricingInputError("fixed inventory differs from current selected ED value")
        if id(variable) in objective_support:
            raise PricingInputError("fixed inventory must not also enter pricing bids or MW penalties")
    return indexed


def build_pricing_lp(source, spec: PricingSpec, *, sced_result) -> PricingLP:
    """Clone an optimal, reviewed same-state SCED refinement; never mutate the input.

    Fixing selected states for LP dual pricing is a research implementation,
    not a claim that the official LMP equations contain no integer variables.

    Missing references, ambiguous negative/zero eligibility, stale solve records,
    incomplete reviews and unfixed integers fail before any pricing solve.
    """
    if not isinstance(spec, PricingSpec):
        raise PricingInputError("PricingSpec required")
    spec.validate_source_snapshot(source)
    if spec.stage not in {"DA", "RT"}:
        raise PricingInputError("stage must explicitly be DA or RT")
    delta = _number(spec.delta, "delta", minimum=0.)
    _text(spec.delta_source, "delta source")
    _text(spec.source_scope, "source scope")
    if not spec.coverage_limitations:
        raise PricingInputError("explicit upstream coverage limitations are required")
    for limitation in spec.coverage_limitations:
        _text(limitation, "coverage limitation")
    periods = _unique(spec.periods, lambda p: p.key, "periods")
    if not periods:
        raise PricingInputError("explicit periods are required; no assumed 96/98 grid")
    for period in periods.values():
        _text(period.key, "period key")
        if _number(period.duration_h, "period duration", minimum=0.) == 0:
            raise PricingInputError("period duration must be positive")
        _text(period.source, "period duration source")
    if not spec.powers:
        raise PricingInputError("complete nonempty power inventory is required")
    powers = _unique(spec.powers, lambda p: (p.resource, p.period), "power points")
    refs = _unique(spec.powers, lambda p: p.power_ref, "power references")
    if len(set(spec.dispatch_inventory)) != len(spec.dispatch_inventory) or set(refs) != set(spec.dispatch_inventory):
        raise PricingInputError("power points must exactly cover the supplied complete dispatch inventory")
    parameters = _unique(spec.penalty_parameters, lambda p: p.category, "penalty parameters")
    required = {"network", "curtailment"}
    if spec.stage == "RT":
        required |= {"load", "first", "emergency"}
    if set(parameters) != required:
        raise PricingInputError(f"explicit penalty parameters required exactly for {sorted(required)}")
    for par in parameters.values():
        _number(par.yuan_per_mwh, f"penalty {par.category}", minimum=0.)
        _text(par.source, f"penalty source {par.category}")
    _check_source(source, spec, sced_result)
    omissions=_validate_omitted_rows(source,spec)
    fixed_inventory = _validate_rt_fixed_inventory(source, spec)
    if source.component("gd_lmp") is not None:
        raise PricingInputError("source already has a gd_lmp pricing block")
    m = source.clone()
    for reference, inventory in fixed_inventory.items():
        _ref(m, reference, pyo.Var).fix(inventory.selected_value_m3)
    for reference in omissions:
        _ref(m,reference,pyo.Constraint).deactivate()
    # IMPORT suffixes carry SCED solver output. None belongs to a new price solve.
    for suffix in m.component_objects(pyo.Suffix, descend_into=True):
        if suffix.import_enabled():
            suffix.clear()
    if m.component("dual") is not None:
        if m.dual.ctype is not pyo.Suffix:
            raise PricingInputError("source dual component is not a Suffix")
        m.del_component("dual")
    m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    for obj in m.component_data_objects(pyo.Objective, active=True, descend_into=True):
        obj.deactivate()
    m.gd_lmp = pyo.Block()
    m.gd_lmp.envelopes = pyo.ConstraintList()
    bounds = []
    for point in powers.values():
        _text(point.resource, "resource identifier")
        if point.kind not in _KINDS:
            raise UnsupportedPricingCase(f"unsupported resource kind {point.kind}; conventional hydro is excluded")
        if point.period not in periods or type(point.eligible) is not bool or type(point.online) is not bool:
            raise PricingInputError("power point requires known period and explicit Boolean eligibility/state")
        _text(point.boundary_source, "SCED boundary source")
        if point.boundary_basis != "sced_effective_after_services":
            raise PricingInputError("power bounds must explicitly be sced_effective_after_services, not raw/nameplate Pmax")
        _text(point.eligibility_source, "pricing eligibility source")
        quantity = _linear_quantity(m, point.power_ref)
        dispatch = _number(pyo.value(quantity), "SCED dispatch")
        lo = _number(point.sced_lower_mw, "SCED lower MW")
        hi = _number(point.sced_upper_mw, "SCED upper MW")
        if lo > hi or dispatch < lo-1e-7 or dispatch > hi+1e-7:
            raise PricingInputError("SCED dispatch outside declared original bounds")
        bound_audit = _source_power_bound_audit(quantity, lo, hi)
        if point.negative_output_policy is not None or point.negative_output_source is not None:
            if not (point.eligible and point.online and dispatch < 0 and point.kind in {"storage", "pumped"}):
                raise PricingInputError("negative-output policy is only valid for online eligible negative storage/pumped power")
            if point.negative_output_policy != "signed_magnitude_relative":
                raise UnsupportedPricingCase("unknown negative-output policy; no implicit research interpretation")
            _text(point.negative_output_source, "negative-output research interpretation source")
        if dispatch < 0 and point.kind not in {"storage", "pumped"}:
            raise UnsupportedPricingCase("negative injection only represented for storage/pumped resources")
        flag = None
        if not point.online:
            if abs(dispatch) > 1e-7 or not lo <= 0 <= hi:
                raise PricingInputError("offline resource must have zero SCED dispatch within original bounds")
            lower = upper = 0.
            flag = "offline_zero"
        elif not point.eligible:
            lower = upper = dispatch
            flag = "ineligible_sced_lock"
        elif dispatch < 0:
            if point.negative_output_policy != "signed_magnitude_relative":
                raise UnsupportedPricingCase("eligible negative charging/pumping: printed relative bounds need an explicit research interpretation")
            if point.zero_output_policy is not None or point.zero_output_source is not None:
                raise PricingInputError("zero-output policy supplied for negative dispatch")
            magnitude = abs(dispatch)
            magnitude_lower = max(0., (1-delta)*magnitude)
            magnitude_upper = (1+delta)*magnitude
            lower, upper = max(lo, -magnitude_upper), min(hi, -magnitude_lower)
            flag = "negative_output_signed_magnitude_research"
        else:
            if dispatch == 0:
                if point.zero_output_policy != "literal_relative_zero":
                    raise UnsupportedPricingCase("eligible zero output requires explicit literal_relative_zero policy, never an implicit lock")
                _text(point.zero_output_source, "zero-output interpretation source")
                flag = "zero_output_literal_lock"
            elif point.zero_output_policy is not None:
                raise PricingInputError("zero-output policy supplied for nonzero dispatch")
            lower, upper = max((1-delta)*dispatch, lo), min((1+delta)*dispatch, hi)
        if point.eligible and point.online and lower < upper:
            repn = generate_standard_repn(quantity, compute_values=True)
            free = [v for v, c in zip(repn.linear_vars, repn.linear_coefs) if c != 0 and not v.fixed and
                    (v.lb is None or v.ub is None or pyo.value(v.lb) < pyo.value(v.ub))]
            if not free:
                raise PricingInputError("eligible power already fixed in source; clarify rather than silently unfix/freeze")
        if lower > upper:
            raise PricingInputError("empty LMP power-bound intersection")
        m.gd_lmp.envelopes.add(pyo.inequality(lower, quantity, upper))
        bounds.append(dict(resource=point.resource, period=point.period, kind=point.kind,
                           power_ref=point.power_ref, sced_dispatch_mw=dispatch,
                           original_lower_mw=lo, original_upper_mw=hi,
                           lower_mw=lower, upper_mw=upper, eligible=point.eligible,
                           online=point.online, audit_flag=flag,
                           eligibility_source=point.eligibility_source,
                           boundary_source=point.boundary_source, boundary_basis=point.boundary_basis,
                           zero_output_source=point.zero_output_source,
                           negative_output_policy=point.negative_output_policy,
                           negative_output_source=point.negative_output_source,
                           research_assumption=flag == "negative_output_signed_magnitude_research",
                           **bound_audit))
    if {p.period for p in spec.powers} != set(periods):
        raise PricingInputError("every supplied period must have declared dispatch powers")
    if not spec.bid_terms:
        raise PricingInputError("complete pricing bid objective is required")
    _unique(spec.bid_terms, lambda t: t.power_ref, "bid power terms")
    objective = 0
    bid_support = set()
    for term in spec.bid_terms:
        if term.period not in periods:
            raise PricingInputError("unknown bid period")
        _text(term.source, "bid source and sign convention")
        coefficient = _number(term.yuan_per_mwh, "bid price")
        quantity = _linear_quantity(m, term.power_ref)
        # Include fixed and currently-zero variables, and inspect expressions:
        # a legacy penalty must not hide in a nominal energy-cost component.
        bid_support.update(id(v) for v in identify_variables(quantity, include_fixed=True))
        objective += periods[term.period].duration_h * coefficient * quantity
    _unique(spec.penalty_terms, lambda t: t.slack_ref, "penalty slack terms")
    paired = {}
    for term in spec.penalty_terms:
        if term.category not in parameters or term.period not in periods:
            raise PricingInputError("unknown or stage-incompatible penalty category/period")
        _text(term.source, "penalty term source")
        slack = _ref(m, term.slack_ref, pyo.Var)
        if id(slack) in bid_support:
            raise PricingInputError(f"bid/penalty variable support overlap: {term.slack_ref}; legacy penalties cannot enter energy bids")
        if slack.lb is None or pyo.value(slack.lb) < 0:
            raise PricingInputError("pricing penalty slack must be a nonnegative MW variable")
        if term.category in {"network", "load"}:
            allowed = {"line", "section"} if term.category == "network" else {"balance"}
            if term.family not in allowed or term.direction not in {"+", "-"}:
                raise PricingInputError("network/load slacks require explicit family and +/- directions")
            _text(term.element, "network/load element")
            key = (term.category, term.family, term.element, term.period)
            directions = paired.setdefault(key, set())
            if term.direction in directions:
                raise PricingInputError("duplicate directional slack")
            directions.add(term.direction)
        elif any(v is not None for v in (term.family, term.element, term.direction)):
            raise PricingInputError("non-network/load penalty must not carry directional-pair metadata")
        objective += periods[term.period].duration_h * parameters[term.category].yuan_per_mwh * slack
    if any(directions != {"+", "-"} for directions in paired.values()):
        raise PricingInputError("each network/load element needs separate positive and negative penalty terms")
    m.gd_lmp.objective = pyo.Objective(expr=objective, sense=pyo.minimize)
    rows = _unique(spec.price_rows, lambda r: (r.node, r.period), "price nodes/periods")
    if not rows or {r.period for r in spec.price_rows} != set(periods):
        raise PricingInputError("price rows must cover every declared period")
    _unique(spec.price_rows, lambda r: r.constraint_ref, "price constraint rows")
    _unique(spec.price_rows, lambda r: r.load_parameter_ref, "price load parameters")
    # The retained rows, all envelopes, and the independent objective are now
    # final. Scope this immutable snapshot to this validation loop only; probes
    # restore parameter values and do not alter the symbolic dependency graph.
    dependencies = _index_load_dependencies(m)
    signs = {}
    for key, row in rows.items():
        _text(row.node, "price node")
        signs[key] = _price_row_sign(m, row, _dependencies=dependencies)
    return PricingLP(m, spec, tuple(bounds), signs)


def solve_pricing_lp(pricing: PricingLP, *, time_limit_s=60., threads=1,
                     mip_rel_gap=0.001, tee=False) -> PricingResult:
    """Run HiGHS for conditional fixed-state research prices; no fallback/clipping."""
    if not isinstance(pricing, PricingLP):
        raise PricingInputError("PricingLP required")
    limit = _number(time_limit_s, "pricing LP time limit", minimum=0.)
    gap = _number(mip_rel_gap, "mip relative gap", minimum=0.)
    if gap > .001: raise PricingInputError("MIP relative gap cannot exceed 0.001")
    if type(tee) is not bool: raise PricingInputError("tee must be Boolean")
    if limit == 0 or type(threads) is not int or threads < 1:
        raise PricingInputError("positive time limit and positive integer threads required")
    model = pricing.model
    _assert_supported_structure(model)
    if any(v.is_integer() and not v.fixed for v in model.component_data_objects(pyo.Var, descend_into=True)):
        raise UnsupportedPricingCase("pricing model contains an unfixed integer; cannot obtain LP prices")
    solver = HighsSolver(dict(time_limit=limit, threads=threads, output_flag=bool(tee), mip_rel_gap=gap))
    result = solver.solve(model, tee=tee)
    if str(result.solver.termination_condition) != "optimal" or not solver.primal_available:
        raise RuntimeError("pricing LP did not return an optimal primal and dual; prices unavailable")
    durations = {p.key: p.duration_h for p in pricing.spec.periods}
    prices, duals = {}, {}
    for row in pricing.spec.price_rows:
        key = (row.node, row.period)
        con = _ref(model, row.constraint_ref, pyo.Constraint)
        if con not in model.dual:
            raise RuntimeError(f"missing pricing LP dual for {row.constraint_ref}; no SCED fallback")
        dual = _number(model.dual[con], "pricing row dual")
        duals[key] = dual
        prices[key] = -pricing.load_residual_signs[key]*dual/durations[row.period]
    audit = dict(
        **pricing_algorithm_audit(pricing.spec.stage),
        stage=pricing.spec.stage, delta=pricing.spec.delta, delta_source=pricing.spec.delta_source,
        penalty_parameters=[vars(p).copy() for p in pricing.spec.penalty_parameters],
        periods=[vars(p).copy() for p in pricing.spec.periods], source_scope=pricing.spec.source_scope,
        coverage_limitations=list(_BASE_LIMITATIONS)+list(pricing.spec.coverage_limitations),
        research_interpretations=[dict(resource=b["resource"], period=b["period"],
                                       policy=b["negative_output_policy"], source=b["negative_output_source"])
                                  for b in pricing.power_bounds if b["research_assumption"]],
        constraint_reviews=[vars(r).copy() for r in pricing.spec.constraint_reviews],
        omitted_dispatch_rows=[vars(r).copy() for r in pricing.spec.omitted_dispatch_rows],
        retained_dispatch_rows=[r.constraint_ref for r in pricing.spec.constraint_reviews if r.constraint_ref not in {o.constraint_ref for o in pricing.spec.omitted_dispatch_rows}],
        fixed_variable_sources=[vars(r).copy() for r in pricing.spec.fixed_variable_sources],
        rt_fixed_inventory=[vars(r).copy() for r in pricing.spec.rt_fixed_inventory],
        rt_inventory_unit="m3" if pricing.spec.rt_fixed_inventory else None,
        rt_inventory_penalty_added=False,
        bid_terms=[vars(t).copy() for t in pricing.spec.bid_terms],
        penalty_terms=[vars(t).copy() for t in pricing.spec.penalty_terms],
        solver_backend="highs", solver_version=getattr(solver,"solver_version",None), solver_settings=getattr(solver,"native_settings",None),
        native_status=getattr(solver,"native_status",None), dual_available=getattr(solver,"dual_available",None),
        dual_convention="row RHS objective sensitivity; price = -load_residual_sign * row_dual / duration_h",
        lp_optimal_required=True, configured_mip_rel_gap=gap, sced_source_untouched=True,
        objective_unit="yuan", power_unit="MW", raw_price_unit="yuan/MWh",
        final_price_status="not_implemented", scarcity_increment_applied=False, clipping_applied=False,
    )
    return PricingResult(prices, duals, _number(pyo.value(model.gd_lmp.objective), "pricing objective"),
                         "optimal", "highs", pricing.power_bounds, audit)

# Native workflow guards must also cover this module's imported solver alias,
# not just the dispatch adapter. Test price records never certify native origin.
_ORIGINAL_PRICING_SOLVER_CLASS = HighsSolver
_ORIGINAL_SOLVE_PRICING_LP = solve_pricing_lp
