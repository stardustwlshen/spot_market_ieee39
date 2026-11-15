"""Pure same-run executed-hour economics. No solver, bid-as-cost or formal bill."""
from decimal import Decimal
from datetime import datetime

D = Decimal

def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not money/energy')
    n = D(str(value))
    if not n.is_finite():
        raise ValueError('nonfinite economics input')
    return n

def summarize(hour, inputs=None):
    """Inputs bind resource costs/services to this exact run+hour and explicit source.

    costs: resource -> generation_CNY_MWh and executed_startup_cost_CNY.
    The latter is the SUM of actual executed startup events, not the bid per start.
    services: resource -> agc/reserve -> amount_CNY, source.
    Explicit zero is allowed (including documented nonparticipation); absent is unknown.
    Charging purchases already enter signed energy settlement, never a second cost.
    """
    rows = hour['rows']
    periods = {(r['start'], r['end']) for r in rows}
    if len(periods) != 1:
        raise ValueError('exactly one executed hour required')
    start, end = next(iter(periods))
    if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
        raise ValueError('invalid interval')
    inputs = inputs or {}
    if inputs:
        if (inputs.get('run_id'), inputs.get('start'), inputs.get('end')) != (hour['run_id'], start, end):
            raise ValueError('economics source run/interval mismatch')
        if not inputs.get('source'):
            raise ValueError('economics provenance required')
    grouped = {}
    seen = set()
    for r in rows:
        key = (r['resource'], str(r['node']), r['direction'])
        if key in seen:
            raise ValueError('duplicate resource/node/direction ledger')
        seen.add(key)
        g = grouped.setdefault(r['resource'], {'energy': D(0), 'output': D(0), 'technology': r.get('technology')})
        if g['technology'] != r.get('technology'):
            raise ValueError('resource technology mismatch')
        g['energy'] += number(r['energy_amount_CNY'])
        q = number(r['simulated_executed_MWh'])
        if r['direction'] != 'charge' and q < 0:
            raise ValueError('executed generation energy must be nonnegative')
        if r['direction'] != 'charge':
            g['output'] += q
    out = []
    for resource, g in grouped.items():
        missing = []
        c = inputs.get('costs', {}).get(resource)
        generation = startup = None
        if c and c.get('source') and c.get('generation_CNY_MWh') is not None:
            rate=number(c['generation_CNY_MWh'])
            if rate<0:raise ValueError('negative generation cost')
            generation=rate*g['output']
        else:missing.append('发电成本缺失')
        if c and c.get('source') and c.get('executed_startup_cost_CNY') is not None:
            startup=number(c['executed_startup_cost_CNY'])
            if startup<0:raise ValueError('negative executed startup cost')
        else:missing.append('已执行启停成本缺失')
        service_amounts = {}
        for name, label in [('agc', '调频'), ('reserve', '备用')]:
            v = inputs.get('services', {}).get(resource, {}).get(name)
            if not v or not v.get('source') or v.get('amount_CNY') is None:
                service_amounts[name] = None
                missing.append(label + '分项缺失（不能用容量中标量代替收入）')
            else:
                service_amounts[name] = number(v['amount_CNY'])
        cost = generation + startup if generation is not None and startup is not None else None
        energy_margin = g['energy'] - cost if cost is not None else None
        profit = energy_margin + sum(service_amounts.values(), D(0)) if not missing else None
        out.append(dict(resource=resource, energy_revenue_CNY=g['energy'],
                        agc_revenue_CNY=service_amounts['agc'], reserve_revenue_CNY=service_amounts['reserve'],
                        generation_cost_CNY=generation, executed_startup_cost_CNY=startup,
                        spot_cost_CNY=cost, energy_contribution_CNY=energy_margin,
                        simulated_spot_profit_CNY=profit, missing=missing))
    return dict(run_id=hour['run_id'], start=start, end=end, rows=out,
                status='complete_simulation' if all(not r['missing'] for r in out) else 'partial',
                scope='电能量+调频+备用收入，减发电及实际启停成本；不含运维、容量电费、税费及未列示考核',
                formal_bill=None, native_calls=0)

