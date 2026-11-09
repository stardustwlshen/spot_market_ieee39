"""Profit bookkeeping stays within the validated executed resource/period grid."""

import pandas as pd
import pytest

from smm import profit, spot_costs
from test_operating_profit import sample


@pytest.mark.parametrize('policy', [spot_costs.BASIS, spot_costs.LEGACY])
@pytest.mark.parametrize('period_count', [1, 2])
def test_partition_preserves_cash_and_output_for_reordered_prefix(tmp_path, policy, period_count):
    res, _ = sample(tmp_path)
    res['rr'].rt = res['rr'].rt[:period_count]
    res['rr'].states = res['rr'].states[:period_count]
    detail = res['settle']['detail']
    res['settle']['detail'] = detail[detail.period < period_count].copy()
    # Different amounts in each period expose accidental cross-period pooling.
    detail = res['settle']['detail']
    detail.loc[detail.period == 1, ['da_amount', 'rt_amount', 'total']] *= 3
    expected = profit.analyze(res, policy=policy)
    res['settle']['detail'] = detail.sample(frac=1, random_state=42)
    actual = profit.analyze(res, policy=policy)
    for name in ('by_period', 'by_unit', 'by_type'):
        pd.testing.assert_frame_equal(actual[name], expected[name], check_exact=True)
    assert actual['summary'] == expected['summary']
    units = actual['by_unit'].set_index('unit')
    assert units.loc['B', 'cash_revenue_yuan'] == 40 * (1 if period_count == 1 else 4)
    assert units.loc['B', 'charging_settlement_yuan'] == -60 * (1 if period_count == 1 else 4)
    if policy == spot_costs.BASIS:
        assert actual['summary']['simulated_operating_profit_yuan'] is None


def test_profit_uses_executed_initial_state_not_intraday_start_plan(tmp_path):
    res, _ = sample(tmp_path)
    res['rr'].id_rounds = [{'snap': {'u': {'G': [0, 0]}}}]
    res['rr'].states[0].thermal['G']['online'] = True
    actual = profit.analyze(res, policy=spot_costs.LEGACY)
    unit = actual['by_unit'].set_index('unit').loc['G']
    assert unit.startup_count == 0
    assert unit.fixed_online_cost_yuan == 20
    assert unit.startup_cost_yuan == 0


def test_disabled_resource_cash_is_not_silently_included(tmp_path):
    res, _ = sample(tmp_path)
    res['da'].case.cfg.units['renewable'] = []
    with pytest.raises(ValueError, match='unknown settlement entity'):
        profit.analyze(res, policy=spot_costs.LEGACY)
    detail = res['settle']['detail']
    res['settle']['detail'] = detail[detail.entity != 'W']
    actual = profit.analyze(res, policy=spot_costs.LEGACY)
    assert 'W' not in set(actual['by_unit'].unit)
