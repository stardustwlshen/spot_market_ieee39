"""Pure conditional-price evidence and explicit cash-price selection.

No solver, SCED-dual fallback, official publication algorithm, or cash is invoked.
Input provenance is a caller declaration, not something these arithmetic checks
can independently certify. See rebuild docs/25_定价研究证据与能量现金接口.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from numbers import Real

from .gd_price_basis import decompose_conditional_price


class PriceResearchInputError(ValueError):
    """Invalid, ambiguous or non-finite research evidence."""


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PriceResearchInputError(f"{label}: explicit nonempty identifier/source required")
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PriceResearchInputError(f"{label}: finite numeric value required")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise PriceResearchInputError(f"{label}: finite numeric value required") from exc
    if not isfinite(number):
        raise PriceResearchInputError(f"{label}: finite numeric value required")
    return number


@dataclass(frozen=True)
class EvidenceValue:
    value: float | None
    kind: str
    source: str
    missing_reason: str | None = None

    def validate(self, label="value"):
        _text(self.source, label + ".source")
        if self.kind not in {"external_verified", "research", "synthetic", "missing"}:
            raise PriceResearchInputError(f"{label}: explicit evidence classification required")
        if self.kind == "missing":
            if self.value is not None:
                raise PriceResearchInputError(f"{label}: missing evidence must have value None")
            _text(self.missing_reason, label + ".missing_reason")
        else:
            _number(self.value, label)
            if self.missing_reason is not None:
                raise PriceResearchInputError(f"{label}: nonmissing evidence cannot carry a missing reason")
        return self


@dataclass(frozen=True)
class ConditionalPriceObservation:
    stage: str
    pricing_model_id: str
    model_role: str
    termination_condition: str
    node: str
    period: str
    duration_h: float
    balance_row_ref: str
    load_residual_sign: int
    balance_row_dual_yuan_per_mw: float
    emergency_row_ref: str | None
    emergency_lower_bound_dual_yuan_per_mw: float | None
    model_basis: str
    selected_integer_state_source: str
    sensitivity_source: str
    source: str
    input_kind: str


@dataclass(frozen=True)
class PricingResearchParameters:
    delta: float
    delta_source: str
    negative_power_band_policy: str
    negative_power_band_source: str
    reservoir_band_slack_policy: str
    reservoir_band_slack_source: str
    scarcity_increment: EvidenceValue


@dataclass(frozen=True)
class SelectedCashPrice:
    """Explicitly selected arithmetic input, not official-mapping certification."""
    stage: str
    node: str
    period: str
    value: EvidenceValue
    price_basis: str
    approval_source: str
    pricing_model_id: str | None = None

    def validate(self):
        if self.stage not in {"DA", "RT"}:
            raise PriceResearchInputError("cash price: explicit DA/RT stage required")
        for label in ("node", "period", "approval_source"):
            _text(getattr(self, label), "cash price." + label)
        if not isinstance(self.value, EvidenceValue):
            raise PriceResearchInputError("cash price: typed EvidenceValue required")
        self.value.validate("cash price")
        if self.price_basis not in {"external_settlement_price", "conditional_marginal_research", "literal_formula_research", "synthetic_price", "unavailable"}:
            raise PriceResearchInputError("cash price: explicit supported price basis required")
        if self.value.kind == "missing":
            if self.price_basis != "unavailable":
                raise PriceResearchInputError("missing cash price requires unavailable basis")
        elif self.price_basis == "unavailable":
            raise PriceResearchInputError("unavailable price cannot carry a numeric value")
        if self.value.kind == "external_verified" and self.price_basis != "external_settlement_price":
            raise PriceResearchInputError("research candidates cannot be promoted to external verified prices")
        if self.price_basis == "external_settlement_price" and self.value.kind != "external_verified":
            raise PriceResearchInputError("external settlement price requires explicit external verification declaration")
        if self.price_basis == "synthetic_price" and self.value.kind != "synthetic":
            raise PriceResearchInputError("synthetic price basis requires synthetic evidence")
        if self.price_basis in {"conditional_marginal_research", "literal_formula_research"}:
            _text(self.pricing_model_id, "research pricing model ID")
        if self.pricing_model_id is not None:
            _text(self.pricing_model_id, "pricing model ID")
        return self


def build_price_evidence(observation: ConditionalPriceObservation,
                         parameters: PricingResearchParameters) -> dict:
    """Normalize independent LP row duals and retain both RT formula candidates."""
    if not isinstance(observation, ConditionalPriceObservation) or not isinstance(parameters, PricingResearchParameters):
        raise PriceResearchInputError("typed observation and research parameters required")
    o, p = observation, parameters
    if o.stage not in {"DA", "RT"}:
        raise PriceResearchInputError("explicit DA/RT stage required")
    if o.model_role != "independent_fixed_selected_state_pricing":
        raise PriceResearchInputError("only independent fixed selected-state pricing evidence is accepted; no SCED fallback")
    if o.termination_condition != "optimal":
        raise PriceResearchInputError("optimal independent pricing solve required")
    if o.input_kind not in {"external_verified", "research", "synthetic"}:
        raise PriceResearchInputError("explicit input evidence classification required")
    for label in ("pricing_model_id", "node", "period", "balance_row_ref", "selected_integer_state_source", "sensitivity_source", "source"):
        _text(getattr(o, label), label)
    dt = _number(o.duration_h, "duration_h")
    if dt <= 0:
        raise PriceResearchInputError("positive duration_h required")
    if type(o.load_residual_sign) is not int or o.load_residual_sign not in {-1, 1}:
        raise PriceResearchInputError("load residual derivative must be exactly integer -1 or 1")
    raw = -o.load_residual_sign * _number(o.balance_row_dual_yuan_per_mw, "balance row dual") / dt
    _number(raw, "normalized nodal price")
    if o.stage == "RT":
        _text(o.emergency_row_ref, "emergency row reference")
        xi = _number(o.emergency_lower_bound_dual_yuan_per_mw, "positive emergency lower-bound row dual") / dt
        if xi < 0:
            raise PriceResearchInputError("positive lower-bound emergency dual convention required")
    else:
        if o.emergency_row_ref is not None or o.emergency_lower_bound_dual_yuan_per_mw is not None:
            raise PriceResearchInputError("DA must not carry the RT emergency multiplier")
        xi = None
    if _number(p.delta, "delta") < 0:
        raise PriceResearchInputError("delta must be nonnegative")
    for label in ("delta_source", "negative_power_band_source", "reservoir_band_slack_source"):
        _text(getattr(p, label), label)
    if p.negative_power_band_policy not in {"unresolved", "signed_magnitude_relative", "no_eligible_negative_output"}:
        raise PriceResearchInputError("explicit supported negative-power policy required")
    policies = {"unresolved", "literal_shared_unpenalized", "research_shared_penalty", "fixed_dispatch_shared_slacks", "hard_shared_band", "research_independent_four_slacks", "fixed_dispatch_independent_four_slacks", "not_applicable"}
    if p.reservoir_band_slack_policy not in policies:
        raise PriceResearchInputError("explicit supported reservoir slack policy required")
    if o.stage == "DA" and p.reservoir_band_slack_policy != "not_applicable":
        raise PriceResearchInputError("DA independent pricing must not inherit RT trajectory-band policies")
    if not isinstance(p.scarcity_increment, EvidenceValue):
        raise PriceResearchInputError("typed explicitly sourced scarcity increment required")
    p.scarcity_increment.validate("scarcity increment")
    if p.scarcity_increment.kind == "missing":
        raise PriceResearchInputError("scarcity increment is unavailable; do not silently assume zero")
    try:
        result = decompose_conditional_price(stage=o.stage, raw_nodal_yuan_per_mwh=raw,
            emergency_dual_yuan_per_mwh=xi,
            scarcity_increment_yuan_per_mwh=p.scarcity_increment.value,
            scarcity_source=p.scarcity_increment.source, model_basis=o.model_basis, source=o.source)
    except ValueError as exc:
        raise PriceResearchInputError(str(exc)) from exc
    # DA also has a literal formula candidate, but never an RT emergency adder.
    if o.stage == "DA":
        result["literal_unverified_candidate_yuan_per_mwh"] = result["research_formula_candidate_yuan_per_mwh"]
    result.update(observation=asdict(o), parameters=asdict(p),
        pricing_model_id=o.pricing_model_id, node=o.node, period=o.period,
        fixed_state_pricing_algorithm_status="research_implementation",
        full_integer_pricing_algorithm_status="unresolved",
        candidate_kind="synthetic" if "synthetic" in {o.input_kind, p.scarcity_increment.kind} else "research",
        approved_for_cash_price=None, final_official_price=None,
        publication_mapping_verified=False, provenance_verified_by_component=False,
        observation_is_caller_declaration=True,
        unresolved_official_items=["integer_pricing_and_degenerate_dual_selection", "official_delta_and_pricing_penalties",
            "negative_eligible_pumping_band_interpretation", "scarcity_and_final_publication_mapping"] +
            (["RT_wording_references_DA_SCED", "RT_inventory_MP_and_shared_SL_mapping", "RT_emergency_multiplier_basis_mapping"] if o.stage == "RT" else []),
        source_document="https://nxepta.com/attached/file/20260205/20260205011253766.pdf",
        source_section="5.8.2.3" if o.stage == "DA" else "6.6.2.2",
        source_printed_pages=[69, 79] if o.stage == "DA" else [113, 124])
    return result


def select_research_cash_price(observation: ConditionalPriceObservation,
                               parameters: PricingResearchParameters, *,
                               candidate: str, approval_source: str) -> SelectedCashPrice:
    """Explicit opt-in to research arithmetic; never upgrades official status.

    Rebuild from typed observations rather than trusting a mutable report dict.
    A settlement interval/key mapping is separately required by gd_energy_cash.
    """
    _text(approval_source, "research cash selection source")
    fields = {"conditional_marginal_research": "research_formula_candidate_yuan_per_mwh",
              "literal_formula_research": "literal_unverified_candidate_yuan_per_mwh"}
    if candidate not in fields:
        raise PriceResearchInputError("choose an explicitly classified research candidate")
    result = build_price_evidence(observation, parameters)
    return SelectedCashPrice(observation.stage, observation.node, observation.period,
        EvidenceValue(result[fields[candidate]], result["candidate_kind"],
            f"{observation.source}; selection={candidate}; scarcity={parameters.scarcity_increment.source}"),
        candidate, approval_source, observation.pricing_model_id).validate()


def finite_difference_diagnostic(*, base_objective_yuan, minus_objective_yuan,
        plus_objective_yuan, perturbation_mw, duration_h, raw_nodal_yuan_per_mwh,
        frozen_model_source, tolerance_yuan_per_mwh=1e-5) -> dict:
    """Check supplied +/- same-LP solves, not rerun-UC/full-parameter sensitivity.

    At a kink any valid dual can lie between the one-sided slopes. This numerical
    observation does not certify that the caller really held the model fixed.
    """
    _text(frozen_model_source, "same-model perturbation source")
    base = _number(base_objective_yuan, "base objective")
    minus = _number(minus_objective_yuan, "minus objective")
    plus = _number(plus_objective_yuan, "plus objective")
    step = _number(perturbation_mw, "perturbation")
    dt = _number(duration_h, "duration")
    tol = _number(tolerance_yuan_per_mwh, "tolerance")
    raw = _number(raw_nodal_yuan_per_mwh, "raw price")
    if step <= 0 or dt <= 0 or tol < 0:
        raise PriceResearchInputError("positive step/duration and nonnegative tolerance required")
    denominator = _number(step * dt, "perturbation energy")
    if denominator == 0:
        raise PriceResearchInputError("perturbation energy underflow")
    left = _number((base - minus) / denominator, "left slope")
    right = _number((plus - base) / denominator, "right slope")
    convex = left <= right + tol
    return dict(left_slope_yuan_per_mwh=left, right_slope_yuan_per_mwh=right,
        raw_nodal_yuan_per_mwh=raw, convex_slopes=convex,
        dual_within_one_sided_interval=convex and left - tol <= raw <= right + tol,
        smooth_at_test_scale=abs(right-left) <= tol,
        source=frozen_model_source, publication_mapping_verified=False,
        official_price_verified=False, parameter_path="only_nodal_load_in_same_fixed_state_pricing_LP")


def build_da_result_evidence(pricing, result, *, pricing_model_id: str,
        source: str, input_kind: str, selected_integer_state_source: str,
        sensitivity_source: str, scarcity_by_period: dict[str, EvidenceValue]) -> tuple[dict, ...]:
    """Read a gd_lmp DA adapter result into evidence, without solving or cash.

    Works with build_da_reference_pricing_spec outputs after build/solve_pricing_lp.
    Checks exact price-row coverage and consistency with the retained LP duals.
    These checks do not authenticate input provenance or certify the official
    model. RT is deliberately rejected until its inventory policy is resolved.
    """
    import pyomo.environ as pyo
    from pyomo.core.base.componentuid import ComponentUID
    from .gd_lmp import PricingLP, PricingResult

    if not isinstance(pricing, PricingLP) or not isinstance(result, PricingResult):
        raise PriceResearchInputError("actual typed PricingLP and PricingResult required")
    if pricing.spec.stage != "DA" or result.audit.get("stage") != "DA":
        raise PriceResearchInputError("result adapter supports only explicit DA independent pricing")
    if result.termination_condition != "optimal" or result.backend != "highs":
        raise PriceResearchInputError("optimal HiGHS independent result required")
    if result.final_lmp_yuan_per_mwh is not None or result.final_price_status != "not_implemented":
        raise PriceResearchInputError("unexpected official/final-price claim in research result")
    periods = {p.key: p for p in pricing.spec.periods}
    if len(periods) != len(pricing.spec.periods):
        raise PriceResearchInputError("duplicate pricing periods")
    if not isinstance(scarcity_by_period, dict) or set(scarcity_by_period) != set(periods):
        raise PriceResearchInputError("explicit scarcity evidence must exactly cover DA periods")
    expected = {(r.node, r.period) for r in pricing.spec.price_rows}
    if len(expected) != len(pricing.spec.price_rows) or not expected:
        raise PriceResearchInputError("complete unique DA price rows required")
    for field in (result.raw_lmp_yuan_per_mwh, result.row_dual_yuan_per_mw, pricing.load_residual_signs):
        if not isinstance(field, dict) or set(field) != expected:
            raise PriceResearchInputError("result/sign rows must exactly match DA price-row scope")
    objective = _number(pyo.value(pricing.model.gd_lmp.objective), "retained pricing objective")
    reported = _number(result.objective_yuan, "reported objective")
    if abs(objective-reported) > max(1e-7, 1e-9*abs(reported)):
        raise PriceResearchInputError("retained LP objective differs from pricing result")
    reports = []
    for row in pricing.spec.price_rows:
        key = row.node, row.period
        con = ComponentUID(row.constraint_ref).find_component_on(pricing.model)
        if con is None or con.ctype is not pyo.Constraint or not con.active or con not in pricing.model.dual:
            raise PriceResearchInputError("missing retained independent pricing row/dual")
        dual = _number(result.row_dual_yuan_per_mw[key], "result row dual")
        current = _number(pricing.model.dual[con], "retained model row dual")
        if dual != current:
            raise PriceResearchInputError("retained LP dual differs from pricing result")
        negatives = [p for p in pricing.spec.powers if p.period == row.period and p.negative_output_policy is not None]
        if any(p.negative_output_policy != "signed_magnitude_relative" for p in negatives):
            raise PriceResearchInputError("unsupported negative-output policy in DA result")
        params = PricingResearchParameters(pricing.spec.delta, pricing.spec.delta_source,
            "signed_magnitude_relative" if negatives else "no_eligible_negative_output",
            "; ".join(_text(p.negative_output_source,"negative output source") for p in negatives) if negatives
                else "explicit complete DA PricingSpec has no eligible negative-output policy for this period",
            "not_applicable", "DA LMP has no RT relative-DA inventory-band policy; GDE 5.8.2.3",
            scarcity_by_period[row.period])
        observation = ConditionalPriceObservation("DA", pricing_model_id,
            "independent_fixed_selected_state_pricing", result.termination_condition, row.node, row.period,
            periods[row.period].duration_h, row.constraint_ref, pricing.load_residual_signs[key], dual,
            None, None, "nodal_balance_and_original_headroom_emergency_row",
            selected_integer_state_source, sensitivity_source, source, input_kind)
        report = build_price_evidence(observation, params)
        raw = _number(result.raw_lmp_yuan_per_mwh[key], "result normalized raw price")
        if abs(raw-report["raw_nodal_yuan_per_mwh"]) > max(1e-7,1e-9*abs(raw)):
            raise PriceResearchInputError("reported raw price disagrees with dual/sign/duration normalization")
        report.update(result_adapter="gd_lmp_DA_result_read_only",
            upstream_scope=pricing.spec.source_scope,
            upstream_coverage_limitations=list(pricing.spec.coverage_limitations),
            pricing_penalty_parameters=[asdict(p) for p in pricing.spec.penalty_parameters])
        reports.append(report)
    return tuple(reports)
