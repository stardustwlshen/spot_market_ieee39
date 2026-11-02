"""Independent arithmetic/period-accounting tests, no optimization needed."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from smm import regulation_results as R


def inputs():
    sn={'enabled':True,'mileage_ratio':4.,'period_indices':[10,11],
        'requirement_mw':[8.,8.], 'shortage_mw':[0.,0.],
        'q_mw':{'A':[3.,30.],'B':[5.,50.],'UNAWARDED':[0.,0.]},
        'resources':{
            'A':{'bid_yuan_per_mw':6.,'performance':2.,'capacity_mw':50.,'eligible':True},
            'B':{'bid_yuan_per_mw':8.,'performance':1.,'capacity_mw':50.,'eligible':True},
            'UNAWARDED':{'bid_yuan_per_mw':15.,'performance':.1,'capacity_mw':100.,'eligible':True}}}
    price={'requirement_marginal_yuan_per_mw_period':[1000.,2000.],
           'normalized_marginal_yuan_per_mw_mileage':[250.,500.]}
    return sn,price


def test_reference_price_awarded_max_adjusted_bid_and_payment_units_no_dt():
    sn,price=inputs()
    d,s=R.table_for_snapshot(sn,price,[0])
    assert s.period.tolist()==[10]
    assert s.reference_settlement_price_yuan_per_mw.tolist()==[8.]
    # Rule payment = reference price * K * simulated mileage, not capacity * dt.
    assert d.set_index('unit').loc['A','expected_mileage_mw']==12.
    assert d.set_index('unit').loc['A','expected_payment_yuan']==192.
    assert d.set_index('unit').loc['B','expected_payment_yuan']==160.
    assert d.set_index('unit').loc['UNAWARDED','expected_payment_yuan']==0.
    assert s.expected_payment_yuan.iloc[0]==352.
    assert s.requirement_marginal_yuan_per_mw_period.iloc[0]==1000.
    assert s.normalized_marginal_yuan_per_mw_mileage.iloc[0]==250.
    assert d.set_index('unit').loc['A','performance_adjusted_score_cost_yuan']==36.


def test_reference_price_capped_at_15_independent_of_dual():
    sn,price=inputs();sn['resources']['B']['performance']=.25
    d,s=R.table_for_snapshot(sn,price,[0])
    assert s.reference_settlement_price_yuan_per_mw.iloc[0]==15.
    assert d.set_index('unit').loc['B','expected_payment_yuan']==75.


@pytest.mark.parametrize('no_demand',[False,True])
def test_no_award_or_no_demand_zero_reference_and_payment(no_demand):
    sn,price=inputs()
    if no_demand:sn['requirement_mw'][0]=0.
    else:
        for vals in sn['q_mw'].values():vals[0]=0.
        sn['shortage_mw'][0]=8.
    d,s=R.table_for_snapshot(sn,price,[0])
    assert s.no_award.iloc[0]
    assert s.reference_settlement_price_yuan_per_mw.iloc[0]==0.
    assert s.expected_payment_yuan.iloc[0]==0.
    assert bool(s.requirement_met.iloc[0])==no_demand


def test_shortage_flag_not_hidden_by_nonzero_awards_or_payment():
    sn,price=inputs();sn['requirement_mw'][0]=10.;sn['shortage_mw'][0]=2.
    d,s=R.table_for_snapshot(sn,price,[0]);summary=R.summarize(d,s)
    assert not s.requirement_met.iloc[0]
    assert summary['shortage_periods']==1 and summary['shortage_max_mw']==2.
    assert summary['expected_procurement_payment_yuan']==352.


def test_overlapping_lookahead_only_first_periods_paid_no_duplicate_da_payment():
    sn,price=inputs();next_sn=deepcopy(sn);next_sn['period_indices']=[11,12]
    rr=SimpleNamespace(rt=[{'snap':{'regulation':sn},'prices':{'regulation':price}},
                           {'snap':{'regulation':next_sn},'prices':{'regulation':price}}])
    before=deepcopy(rr)
    d,s=R.realtime_tables(rr)
    assert s.period.tolist()==[10,11]
    assert len(d)==6 and s.expected_payment_yuan.sum()==704.
    assert not d.duplicated(['period','unit']).any()
    assert rr==before
    summary=R.summarize(d,s)
    assert summary['executed_periods']==2
    assert summary['settlement_scope']=='RT_executed_first_periods_only;DA_awards_not_paid_again'
    assert summary['payer']=='system_procurement_cost_not_allocated_to_load;separate_from_energy_reconciliation'
    assert summary['capacity_market'] is False
    assert summary['opportunity_cost_recovery_guaranteed'] is False
    # This separate procurement table does not invent energy reconciliation amounts.
    assert not {'da_amount','rt_amount','congestion_rent','merchandising_surplus'} & set(d.columns)


def test_disabled_is_empty_with_stable_schema_and_zero_summary():
    d,s=R.table_for_snapshot({'enabled':False},{},[0])
    assert d.empty and s.empty
    assert list(d.columns)==R.DETAIL_COLUMNS and list(s.columns)==R.SYSTEM_COLUMNS
    assert R.summarize(d,s)=={'enabled':False,'expected_procurement_payment_yuan':0.}
    d2,s2=R.realtime_tables(SimpleNamespace(rt=[{'snap':{},'prices':{}}]))
    pd.testing.assert_frame_equal(d,d2);pd.testing.assert_frame_equal(s,s2)
