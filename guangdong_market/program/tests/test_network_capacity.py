import numpy as np
import pytest
from smm.config import load_config
from smm.network import build_network


def test_overall_capacity_applies_after_individual_compression():
    cfg = load_config()
    cfg.base['line_capacity_multiplier'] = 1.
    reference = build_network(cfg)
    cfg.base['line_capacity_multiplier'] = 1.2
    expanded = build_network(cfg)
    np.testing.assert_allclose(expanded.branches.rate_mw, reference.branches.rate_mw * 1.2)
    np.testing.assert_allclose(expanded.ptdf, reference.ptdf)
    # This is a capacity assumption, not a change of reactance or network topology.
    assert expanded.branches.branch_id.tolist() == reference.branches.branch_id.tolist()


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_invalid_overall_capacity_rejected(value):
    cfg=load_config();cfg.base['line_capacity_multiplier']=value
    with pytest.raises(ValueError, match='line_capacity_multiplier'):
        build_network(cfg)
