"""A disabled resource family must not break completed DA/ID/RT settlement."""
import numpy as np
import pandas as pd
import pytest

from smm.full import run_full
from smm.input_validation import validate_physical_inputs
from test_da_stages import tiny_case


@pytest.mark.parametrize('intraday', [False, True])
@pytest.mark.parametrize('storage_mode', ['value', 'sced_anchor', 'fixed_da'])
def test_no_batteries_all_storage_policies_complete_settlement(storage_mode, intraday):
    case = tiny_case(reserve=0.)
    assert case.cfg.units['storage'] == []
    case.cfg.mode['realtime']['storage']['mode'] = storage_mode
    case.cfg.mode['intraday']['enabled'] = intraday
    case.cfg.mode['reserve'].update(up_requirement_mode='formula',include_ctg=False, rho_L=0., rho_RE=0.)
    case.profiles = pd.DataFrame({'period': [1], 'time': ['00:00'],
        **{f'load_{stage}': [100.] for stage in ('actual', 'da', 'id', 'rt')}})
    validate_physical_inputs(case.cfg)
    result = run_full(case.cfg, case, periods=1, log=lambda *a: None)
    assert len(result['rr'].rt) == 1
    assert len(result['rr'].id_rounds) == int(intraday)
    assert result['settlement_calculated'] is True
    assert result['rt']['storage'].empty
    if storage_mode == 'fixed_da':
        assert result['checks']['storage_vs_da_err_mw'] == 0.
    assert result['checks']['physical_feasible_checked_constraints'] is True
    np.testing.assert_allclose(result['settle']['detail']['rt_amount'], 0., atol=1e-6)
    assert result['settle']['reconciliation']['residual'].abs().max() < 1e-6
