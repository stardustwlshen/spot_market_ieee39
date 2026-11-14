"""Research mileage-rule reference settlement, separate from SCED marginal cost.

Only executed RT first periods are paid. DA awards are indicative, not paid again.
Mileage is exogenous simulated mileage, not measured AGC telemetry.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

DETAIL_COLUMNS = ['period','unit','eligible','offered_capacity_mw','awarded_capacity_mw',
                  'bid_yuan_per_mw','performance','expected_mileage_mw',
                  'performance_weighted_mileage_mw','reference_settlement_price_yuan_per_mw',
                  'expected_payment_yuan','performance_adjusted_score_cost_yuan']
SYSTEM_COLUMNS = ['period','requirement_mw','awarded_capacity_mw','shortage_mw',
                  'requirement_marginal_yuan_per_mw_period','normalized_marginal_yuan_per_mw_mileage',
                  'reference_settlement_price_yuan_per_mw','no_award','requirement_met',
                  'expected_payment_yuan']


def table_for_snapshot(snapshot, prices, executed_indices=None):
    """Explicit index selection prevents payments for overlapping lookahead periods."""
    if not snapshot.get('enabled',False):
        return pd.DataFrame(columns=DETAIL_COLUMNS),pd.DataFrame(columns=SYSTEM_COLUMNS)
    indices=range(len(snapshot['requirement_mw'])) if executed_indices is None else executed_indices
    number_or_none=lambda value: None if value is None else float(value)
    detail=[];system=[];resources=snapshot['resources'];m=float(snapshot['mileage_ratio'])
    for k in indices:
        awards={u:float(v[k]) for u,v in snapshot['q_mw'].items()}
        active=[u for u,q in awards.items() if q>1e-6]
        required=float(snapshot['requirement_mw'][k])
        no_award=not active or required<=1e-6
        ref=0. if no_award else min(15.,max(float(resources[u]['bid_yuan_per_mw'])/float(resources[u]['performance']) for u in active))
        total=0.
        period=int(snapshot['period_indices'][k])
        for u,q in awards.items():
            d=resources[u];K=float(d['performance']);bid=float(d['bid_yuan_per_mw']);mileage=m*q;pay=ref*K*mileage;total+=pay
            detail.append(dict(period=period,unit=u,eligible=bool(d.get('eligible',True)),offered_capacity_mw=float(d.get('offered_capacity_mw',d['capacity_mw'])),awarded_capacity_mw=q,bid_yuan_per_mw=bid,performance=K,expected_mileage_mw=mileage,performance_weighted_mileage_mw=K*mileage,reference_settlement_price_yuan_per_mw=ref,expected_payment_yuan=pay,performance_adjusted_score_cost_yuan=bid/K*mileage))
        shortage=float(snapshot['shortage_mw'][k])
        system.append(dict(period=period,requirement_mw=required,awarded_capacity_mw=sum(awards.values()),shortage_mw=shortage,
                           requirement_marginal_yuan_per_mw_period=number_or_none(prices['requirement_marginal_yuan_per_mw_period'][k]),
                           normalized_marginal_yuan_per_mw_mileage=number_or_none(prices['normalized_marginal_yuan_per_mw_mileage'][k]),
                           reference_settlement_price_yuan_per_mw=ref,no_award=no_award,requirement_met=shortage<=1e-6 and sum(awards.values())>=required-1e-6,expected_payment_yuan=total))
    return pd.DataFrame(detail,columns=DETAIL_COLUMNS),pd.DataFrame(system,columns=SYSTEM_COLUMNS)


def realtime_tables(rr):
    ds=[];ss=[]
    for r in rr.rt:
        d,s=table_for_snapshot(r['snap'].get('regulation',{}),r['prices'].get('regulation',{}),[0])
        ds.append(d);ss.append(s)
    return (pd.concat(ds,ignore_index=True) if ds else pd.DataFrame(columns=DETAIL_COLUMNS),
            pd.concat(ss,ignore_index=True) if ss else pd.DataFrame(columns=SYSTEM_COLUMNS))


def summarize(detail,system):
    if system.empty:return {'enabled':False,'expected_procurement_payment_yuan':0.}
    return dict(enabled=True,executed_periods=int(len(system)),shortage_periods=int((system.shortage_mw>1e-6).sum()),
                shortage_max_mw=float(system.shortage_mw.max()),awarded_capacity_mw_periods=float(system.awarded_capacity_mw.sum()),
                expected_mileage_mw=float(detail.expected_mileage_mw.sum()),expected_procurement_payment_yuan=float(detail.expected_payment_yuan.sum()),
                reference_price_min=float(system.reference_settlement_price_yuan_per_mw.min()),reference_price_max=float(system.reference_settlement_price_yuan_per_mw.max()),
                dispatch_marginal_cost_max=(float(system.normalized_marginal_yuan_per_mw_mileage.max()) if system.normalized_marginal_yuan_per_mw_mileage.notna().any() else None),
                mechanism='sequential_energy_SCUC_regulation_allocation_final_energy_SCED_rule_reference_payment',
                mileage_source='simulated_exogenous_ratio_not_measured_AGC',
                settlement_scope='RT_executed_first_periods_only;DA_awards_not_paid_again',
                payer='system_procurement_cost_not_allocated_to_load;separate_from_energy_reconciliation',
                opportunity_cost_recovery_guaranteed=False,capacity_market=False)
