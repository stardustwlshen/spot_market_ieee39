"""Source-pinned October rule mathematics and explicit, non-certifying inputs.

This is NOT a pricing solver. In particular, averaging SCED multipliers does not
construct the eight-period independent LMP model. No production mapping, delta,
negative-power correction, integer treatment, or official price is inferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import fsum

from .gd_price_research import EvidenceValue, SelectedCashPrice, _number, _text
from . import gd_october_rt_contract as contract

REGION_SHA256 = '978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf'
REGION_URL = 'http://www.gzpec.cn/law/jygz/202611/P020261103633894465723.pdf'
SCHEMA = 'gd.october.pricing_policy.v1'
PARAMETERS = ('delta', 'accident_reserve_penalty_yuan_per_mwh',
              'network_penalty_yuan_per_mwh', 'renewable_penalty_yuan_per_mwh',
              'market_hydro_penalty_yuan_per_mwh', 'load_penalty_yuan_per_mwh')
# These are required decisions, NOT answers supplied by the rule or this code.
DECISIONS = {
    'horizon_and_group_energy': '8x15 at PDF127 versus group-energy 24x5 at PDF131; resolve scope and tstart.',
    'reference_stage': 'RT power bands still call the reference DA SCED (PDF130/133).',
    'reference_power_mapping': 'Specify each resource 24x5 -> 8x15 reference, time semantics, and numerical inputs.',
    'state_and_integer_mapping': 'Specify selected SCUC/SCED states, mixed-state quarters, integer and dual method.',
    'storage_power_band': 'Resolve signed negative charging and printed discharge max/min labels (PDF132/133).',
    'pumped_negative_power': 'DA pump fixed point (PDF82) is not an explicit RT rule.',
    'inventory_slack_mapping': 'Four RT ED slacks fixed (PDF136); specify each plant/time mapping, not an average by default.',
    'storage_physics_and_boundary': 'Specify SOC dynamics/hard bounds inheritance and day versus rolling endpoints (PDF133).',
    'reserve_constraint_inheritance': 'Positive/negative/primary rows explicit; accident reserve objective but no repeated LMP row.',
    'objective_and_multiplier_units': 'Specify consistent yuan objective, MWh scaling, row directions and multiplier basis.',
    'pricing_eligibility': 'Supply resource/time-specific participation and physical bounds, not all variables eligible.',
    'publication_and_limits': 'Supply effective caps/floors, operational validation and publication version; no fallback prices.',
}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _finite_sum(values, label):
    try:
        return _number(fsum(values), label)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + ': nonfinite arithmetic') from exc


def rule_status():
    """Facts from retained official bytes, not a claim that all gaps are public."""
    return dict(rule_sha256=REGION_SHA256, source_url=REGION_URL,
        source_sections={'sced': '3.4.3, print110/PDF113', 'lmp': '3.4.4, print123-124/PDF126-127',
            'group_conflict': '3.4.4.7, print128/PDF131', 'power': '3.4.4.5/.11, PDF130/132-133',
            'inventory': '3.4.4.15, print133/PDF136', 'formula': '3.4.4.18, print134/PDF137',
            'hourly_price': '3.6.1.2, print141/PDF144', 'defaults': 'Appendix3 rows18-21, print180/PDF183'},
        sced_grid=dict(count=24, minutes=5), independent_lmp_grid=dict(count=8, minutes=15),
        appendix_default_facts={name: 1000. for name in PARAMETERS[2:]},
        defaults_are_effective_guangdong_parameters=False, default_delta=None, default_accident_penalty=None,
        unresolved_degrees=dict(DECISIONS), official_price=None, official_pricing_model_ready=False,
        price_or_settlement_created=False)


def audit_pricing_input_contract(document, *, pricing_policy_source, expected_identity,
        expected_delivery_start, asof, source_artifacts, mode):
    """Extend the existing 24/8 input contract with source-backed decisions.

    A complete policy is still NOT a verified operational mapping or runnable
    independent LP. Source hashes prove bytes, not provider authority. Missing
    policies/parameters return unavailable; no research choice is made here.
    """
    base = contract.validate_october_rt_contract(document, expected_identity=expected_identity,
        expected_delivery_start=expected_delivery_start, asof=asof,
        source_artifacts=source_artifacts, mode=mode)
    result = dict(status='unavailable', input_contract=base, policy=None, missing=[], issues=[],
        official_price=None, independent_pricing_model_built=False, official_pricing_model_ready=False,
        operational_mapping_verified=False, source_authenticity_verified=False,
        native_solve_performed=False, settlement_cash_created=False, rule_status=rule_status())
    if base['status'] != 'validated':
        result['missing'] = ['base_24x5_8x15_contract']
        return result
    try:
        used = {}
        p = contract._source(pricing_policy_source, source_artifacts, used, 'pricing_policy')
        contract._object(p, {'schema_version', 'identity', 'rule_source', 'classification',
            'published_at', 'available_at', 'parameters', 'decisions'}, 'pricing_policy')
        _require(p['schema_version'] == SCHEMA, 'unsupported pricing policy schema')
        contract._identity(p['identity'], expected_identity, 'pricing_policy.identity')
        _require(p['classification'] in {'research', 'external_declared'}, 'policy classification must not claim certification')
        _require(mode == 'research' or p['classification'] == 'external_declared', 'research policy requires research mode')
        d = contract._document(document)
        cutoff = contract._time(d['information_cutoff']['lmp'], 'LMP cutoff')
        published, available = (contract._time(p[k], k) for k in ('published_at', 'available_at'))
        _require(published <= available <= cutoff, 'pricing policy unavailable at LMP cutoff')
        contract._source(p['rule_source'], source_artifacts, used, 'pricing_policy.rule_source', parse=False)
        _require(p['rule_source']['source_sha256'] == REGION_SHA256, 'wrong rule version or source bytes')
        contract._object(p['parameters'], PARAMETERS, 'pricing_policy.parameters')
        for name, raw in p['parameters'].items():
            contract._object(raw, {'value', 'kind', 'source', 'missing_reason'}, 'parameter.' + name)
            value = EvidenceValue(**raw).validate(name)
            if value.kind == 'missing':
                result['missing'].append('parameter.' + name)
            else:
                _require(value.value >= 0, name + ': nonnegative value required')
                _require(p['classification'] == 'research' or value.kind == 'external_verified',
                         'research parameter cannot be relabelled external')
        contract._object(p['decisions'], DECISIONS, 'pricing_policy.decisions')
        for name, decision in p['decisions'].items():
            contract._object(decision, {'status', 'description', 'source_ref'}, 'decision.' + name)
            _require(decision['status'] in {'unresolved', 'external_declared', 'research_choice'}, 'invalid decision status')
            _text(decision['description'], name + ' description')
            contract._source(decision['source_ref'], source_artifacts, used, 'decision.' + name, parse=False)
            if decision['status'] == 'unresolved':
                result['missing'].append('decision.' + name)
            if decision['status'] == 'research_choice':
                _require(p['classification'] == 'research', 'research choice cannot be relabelled external')
        result.update(policy=p, policy_source_hashes=used,
            status='unavailable' if result['missing'] else 'declared_inputs_complete_not_model_ready')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        result['issues'].append(str(exc))
        result['policy'] = None
        result['missing'].append('valid_explicit_pricing_policy')
    return result


@dataclass(frozen=True)
class NetworkMultiplier:
    constraint_id: str
    positive_yuan_per_mwh: float
    negative_yuan_per_mwh: float
    node_ptdf: float


def october_lmp_components(*, balance_yuan_per_mwh, lines, sections, multiplier_basis, source):
    """Evaluate §3.4.4.18 after explicit unit/sign normalization, not solve it."""
    _text(source, 'multiplier source')
    _require(multiplier_basis == 'rule_normalized_yuan_per_mwh', 'raw solver duals require independent sign/time normalization')
    balance = _number(balance_yuan_per_mwh, 'balance multiplier')
    totals = {}
    records = {}
    for family, terms in (('lines', lines), ('sections', sections)):
        _require(type(terms) is tuple and all(isinstance(t, NetworkMultiplier) for t in terms), 'typed complete network multiplier tuple required')
        ids, amounts = set(), []
        for t in terms:
            _text(t.constraint_id, 'constraint id')
            _require(t.constraint_id not in ids, 'duplicate ' + family + ' multiplier')
            ids.add(t.constraint_id)
            plus, minus, g = (_number(v, 'network multiplier/PTDF') for v in
                              (t.positive_yuan_per_mwh, t.negative_yuan_per_mwh, t.node_ptdf))
            _require(plus >= 0 and minus >= 0, 'rule positive magnitude convention required')
            amounts.append(_number((plus-minus)*g, 'congestion contribution'))
        totals[family] = _finite_sum(amounts, family + ' sum')
        records[family] = [dict(constraint_id=t.constraint_id, positive_yuan_per_mwh=t.positive_yuan_per_mwh,
            negative_yuan_per_mwh=t.negative_yuan_per_mwh, node_ptdf=t.node_ptdf) for t in terms]
    value = _finite_sum([balance, -totals['lines'], -totals['sections']], 'LMP formula')
    return dict(formula_value_yuan_per_mwh=value, balance_yuan_per_mwh=balance,
        line_congestion_yuan_per_mwh=totals['lines'], section_congestion_yuan_per_mwh=totals['sections'],
        multipliers=records, source=source, rule_sha256=REGION_SHA256, source_section='3.4.4.18, print134/PDF137',
        independent_model_solution_verified=False, network_coverage_verified=False,
        scarcity_or_emergency_adder_applied=False, price_clipping_applied=False,
        official_price=None, publication_mapping_verified=False, settlement_cash_created=False)


def select_october_formula_research_price(*, node, period, pricing_model_id, approval_source, **formula_inputs):
    """Explicit bridge into existing cash-price objects; always research evidence.

    This evaluates supplied formula inputs and records caller selection. It does
    not certify an optimal independent solve or turn a formula into formal LMP.
    """
    report = october_lmp_components(**formula_inputs)
    return SelectedCashPrice('RT', node, period,
        EvidenceValue(report['formula_value_yuan_per_mwh'], 'research',
            report['source'] + '; supplied §3.4.4.18 arithmetic only; no verified independent solution'),
        'literal_formula_research', approval_source, pricing_model_id).validate()


def translated_reservoir_band(*, target_m3, width_m3, positive_slack_m3, negative_slack_m3, source):
    """§3.4.4.15: both sides translate by the fixed ED SL+ minus SL-."""
    _text(source, 'same-selected-ED mapped slack source')
    target, width, plus, minus = (_number(v, 'reservoir value') for v in
                                 (target_m3, width_m3, positive_slack_m3, negative_slack_m3))
    _require(min(target, width, plus, minus) >= 0, 'reservoir quantities must be nonnegative')
    lower = _finite_sum([target, -width, plus, -minus], 'translated lower band')
    upper = _finite_sum([target, width, plus, -minus], 'translated upper band')
    return dict(lower_m3=lower, upper_m3=upper, fixed_positive_slack_m3=plus, fixed_negative_slack_m3=minus,
        source=source, source_section='3.4.4.15, print133/PDF136', source_mapping_verified=False,
        physical_hard_bounds_applied=False, freely_optimized_inventory_slack=False,
        inventory_penalty_created=False, official_price=None)


def literal_pricing_power_band(*, kind, reference_mw, physical_lower_mw, physical_upper_mw, delta, source):
    """Keep the printed formulas, including ambiguous labels; never swap them.

    physical_lower/upper are NUMERICAL signed bounds. Under SCED PDF119,
    P_ch,max <= P_ch <= P_ch,min <= 0, so printed P_ch,max is the numerical
    lower bound and printed P_ch,min the upper. This unusual nomenclature
    must not be silently replaced with positive charging magnitudes.
    """
    _text(source, 'explicit reference stage/delta/bounds source')
    p, lo, hi, d = (_number(v, 'power band input') for v in (reference_mw, physical_lower_mw, physical_upper_mw, delta))
    _require(lo <= p <= hi and d >= 0, 'reference must lie in explicit physical interval; delta nonnegative')
    _require(kind in {'generator', 'storage_charge', 'storage_discharge'}, 'explicit supported band kind required')
    if kind == 'storage_charge':
        _require(hi <= 0, 'charging reference and bounds must be signed negative')
    if kind == 'storage_discharge':
        _require(lo >= 0, 'discharging reference and bounds must be nonnegative')
    low_expr = max(_number((1-d)*p, 'relative expression'), lo)
    high_expr = min(_number((1+d)*p, 'relative expression'), hi)
    if kind == 'generator':
        printed = dict(P_min=low_expr, P_max=high_expr)
        status = 'literal_interval_only' if low_expr <= high_expr else 'unresolved_reversed_interval'
        bounds = [low_expr, high_expr] if low_expr <= high_expr else None
    else:
        # Both storage formulas print max on the first line and min on the second.
        printed = dict(P_max=low_expr, P_min=high_expr)
        status = 'unresolved_signed_charge_band' if kind == 'storage_charge' else 'unresolved_discharge_labels'
        bounds = None
    return dict(status=status, printed_labels=printed, applicable_bounds_mw=bounds, source=source,
        source_section='3.4.4.5/.11, PDF130/132-133', reference_stage_verified=False,
        delta_effective_value_verified=False, negative_power_repaired=False, official_price=None)
