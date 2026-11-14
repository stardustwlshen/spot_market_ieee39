"""Independent full-day state arithmetic. Standard library, no producer imports.

Source rows fix phase/mode and transition shape. Accepted RT primal supplies
variable dispatch. This function validates arithmetic, never creates authority.
"""
from copy import deepcopy
from datetime import timedelta
from independent_chain import require, equal, stamp


def evolve(state, cell, technical):
    dt = 1 / 12
    tol = 1e-6
    begin, end = stamp(cell['start']), stamp(cell['end'])
    require(stamp(state['at']) == begin and end - begin == timedelta(minutes=5), 'next exact five-minute cell')
    for family in ('thermal', 'batteries'):
        require(set(state[family]) == set(cell[family]) == set(technical[family]), 'state/resource inventory')
    new = deepcopy(state)
    new.update(at=cell['end'], thermal={}, batteries={})
    for name, tech in technical['thermal'].items():
        old, row = state['thermal'][name], cell['thermal'][name]
        phase = row['phase']
        require(phase in ('stable', 'off', 'startup', 'shutdown'), 'known thermal phase')
        equal(row['begin_mw'], old['power_mw'], 'continuous thermal boundary')
        age = old['state_age_h']
        require(age >= 0, 'nonnegative thermal age')
        cursor = length = None
        if phase in ('startup', 'shutdown'):
            k, count = row['curve_step'], row['curve_length']
            require(type(k) is int and type(count) is int and 0 <= k < count, 'valid transition curve cursor')
            if k == 0:
                predecessor = 'off' if phase == 'startup' else 'stable'
                require(old['phase'] == predecessor, 'transition predecessor')
                minimum = tech['min_down_h' if predecessor == 'off' else 'min_up_h']
                require(age + tol >= minimum, 'minimum thermal residence before transition')
            else:
                require(old['phase'] == phase and old['curve_step'] == k and old['curve_length'] == count, 'continuous unfinished transition')
            equal(row['mean_mw'], (row['begin_mw'] + row['end_mw']) / 2, 'piecewise-linear transition cell integral')
            if phase == 'startup' and k == 0:
                age = 0.
            age += dt
            if k + 1 < count:
                after, cursor, length = phase, k + 1, count
            else:
                after = 'stable' if phase == 'startup' else 'off'
                if after == 'off':
                    age = 0.
        else:
            require(old['phase'] == phase and old['curve_step'] is None and old['curve_length'] is None, 'no unexplained thermal phase/cursor reset')
            equal(row['mean_mw'], row['end_mw'], 'stable dispatch integral')
            change = row['end_mw'] - old['power_mw']
            require(-tech['ramp_down_mw_per_h'] * dt - tol <= change <= tech['ramp_up_mw_per_h'] * dt + tol, 'signed thermal ramp')
            if phase == 'off':
                equal(row['end_mw'], 0., 'offline output')
            after, age = phase, age + dt
        online = int(after != 'off')
        nxt = dict(phase=after, online=online, state_age_h=age, power_mw=row['end_mw'], curve_step=cursor, curve_length=length,
                   residence_remaining_h=max(0., tech['min_up_h' if online else 'min_down_h'] - age))
        for key, value in [('age_begin_h', old['state_age_h']), ('age_end_h', age), ('residence_remaining_h', nxt['residence_remaining_h'])]:
            if key in row:
                equal(row[key], value, 'thermal stored ' + key)
        new['thermal'][name] = nxt
    date, end_date = begin.date().isoformat(), end.date().isoformat()
    hour = begin.replace(minute=0, second=0, microsecond=0).isoformat()
    end_hour = end.replace(minute=0, second=0, microsecond=0).isoformat()
    for name, tech in technical['batteries'].items():
        old, row = state['batteries'][name], cell['batteries'][name]
        mode = row['mode']
        require(mode in ('idle', 'charge', 'discharge'), 'known battery mode')
        charge, discharge = row['charge_mw'], row['discharge_mw']
        require(-tol <= charge <= tech['charge_max_mw'] + tol and -tol <= discharge <= tech['discharge_max_mw'] + tol, 'battery rate limits')
        require(abs(charge) + abs(discharge) <= tol if mode == 'idle' else abs(discharge if mode == 'charge' else charge) <= tol, 'battery direction from source mode')
        power = discharge - charge
        equal(power, row['power_mw'], 'battery signed power')
        require(-tech['ramp_down_mw_per_h'] * dt - tol <= power - old['power_mw'] <= tech['ramp_up_mw_per_h'] * dt + tol, 'battery signed ramp')
        energy = old['energy_mwh'] + (tech['eta_charge'] * charge - discharge / tech['eta_discharge']) * dt
        for amount in (old['energy_mwh'], energy):
            require(tech['energy_min_mwh'] - tol <= amount <= tech['energy_max_mwh'] + tol, 'battery SOC hard bounds')
        sign = {'idle': 0, 'charge': -1, 'discharge': 1}[mode]
        permission = row['selected_hour_direction_permission']
        require(permission in (-1, 0, 1) and (sign == 0 or sign == permission), 'battery sourced hourly permission')
        direction = old['hour_direction'] if old['calendar_hour'] == hour else 0
        require(sign == 0 or direction in (0, sign), 'no same-hour reversal including zero-power active mode')
        direction = sign or direction
        entered = mode != old['mode']
        if entered:
            require(old['mode_age_h'] + tol >= tech['minimum_mode_h'][old['mode']], 'inherited minimum mode age')
            preparation = tech['preparation_h'].get(mode, 0.)
            require(preparation == 0 or (old['mode'] == 'idle' and old['mode_age_h'] + tol >= preparation), 'sourced preparation interval')
        used = deepcopy(old['daily_used_internal_mwh'])
        require(all(v >= 0 for v in used.values()), 'nonnegative dated usage')
        equal(sum(used.values()), old['cumulative_internal_mwh'], 'inherited dated and cumulative usage agree')
        increment = (tech['eta_charge'] * charge + discharge / tech['eta_discharge']) * dt
        used[date] = used.get(date, 0.) + increment
        used.setdefault(end_date, 0.)
        for d, amount in used.items():
            budget = tech['cycle_budget'].get(d)
            require((amount <= tol) if budget is None else (amount <= budget['limit_internal_mwh'] + tol), 'date-specific throughput limit')
        nxt = dict(energy_mwh=energy, power_mw=power, mode=mode, mode_age_h=dt if entered else old['mode_age_h'] + dt,
                   hour_direction=direction if hour == end_hour else 0, calendar_hour=end_hour,
                   daily_used_internal_mwh=used, cumulative_internal_mwh=old['cumulative_internal_mwh'] + increment)
        for key, value in [('energy_begin_mwh', old['energy_mwh']), ('energy_end_mwh', energy), ('mode_age_end_h', nxt['mode_age_h']),
                           ('hour_direction', nxt['hour_direction']), ('daily_used_internal_mwh', used)]:
            if key in row:
                equal(row[key], value, 'battery stored ' + key)
        new['batteries'][name] = nxt
    return new


def project(receipt, commands, *, cutoff, start, technical):
    """Reject future, repeated, missing or out-of-order evidence; no capability."""
    cutoff_t, start_t = stamp(cutoff), stamp(start)
    require(stamp(receipt['available_at']) <= cutoff_t and stamp(receipt['at']) <= cutoff_t, 'receipt available at cutoff')
    require(receipt['state']['at'] == receipt['at'], 'receipt endpoint matches state')
    require(len({c['id'] for c in commands}) == len(commands), 'unique command IDs')
    state = deepcopy(receipt['state'])
    require(stamp(state['at']) <= start_t, 'receipt not after projected start')
    for command in commands:
        require(stamp(command['issued_at']) <= cutoff_t and stamp(command['available_at']) <= cutoff_t, 'command available at cutoff')
        require(stamp(command['cell']['end']) <= start_t, 'no lookahead projection')
        state = evolve(state, command['cell'], technical)
    require(state['at'] == start, 'projection covers exact prefix without gap')
    return state
