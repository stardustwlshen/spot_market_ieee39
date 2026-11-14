"""Live R19 first-quarter research prices and simulated energy, never a bill.

Only exact in-process priced-run capabilities can freeze or reconcile records.
Pure arithmetic helpers confer no acceptance or execution authority. Archived
R14 DA96 research prices exist separately; they are not new solves or official
publications. Missing contracts, meters and ancillary performance remain null.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from weakref import WeakKeyDictionary

from .gd_r15_da_rt_bridge import digest, require, finite, stamp, integrate_cell
from .gd_r13_energy_ledger import quarter_energy_rows, hourly_component as _hourly_arithmetic

_LEDGERS = WeakKeyDictionary()


@dataclass(eq=False)
class ResearchEnergyLedger:
    data: dict


def new_ledger():
    obj = ResearchEnergyLedger(dict(
        schema='gd.research.r19.priced_energy_ledger.v1', frozen_prices={},
        executed_quarters={}, formal_bill=None, official_settlement_yuan=None,
        whole_day=False))
    _LEDGERS[obj] = dict(seal=digest(obj.data), sources={}, run=None)
    return obj


def _check(ledger):
    require(type(ledger) is ResearchEnergyLedger and ledger in _LEDGERS,
            'live R19 energy ledger required')
    require(digest(ledger.data) == _LEDGERS[ledger]['seal'],
            'immutable R19 energy ledger mutated')
    return ledger.data


def _seal(ledger):
    _LEDGERS[ledger]['seal'] = digest(ledger.data)


def _adapter():
    from . import gd_r19_priced_native_adapter
    return gd_r19_priced_native_adapter


def resource_nodes(resources):
    """Map R14/R19 generator naming to the physical thermal cell family."""
    require(all(u['kind'] in ('generator', 'battery') for u in resources.values()),
            'unsupported resource kind cannot be discarded')
    return {family: {r: u['node'] for r, u in resources.items() if u['kind'] == kind}
            for family, kind in (('thermal', 'generator'), ('battery', 'battery'))}


def freeze_first_quarter(ledger, price_cap, rt_cap, run):
    """Freeze first price at logical cutoff before commands are committed.

    The clock is explicitly synthetic; this does not assert wall-clock advance
    publication, official price status, or complete Guangdong settlement.
    """
    d = _check(ledger)
    api = _adapter()
    api.assert_same_run(run, rt_cap, price_cap)
    source = api.accepted_rt_source(rt_cap)
    p = api.accepted_price_report(price_cap)
    h = api.chain_report(run)
    inp, binding = source['input'], source['binding']
    w = inp['window']
    start, end, cutoff = stamp(w['start']), stamp(w['delivery_end']), stamp(w['cutoff'])
    require(type(w['index']) is int and w['index'] in range(96) and start - cutoff == timedelta(minutes=15)
            and end - start == timedelta(minutes=15), 'exact day-window quarter required')
    require(p['source_binding_sha256'] == digest(binding) and p['window'] == w,
            'price must bind exact accepted physical RT source and window')
    require(p['official_market'] is False, 'research prices only')
    require(p['first_quarter_freeze_status'] == 'separate_ledger_record_required'
            and p['availability_is_synthetic'] is True
            and p['actual_operational_timeliness_claim'] is False,
            'synthetic logical ex-ante pricing only')
    require(binding['transaction_id'] == inp['transaction_id']
            and binding['window'] == w and binding['input_sha256'] == digest(inp)
            and binding['solution_cells_sha256'] == digest(source['cells']),
            'accepted RT binding must cover input, window and physical cells')
    require(h['accepted_RT_windows'] == w['index'] and h['stopped'] is False,
            'freeze only next uncommitted window on running chain')
    require(stamp(h['logical_clock']) == cutoff == stamp(p['price_available_at']),
            'price freeze at live logical cutoff only; no backdating')
    require(set(p['prices']) == set(inp['nodes']) and all(
        len(vs) == 8 for vs in p['prices'].values()), 'full node eight-quarter prices required')
    for vs in p['prices'].values():
        for value in vs:
            finite(value)
    require(len(source['cells']) == len(inp['da_cells']) == 24,
            'full 24-cell physical source required')
    require(not any(stamp(c['cell']['start']) >= start and stamp(c['cell']['start']) < end
                    for c in h['commands']), 'freeze required before first-quarter issue')
    require(all(stamp(r['at']) <= stamp(r['available_at']) <= cutoff
                for r in h['receipts']),
            'future or unavailable execution receipt at freeze cutoff')
    key = w['start']
    entry = _LEDGERS[ledger]
    require(entry['run'] is None or entry['run'] is run, 'different run cannot share ledger')
    require(key not in d['frozen_prices'] and len(d['frozen_prices']) < 96,
            'duplicate/overlapping quarter or more than 96 windows forbidden')
    require(not any(start < stamp(v['end']) and stamp(v['start']) < end
                    for v in d['frozen_prices'].values()), 'overlapping frozen quarter')
    require(w['index'] == len(d['frozen_prices']), 'contiguous freeze index required')
    anchor = start - timedelta(minutes=15*w['index'])
    require(anchor.hour == anchor.minute == anchor.second == anchor.microsecond == 0,
            'whole aligned day required')
    if d['frozen_prices']:
        require(anchor == min(stamp(k) for k in d['frozen_prices']),
                'gap or different day in frozen quarters')
    require(all(v['rt_binding_sha256'] != digest(binding)
                and v['price_occurrence']['id'] != p['occurrence']['id']
                for v in d['frozen_prices'].values()),
            'independent physical and price occurrences required')
    record = dict(window_index=w['index'], start=key, end=w['delivery_end'], available_at=p['price_available_at'],
        prices_yuan_per_mwh={n: vs[0] for n, vs in p['prices'].items()},
        price_model_sha256=p['model_sha256'], price_occurrence=deepcopy(p['occurrence']),
        rt_binding_sha256=digest(binding), rt_input_sha256=digest(inp),
        status='frozen_research_price_not_official', lookahead_prices_frozen=0,
        source_clock='declared_synthetic_research_clock',
        price_frozen_before_delivery='logical_clock_only', wall_clock_ex_ante_claim=False)
    nodes = resource_nodes(inp['resources'])
    require(not entry['sources'] or nodes == next(iter(entry['sources'].values()))['nodes'],
            'dense unchanged day resource inventory required')
    # Check shape and exact physical DA quarter correspondence before freezing.
    quarter_energy_rows(source['cells'][:3], inp['da_cells'][:3], nodes,
                        record['prices_yuan_per_mwh'])
    entry['sources'][key] = dict(run=run, rt_cap=rt_cap, price_cap=price_cap,
        window=deepcopy(w), cells=deepcopy(source['cells'][:3]),
        da_cells=deepcopy(inp['da_cells'][:3]), nodes=nodes,
        binding=deepcopy(binding))
    entry['run'] = run
    d['frozen_prices'][key] = record
    _seal(ledger)
    return deepcopy(record)


def validate_quarter_receipts(*, commands, receipts, cells, window, binding, logical_clock):
    """Pure validation, not authentication: three exact cells and receipts.

    Reject duplicates even when the second record has a wrong hash. The caller
    must separately authenticate the live capabilities and the source cells.
    """
    require(len(cells) == 3, 'exactly three first-quarter physical cells required')
    require(type(window['index']) is int and window['index'] in range(96)
            and binding['transaction_id'].endswith(f"-W{window['index']}"),
            'exact source transaction and window index required')
    clock = stamp(logical_clock)
    start, end = stamp(window['start']), stamp(window['delivery_end'])
    require(start - stamp(window['cutoff']) == timedelta(minutes=15)
            and end - start == timedelta(minutes=15) and end <= clock,
            'quarter not yet fully available')
    require(len({c['id'] for c in commands}) == len(commands), 'duplicate command IDs')
    require(len({r['id'] for r in receipts}) == len(receipts), 'duplicate receipt IDs')
    chosen_commands, chosen_receipts = [], []
    for j, cell in enumerate(cells):
        at = start + timedelta(minutes=5*j)
        require(stamp(cell['start']) == at and stamp(cell['end']) == at + timedelta(minutes=5),
                'noncontiguous first-quarter source cells')
        matches = [c for c in commands if stamp(c['cell']['start']) == at]
        require(len(matches) == 1, 'missing or overlapping quarter command')
        c = matches[0]
        require(c['id'] == binding['transaction_id'] + f"-RT-{j}"
                and c['kind'] == 'accepted_fresh_RT_research_command'
                and c['occurrence_id'] == binding['occurrence']['id']
                and c['acceptance_sha256'] == digest(binding)
                and digest(c['cell']) == digest(cell), 'command/source binding mismatch')
        require(stamp(c['issued_at']) == stamp(window['cutoff'])
                and stamp(c['issued_at']) <= stamp(c['available_at']) <= at,
                'late or invalid command availability')
        rs = [r for r in receipts if r.get('command_id') == c['id']]
        require(len(rs) == 1, 'simulated receipt missing or duplicated')
        r = rs[0]
        require(r['command_sha256'] == digest(c) and r['is_measurement'] is False
                and r['kind'] == 'simulated_zero_tracking_execution_not_telemetry'
                and r['RT'] is True, 'receipt/source or execution kind mismatch')
        require(stamp(r['at']) == stamp(cell['end'])
                and stamp(r['at']) <= stamp(r['available_at']) <= clock
                and stamp(r['state']['at']) == stamp(r['at']),
                'receipt unavailable or endpoint mismatch')
        chosen_commands.append(deepcopy(c))
        chosen_receipts.append(deepcopy(r))
    return chosen_commands, chosen_receipts


def validate_receipt_state_trace(cells, receipts, initial_state, technical):
    """Pure full-state replay, not authentication or permission to execute.

    Exact state digests include thermal endpoints, battery SOC and throughput,
    and unchanged pumped-storage inventory; timestamp-only matches cannot pass.
    """
    require(len(cells) == len(receipts) == 3,
            'exact three-cell receipt state trace required')
    state = deepcopy(initial_state)
    for cell, receipt in zip(cells, receipts):
        state = integrate_cell(state, cell, technical)
        require(digest(receipt['state']) == digest(state),
                'receipt state differs from physical cell integration')
    return deepcopy(state)


def record_available_quarters(ledger, run):
    """Reconcile only fully elapsed frozen quarters from the same live run."""
    d = _check(ledger)
    entry = _LEDGERS[ledger]
    require(entry['run'] is None or entry['run'] is run, 'different execution run')
    api = _adapter()
    h = api.chain_report(run)
    written = []
    for start, f in sorted(d['frozen_prices'].items()):
        if start in d['executed_quarters'] or stamp(f['end']) > stamp(h['logical_clock']):
            continue
        s = entry['sources'][start]
        api.assert_same_run(run, s['rt_cap'], s['price_cap'])
        source = api.accepted_rt_source(s['rt_cap'])
        p = api.accepted_price_report(s['price_cap'])
        require(digest(source['binding']) == f['rt_binding_sha256']
                and digest(source['input']) == f['rt_input_sha256']
                and p['model_sha256'] == f['price_model_sha256']
                and p['source_binding_sha256'] == f['rt_binding_sha256']
                and {n: v[0] for n, v in p['prices'].items()} == f['prices_yuan_per_mwh'],
                'frozen live source or price changed')
        commands, receipts = validate_quarter_receipts(commands=h['commands'],
            receipts=h['receipts'], cells=s['cells'], window=s['window'],
            binding=s['binding'], logical_clock=h['logical_clock'])
        validate_receipt_state_trace([c['cell'] for c in commands], receipts,
            source['input']['projection']['state'], source['input']['technical'])
        rows = quarter_energy_rows([c['cell'] for c in commands], s['da_cells'],
                                   s['nodes'], f['prices_yuan_per_mwh'])
        result = dict(start=start, end=f['end'], recorded_at=h['logical_clock'],
            status='simulated_RT_deviation_energy_diagnostic',
            price_model_sha256=f['price_model_sha256'],
            receipt_ids=[r['id'] for r in receipts], command_ids=[c['id'] for c in commands],
            rows=rows, actual_metered_energy_mwh=None, official_settlement_yuan=None,
            lookahead_energy_counted=False, price_dispatch_used_as_execution=False)
        d['executed_quarters'][start] = result
        _seal(ledger)
        written.append(deepcopy(result))
    return written


def ledger_report(ledger):
    d = deepcopy(_check(ledger))
    live = _LEDGERS[ledger]['run']
    if live is not None:
        h = _adapter().chain_report(live)
        require(all(stamp(q['end']) <= stamp(h['logical_clock']) for q in d['executed_quarters'].values()),
                'live clock must cover recorded execution')
    # Only sealed live-ledger records can confer coverage; the pure helper cannot.
    d['hourly_components'] = []
    keys = sorted(d['executed_quarters'], key=stamp)
    for h in range(24):
        group = [k for k in keys if d['frozen_prices'][k]['window_index']//4 == h]
        if len(group) == 4:
            d['hourly_components'].append(validate_hour_component(
                {k:d['executed_quarters'][k] for k in group},
                {k:d['frozen_prices'][k] for k in group}, hour_index=h))
    d['whole_day'] = len(d['hourly_components']) == 24 and len(d['frozen_prices']) == 96
    if d['whole_day']:
        validate_day_components(d['executed_quarters'], d['frozen_prices'])
    d['time_coverage'] = dict(executed_quarters=len(d['executed_quarters']),
        frozen_quarters=len(d['frozen_prices']),
        executed_five_minute_cells=3*len(d['executed_quarters']),
        complete_hours=len(d['hourly_components']),
        official_settlement_complete=False, reason=('complete_hour_groups_with_matching_prices_and_execution'
                if d['hourly_components'] else 'incomplete_day'))
    d['DA_research_price_status'] = dict(archived_R14_DA96_research_prices_exist=True,
        newly_solved=False, included_in_this_RT_deviation_component=False,
        official_publication=None)
    d['missing_components'] = dict(contract_quantities=None, contract_prices=None,
        actual_metered_energy=None, ancillary_service_performance=None,
        storage_quarter_settlement_eligibility=None, official_publication=None)
    d['scope'] = dict(provincial_only=True, simulated_quantity_not_meter=True,
        first_quarter_only=True, missing_assumed_zero=False, partial_component_not_bill=True,
        quarter_outputs_not_official_settlement=True, full_bill=None,
        archived_DA_prices_not_new_or_official=True, source_clock='synthetic_logical_only')
    return d


def hourly_component(quarters):
    """Strict pure arithmetic helper, never proof of live acceptance/execution."""
    require(len(quarters) == 4, 'four complete quarters required for hourly component')
    for q in quarters:
        require(q.get('actual_metered_energy_mwh') is None
                and q.get('official_settlement_yuan') is None
                and q.get('formal_bill') is None,
                'research quantities cannot assert metering or formal settlement')
        rows = q['rows']
        keys = [(r['kind'], r['resource'], r['direction'], r['node']) for r in rows]
        require(bool(rows) and len(keys) == len(set(keys)),
                'empty or duplicate hourly resource rows')
        resources = {}
        for r in rows:
            require(r.get('settlement_yuan') is None
                    and r.get('quarter_settlement_eligibility') is None
                    and r.get('official_settlement_yuan') is None
                    and r.get('actual_metered_energy_mwh') is None,
                    'research rows cannot assert settlement eligibility or metering')
            require(r['kind'] in ('thermal', 'battery'), 'unsupported hourly resource kind')
            identity = r['kind'], r['resource']
            node, directions = resources.setdefault(identity, (r['node'], set()))
            require(node == r['node'], 'resource node changed within quarter')
            directions.add(r['direction'])
            for field in ('rt_price_yuan_per_mwh', 'simulated_executed_mwh',
                          'day_ahead_plan_mwh', 'deviation_mwh', 'quarter_diagnostic_yuan'):
                finite(r[field])
            actual, da = r['simulated_executed_mwh'], r['day_ahead_plan_mwh']
            require((r['direction'] == 'export' and actual >= 0 and da >= 0)
                    or (r['kind'] == 'battery' and r['direction'] == 'import'
                        and actual <= 0 and da <= 0), 'invalid signed energy direction')
            require(abs(r['deviation_mwh'] - (actual-da)) <= 1e-8
                    and abs(r['quarter_diagnostic_yuan'] -
                            (actual-da)*r['rt_price_yuan_per_mwh']) <= 1e-7,
                    'inconsistent quarter arithmetic')
        for (kind, _), (_, directions) in resources.items():
            require(directions == ({'export'} if kind == 'thermal' else {'export', 'import'}),
                    'missing hourly resource direction')
    return _hourly_arithmetic(quarters)


def validate_hour_component(executed_quarters, frozen_prices, hour_index=None):
    """Pure structural review of four freezes and twelve cells, not authentication.

    Live acceptance is exclusively enforced by freeze/record and the sealed
    ledger registry. Calling this helper on fixtures grants no capabilities.
    """
    require(len(executed_quarters) == len(frozen_prices) == 4
            and set(executed_quarters) == set(frozen_prices),
            'four matching executed and frozen quarters required')
    quarters = [executed_quarters[k] for k in sorted(executed_quarters, key=stamp)]
    first = stamp(quarters[0]['start'])
    inferred = first.hour
    if hour_index is None: hour_index = inferred
    require(type(hour_index) is int and hour_index in range(24) and hour_index == inferred,
            'exact day hour index required')
    commands, receipts, bindings, occurrences = [], [], [], []
    for i, q in enumerate(quarters):
        p = frozen_prices[q['start']]
        require(q['start'] == p['start'] and q['end'] == p['end']
                and type(p['window_index']) is int and p['window_index'] == 4*hour_index+i, 'frozen quarter interval/index mismatch')
        require(stamp(p['available_at']) == stamp(q['start']) - timedelta(minutes=15)
                and stamp(q['recorded_at']) >= stamp(q['end']),
                'invalid price freeze or execution availability')
        require(p['lookahead_prices_frozen'] == 0
                and p['status'] == 'frozen_research_price_not_official'
                and p['wall_clock_ex_ante_claim'] is False
                and q['status'] == 'simulated_RT_deviation_energy_diagnostic'
                and q['lookahead_energy_counted'] is False
                and q['price_dispatch_used_as_execution'] is False,
                'research first-quarter physical execution only')
        require(q['price_model_sha256'] == p['price_model_sha256'],
                'frozen price model mismatch')
        require(len(q['command_ids']) == len(q['receipt_ids']) == 3,
                'exactly three executed cells per quarter required')
        for r in q['rows']:
            require(r['node'] in p['prices_yuan_per_mwh']
                    and r['rt_price_yuan_per_mwh'] == p['prices_yuan_per_mwh'][r['node']],
                    'row price differs from independent first-quarter freeze')
        commands.extend(q['command_ids'])
        receipts.extend(q['receipt_ids'])
        bindings.append(p['rt_binding_sha256'])
        occurrences.append(p['price_occurrence']['id'])
    require(len(set(commands)) == len(set(receipts)) == 12,
            'twelve distinct executed command and receipt IDs required')
    require(len(set(bindings)) == len(set(occurrences)) == 4,
            'four independently frozen source/price occurrences required')
    result = hourly_component(quarters)
    result.update(executed_five_minute_cells=12, independent_first_quarter_prices=4,
                  formal_bill=None, source_clock='synthetic_logical_only')
    return result


def validate_day_components(executed_quarters, frozen_prices):
    """Pure full-day structural check; cannot confer live capability."""
    require(len(executed_quarters)==len(frozen_prices)==96 and set(executed_quarters)==set(frozen_prices),
            'exact 96 matching day quarters required')
    keys=sorted(executed_quarters,key=stamp)
    anchor=stamp(keys[0])
    require(anchor.hour==anchor.minute==anchor.second==anchor.microsecond==0,'midnight day anchor required')
    commands=[];receipts=[];bindings=[];occurrences=[];inventory=None;hours=[]
    for i,k in enumerate(keys):
        require(stamp(k)==anchor+timedelta(minutes=15*i),'day gap or duplicate quarter')
        q=executed_quarters[k];p=frozen_prices[k]
        require(q['start']==p['start']==k and p['window_index']==i,'global day index mismatch')
        current={(r['kind'],r['resource'],r['direction'],r['node']) for r in q['rows']}
        if inventory is None: inventory=current
        require(current==inventory,'dense day resource inventory required')
        commands.extend(q['command_ids']);receipts.extend(q['receipt_ids'])
        bindings.append(p['rt_binding_sha256']);occurrences.append(p['price_occurrence']['id'])
    require(len(set(commands))==len(set(receipts))==288,'288 distinct day command and receipt IDs required')
    require(len(set(bindings))==len(set(occurrences))==96,'96 distinct source and price occurrences required')
    for h in range(24):
        group=keys[4*h:4*h+4]
        hours.append(validate_hour_component({k:executed_quarters[k] for k in group},
            {k:frozen_prices[k] for k in group},hour_index=h))
    require(stamp(executed_quarters[keys[-1]]['recorded_at'])>=anchor+timedelta(days=1),'D+1 midnight drain required')
    return hours


def compact_ledger_report(ledger):
    """Sealed summary suitable for adapter-owned checkpoint persistence."""
    full=ledger_report(ledger)
    return {k:deepcopy(v) for k,v in full.items() if k not in ('executed_quarters','frozen_prices')}
