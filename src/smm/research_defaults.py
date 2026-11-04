"""Pure, ID-independent synthetic study defaults, never observed plant costs.

Only an explicit caller action may persist these outputs. Offers and cash costs
are separate artifacts. Capacity scaling below is a research convention, not an
empirical plant-cost law; marginal CNY/MWh is never multiplied by MW.
"""
from __future__ import annotations

import hashlib
import json
import math
from numbers import Real

ALGORITHM_VERSION = 'typed-research-defaults-v1'
SOURCE_KIND = 'research_assumption_not_observed_plant_data'
SOURCE = ('Synthetic research assumptions, not observed plant data. Type templates '
          'from original config/bid_cost_basis.yaml and config/fixed_costs.yaml; '
          'online/startup capacity scaling is a research convention, not an empirical law.')
_THERMAL = {'coal': (30, 9, 10, 646, 6000, 250000),
            'gas_ccgt': (60, 6.5, 15, 687, 9000, 150000),
            'gas_gt': (60, 10, 20, 300, 4000, 40000)}


def research_record_hash(record: dict) -> str:
    """Deterministic exact record fingerprint; metadata/extra fields also count."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _legacy_records():
    records = {}
    for uid, typ, marginal, online, startup in (
        ('C1', 'coal', 280, 6000, 250000), ('C2', 'coal', 297, 6500, 280000),
        ('C3', 'coal', 315, 7500, 320000), ('CCGT', 'gas_ccgt', 405, 9000, 150000),
        ('GT', 'gas_gt', 620, 4000, 40000)):
        records[uid] = dict(type=typ, scalar_marginal_cost_yuan_per_mwh=marginal,
                            fixed_online_cost_yuan_per_h=online,
                            fixed_startup_cost_yuan_per_start=startup)
    for uid, typ, marginal in (('W1', 'wind', 8), ('W2', 'wind', 11),
                              ('PV1', 'pv', 5), ('H1', 'hydro', 6)):
        records[uid] = dict(type=typ, scalar_marginal_cost_yuan_per_mwh=marginal)
    for uid, degradation in (('ES1', 25), ('ES2', 35), ('ES3', 45)):
        records[uid] = dict(type='storage', discharge_degradation_yuan_per_mwh=degradation)
    records['PS1'] = dict(type='pumped', generation_vom_yuan_per_mwh=10,
                         gen_start_yuan_per_unit_start=2000, pump_start_yuan_per_unit_start=4000)
    return records


def is_known_legacy_research_record(uid: str, record: dict) -> bool:
    """Match the 13 original records exactly, not a caller's provenance label."""
    expected = _legacy_records().get(uid)
    return (expected is not None and isinstance(record, dict) and record == expected
            and all(not isinstance(v, bool) for v in record.values()))


def _positive(spec, field, uid, *, integer=False):
    value = spec.get(field)
    if (not isinstance(value, Real) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0
            or (integer and int(value) != value)):
        raise ValueError(f'{uid}.{field}: 必须提供有限正' + ('整数' if integer else '数'))
    return float(value)


