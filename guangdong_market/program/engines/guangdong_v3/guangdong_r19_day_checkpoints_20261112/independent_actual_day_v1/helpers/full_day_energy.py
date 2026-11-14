"""Independent 24-hour signed direction arithmetic; never authenticates a run."""
from datetime import timedelta
from math import fsum
from independent_chain import require, stamp, equal
from independent_hour_oracle import first_hour

def day_components(executed, frozen, day_start):
    anchor = stamp(day_start)
    require(anchor.hour == anchor.minute == anchor.second == anchor.microsecond == 0, 'aligned Shanghai midnight')
    require(len(executed) == len(frozen) == 96 and set(executed) == set(frozen), 'exact full-day 96-quarter inventory')
    ids = {'commands': [], 'receipts': [], 'price_occurrences': [], 'RT_bindings': []}
    inventory = None
    hours = []
    for q in range(96):
        key = (anchor + timedelta(minutes=15*q)).isoformat()
        require(key in executed, 'no gaps or substituted next-day quarters')
        row, price = executed[key], frozen[key]
        require(row['start'] == price['start'] == key and price['window_index'] == q, 'exact global quarter index')
        require(price['status'] == 'frozen_research_price_not_official' and price['wall_clock_ex_ante_claim'] is False, 'synthetic first-quarter freeze')
        require(row['status'] == 'simulated_RT_deviation_energy_diagnostic', 'research energy kind')
        identity = {(r['kind'], r['resource'], r['direction'], r['node']) for r in row['rows']}
        require(len(identity) == len(row['rows']), 'duplicate resource-direction row')
        inventory = identity if inventory is None else inventory
        require(identity == inventory, 'unchanged full-day resource/direction inventory')
        ids['commands'].extend(row['command_ids']); ids['receipts'].extend(row['receipt_ids'])
        ids['price_occurrences'].append(price['price_occurrence']['id']); ids['RT_bindings'].append(price['rt_binding_sha256'])
    require(len(ids['commands']) == len(set(ids['commands'])) == 288 and len(ids['receipts']) == len(set(ids['receipts'])) == 288, '288 distinct RT commands/receipts')
    require(len(set(ids['price_occurrences'])) == len(set(ids['RT_bindings'])) == 96, '96 distinct RT and PRICE bindings')
    for h in range(24):
        keys = [(anchor + timedelta(minutes=15*(4*h+q))).isoformat() for q in range(4)]
        item = first_hour({k: executed[k] for k in keys}, {k: frozen[k] for k in keys})
        item['hour_index'] = h
        hours.append(item)
    require(hours[-1]['end'] == (anchor + timedelta(days=1)).isoformat(), 'complete final midnight drain')
    return dict(hours=hours, complete_hours=24, frozen_quarters=96, executed_quarters=96, executed_RT_cells=288,
                total_hourly_component_yuan=fsum(x['hourly_total_yuan'] for x in hours), formal_bill=None,
                official_meter=None, official_publication=None, mathematical_full_day=True, native_authentication=False)

def quarter_quantities(commands, receipts, da_cells, resource_nodes, recorded_rows):
    """Compare signed energy against exact executed commands, not saved totals."""
    require(len(commands) == len(receipts) == len(da_cells) == 3, 'exact three-cell quantity source')
    rows = {(r['kind'], r['resource'], r['direction'], r['node']): r for r in recorded_rows}
    expected_inventory = set()
    for family, resources in resource_nodes.items():
        require(family in ('thermal', 'battery'), 'resource family')
        for resource, node in resources.items():
            directions = ('export',) if family == 'thermal' else ('export', 'import')
            for direction in directions:
                key = family, resource, direction, node
                expected_inventory.add(key)
                actual, planned = [], []
                for command, receipt, da in zip(commands, receipts, da_cells):
                    require(receipt['command_id'] == command['id'] and receipt['at'] == command['cell']['end'], 'matching executed command and receipt')
                    require(command['cell']['start'] == da['start'] and command['cell']['end'] == da['end'], 'time-aligned original DA')
                    fam = 'thermal' if family == 'thermal' else 'batteries'
                    field = 'mean_mw' if family == 'thermal' else 'power_mw'
                    a, b = command['cell'][fam][resource][field], da[fam][resource][field]
                    select = max if direction == 'export' else min
                    actual.append(select(a, 0.)/12); planned.append(select(b, 0.)/12)
                row = rows[key]
                equal(row['simulated_executed_mwh'], fsum(actual), 'independent signed RT quantity')
                equal(row['day_ahead_plan_mwh'], fsum(planned), 'independent signed DA quantity')
                equal(row['deviation_mwh'], fsum(actual)-fsum(planned), 'independent energy deviation')
    require(set(rows) == expected_inventory and len(rows) == len(recorded_rows), 'complete exact resource-direction inventory')
    return dict(rows=len(rows), cells=3, signed_directions_separate=True)
