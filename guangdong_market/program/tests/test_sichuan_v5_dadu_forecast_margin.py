from dataclasses import replace
from pathlib import Path

import pytest


def test_d1_prior_commits_headroom_that_realtime_can_use(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "app"))
    from sichuan_v5_dadu_run import _uncertainty_boundary
    from sichuan_v5_energy import Case, Resource, day_ahead, realtime

    units = (Resource("cheap", 1, "public_coal", "thermal", 100., 100., 1., 100., 96.),
             Resource("slow", 1, "public_coal", "thermal", 10., 300., 1., 10., 0.,
                      min_up_quarters=8, startup_yuan=100.))
    base = Case((1,), (), units, ({1: 96.},) * 96, ({},) * 96)
    padded, record = _uncertainty_boundary(base, "forecast_error")
    assert record["da_extra_up_mw"] == 10.
    assert record["rt_requirement_mw"] == base.reserve_mw == 0.
    assert _uncertainty_boundary(base, "dry_peak")[0] == base
    da = day_ahead(padded)
    assert da[0]["online"]["slow"] == 1
    assert sum(da[0]["thermal_reliability_reserve_mw"].values()) >= 10. - 1e-7
    # Opening telemetry reveals a +9 MW error. Holding the extra again in RT
    # would require at least 115 MW from only 110 MW of online units.
    def opening_observation(case, start, stop):
        loads = list(case.demand)
        loads[start] = {1: 105.}
        return replace(case, demand=tuple(loads))
    rt = realtime(base, da, executed_periods=1, window_inputs=opening_observation)
    assert sum(rt[0]["power"].values()) == pytest.approx(105.)
    assert sum(rt[0]["shed"].values()) == 0.
    assert rt[0]["reliability_reserve_short_mw"] == 0.


def test_reserved_power_can_reach_a_congested_receiving_bus(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "app"))
    from sichuan_v5_energy import Case, Resource, Line, day_ahead

    fleet = (Resource("remote", 1, "public_coal", "thermal", 100., 10., 1., 100., 50.),
             Resource("local", 2, "public_coal", "thermal", 25., 300., 1., 25., 0.,
                      startup_yuan=100.))
    case = Case((1,2), (Line("to-city", 1, 2, .05, 50.),), fleet,
                ({1:0., 2:50.},)*96, ({},)*96, reserve_mw=10.)
    untested = day_ahead(case)
    assert untested[0]["online"]["local"] == 0
    assert untested[0]["flows"]["to-city"] == pytest.approx(50.)
    dispatch = day_ahead(replace(case,deliverable_up_mw=10.))
    for row in dispatch:
        assert row["online"]["local"] == 1
        activation = row["forecast_activation_mw"]
        assert sum(activation.values()) == pytest.approx(10.)
        stress_flow = row["forecast_stress_flows_mw"]["to-city"]
        assert stress_flow == pytest.approx(row["flows"]["to-city"] + activation["remote"])
        assert abs(stress_flow) <= 50. + 1e-7
