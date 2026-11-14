"""No solve: safety checks must catch overloads even if power/monetary balance holds."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from smm.full import execution_safety_checks


def fixture(flow=500.0, limit=500.0):
    # The exported limit deliberately differs: scenario network is authoritative.
    lines = pd.DataFrame([dict(period=44, line='L2-3', flow_mw=flow, rate_mw=9999, slack_mw=0)])
    net = SimpleNamespace(branches=pd.DataFrame([dict(branch_id='L2-3', rate_mw=limit)]))
    da = SimpleNamespace(case=SimpleNamespace(net=net, T=1, cfg=SimpleNamespace(T=1)), tables={'lines':lines.assign(flow_mw=400., period=0)})
    prices = pd.DataFrame([dict(lmp_raw=850., energy=800., congestion=50., lmp=800., inj_mw=0)])
    return da, dict(lines=lines, prices=prices, system=pd.DataFrame({"period":[44]}))


def test_executed_overload_not_hidden_by_balanced_injections_or_exported_limit():
    da, rt = fixture(574.6164)
    c = execution_safety_checks(da, rt)
    assert c['rt_line_overload_count'] == c['rt_line_overload_periods'] == 1
    assert c['rt_line_overload_max_mw'] == pytest.approx(74.6164)
    assert c['rt_line_loading_max'] == pytest.approx(1.1492328)
    assert c['da_network_feasible'] and not c['network_feasible']
    assert c['rt_raw_lmp_decomposition_max_err'] == 0  # clipped price not used


def test_reverse_flow_overload_and_numerical_tolerance():
    da, rt = fixture(-500.000001)
    assert execution_safety_checks(da, rt)['network_feasible']
    rt['lines'].loc[0,'flow_mw'] = -501
    assert execution_safety_checks(da, rt)['rt_line_overload_max_mw'] == 1


@pytest.mark.parametrize('value', [np.nan, np.inf])
def test_invalid_data_cannot_pass(value):
    da, rt = fixture(value)
    c = execution_safety_checks(da, rt)
    assert not c['rt_line_data_valid'] and not c['network_feasible']
    assert c['rt_line_overload_count'] is None


def test_da_physical_slack_and_overload_reported_separately_from_pricing():
    da, rt = fixture()
    da.tables['lines'].loc[0,['flow_mw','slack_mw']] = [510.,10.]
    c = execution_safety_checks(da, rt)
    assert c['da_dispatch_line_slack_max_mw'] == 10
    assert c['da_line_overload_max_mw'] == 10
    assert not c['da_network_feasible']


def test_duplicate_line_records_fail_closed():
    da, rt = fixture()
    rt['lines'] = pd.concat([rt['lines']]*2)
    assert not execution_safety_checks(da, rt)['network_feasible']


def test_missing_entire_rt_period_fails_closed():
    da, rt = fixture()
    rt['system'] = pd.DataFrame({'period':[44,45]})
    assert not execution_safety_checks(da, rt)['rt_line_data_valid']


def test_missing_entire_da_period_fails_closed():
    da, rt = fixture()
    da.case.T = 2
    assert not execution_safety_checks(da, rt)['da_line_data_valid']


@pytest.mark.parametrize('periods', [[44,44], [44,np.nan], [44,45.5]])
def test_invalid_system_periods_fail_closed(periods):
    da, rt = fixture()
    rt['system'] = pd.DataFrame({'period':periods})
    result = execution_safety_checks(da, rt)
    assert not result['rt_system_periods_valid']
    assert not result['rt_network_feasible']


def test_soft_anchor_slack_reported_without_constraint_relaxation():
    from smm.full import slack_policy_checks
    zero = dict(shed_mwh=0., surplus_mwh=0., line_slack_max=0., reserve_up_short_max=0.,
                reserve_dn_short_max=0., anchor_slack_max_mwh=0.)
    rt = [{'anchor_relaxed':0, 'repriced':False, 'slack':dict(zero, anchor_slack_max_mwh=12.)}]
    result = slack_policy_checks(zero, rt)
    assert result['anchor_slack_periods'] == 1
    assert not result['rules_satisfied_no_slack']
    assert result['slack_summary_data_valid']
    rt[0]['slack'] = zero
    assert slack_policy_checks(zero, rt)['rules_satisfied_no_slack']


def test_no_slack_flag_covers_da_and_window_reserve_not_just_executed_balance():
    from smm.full import slack_policy_checks
    zero = dict(shed_mwh=0., surplus_mwh=0., line_slack_max=0., reserve_up_short_max=0.,
                reserve_dn_short_max=0., anchor_slack_max_mwh=0.)
    assert not slack_policy_checks(dict(zero, reserve_up_short_max=1.), [{'slack':zero}])['rules_satisfied_no_slack']
    assert not slack_policy_checks(zero, [{'slack':dict(zero, reserve_dn_short_max=1.)}])['rules_satisfied_no_slack']
    assert not slack_policy_checks({}, [{'slack':zero}])['rules_satisfied_no_slack']


def test_anchor_units_are_split_without_changing_legacy_or_physical_slack():
    from smm.scuc import slack_summary, has_slack
    from smm.full import slack_policy_checks
    m = SimpleNamespace(T=[0], N=[1], L=['L'], A=[0,1], _md=SimpleNamespace(dt=.25),
        shed={(1,0):0.}, surplus={0:0.}, sl={('L',0):0.}, sR_up={0:0.}, sR_dn={0:0.},
        sA_up={0:4.,1:7.}, sA_dn={0:1.,1:2.},
        _anchors=[('ES1',0,0,0,'E'),('PS1',0,0,0,'Vu')])
    s = slack_summary(m)
    assert s['storage_anchor_slack_max_mwh'] == 5
    assert s['pumped_anchor_slack_max_1e4m3'] == 9
    assert s['anchor_slack_max_mwh'] == 9  # compatibility only, NOT MWh
    assert not has_slack(m)
    assert all(isinstance(v,(int,float)) for v in s.values())
    c = slack_policy_checks(s,[{'slack':s}])
    assert c['storage_anchor_slack_max_mwh'] == 5
    assert c['pumped_anchor_slack_max_1e4m3'] == 9
    assert c['pumped_anchor_slack_periods'] == c['storage_anchor_slack_periods'] == 1
    assert not c['rules_satisfied_no_slack']
    old = {k:v for k,v in s.items() if k not in ('storage_anchor_slack_max_mwh','pumped_anchor_slack_max_1e4m3')}
    legacy = slack_policy_checks(old,[{'slack':old}])
    assert legacy['storage_anchor_slack_max_mwh'] is None
    assert legacy['pumped_anchor_slack_max_1e4m3'] is None
