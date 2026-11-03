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
