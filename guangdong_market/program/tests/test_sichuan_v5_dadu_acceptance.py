"""Independent replay of the archived physical and financial records."""

from collections import defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from hashlib import sha256
import json
import os

import pytest


HYDRO = {f"D{i:02}" for i in range(1, 20)}
RESERVOIRS = {"D01", "D03", "D04", "D10"}
COAL = {f"COAL{i}" for i in range(1, 5)}
GAS = {"GAS1", "GAS2"}
PV = {f"PV{i}" for i in range(1, 5)}
ALL = HYDRO | COAL | GAS | PV
SCENARIOS = ("wet_low", "dry_peak", "forecast_error")


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "app"))
    import sichuan_v5_dadu_case
    import sichuan_v5_dadu_run
    return sichuan_v5_dadu_case, sichuan_v5_dadu_run


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_catalog_and_independent_boundaries(modules, scenario):
    fixtures, _ = modules
    case, meta = fixtures.build_case(scenario)
    units = {r.id: r for r in case.resources}
    assert len(units) == len(case.resources) == 29 and set(units) == ALL
    assert sum(units[i].capacity_mw for i in HYDRO) == 22056
    assert sorted(units[i].capacity_mw for i in COAL) == [600, 600, 1000, 1000]
    assert all(units[i].capacity_mw == 600 and not units[i].segments for i in GAS)
    assert sum(units[i].capacity_mw for i in PV) == 8820
    assert {s["id"] for s in meta["stations"] if s["match"] != "确认"} == {
        "D09", "D13", "D14", "D15", "D18"}
    assert len({s["group"] for s in meta["stations"]}) > 1
    assert len({s["portfolio"] for s in meta["stations"]}) == 1
    assert len(case.buses) == 39 and len(case.lines) == 46
    assert all(u.bus in case.buses for u in units.values())
    assert {v.id for v in case.cascade.reservoirs if v.storage_max_m3 > 0} == RESERVOIRS
    assert {(r.upstream, r.downstream) for r in case.cascade.reaches} == {
        (f"D{i:02}", f"D{i+1:02}") for i in range(1,19)}
    load = [sum(row.values()) for row in case.demand]
    assert min(load) > 0
    assert max(load) == pytest.approx(meta["load_peak_mw"])
    assert sum(load) * .25 == pytest.approx(1000 * meta["load_energy_gwh"])
    assert all(set(s) == GAS and all(0 <= mw <= case.available[t][i] for i,mw in s.items())
               for t,s in enumerate(case.fixed_schedule))

    company = fixtures.company_snapshot(case, meta)
    assert company.cascade.natural_inflow_m3s != case.cascade.natural_inflow_m3s
    # A changed future RT observation scenario cannot change the bid-time forecast.
    altered = dict(meta, scenario="forecast_error" if scenario != "forecast_error" else "dry_peak")
    assert fixtures.company_snapshot(case, altered).cascade == company.cascade
    rt = fixtures.forecast_callback(meta)(case, 48, 56)
    assert rt.cascade.natural_inflow_m3s[:48] == case.cascade.natural_inflow_m3s[:48]
    assert rt.cascade.natural_inflow_m3s[56:] == case.cascade.natural_inflow_m3s[56:]
    assert rt.demand[48] != case.demand[48]


def _audit_physics(inputs, rows, *, executed):
    units = {r["id"]: r for r in inputs["assets"]}
    lines = inputs["lines"]
    cascade = inputs["market_case"]["cascade"]
    plants = {s["id"]: s for s in cascade["reservoirs"]}
    volumes = {s:v["initial_storage_m3"] for s,v in plants.items()}
    queues = {(r["upstream"], r["downstream"]):deque(r["initial_arrivals_m3"]) for r in cascade["reaches"]}
    for t, row in enumerate(rows):
        assert row["quarter"] == t and set(row["power"]) == ALL
        flow = row["flows"]
        buses = {int(b) for b in row["demand"]}
        balance = {int(b): -v for b,v in row["demand"].items()}
        edges = defaultdict(list)
        for unit, mw in row["power"].items():
            assert -.0001 <= mw <= units[unit]["capacity_mw"] + .0001
            balance[units[unit]["bus"]] += mw
        for line in lines:
            f, a, b = flow[line["id"]], line["source"], line["target"]
            assert abs(f) <= line["limit_mw"] + .0001
            balance[a] -= f
            balance[b] += f
            drop = f * line["reactance"] / 100
            edges[a].append((b, -drop))
            edges[b].append((a, drop))
        assert max(abs(v) for v in balance.values()) < .0001
        assert all(abs(v) < 1e-5 for field in ("shed", "spill") for v in row[field].values())
        assert abs(row["reliability_reserve_short_mw"]) < 1e-5
        angles = {min(buses): 0.}
        pending = deque(angles)
        while pending:
            a = pending.popleft()
            for b, diff in edges[a]:
                if b not in angles:
                    angles[b] = angles[a] + diff
                    pending.append(b)
                assert abs(angles[b] - angles[a] - diff) < 1e-6
        assert set(angles) == buses
        for g in GAS:
            assert row["power"][g] == pytest.approx(inputs["fixed_schedule"][t][g])

        water = row["hydro"]
        arrival = defaultdict(float)
        for (u, d), queue in queues.items():
            arrival[d] += queue.popleft()
            queue.append(900 * water[u]["release_m3s"])
        for i, station in plants.items():
            w = water[i]
            assert abs(w["upstream_arrival_m3"] - arrival[i]) < .001
            assert w["turbine_m3s"] == pytest.approx(row["power"][i] * station["generation_m3s_per_mw"], abs=1e-6)
            assert w["release_m3s"] == pytest.approx(w["turbine_m3s"] + w["eco_bypass_m3s"] + w["water_spill_m3s"], abs=1e-6)
            assert w["release_m3s"] >= station["min_release_m3s"] - 1e-6
            assert w["eco_bypass_m3s"] >= station["min_bypass_m3s"] - 1e-6
            volumes[i] += 900*(w["natural_inflow_m3s"] - w["release_m3s"]) + arrival[i]
            assert abs(volumes[i] - w["storage_m3"]) < .01
            assert station["storage_min_m3"] - .01 <= volumes[i] <= station["storage_max_m3"] + .01
            if i not in RESERVOIRS:
                assert abs(volumes[i]) < .01
        if executed:
            handoff = row["water_handoff"]
            assert handoff["next_quarter"] == t+1
            published = {(r["upstream"], r["downstream"]):r["next_arrivals_m3"] for r in handoff["in_transit"]}
            assert set(published) == set(queues)
            for link, queue in queues.items():
                assert published[link] == pytest.approx(list(queue), abs=.001)
        if t == 95:
            for i in RESERVOIRS:
                assert plants[i]["terminal_min_m3"] - .01 <= volumes[i] <= plants[i]["terminal_max_m3"] + .01


