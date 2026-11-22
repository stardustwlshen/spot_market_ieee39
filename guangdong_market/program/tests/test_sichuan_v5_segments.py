from decimal import Decimal

import pytest

from app.sichuan_v5_segments import validate_curve


def series(ends, first_price=85, step=20, start=0):
    return [(a, b, first_price + i * step) for i, (a, b) in
            enumerate(zip([start, *ends[:-1]], ends))]


def test_reservoir_regular_requires_five_price_segments_below_eighty_percent():
    with pytest.raises(ValueError, match="前 80%"):
        validate_curve("reservoir", 100, series([20,40,60,80,100]))
    assert len(validate_curve("reservoir", 100, series([16,32,48,64,80,100]))) == 6
    assert len(validate_curve("reservoir_curtailment", 100, series([20,40,60,80,100]))) == 5


def test_variable_source_single_flat_only_at_floor():
    assert validate_curve("renewable", 100, [(0, 100, 0)])[0].end_mw == 100
    with pytest.raises(ValueError, match="5 至 10"):
        validate_curve("renewable", 100, [(0, 100, 1)])


def test_coal_regulated_bounds_and_optional_low_output_block():
    rows = series([36,52,68,84,100], start=20, first_price=105)
    assert validate_curve("coal", 100, rows, coal_min_stable_mw=20,
                          coal_min_adjustable_mw=10)[0].start_mw == 20
    result = validate_curve("coal", 100, rows, coal_min_stable_mw=20,
                            coal_min_adjustable_mw=10, coal_flexibility_retrofit=True,
                            coal_flex_block_price=85)
    assert len(result) == 6 and result[0].price_yuan_mwh == Decimal(85)
    with pytest.raises(ValueError, match="至少 85"):
        validate_curve("coal", 100, series([36,52,68,84,100], first_price=84, start=20),
                       coal_min_stable_mw=20, coal_min_adjustable_mw=10)


@pytest.mark.parametrize("bad, error", [
    (series([20,40,60,80,100], step=19), "价差"),
    (series([20,40,60,80,100], step=151), "价差"),
    (series([20,40,60,80,100], first_price=750), "超出"),
    (series([20.001,40,60,80,100]), "精度"),
    (series([21,40,60,80,100]), "跨度"),
    ([(0,16,0),(17,32,20),(32,48,40),(48,65,60),(65,82,80),(82,100,100)], "连续"),
])
def test_inadmissible_segment_prices_endpoints_widths_and_ticks_rejected(bad, error):
    with pytest.raises(ValueError, match=error):
        validate_curve("run_of_river", 100, bad)


def test_research_curve_provides_full_solver_capacity_and_valid_regulatory_offer_part():
    from app.sichuan_v5_segments import research_curve

    coal = research_curve("coal", 1800, 350, coal_minimum_mw=280)
    assert sum(width for width, _ in coal) == pytest.approx(1800)
    assert coal[0] == (280.0, 350.0) and len(coal[1:]) == 6
    assert coal[-1][1] == 450
    reservoir = research_curve("reservoir", 1250, 120)
    assert sum(width for width, _ in reservoir) == pytest.approx(1250)
    assert len(reservoir) == 6
