"""Verified arithmetic only, Southern regulation rules issued 2026-08-31.

No auction, regional minimum allocation, operational eligibility, performance
measurement, or dispatch is implemented here. All metrics supplied to these
functions must first pass the applicable technical and provenance validation.
No old market module imports this file; it cannot enable a market by itself.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from numbers import Real

RULE_ID = "southern_regulation_2026_issued_20260831"
PRICE_CAP_YUAN_PER_MW = 15.0
PRICE_STEP_YUAN_PER_MW = 0.1


class UnresolvedRegulationInput(ValueError):
    """Required market/operational input is absent or its treatment unverified."""


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name}: finite numeric value required")
    value = float(value)
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"{name}: {'positive' if positive else 'nonnegative'} value required")
    return value


def normalize_performance(k_i, *, control_area_k_max):
    """§47: denominator is ALL units in the Southern unified control area.

    Coverage is not inferred from local offers or tested by this arithmetic.
    """
    k = _number(k_i, "sorting k_i", positive=True)
    maximum = _number(control_area_k_max, "full control-area k_max", positive=True)
    if k > maximum:
        raise ValueError("k_i exceeds declared full control-area k_max")
    return k / maximum


def sorting_price(bid_yuan_per_mw, normalized_performance, *, independent_storage=False, marginal_substitution_factor=None):
    """§§47–48: offer/P or offer/(P*Fm), with NO price-cap clipping.

    This computes a price, not valid-bid status or an award. Fm is mandatory for
    independent storage and forbidden for other resources to prevent conflation.
    """
    bid = _number(bid_yuan_per_mw, "mileage offer")
    p = _number(normalized_performance, "normalized sorting performance", positive=True)
    if p > 1:
        raise ValueError("normalized sorting performance exceeds 1")
    if type(independent_storage) is not bool:
        raise ValueError("independent_storage must be an explicit Boolean")
    if independent_storage:
        if marginal_substitution_factor is None:
            raise UnresolvedRegulationInput("storage Fm required; do not infer it from local offers")
        fm = _number(marginal_substitution_factor, "storage Fm")
        if fm == 0:
            raise UnresolvedRegulationInput("Fm=0 eligibility treatment not verified; no silent infinite price")
        denominator = p * fm
    else:
        if marginal_substitution_factor is not None:
            raise ValueError("Fm applies only to independent storage sorting")
        denominator = p
    if denominator <= 0 or not math.isfinite(denominator):
        raise ValueError("invalid or underflowed sorting denominator")
    result = bid / denominator
    if not math.isfinite(result):
        raise ValueError("sorting price overflow")
    return result


@dataclass(frozen=True)
class UniformRegulationPrice:
    price_yuan_per_mw: float
    source: str
    raw_marginal_sorting_price_yuan_per_mw: float | None
    rule_id: str = RULE_ID


def price_from_selected_marginal(marginal_sorting_price, *, previous_hour_marginal_price=None):
    """§57: caller supplies an independently selected eligible price setter.

    Reserved-area awards cannot automatically be price setters. Their selection
    is deliberately NOT implemented here. Missing first-hour history is an error.
    """
    if marginal_sorting_price is None:
        if previous_hour_marginal_price is None:
            raise UnresolvedRegulationInput("no marginal price and no verified previous-hour price")
        previous = _number(previous_hour_marginal_price, "previous-hour RAW marginal sorting price")
        return UniformRegulationPrice(min(previous, PRICE_CAP_YUAN_PER_MW), "previous_hour_marginal", previous)
    raw = _number(marginal_sorting_price, "selected eligible marginal sorting price")
    return UniformRegulationPrice(min(raw, PRICE_CAP_YUAN_PER_MW), "selected_eligible_marginal", raw)


def mileage_payment(actual_mileage_mw, uniform_price_yuan_per_mw, settlement_performance_m, *, designated_performance_test=False):
    """§74 gross formula: actual mileage * uniform price * m. NO Fm or dt.

    Quality deductions/exclusions under §§68–69 are NOT implemented; this is
    not a complete final bill or a waiver of those rules.

    Eligibility/calling status and measured mileage must be established outside
    this function. A lookahead capacity award is not actual mileage.
    """
    mileage = _number(actual_mileage_mw, "actual mileage MW")
    price = _number(uniform_price_yuan_per_mw, "uniform regulation price")
    m = _signed_number(settlement_performance_m, "settlement performance m")
    if m < 0:
        raise UnresolvedRegulationInput("negative published m needs verified compensation/quality treatment; not silently clipped")
    if price > PRICE_CAP_YUAN_PER_MW:
        raise ValueError("uniform regulation price exceeds 15 yuan/MW")
    if type(designated_performance_test) is not bool:
        raise ValueError("designated_performance_test must be an explicit Boolean")
    result = 0.0 if designated_performance_test else mileage * price * m
    if not math.isfinite(result):
        raise ValueError("mileage payment overflow")
    return result


def _signed_number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name}: finite numeric value required")
    return float(value)


def sorting_performance(k_I, k_II, k_III):
    """Appendices: weighted sorting score from ALREADY validated submetrics."""
    values = [_signed_number(v, "sorting submetric") for v in (k_I, k_II, k_III)]
    if not 0 <= values[0] <= 5 or values[1] > 1 or values[2] > 1:
        raise ValueError("sorting submetric exceeds verified formula bounds; inputs must be prevalidated")
    # No hidden lower clipping of delay/error components is specified.
    result = .5 * values[0] + .25 * values[1] + .25 * values[2]
    if not math.isfinite(result):
        raise ValueError("sorting performance overflow")
    return result


def settlement_performance(m_I, m_II, m_III):
    """Appendices: settlement m uses DIFFERENT metrics/weights from sorting k."""
    values = [_signed_number(v, "settlement submetric") for v in (m_I, m_II, m_III)]
    if not 0 <= values[0] <= 7.25 or values[1] > 1 or values[2] > 1:
        raise ValueError("settlement submetric exceeds verified formula bounds; inputs must be prevalidated")
    # Negative response/accuracy components are NOT silently clipped to zero.
    result = .16 * values[0] + .42 * values[1] + .42 * values[2]
    if not math.isfinite(result):
        raise ValueError("settlement performance overflow")
    return result


def ordinary_requirement(load_forecast_max_mw, renewable_forecast_max_mw, *, load_coefficient, renewable_coefficient):
    """Appendix 4 ordinary requirement; explicit CURRENT coefficients required.

    Maxima come from the relevant forecasting interval/area. The published
    coefficient bands are TYPICAL values, not legal hard bounds. This component
    currently supports that ordinary domain only; an atypical operator setting
    needs separately verified support, not automatic rejection as unlawful.
    No rolling interval, zone allocation, or emergency override is inferred.
    """
    load = _number(load_forecast_max_mw, "forecast load maximum")
    renewable = _number(renewable_forecast_max_mw, "forecast renewable maximum")
    a = _number(load_coefficient, "load coefficient")
    b = _number(renewable_coefficient, "renewable coefficient")
    if not .008 <= a <= .015 or not .008 <= b <= .03:
        raise UnresolvedRegulationInput("coefficient outside typical software-supported domain; verified special/operator setting needed, not a rule violation")
    result = a * load + b * renewable
    if not math.isfinite(result):
        raise ValueError("ordinary requirement overflow")
    return result


def validate_mileage_offer_price(price_yuan_per_mw, *, current_floor_yuan_per_mw):
    """Hourly offer price, cap 15 and tick .1; CURRENT floor is explicit.

    The initial floor was 3, minimum dynamically permitted floor 1. This function
    does not infer that any particular floor is effective for an operation date.
    """
    price = _number(price_yuan_per_mw, "mileage offer")
    floor = _number(current_floor_yuan_per_mw, "current published floor")
    if not 1 <= floor <= PRICE_CAP_YUAN_PER_MW:
        raise ValueError("current published floor outside supported bounds")
    if not floor <= price <= PRICE_CAP_YUAN_PER_MW:
        raise ValueError("mileage offer outside current bounds")
    if abs(price / PRICE_STEP_YUAN_PER_MW - round(price / PRICE_STEP_YUAN_PER_MW)) > 1e-9:
        raise ValueError("mileage offer must use 0.1 yuan/MW increments")
    return price