def audit_archive(result):
    """All inputs are public archived outputs, independent of solver internals."""
    inputs, company, money = result["inputs"], result["company"], result["money"]
    assets = {r["id"]:r for r in inputs["assets"]}
    assert set(assets) == ALL and set(company["offers"]) == HYDRO
    assert len(result["da"]) == 96
    assert len(result["rt"]) == result["summary"]["n_executed"] == len(money["quarters"])
    assert result["summary"]["bid_hash"] == company["submission_sha256"]
    payload = {k: company[k] for k in ("schema", "strategy", "as_of", "offers")}
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert sha256(data.encode("utf-8")).hexdigest() == company["submission_sha256"]
    assert set(result["raw_offers"]) == set(result["effective_offers"]) == ALL - GAS
    for i in HYDRO:
        blocks = company["offers"][i]["segments"]
        rating = assets[i]["capacity_mw"]
        assert 5 <= len(blocks) <= 10
        assert all(.05*rating - 1e-7 <= w <= .2*rating + 1e-7 and 0 <= p <= 800 and p == round(p)
                   for w,p in blocks)
        assert all(20 <= y[1]-x[1] <= 150 for x,y in zip(blocks,blocks[1:]))
        assert sum(w for w,_ in blocks) == pytest.approx(rating)
    _audit_physics(inputs, result["da"], executed=False)
    _audit_physics(inputs, result["rt"], executed=True)
    assert set(money["eligible"]) == ALL - GAS
    assert set(money["portfolio_members"]) == HYDRO
    rt_revenue = 0.
    for row, price in zip(result["rt"], money["quarters"]):
        assert price["quarter"] == row["quarter"] and price["price_source"] == "rt_sced"
        assert row["online"] == result["da"][row["quarter"]]["online"]
        for bus, raw in row["raw_nodal_prices"].items():
            expected = max(Decimal(0), min(Decimal(str(price["cap"])), Decimal(str(raw))))
            expected = expected.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            assert price["effective_price_by_bus"][bus] == pytest.approx(float(expected))
        for i in HYDRO:
            rt_revenue += row["power"][i] * .25 * price["effective_price_by_bus"][assets[i]["bus"]]
    # Each 15-minute station amount is rounded to cents before being accumulated.
    assert abs(rt_revenue - result["summary"]["company_revenue"]) <= .0051 * len(HYDRO) * len(result["rt"])


def test_executed_run_replay(modules, tmp_path):
    # CI runs a short, genuine solve. Point at completed archives to recheck full
    # runs without invoking a second solver (including both bidding strategies).
    _, runs = modules
    root = os.environ.get("DADU_ACCEPTANCE_RUNS")
    if root:
        names = runs.list_dadu(root)
        assert names, "No completed Dadu archives were found"
    else:
        root = tmp_path
        name, _ = runs.run_dadu(root, scenario="wet_low", strategy="water_value", periods=4)
        names = [name]
    matches = defaultdict(dict)
    for name in names:
        result = runs.read_dadu(root, name)
        audit_archive(result)
        inputs, company = result["inputs"], result["company"]
        key = (inputs["scenario"], inputs["seed"], inputs["periods"], inputs["water_value_scale"],
               inputs["corridor_scale"], inputs["gas_plan_scale"])
        matches[key][company["strategy"]] = (result["summary"]["boundary_hash"],
                                              company["company_forecast_sha256"],
                                              company["submission_sha256"])
    for pair in matches.values():
        if {"naive","water_value"} <= set(pair):
            assert pair["naive"][:2] == pair["water_value"][:2]
            assert pair["naive"][2] != pair["water_value"][2]
    if os.environ.get("DADU_REQUIRE_MATRIX") == "1":
        for scenario in SCENARIOS:
            assert matches[(scenario,39,96,1.,1.,1.)].keys() == {"naive","water_value"}
