import pytest

from app.sichuan_v5_rules import (
    BidMode, GridAllocation, Participant, Product, Scope, classify_seasons,
    nonrolling_contract_bounds, participation_rule, rolling_contract_bounds,
    spot_bid_bounds, spot_clear_bounds, validate_spot_bid_price,
    validate_spot_submission, validate_storage_period,
)


@pytest.mark.parametrize(
    ("month", "pv", "risk", "contracts", "cap"),
    [(1, "dry", "dry", "dry", 415.63), (4, "dry", "dry", "dry", 415.63),
     (5, "dry", "wet", "normal", 333.84), (6, "wet", "wet", "wet", 211.43),
     (10, "wet", "wet", "wet", 211.43), (11, "dry", "dry", "normal", 333.84),
     (12, "dry", "dry", "dry", 415.63)],
)
def test_season_purposes_never_share_a_calendar(month, pv, risk, contracts, cap):
    s = classify_seasons(month)
    assert (s.competitive_pv_bidding, s.market_power, s.nonrolling_contract) == (pv, risk, contracts)
    assert s.market_power_scope == s.nonrolling_contract_scope == Scope.NOV3_PUBLIC_SUMMARY
    assert nonrolling_contract_bounds(month) == (0, cap)
    assert rolling_contract_bounds() == (0, 800)
    expected = BidMode.QUANTITY_PRICE if pv == "wet" else BidMode.QUANTITY_ONLY
    assert participation_rule(Participant.COMPETITIVE_PV, month).bid_mode == expected
    assert spot_bid_bounds() == spot_clear_bounds() == (0, 800)


@pytest.mark.parametrize("invalid", [True, 0, 13, 5.0, "6", None])
def test_invalid_month_cannot_sneak_into_a_season(invalid):
    with pytest.raises(ValueError):
        classify_seasons(invalid)


@pytest.mark.parametrize(
    ("participant", "expected"),
    [(Participant.PROVINCIAL_HYDRO, BidMode.QUANTITY_PRICE),
     (Participant.PUBLIC_COAL, BidMode.QUANTITY_PRICE),
     (Participant.CENTRAL_RENEWABLE, BidMode.QUANTITY_PRICE),
     (Participant.RENEWABLE_WITH_COLOCATED_STORAGE, BidMode.QUANTITY_PRICE),
     (Participant.POVERTY_PV, BidMode.QUANTITY_ONLY),
     (Participant.INDEPENDENT_STORAGE, BidMode.QUANTITY_ONLY),
     (Participant.VPP_LOAD, BidMode.QUANTITY_ONLY),
     (Participant.GREEN_DIRECT_LINE, BidMode.QUANTITY_ONLY),
     (Participant.POWER_USER, BidMode.NON_REPORTING),
     (Participant.RETAILER, BidMode.NON_REPORTING),
     (Participant.GAS, BidMode.NON_MARKET),
     (Participant.BIOMASS, BidMode.NON_MARKET),
     (Participant.SECURITY_SUPPORT, BidMode.NON_MARKET)],
)
def test_participant_classes_not_a_one_size_fits_all_price_bid(participant, expected):
    d = participation_rule(participant, 7)
    assert d.bid_mode == expected and d.scope == Scope.V5_DRAFT and d.basis.startswith("V5.0 ")
    assert d.quantity_is_fixed_boundary == (expected == BidMode.NON_MARKET)


def test_resource_access_requires_verified_conditions_and_separate_vpp_side():
    p = Participant.DISTRIBUTED_RENEWABLE
    assert participation_rule(p, 7).bid_mode == BidMode.UNDETERMINED
    assert participation_rule(p, 7, four_capabilities=True).bid_mode == BidMode.QUANTITY_PRICE
    assert participation_rule(p, 7, four_capabilities=False).bid_mode == BidMode.NON_REPORTING
    with pytest.raises(ValueError):
        participation_rule(p, 7, four_capabilities="yes")
    p = Participant.VPP_GENERATION
    assert participation_rule(p, 7).bid_mode == BidMode.UNDETERMINED
    assert participation_rule(p, 7, four_capabilities=False).bid_mode == BidMode.NOT_ELIGIBLE
    assert participation_rule(p, 7, four_capabilities=True).bid_mode == BidMode.QUANTITY_ONLY


def test_grid_dispatch_only_partial_provincial_volume_is_fixed_boundary():
    p = Participant.GRID_DIRECT_PLANT
    assert participation_rule(p, 7, grid_allocation="all_in_province").bid_mode == BidMode.UNDETERMINED
    assert participation_rule(p, 7, provincial_mlt=True).bid_mode == BidMode.UNDETERMINED
    assert participation_rule(p, 7, provincial_mlt=False, grid_allocation="all_in_province").bid_mode == BidMode.NOT_ELIGIBLE
    d = participation_rule(p, 7, provincial_mlt=True, grid_allocation=GridAllocation.ALL_IN_PROVINCE)
    assert d.bid_mode == BidMode.QUANTITY_PRICE and not d.quantity_is_fixed_boundary
    d = participation_rule(p, 7, provincial_mlt=True, grid_allocation=GridAllocation.PARTLY_IN_PROVINCE)
    assert d.bid_mode == BidMode.NON_REPORTING and d.quantity_is_fixed_boundary
    assert participation_rule(p, 7, provincial_mlt=True, grid_allocation="none_in_province").bid_mode == BidMode.NOT_ELIGIBLE


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -0.01, 800.01, "", True, None])
def test_price_is_validated_never_silently_clipped(price):
    with pytest.raises(ValueError):
        validate_spot_bid_price(price)


def test_submission_mode_zero_price_is_still_a_price_and_bounds_are_inclusive():
    coal = participation_rule(Participant.PUBLIC_COAL, 7)
    for p in (0, 800):
        assert validate_spot_bid_price(p) == p
        assert validate_spot_submission(coal, "quantity_price", price=p) == BidMode.QUANTITY_PRICE
    battery = participation_rule(Participant.INDEPENDENT_STORAGE, 7)
    assert validate_spot_submission(battery, "quantity_only") == BidMode.QUANTITY_ONLY
    with pytest.raises(ValueError):
        validate_spot_submission(battery, "quantity_only", price=0)
    with pytest.raises(ValueError):
        validate_spot_submission(battery, "quantity_price", price=0)
    gas = participation_rule(Participant.GAS, 7)
    with pytest.raises(ValueError):
        validate_spot_submission(gas, "non_market")


@pytest.mark.parametrize("product", [Product.REGULATION, Product.RESERVE])
def test_storage_nonspot_product_has_zero_spot_schedule(product):
    assert validate_storage_period(product, 0, 0) == product
    for charge, discharge in [(1, 0), (0, 1)]:
        with pytest.raises(ValueError):
            validate_storage_period(product, charge, discharge)


def test_storage_rejects_mixed_product_nonfinite_and_negative():
    assert validate_storage_period(Product.SPOT, 2, 0) == Product.SPOT
    for inputs in [(["spot", "reserve"], 0, 0), ("spot", -1, 0), ("reserve", 0, float("nan")), ("invalid", 0, 0)]:
        with pytest.raises(ValueError):
            validate_storage_period(*inputs)
