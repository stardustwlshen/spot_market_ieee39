from copy import deepcopy
import pytest
from smm.series_financial import aggregate,normalize_day


def day(date='2026-07-15',basis='spot_energy_cash_v2',profile='a',cost_available=True):
    version='spot-market-costs-v1' if basis=='spot_energy_cash_v2' else 'legacy-fixed-costs-v1'
    policy=dict(profit_basis=basis,cost_basis_version=version,cost_profile_sha256=profile)
    row=dict(unit='PS1',period=0,cash_revenue_yuan=-10.,cash_operating_cost_yuan=3. if cost_available else None,
        simulated_operating_profit_yuan=-13. if cost_available else None,cost_available=cost_available,**policy)
    return dict(date=date,summary=policy,rows=[row])


def test_same_policy_different_daily_cost_hash_keeps_both():
    result=aggregate([day(),day('2026-07-16',profile='b')])
    assert result['aggregate_cost_profit_available'] and result['cash_revenue_yuan']==-20.
    assert result['cash_operating_cost_yuan']==6. and result['simulated_operating_profit_yuan']==-26.
    assert [d['cost_profile_sha256'] for d in result['cost_profiles_by_date']]==['a','b']


def test_mixed_policy_keeps_cash_and_rows_but_not_cost_profit():
    result=aggregate([day(),day('2026-07-16',basis='legacy_cash_operating_v1')])
    assert result['cash_revenue_yuan']==-20. and result['all_costs_available']
    assert not result['cost_basis_consistent'] and not result['aggregate_cost_profit_available']
    assert result['cash_operating_cost_yuan'] is None and result['simulated_operating_profit_yuan'] is None
    assert result['profit_basis'] is None and result['cost_aggregation_unavailable_reason']=='mixed_profit_basis_or_cost_version'


def test_missing_profile_and_unexecuted_day_never_insert_zero_rows():
    result=aggregate([day(cost_available=False)])
    assert result['period_unit_count']==1 and result['cash_operating_cost_yuan'] is None
    assert result['cost_aggregation_unavailable_reason']=='missing_explicit_unit_costs'
    assert aggregate([])['rows']==[] and aggregate([])['cash_operating_cost_yuan'] is None


def test_unmarked_archive_is_explicit_inferred_legacy_only():
    old=day(basis='legacy_cash_operating_v1')
    for obj in [old['summary'],*old['rows']]:
        obj.pop('profit_basis');obj.pop('cost_basis_version')
    result=aggregate([old]);assert result['profit_basis']=='legacy_cash_operating_v1'
    assert result['cost_profiles_by_date'][0]['legacy_basis_inferred']

@pytest.mark.parametrize('corruption',['unknown_basis','row_mismatch','missing_new_marker','hash','cost_zero_fill','profit_formula'])
def test_corrupt_rows_rejected(corruption):
    data=day();row=data['rows'][0]
    if corruption=='unknown_basis':data['summary']['profit_basis']='unknown'
    elif corruption=='row_mismatch':row['profit_basis']='legacy_cash_operating_v1'
    elif corruption=='missing_new_marker':row.pop('profit_basis')
    elif corruption=='hash':row['cost_profile_sha256']='other'
    elif corruption=='cost_zero_fill':row['cost_available']=False;row['cash_operating_cost_yuan']=0.
    else:row['simulated_operating_profit_yuan']=42.
    with pytest.raises(ValueError):aggregate([data])


def test_duplicate_actual_period_and_date_are_rejected():
    data=day();data['rows'].append(deepcopy(data['rows'][0]))
    with pytest.raises(ValueError,match='Overlapping'):aggregate([data])
    with pytest.raises(ValueError,match='Duplicate'):aggregate([day(),day()])
