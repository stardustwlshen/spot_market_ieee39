"""Energy bids remain; neither renewable curtailment nor hydro spill is penalized."""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc, results
from smm.da import run_da
from smm.config import load_config
from test_hydro_rolling import hydro_case


def case_with_re(dt=.25):
    case = hydro_case(T=1)
    case.cfg.base['dt_h'] = dt
    re = deepcopy(load_config().units['renewable'][0])
    re.update(id='W', bus=2, cap=100.)
    case.cfg.units['renewable'] = [re]
    case.bids['units']['W'] = dict(mode='price_quantity', segments=[dict(p_start=0., p_end=100., price=20.)])
    case.ren_fc = {'W': np.array([20.])}
    return case


@pytest.mark.parametrize('stage', ['DA', 'RT'])
@pytest.mark.parametrize('dt', [.25, .5])
def test_spill_and_re_curtailment_have_no_objective_penalty(stage, dt):
    case = case_with_re(dt)
    case.stage = stage
    # Historical saved configs may retain this key; it must have no active effect.
    case.cfg.units['hydro'][0]['spill_penalty'] = 1e9
    m = scuc.build_model(case)
    for var in m.component_data_objects(pyo.Var):
        var.set_value(0)
    base = pyo.value(m.obj)
    m.Qspill['H1', 0].set_value(30.)
    m.Pw['W', 0].set_value(10.)
    assert pyo.value(m.obj) == base
    costs = results.cost_breakdown(m)
    assert costs['hydro_spill_penalty'] == 0
    assert costs['objective_incl_penalties'] == base
    # Bid terms remain exactly yuan/MWh * MW * hours.
    m.pw['W', 0, 0].set_value(4.)
    m.ph['H1', 0, 0].set_value(3.)
    assert pyo.value(m.obj) - base == pytest.approx((20 * 4 + 5 * 3) * dt)
    costs = results.cost_breakdown(m)
    assert costs['renewable'] == pytest.approx(20 * 4 * dt)
    assert costs['hydro'] == pytest.approx(5 * 3 * dt)
    # Spillage still changes water conservation and ecological/outflow constraints.
    water_before = pyo.value(m.c_water['H1', 0].body)
    m.Qspill['H1', 0].set_value(31.)
    assert abs(pyo.value(m.c_water['H1', 0].body) - water_before) == pytest.approx(dt * .36)
    assert ('H1', 0) in m.c_eco and ('H1', 0) in m.c_qout


def test_energy_sced_retains_same_objective_and_physics():
    result = run_da(case_with_re(), verbose=False)
    m = result.model
    assert all(v.fixed for name in scuc.INT_VARS for v in getattr(m, name).values())
    assert result.stage_comparison['sced_minus_scuc_objective'] <= 1e-6
    assert result.costs['objective_incl_penalties'] == pytest.approx(result.costs['bid_cost_total'], abs=1e-6)
    assert result.costs['renewable'] > 0
    assert result.costs['hydro_spill_penalty'] == 0
    for con in m.c_water.values():
        assert abs(pyo.value(con.body)) < 1e-6
    for h in m.HS:
        assert pyo.value(m.V[h, 0]) >= m._md.hydro[h]['v_end'] - 1e-6


def test_default_units_no_longer_declare_spill_penalty():
    assert all('spill_penalty' not in h for h in load_config().units['hydro'])
