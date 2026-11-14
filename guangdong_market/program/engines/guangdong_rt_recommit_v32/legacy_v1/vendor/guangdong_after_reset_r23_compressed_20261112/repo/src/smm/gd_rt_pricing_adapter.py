"""Explicit RT independent conditional-pricing research, never a published price.

October 2026 full-rule execution is fail-closed with enumerated mapping gaps.
The optional legacy profile retains R1/R2 physical rows, fixes the selected ED
four-direction inventory slacks, and omits inventory MP from the new objective.
It is not the October inventory translated-band geometry. No solver is called.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from numbers import Real

import pyomo.environ as pyo
from pyomo.core.base.componentuid import ComponentUID
from pyomo.core.expr.visitor import identify_variables
from pyomo.repn import generate_standard_repn

from .gd_lmp import (PricingInputError, UnsupportedPricingCase, PricingSpec, PricingLP,
    PricingResult, Period, PowerPoint, BidTerm, PenaltyParameter, PenaltyTerm, PriceRow,
    ConstraintReview, FixedVariableSource, RTFixedInventory, _index_load_dependencies, _price_row_sign)
from .gd_lmp_reference_adapter import da_resource_inventory
from .gd_price_research import finite_difference_diagnostic

LEGACY_PROFILE = 'legacy_r1r2_fixed_state_research'
OCTOBER_PROFILE = 'october_2026_official'
OCTOBER_GAPS = (
    ('rt_time_grid_and_state_mapping', '24 x 5-minute RT calculation points to 8 x 15-minute publication points and selected-state mapping are not implemented in the inherited 15-minute source.'),
    ('operating_up_down_reserve_rows', 'October 3.4.4.2/.3 retains positive/negative operating reserve; inherited RT reserve block contains neither row family.'),
    ('battery_power_band_printed_orientation', 'October 3.4.4.11 charging/discharging max/min printed orientation requires explicit clarification; signed-magnitude is only an optional research choice.'),
    ('battery_soc_scope', 'Inherited hourly DA SOC anchors/direction constraints are not certified October LMP rows; first/last SOC, recurrence, limits and cycle mappings require a complete review.'),
    ('pumped_translated_inventory_bands', 'October 3.4.4.15 uses two-sided volume - fixed ED SL+ + fixed ED SL- bands. Inherited two independent one-sided rows are not this geometry.'),
    ('accident_reserve_row_mapping', 'October objective contains MR\u2032 SLpr, but LMP does not separately restate the SCED accident-reserve constraints. Inherited headroom rows are not a verified equivalent.'),
    ('selected_state_and_dual_algorithm', 'State handoff and degenerate-dual selection are not a verified operator algorithm; current LMP is not claimed to retain all earlier binary equations.'),
    ('operator_parameters_and_eligibility', 'Appendix 3 supplies 1000 yuan/MWh network, renewable and load pricing penalties; actual effective Guangdong versions, delta, accident-reserve penalty and resource-period eligibility still need sourced inputs.'),
)


@dataclass(frozen=True)
class RTReferencePricePolicy:
    # No defaults: choosing a research profile is an explicit caller action.
    profile: str
    profile_source: str
    delta: float
    delta_source: str
    penalty_parameters: tuple[PenaltyParameter, ...]
    eligibility: dict
    eligibility_source: str
    zero_output_policy: str
    zero_output_source: str
    negative_output_policy: str
    negative_output_source: str
    integer_policy: str
    integer_policy_source: str
    inventory_policy: str
    inventory_policy_source: str
    boundary_review_source: str
    constraint_review_source: str
    rule_source: str


@dataclass(frozen=True, kw_only=True)
class RTReferencePricingSpec(PricingSpec):
    run_id: str
    selected_dispatch_id: str
    selected_dispatch_fingerprint: str
    selected_integer_values: tuple[tuple[str, float], ...]
    adapter_policy: RTReferencePricePolicy
    adapter_audit: dict

    def validate_source_snapshot(self, source):
        """Reject every factory-to-build source/spec change; never refresh IDs."""
        if rt_selected_dispatch_fingerprint(source) != self.selected_dispatch_fingerprint:
            raise PricingInputError('selected source snapshot changed after RT spec creation')
        rebuilt = build_rt_reference_pricing_spec(source, dict(stage='RT', run_id=self.run_id,
            selected_dispatch_id=self.selected_dispatch_id,
            selected_dispatch_fingerprint=self.selected_dispatch_fingerprint), policy=self.adapter_policy)
        if self != rebuilt:
            raise PricingInputError('source structure or RT pricing contract changed after spec creation')


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise PricingInputError(name + ': explicit nonempty identifier/source required')
    return value


def _number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PricingInputError(name + ': finite numeric input required')
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise PricingInputError(name + ': finite numeric input required') from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise PricingInputError(name + ': finite numeric input in range required')
    return result


def _uid(value):
    return str(ComponentUID(value))


def rt_pricing_profile_status(profile):
    """Describe executable scope without selecting a profile or touching a model."""
    if profile not in {LEGACY_PROFILE, OCTOBER_PROFILE}:
        raise PricingInputError('explicit supported RT pricing profile required')
    return dict(profile=profile, available=profile == LEGACY_PROFILE,
        requires_explicit_research_selection=profile == LEGACY_PROFILE,
        official_full_pricing_available=False,
        unresolved_mappings=[dict(code=code, detail=detail) for code, detail in OCTOBER_GAPS],
        resolved_rule_facts=dict(inventory='four fixed ED directional slacks; October translated bands',
            price_formula='lambda minus line and section congestion; no separate xi or scarcity adder',
            inventory_MP_in_pricing_objective=False,
            appendix3_pricing_penalty_yuan_per_mwh=dict(network=1000., renewable=1000., load=1000.),
            appendix3_parameter_source='October 2026 Appendix 3 printed180/PDF183; not an automatic execution default'),
        solver_invoked=False)


def rt_selected_dispatch_fingerprint(model):
    """Content consistency, not provenance authentication or optimality evidence.

    Clone-safe selected primal/fixed-state identity. Freezing a load-shortfall
    cap at its baseline does not change this fingerprint. Price-solve primals
    are deliberately not compared to it, because independent pricing moves P.
    """
    active = list(model.component_data_objects(pyo.Objective, active=True))
    if len(active) != 1:
        raise PricingInputError('one selected dispatch objective required')
    payload = dict(variables=sorted((_uid(v), _number(v.value, _uid(v)), v.fixed)
        for v in model.component_data_objects(pyo.Var)),
        periods=[(str(t), _number(model._energy_horizon.duration_h[t], 'duration')) for t in model.T],
        objective_yuan=_number(pyo.value(active[0]), 'selected objective'))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()



def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _row_fingerprint(model, references):
    rows = []
    for ref in references:
        row = ComponentUID(ref).find_component_on(model)
        if row is None or row.ctype is not pyo.Constraint:
            raise PricingInputError('missing retained source constraint: ' + ref)
        rows.append((ref, row.active, str(row.expr)))
    return _digest(sorted(rows))


def _parameter_fingerprint(model):
    return _digest(sorted((part.name, str(key), str(pyo.value(value)))
        for part in model.component_objects(pyo.Param) for key, value in part.items()))



def _variable_domain_fingerprint(model):
    """Clone-stable complete variable inventory and effective/symbolic bounds."""
    return _digest(sorted((_uid(v), str(v.domain),
        sorted(str(x) for x in v.domain) if v.domain.isfinite() else None,
        str(v.lower), str(v.upper),
        None if v.lb is None else _number(pyo.value(v.lb), 'variable lower bound'),
        None if v.ub is None else _number(pyo.value(v.ub), 'variable upper bound'))
        for v in model.component_data_objects(pyo.Var)))


def _objective_signature(expression):
    repn = generate_standard_repn(expression, compute_values=True)
    if not repn.is_linear():
        raise PricingInputError('independent objective must remain linear')
    return dict(constant=_number(pyo.value(repn.constant), 'objective constant'),
        terms=sorted((_uid(v), _number(pyo.value(c), 'objective coefficient'))
                     for v, c in zip(repn.linear_vars, repn.linear_coefs) if c != 0.))


def _assert_retained_pricing_domain(pricing):
    spec, m = pricing.spec, pricing.model
    references = tuple(r.constraint_ref for r in spec.constraint_reviews)
    expected = set(references) | {_uid(c) for c in m.gd_lmp.envelopes.values()}
    if { _uid(c) for c in m.component_data_objects(pyo.Constraint, active=True)} != expected:
        raise PricingInputError('active independent pricing rows changed')
    if _row_fingerprint(m, references) != spec.adapter_audit['source_row_fingerprint']:
        raise PricingInputError('retained source constraint structure changed')
    if _parameter_fingerprint(m) != spec.adapter_audit['source_parameter_fingerprint']:
        raise PricingInputError('baseline pricing parameters changed; not the selected-dispatch result')
    if _variable_domain_fingerprint(m) != spec.adapter_audit['source_variable_domain_fingerprint']:
        raise PricingInputError('independent variable domain, bounds, or inventory changed')
    expected_fixed = dict(spec.adapter_audit['source_fixed_variable_values'])
    expected_fixed.update({r.slack_ref: r.selected_value_m3 for r in spec.rt_fixed_inventory})
    if {_uid(v): float(pyo.value(v)) for v in m.component_data_objects(pyo.Var) if v.fixed} != expected_fixed:
        raise PricingInputError('fixed pricing variable coverage or values changed')
    original_objectives = spec.adapter_audit['source_objectives']
    if {_uid(o) for o in m.component_data_objects(pyo.Objective)} != {
            r['reference'] for r in original_objectives} | {'gd_lmp.objective'}:
        raise PricingInputError('pricing objective inventory changed')
    for record in original_objectives:
        obj = ComponentUID(record['reference']).find_component_on(m)
        if obj.active or str(obj.expr) != record['expression'] or str(obj.sense) != record['sense']:
            raise PricingInputError('retained original objective changed')
    objectives = list(m.component_data_objects(pyo.Objective, active=True))
    if len(objectives) != 1 or objectives[0] is not m.gd_lmp.objective or objectives[0].sense != pyo.minimize:
        raise PricingInputError('independent pricing objective identity changed')
    dt = {p.key: p.duration_h for p in spec.periods}
    params = {p.category: p.yuan_per_mwh for p in spec.penalty_parameters}
    expression = sum(dt[t.period]*t.yuan_per_mwh*ComponentUID(t.power_ref).find_component_on(m) for t in spec.bid_terms)
    expression += sum(dt[t.period]*params[t.category]*ComponentUID(t.slack_ref).find_component_on(m) for t in spec.penalty_terms)
    if _objective_signature(expression) != _objective_signature(m.gd_lmp.objective.expr):
        raise PricingInputError('independent pricing objective structure changed')
    if len(m.gd_lmp.envelopes) != len(pricing.power_bounds):
        raise PricingInputError('independent power-envelope coverage changed')
    for con, bound in zip(m.gd_lmp.envelopes.values(), pricing.power_bounds):
        quantity = ComponentUID(bound['power_ref']).find_component_on(m)
        if (pyo.value(con.lower) != bound['lower_mw'] or pyo.value(con.upper) != bound['upper_mw']
                or _objective_signature(con.body) != _objective_signature(quantity)):
            raise PricingInputError('independent power envelope changed')


def rt_resource_inventory(model):
    """Exact full-resource source inventory; no inferred price eligibility."""
    ids = da_resource_inventory(model)
    if model.gd_storage.audit['stage'] != 'RT_SCED' or model.gd_reserve._policy_scope['stage'] != 'RT_SCED':
        raise UnsupportedPricingCase('selected RT_SCED storage and reserves required')
    return ids


def _validate_policy(policy):
    if not isinstance(policy, RTReferencePricePolicy):
        raise PricingInputError('explicit RTReferencePricePolicy required')
    status = rt_pricing_profile_status(policy.profile)
    for field in ('profile_source', 'delta_source', 'eligibility_source', 'zero_output_source',
                  'negative_output_source', 'integer_policy_source', 'inventory_policy_source',
                  'boundary_review_source', 'constraint_review_source', 'rule_source'):
        _text(getattr(policy, field), field)
    if not status['available']:
        raise UnsupportedPricingCase('October full-rule pricing unavailable: ' + '; '.join(
            f'{row["code"]}: {row["detail"]}' for row in status['unresolved_mappings']))
    _number(policy.delta, 'delta', 0.)
    if policy.integer_policy != 'fixed_selected_state_research':
        raise UnsupportedPricingCase('explicit fixed_selected_state_research algorithm required; no operator algorithm inferred')
    if policy.inventory_policy != 'fixed_dispatch_independent_four_slacks':
        raise UnsupportedPricingCase('only fixed_dispatch_independent_four_slacks in legacy research domain; shared/free/unresolved inventory rejected')
    if policy.zero_output_policy not in {'literal_relative_zero', 'unresolved'}:
        raise UnsupportedPricingCase('unsupported zero-output policy')
    if policy.negative_output_policy not in {'signed_magnitude_relative', 'unresolved'}:
        raise UnsupportedPricingCase('unsupported negative-output policy')
    required = {'network', 'curtailment', 'load', 'first', 'emergency'}
    records = policy.penalty_parameters
    if (not isinstance(records, tuple) or any(not isinstance(p, PenaltyParameter) for p in records)
            or len(records) != len(required) or {p.category for p in records} != required):
        raise PricingInputError('five explicit sourced penalty parameters required, with no defaults')
    for parameter in records:
        _number(parameter.yuan_per_mwh, 'penalty ' + parameter.category, 0.)
        _text(parameter.source, 'penalty source ' + parameter.category)


def build_rt_reference_pricing_spec(model, context, *, policy):
    """Build only. October official profile fails before model access.

    context must name the run, selected dispatch, and its expected fingerprint.
    The workflow's source clone with fixed baseline load-shortfall caps is valid.
    The factory never writes model values, rows, objectives, suffixes or integers.
    """
    _validate_policy(policy)
    if not isinstance(context, dict) or context.get('stage') != 'RT':
        raise UnsupportedPricingCase('RT-only adapter requires explicit RT context')
    for key in ('run_id', 'selected_dispatch_id', 'selected_dispatch_fingerprint'):
        _text(context.get(key), key)
    fingerprint = rt_selected_dispatch_fingerprint(model)
    if context['selected_dispatch_fingerprint'] != fingerprint:
        raise PricingInputError('selected dispatch identity fingerprint mismatch')
    ids = rt_resource_inventory(model)
    expected = {(u, t) for u in ids for t in model.T}
    if (not isinstance(policy.eligibility, dict) or set(policy.eligibility) != expected
            or any(type(v) is not bool for v in policy.eligibility.values())
            or any(type(key[1]) is not int for key in policy.eligibility)):
        raise PricingInputError('eligibility must exactly cover resource/integer-period pairs with Boolean values')
    integers = []
    for variable in model.component_data_objects(pyo.Var):
        if variable.is_integer():
            value = _number(variable.value, 'selected integer')
            if not variable.fixed or value != round(value):
                raise PricingInputError('all selected integer states must already be fixed and integral')
            integers.append((_uid(variable), value))
    s, b, reserve = model.gd_storage, model.gd_piecewise_bids, model.gd_reserve
    if any(r['anchor_policy']['mode'] != 'hard' for r in s._storage_inputs['batteries'].values()):
        raise UnsupportedPricingCase('emergency battery anchor policy lacks a pricing mapping')
    if any(not v.fixed or _number(v.value, 'anchor slack') != 0.
           for part in (s.anchor_low, s.anchor_high) for v in part.values()):
        raise PricingInputError('hard battery anchor slacks must be fixed zero')
    powers, bids, penalties, rows = [], [], [], []

    def point(resource, t, kind, power, online, lower, upper):
        value = _number(pyo.value(power), 'selected resource power')
        eligible = policy.eligibility[resource, t]
        options = {}
        if online and eligible and value == 0.:
            if policy.zero_output_policy == 'unresolved':
                raise UnsupportedPricingCase('eligible zero output has unresolved policy')
            options.update(zero_output_policy=policy.zero_output_policy, zero_output_source=policy.zero_output_source)
        if online and eligible and value < 0.:
            if policy.negative_output_policy == 'unresolved':
                raise UnsupportedPricingCase('eligible negative output has unresolved policy')
            options.update(negative_output_policy=policy.negative_output_policy, negative_output_source=policy.negative_output_source)
        powers.append(PowerPoint(resource, str(t), kind, _uid(power), eligible, online,
            float(lower), float(upper), policy.boundary_review_source, policy.eligibility_source,
            'sced_effective_after_services', **options))

    for t in model.T:
        for resource, record in model._energy_inputs['thermal'].items():
            on = bool(pyo.value(model.online[resource, t]))
            point(resource, t, 'thermal', model.power[resource, t], on,
                  pyo.value(model.online_min[resource, t])*on, pyo.value(model.online_max[resource, t])*on)
            for segment, data in record['segments'].items():
                bids.append(BidTerm(_uid(model.segment_power[resource, segment, t]), str(t),
                                    data['price_yuan_per_mwh'][t], record['source']))
        for record in b._piecewise_raw_bids:
            curve = b.audit['normalized_curves'][record.bid_id]
            for segment, price in enumerate(curve['prices_yuan_per_mwh'][t]):
                bids.append(BidTerm(_uid(b.segment_power[record.bid_id, segment, t]), str(t),
                                    curve['objective_sign']*price, record.curve.price_source))
            if record.kind == 'renewable':
                point(record.resource_id, t, 'renewable', b.resource_power[record.bid_id, t], True,
                      0., record.availability_mw[t])
        for resource, record in s._storage_inputs['batteries'].items():
            charge = int(pyo.value(s.charge_mode[resource, t]))
            discharge = int(pyo.value(s.discharge_mode[resource, t]))
            point(resource, t, 'storage', s.battery_net_power[resource, t], bool(charge or discharge),
                  -record['charge_max_mw'][t]*charge, record['discharge_max_mw'][t]*discharge)
        for plant, record in s._storage_inputs['plants'].items():
            lo = -sum(s._storage_inputs['units'][u]['pump_power_mw']*pyo.value(s.mode[u, t, 'pump']) for u in record['members'])
            hi = sum(s._storage_inputs['units'][u]['generation_max_mw'][t]*pyo.value(s.mode[u, t, 'generate']) for u in record['members'])
            point(plant, t, 'pumped', s.plant_net_power[plant, t], bool(lo or hi), lo, hi)
        for bus in model.B:
            rows.append(PriceRow(bus, str(t), _uid(model.c_balance[bus, t]), _uid(model.demand_mw[bus, t])))
            penalties.append(PenaltyTerm(_uid(model.renewable_curtailment[bus, t]), str(t), 'curtailment', policy.constraint_review_source))
            for component, direction in ((model.balance_shortfall, '+'), (model.balance_surplus, '-')):
                penalties.append(PenaltyTerm(_uid(component[bus, t]), str(t), 'load', policy.constraint_review_source, 'balance', bus, direction))
        for family, names, plus, minus in (('line', model.L, model.line_over, model.line_under),
                                           ('section', model.S, model.section_over, model.section_under)):
            for key in names:
                for component, direction in ((plus, '+'), (minus, '-')):
                    penalties.append(PenaltyTerm(_uid(component[key, t]), str(t), 'network', policy.constraint_review_source, family, key, direction))
        penalties.extend((PenaltyTerm(_uid(reserve.primary_short[t]), str(t), 'first', policy.constraint_review_source),
                          PenaltyTerm(_uid(reserve.emergency_short[t]), str(t), 'emergency', policy.constraint_review_source)))
    inventory = tuple(RTFixedInventory(_uid(v), str(t), _number(v.value, 'selected ED inventory m3', 0.),
        policy.inventory_policy_source) for (plant, t, side), v in s.band_deviation.items())
    audit = rt_pricing_profile_status(policy.profile)
    audit.update(source_model_domain='inherited_R1R2_RT_SCED_rows',
        source_row_fingerprint=_row_fingerprint(model, tuple(_uid(c) for c in model.component_data_objects(pyo.Constraint, active=True))),
        source_parameter_fingerprint=_parameter_fingerprint(model),
        source_objectives=[dict(reference=_uid(o), expression=str(o.expr), sense=str(o.sense), active=o.active)
            for o in model.component_data_objects(pyo.Objective)],
        source_variable_domain_fingerprint=_variable_domain_fingerprint(model),
        source_fixed_variable_values={_uid(v): float(pyo.value(v)) for v in model.component_data_objects(pyo.Var) if v.fixed},
        inventory_geometry='inherited_separate_one_sided_bands_not_October_translated_two_sided_bands',
        retained_constraint_families=sorted({c.parent_component().name for c in model.component_data_objects(pyo.Constraint, active=True)}),
        omitted_fixed_objective_terms='thermal fixed base/start/stop; inventory MP removed from independent objective',
        first_reserve_penalty_status='inherited_legacy_research_not_October_parameter_certification',
        current_formula_form='conditional nodal marginal with no additional xi/mu; old physical domain, not current official price',
        selected_state_algorithm='explicit research handoff; official selection algorithm unverified',
        profile_selection_is_caller_declaration=True, source_authentication_verified=False)
    return RTReferencePricingSpec(stage='RT', delta=float(policy.delta), delta_source=policy.delta_source,
        periods=tuple(Period(str(t), model._energy_horizon.duration_h[t], model._energy_horizon.time_mapping_source) for t in model.T),
        powers=tuple(powers), dispatch_inventory=tuple(p.power_ref for p in powers), bid_terms=tuple(bids),
        penalty_parameters=deepcopy(policy.penalty_parameters), penalty_terms=tuple(penalties), price_rows=tuple(rows),
        constraint_reviews=tuple(ConstraintReview(_uid(c), 'nodal_balance' if c.parent_component() is model.c_balance else 'physical',
            policy.constraint_review_source) for c in model.component_data_objects(pyo.Constraint, active=True)),
        fixed_variable_sources=tuple(FixedVariableSource(_uid(v), policy.integer_policy_source if v.is_integer() else policy.boundary_review_source)
            for v in model.component_data_objects(pyo.Var) if v.fixed),
        source_scope='Explicit legacy R1R2 RT fixed-state conditional research; NOT October full-rule or published pricing',
        coverage_limitations=tuple(detail for code, detail in OCTOBER_GAPS), rt_fixed_inventory=inventory,
        run_id=context['run_id'], selected_dispatch_id=context['selected_dispatch_id'],
        selected_dispatch_fingerprint=fingerprint, selected_integer_values=tuple(sorted(integers)),
        adapter_policy=deepcopy(policy), adapter_audit=audit)


def build_rt_result_evidence(pricing, result, *, pricing_model_id, run_id,
        selected_dispatch_id, selected_dispatch_fingerprint, source, input_kind):
    """Pure result reader. No solve, SCED fallback, scarcity input, or cash choice.

    A synthetic fixture can populate typed result/dual records for tests, but is
    explicitly caller-declared synthetic, never native numerical acceptance.
    """
    if (not isinstance(pricing, PricingLP) or not isinstance(result, PricingResult)
            or not isinstance(pricing.spec, RTReferencePricingSpec)):
        raise PricingInputError('typed independent RT adapter PricingLP and PricingResult required')
    spec = pricing.spec
    for label, value in (('pricing_model_id', pricing_model_id), ('source', source), ('run_id', run_id),
                         ('selected_dispatch_id', selected_dispatch_id), ('selected_dispatch_fingerprint', selected_dispatch_fingerprint)):
        _text(value, label)
    if (run_id, selected_dispatch_id, selected_dispatch_fingerprint) != (
            spec.run_id, spec.selected_dispatch_id, spec.selected_dispatch_fingerprint):
        raise PricingInputError('result selected-dispatch/run identity mismatch')
    if input_kind not in {'synthetic', 'research', 'external_verified'}:
        raise PricingInputError('explicit input evidence classification required')
    if (spec.stage != 'RT' or result.audit.get('stage') != 'RT' or result.backend != 'highs'
            or result.termination_condition != 'optimal' or result.audit.get('configured_mip_rel_gap') != .001):
        raise PricingInputError('optimal independent RT HiGHS result with configured gap 0.001 required')
    if result.final_lmp_yuan_per_mwh is not None or result.final_price_status != 'not_implemented':
        raise PricingInputError('unexpected final official price claim')
    _validate_policy(spec.adapter_policy)
    _assert_retained_pricing_domain(pricing)
    m = pricing.model
    for reference, value in spec.selected_integer_values:
        var = ComponentUID(reference).find_component_on(m)
        if var is None or not var.fixed or not var.is_integer() or pyo.value(var) != value:
            raise PricingInputError('pricing selected integer state changed')
    for record in spec.rt_fixed_inventory:
        var = ComponentUID(record.slack_ref).find_component_on(m)
        if var is None or not var.fixed or pyo.value(var) != record.selected_value_m3:
            raise PricingInputError('pricing fixed ED inventory changed')
    expected = {(r.node, r.period) for r in spec.price_rows}
    if not expected or len(expected) != len(spec.price_rows):
        raise PricingInputError('complete unique RT node-period rows required')
    for mapping in (result.raw_lmp_yuan_per_mwh, result.row_dual_yuan_per_mw, pricing.load_residual_signs):
        if not isinstance(mapping, dict) or set(mapping) != expected:
            raise PricingInputError('result/sign rows must exactly match RT price-row scope')
    objective = _number(pyo.value(m.gd_lmp.objective), 'retained pricing objective')
    if not math.isclose(objective, _number(result.objective_yuan, 'reported objective'), rel_tol=1e-9, abs_tol=1e-7):
        raise PricingInputError('reported objective differs from retained independent LP')
    if result.power_bounds != pricing.power_bounds:
        raise PricingInputError('reported pricing power envelopes differ')
    periods = {p.key: p for p in spec.periods}
    emergency = {str(t): m.gd_reserve.c_emergency[t] for t in m.gd_reserve.T}
    if set(emergency) != set(periods):
        raise PricingInputError('complete emergency row-period coverage required')
    dependencies = _index_load_dependencies(m)
    reports = []
    for row in spec.price_rows:
        key = row.node, row.period
        con = ComponentUID(row.constraint_ref).find_component_on(m)
        erow = emergency[row.period]
        if (con is None or not con.active or not con.equality or con not in m.dual
                or not erow.active or erow.lower is None or erow.upper is not None or erow not in m.dual):
            raise PricingInputError('missing independent node or lower-bound emergency row dual; no fallback')
        dual = _number(result.row_dual_yuan_per_mw[key], 'reported node dual')
        if dual != _number(m.dual[con], 'retained node dual'):
            raise PricingInputError('reported node dual differs from retained LP dual')
        sign = pricing.load_residual_signs[key]
        if type(sign) is not int or sign not in {-1, 1}:
            raise PricingInputError('explicit +/-1 nodal residual sign required')
        if sign != _price_row_sign(m, row, _dependencies=dependencies):
            raise PricingInputError('reported nodal load sign differs from retained balance row')
        dt = _number(periods[row.period].duration_h, 'duration', 0.)
        if not dt:
            raise PricingInputError('positive duration required')
        raw = _number(-sign*dual/dt, 'normalized raw nodal price')
        if not math.isclose(raw, _number(result.raw_lmp_yuan_per_mwh[key], 'reported raw price'), rel_tol=1e-9, abs_tol=1e-7):
            raise PricingInputError('raw price differs from dual/sign/duration normalization')
        emergency_dual = _number(m.dual[erow], 'emergency lower-bound dual', 0.)
        xi = _number(emergency_dual/dt, 'normalized emergency diagnostic')
        reports.append(dict(stage='RT', run_id=run_id, selected_dispatch_id=selected_dispatch_id,
            selected_dispatch_fingerprint=selected_dispatch_fingerprint, pricing_model_id=pricing_model_id,
            model_role='independent_fixed_selected_state_pricing', node=row.node, period=row.period,
            duration_h=dt, balance_row_ref=row.constraint_ref, load_residual_sign=sign,
            balance_row_dual_yuan_per_mw=dual, raw_nodal_yuan_per_mwh=raw,
            emergency_row_ref=_uid(erow), emergency_lower_bound_dual_yuan_per_mw=emergency_dual,
            xi_diagnostic_yuan_per_mwh=xi, xi_added_to_price=False,
            october_formula_form_candidate_yuan_per_mwh=raw,
            conditional_marginal_research_yuan_per_mwh=raw,
            candidate_domain='old physical domain; October published-form conditional node value, NOT current official price',
            legacy_raw_plus_xi_counterexample_yuan_per_mwh=_number(raw+xi, 'legacy double-count diagnostic'),
            legacy_raw_plus_xi_status='not_current_formula_not_selected_for_price',
            scarcity_increment_applied=False, input_kind=input_kind,
            candidate_kind='synthetic' if input_kind == 'synthetic' else 'research',
            source=source, rule_source=spec.adapter_policy.rule_source,
            adapter_audit=deepcopy(spec.adapter_audit),
            inventory_fixed_m3=[asdict(r) for r in spec.rt_fixed_inventory if r.period == row.period],
            objective_unit='yuan', penalty_parameters=[asdict(p) for p in spec.penalty_parameters],
            final_official_price=None, publication_mapping_verified=False,
            settlement_cash_created=False, approved_for_cash_price=None, price_cap_applied=False,
            observation_is_caller_declaration=True, provenance_verified_by_component=False,
            native_numerical_acceptance=False if input_kind == 'synthetic' else None))
    return tuple(reports)


def rt_finite_difference_diagnostic(*, run_id, selected_dispatch_id, pricing_model_id,
        frozen_inventory_source, frozen_model_source, **values):
    """Arithmetic definition over caller-supplied same-LP objectives; no solving."""
    for label, value in (('run_id', run_id), ('selected_dispatch_id', selected_dispatch_id),
                         ('pricing_model_id', pricing_model_id), ('frozen_inventory_source', frozen_inventory_source)):
        _text(value, label)
    result = finite_difference_diagnostic(frozen_model_source=frozen_model_source, **values)
    result.update(run_id=run_id, selected_dispatch_id=selected_dispatch_id, pricing_model_id=pricing_model_id,
        frozen_inventory_source=frozen_inventory_source,
        held_constant=('selected integers', 'power envelopes', 'fixed ED inventory slacks',
            'reserve requirements', 'bid/penalty coefficients', 'baseline load shortfall caps'),
        perturbed_parameter='one nodal load only', xi_added_to_marginal=False,
        rerun_dispatch_is_not_same_LP_sensitivity=True, solver_invoked=False)
    return result
