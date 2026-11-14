"""Pure, hash-pinned R14 physical witness -> distinct R15 research publication.

This module never restores an R13/live solver permit. It authenticates a named
archived research witness, rechecks its arithmetic, and issues only a new
in-process *research data* capability. No import or API starts a solver.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary

PIN_SHA256 = '1039b9e7fcc3fd8f06288cca25f41bda2321935845e3c0649e65761f76bda5c7'
PREPARATION_PIN_SHA256 = 'd2f1d1e3708cc939305d02fbc6685492f394cf812a73ea754bf1dff66d7e9cc0'
SOURCE_ARCHIVE_SHA256 = '623b83091ca794595382bb62796593733bad95916c18ba3df9188eacd804e239'
DT = 1 / 12
TOL = 1e-6
SCOPES = dict(official_market=False, official_DA98=False, free_SCUC=False,
              actual_telemetry=False, execution_authority=False,
              AGC_complete=False, AC_verified=False, formal_bill=None,
              future_RT_used=False, new_native_calls=0)
_PERMITS = WeakKeyDictionary()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def stamp(value):
    d = datetime.fromisoformat(value)
    require(d.utcoffset() is not None, 'aware logical timestamp required')
    require(d.utcoffset() == timedelta(hours=8), 'Shanghai +08 logical calendar required')
    return d


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'finite numeric value required')
    return value


def close(a, b, message):
    require(abs(finite(a) - finite(b)) <= TOL, message)


def file_sha(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, 'duplicate JSON key')
        result[k] = v
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=_pairs,
                      parse_constant=lambda _: require(False, 'nonfinite JSON'))


class ResearchBridgePermit:
    """Nonserializable capability; constructing this class grants nothing."""
    __slots__ = ('__weakref__',)


def verify_files(root):
    pins_path = Path(__file__).with_name('gd_r15_accepted_v4b_pins.json')
    require(file_sha(pins_path) == PIN_SHA256, 'unapproved R14 pin manifest')
    pins = read_json(pins_path)
    require(pins['source_archive_sha256'] == SOURCE_ARCHIVE_SHA256, 'source archive identity')
    root = Path(root).resolve()
    for rel, expected in pins['files'].items():
        path = root / rel
        require(path.is_file() and path.resolve().is_relative_to(root), 'missing/outside R14 witness: ' + rel)
        require(file_sha(path) == expected, 'R14 witness bytes changed: ' + rel)
    # Result is pinned above and independently binds the two pricing raw files.
    pricing = read_json(root/'ieee39_native_v4b/reports/lmp/result.json')
    for kind in ('request', 'response'):
        path = root/f'ieee39_native_v4b/reports/lmp/{kind}.json'
        require(path.is_file() and path.resolve().is_relative_to(root), 'missing/outside independent LMP raw')
        require(file_sha(path) == pricing[f'raw_{kind}_sha256'], 'independent LMP raw witness changed')
    return pins


def audit_native_witness(request, response, names, *, mip=False):
    """All original rows/bounds/objective, no new matrix, no optimizer."""
    require(response.get('status') == 'optimal' and response.get('backend') == 'highs'
            and response.get('solver_version') == '1.15.1'
            and response.get('has_primal') is True, 'accepted archived native status required')
    settings = response['native_settings']
    require(settings['threads'] == 1 and settings['mip_rel_gap'] == .001
            and settings['mip_abs_gap'] == 0., 'archived solver settings changed')
    require(set(names) == {'variables', 'constraints'}, 'complete variable/constraint name schema')
    x = response['primal']
    require(len(x) == len(request['variables']) == len(names['variables'])
            == len(set(names['variables'])), 'primal/name cardinality or duplicates')
    require(len(request['rows']) == len(names['constraints'])
            == len(set(names['constraints'])), 'constraint name cardinality or duplicates')
    from .gd_r13_source_recipe import independent_primal_check
    check = independent_primal_check(request, x)
    require(check['passed'], 'archived full primal residual failure')
    close(check['objective_yuan'], response['objective'], 'archived objective mismatch')
    if mip:
        from .gd_r14_da_bess_prepare import validate_mip_evidence
        check['gap_check'] = validate_mip_evidence(response)
    else:
        require(response.get('dual_valid') is True and check['integer_variables'] == 0,
                'SCED must be archived continuous LP with duals')
    return dict(zip(names['variables'], x)), check


def _technical(source, data):
    thermal = {}
    for g, t in source['energy_inputs']['thermal'].items():
        thermal[g] = {k: deepcopy(t[k]) for k in ('min_up_h', 'min_down_h')}
        for key in ('ramp_up_mw_per_h', 'ramp_down_mw_per_h'):
            require(len(set(t[key].values())) == 1, 'nonconstant thermal ramp unsupported')
            thermal[g][key] = t[key][0]
    batteries = {}
    for b, t in source['batteries'].items():
        u = data['resources'][b]
        batteries[b] = {k: deepcopy(t[k]) for k in (
            'energy_min_mwh', 'energy_max_mwh', 'energy_capacity_mwh',
            'charge_max_mw', 'discharge_max_mw', 'ramp_up_mw_per_h',
            'ramp_down_mw_per_h', 'minimum_mode_h', 'preparation_h')}
        batteries[b].update(eta_charge=u['eta_charge'], eta_discharge=u['eta_discharge'],
                            cycle_budget=deepcopy(data['physical_lift']['resources'][b]['cycle_budget']))
        require(all(v == 0 for v in t['minimum_mode_h'].values())
                and all(v == 0 for v in t['preparation_h'].values()),
                'nonzero battery residence/preparation requires a new reviewed implementation')
        close(u['eta_charge'] * u['eta_discharge'], t['roundtrip_efficiency'], 'directional efficiency mismatch')
    return dict(thermal=thermal, batteries=batteries)


THROUGHPUT_ROUNDOFF_MWH = 1e-9

def normalize_ledger_throughput(raw):
    """R45-only accounting negative-zero rule. Never changes physical values."""
    raw=finite(raw)
    require(raw >= -THROUGHPUT_ROUNDOFF_MWH, 'negative throughput exceeds R45 accounting tolerance')
    return (0., -raw) if raw < 0. else (raw, 0.)

def integrate_cell(state, cell, technical):
    """Pure endpoint/phase/SOC/dated-throughput evolution, never a receipt."""
    require(stamp(state['at']) == stamp(cell['start'])
            and stamp(cell['end']) - stamp(cell['start']) == timedelta(minutes=5),
            'state/command must cover the next exact five minutes')
    require(set(state['thermal']) == set(cell['thermal']) == set(technical['thermal']), 'thermal inventory mismatch')
    require(set(state['batteries']) == set(cell['batteries']) == set(technical['batteries']), 'battery inventory mismatch')
    new = deepcopy(state)
    new['at'] = cell['end']
    for g, t in technical['thermal'].items():
        old, row = state['thermal'][g], cell['thermal'][g]
        phase = row['phase']
        require(phase in ('stable', 'off', 'startup', 'shutdown'), 'unknown thermal phase')
        age = finite(old['state_age_h'])
        require(age >= 0, 'negative inherited thermal age')
        close(row['begin_mw'], old['power_mw'], 'thermal true endpoint discontinuity')
        if phase in ('startup', 'shutdown'):
            step, length = row['curve_step'], row['curve_length']
            require(type(step) is int and type(length) is int and 0 <= step < length, 'curve cursor invalid')
            if step == 0:
                expected = 'off' if phase == 'startup' else 'stable'
                require(old['phase'] == expected and age + TOL >= t['min_down_h' if expected == 'off' else 'min_up_h'], 'transition predecessor/residence')
                age = 0 if phase == 'startup' else age
            else:
                require(old['phase'] == phase and old['curve_step'] == step, 'unfinished curve cursor mismatch')
            close(row['mean_mw'], (row['begin_mw'] + row['end_mw']) / 2, 'curve integral mismatch')
            end = step + 1 == length
            next_phase = ('stable' if phase == 'startup' else 'off') if end else phase
            age += DT
            if end and phase == 'shutdown':
                age = 0.
            cursor = None if end else step + 1
            curve_length = None if end else length
        else:
            require(old['phase'] == phase, 'phase change without sourced curve')
            close(row['mean_mw'], row['end_mw'], 'stable/off dispatch endpoint semantics')
            delta = finite(row['end_mw']) - finite(old['power_mw'])
            require(-t['ramp_down_mw_per_h'] * DT - TOL <= delta <= t['ramp_up_mw_per_h'] * DT + TOL, 'signed thermal ramp')
            if phase == 'off':
                close(row['end_mw'], 0., 'offline thermal must have zero output')
            age += DT
            next_phase, cursor, curve_length = phase, None, None
        online = int(next_phase != 'off')
        new['thermal'][g] = dict(phase=next_phase, online=online,
            state_age_h=age, power_mw=row['end_mw'], curve_step=cursor,
            curve_length=curve_length, residence_remaining_h=max(0., t['min_up_h' if online else 'min_down_h'] - age))
    date = stamp(cell['start']).date().isoformat()
    hour = stamp(cell['start']).replace(minute=0, second=0, microsecond=0).isoformat()
    end_hour = stamp(cell['end']).replace(minute=0, second=0, microsecond=0).isoformat()
    for b, t in technical['batteries'].items():
        old, row = state['batteries'][b], cell['batteries'][b]
        mode = row['mode']
        require(mode in ('idle', 'charge', 'discharge'), 'battery mode required, never infer from power')
        c, d = finite(row['charge_mw']), finite(row['discharge_mw'])
        require(-TOL <= c <= t['charge_max_mw'] + TOL and -TOL <= d <= t['discharge_max_mw'] + TOL, 'battery magnitude bounds')
        require(abs(c) + abs(d) <= TOL if mode == 'idle' else abs(d if mode == 'charge' else c) <= TOL, 'battery mode/power mismatch')
        power = d - c
        close(power, row['power_mw'], 'signed battery output mismatch')
        require(-t['ramp_down_mw_per_h'] * DT - TOL <= power - old['power_mw'] <= t['ramp_up_mw_per_h'] * DT + TOL, 'signed battery ramp')
        energy = finite(old['energy_mwh'])
        require(t['energy_min_mwh'] - TOL <= energy <= t['energy_max_mwh'] + TOL, 'inherited storage SOC bounds')
        energy += (t['eta_charge'] * c - d / t['eta_discharge']) * DT
        require(t['energy_min_mwh'] - TOL <= energy <= t['energy_max_mwh'] + TOL, 'next storage SOC bounds')
        sign = {'idle': 0, 'charge': -1, 'discharge': 1}[mode]
        permission = row['selected_hour_direction_permission']
        require(permission in (-1, 0, 1) and (not sign or sign == permission), 'source hourly direction permission')
        direction = old['hour_direction'] if old['calendar_hour'] == hour else 0
        require(not sign or direction in (0, sign), 'same-hour direction reversal, including zero-power mode')
        if sign:
            direction = sign
        entered = mode != old['mode']
        if entered:
            require(old['mode_age_h'] + TOL >= t['minimum_mode_h'][old['mode']], 'battery inherited mode residence')
            prep = t['preparation_h'].get(mode, 0)
            require(prep == 0 or (old['mode'] == 'idle' and old['mode_age_h'] + TOL >= prep), 'battery preparation time')
        used = deepcopy(old['daily_used_internal_mwh'])
        require(all(finite(v) >= 0 for v in used.values()), 'negative dated battery throughput')
        raw_throughput = (t['eta_charge'] * c + d / t['eta_discharge']) * DT
        throughput, adjustment = normalize_ledger_throughput(raw_throughput)
        if adjustment:
            new.setdefault('numeric_ledger_adjustments', []).append(dict(resource=b,cell_start=cell['start'],cell_end=cell['end'],date=date,raw_internal_throughput_mwh=raw_throughput,accounted_internal_throughput_mwh=throughput,adjustment_mwh=adjustment,tolerance_mwh=THROUGHPUT_ROUNDOFF_MWH,physical_power_unchanged=True,physical_SOC_unchanged=True))
        used[date] = used.get(date, 0.) + throughput
        budget = t['cycle_budget'].get(date)
        require(budget is not None or abs(used[date]) <= TOL, 'undeclared operating-date cycle budget')
        if budget is not None:
            require(used[date] <= budget['limit_internal_mwh'] + TOL, 'dated battery throughput limit')
        end_date = stamp(cell['end']).date().isoformat()
        used.setdefault(end_date, 0.)
        new['batteries'][b] = dict(energy_mwh=energy, power_mw=power, mode=mode,
            mode_age_h=DT if entered else old['mode_age_h'] + DT,
            hour_direction=direction if end_hour == hour else 0, calendar_hour=end_hour,
            daily_used_internal_mwh=used,
            cumulative_internal_mwh=old['cumulative_internal_mwh'] + throughput)
    return new


def _initial_state(source, data):
    day = data['grid'][0]['start']
    thermal, batteries = {}, {}
    for g, t in source['energy_inputs']['thermal'].items():
        close(t['initial_power_mw'], data['resources'][g]['initial_power_mw'], 'SCED initial power/source mismatch')
        phase = 'stable' if t['initial_online'] else 'off'
        thermal[g] = dict(phase=phase, online=t['initial_online'], power_mw=t['initial_power_mw'],
            state_age_h=t['initial_state_age_h'], curve_step=None, curve_length=None,
            residence_remaining_h=max(0., t['min_up_h' if t['initial_online'] else 'min_down_h'] - t['initial_state_age_h']))
    for b, t in source['batteries'].items():
        u = data['resources'][b]
        for k in ('initial_energy_mwh', 'initial_power_mw', 'energy_min_mwh', 'energy_max_mwh'):
            close(t[k], u[k], 'SCED original storage parameter mismatch')
        used = {date: q['used_internal_mwh'] for date, q in data['physical_lift']['resources'][b]['cycle_budget'].items() if date <= day[:10]}
        # The previous preparation date is explicitly zero, not silently reset.
        used[(stamp(day) - timedelta(days=1)).date().isoformat()] = 0.
        batteries[b] = dict(energy_mwh=t['initial_energy_mwh'], power_mw=t['initial_power_mw'],
            mode=t['initial_mode'], mode_age_h=t['initial_mode_age_h'],
            hour_direction=t['initial_hour_direction'], calendar_hour=day,
            daily_used_internal_mwh=used, cumulative_internal_mwh=sum(used.values()))
    return dict(at=day, thermal=thermal, batteries=batteries,
                pumped_storage=deepcopy(source['contract']['storage']['pumped_storage']['inventory']))


def _preparation(source, data, report, technical):
    from .gd_da_prepared_boundary import validate_boundary_report
    from .gd_da_candidate import digest as historical_digest
    validate_boundary_report(report)  # Rebuild seven small proofs; zero solve.
    require(historical_digest(report) == historical_digest(source['prepared_boundary']), 'portable boundary/source mismatch')
    h = report['problem']['hypothesis']
    initial = _initial_state(source, data)
    require(report['output_at'] == initial['at'] == h['delivery_boundary_at'], 'boundary clock mismatch')
    seed = deepcopy(initial)
    seed['at'] = h['preparation_boundary_at']
    for g, t in seed['thermal'].items():
        t.update(power_mw=report['equilibrium']['thermal_power_mw'][g], state_age_h=h['age_h'][g])
        require(h['online'][g] == 1 and t['phase'] == 'stable', 'supported prepared stable state required')
        for key in ('initial_online', 'initial_power_mw', 'initial_state_age_h'):
            close(report['thermal'][g][key], source['energy_inputs']['thermal'][g][key], 'prepared terminal/technical mismatch')
    for b, t in seed['batteries'].items():
        inv = report['storage_inventory']['batteries'][b]
        close(t['energy_mwh'], inv['energy_mwh'], 'prepared battery inventory mismatch')
        close(t['power_mw'], inv['power_mw'], 'prepared battery power mismatch')
        require(t['mode'] == inv['mode'] == 'idle', 'prepared battery idle mode required')
        t['mode_age_h'] -= .25
        t['calendar_hour'] = stamp(seed['at']).replace(minute=0, second=0, microsecond=0).isoformat()
        t['daily_used_internal_mwh'] = {seed['at'][:10]: 0.}
    state = deepcopy(seed)
    cells = []
    for row in report['cells']:
        cell = dict(start=row['at'], end=row['end'], thermal={}, batteries={})
        for g, power in row['power_mw'].items():
            cell['thermal'][g] = dict(phase='stable', begin_mw=state['thermal'][g]['power_mw'],
                mean_mw=power, end_mw=power, curve_step=None, curve_length=None)
        for b in seed['batteries']:
            cell['batteries'][b] = dict(mode='idle', charge_mw=0., discharge_mw=0., power_mw=0., selected_hour_direction_permission=0)
        state = integrate_cell(state, cell, technical)
        cells.append(cell)
    require(len(cells) == 3 and state['at'] == initial['at'], 'exact three-cell preparation required')
    # Floating additions of 1/12 need numeric comparison, not rounded repair.
    for family in ('thermal', 'batteries'):
        for r in initial[family]:
            for k, value in initial[family][r].items():
                actual = state[family][r][k]
                if type(value) in (int, float):
                    close(actual, value, 'projected preparation initial state mismatch: ' + k)
                else:
                    require(actual == value, 'projected preparation initial lineage mismatch: ' + k)
    return dict(seed_state=seed, cells=cells, midnight_state=state,
                evidence_kind='portable_archived_mathematical_proof_not_live_native',
                original_predecessor_reachable=False, historical_telemetry=False)


def _derive_plan(data, values, publication, historical, source, prep, technical, bindings):
    from .gd_da_candidate import compile_path
    require(data['strategy_id'] == 'v4_native_BESS_modes_original_bounds_tail', 'accepted open-tail fixed-mode strategy required')
    lift = data['physical_lift']
    require(lift['future_RT_used'] is False and lift['availability_is_simulated'] is True, 'future RT/historical actual forbidden')
    require(len(data['grid']) == 98 and len(lift['cells']) == 576, 'accepted DA98/full physical domain required')
    require(lift['market_to_physical'][:96] == [list(range(3*t, 3*t+3)) for t in range(96)], 'D96 mapping changed')
    require(publication['official'] is False and publication['execution_authority'] is False, 'publication scope overclaim')
    day = stamp(data['grid'][0]['start'])
    pub = stamp(data['planned_publication_at'])
    require(stamp(data['fixed_state_available_at']) <= pub <= day - timedelta(minutes=15), 'logical publication chronology')
    require(set(data['resources']) == set(technical['thermal']) | set(technical['batteries']), 'unexpected resource inventory')
    paths = {}
    for g, t in source['energy_inputs']['thermal'].items():
        selected = [s for s in source['candidates'][g] if s['id'] == historical['selected_paths'][g]]
        require(len(selected) == 1, 'unique historical selected phase source required')
        paths[g] = (selected[0], compile_path(t, selected[0], tuple(range(288)), source['contract']))
    events = []
    for g, (spec, _) in paths.items():
        for event in spec['events']:
            effective = day + timedelta(minutes=5*event['at'])
            if event['kind'] == 'startup':
                require(pub <= stamp(event['notification_at']) <= effective - timedelta(minutes=30), 'startup advance notice chronology')
            events.append(dict(unit=g, kind=event['kind'], effective_at=effective.isoformat(),
                notification_at=event['notification_at'], available_at=pub.isoformat(),
                curve_steps=event['steps'], source_sha256=source['contract']['sources'][event['source_id']]['sha256'],
                research_schedule=True, actual_command_history=False))
    state = deepcopy(prep['midnight_state'])
    cells = []
    for j in range(288):
        raw_cell = lift['cells'][j]
        require(stamp(raw_cell['start']) == day + timedelta(minutes=5*j)
                and stamp(raw_cell['end']) == day + timedelta(minutes=5*(j+1)), 'physical clock shape')
        cell = dict(start=raw_cell['start'], end=raw_cell['end'], thermal={}, batteries={})
        req = lift['reserve'][j]
        require(req['positive_mw'] == 400. and req['accident_mw'] == 400.
                and req['negative_mw'] == req['primary_mw'] == 0., 'original 400/400 reserves changed')
        cell['reserve_requirement_mw'] = deepcopy(req)
        for g, (spec, path) in paths.items():
            fixed = lift['resources'][g]['rows'][j]
            p = path['cells'][j]
            require(fixed['phase'] == p['phase'], 'fixed thermal phase/source mismatch')
            power = values[f'physical_power[{g},{j}]']
            cursor = next(((j-e['at'], len(e['steps'])) for e in spec['events'] if e['at'] <= j < e['at']+len(e['steps'])), (None, None))
            transition = p['phase'] in ('startup', 'shutdown')
            if transition:
                for key in ('begin', 'mean', 'end'):
                    close(fixed[key+'_mw'], p[key], 'source transition curve changed')
                close(power, p['mean'], 'SCED transition primal/source mismatch')
            row = dict(phase=p['phase'], mean_mw=power,
                begin_mw=p['begin'] if transition else state['thermal'][g]['power_mw'],
                end_mw=p['end'] if transition else power,
                curve_step=cursor[0], curve_length=cursor[1],
                stable=int(p['phase'] == 'stable'), connected=int(p['phase'] != 'off'),
                age_begin_h=state['thermal'][g]['state_age_h'])
            cell['thermal'][g] = row
        for b, t in technical['batteries'].items():
            fixed = lift['resources'][b]['rows'][j]
            old = historical['physical_cells'][j]['batteries'][b]
            require(fixed['mode'] == old['mode'] and fixed['reserve_eligible'] is False
                    and all(fixed[k+'_cap_mw'] == 0 for k in ('positive', 'negative', 'primary', 'accident')), 'storage mode/qualification changed')
            power = values[f'physical_power[{b},{j}]']
            cell['batteries'][b] = dict(mode=fixed['mode'], power_mw=power,
                charge_mw=-power if fixed['mode'] == 'charge' else 0.,
                discharge_mw=power if fixed['mode'] == 'discharge' else 0.,
                selected_hour_direction_permission=old['selected_hour_direction_permission'],
                energy_begin_mwh=values[f'physical_energy[{b},{j}]'],
                energy_end_mwh=values[f'physical_energy[{b},{j+1}]'])
            close(state['batteries'][b]['energy_mwh'], cell['batteries'][b]['energy_begin_mwh'], 'SCED inherited physical energy mismatch')
        state = integrate_cell(state, cell, technical)
        for g in technical['thermal']:
            cell['thermal'][g].update(age_end_h=state['thermal'][g]['state_age_h'], residence_remaining_h=state['thermal'][g]['residence_remaining_h'])
        for b in technical['batteries']:
            close(state['batteries'][b]['energy_mwh'], cell['batteries'][b]['energy_end_mwh'], 'SCED energy integration mismatch')
            cell['batteries'][b].update(mode_age_end_h=state['batteries'][b]['mode_age_h'],
                hour_direction=state['batteries'][b]['hour_direction'],
                daily_used_internal_mwh=deepcopy(state['batteries'][b]['daily_used_internal_mwh']))
        cells.append(cell)
    require(set(publication['power_mw']) == set(data['resources']), 'DA publication resource coverage')
    for r, series in publication['power_mw'].items():
        require(len(series) == 96, 'representative points must not enter D96 publication')
        for t, power in enumerate(series):
            close(power, values[f'power[{r},{t}]'], 'publication/SCED market primal mismatch')
            close(power, sum(values[f'physical_power[{r},{j}]'] for j in range(3*t, 3*t+3))/3, 'D96 physical mean mismatch')
    require(set(publication['lmp_yuan_mwh']) == set(data['nodes'])
            and all(len(s) == 96 and all(type(v) in (int, float) and math.isfinite(v) for v in s)
                    for s in publication['lmp_yuan_mwh'].values()), 'DA96 node-price coverage')
    plan = dict(schema='gd.research.r15.r14_physical_publication.v1',
        transaction_id='R15-DA-' + uuid4().hex, actual_created_at_utc=datetime.now(timezone.utc).isoformat(),
        day_start=day.isoformat(), source_available_at=data['fixed_state_available_at'],
        publication_at=pub.isoformat(), publication_clock_is_simulated=True,
        historical_publication_claim=False, source_archive_sha256=SOURCE_ARCHIVE_SHA256,
        source_bindings=bindings, source_kind='accepted_archived_R14_SCED_physical_primal',
        publication96=deepcopy(publication), physical_cells=cells, events=events,
        initial_preparation=prep, terminal_state=state, technical=technical,
        coverage_end=cells[-1]['end'], native_execution_authenticated=False,
        limitations=['fixed thermal states', 'fixed current-day BESS modes',
                    'open D+1 terminal', 'synthetic logical clocks and zero-tracking hypothesis',
                    'portable archived seed proof, not actual or live-native preparation'],
        scopes=deepcopy(SCOPES))
    return plan


def verify_preparation_raw(root, report):
    """Bind the seven old raw occurrences to independently rebuilt proofs.

    Archived authentication is not reissued as live-native authentication.
    The exact named root is required; there is no fallback to another attempt.
    """
    from .gd_da_candidate import digest as historical_digest
    from .gd_r13_source_recipe import independent_primal_check
    pins_path = Path(__file__).with_name('gd_r15_accepted_preparation_pins.json')
    require(file_sha(pins_path) == PREPARATION_PIN_SHA256, 'unapproved preparation pin manifest')
    pins = read_json(pins_path)
    root = Path(root).resolve()
    for rel, expected in pins['files'].items():
        path = root / rel
        require(path.is_file() and path.resolve().is_relative_to(root), 'missing/outside preparation raw: ' + rel)
        require(file_sha(path) == expected, 'preparation raw changed: ' + rel)
    certificates = report['equilibrium']['certificates'] + [report['preparation_certificate']]
    require(len(certificates) == 7, 'complete preparation stage chain required')
    checks = []
    for i, certificate in enumerate(certificates, 1):
        request, response, names = [read_json(root / f'worker_{kind}_{i:04}.json') for kind in ('request', 'response', 'names')]
        require(historical_digest({k: request[k] for k in ('version', 'variables', 'rows', 'objective')}) == certificate['model_sha256'], 'raw preparation mathematics differs from rebuilt certificate')
        require(response.get('status') == 'optimal' and response.get('backend') == 'highs'
                and response.get('dual_valid') is True and response.get('has_primal') is True
                and response['native_settings']['threads'] == 1, 'preparation native archived status')
        require(response['primal'] == certificate['primal'] and response['dual'] == certificate['row_dual'], 'preparation raw/certificate vector mismatch')
        require(set(names) == {'variables', 'rows'}
                and len(names['variables']) == len(set(names['variables'])) == len(response['primal'])
                and len(names['rows']) == len(set(names['rows'])) == len(response['dual']), 'preparation raw named coverage')
        check = independent_primal_check(request, response['primal'])
        require(check['passed'], 'preparation raw primal residual')
        checks.append(dict(stage=certificate['metrics']['stage'], maximum_violation=check['max_primal_violation']))
    return dict(files=deepcopy(pins['files']), checks=checks,
                historical_native_occurrences=7, new_native_calls=0,
                live_native_authority_restored=False)


def load_accepted_r14(root, *, preparation_root):
    """Read-only accepted bytes, then pure verification and fresh data permit."""
    root = Path(root).resolve()
    pins = verify_files(root)
    sced = root / 'ieee39_native_v4b/reports/sced'
    data = read_json(sced/'input.json')
    values, sced_check = audit_native_witness(read_json(sced/'request.json'), read_json(sced/'response.json'), read_json(sced/'names.json'))
    mip = root / 'bess_native_v4b/reports/native'
    mip_values, mip_check = audit_native_witness(read_json(mip/'request.json'), read_json(mip/'response.json'), read_json(mip/'names.json'), mip=True)
    for b, modes in data['native_upstream_modes'].items():
        require(len(modes) == 24, '24 D+1 upstream selected modes required')
        for h, mode in enumerate(modes, 24):
            require(mode in ('idle', 'charge', 'discharge'), 'unknown upstream mode')
            for k in ('idle', 'charge', 'discharge'):
                close(mip_values[f'mode[{b},{h},{k}]'], int(k == mode), 'SCED/native upstream binary mode mismatch')
    from .gd_october_job_codec import loads
    hist = root / 'input/historical_da_only'
    source = loads((hist/'source_reconstructed_day_inputs.json').read_bytes())
    historical = loads((hist/'issued_day_research_plan.json').read_bytes())
    report = loads((hist/'fresh_boundary_report.json').read_bytes())
    preparation_proof = verify_preparation_raw(preparation_root, report)
    for artifact in data['source_binding']['artifacts']:
        by_name = [v for k, v in pins['files'].items() if k.endswith('/'+Path(artifact['path']).name)]
        require(artifact['sha256'] in by_name, 'SCED original artifact binding mismatch')
    technical = _technical(source, data)
    prep = _preparation(source, data, report, technical)
    publication = read_json(root/'ieee39_native_v4b/reports/publication96.json')
    pricing = read_json(root/'ieee39_native_v4b/reports/lmp/result.json')
    require(pricing['status'] == 'optimal' and pricing['sced_duals_used'] is False
            and pricing['independent_variables'] is True
            and pricing['dispatch_executable'] is False
            and pricing['official_equivalence'] is False,
            'archived independent LMP scope/status mismatch')
    require(publication['lmp_yuan_mwh'] == pricing['lmp96_yuan_mwh'], 'published prices differ from independent LMP result')
    for kind in ('request', 'response'):
        require(file_sha(root/f'ieee39_native_v4b/reports/lmp/{kind}.json') == pricing[f'raw_{kind}_sha256'], 'independent LMP raw witness changed')
    sced_price_links = {a['role']: a['sha256'] for a in pricing['source_binding']['artifacts'] if a['role'].startswith('sced_')}
    require(sced_price_links == {'sced_'+kind: pins['files'][f'ieee39_native_v4b/reports/sced/{kind}.json']
                                for kind in ('request', 'response', 'names', 'input')}, 'pricing/physical SCED source lineage mismatch')
    plan = _derive_plan(data, values, publication, historical, source, prep, technical, deepcopy(pins['files']))
    plan['preparation_source_bindings'] = preparation_proof['files']
    plan['verification'] = dict(sced=sced_check, upstream_mip=mip_check,
        seed='seven portable optimality/physical certificates independently rebuilt',
        source_matrix_rebuilt_here=False,
        matrix_authentication='exact pinned accepted V4b matrices; independently reconstructed in prior acceptance',
        DA96_price_binding='exact independent LMP96 result and raw request/response hashes; SCED lineage rechecked',
        preparation_raw=preparation_proof, new_native_calls=0)
    permit = ResearchBridgePermit()
    _PERMITS[permit] = dict(plan_sha256=digest(plan), root=root, pins=pins,
        preparation_root=Path(preparation_root).resolve(), preparation_report=report,
        source=source, data=data, plan=deepcopy(plan))
    return plan, permit


def validate_bridge(plan, permit, *, cutoff=None, recheck_files=False):
    require(type(permit) is ResearchBridgePermit and permit in _PERMITS,
            'fresh R15 archived-research capability required; old/forged permits forbidden')
    entry = _PERMITS[permit]
    require(digest(plan) == entry['plan_sha256'], 'R15 publication mutated')
    require(plan['scopes'] == SCOPES, 'research scope changed')
    if cutoff is not None:
        require(stamp(plan['source_available_at']) <= stamp(plan['publication_at']) <= stamp(cutoff), 'publication unavailable at cutoff')
    if recheck_files:
        verify_files(entry['root'])
        verify_preparation_raw(entry['preparation_root'], entry['preparation_report'])
    return True


def bridge_sources(plan, permit):
    validate_bridge(plan, permit)
    entry = _PERMITS[permit]
    return deepcopy(entry['source']), deepcopy(entry['data'])
