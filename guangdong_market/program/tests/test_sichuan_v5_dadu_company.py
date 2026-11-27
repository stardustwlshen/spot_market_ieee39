from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.fixture
def company(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "app"))
    import sichuan_v5_company as api
    from sichuan_v5_energy import Case, Resource
    from sichuan_v5_hydro import Cascade, Reservoir

    station = Reservoir("A", 0., 2_000_000., 1_000_000., 1., 0.)
    resource = Resource("A", 1, "provincial_hydro", "hydro", 10., 0.)
    cascade = Cascade((station,), (), tuple({"A": 0.} for _ in range(96)))
    case = Case((1,), (), (resource,), tuple({1: 5.} for _ in range(96)),
                tuple({"A": 10.} for _ in range(96)), cascade=cascade)
    prices = [{"A": 220. if 60 <= t < 76 else 80.} for t in range(96)]
    return api, case, prices


def test_opportunity_cost_units_and_plan_certificate(company):
    api, case, prices = company
    offer = api.prepare_company_submission(case, prices, {"A": .05})
    unit = offer["offers"]["A"]
    # 3,600 m³/MWh at 0.05 yuan/m³ gives exactly 180 yuan/MWh.
    assert [r["A"] for r in offer["marginal_water_cost_yuan_mwh"]] == pytest.approx([180.] * 96)
    assert [r["A"] for r in offer["internal_plan_mw"]] == pytest.approx(
        [10. if 60 <= t < 76 else 0. for t in range(96)])
    assert unit["daily_energy_mwh"] == pytest.approx(240.)
    assert unit["sustained_3h_mw"] == pytest.approx(10.)
    assert 0 <= unit["sustained_start_quarter"] <= 84
    assert unit["available_mw"] == pytest.approx([10.] * 96)
    assert unit["minimum_mw"] == pytest.approx([0.] * 96)
    assert sum(w for w, _ in unit["segments"]) == pytest.approx(10.)
    assert offer["inventory_and_transit_value_change_yuan"] == pytest.approx(-7200.)


def test_frozen_publication_is_separate_from_market_physics_and_strategy(company):
    api, case, prices = company
    water = {"A": .05}
    first = api.prepare_company_submission(case, prices, water)
    control = api.prepare_company_submission(case, prices, water, strategy="naive")
    assert first["company_forecast_sha256"] == control["company_forecast_sha256"]
    assert first["internal_plan_mw"] == control["internal_plan_mw"]
    assert first["offers"]["A"]["segments"] != control["offers"]["A"]["segments"]
    assert first["submission_sha256"] != control["submission_sha256"]
    independent = replace(case, available=tuple({"A": 4.} for _ in range(96)))
    market = api.apply_company_submission(independent, first)
    assert market.available == independent.available
    assert market.cascade is independent.cascade
    assert market.resources[0].segments == tuple(tuple(x) for x in first["offers"]["A"]["segments"])
    altered = deepcopy(first)
    altered["offers"]["A"]["segments"][1][1] += 1
    with pytest.raises(ValueError, match="摘要"):
        api.apply_company_submission(independent, altered)


def test_all_day_outage_and_invalid_forecast(company):
    api, case, prices = company
    outage = replace(case, available=tuple({"A": 0.} for _ in range(96)))
    frozen = api.prepare_company_submission(outage, prices, {"A": .05})
    assert frozen["offers"]["A"]["daily_energy_mwh"] == 0.
    assert frozen["offers"]["A"]["sustained_3h_mw"] == 0.
    assert all(r["A"] is None for r in frozen["marginal_water_cost_yuan_mwh"])
    with pytest.raises(ValueError, match="预期价"):
        api.prepare_company_submission(outage, prices[:-1], {"A": .05})


def test_three_hour_limit_is_independent_of_private_economic_withholding(company):
    api, case, prices = company
    station = replace(case.cascade.reservoirs[0], initial_storage_m3=54_000.)
    limited = replace(case, cascade=replace(case.cascade, reservoirs=(station,)))
    # With no arrivals, 54,000 m³ allows 15 MWh, or at most 5 MW over 3 h.
    # At 1 yuan/m³ storing water is preferable to selling at every forecast price.
    result = api.prepare_company_submission(limited, prices, {"A": 1.})
    certificate = result["offers"]["A"]
    assert all(row["A"] == pytest.approx(0.) for row in result["internal_plan_mw"])
    assert certificate["daily_energy_mwh"] == pytest.approx(15.)
    assert certificate["sustained_3h_mw"] == pytest.approx(5.)
    assert certificate["available_mw"] == pytest.approx([10.] * 96)
