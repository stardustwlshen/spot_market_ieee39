"""Topology/lag input validation only; no optimization or solver invocation."""
from copy import deepcopy

import pytest

from smm.config import load_config
from smm.input_validation import validate_physical_inputs


def chain():
    cfg = load_config()
    first = deepcopy(cfg.units['hydro'][0])
    first.update(id='H1', upstream=None, lag_periods=0, enabled=True)
    second = deepcopy(first)
    second.update(id='H2', upstream='H1', lag_periods=2)
    cfg.units['hydro'] = [first, second]
    return cfg


@pytest.mark.parametrize('lag', [0, 2, 2.0])
def test_valid_chain_preserved(lag):
    cfg = chain()
    cfg.units['hydro'][1]['lag_periods'] = lag
    before = deepcopy(cfg.units)
    validate_physical_inputs(cfg)
    assert cfg.units == before


def test_self_loop_rejected():
    cfg = chain()
    cfg.units['hydro'][0]['upstream'] = 'H1'
    with pytest.raises(ValueError, match='H1.*自身'):
        validate_physical_inputs(cfg)


def test_two_station_cycle_rejected_even_with_positive_lag():
    cfg = chain()
    cfg.units['hydro'][0].update(upstream='H2', lag_periods=2)
    with pytest.raises(ValueError, match='环路'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('upstream', ['EXTERNAL', '', 123, []])
def test_unknown_upstream_rejected(upstream):
    cfg = chain()
    cfg.units['hydro'][1]['upstream'] = upstream
    with pytest.raises(ValueError, match='H2.upstream'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('filtered', [False, True])
def test_disabled_upstream_rejected(filtered):
    cfg = chain()
    if filtered:
        cfg.units['hydro'].pop(0)
        cfg.units['_disabled'] = ['H1']
    else:
        cfg.units['hydro'][0]['enabled'] = False
    with pytest.raises(ValueError, match='H2.upstream'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('station', [0, 1])
@pytest.mark.parametrize('lag', [.5, -1, True, float('nan'), float('inf'), '2', None])
def test_invalid_lag_rejected_even_without_upstream(station, lag):
    cfg = chain()
    cfg.units['hydro'][station]['lag_periods'] = lag
    with pytest.raises(ValueError, match=f'H{station + 1}.lag_periods'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('lags', [(0, 0), (2, 2), (0, 3)])
def test_fork_rejected_before_full_release_can_be_duplicated(lags):
    cfg = chain()
    cfg.units['hydro'][1]['lag_periods'] = lags[0]
    third = deepcopy(cfg.units['hydro'][1])
    third.update(id='H3', lag_periods=lags[1])
    cfg.units['hydro'].append(third)
    with pytest.raises(ValueError, match='H1.*多个下游.*H2.*H3'):
        validate_physical_inputs(cfg)


def test_disabled_second_child_does_not_restrict_the_active_chain():
    cfg = chain()
    third = deepcopy(cfg.units['hydro'][1])
    third.update(id='H3', enabled=False)
    cfg.units['hydro'].append(third)
    validate_physical_inputs(cfg)


def test_long_chain_and_disjoint_chain_are_supported():
    cfg = chain()
    third = deepcopy(cfg.units['hydro'][1])
    third.update(id='H3', upstream='H2')
    fourth = deepcopy(cfg.units['hydro'][0])
    fourth.update(id='H4')
    fifth = deepcopy(cfg.units['hydro'][1])
    fifth.update(id='H5', upstream='H4')
    cfg.units['hydro'].extend([third, fourth, fifth])
    before = deepcopy(cfg.units)
    validate_physical_inputs(cfg)
    assert cfg.units == before


@pytest.mark.parametrize('stage', ['DA', 'ID', 'RT'])
def test_fork_is_rejected_at_every_mutable_model_build(stage):
    from smm.scuc import build_model
    from test_hydro_rolling import hydro_case
    c = hydro_case(T=1)
    c.stage = stage
    for name in ('H2', 'H3'):
        h = deepcopy(c.cfg.units['hydro'][0])
        h.update(id=name, upstream='H1', lag_periods=0)
        c.cfg.units['hydro'].append(h)
        c.bids['units'][name] = deepcopy(c.bids['units']['H1'])
    with pytest.raises(ValueError, match='多个下游'):
        build_model(c)
