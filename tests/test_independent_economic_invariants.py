"""Independent marginal-price and settlement reconstruction, no golden outputs."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import scuc, settlement
from smm.da import run_da
from smm.rolling import snapshot
from test_da_stages import tiny_case


@pytest.mark.parametrize('dt', [.25, .5, 1.])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('auxiliary', [False, True])
def test_price_derivative_and_cash_balance_both_line_directions(dt, reverse, auxiliary):
    case = tiny_case(reserve=0.)
    case.cfg.base['dt_h'] = dt
    case.cfg.mode['realtime']['interval_min'] = dt * 60
    case.cfg.mode['auxiliary_pricing']['enabled'] = auxiliary
    demand_bus = 1 if reverse else 2
    case.load_bus = {demand_bus: np.array([100.])}
    if reverse:
        case.bids['units']['CHEAP']['segments'][0]['price'] = 700.
        case.bids['units']['LOCAL']['segments'][0]['price'] = 10.
    da = run_da(case, verbose=False)
    expected = [700., 10.] if reverse else [10., 700.]
    np.testing.assert_allclose(da.prices['lmp'][:, 0], expected, atol=1e-7)
    np.testing.assert_allclose(da.prices['lmp_decomp'], da.prices['lmp'], atol=1e-7)
    assert da.tables['lines'].flow_mw.iloc[0] == pytest.approx(-50. if reverse else 50.)
    assert da.prices['mu_minus' if reverse else 'mu_plus'][0, 0] == pytest.approx(690.)
    pricing_model = da.pricing_model if auxiliary else da.model
    for bus in (1, 2):
        changed = pricing_model.clone()
        constraint = changed.c_inj[bus, 0]
        constraint.set_value(constraint.body == pyo.value(constraint.upper) + 1e-3)
        scuc.solve_sced(changed)
        derivative = (pyo.value(changed.obj)-pyo.value(pricing_model.obj))/1e-3/dt
        assert derivative == pytest.approx(expected[bus-1], abs=1e-5)
    rt_case = deepcopy(case)
    rt_case.load_bus[demand_bus][0] += 5.
    rt = run_da(rt_case, verbose=False)
    result = settlement.settle(da, SimpleNamespace(rt=[dict(t=0, snap=snapshot(rt.model), prices=rt.prices)]))
    detail = result['detail']
    independently_calculated = (detail.q_da*detail.lmp_da+(detail.q_rt-detail.q_da)*detail.lmp_rt)*dt
    np.testing.assert_allclose(detail.total, independently_calculated, atol=1e-7)
    assert abs(detail.q_da.sum()) < 1e-7 and abs(detail.q_rt.sum()) < 1e-7
    assert result['reconciliation'].residual.abs().max() < 1e-7
    assert result['reconciliation'].max_power_imbalance_mw.max() < 1e-7
