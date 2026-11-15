"""Editable simulation assumptions; never infer costs from bids or actual outputs."""
from decimal import Decimal

SOURCE = '研究假设：docs/模拟成本自动接入.md；非实际成本、非报价、非官方参数'

def profile(technology, capacity_mw=None):
    capacity = None if capacity_mw is None else Decimal(str(capacity_mw))
    if capacity is not None and (not capacity.is_finite() or capacity <= 0):
        raise ValueError('capacity must be positive finite MW')
    if technology == 'coal':
        rate = 300 if capacity is None else 280 if capacity >= 600 else 330 if capacity < 300 else 300
        start = 100000
    elif technology == 'gas':
        rate = 550 if capacity is None else 500 if capacity >= 300 else 600 if capacity < 100 else 550
        start = 30000
    elif technology in ('vre', 'wind', 'solar', 'independent_storage', 'battery', 'pumped_storage'):
        rate = start = 0
    else:
        return None
    return dict(generation_CNY_MWh=str(rate), startup_CNY_per_event=str(start),
                stop_CNY_per_event='0', source=SOURCE)

def executed_cost(view, resource, periods, unit_profile):
    """Cost only complete, exact, source-bound executed event summaries."""
    found = {}
    for summary in view.get('execution_event_summaries', []):
        period = (summary.get('start'), summary.get('end'))
        if period not in periods: continue
        if summary.get('run_id') != view['run_id']: continue
        path = summary.get('source_path'); digest = summary.get('source_sha256')
        if not path or not digest or view.get('source_manifest', {}).get(path) != digest: continue
        if period in found: raise ValueError('duplicate executed event summary')
        rows = [r for r in summary.get('rows', []) if r.get('resource') == resource]
        if len(rows) != 1: continue
        r = rows[0]
        if r.get('missing') or any(type(r.get(k)) is not int or r[k] < 0 for k in ('start_count','stop_count')): continue
        found[period] = r
    if len(found) != len(periods): return None
    return str(sum((Decimal(r['start_count'])*Decimal(unit_profile['startup_CNY_per_event'])
                    + Decimal(r['stop_count'])*Decimal(unit_profile['stop_CNY_per_event'])
                    for r in found.values()), Decimal(0)))

def defaults_for(view, selected, resource):
    rows = [r for r in selected['rows'] if r['resource'] == resource]
    technology = rows[0].get('technology')
    capacities = {str(r['capacity_mw']) for r in rows if r.get('capacity_mw') is not None}
    if len(capacities) > 1: raise ValueError('inconsistent resource capacity')
    p = profile(technology, next(iter(capacities)) if capacities else None)
    if p is None: return {}
    startup = '0' if technology in ('vre','wind','solar','independent_storage','battery','pumped_storage') else executed_cost(view, resource, selected['periods'], p)
    if technology == 'pumped_storage':
        startup = str(sum((Decimal(r['executed_startup_cost_CNY']) for r in rows),Decimal(0))) if all(r.get('executed_startup_cost_CNY') is not None for r in rows) else None
    return dict(generation_CNY_MWh=p['generation_CNY_MWh'], executed_startup_cost_CNY=startup,
                source=p['source'] + ('；容量未提供，采用类型基础值' if not capacities and technology in ('coal','gas') else ''))