def from_components(hour, *, costs=None, agc_by_resource=None, reserve_component=None, source):
    """Wire already-calculated same-hour service components, not capacity awards.

    agc_by_resource entries are SettlementSession.agc outputs, and reserve_component
    is SettlementSession.reserve output. Each must carry matching runtime binding.
    Costs must be explicitly supplied from actual cost settings / executed events.
    """
    if not source:
        raise ValueError('component adapter source required')
    periods = {(r['start'], r['end']) for r in hour['rows']}
    if len(periods) != 1:
        raise ValueError('one hour required')
    start, end = next(iter(periods))
    inputs = dict(run_id=hour['run_id'], start=start, end=end, source=source,
                  costs=costs or {}, services={})
    def bound(result):
        if result.get('binding', {}).get('run_id') != hour['run_id']:
            raise ValueError('service component run mismatch')
        # The caller must pass the hash-bound output of the selected hour, not another
        # hour in the same run. Explicit digest equality avoids ambiguous intervals.
        if not hour.get('_hour_sha256') or result['binding'].get('hour_sha256') != hour['_hour_sha256']:
            raise ValueError('service component hour mismatch')
    for resource, result in (agc_by_resource or {}).items():
        bound(result)
        amount = result.get('component', {}).get('reconciled_research_yuan')
        if amount is not None:
            inputs['services'].setdefault(resource, {})['agc'] = dict(amount_CNY=amount, source=source)
    if reserve_component:
        bound(reserve_component)
        for r in reserve_component.get('component', {}).get('rows', []):
            if r.get('amount_CNY') is not None:
                inputs['services'].setdefault(r['resource'], {})['reserve'] = dict(amount_CNY=r['amount_CNY'], source=source)
    return summarize(hour, inputs)

def execution_grid_minutes(hour, execution):
    mode=execution.get('rt_clock_mode')
    step=15 if mode=='provincial_15min_execution' else 5
    declared=hour.get('input_binding',{}).get('RT_physical_interval_minutes')
    if declared is not None and declared!=step:
        raise ValueError('hour/execution grid mismatch')
    if mode not in (None,'legacy_issued_prefix','same_period_execution','provincial_15min_execution'):
        raise ValueError('unknown execution clock mode')
    if hour.get('executed_cells')!=60//step:
        raise ValueError('complete hour cell count mismatch')
    return step

def executed_events(hour, execution):
    """Report model start/stop flags ONLY in the selected executed 5-minute cells.

    Does not infer cold/warm/hot cost or count a future issued/window-end event.
    Caller retains source file/hash verification; these checks enforce scope.
    """
    import re
    from datetime import timedelta
    if execution.get('schema') != 'gd.live_host.execution_view.v3' or execution.get('run_id') != hour['run_id']:
        raise ValueError('execution schema/run mismatch')
    periods = {(r['start'], r['end']) for r in hour['rows']}
    if len(periods) != 1:
        raise ValueError('one hour required')
    start, end = map(datetime.fromisoformat, next(iter(periods)))
    origin = datetime.fromisoformat(execution['origin'])
    step=execution_grid_minutes(hour, execution)
    cells = execution['cells']
    if not cells or len(set(cells)) != len(cells) or any(type(t) is not int for t in cells):
        raise ValueError('invalid executed cells')
    ordered = sorted(cells)
    if ordered != list(range(ordered[0], ordered[-1] + 1)) or origin + timedelta(minutes=step*ordered[0]) != start or origin + timedelta(minutes=step*(ordered[-1]+1)) != end:
        raise ValueError('executed cells/hour interval mismatch')
    if ordered[-1] >= execution['executed_end'] or set(cells) & set(execution.get('excluded_issued_ticks', [])):
        raise ValueError('issued-only ticks cannot enter realized events')
    resources = {r['resource'] for r in hour['rows']}
    flags = {}
    for key, value in execution.get('values', {}).items():
        m = re.fullmatch(r'(start|stop)\[([^,]+),(\d+)\]', key)
        if not m:
            continue
        kind, resource, tick = m.group(1), m.group(2).strip("'\""), int(m.group(3))
        if tick not in cells or resource not in resources:
            continue
        n = number(value)
        if min(abs(n), abs(n-1)) > D('0.000001'):
            raise ValueError('nonbinary executed event flag')
        flags.setdefault(resource, {}).setdefault(kind, {})[tick] = int(n > D('.5'))
    rows = []
    for resource in sorted(resources):
        record = dict(resource=resource, start_count=None, stop_count=None, events=[], missing=[])
        for kind in ('start', 'stop'):
            values = flags.get(resource, {}).get(kind, {})
            if set(values) != set(cells):
                record['missing'].append(kind + ': 已执行时段标记不完整或不适用于此资源')
                continue
            record[kind + '_count'] = sum(values.values())
            record['events'] += [dict(kind=kind, tick=t, timestamp=(origin+timedelta(minutes=step*t)).isoformat()) for t,v in sorted(values.items()) if v]
        record['cost_type_status'] = '仅模型start/stop标记；冷/温/热及曲线类型成本未推断'
        rows.append(record)
    return dict(run_id=hour['run_id'], start=start.isoformat(), end=end.isoformat(), rows=rows,
                source_schema=execution['schema'], excluded_issued_ticks=execution.get('excluded_issued_ticks', []),
                status='partial' if any(r['missing'] for r in rows) else 'event_flags_complete', native_calls=0)
