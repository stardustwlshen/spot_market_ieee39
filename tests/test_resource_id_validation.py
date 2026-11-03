"""Resource identity input guards; no solver calls."""
from copy import deepcopy

import pytest

from smm.config import load_config
from smm.input_validation import validate_physical_inputs


FAMILIES = ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie')


@pytest.mark.parametrize('family', FAMILIES)
def test_duplicate_within_active_family_rejected(family):
    cfg = load_config()
    uid = cfg.units[family][0]['id']
    cfg.units[family].append(deepcopy(cfg.units[family][0]))
    with pytest.raises(ValueError, match=f'{uid}.*重复'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('family', FAMILIES[1:])
def test_cross_family_duplicate_rejected(family):
    cfg = load_config()
    cfg.units[family][0]['id'] = cfg.units['thermal'][0]['id']
    with pytest.raises(ValueError, match='全局唯一'):
        validate_physical_inputs(cfg)


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('bad', ['', ' \t', None, True, 42, []])
def test_invalid_id_rejected_before_dict_construction(family, bad):
    cfg = load_config()
    cfg.units[family][0]['id'] = bad
    with pytest.raises(ValueError, match=rf'{family}\[0\].id.*非空字符串'):
        validate_physical_inputs(cfg)


def test_missing_id_is_clear_value_error():
    cfg = load_config()
    del cfg.units['hydro'][0]['id']
    with pytest.raises(ValueError, match=r'hydro\[0\].id'):
        validate_physical_inputs(cfg)


def test_disabled_duplicate_does_not_pollute_active_namespace():
    cfg = load_config()
    copy = deepcopy(cfg.units['hydro'][0])
    copy['enabled'] = False
    cfg.units['hydro'].extend([copy, deepcopy(copy)])
    validate_physical_inputs(cfg)
