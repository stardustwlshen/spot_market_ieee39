"""Sichuan V5.0 draft §3.4: bind declared ancillary awards to energy clearing.

This module is not an auxiliary-service auction, dispatcher, or settlement.
The input schedule is a scenario boundary, not a result of the spot optimizer.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Iterable, Literal, Mapping

DAY_QUARTERS = 96
Product = Literal["spot", "regulation", "reserve", "none"]
PRODUCTS = frozenset(("spot", "regulation", "reserve", "none"))


@dataclass(frozen=True)
class Award:
    """Scheduled quarter, zero-based; each capacity is nonnegative MW."""

    quarter: int
    unit: str
    regulation_up_mw: float = 0.0
    regulation_down_mw: float = 0.0
    reserve_up_mw: float = 0.0


def prepare_energy_boundary(
    *,
    unit_ids: Iterable[str],
    storage_market: Mapping[str, tuple[Product, ...]],
    awards: Iterable[Award] = (),
    quarters: int = DAY_QUARTERS,
) -> tuple[tuple[dict[str, tuple[float, float]], ...], tuple[frozenset[str], ...]]:
    """Return immutable quarter sequence of (up, down) holds and spot-disabled IDs.

    Existing day-ahead and real-time operating limits must still be checked by
    the caller, including SOC/duration where relevant. A hold must not be
    silently reduced merely to recover energy solver feasibility.
    """
    if isinstance(quarters, bool) or not isinstance(quarters, int) or not 1 <= quarters <= DAY_QUARTERS:
        raise ValueError("quarters must be an integer from 1 to 96")
    ids = tuple(unit_ids)
    if any(not isinstance(u, str) or not u.strip() for u in ids) or len(set(ids)) != len(ids):
        raise ValueError("unit IDs must be nonempty and unique")
    known = set(ids)
    disabled: list[set[str]] = [set() for _ in range(quarters)]
    for unit, choices in storage_market.items():
        if unit not in known:
            raise ValueError(f"unknown independent storage: {unit}")
        if len(choices) != quarters or any(not isinstance(c, str) or c not in PRODUCTS for c in choices):
            raise ValueError(f"invalid independent storage market declaration: {unit}")
        for k, product in enumerate(choices):
            if product != "spot":
                disabled[k].add(unit)

    holds: list[dict[str, tuple[float, float]]] = [{} for _ in range(quarters)]
    seen: set[tuple[int, str]] = set()
    for award in awards:
        k, unit = award.quarter, award.unit
        if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < quarters:
            raise ValueError(f"invalid award quarter: {k}")
        if unit not in known or (k, unit) in seen:
            raise ValueError(f"unknown unit or duplicate award: {unit} at {k}")
        seen.add((k, unit))
        slots = (award.regulation_up_mw, award.regulation_down_mw, award.reserve_up_mw)
        if any(isinstance(v, bool) or not isinstance(v, Real) or not isfinite(v) or v < 0 for v in slots):
            raise ValueError(f"invalid nonnegative MW award: {unit} at {k}")
        reg_up, reg_down, reserve = map(float, slots)
        choices = storage_market.get(unit)
        if choices is not None:
            product = choices[k]
            if (reg_up or reg_down) and product != "regulation":
                raise ValueError(f"independent storage regulation conflicts with declaration: {unit} at {k}")
            if reserve and product != "reserve":
                raise ValueError(f"independent storage reserve conflicts with declaration: {unit} at {k}")
        total_up = reg_up + reserve
        if not isfinite(total_up):
            raise ValueError(f"nonfinite total upward award: {unit} at {k}")
        if total_up or reg_down:
            holds[k][unit] = (total_up, reg_down)
    return tuple(holds), tuple(frozenset(s) for s in disabled)
