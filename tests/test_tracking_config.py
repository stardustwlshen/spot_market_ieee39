from copy import deepcopy
import pytest
from smm.config import load_config, validate_tracking_policy, validate_auxiliary_pricing


def test_explicit_tracking_schema_and_legacy_absence():
    m = deepcopy(load_config().mode)
    validate_tracking_policy(m)
    m['realtime']['tracking_policy'] = dict(mode='soft_deliverable_energy', penalty_yuan_per_discharge_mwh=1000)
    validate_tracking_policy(m)
    m['auxiliary_pricing'].pop('anchor_penalty_yuan_per_discharge_mwh', None)
    m['auxiliary_pricing']['rt_tracking_policy'] = 'remove'
    validate_auxiliary_pricing(m)


@pytest.mark.parametrize('bad', [0, -1, True, float('nan'), float('inf'), '1000'])
def test_bad_tracking_coefficient_rejected(bad):
    m = deepcopy(load_config().mode)
    m['realtime']['tracking_policy'] = dict(mode='soft_deliverable_energy', penalty_yuan_per_discharge_mwh=bad)
    with pytest.raises(ValueError, match='有限正数'):
        validate_tracking_policy(m)
