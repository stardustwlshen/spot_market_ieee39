"""End-to-end numerical acceptance, independent of the Sichuan UI render."""
from dataclasses import replace

import pytest

pytest.importorskip("pyomo.environ")
pytest.importorskip("highspy")

from app.sichuan_v5_energy import Case, Line, Resource, day_ahead, realtime


def _case(load=80.0, cap=50.0):
    return Case(
        buses=(1, 2),
        lines=(Line("L", 1, 2, .1, cap),),
        resources=(
            Resource("A", 1, "generator", "thermal", 100., 20., initial_mw=50.),
            Resource("B", 2, "generator", "thermal", 100., 300., initial_mw=30.),
        ),
        demand=tuple({1: 0., 2: load} for _ in range(96)),
        available=tuple({} for _ in range(96)),
    )


def test_sichuan_rt_congested_pricing_is_first_executed_quarter_with_correct_units():
    case = _case()
    da = day_ahead(case, time_limit_s=30)
    assert [r["quarter"] for r in da] == list(range(96))
    # Change only first-quarter demand in the RT scenario. The result must
    # not copy DA power or return a second planned (unexecuted) quarter.
    rt_case = replace(case, demand=({1: 0., 2: 82.}, *case.demand[1:]))
    rt = realtime(rt_case, da, executed_periods=1, time_limit_s=30)
    assert len(rt) == 1 and rt[0]["quarter"] == 0
    row = rt[0]
    assert row["power"] == pytest.approx({"A": 50., "B": 32.})
    assert row["flows"] == pytest.approx({"L": 50.})
    assert row["raw_nodal_prices"] == pytest.approx({1: 20., 2: 300.})
    assert sum(row["shed"].values()) == pytest.approx(0.)
    assert sum(row["spill"].values()) == pytest.approx(0.)
    # Independent finite difference on dispatched quarter under same
    # commitment, avoiding the MW vs MWh normalization error.
    delta = 0.1
    bumped = replace(rt_case, demand=({1: 0., 2: 82. + delta}, *rt_case.demand[1:]))
    bump = realtime(bumped, da, executed_periods=1, time_limit_s=30)[0]
    marginal = sum((bump["power"][u] - row["power"][u]) * .25 * p
                   for u, p in (("A", 20.), ("B", 300.))) / (delta * .25)
    assert marginal == pytest.approx(row["raw_nodal_prices"][2])
