from copy import deepcopy
import pytest
from smm.config import load_config, validate_auxiliary_pricing


def test_approved_current_policy_defaults():
    cfg = load_config()
    assert cfg.mode['auxiliary_pricing'] == {
        'enabled': True,
        'rt_tracking_policy': 'remove',
        'penalties_yuan_per_mwh': {'balance': 10000, 'line': 1000, 'reserve': 2500},
        'dispatch_band': {'relative_to_dispatch': .03, 'minimum_mw': 1.0},
    }
    assert cfg.mode['penalties'] == {'balance': 1e6, 'line': 10000, 'reserve': 1e5}
    assert cfg.mode['reserve']['storage_reserve'] is True
    assert cfg.mode['reserve']['down_enabled'] is False
    assert cfg.mode['reserve']['T_dur_h'] == .25
    assert cfg.mode['realtime']['tracking_policy']['penalty_yuan_per_discharge_mwh'] == 1000
    # Objective convention remains yuan/MWh * MW * hours.
    for dt in [.25, .5]:
        assert cfg.mode['auxiliary_pricing']['penalties_yuan_per_mwh']['line'] * (1 / dt) * dt == 1000


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '1000'])
def test_invalid_price_rejected(value):
    mode = deepcopy(load_config().mode)
    mode['auxiliary_pricing']['penalties_yuan_per_mwh']['line'] = value
    with pytest.raises(ValueError, match='有限非负'):
        validate_auxiliary_pricing(mode)


def test_no_policy_guess_or_implicit_activation():
    mode = deepcopy(load_config().mode)
    mode['auxiliary_pricing']['enabled'] = 'true'
    with pytest.raises(ValueError, match='布尔值'):
        validate_auxiliary_pricing(mode)
    mode['auxiliary_pricing']['enabled'] = False
    mode['auxiliary_pricing']['dispatch_band'] = {'relative': .01}
    with pytest.raises(ValueError, match='须配置'):
        validate_auxiliary_pricing(mode)
    validate_auxiliary_pricing({'pricing_penalties': {'balance': 1e4, 'line': 500, 'reserve': 500}})


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '1000'])
def test_invalid_discharge_equivalent_anchor_price_rejected(value):
    mode = deepcopy(load_config().mode)
    mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = value
    with pytest.raises(ValueError, match='有限非负'):
        validate_auxiliary_pricing(mode)


def test_absent_or_null_anchor_override_retains_historical_option():
    mode = deepcopy(load_config().mode)
    mode['auxiliary_pricing'].pop('anchor_penalty_yuan_per_discharge_mwh', None)
    validate_auxiliary_pricing(mode)
    mode['auxiliary_pricing']['anchor_penalty_yuan_per_discharge_mwh'] = None
    validate_auxiliary_pricing(mode)
