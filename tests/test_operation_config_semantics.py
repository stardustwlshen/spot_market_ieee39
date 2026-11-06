"""Malformed configuration must not silently reverse an explicit market policy."""
import numpy as np
import pytest

from smm import scuc
from smm.case import reserve_requirement
from smm.config import load_config
from smm.input_validation import validate_physical_inputs
from test_storage_reserve import battery_case, solve


@pytest.mark.parametrize('key', ['include_ctg', 'storage_reserve', 'down_enabled'])
@pytest.mark.parametrize('bad', ['false', 'true', 0, 1, None, [], {}])
def test_reserve_switches_do_not_coerce_truthiness(key, bad):
    cfg = load_config()
    cfg.mode['reserve'][key] = bad
    with pytest.raises(ValueError, match=key):
        reserve_requirement(cfg, np.array([100.]), np.array([0.]))


@pytest.mark.parametrize('key', ['rho_L', 'rho_RE', 'rho_L_dn', 'rho_RE_dn'])
@pytest.mark.parametrize('bad', [-.01, float('nan'), float('inf'), True, '.03'])
def test_negative_or_invalid_coefficients_cannot_hide_behind_contingency(key, bad):
    cfg = load_config()
    cfg.mode['reserve'][key] = bad
    with pytest.raises(ValueError, match=key):
        reserve_requirement(cfg, np.array([100.]), np.array([20.]))


@pytest.mark.parametrize('bad', [-.01, 1.01, float('nan'), float('inf'), True, '0'])
def test_renewable_reserve_confidence_cannot_create_more_than_headroom(bad):
    case = battery_case()
    case.cfg.mode['reserve']['mu_RE'] = bad
    with pytest.raises(ValueError, match='mu_RE'):
        scuc.build_model(case)


@pytest.mark.parametrize('bad', ['hydro', ['hydroo'], ['coal', 'coal'], [False], None])
def test_providers_is_a_supported_type_list(bad):
    cfg = load_config()
    cfg.mode['reserve']['providers'] = bad
    with pytest.raises(ValueError, match='providers'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('bad', [-1., float('nan'), float('inf'), True, '10'])
def test_contingency_values_are_not_silently_replaced_or_masked(bad):
    cfg = load_config()
    cfg.base['n1_loss_mw'] = bad
    with pytest.raises(ValueError, match='n1_loss_mw'):
        reserve_requirement(cfg, np.array([100.]), np.array([0.]))
    cfg.base['n1_loss_mw'] = None
    with pytest.raises(ValueError, match='extra_n1'):
        reserve_requirement(cfg, np.array([100.]), np.array([0.]), extra_n1=bad)


@pytest.mark.parametrize('section,key', [('solver', 'storage_binary'), ('solver', 'tighten'),
                                        ('solver', 'warm_start'), ('intraday', 'enabled'),
                                        ('realtime', 'first_period_actual')])
@pytest.mark.parametrize('bad', ['false', 0, 1, None])
def test_model_policy_switches_are_explicit_booleans(section, key, bad):
    cfg = load_config()
    (cfg.base if section == 'solver' else cfg.mode)[section][key] = bad
    with pytest.raises(ValueError, match=key):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('bad', [0, -1, 1.5, True, '4', float('nan')])
def test_pumped_mode_step_cannot_be_truncated_or_disable_lock(bad):
    cfg = load_config()
    cfg.mode['pumped']['mode_step_periods'] = bad
    with pytest.raises(ValueError, match='mode_step_periods'):
        validate_physical_inputs(cfg)


def test_false_string_does_not_supply_reserve_but_valid_false_remains_zero():
    case = battery_case(enabled='false')
    with pytest.raises(ValueError, match='storage_reserve'):
        scuc.build_model(case)
    model = solve(battery_case(enabled=False))
    assert model.rs_up['ES1', 0].value == pytest.approx(0.)
    assert not hasattr(model, 'c_rsup_soc')


def test_valid_boundary_policies_and_legacy_optional_defaults_are_preserved():
    cfg = load_config()
    cfg.mode['reserve'].update(providers=[], mu_RE=1., storage_reserve=False, T_dur_h=float('nan'))
    for key in ('storage_binary', 'tighten', 'warm_start'):
        cfg.base['solver'].pop(key, None)
    cfg.mode['intraday'].pop('enabled', None)
    cfg.mode['realtime'].pop('first_period_actual', None)
    cfg.mode['pumped'].pop('mode_step_periods', None)
    validate_physical_inputs(cfg)