def generate_research_defaults(cfg) -> dict:
    """Return detached ``profile``, legacy-schema ``worksheet``, and ID evidence.

    Requires only relevant technical fields, not bids or existing cost archives.
    All eight supported types use type templates; IDs never select parameters.
    Disabled entries and fixed-schedule tie resources are excluded. Unknown
    active types, duplicate IDs, and missing/invalid technical inputs fail closed.
    """
    if not isinstance(cfg.units, dict):
        raise ValueError('units须为对象')
    season = cfg.base.get('season')
    if season not in ('wet', 'dry'):
        raise ValueError('season须为wet或dry')
    profile = dict(version='synthetic-fixed-operating-costs-v1', source_kind=SOURCE_KIND,
                   source=SOURCE, accounting_scope='Energy-only simulated cash operating profit; excludes capital, financing, tax, AGC, compensation, inventory valuation, water opportunity cost and operator constraint penalties. Charging settlement already belongs to net cash revenue.', units={})
    sheet = dict(version='synthetic-cost-study-v1', source_kind=SOURCE_KIND, source=SOURCE,
                 currency_energy_units='CNY/MWh; fuel CNY/GJ_HHV; heat rate GJ_HHV/MWh',
                 thermal={}, renewable_vom={}, hydro={}, storage={}, pumped={}, season=season,
                 thermal_offer_markup_factors=[1, 1.02, 1.05, 1.09, 1.14],
                 renewable_strategy_increments=[0, 10, 20, 30, 40],
                 hydro_opportunity_increments=[0, 15, 35, 60, 90],
                 storage_sell_increments=[0, 15, 30], storage_buy_decrements=[0, 15, 30],
                 pumped_sell_increments=[0, 20, 45],
                 accounting_warning='Offer opportunity costs only; do not add again to objective or settlement costs',
                 fixed_cost_link='Cash costs belong only to the separate fixed cost profile; offer worksheets never override them.')
    evidence = {}
    allowed = {'thermal': set(_THERMAL), 'renewable': {'wind', 'pv'},
               'hydro': {'hydro'}, 'storage': {'storage'}, 'pumped': {'pumped'}}
    for family, entries in cfg.units.items():
        if family in ('tie', '_disabled'):
            continue
        if family not in allowed:
            raise ValueError(f'未知资源组: {family}')
        if not isinstance(entries, list):
            raise ValueError(f'{family}须为资源列表')
        for spec in entries:
            if not isinstance(spec, dict):
                raise ValueError(f'{family}资源须为对象')
            if spec.get('enabled', True) is False:
                continue
            uid = spec.get('id')
            if not isinstance(uid, str) or not uid.strip() or uid in evidence:
                raise ValueError(f'资源ID缺失或重复: {uid}')
            typ = spec.get('type', family if family in ('hydro', 'storage', 'pumped') else None)
            if typ not in allowed[family]:
                raise ValueError(f'{uid}: 未知或不匹配资源类型 {typ}')
            tech, params, formulas = {}, {}, {}
            positive = lambda field, integer=False: _positive(spec, field, uid, integer=integer)
            rec = {'type': typ}
            if typ in _THERMAL:
                cap = positive('pmax')
                fuel, heat, vom, reference, online, startup = _THERMAL[typ]
                params = dict(fuel_price=fuel, heat_rate=heat, variable_om=vom,
                              reference_capacity_mw=reference, reference_online_yuan_per_h=online,
                              reference_startup_yuan_per_start=startup)
                rec.update(scalar_marginal_cost_yuan_per_mwh=fuel * heat + vom,
                           fixed_online_cost_yuan_per_h=online * (cap / reference),
                           fixed_startup_cost_yuan_per_start=startup * (cap / reference))
                sheet['thermal'][uid] = dict(fuel_price=fuel, heat_rate=heat, variable_om=vom)
                formulas = dict(marginal='fuel_price * heat_rate + variable_om',
                                online='reference_online * pmax / reference_capacity',
                                startup='reference_startup * pmax / reference_capacity')
            elif typ in ('wind', 'pv'):
                cap = positive('cap')
                vom = 8 if typ == 'wind' else 5
                params = {'variable_om': vom}
                rec['scalar_marginal_cost_yuan_per_mwh'] = vom
                sheet['renewable_vom'][uid] = vom
            elif typ == 'hydro':
                tech = dict(n_units=positive('n_units', True), unit_pmax=positive('unit_pmax'))
                cap = tech['n_units'] * tech['unit_pmax']
                params = dict(variable_om=6, water_opportunity={'wet': 110, 'dry': 180})
                rec['scalar_marginal_cost_yuan_per_mwh'] = 6
                sheet['hydro'][uid] = dict(variable_om=6, water_opportunity={'wet': 110, 'dry': 180})
                formulas = dict(cash_marginal='variable_om', offer_anchor='variable_om + water_opportunity[season]')
            elif typ == 'storage':
                cap = positive('pmax')
                tech = {key: positive(key) for key in ('ecap', 'eta_c', 'eta_d')}
                if tech['eta_c'] > 1 or tech['eta_d'] > 1:
                    raise ValueError(f'{uid}: 充放电效率须不大于1')
                tech['roundtrip_efficiency'] = tech['eta_c'] * tech['eta_d']
                tech['duration_h'] = tech['ecap'] / cap
                params = dict(replacement_energy=250, degradation=25, reservation=410)
                rec['discharge_degradation_yuan_per_mwh'] = 25
                sheet['storage'][uid] = params.copy()
                formulas = dict(efficiency='eta_c * eta_d', cash_marginal='degradation',
                                offer_anchor='replacement_energy / roundtrip_efficiency + degradation')
            else:
                tech = {key: positive(key, key == 'n_units') for key in
                        ('n_units', 'unit_gen_max', 'unit_pump', 'e_gen', 'e_pump')}
                cap = tech['n_units'] * tech['unit_gen_max']
                tech['pump_capacity_mw'] = tech['n_units'] * tech['unit_pump']
                tech['roundtrip_efficiency'] = tech['e_gen'] / tech['e_pump']
                if tech['roundtrip_efficiency'] > 1:
                    raise ValueError(f'{uid}: 抽蓄综合效率须不大于1')
                params = dict(replacement_energy=250, variable_om=10, reservation=390)
                rec.update(generation_vom_yuan_per_mwh=10,
                           gen_start_yuan_per_unit_start=2000 * (tech['unit_gen_max'] / 300),
                           pump_start_yuan_per_unit_start=4000 * (tech['unit_pump'] / 300))
                sheet['pumped'][uid] = params.copy()
                params.update(reference_unit_mw=300, reference_gen_start=2000, reference_pump_start=4000)
                formulas = dict(efficiency='e_gen / e_pump', cash_marginal='variable_om',
                                offer_anchor='replacement_energy / roundtrip_efficiency + variable_om',
                                gen_start='2000 * unit_gen_max / 300', pump_start='4000 * unit_pump / 300')
            # Reject overflow/underflow as well as invalid supplied values.
            derived = [cap, *[v for v in tech.values() if isinstance(v, Real)],
                       *[v for v in rec.values() if isinstance(v, Real)]]
            if any(not math.isfinite(v) or v <= 0 for v in derived):
                raise ValueError(f'{uid}: 派生容量/效率/成本须为有限正数')
            profile['units'][uid] = rec
            evidence[uid] = dict(type=typ, capacity_mw=cap, technical_parameters=tech,
                                 parameters=params, formulas=formulas, algorithm_version=ALGORITHM_VERSION,
                                 source_kind=SOURCE_KIND, source=SOURCE, record_hash=research_record_hash(rec),
                                 capacity_scope='MW/MWh constrain quantities; do not scale marginal CNY/MWh')
    return dict(profile=profile, worksheet=sheet, evidence=evidence)


