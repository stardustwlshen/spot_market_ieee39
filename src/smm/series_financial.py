"""Aggregate disjoint actual cash while keeping accounting policies explicit."""
from __future__ import annotations
import copy
import math

VERSIONS={'legacy_cash_operating_v1':'legacy-fixed-costs-v1',
          'spot_energy_cash_v2':'spot-market-costs-v1'}
LEGACY='legacy_cash_operating_v1'


def _number(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{label}: finite numeric actual financial value required')
    return float(value)


def normalize_day(day):
    out=copy.deepcopy(day);summary=out.get('summary',{})
    basis=summary.get('profit_basis',LEGACY)
    if basis not in VERSIONS:raise ValueError('Unknown archived profit_basis; cost/profit aggregation is prohibited')
    inferred='profit_basis' not in summary
    version=summary.get('cost_basis_version',VERSIONS[LEGACY] if basis==LEGACY else None)
    if version!=VERSIONS[basis]:raise ValueError('Unknown or incompatible archived cost_basis_version')
    profile=summary.get('cost_profile_sha256')
    for row in out['rows']:
        row_basis=row.get('profit_basis',LEGACY)
        row_version=row.get('cost_basis_version',VERSIONS[LEGACY] if row_basis==LEGACY else None)
        if row_basis!=basis or row_version!=version:
            raise ValueError('Daily financial rows and summary use different or missing accounting policies')
        if basis!=LEGACY and 'cost_profile_sha256' not in row:
            raise ValueError('New-policy financial row is missing its cost profile provenance')
        if row.get('cost_profile_sha256',profile)!=profile:
            raise ValueError('Daily financial rows and summary use different cost profile hashes')
        if type(row.get('cost_available')) is not bool:
            raise ValueError('Financial cost_available must be explicit boolean')
        _number(row['cash_revenue_yuan'],'cash_revenue_yuan')
        if row['cost_available']:
            if not isinstance(profile,str) or not profile:
                raise ValueError('Available operating costs require a frozen cost profile hash')
            cost=_number(row['cash_operating_cost_yuan'],'cash_operating_cost_yuan')
            profit=_number(row['simulated_operating_profit_yuan'],'simulated_operating_profit_yuan')
            if not math.isclose(profit,row['cash_revenue_yuan']-cost,rel_tol=1e-9,abs_tol=1e-6):
                raise ValueError('Financial profit does not equal signed cash minus stated cost')
        elif row.get('cash_operating_cost_yuan') is not None or row.get('simulated_operating_profit_yuan') is not None:
            raise ValueError('Missing operating costs must remain null, never fabricated zero')
        row.update(profit_basis=basis,cost_basis_version=version,cost_profile_sha256=profile)
    summary.update(profit_basis=basis,cost_basis_version=version,cost_profile_sha256=profile)
    out['summary']=summary
    out.update(profit_basis=basis,cost_basis_version=version,cost_profile_sha256=profile,
               legacy_basis_inferred=inferred or bool(out.get('legacy_basis_inferred')))
    return out


def aggregate(financial):
    days=[normalize_day(d) for d in financial]
    if len({d['date'] for d in days})!=len(days):raise ValueError('Duplicate financial operating date')
    rows=[dict(row,date=d['date']) for d in days for row in d['rows']]
    keys=[(r['date'],r['period'],r['unit']) for r in rows]
    if len(set(keys))!=len(keys):raise ValueError('Overlapping actual financial periods')
    policies={(d['profit_basis'],d['cost_basis_version']) for d in days}
    consistent=len(policies)<=1
    complete=bool(rows) and all(r['cost_available'] for r in rows)
    reasons=[]
    if not consistent:reasons.append('mixed_profit_basis_or_cost_version')
    if not rows:reasons.append('no_executed_financial_periods')
    elif not complete:reasons.append('missing_explicit_unit_costs')
    available=complete and consistent
    basis,version=next(iter(policies)) if len(policies)==1 else (None,None)
    return dict(scope='disjoint_actual_days_DA_cash_plus_RT_deviation_minus_actual_operating_cost',
        exclusions='ID,lookahead,operator_slack_penalties,inventory_valuation,TIE_profit',
        completed_day_count=len(days),period_unit_count=len(rows),all_costs_available=complete,
        cost_basis_consistent=consistent,aggregate_cost_profit_available=available,
        profit_basis=basis,cost_basis_version=version,
        cost_aggregation_unavailable_reason='; '.join(reasons),
        cost_profiles_by_date=[{k:d[k] for k in ('date','profit_basis','cost_basis_version','cost_profile_sha256','legacy_basis_inferred')} for d in days],
        cash_revenue_yuan=sum(r['cash_revenue_yuan'] for r in rows),
        cash_operating_cost_yuan=sum(r['cash_operating_cost_yuan'] for r in rows) if available else None,
        simulated_operating_profit_yuan=sum(r['simulated_operating_profit_yuan'] for r in rows) if available else None,
        rows=rows)
