from dataclasses import replace

import pytest

from app.sichuan_v5_energy import Case, ClearingError, Resource, _build, _solve, day_ahead, realtime, validate
from app.sichuan_v5_hydro import Cascade, Reach, Reservoir, advance_cascade, initial_cascade_state


def two_station_case():
    # U's entire daily inflow is owed to its separate ecological bypass;
    # its initial water must remain available at 24:00. D cannot buffer water.
    upper = Reservoir('U', 0, 9_000, 9_000, 1, 20,
                      terminal_min_m3=9_000, min_bypass_m3s=1)
    lower = Reservoir('D', 0, 0, 0, 1, 20, regulation='run_of_river', min_bypass_m3s=.5)
    water = Cascade((upper, lower), (Reach('U', 'D', 2, (900., 900.)),),
                    ({'U': 1., 'D': 2.},) * 96, require_all_hydro=True)
    resources = (
        Resource('U', 1, 'provincial_hydro', 'hydro', 10., 0., ramp_mw_15min=1.),
        Resource('D', 1, 'provincial_hydro', 'hydro', 10., 5., ramp_mw_15min=1., initial_mw=2.5),
        Resource('G', 1, 'gas', 'fixed', 8., 0.),
    )
    return Case((1,), (), resources, ({1: 4.5},) * 96, ({'U': 10., 'D': 10., 'G': 8.},) * 96,
                cascade=water, fixed_schedule=({'G': 2.},) * 96)


def test_current_window_protects_final_stock_and_carries_only_executed_transit():
    case = two_station_case()
    da = day_ahead(case)
    assert da[-1]['hydro']['U']['storage_m3'] == pytest.approx(9000.)
    rows = realtime(case, da, executed_periods=4, window_quarters=1)
    for t, row in enumerate(rows):
        assert row['power'] == pytest.approx({'U': 0., 'D': 2.5, 'G': 2.})
        lower = row['hydro']['D']
        assert lower['release_m3s'] == pytest.approx(3.)
        assert lower['water_spill_m3s'] == pytest.approx(0.)
        assert lower['non_turbine_m3s'] == pytest.approx(.5)
        assert lower['storage_m3'] == pytest.approx(0.)
        assert row['water_handoff']['next_quarter'] == t + 1
        assert row['water_handoff']['in_transit'][0]['next_arrivals_m3'] == pytest.approx([900., 900.])
        assert row['water_handoff']['basin_exit_m3']['D'] == pytest.approx(2700.)
        assert abs(row['water_handoff']['basin_residual_m3']) < 1.e-5


def test_reject_detached_station_nonmarket_overdispatch_and_changed_bid():
    case = two_station_case()
    extra = Resource('HUD', 1, 'provincial_hydro', 'hydro', 8., 80.)
    with pytest.raises(ClearingError, match='梯级'):
        validate(replace(case, resources=(*case.resources, extra)))
    with pytest.raises(ClearingError, match='非市场计划'):
        validate(replace(case, fixed_schedule=({'G': 9.},) * 96))
    with pytest.raises(ClearingError, match='可用容量'):
        validate(replace(case, available=({'D': 10., 'G': 8.},) * 96))
    da = day_ahead(case)
    with pytest.raises(ClearingError, match='冻结'):
        realtime(case, da, executed_periods=1, window_inputs=lambda c,t,s:
                 replace(c, resources=(replace(c.resources[0], offer_yuan_mwh=50.), *c.resources[1:])))


def test_independent_ledger_rejects_day_end_target_breach():
    c = two_station_case().cascade
    c = replace(c, natural_inflow_m3s=c.natural_inflow_m3s[:1])
    with pytest.raises(ValueError, match='日末库存'):
        advance_cascade(c, initial_cascade_state(c), {'U': 1., 'D': 2.5}, {'U': 0., 'D': 0.})


def test_thermal_minimum_block_cost_is_paid_per_online_hour():
    from pyomo.environ import value
    coal = Resource('C', 1, 'public_coal', 'thermal', 20., 80., minimum_mw=10.,
                    initial_mw=10., min_up_quarters=288, min_down_quarters=96,
                    initial_state_age_quarters=1, online_cost_yuan_hour=200.)
    case = Case((1,), (), (coal,), ({1: 10.},)*96, ({},)*96)
    model = _build(case, 0, 1)
    _solve(model, mip=True, time_limit_s=20)
    assert value(model.objective) == pytest.approx(.25*(10.*80.+200.))
    assert value(model.online['C',0]) == 1