def is_known_legacy_worksheet_record(group: str, uid: str, record) -> bool:
    """Strict equality against original worksheet values; no file reads/ID inference."""
    legacy = {
        'thermal': {
            'C1': dict(fuel_price=30, heat_rate=9, variable_om=10),
            'C2': dict(fuel_price=30, heat_rate=9.5, variable_om=12),
            'C3': dict(fuel_price=30, heat_rate=10, variable_om=15),
            'CCGT': dict(fuel_price=60, heat_rate=6.5, variable_om=15),
            'GT': dict(fuel_price=60, heat_rate=10, variable_om=20)},
        'renewable_vom': {'W1': 8, 'W2': 11, 'PV1': 5},
        'hydro': {'H1': dict(variable_om=6, water_opportunity={'wet': 110, 'dry': 180})},
        'storage': {
            'ES1': dict(replacement_energy=250, degradation=25, reservation=410),
            'ES2': dict(replacement_energy=250, degradation=35, reservation=425),
            'ES3': dict(replacement_energy=250, degradation=45, reservation=440)},
        'pumped': {'PS1': dict(replacement_energy=250, variable_om=10, reservation=390)},
    }
    expected = legacy.get(group, {}).get(uid)
    # Boolean values cannot match any positive legacy numeric constants, but
    # recursive equality intentionally treats YAML integer/float equivalents alike.
    return expected is not None and record == expected
