"""Read-only historical evidence checks and separately published cost supplements.

No dispatch, settlement, frozen input or original report is ever rewritten.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd
import yaml

from .profit import REQUIRED, COST_COLUMNS, REVENUE_COLUMNS, validate_profile
from .process_control import file_lock
from . import spot_costs

VERSION = 1
ROUNDING = 0.000050001  # Each persisted four-decimal input may differ by half a unit.
THERMAL = {'coal', 'gas_ccgt', 'gas_gt'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f'Symbolic links are not accepted: {path}')
    if not path.is_file():
        raise ValueError(f'Required historical evidence missing: {path}')
    return path.read_bytes()



def archived_actual_initial(run, specs):
    """Use the frozen actual first state, never the series-original device defaults."""
    path = Path(run) / 'rolling_actual_states.json'
    if not path.exists():
        if (Path(run)/'actual_checkpoint.json').exists() or (Path(run)/'admission.json').exists():
            raise ValueError('Continuous-day archive is missing its actual initial state')
        return None  # Explicit compatibility for old single-day archives only.
    payload = json.loads(_read(path))
    windows = payload.get('windows')
    if not isinstance(windows,list) or not windows or windows[0].get('t') != 0:
        raise ValueError('Archived actual initial state must begin at period zero')
    initial = windows[0]
    for uid, typ in specs.items():
        if typ in THERMAL:
            if initial.get('thermal',{}).get(uid,{}).get('online') not in (True,False,0,1):
                raise ValueError(f'{uid}: actual initial thermal commitment missing')
        elif typ == 'pumped':
            for key in ('ng','np'):
                value = initial.get('pumped',{}).get(uid,{}).get(key)
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or value!=int(value):
                    raise ValueError(f'{uid}: actual initial pumped count missing or invalid')
    return initial

def _fingerprints(run, source):
    paths = [run / name for name in ('summary.json', 'operating_profit_by_period.csv',
             'settlement_detail.csv', 'rt_units.csv', 'fixed_cost_profile.json',
             'output_scope.json', 'run_provenance.json', 'frozen_input_provenance.json',
             'rt_storage.csv', 'rt_pumped.csv', 'rolling_actual_states.json')]
    snapshot = run / 'scenario_snapshot'
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise ValueError('Frozen scenario_snapshot is required')
    paths += sorted(p for p in snapshot.rglob('*') if not p.is_dir())
    if source is not None:
        paths += [source / 'config/fixed_costs.yaml', source / 'config' / spot_costs.FILENAME]
    return {str(p): _hash(_read(p)) if os.path.lexists(p) else None for p in paths}


def _numeric(frame, columns, label):
    if not set(columns).issubset(frame):
        raise ValueError(f'{label}: missing columns {set(columns) - set(frame)}')
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            raise ValueError(f'{label}: non-finite {column}')


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or value < minimum:
        raise ValueError(f'Invalid integer {label}')
    return int(value)


def _grid(frame, id_column, ids, periods, label):
    _numeric(frame, ['period'], label)
    if frame.duplicated([id_column, 'period']).any():
        raise ValueError(f'{label}: duplicate unit/entity period')
    if set(zip(frame[id_column], frame.period)) != {(u, p) for u in ids for p in periods}:
        raise ValueError(f'{label}: incomplete or foreign unit/period scope')


def _scope(frame, scope, label):
    for key, value in scope.items():
        if key in frame and (frame[key].isna().any() or not (frame[key] == value).all()):
            raise ValueError(f'{label}: inconsistent {key}')


def _close(actual, expected, tolerance, label):
    if not np.isclose(actual, expected, atol=tolerance, rtol=0):
        raise ValueError(f'{label}: historical evidence does not reconcile ({actual} vs {expected})')


def _profile(path, *, archived=False):
    if not path.exists():
        return None
    data = json.loads(_read(path)) if archived else yaml.safe_load(_read(path))
    if not isinstance(data, dict):
        raise ValueError(f'Invalid cost profile: {path}')
    if archived and data.get('available') is False:
        if data.get('units'):
            raise ValueError('Unavailable original cost profile contains unit costs')
        return None
    (spot_costs.validate_profile if data.get("profit_basis")==spot_costs.BASIS else validate_profile)(dict(data, available=True))
    return data


def _calculate_original(run, source):
    before = _fingerprints(run, source)
    summary = json.loads(_read(run / 'summary.json'))
    original_basis=summary.get('operating_profit',{}).get('profit_basis',spot_costs.LEGACY)
    if original_basis not in (spot_costs.LEGACY,spot_costs.BASIS):raise ValueError('Unknown archived profit basis')
    original_spot=original_basis==spot_costs.BASIS
    base = yaml.safe_load(_read(run / 'scenario_snapshot/config/base.yaml'))
    units = yaml.safe_load(_read(run / 'scenario_snapshot/config/units.yaml'))
    if not isinstance(base, dict) or not isinstance(units, dict):
        raise ValueError('Invalid frozen configuration')
    dt = base.get('dt_h')
    if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt <= 0:
        raise ValueError('Frozen dt_h must be finite and positive')
    expected = _integer(summary.get('expected_periods'), 'expected_periods', 1)
    executed = _integer(summary.get('executed_periods'), 'executed_periods', 1)
    if executed > expected or expected != _integer(base.get('periods'), 'frozen periods', 1):
        raise ValueError('Run scope disagrees with frozen horizon')
    scope = dict(expected_periods=expected, executed_periods=executed,
                 completed_scope='full_day' if executed == expected else 'debug_prefix',
                 settlement_scope='full_day' if executed == expected else 'illustrative_prefix')
    for key, value in scope.items():
        if summary.get(key) != value:
            raise ValueError(f'Summary lacks consistent {key}')
    if summary.get('settlement_calculated') is False:
        raise ValueError('No formal settlement was calculated')
    for name in ('output_scope.json',):
        if (run / name).exists():
            other = json.loads(_read(run / name))
            if any(other.get(k) != v for k, v in scope.items()):
                raise ValueError('Output scope disagrees with summary')
    if isinstance(summary.get('operating_profit'), dict):
        for key, value in scope.items():
            if key in summary['operating_profit'] and summary['operating_profit'][key] != value:
                raise ValueError('Original profit scope disagrees with summary')
    specs = {}
    physical = {}
    ties = set()
    for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie'):
        for item in units.get(family, []) or []:
            if not item.get('enabled', True):
                continue
            uid = item.get('id')
            typ = item.get('type') if family in ('thermal', 'renewable') else family
            if not isinstance(uid, str) or not uid or uid in specs or uid in ties:
                raise ValueError('Missing or duplicate frozen unit ID')
            if family == 'tie':
                ties.add(uid)
            elif typ not in ({'coal', 'gas_ccgt', 'gas_gt'} if family == 'thermal' else {'wind', 'pv'} if family == 'renewable' else {family}):
                raise ValueError(f'{uid}: unknown resource type')
            else:
                specs[uid] = typ
                physical[uid] = item
    if not specs:
        raise ValueError('No enabled frozen resources')
    periods = range(executed)
    frame = pd.read_csv(run / 'operating_profit_by_period.csv')
    rt = pd.read_csv(run / 'rt_units.csv')
    settlement = pd.read_csv(run / 'settlement_detail.csv')
    _grid(frame, 'unit', specs, periods, 'profit')
    extra_rt=set(rt.unit)-set(specs)
    if not extra_rt<=ties:raise ValueError('Unknown extra RT resource')
    if extra_rt and not rt.loc[rt.unit.isin(extra_rt),'type'].eq('tie').all():raise ValueError('Invalid frozen tie type')
    _grid(rt, 'unit', set(specs)|extra_rt, periods, 'RT units')
    for table in (frame,):
        if 'profit_basis' in table and not table.profit_basis.eq(original_basis).all():raise ValueError('Inconsistent archived profit basis')
        if original_spot and ('profit_basis' not in table or 'cost_basis_version' not in table or not table.cost_basis_version.eq(spot_costs.VERSION).all()):raise ValueError('Missing or inconsistent archived spot cost version')
    _numeric(frame, [*REVENUE_COLUMNS, 'energy_generated_or_discharged_mwh', 'startup_count', 'pump_startup_count'], 'profit')
    _numeric(rt, ['on', 'P_mw'], 'RT units')
    _numeric(settlement, ['period', 'da_amount', 'rt_amount', 'total'], 'settlement')
    for table, label in ((frame, 'profit'), (rt, 'RT'), (settlement, 'settlement')):
        _scope(table, scope, label)
    _grid(settlement, 'entity', set(settlement.entity), periods, 'settlement')
    if settlement.duplicated(['entity', 'period']).any() or not set(settlement.period).issubset(set(periods)):
        raise ValueError('Duplicate or foreign settlement periods')
    if not np.allclose(settlement.total, settlement.da_amount + settlement.rt_amount, atol=3 * ROUNDING, rtol=0):
        raise ValueError('Settlement total differs from DA + RT')
    # Non-resource rows are permitted only for explicit ties, numbered load buses and surplus.
    names = {u: ([u + '_放电', u + '_充电'] if t == 'storage' else
                 [u + '_发电', u + '_抽水'] if t == 'pumped' else [u]) for u, t in specs.items()}
    entities = {n for ns in names.values() for n in ns}
    extras = set(settlement.entity) - entities - ties - {'平衡松弛(过剩)'}
    if any(not isinstance(e, str) or not e.startswith('L') or not e[1:].isdigit() for e in extras):
        raise ValueError('Unknown settlement entity')
    _grid(settlement[settlement.entity.isin(entities)].copy(), 'entity', entities, periods, 'resource settlement')
    original = _profile(run / 'fixed_cost_profile.json', archived=True)
    from .profit import load_fixed_cost_profile
    from types import SimpleNamespace
    frozen_cfg=SimpleNamespace(root=run / 'scenario_snapshot',units=units)
    frozen_profile = spot_costs.load_profile(frozen_cfg) if original_spot else load_fixed_cost_profile(frozen_cfg)
    frozen = frozen_profile if frozen_profile['available'] else None
    if original is None and (run / 'fixed_cost_profile.json').exists() and frozen is not None:
        raise ValueError('Unavailable original profile contradicts existing frozen costs')
    if original is not None and frozen is not None:
        if original['units'] != frozen['units'] or original.get('sha256') != frozen['sha256']:
            raise ValueError('Original cost profile disagrees with frozen cost profile')
    if original is None and not (run / 'fixed_cost_profile.json').exists():
        original = frozen
    current = _profile(source / 'config' / (spot_costs.FILENAME if original_spot else 'fixed_costs.yaml'))
    from .research_defaults import (generate_research_defaults,
                                    is_known_legacy_research_record)
    from .research_cost_setup import known_legacy_origin
    generated = None
    selected, origins = {}, {}
    for uid, typ in specs.items():
        old = original is not None and uid in original['units']
        profile = original if old else current
        use_generated = (not original_spot and not old and (profile is None or uid not in profile['units'] or
                         (known_legacy_origin(profile) and
                          is_known_legacy_research_record(uid, profile['units'][uid]))))
        if use_generated:
            if generated is None:
                generated = generate_research_defaults(SimpleNamespace(base=base, units=units))
            profile = generated['profile']
        if profile is None or uid not in profile['units'] or profile['units'][uid]['type'] != typ:
            raise ValueError(f'{uid}: explicit matching cost profile is missing')
        selected[uid] = profile['units'][uid]
        origin_path = ((run / 'fixed_cost_profile.json' if (run / 'fixed_cost_profile.json').exists() else
                       run / 'scenario_snapshot/config/fixed_costs.yaml' if (run / 'scenario_snapshot/config/fixed_costs.yaml').exists() else
                       run / 'scenario_snapshot/config/generated_research_costs.json') if old else
                      run / 'scenario_snapshot/config/units.yaml' if use_generated else source / 'config' / (spot_costs.FILENAME if original_spot else 'fixed_costs.yaml'))
        unit_source = profile.get('unit_sources', {}).get(uid, {})
        origins[uid] = dict(input_file=str(origin_path), input_file_sha256=_hash(_read(origin_path)), kind='original_frozen_cost' if old else 'retrospective_supplemental_assumption',
            source=unit_source.get('source') or unit_source.get('evidence', {}).get('source') or profile['source'],
            source_kind=unit_source.get('source_kind', profile.get('source_kind', 'explicit_assumption_status_not_supplied')),
            profile_sha256=_hash(_json(profile).encode()), parameters=selected[uid])
        if old and unit_source:
            origins[uid]['original_unit_source'] = unit_source
        if use_generated:
            origins[uid]['generation_evidence'] = generated['evidence'][uid]
    for uid, typ in specs.items():
        if typ in ('hydro', 'storage', 'pumped'):
            inventory = frame[frame.unit == uid].copy().sort_values('period')
            _numeric(inventory, ['inventory_start', 'inventory_end', 'inventory_change'], f'{uid} inventory')
            expected_unit = 'MWh' if typ == 'storage' else '1e4m3'
            if 'inventory_unit' not in inventory or not (inventory.inventory_unit == expected_unit).all():
                raise ValueError(f'{uid}: missing or inconsistent inventory unit')
            if not np.allclose(inventory.inventory_end - inventory.inventory_start, inventory.inventory_change, atol=3 * ROUNDING, rtol=0):
                raise ValueError(f'{uid}: inventory changes do not reconcile')
            if len(inventory) > 1 and not np.allclose(inventory.inventory_end.to_numpy()[:-1], inventory.inventory_start.to_numpy()[1:], atol=2 * ROUNDING, rtol=0):
                raise ValueError(f'{uid}: discontinuous executed inventories')
    if 'cost_available' not in frame:
        raise ValueError('Original per-unit cost availability is unknown')
    frame['cost_missing_reason'] = frame['cost_missing_reason'].astype(object) if 'cost_missing_reason' in frame else ''
    rt_index = rt.set_index(['unit', 'period'])
    detail_indices = {}
    for typ, filename, cols in (
        ('storage', 'rt_storage.csv', ['P_dis', 'P_ch']),
        ('pumped', 'rt_pumped.csv', ['P_gen', 'P_pump', 'n_gen', 'n_pump'])):
        selected_ids = {u for u, kind in specs.items() if kind == typ}
        if selected_ids:
            detail = pd.read_csv(run / filename)
            _grid(detail, 'unit', selected_ids, periods, filename)
            _numeric(detail, cols, filename)
            if (detail[cols] < 0).any().any():
                raise ValueError(f'{filename}: negative power or mode count')
            if typ == 'pumped' and not np.equal(detail[['n_gen', 'n_pump']], np.floor(detail[['n_gen', 'n_pump']])).all().all():
                raise ValueError('Noninteger pumped counts')
            detail_indices[typ] = detail.set_index(['unit', 'period'])
    actual_initial = archived_actual_initial(run, specs)
    for uid, typ in specs.items():
        if actual_initial is None and typ in THERMAL and physical[uid].get('init', {}).get('online') not in (True, False, 0, 1):
            raise ValueError(f'{uid}: unknown initial thermal status')
        if actual_initial is None and typ == 'pumped' and physical[uid].get('init_mode') not in ('gen', 'pump', 'idle'):
            raise ValueError(f'{uid}: unknown initial pumped mode')
    for index, row in frame.iterrows():
        uid, t, typ = row.unit, row.period, specs[row.unit]
        if row.get('type') != typ or rt_index.loc[(uid, t)].get('type') != typ:
            raise ValueError(f'{uid}: type disagrees with frozen configuration')
        energy, starts, pumpstarts = (row[k] for k in ('energy_generated_or_discharged_mwh', 'startup_count', 'pump_startup_count'))
        if energy < 0 or starts < 0 or pumpstarts < 0 or starts != int(starts) or pumpstarts != int(pumpstarts):
            raise ValueError(f'{uid}: invalid energy/start counts')
        on = float(rt_index.loc[(uid, t), 'on']) if typ in THERMAL else 0.
        if typ in THERMAL and (on not in (0., 1.) or starts > 1):
            raise ValueError(f'{uid}: invalid thermal status')
        if typ not in THERMAL | {'pumped'} and starts != 0 or typ != 'pumped' and pumpstarts != 0:
            raise ValueError(f'{uid}: unsupported start counts')
        if typ in THERMAL:
            prior_on = (actual_initial['thermal'][uid]['online'] if actual_initial is not None else physical[uid]['init']['online']) if t == 0 else rt_index.loc[(uid, t - 1), 'on']
            _close(starts, int(on > 0 and not prior_on), 0, f'{uid} thermal startup')
        if typ in detail_indices:
            details = detail_indices[typ]
            detail_row = details.loc[(uid, t)]
            generation = float(detail_row['P_dis' if typ == 'storage' else 'P_gen'])
            consumption = float(detail_row['P_ch' if typ == 'storage' else 'P_pump'])
            _close(energy, generation * dt, ROUNDING * (1 + dt), f'{uid} gross generation')
            _close(float(rt_index.loc[(uid, t), 'P_mw']), generation - consumption, 3 * ROUNDING, f'{uid} net power')
            if typ == 'pumped':
                count = _integer(physical[uid].get('n_units'), f'{uid} n_units', 1)
                for key, mode, recorded in (('n_gen', 'gen', starts), ('n_pump', 'pump', pumpstarts)):
                    prior = (actual_initial['pumped'][uid]['ng' if mode == 'gen' else 'np'] if actual_initial is not None else (count if physical[uid]['init_mode'] == mode else 0)) if t == 0 else details.loc[(uid, t - 1), key]
                    if detail_row[key] > count:
                        raise ValueError(f'{uid}: pumped count exceeds physical units')
                    _close(recorded, max(float(detail_row[key]) - prior, 0), 0, f'{uid} pumped startup')
        if typ not in ('storage', 'pumped'):
            _close(energy, float(rt_index.loc[(uid, t), 'P_mw']) * dt, ROUNDING * (1 + dt), f'{uid} energy')
        cash = settlement[(settlement.entity.isin(names[uid])) & (settlement.period == t)]
        for output, column in zip(REVENUE_COLUMNS[:3], ('da_amount', 'rt_amount', 'total')):
            _close(row[output], cash[column].sum(), (len(cash) + 1) * ROUNDING, f'{uid} {output}')
        charge = cash.iloc[0:0] if typ not in ('storage', 'pumped') else cash[cash.entity == names[uid][1]]
        _close(row.charging_settlement_yuan, charge.total.sum(), (len(charge) + 1) * ROUNDING, f'{uid} charging')
        stops=pumpstops=0.
        if typ in THERMAL:stops=int(on==0 and prior_on)
        elif typ=='pumped':
            init=actual_initial['pumped'][uid] if actual_initial is not None else {'ng':count if physical[uid]['init_mode']=='gen' else 0,'np':count if physical[uid]['init_mode']=='pump' else 0}
            stops=max((init['ng'] if t==0 else details.loc[(uid,t-1),'n_gen'])-detail_row.n_gen,0)
            pumpstops=max((init['np'] if t==0 else details.loc[(uid,t-1),'n_pump'])-detail_row.n_pump,0)
        if original_spot:
            for key,value in [('shutdown_count',stops),('pump_shutdown_count',pumpstops)]:
                if key not in frame:raise ValueError('Missing archived shutdown evidence')
                _close(row[key],value,0,f'{uid} {key}')
        frame.at[index,'shutdown_count']=stops;frame.at[index,'pump_shutdown_count']=pumpstops
        params = selected[uid]
        rate = params.get('scalar_marginal_cost_yuan_per_mwh', params.get('discharge_degradation_yuan_per_mwh', params.get('generation_vom_yuan_per_mwh')))
        rate=params.get("fuel_cost_yuan_per_mwh",0.) if original_spot else rate
        variable = rate * energy
        online = params.get('fixed_online_cost_yuan_per_h', 0.) * on * dt
        startup = params.get('fixed_startup_cost_yuan_per_start', params.get('gen_start_yuan_per_unit_start', 0.)) * starts + params.get('pump_start_yuan_per_unit_start', 0.) * pumpstarts
        shutdown=0.
        if original_spot:variable,online,startup,shutdown=spot_costs.period_cost(params,energy,on,starts,pumpstarts,stops,pumpstops,dt)
        total = variable + online + startup + shutdown
        values = (variable, online, startup, total, row.cash_revenue_yuan - total)
        available = row.cost_available
        if not isinstance(available, (bool, np.bool_)):
            raise ValueError(f'{uid}: invalid original cost_available')
        if bool(available) != (origins[uid]['kind'] == 'original_frozen_cost'):
            raise ValueError(f'{uid}: original availability disagrees with archived costs')
        if available:
            for key, value in zip(COST_COLUMNS, values):
                old = row.get(key)
                if pd.isna(old) or not math.isfinite(float(old)):
                    raise ValueError(f'{uid}: existing costs incomplete')
                _close(float(old), value, ROUNDING * (abs(rate) + 3), f'{uid} original {key}')
        if original_spot and available:_close(row.get('shutdown_cost_yuan',float('nan')),shutdown,ROUNDING*3,f'{uid} shutdown cost')
        frame.at[index,'shutdown_cost_yuan']=shutdown
        for key, value in zip(COST_COLUMNS, values):
            frame.at[index, key] = value
        frame.at[index, 'cost_available'] = True
        frame.at[index, 'cost_missing_reason'] = ''
        frame.at[index, 'cost_source_kind'] = origins[uid]['kind']
        frame.at[index, 'cost_profile_sha256'] = origins[uid]['profile_sha256']
    sums = [*REVENUE_COLUMNS, *COST_COLUMNS, 'shutdown_cost_yuan','energy_generated_or_discharged_mwh', 'startup_count', 'pump_startup_count','shutdown_count','pump_shutdown_count']
    by_unit = frame.groupby(['unit', 'type'], sort=False)[sums].sum().reset_index()
    by_unit['inventory_unit'] = pd.Series([None] * len(by_unit), dtype=object)
    by_type = frame.groupby('type', sort=False)[sums].sum().reset_index()
    for table in (frame, by_unit, by_type):
        for key, value in scope.items():
            table[key] = value
        table['report_kind'] = 'retrospective_supplemental_assumptions'
        table['cost_available'] = True
        table['cost_missing_reason'] = ''
    for index, row in by_unit.iterrows():
        group = frame[frame.unit == row.unit].sort_values('period')
        for column in ('inventory_start', 'inventory_end', 'inventory_change', 'inventory_unit'):
            if column in frame:
                by_unit.at[index, column] = (group.iloc[0][column] if column in ('inventory_start', 'inventory_unit') else group.iloc[-1][column])
        if 'inventory_start' in by_unit and 'inventory_end' in by_unit:
            by_unit.at[index, 'inventory_change'] = by_unit.at[index, 'inventory_end'] - by_unit.at[index, 'inventory_start']
        by_unit.at[index, 'cost_source_kind'] = origins[row.unit]['kind']
        by_unit.at[index, 'cost_profile_sha256'] = origins[row.unit]['profile_sha256']
    report_summary = dict(scope, profit_basis=original_basis,cost_basis_version=spot_costs.VERSION if original_spot else 'legacy-fixed-costs-v1',report_kind='retrospective_supplemental_assumptions',
        label='simulated_cash_operating_profit_not_total_project_profit', all_unit_costs_available=True,
        unit_count=len(specs), original_run_unchanged=True, dt_h=dt,
        **{key: float(frame[key].sum()) for key in (*REVENUE_COLUMNS, *COST_COLUMNS)},
        precision='Recomputed from persisted four-decimal quantities, not exact pre-rounding dispatch. '
                  'Reconciliation uses half a last decimal per summed input; cost tolerance scales with marginal rate. '
                  'Output CSV uses four decimals; summary sums unrounded supplemental calculations.',
        exclusions='CAPEX,financing,tax,capacity,AGC,compensation,operator penalties,water opportunity cost,inventory valuation',
        revenue_scope='DA + RT signed net settlement; charging included; executed prefix only')
    if before != _fingerprints(run, source):
        raise ValueError('Historical evidence or selected costs changed during preflight')
    return before, origins, report_summary, {'by_period': frame, 'by_unit': by_unit, 'by_type': by_type}


def _calculate(run, source, policy=spot_costs.LEGACY):
    evidence,origins,summary,frames=_calculate_original(run,source)
    if policy not in (spot_costs.LEGACY,spot_costs.BASIS):raise ValueError('Unknown target profit basis')
    original_basis=summary['profit_basis']
    if policy==spot_costs.LEGACY:
        if original_basis!=spot_costs.LEGACY:raise ValueError('Cannot relabel a spot archive as legacy')
        return evidence,origins,summary,frames
    from types import SimpleNamespace
    units=yaml.safe_load(_read(run/'scenario_snapshot/config/units.yaml'))
    chosen=spot_costs.load_profile(SimpleNamespace(root=source,units=units))
    archived=_profile(run/'fixed_cost_profile.json',archived=True)
    archived=archived if archived and archived.get('profit_basis')==spot_costs.BASIS else None
    selected={};new_origins={}
    for uid,typ in zip(frames['by_unit'].unit,frames['by_unit'].type):
        profile=chosen if uid in chosen['units'] else archived
        if not profile or uid not in profile['units']:raise ValueError(f'{uid}: 缺少明确新口径成本；先保存现货成本，不从混合旧标量猜测')
        rec=profile['units'][uid];spot_costs.validate_record(uid,rec,typ);selected[uid]=rec
        path=source/'config'/spot_costs.FILENAME if profile is chosen else run/'fixed_cost_profile.json'
        new_origins[uid]=dict(input_file=str(path),input_file_sha256=_hash(_read(path)),
            kind='explicit_spot_cost_revision' if profile is chosen else 'original_frozen_cost',
            source=rec['source'],source_kind=rec['source_kind'],parameters=rec,profile_sha256=profile['sha256'])
    frame=frames['by_period'];rt=pd.read_csv(run/'rt_units.csv').set_index(['unit','period']);dt=summary['dt_h']
    for idx,row in frame.iterrows():
        rec=selected[row.unit];on=rt.loc[(row.unit,row.period),'on'] if row.type in THERMAL else 0.
        v,o,s,d=spot_costs.period_cost(rec,row.energy_generated_or_discharged_mwh,on,row.startup_count,row.pump_startup_count,row.shutdown_count,row.pump_shutdown_count,dt)
        for key,value in zip(COST_COLUMNS,(v,o,s,v+o+s+d,row.cash_revenue_yuan-v-o-s-d)):frame.at[idx,key]=value
        frame.at[idx,'shutdown_cost_yuan']=d
        frame.at[idx,'cost_profile_sha256']=new_origins[row.unit]['profile_sha256']
        frame.at[idx,'cost_source_kind']=rec['source_kind']
    sums=[*REVENUE_COLUMNS,*COST_COLUMNS,'shutdown_cost_yuan','energy_generated_or_discharged_mwh','startup_count','pump_startup_count','shutdown_count','pump_shutdown_count']
    # Preserve separately verified inventories, availability and scope metadata.
    for key,group in (('by_unit',['unit','type']),('by_type',['type'])):
        aggregated=frame.groupby(group,sort=False)[sums].sum()
        table=frames[key].set_index(group)
        for col in sums:table[col]=aggregated[col]
        frames[key]=table.reset_index()
    for table in frames.values():
        table['profit_basis']=policy;table['cost_basis_version']=spot_costs.VERSION
        table['report_kind']='explicit_spot_cost_recalculation'
    for idx,row in frames['by_unit'].iterrows():
        frames['by_unit'].at[idx,'cost_profile_sha256']=new_origins[row.unit]['profile_sha256']
        frames['by_unit'].at[idx,'cost_source_kind']=selected[row.unit]['source_kind']
    original_rows=pd.read_csv(run/'operating_profit_by_period.csv')
    original_complete=bool(len(original_rows) and original_rows.cost_available.eq(True).all())
    for idx,row in frames['by_type'].iterrows():
        ids=frames['by_unit'].loc[frames['by_unit'].type==row.type,'unit']
        frames['by_type'].at[idx,'cost_profile_sha256']=spot_costs.digest({uid:new_origins[uid]['profile_sha256'] for uid in ids})
    summary=dict(summary,profit_basis=policy,cost_basis_version=spot_costs.VERSION,original_profit_basis=original_basis,
        cost_profile_sha256=spot_costs.digest({'units':selected,'sources':{u:x['profile_sha256'] for u,x in new_origins.items()}}),
        cost_profile_hash_kind='resolved_selection',
        previous_policy_recomputed_cost_yuan=summary['cash_operating_cost_yuan'],
        report_kind='explicit_spot_cost_recalculation',exclusions=spot_costs.EXCLUSIONS,
        original_cash_operating_cost_yuan=summary['cash_operating_cost_yuan'] if original_complete else None,
        **{k:float(frame[k].sum()) for k in (*REVENUE_COLUMNS,*COST_COLUMNS,'shutdown_cost_yuan')})
    if evidence!=_fingerprints(run,source):raise ValueError('Evidence changed during spot recalculation')
    return evidence,new_origins,summary,frames


def prepare_recalculation(run_dir, cost_root, *, policy=spot_costs.LEGACY):
    """Return a JSON-serializable, read-only plan; never infer a cost source."""
    run, source = Path(os.path.abspath(run_dir)), Path(os.path.abspath(cost_root))
    try:
        evidence, origins, summary, _ = _calculate(run, source, policy)
    except (OSError, KeyError, TypeError, AttributeError, yaml.YAMLError, pd.errors.ParserError) as exc:
        raise ValueError(f'Historical cost preflight failed: {exc}') from exc
    plan = dict(version=2 if policy==spot_costs.BASIS else VERSION, run_dir=str(run), cost_root=str(source),
                evidence=evidence, unit_sources=origins, summary=summary)
    if policy==spot_costs.BASIS:plan['profit_basis']=policy
    plan['fingerprint'] = _hash(_json(plan).encode())
    plan['output_dir'] = str(run / 'cost_recalculations' / plan['fingerprint'])
    return plan


def apply_recalculation(plan):
    """Verify an approved plan and publish an immutable, idempotent supplement."""
    fresh = prepare_recalculation(plan['run_dir'], plan['cost_root'],policy=plan.get('profit_basis',spot_costs.LEGACY))
    if fresh != plan:
        raise ValueError('Evidence or selected cost assumptions changed; prepare a new plan')
    run, source = Path(plan['run_dir']), Path(plan['cost_root'])
    root = run / 'cost_recalculations'
    if root.is_symlink():
        raise ValueError('Supplement directory must not be a symbolic link')
    root.mkdir(exist_ok=True)
    destination = root / plan['fingerprint']
    with file_lock(root / '.publish.lock', blocking=True):
        if destination.exists():
            manifest = json.loads(_read(destination / 'manifest.json'))
            if manifest.get('plan') != plan or any(_hash(_read(destination / name)) != digest for name, digest in manifest.get('outputs', {}).items()):
                raise ValueError('Existing supplemental report has changed; refusing overwrite')
            if set(manifest.get('outputs', {})) != {'operating_profit_summary.json', 'profile.json', 'operating_profit_by_period.csv', 'operating_profit_by_unit.csv', 'operating_profit_by_type.csv'}:
                raise ValueError('Existing supplemental report is incomplete')
            return destination
        _, _, summary, frames = _calculate(run, source,plan.get('profit_basis',spot_costs.LEGACY))
        if prepare_recalculation(run, source,policy=plan.get('profit_basis',spot_costs.LEGACY)) != plan:
            raise ValueError('Evidence changed before publication')
        temporary = Path(tempfile.mkdtemp(prefix='.preparing-', dir=root))
        try:
            (temporary / 'operating_profit_summary.json').write_text(_json(summary), encoding='utf-8')
            (temporary / 'profile.json').write_text(_json(plan['unit_sources']), encoding='utf-8')
            for key, frame in frames.items():
                frame.to_csv(temporary / f'operating_profit_{key}.csv', index=False, float_format='%.4f')
            outputs = {p.name: _hash(p.read_bytes()) for p in temporary.iterdir()}
            (temporary / 'manifest.json').write_text(_json(dict(plan=plan, outputs=outputs)), encoding='utf-8')
            if prepare_recalculation(run, source,policy=plan.get('profit_basis',spot_costs.LEGACY)) != plan:
                raise ValueError('Evidence changed while preparing report')
            os.rename(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination


def read_recalculation(report_dir, *, run_dir=None):
    """Read a verified immutable supplement, rejecting altered source evidence."""
    directory = Path(os.path.abspath(report_dir))
    manifest = json.loads(_read(directory / 'manifest.json'))
    plan = manifest['plan']
    if plan.get('version') not in (1,2) or plan.get('profit_basis',spot_costs.LEGACY) not in (spot_costs.LEGACY,spot_costs.BASIS):raise ValueError('Unknown saved report policy/version')
    if run_dir is not None and Path(os.path.abspath(run_dir)) != Path(plan['run_dir']):
        raise ValueError('Supplement belongs to a different run')
    if directory != Path(plan['output_dir']):
        raise ValueError('Supplement directory disagrees with its manifest')
    identity = {k: v for k, v in plan.items() if k not in ('fingerprint', 'output_dir')}
    if _hash(_json(identity).encode()) != plan['fingerprint']:
        raise ValueError('Supplemental plan fingerprint is inconsistent')
    run = Path(plan['run_dir'])
    original_evidence = {filename: digest for filename, digest in plan['evidence'].items()
                         if Path(filename).is_relative_to(run)}
    # Compare the complete frozen file set, not only paths that existed before.
    # The external cost source may change or disappear after publication.
    current_evidence = _fingerprints(run, None)
    if current_evidence != original_evidence:
        raise ValueError('Original run evidence changed since supplementation')
    expected = {'operating_profit_summary.json', 'profile.json', 'operating_profit_by_period.csv', 'operating_profit_by_unit.csv', 'operating_profit_by_type.csv'}
    if set(manifest.get('outputs', {})) != expected:
        raise ValueError('Incomplete supplemental report manifest')
    for name, digest in manifest['outputs'].items():
        if _hash(_read(directory / name)) != digest:
            raise ValueError('Supplemental report output changed')
    result = {'manifest': manifest, 'summary': json.loads(_read(directory / 'operating_profit_summary.json'))}
    for key in ('by_period', 'by_unit', 'by_type'):
        result[key] = pd.read_csv(directory / f'operating_profit_{key}.csv')
    if json.loads(_read(directory / 'profile.json')) != plan['unit_sources'] or result['summary'] != plan['summary']:
        raise ValueError('Saved profile/summary disagrees with approved plan')
    period = result['by_period']
    basis=plan.get('profit_basis',spot_costs.LEGACY)
    for table in (result['by_period'],result['by_unit'],result['by_type']):
        if 'profit_basis' in table and not table.profit_basis.eq(basis).all():raise ValueError('Saved report policy mismatch')
    original_rt = pd.read_csv(run / 'rt_units.csv').set_index(['unit', 'period'])
    for _, row in period.iterrows():
        parameters = plan['unit_sources'][row.unit]['parameters']
        is_spot=plan.get('profit_basis',spot_costs.LEGACY)==spot_costs.BASIS
        rate = 0. if is_spot else parameters.get('scalar_marginal_cost_yuan_per_mwh', parameters.get('discharge_degradation_yuan_per_mwh', parameters.get('generation_vom_yuan_per_mwh')))
        variable = row.energy_generated_or_discharged_mwh * rate
        online = parameters.get('fixed_online_cost_yuan_per_h', 0) * (original_rt.loc[(row.unit, row.period), 'on'] if row.type in THERMAL else 0) * plan['summary']['dt_h']
        startup = parameters.get('fixed_startup_cost_yuan_per_start', parameters.get('gen_start_yuan_per_unit_start', 0)) * row.startup_count + parameters.get('pump_start_yuan_per_unit_start', 0) * row.pump_startup_count
        shutdown=0.
        if is_spot:
            variable,online,startup,shutdown=spot_costs.period_cost(parameters,row.energy_generated_or_discharged_mwh,original_rt.loc[(row.unit,row.period),'on'] if row.type in THERMAL else 0,row.startup_count,row.pump_startup_count,row.shutdown_count,row.pump_shutdown_count,plan['summary']['dt_h'])
            _close(row.shutdown_cost_yuan,shutdown,2*ROUNDING,'Saved shutdown cost')
        total = variable + online + startup + shutdown
        for column, value in zip(COST_COLUMNS, (variable, online, startup, total, row.cash_revenue_yuan - total)):
            _close(float(row[column]), value, 2 * ROUNDING, f'Saved report {column}')
    for key in (*REVENUE_COLUMNS, *COST_COLUMNS):
        _close(float(period[key].sum()), result['summary'][key], (len(period) + 1) * ROUNDING, f'Saved summary {key}')
    return result
