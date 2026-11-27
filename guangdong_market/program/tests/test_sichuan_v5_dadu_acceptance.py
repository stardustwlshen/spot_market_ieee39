"""Independent replay of the archived physical and financial records."""

from collections import defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from hashlib import sha256
import json
import math
import os
import re

import pytest


HYDRO = {f"D{i:02}" for i in range(1, 20)}
RESERVOIRS = {"D01", "D03", "D04", "D10"}
COAL = {f"COAL{i}" for i in range(1, 5)}
GAS = {"GAS1", "GAS2"}
PV = {f"PV{i}" for i in range(1, 5)}
ALL = HYDRO | COAL | GAS | PV
SCENARIOS = ("wet_low", "dry_peak", "forecast_error")
SLACK_TOL = 1e-5


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
    diagnostics = []
    for t, row in enumerate(rows):
        assert row["quarter"] == t and set(row["power"]) == ALL
        flow = row["flows"]
        buses = set(inputs["market_case"]["buses"])
        demand = {int(b): v for b, v in row["demand"].items()}
        shed = {int(b): v for b, v in row["shed"].items()}
        excess = {int(b): v for b, v in row["spill"].items()}
        assert set(demand) == set(shed) == set(excess) == buses
        assert all(math.isfinite(v) and v >= -1e-7 for v in (*shed.values(), *excess.values(),
                                                               row["reliability_reserve_short_mw"]))
        assert all(shed[b] <= demand[b] + .0001 for b in buses)
        balance = {b: shed[b] - excess[b] - demand[b] for b in buses}
        assert set(flow) == {line["id"] for line in lines}
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
        assert max(abs(v) for v in balance.values()) < .0001, f"第{t+1}段节点功率平衡不闭合"
        short = {"unserved_load_mw": sum(max(0., v) for v in shed.values()),
                 "excess_injection_mw": sum(max(0., v) for v in excess.values()),
                 "reserve_short_mw": max(0., row["reliability_reserve_short_mw"])}
        if "system_shortfalls" in row:
            assert row["system_shortfalls"] == pytest.approx(short, abs=1e-7)
        if not executed or inputs["scenario"] != "forecast_error":
            assert max(short.values()) <= SLACK_TOL, f"{'实时' if executed else '日前'}第{t+1}段出现可靠性松弛，{max(short.values()):.2f} MW"
        diagnostics.append(short)
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
            assert handoff["storage_m3"] == pytest.approx(volumes, abs=.01, rel=0)
            assert abs(handoff["basin_residual_m3"]) < .01
            published = {(r["upstream"], r["downstream"]):r["next_arrivals_m3"] for r in handoff["in_transit"]}
            assert set(published) == set(queues)
            for link, queue in queues.items():
                assert published[link] == pytest.approx(list(queue), abs=.001)
        if t == 95:
            for i in RESERVOIRS:
                assert plants[i]["terminal_min_m3"] - .01 <= volumes[i] <= plants[i]["terminal_max_m3"] + .01
    return diagnostics


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
    diagnostics = _audit_physics(inputs, result["rt"], executed=True)
    assert set(money["eligible"]) == ALL - GAS
    assert set(money["portfolio_members"]) == HYDRO
    n = len(result["rt"])
    assert money["executed_quarters"] == n
    assert money["complete_day"] == (n == 96)
    summary = result["summary"]
    assert summary["max_slack_mw"] == pytest.approx(max(max(s.values()) for s in diagnostics), abs=1e-7)
    unserved = .25 * sum(s["unserved_load_mw"] for s in diagnostics)
    if "unserved_energy_mwh" in summary:
        assert summary["unserved_energy_mwh"] == pytest.approx(unserved, abs=1e-7)
    quantities, amounts = defaultdict(float), defaultdict(float)
    bad_hours = set()
    eligible = ALL - GAS
    buses = set(inputs["market_case"]["buses"])
    for row, price, short in zip(result["rt"], money["quarters"], diagnostics):
        clean = max(short.values()) <= SLACK_TOL
        t = row["quarter"]
        hour = 1 + t // 4
        assert price["quarter"] == t and price["reliable"] is clean
        assert price["cap"] == result["policy"]["cap"]
        assert price["price_source"] == ("rt_sced" if clean else "withheld")
        assert row["online"] == result["da"][row["quarter"]]["online"]
        raw_lmp = {int(b): v for b, v in row["raw_nodal_prices"].items()}
        raw_copy = {int(b): v for b, v in price["raw_price_by_bus"].items()}
        effective = {int(b): v for b, v in price["effective_price_by_bus"].items()}
        assert set(raw_lmp) == set(raw_copy) == set(effective) == buses
        assert all(math.isfinite(val) for val in raw_lmp.values())
        assert raw_copy == raw_lmp
        for bus, raw in raw_lmp.items():
            if clean:
                expected = max(Decimal(0), min(Decimal(str(price["cap"])), Decimal(str(raw))))
                expected = expected.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                assert effective[bus] == pytest.approx(float(expected))
            else:
                assert effective[bus] is None
        total = sum(.25 * row["power"][i] for i in eligible)
        assert price["uniform_weight_mwh"] == pytest.approx(total, abs=1e-7)
        uniform_amount = 0.
        for i in eligible:
            mwh = .25 * row["power"][i]
            quantities[hour, i] += mwh
            if clean:
                value = mwh * effective[assets[i]["bus"]]
                amounts[hour, i] += value
                uniform_amount += value
        if not clean:
            bad_hours.add(hour)
            assert "dispatch_not_verified" in price["issues"]
        if clean and total > 0:
            assert price["uniform_price"] == pytest.approx(uniform_amount / total)
        else:
            assert price["uniform_price"] is None

    hours = range(1, (n+3)//4 + 1)
    indexed = {(v["hour"], v["resource_id"]): v for v in money["hourly"]}
    assert len(indexed) == len(money["hourly"]) == len(hours)*len(eligible)
    port = {v["hour"]:v for v in money["portfolio"]}
    assert set(port) == set(hours)
    def check_leg(record, hour, energy, cash):
        assert record["energy_mwh"] == pytest.approx(energy, abs=1e-7)
        if hour in bad_hours:
            assert "dispatch_not_verified" in record["issues"]
            assert record["executed_revenue"] is None
        else:
            assert record["executed_revenue"] == pytest.approx(cash, rel=1e-10, abs=1e-6)
        if hour in bad_hours or 4*hour > n:
            assert record["revenue"] is None and record["rt_nodal_price"] is None
        else:
            assert record["revenue"] == pytest.approx(cash, rel=1e-10, abs=1e-6)
            if energy == 0:
                assert record["rt_nodal_price"] is None
            else:
                assert record["rt_nodal_price"] == pytest.approx(cash / energy)
    for h in hours:
        for i in eligible:
            check_leg(indexed[h, i], h, quantities[h, i], amounts[h, i])
        check_leg(port[h], h, sum(quantities[h, i] for i in HYDRO), sum(amounts[h, i] for i in HYDRO))
    if bad_hours:
        assert money["data_quality"] == "incomplete"
        assert money["executed_portfolio_revenue"] is money["total_revenue"] is money["market_total_revenue"] is None
        assert summary["company_revenue"] is None
        if "water_adjusted_value" in summary:
            assert summary["water_adjusted_value"] is None
    else:
        company_cash = sum(amounts[h, i] for h in hours for i in HYDRO)
        assert money["executed_portfolio_revenue"] == pytest.approx(company_cash)
        assert summary["company_revenue"] == pytest.approx(company_cash)
        if n % 4:
            assert money["total_revenue"] is money["market_total_revenue"] is None
        else:
            assert money["total_revenue"] == pytest.approx(company_cash)
            assert money["market_total_revenue"] == pytest.approx(sum(amounts.values()))


def _source_version(market_sha, hydro_sha, files):
    assert market_sha and hydro_sha and files
    return (market_sha, hydro_sha, tuple(sorted(files.items())))


def _record_cohort(matches, result, current_version=None):
    from sichuan_v5_dadu_progress import source_hashes_match
    inputs, company, summary = result["inputs"], result["company"], result["summary"]
    version = _source_version(inputs["market_base_sha"], inputs["hydro_source_sha"], inputs["source_hashes"])
    if (current_version is not None and version[:2] == current_version[:2]
            and source_hashes_match(inputs["source_hashes"], dict(current_version[2]))):
        version = current_version
    assert inputs["boundary_hash"] == summary["boundary_hash"]
    if "issued_boundary_hash" in summary:
        issued = json.dumps(result["issued_forecasts"], sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")
        assert sha256(issued).hexdigest() == summary["issued_boundary_hash"]
    rule_pdf = (inputs.get("assumptions") or {}).get("rules_pdf_sha256")
    if (not summary.get("issued_boundary_hash") or not company.get("company_forecast_sha256")
            or not isinstance(rule_pdf, str) or re.fullmatch(r"[0-9a-f]{64}", rule_pdf) is None):
        return
    knobs = (inputs["seed"], inputs["periods"], inputs["water_value_scale"],
             inputs["corridor_scale"], inputs["gas_plan_scale"])
    # Physical/valuation and issued RT boundaries must agree within each
    # treatment pair. The three scenarios intentionally have different water.
    key = (version, knobs, inputs["scenario"], summary["boundary_hash"],
           company["company_forecast_sha256"], summary["issued_boundary_hash"], rule_pdf)
    runs = matches[key]
    bid = company["submission_sha256"]
    assert runs.setdefault(company["strategy"], bid) == bid


def test_executed_run_replay(modules, request):
    # CI runs a short, genuine solve. Point at completed archives to recheck full
    # runs without invoking a second solver (including both bidding strategies).
    _, runs = modules
    root = os.environ.get("DADU_ACCEPTANCE_RUNS")
    if root:
        names = runs.list_dadu(root)
        assert names, "No completed Dadu archives were found"
    else:
        root = request.getfixturevalue("tmp_path")
        events = []
        name, _ = runs.run_dadu(root, scenario="wet_low", strategy="water_value", periods=4, progress=events.append)
        assert list(dict.fromkeys(e.stage for e in events)) == ["inputs", "company", "market", "da", "rt", "archive"]
        assert [e.completed for e in events if e.stage == "da"] == [0, 1, 2]
        assert [e.completed for e in events if e.stage == "rt"] == [0, 1, 2, 3, 4]
        assert events[-1].stage == "archive" and events[-1].completed == events[-1].total
        names = [name]
    current = _source_version(runs.BASE_COMMIT, runs.DATA_COMMIT, runs.source_hashes())
    matches = defaultdict(dict)
    failures = []
    for name in names:
        result = runs.read_dadu(root, name)
        _record_cohort(matches, result, current)
        try:
            audit_archive(result)
        except AssertionError as exc:
            failures.append(f"{result['inputs']['scenario']} / {result['company']['strategy']} / {name}: {exc}")
    complete = defaultdict(set)
    for (version, knobs, scenario, *_), pair in matches.items():
        if {"naive","water_value"} <= set(pair):
            assert pair["naive"] != pair["water_value"]
            complete[version, knobs].add(scenario)
    if os.environ.get("DADU_REQUIRE_MATRIX") == "1":
        missing = set(SCENARIOS) - complete[current, (39,96,1.,1.,1.)]
        assert not missing, f"当前源码版本缺少同边界的完整策略对：{sorted(missing)}"
    assert not failures, "\n".join(failures)
