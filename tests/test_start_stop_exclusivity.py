"""Independent physical ramp regression for zero minimum-duration settings.

Run with the repository's src and tests on PYTHONPATH. The original abd1cb7
fails every test here because its transition identity permits v=w=1.
"""
import numpy as np
import pyomo.environ as pyo
import pytest

from smm.da import run_da
from test_da_stages import tiny_case


@pytest.mark.parametrize('startup_mode', ['state_dependent', 'uniform_per_unit'])
@pytest.mark.parametrize('minimum_up_h', [0., 1.])
@pytest.mark.parametrize('direction', ['up', 'down'])
def test_online_unit_cannot_fake_start_stop_to_bypass_ramp(startup_mode, minimum_up_h, direction):
    case = tiny_case(reserve=0.)
    case.cfg.mode['startup_cost_mode'] = startup_mode
    case.cfg.base['solver'].update(tighten=False, commit_step_periods=1)
    case.net.branches['rate_mw'] = 200.
    unit = case.cfg.units['thermal'][0]
    unit.update(pmin=50., ramp=1., ut=minimum_up_h, dt=0.)
    unit['init'].update(p0=50. if direction == 'up' else 100.)
    case.load_bus = {2: np.array([100. if direction == 'up' else 50.])}
    if startup_mode == 'uniform_per_unit':
        for uid in ('CHEAP', 'LOCAL'):
            case.bids['units'][uid]['startup_cost_yuan'] = 0.
    result = run_da(case, verbose=False)
    model = result.model
    u, v, w, output = [pyo.value(getattr(model, name)['CHEAP', 0]) for name in ('u', 'v', 'w', 'P')]
    assert u == 1.
    assert v + w <= 1. + 1e-7, 'A single unit cannot start and stop while continuously online'
    assert abs(output-unit['init']['p0']) <= unit['ramp']*60*case.cfg.dt + 1e-6
