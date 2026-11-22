"""Numerical tests for the provisional Sichuan V5.0 RT spot-energy study."""
from decimal import Decimal
from inspect import signature

import pytest

from app.sichuan_v5_settlement import (
    SettlementInput, SettlementResult, UniformPriceInput, settle_quarter, uniform_price,
)

D = Decimal


def row(role, delivered="0", consumed="0", node="400", uniform="200"):
    return SettlementInput(17, "sample", role, D(delivered), D(consumed),
                           None if node is None else D(node),
                           None if uniform is None else D(uniform))


def weight(identifier, category, energy, lmp):
    return UniformPriceInput(identifier, category, D(energy), None if lmp is None else D(lmp))


def test_uniform_is_eligible_cleared_quantity_weighted_including_storage_discharge():
    # Two coal resources at same bus and a battery discharge: 3 different MWh weights.
    # Battery charging/user volumes are not a permissible input to this calculation.
    items = [weight("coal-1","public_coal","8","100"),
             weight("coal-2","public_coal","1","100"),
             weight("battery-1","independent_storage","1","300")]
    assert uniform_price(iter(items)) == D("120")
    assert uniform_price(reversed(items)) == D("120")
    with pytest.raises(ValueError, match="qualify"):
        weight("user-1","retail_user","5","100")
    with pytest.raises(ValueError, match="qualify"):
        weight("special-1","special_available_generator","2","100")


def test_uniform_negative_zero_and_missing_prices_and_weights():
    n = weight("wind","centralized_renewables","3","-100")
    z = weight("hydro","provincial_hydro","1","0")
    assert uniform_price([n,z]) == D("-75")
    assert uniform_price([weight("idle","grid_dispatch_generator","0",None)]) is None
    assert uniform_price([]) is None
    assert uniform_price([n,weight("idle","grid_dispatch_generator","0",None)]) == D("-100")
    assert uniform_price([n,weight("p2","grid_dispatch_generator","1",None)]) is None
    assert uniform_price([n,weight("compensating","grid_dispatch_generator","3","100")]) == D("0")
    with pytest.raises(ValueError, match="duplicate"):
        uniform_price([n,n])


@pytest.mark.parametrize(("role","delivered","consumed","expected"), [
    ("generator","2","0",("800","0","800")),
    ("special_available_generator","2","0",("400","0","400")),
    ("user","0","2",("0","-400","-400")),
    ("independent_storage","3","2",("1200","-400","800")),
])
def test_directional_value_uses_the_correct_price(role, delivered, consumed, expected):
    assert settle_quarter(row(role, delivered, consumed)) == SettlementResult(*map(D,expected))


def test_negative_prices_are_not_clipped_or_reversed_twice():
    assert settle_quarter(row("generator","2",node="-20")) == SettlementResult(D("-40"),D(0),D("-40"))
    assert settle_quarter(row("independent_storage","1","2",node="-30",uniform="-20")) == SettlementResult(D("-30"),D("40"),D("10"))
    assert settle_quarter(row("user",consumed="1",uniform="0")) == SettlementResult(D(0),D(0),D(0))


def test_no_fake_settlement_if_required_price_is_missing():
    assert settle_quarter(row("user",consumed="0",node=None,uniform=None)) == SettlementResult(D(0),D(0),D(0))
    assert settle_quarter(row("independent_storage","2","1",node=None)) == SettlementResult(
        None,D("-200"),None,("missing_rt_nodal_price_for_delivery",),
    )
    assert settle_quarter(row("special_available_generator","1",uniform=None)) == SettlementResult(
        None,D(0),None,("missing_rt_uniform_price_for_delivery",),
    )
    assert settle_quarter(row("user",consumed="2",uniform=None)) == SettlementResult(
        D(0),None,None,("missing_rt_uniform_price_for_consumption",),
    )
    assert "da" not in " ".join(signature(settle_quarter).parameters)


@pytest.mark.parametrize("invalid", [D("-0.1"),D("NaN"),D("Infinity"),None,1,1.0])
def test_reject_negative_or_non_decimal_mwh(invalid):
    with pytest.raises(ValueError, match="grid_delivered_mwh"):
        SettlementInput(0,"mock","generator",invalid,D(0))
    with pytest.raises(ValueError, match="cleared_delivered_mwh"):
        UniformPriceInput("mock","public_coal",invalid,D(0))


@pytest.mark.parametrize("quarter", [-1,96,True,1.0])
def test_reject_foreign_or_invalid_execution_period(quarter):
    with pytest.raises(ValueError, match="quarter"):
        SettlementInput(quarter,"mock","generator",D(0),D(0))


def test_reject_unsupported_role_direction_or_missing_identity():
    with pytest.raises(ValueError, match="role"):
        row("pumped_storage")
    with pytest.raises(ValueError, match="generator consumption"):
        row("generator",consumed="1")
    with pytest.raises(ValueError, match="user delivery"):
        row("user",delivered="1")
    with pytest.raises(ValueError, match="resource_id"):
        SettlementInput(0," ","generator",D(0),D(0))
