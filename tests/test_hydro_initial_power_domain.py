"""Rolling actual power must be in the exact sum of online units' stable zones."""
from copy import deepcopy
from itertools import product

import pytest

from smm import rolling, scuc
from smm.config import load_config
from smm.input_validation import hydro_aggregate_power_intervals, validate_physical_inputs
from test_hydro_rolling import hydro_case


def case_with_actual(power, online, *, total=2):
    c = hydro_case(T=1)
    h = c.cfg.units['hydro'][0]
    h.update(n_units=total, zones=[[10., 20.], [80., 100.]],
             init_units_on=1, init_p_unit=10., ramp=100.)
    c.bids['units']['H1']['segments'][0]['p_end'] = 100. * total
    state = rolling.initial_state(c.cfg)
    state.hydro_p['H1'] = power
    state.hydro_online['H1'] = online
    c.init = {key: value for key, value in vars(state).items() if key != 't'}
    return c


@pytest.mark.parametrize('power', [0., 5., 21., 50., 79., 101.])
def test_one_online_unit_cannot_start_in_a_forbidden_region(power):
    c = case_with_actual(power, 1)
    with pytest.raises(ValueError, match='H1.*稳定区聚合可达域'):
        scuc.build_model(c)


@pytest.mark.parametrize('power', [10., 20., 80., 100.])
def test_one_unit_zone_endpoints_are_accepted(power):
    c = case_with_actual(power, 1)
    validate_physical_inputs(c.cfg, c.init)


@pytest.mark.parametrize('power', [20., 40., 90., 100., 120., 160., 200.])
def test_two_units_may_use_different_stable_zones(power):
    c = case_with_actual(power, 2)
    # E.g. 100 MW = 10+90. Its average 50 is in the single-unit forbidden gap.
    validate_physical_inputs(c.cfg, c.init)
    model = scuc.build_model(c)
    assert model._md.hydro_units['H1']['p0'] == power
    assert model._md.hydro_units['H1']['units0'] == 2


@pytest.mark.parametrize('power', [0., 19., 41., 60., 89., 121., 140., 159., 201.])
def test_two_unit_total_must_not_fill_gaps_between_attainable_sums(power):
    c = case_with_actual(power, 2)
    with pytest.raises(ValueError, match='稳定区聚合可达域'):
        validate_physical_inputs(c.cfg, c.init)


def test_zero_online_units_require_zero_power_with_existing_numeric_tolerance():
    for power in (0., 5e-7, -5e-7):
        c = case_with_actual(power, 0)
        validate_physical_inputs(c.cfg, c.init)
    for power in (1., -2e-6):
        c = case_with_actual(power, 0)
        with pytest.raises(ValueError, match='稳定区聚合可达域'):
            validate_physical_inputs(c.cfg, c.init)


@pytest.mark.parametrize('online', [True, '1', None, -1, .5, 3, float('nan')])
def test_invalid_actual_online_count_fails_closed(online):
    c = case_with_actual(10., online)
    with pytest.raises(ValueError, match='hydro_online'):
        validate_physical_inputs(c.cfg, c.init)


@pytest.mark.parametrize('power', [True, '10', None, float('nan'), float('inf')])
def test_invalid_actual_power_fails_closed(power):
    c = case_with_actual(power, 1)
    with pytest.raises(ValueError, match='hydro_p'):
        validate_physical_inputs(c.cfg, c.init)


@pytest.mark.parametrize('name', ['hydro_p', 'hydro_online'])
@pytest.mark.parametrize('damage', ['missing', 'extra', 'not_mapping'])
def test_actual_hydro_maps_are_complete_and_exact(name, damage):
    c = case_with_actual(10., 1)
    if damage == 'missing': c.init[name].pop('H1')
    elif damage == 'extra': c.init[name]['unknown'] = 0.
    else: c.init[name] = []
    with pytest.raises(ValueError, match=name):
        validate_physical_inputs(c.cfg, c.init)


def test_membership_tolerance_is_not_multiplied_by_online_count():
    for power in (40. + 5e-7, 90. - 5e-7):
        c = case_with_actual(power, 2)
        validate_physical_inputs(c.cfg, c.init)
    for power in (40. + 2e-6, 90. - 2e-6):
        c = case_with_actual(power, 2)
        with pytest.raises(ValueError, match='稳定区聚合可达域'):
            validate_physical_inputs(c.cfg, c.init)


def test_interval_union_matches_independent_exhaustive_zone_assignments():
    zones = [(1., 2.), (5., 6.), (10., 12.)]
    for count in range(5):
        actual = hydro_aggregate_power_intervals(zones, count)
        combinations = [(sum(z[0] for z in assignment), sum(z[1] for z in assignment))
                        for assignment in product(zones, repeat=count)]
        # Include every endpoint and midpoint between endpoints in the independent oracle.
        points = sorted({x for interval in combinations for x in interval})
        probes = points + [(a+b)/2 for a, b in zip(points, points[1:])]
        probes += [-1., max(points)+1.]
        for power in probes:
            expected = any(lo <= power <= hi for lo, hi in combinations)
            assert any(lo <= power <= hi for lo, hi in actual) == expected
        assert all(a[1] < b[0] for a, b in zip(actual, actual[1:]))


@pytest.mark.parametrize('season', ['wet', 'dry'])
def test_current_declared_initial_state_is_unchanged(season):
    cfg = load_config(season=season)
    state = rolling.initial_state(cfg)
    initial = {key: value for key, value in vars(state).items() if key != 't'}
    before = deepcopy(initial)
    validate_physical_inputs(cfg, initial)
    assert initial['hydro_p'] == before['hydro_p']
    assert initial['hydro_online'] == before['hydro_online']
