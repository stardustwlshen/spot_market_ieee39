from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from sichuan_v5_dadu_policy import effective_offer, market_power_policy, settle_realtime


def day(month, capacity=200, daily_energy=5000):
    return market_power_policy(month, [capacity] * 96, [100] * 96, [0] * 96, daily_energy)


def test_risk_month_thresholds_energy_and_capacity_have_independent_denominators():
    assert day(5)["active"] is False
    assert day(4)["trigger"] == "dry_routine"
    tight_power = day(6, capacity=101)
    assert tight_power["trigger"] == "wet_power"
    assert tight_power["k"] == tight_power["k2"]
    tight_energy = day(6, daily_energy=3000)
    assert tight_energy["energy_ratio"] == 1.25
    assert tight_energy["trigger"] == "wet_energy"
    assert tight_energy["k"] == tight_energy["k1"]
    assert 0 < tight_energy["cap"] < tight_power["cap"] < day(5)["cap"] == 800
    with pytest.raises(ValueError):
        market_power_policy(5, [10], [10], [0], 100)


def test_frozen_offers_and_reference_curve_by_technology():
    raw = tuple((100 * i, 100 * (i + 1), 500 + i * 20) for i in range(10))
    policy = day(2)
    reservoir = effective_offer(raw, "reservoir", 1000, policy)
    river = effective_offer(raw, "run_of_river", 1000, policy)
    assert [s[2] for s in raw] == [500 + i * 20 for i in range(10)]
    assert reservoir[0][2] > river[0][2] and reservoir[-1][2] < raw[-1][2]
    assert effective_offer(raw, "reservoir", 1000, day(6)) == list(raw)
    assert effective_offer([(0, 100, 0)], "pv", 100, policy) == [(0, 100, 0)]
    with pytest.raises(ValueError):
        effective_offer(raw, "gas", 1000, policy)
    with pytest.raises(ValueError):
        effective_offer(raw[:-1], "reservoir", 1000, policy)


RESOURCES = [
    {"id": "H", "kind": "reservoir", "bus": 30},
    {"id": "C", "kind": "coal", "bus": 31},
    {"id": "G", "kind": "gas", "bus": 32},
]


def quarter(q):
    return {"quarter": q, "price_by_bus": {30: 100 + 100 * q, 31: 40, 32: 780},
            "cleared_mw": {"H": 8, "C": 8, "G": 600},
            "metered_mw": {"H": 4 * (q + 1), "C": 8, "G": 600},
            "cap": 250, "reliable": True}


def test_hourly_value_weighted_by_own_meter_uniform_weights_clears_and_excludes_gas():
    rows = [quarter(i) for i in range(4)]
    rows[0]["price_by_bus"] = {str(k): v for k, v in rows[0]["price_by_bus"].items()}
    result = settle_realtime(rows, RESOURCES)
    assert result["eligible"] == ["H", "C"]
    assert result["portfolio_members"] == ["H"]
    assert result["quarters"][3]["raw_price_by_bus"][30] == 400
    assert result["quarters"][3]["effective_price_by_bus"][30] == 250
    assert result["quarters"][3]["price_source_by_bus"][30] == "rt_sced_at_cap"
    assert result["quarters"][3]["uniform_price"] == 145
    assert result["uniform_hourly"][0]["uniform_price"] == 120
    assert result["total_revenue"] == 2250
    assert result["market_total_revenue"] == 2250 + 8 * 40
    assert result["portfolio"][0]["rt_nodal_price"] == 225
    assert result["data_quality"] == "verified"


def test_physical_dual_is_preserved_when_floor_applies_and_prices_use_decimal_tick():
    row = quarter(0)
    row["price_by_bus"][30] = -193.015
    row["price_by_bus"][31] = 40.005
    value = settle_realtime([row], RESOURCES)["quarters"][0]
    assert value["raw_price_by_bus"][30] < 0
    assert value["effective_price_by_bus"][30] == 0
    assert value["effective_price_by_bus"][31] == 40.01
    assert value["price_source_by_bus"][30] == "rt_sced_at_floor"


def test_partial_hour_hole_and_system_slack_withhold_comparable_values():
    sample = [quarter(i) for i in range(4)]
    partial = settle_realtime(sample[:1], RESOURCES)
    assert partial["executed_portfolio_revenue"] == 100
    assert partial["total_revenue"] is None
    assert "incomplete_hour" in partial["issues"]
    gapped = settle_realtime([sample[0], sample[2], sample[3]], RESOURCES)
    assert "missing_quarter" in gapped["issues"]
    assert gapped["total_revenue"] is None
    sample[2]["reliable"] = False
    slack = settle_realtime(sample, RESOURCES)
    assert slack["quarters"][2]["effective_price_by_bus"][30] is None
    assert slack["quarters"][2]["raw_price_by_bus"][30] == 300
    assert slack["quarters"][2]["uniform_price"] is None
    assert slack["total_revenue"] is slack["market_total_revenue"] is None


def test_zero_delivery_has_zero_revenue_unknown_positive_price_or_meter_stays_missing():
    sample = [quarter(i) for i in range(4)]
    for row in sample:
        row["price_by_bus"].pop(30)
        row["metered_mw"]["H"] = row["cleared_mw"]["H"] = 0
    zero = settle_realtime(sample, RESOURCES)
    assert zero["total_revenue"] == 0
    assert zero["portfolio"][0]["rt_nodal_price"] is None
    assert zero["uniform_hourly"][0]["uniform_price"] == 40
    sample[1]["metered_mw"]["H"] = sample[1]["cleared_mw"]["H"] = 4
    missing = settle_realtime(sample, RESOURCES)
    assert missing["total_revenue"] is None and missing["quarters"][1]["uniform_price"] is None
    assert "missing_rt_nodal_price_for_delivery" in missing["issues"]
    del sample[1]["metered_mw"]["H"]
    assert "missing_metered_quantity" in settle_realtime(sample, RESOURCES)["issues"]


def test_all_96_quarters_and_duplicate_resource_or_quarter_validation():
    sample = [quarter(i) for i in range(96)]
    full = settle_realtime(sample, RESOURCES)
    assert full["complete_day"] and len(full["portfolio"]) == 24
    assert full["total_revenue"] == sum(hour["revenue"] for hour in full["portfolio"])
    with pytest.raises(ValueError):
        settle_realtime([sample[0], sample[0]], RESOURCES)
    with pytest.raises(ValueError):
        settle_realtime(sample, [*RESOURCES, RESOURCES[0]])
