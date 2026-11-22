"""Pure provisional spot-energy valuation for the Sichuan V5.0 consultation draft.

This module does not read dispatch results, infer eligibility, select price
limits or fallback prices, or receive day-ahead quantities. It uses supplied
real-time prices and quantities; it does not establish ordinary participants'
official settlement cadence, metered/contractual amounts, compensation or bills.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

SettlementRole = Literal[
    "generator", "special_available_generator", "user", "independent_storage",
]
UniformCategory = Literal[
    "provincial_hydro", "public_coal", "centralized_renewables",
    "grid_dispatch_generator", "independent_storage",
]
_ZERO = Decimal(0)
_ROLES = frozenset(("generator", "special_available_generator", "user", "independent_storage"))
_CATEGORIES = frozenset((
    "provincial_hydro", "public_coal", "centralized_renewables",
    "grid_dispatch_generator", "independent_storage",
))


def _require_decimal(value: Decimal, label: str, *, nonnegative: bool = False) -> Decimal:
    """Reject floats and missing/non-finite inputs; adapters own explicit normalization."""
    if not isinstance(value, Decimal) or not value.is_finite() or (nonnegative and value < _ZERO):
        raise ValueError(f"{label}: expected a finite Decimal" + (" >= 0" if nonnegative else ""))
    return value


def _require_identifier(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("resource_id: expected an explicit identifier without surrounding spaces")


@dataclass(frozen=True, slots=True)
class SettlementInput:
    quarter: int
    resource_id: str
    role: SettlementRole
    grid_delivered_mwh: Decimal
    grid_consumed_mwh: Decimal
    rt_nodal_price: Decimal | None = None
    rt_uniform_price: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.quarter) is not int or not 0 <= self.quarter < 96:
            raise ValueError("quarter: expected an integer between 0 and 95")
        _require_identifier(self.resource_id)
        if not isinstance(self.role, str) or self.role not in _ROLES:
            raise ValueError("role: unsupported or unspecified settlement role")
        _require_decimal(self.grid_delivered_mwh, "grid_delivered_mwh", nonnegative=True)
        _require_decimal(self.grid_consumed_mwh, "grid_consumed_mwh", nonnegative=True)
        if self.role in ("generator", "special_available_generator") and self.grid_consumed_mwh:
            raise ValueError("generator consumption needs an explicitly eligible role")
        if self.role == "user" and self.grid_delivered_mwh:
            raise ValueError("user delivery needs an explicitly eligible role")
        for key in ("rt_nodal_price", "rt_uniform_price"):
            value = getattr(self, key)
            if value is not None:
                _require_decimal(value, key)


@dataclass(frozen=True, slots=True)
class SettlementResult:
    """Signed monetary components; positive amounts are receipts."""

    grid_delivered_amount: Decimal | None
    grid_consumed_amount: Decimal | None
    net_amount: Decimal | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UniformPriceInput:
    """One explicitly eligible cleared resource, not a generic injection."""
    resource_id: str
    eligible_category: UniformCategory
    cleared_delivered_mwh: Decimal
    rt_nodal_price: Decimal | None

    def __post_init__(self) -> None:
        _require_identifier(self.resource_id)
        if not isinstance(self.eligible_category, str) or self.eligible_category not in _CATEGORIES:
            raise ValueError("eligible_category: does not qualify for the Sichuan V5.0 uniform price")
        _require_decimal(self.cleared_delivered_mwh, "cleared_delivered_mwh", nonnegative=True)
        if self.rt_nodal_price is not None:
            _require_decimal(self.rt_nodal_price, "rt_nodal_price")


def uniform_price(rows: Iterable[UniformPriceInput]) -> Decimal | None:
    """§4.4: cleared generation-weighted RT nodal price for one quarter.

    Input is exclusively cleared delivered energy from verified eligible
    resources. Independent storage contributes discharge only. No charging,
    user load, non-market generation or special-available injection is weighted.
    Return missing if all weights are zero or a positive-weight price is missing.
    """
    seen: set[str] = set()
    incomplete = False
    with localcontext(prec=34):
        total = weighted = _ZERO
        for row in rows:
            if not isinstance(row, UniformPriceInput):
                raise ValueError("expected an explicitly eligible UniformPriceInput")
            if row.resource_id in seen:
                raise ValueError("duplicate uniform-price resource within quarter")
            seen.add(row.resource_id)
            if not row.cleared_delivered_mwh:
                continue
            if row.rt_nodal_price is None:
                incomplete = True
            else:
                weighted += row.cleared_delivered_mwh * row.rt_nodal_price
            total += row.cleared_delivered_mwh
        return None if incomplete or not total else weighted / total


def settle_quarter(row: SettlementInput) -> SettlementResult:
    """Value the quarter's separate RT legs, without a DA/deviation component.

    Generation and independent storage discharge use the RT nodal price.
    Consumption uses the RT uniform price. Special-available generation uses
    the RT uniform price (draft §9.2.1). No default price is invented for an
    active leg without evidence; a genuinely zero leg contributes zero.
    """
    if not isinstance(row, SettlementInput):
        raise ValueError("expected a SettlementInput")
    delivery_uniform = row.role == "special_available_generator"
    delivery_price = row.rt_uniform_price if delivery_uniform else row.rt_nodal_price
    issues: list[str] = []
    with localcontext(prec=34):
        delivered: Decimal | None = _ZERO
        consumed: Decimal | None = _ZERO
        if row.grid_delivered_mwh:
            if delivery_price is None:
                delivered = None
                issues.append("missing_rt_uniform_price_for_delivery" if delivery_uniform
                              else "missing_rt_nodal_price_for_delivery")
            else:
                delivered = row.grid_delivered_mwh * delivery_price
        if row.grid_consumed_mwh:
            if row.rt_uniform_price is None:
                consumed = None
                issues.append("missing_rt_uniform_price_for_consumption")
            else:
                consumed = -(row.grid_consumed_mwh * row.rt_uniform_price)
        net = None if delivered is None or consumed is None else delivered + consumed
    return SettlementResult(delivered, consumed, net, tuple(issues))
