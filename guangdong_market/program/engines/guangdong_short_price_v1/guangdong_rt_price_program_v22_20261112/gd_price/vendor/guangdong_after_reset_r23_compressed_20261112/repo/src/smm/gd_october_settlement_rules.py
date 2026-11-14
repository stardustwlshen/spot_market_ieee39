"""Source-supported settlement pieces, never a complete official payment ledger.

The Guangdong 2026 settlement file is a repost, not an official-domain original.
Regional compensation formulas defer to effective provincial rules. All facts,
prices, eligibility and parameters are explicit; none are manufactured here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import fsum
from zoneinfo import ZoneInfo

from .gd_energy_cash import (SettlementKey, QuarterHourEligibility, EnergyCashScope,
    EnergyVolumeInput, DayAheadDeduction, EnergyPriceInput, _scope, _aware, _value, _key_dict)
from .gd_price_research import EvidenceValue, _number, _text
from .gd_october_pricing_rules import REGION_SHA256

SETTLEMENT_SHA256 = '768c06ce2b77c958956e3b5ae8ca51de82b2712f78a1247b722ba845b795d263'
SETTLEMENT_URL = 'https://nxepta.com/attached/file/20260205/20260205011306600.pdf'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _evidence(value, label):
    _require(isinstance(value, EvidenceValue), label + ': typed EvidenceValue required')
    return value.validate(label)


def _sum(values, label):
    try:
        return _number(fsum(values), label)
    except OverflowError as exc:
        raise ValueError(label + ': arithmetic overflow') from exc


def _hour(start, end):
    start, end = _aware(start, 'hour start'), _aware(end, 'hour end')
    local = start.astimezone(ZoneInfo('Asia/Shanghai'))
    _require(end-start == timedelta(hours=1) and not (local.minute or local.second or local.microsecond),
             'one complete Shanghai-aligned hour required')
    return start, end


@dataclass(frozen=True)
class HourlyStorageNetContract:
    resource: str
    start: datetime
    end: datetime
    net_energy_mwh: EvidenceValue
    published_at: datetime
    available_at: datetime
    source: str


@dataclass(frozen=True)
class StorageQuarterEnergy:
    key: SettlementKey
    day_ahead_mwh: EvidenceValue
    actual_mwh: EvidenceValue


def split_storage_hour_contract(*, contract: HourlyStorageNetContract,
        quarter_energy: tuple[StorageQuarterEnergy, ...], eligibility: QuarterHourEligibility,
        asof, source) -> tuple[EnergyVolumeInput, ...]:
    """§7.3.2: hourly net contract /4, into 8 existing directional volume inputs.

    DA and actual quarter-hour MWh are independently supplied, never /4 or
    netted here. The returned objects directly feed calculate_energy_cash and
    the existing external-input envelopes. Wrapper time/run checks still apply.
    """
    _require(isinstance(contract, HourlyStorageNetContract), 'typed hourly contract required')
    _text(source, 'split source'); _text(contract.source, 'contract source'); _text(contract.resource, 'resource')
    start, end = _hour(contract.start, contract.end)
    published, available, cutoff = (_aware(v, 'contract publication/availability/asof') for v in
                                    (contract.published_at, contract.available_at, asof))
    _require(published <= available <= cutoff, 'contract unavailable asof')
    c = _evidence(contract.net_energy_mwh, 'hourly net contract MWh')
    _require(type(quarter_energy) is tuple and len(quarter_energy) == 8 and
             all(isinstance(q, StorageQuarterEnergy) for q in quarter_energy),
             'exactly four quarters in each of two separate directions required')
    _require(isinstance(eligibility, QuarterHourEligibility) and eligibility.resource == contract.resource,
             'same resource quarter-hour eligibility required')
    keys = tuple(q.key for q in quarter_energy)
    _scope(EnergyCashScope(keys, source, 'explicit_intervals', (eligibility,)))
    expected = {(start+timedelta(minutes=15*i), start+timedelta(minutes=15*(i+1)), direction)
                for i in range(4) for direction in ('export', 'import')}
    found = set()
    for q in quarter_energy:
        k = q.key
        _require(k.resource == contract.resource and k.resource_kind in {'battery', 'pumped'},
                 'contract split only supports one independent-storage/pumped resource')
        found.add((_aware(k.start, 'quarter start'), _aware(k.end, 'quarter end'), k.direction))
        _value(q.day_ahead_mwh, 'quarter DA MWh', direction=k.direction)
        _value(q.actual_mwh, 'quarter actual MWh', direction=k.direction)
    _require(found == expected, 'quarters must exactly cover contract hour and both directions')
    out = []
    for q in quarter_energy:
        detail = (c.source + '; ' + contract.source + '; ' + source +
                  '; Guangdong2026 §7.3.2 PDF30-31: signed hourly net contract /4; ' + q.key.direction)
        if c.kind == 'missing':
            allocation = EvidenceValue(None, 'missing', detail, c.missing_reason)
        else:
            amount = _number(c.value, 'contract')
            directional = max(amount, 0.) if q.key.direction == 'export' else min(amount, 0.)
            allocation = EvidenceValue(directional/4, c.kind, detail)
        out.append(EnergyVolumeInput(q.key, q.day_ahead_mwh, q.actual_mwh,
                    (DayAheadDeduction('mid_long_term_net_contract', allocation),)))
    return tuple(out)


@dataclass(frozen=True)
class RenewableMeterInput:
    key: SettlementKey
    actual_mwh: EvidenceValue


def calculate_price_taking_renewable_cash(*, keys: tuple[SettlementKey, ...],
        meters: tuple[RenewableMeterInput, ...], prices: tuple[EnergyPriceInput, ...],
        participation_source, node_mapping_source, scope_source, interval_scope):
    """§7.4 actual MWh x RT node price, NOT ordinary DA/RT deviations.

    Caller identifies eligible price-taking renewables and grid-supplied node/
    metering mappings. No interpolation or meter fitting, contracts or DA cash.
    Typed selected prices are reused, with evidence kinds preserved.
    """
    for name, value in (('participation', participation_source), ('node mapping', node_mapping_source), ('scope', scope_source)):
        _text(value, name + ' source')
    valid = _scope(EnergyCashScope(keys, scope_source, interval_scope))
    _require(all(k.resource_kind == 'renewable' and k.direction == 'export' for k in keys),
             'only price-taking renewable export keys supported')
    _require(type(meters) is tuple and all(isinstance(m, RenewableMeterInput) for m in meters), 'typed meter tuple required')
    _require(type(prices) is tuple and all(isinstance(p, EnergyPriceInput) for p in prices), 'typed price tuple required')
    mm, pp = {}, {}
    for m in meters:
        _require(m.key in valid and m.key not in mm, 'meter outside scope or duplicate')
        _value(m.actual_mwh, 'renewable meter MWh', direction='export'); mm[m.key] = m
    for p in prices:
        _require(p.key in valid and p.key not in pp, 'price outside scope or duplicate')
        p.selected_price.validate(); _text(p.interval_mapping_source, 'price interval mapping source')
        _require(p.selected_price.stage == 'RT' and p.selected_price.node == p.key.node and p.selected_price.period == p.key.period,
                 'exact RT node/period selected price required')
        pp[p.key] = p
    rows = []
    for k in keys:
        missing = []
        if k not in mm: missing.append('actual_meter_record')
        if k not in pp: missing.append('RT_selected_price_record')
        values = ([] if k not in mm else [mm[k].actual_mwh]) + ([] if k not in pp else [pp[k].selected_price.value])
        missing += [v.missing_reason for v in values if v.kind == 'missing']
        status = ('unavailable' if missing else 'research_cash' if any(v.kind in {'research','synthetic'} for v in values)
                  else 'external_data_cash')
        cash = None if missing else _number(values[0].value * values[1].value, 'renewable RT cash')
        rows.append(dict(key=_key_dict(k), status=status, missing=missing, actual_rt_energy_cash_yuan=cash,
            actual_meter=None if k not in mm else asdict(mm[k].actual_mwh),
            selected_price=None if k not in pp else asdict(pp[k].selected_price)))
    missing = any(r['status'] == 'unavailable' for r in rows)
    status = 'unavailable' if missing else 'research_cash' if any(r['status'] == 'research_cash' for r in rows) else 'external_data_cash'
    return dict(status=status, rows=rows, actual_rt_energy_cash_yuan=None if missing else _sum(
        [r['actual_rt_energy_cash_yuan'] for r in rows], 'renewable total'), audit=dict(
        source_document=SETTLEMENT_URL, rule_sha256=SETTLEMENT_SHA256, source_section='7.4, print30/PDF32',
        source_classification='repost_of_formal_text_not_official_domain', participation_source=participation_source,
        node_mapping_source=node_mapping_source, scope_source=scope_source, interval_scope=interval_scope,
        settlement_mode='price_taking_renewable_actual_RT', quantity_unit='MWh', price_unit='yuan/MWh', cash_unit='yuan',
        full_official_settlement_verified=False, publication_mapping_verified=False, actual_meter_authenticity_verified=False,
        current_rule_applicability_certified=False, identity_bound_to_run=False, formal_publication_timing_verified=False,
        meter_fitting_performed=False, day_ahead_cash_created=False,
        product_payments_created=False, missing_values_assumed_zero=False))


@dataclass(frozen=True)
class CashApplicability:
    included: bool | None
    source: str
    provincial_rule_source: str
    missing_reason: str | None = None

    def validate(self):
        _require(self.included is None or type(self.included) is bool, 'applicability must be Boolean or unknown')
        _text(self.source, 'eligibility/exclusion source'); _text(self.provincial_rule_source, 'effective provincial rule source')
        if self.included is None:
            _text(self.missing_reason, 'unknown applicability reason')
        else:
            _require(self.missing_reason is None, 'known applicability cannot have missing reason')
        return self


def _component_inputs(values, applicability, section):
    _require(isinstance(applicability, CashApplicability), 'explicit CashApplicability required')
    applicability.validate()
    for label, value in values.items(): _evidence(value, label)
    missing = [label + ': ' + value.missing_reason for label, value in values.items() if value.kind == 'missing']
    if applicability.included is None: missing.append('applicability: ' + applicability.missing_reason)
    status = 'unavailable' if missing else 'research_component' if any(v.kind in {'research','synthetic'} for v in values.values()) else 'external_data_component'
    if applicability.included is False: status = 'explicitly_excluded'
    return dict(status=status, amount_yuan=0. if applicability.included is False else None,
        missing=[] if applicability.included is False else missing, inputs={k:asdict(v) for k,v in values.items()},
        applicability=asdict(applicability), source_section=section, rule_sha256=REGION_SHA256,
        full_official_settlement_verified=False, current_provincial_applicability_verified=False,
        actual_meter_authenticity_verified=False, native_solve_performed=False, payment_executed=False)


def operating_compensation_component(*, operating_cost_yuan, offer_cost_yuan,
        market_revenue_yuan, coefficient, applicability):
    """Regional §4.2.2.3 scalar formula, after externally classified hourly inputs.

    No cost integral/auxiliary-load adjustment, provincial coefficient or eight
    DA/RT quarter exclusion checks are invented. Those are upstream evidence.
    """
    values = dict(operating_cost_yuan=operating_cost_yuan, offer_cost_yuan=offer_cost_yuan,
        market_revenue_yuan=market_revenue_yuan, coefficient=coefficient)
    out = _component_inputs(values, applicability, '4.2.2.3-.4, print148-150/PDF151-153')
    if coefficient.kind != 'missing': _require(coefficient.value >= 0, 'compensation coefficient nonnegative')
    if out['status'] in {'unavailable','explicitly_excluded'}: return out
    difference = _sum([min(operating_cost_yuan.value, offer_cost_yuan.value), -market_revenue_yuan.value], 'cost minus market revenue')
    out['amount_yuan'] = _number(max(difference,0.) * coefficient.value, 'operating compensation')
    return out


def execution_deviation_rate(*, plan_mw, actual_mw, nameplate_mw, denominator_fraction,
        allowed_fraction, source):
    """§4.3.2.2 abs(plan-actual)/max(plan,nameplate*a), not a payment decision.

    The supplied allowed fraction must already include any applicable AGC
    relaxation. Zero denominator stays unavailable. No deviation-hour grouping.
    """
    _text(source, 'effective provincial deviation source')
    values = dict(plan_mw=plan_mw, actual_mw=actual_mw, nameplate_mw=nameplate_mw,
                  denominator_fraction=denominator_fraction, allowed_fraction=allowed_fraction)
    for label, value in values.items():
        _evidence(value, label)
        if value.kind != 'missing': _require(value.value >= 0, label + ': generation-only nonnegative value required')
    missing = [k + ': ' + v.missing_reason for k,v in values.items() if v.kind == 'missing']
    out = dict(status='unavailable', deviation_rate=None, exceeds_allowed=None, denominator_mw=None,
        missing=missing, source=source, inputs={k:asdict(v) for k,v in values.items()},
        source_section='4.3.2.2, print153-154/PDF156-157', rule_sha256=REGION_SHA256,
        agc_relaxation_calculated=False, deviation_hours_selected=False, settlement_cash_created=False)
    if missing: return out
    _require(nameplate_mw.value > 0, 'positive generation nameplate required')
    denominator = max(plan_mw.value, _number(nameplate_mw.value*denominator_fraction.value, 'denominator capacity'))
    if denominator == 0:
        out['missing'] = ['zero_denominator_requires_operational_definition']; return out
    rate = _number(abs(_sum([plan_mw.value,-actual_mw.value], 'power deviation'))/denominator, 'deviation rate')
    out.update(status='supplied_inputs_arithmetic', denominator_mw=denominator,
               deviation_rate=rate, exceeds_allowed=rate > allowed_fraction.value)
    return out


def execution_deviation_return_component(*, actual_export_mwh, instructed_export_mwh,
        rt_price_yuan_per_mwh, approved_cost_yuan_per_mwh, coefficient, applicability):
    """Regional §4.3.2.3; preserve the prose/formula conflict for two negatives.

    Prose requires actual>plan AND price>cost. Printed max(product,0) also turns
    two negatives positive. In that case no payable value is chosen. Caller must
    separately establish deviation-hour coverage and all exclusions.
    """
    values = dict(actual_export_mwh=actual_export_mwh, instructed_export_mwh=instructed_export_mwh,
        rt_price_yuan_per_mwh=rt_price_yuan_per_mwh, approved_cost_yuan_per_mwh=approved_cost_yuan_per_mwh,
        coefficient=coefficient)
    out = _component_inputs(values, applicability, '4.3.2.3-.4, print154-156/PDF157-159')
    for label in ('actual_export_mwh','instructed_export_mwh','coefficient'):
        if values[label].kind != 'missing': _require(values[label].value >= 0, label + ': nonnegative value required')
    out.update(literal_formula_yuan=None, formula_prose_conflict=False)
    if out['status'] in {'unavailable','explicitly_excluded'}: return out
    dq = _sum([actual_export_mwh.value, -instructed_export_mwh.value], 'energy deviation')
    dp = _sum([rt_price_yuan_per_mwh.value, -approved_cost_yuan_per_mwh.value], 'price minus cost')
    literal = _number(max(_number(dq*dp, 'deviation product'),0.) * coefficient.value, 'literal deviation return')
    out['literal_formula_yuan'] = literal
    if dq < 0 and dp < 0:
        out.update(status='unavailable', amount_yuan=None, formula_prose_conflict=True,
            missing=['regional_4.3.2.3_prose_requires_both_positive_but_printed_product_is_positive_for_both_negative'])
    else: out['amount_yuan'] = literal
    return out
