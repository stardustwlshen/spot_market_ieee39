from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from sichuan_v5_dadu_policy import operator_hydro_envelope
from sichuan_v5_energy import Case, Resource
from sichuan_v5_hydro import Cascade, Reach, Reservoir


def two_stations(terminal_min=0):
    # One packet of water (900 m³) can make 1 MW for one slot at each
    # station, arriving at the second station one slot after its release.
    up = Reservoir("U", 0, 900, 900, 1, 1, terminal_min_m3=terminal_min)
    down = Reservoir("D", 0, 0, 0, 1, 1, regulation="run_of_river")
    cascade = Cascade((up, down), (Reach("U", "D", 1, (0.,)),),
                      tuple({"U": 0., "D": 0.} for _ in range(96)), True)
    units = tuple(Resource(key, 1, "provincial_hydro", "hydro", 1., 0.) for key in ("U", "D"))
    available = tuple({"U": float(t in (0, 1)), "D": float(t in (1, 2))} for t in range(96))
    return Case((1,), (), units, (), available, cascade=cascade)


def test_envelope_joint_simultaneity_separate_daily_maximum_and_terminal_stock():
    case = two_stations()
    mw, daily = operator_hydro_envelope(case)
    assert mw == pytest.approx([1., 1., 1.] + [0.] * 93)
    # Both stations can reach 1 MW at t=1, but not simultaneously. Also,
    # adding the three separate 1-MW maxima overstates the daily 0.5 MWh.
    assert daily == pytest.approx(.5)
    assert sum(mw) * .25 > daily
    mw, daily = operator_hydro_envelope(two_stations(450))
    assert mw[:3] == pytest.approx([.5] * 3)
    assert daily == pytest.approx(.25)
    # A changed commercial bid has no impact on independently feasible water.
    altered = replace(case, resources=tuple(replace(u, offer_yuan_mwh=770.) for u in case.resources))
    assert operator_hydro_envelope(altered) == operator_hydro_envelope(case)
