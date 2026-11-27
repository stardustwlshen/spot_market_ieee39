"""Dadu 19-station synthetic market boundaries, with independent issue clocks."""

import csv
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import random

if __package__:
    from .sichuan_v5_energy import Case, ClearingError, Line, Resource
    from .sichuan_v5_hydro import Cascade, Reach, Reservoir
    from .sichuan_v5_rules import Participant, participation_rule
    from .sichuan_v5_segments import research_curve, validate_curve
else:
    from sichuan_v5_energy import Case, ClearingError, Line, Resource
    from sichuan_v5_hydro import Cascade, Reach, Reservoir
    from sichuan_v5_rules import Participant, participation_rule
    from sichuan_v5_segments import research_curve, validate_curve

DATA = Path(__file__).resolve().parents[1] / 'data'
ATLAS = DATA / 'dadu19'
TOPOLOGY = DATA / 'case39'
SCENARIOS = ('wet_low', 'dry_peak', 'forecast_error')
RULE_PDF_SHA256 = '8e8ee5bebadb0f0ae1873315790b47ed8694a68b8ca4d1e160ec669e74c42ad5'


def _table(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog():
    stations = _table(ATLAS / 'stations.csv')
    for row in stations:
        for field in ('source_number', 'bus', 'capacity_mw', 'storage_m3'):
            row[field] = int(row[field])
        for field in ('consumption_m3_kwh', 'mean_flow_m3s'):
            row[field] = float(row[field])
    if ([s['id'] for s in stations] != [f'D{i:02d}' for i in range(1, 20)]
            or sum(s['capacity_mw'] for s in stations) != 22056
            or [s['id'] for s in stations if s['model'] == 'reservoir'] != ['D01', 'D03', 'D04', 'D10']):
        raise ClearingError('大渡河站序、四库集合或设计装机不符')
    return stations


def _network(stations, config):
    demand0 = {int(x['bus']): float(x['load_mw']) for x in _table(TOPOLOGY / 'buses.csv')}
    system_factor = config['network']['design_peak_mw'] / sum(demand0.values())
    branches = _table(TOPOLOGY / 'branches.csv')
    degrees = {b: 0 for b in demand0}
    gen_mw = {b: 0 for b in demand0}
    for line in branches:
        for field in ('from_bus', 'to_bus'):
            degrees[int(line[field])] += 1
    for s in stations:
        gen_mw[s['bus']] += s['capacity_mw']
    for row in config['coal'] + config['gas'] + config['pv']:
        gen_mw[row[2]] += row[3]
    lines = []
    for branch in branches:
        a, b = int(branch['from_bus']), int(branch['to_bus'])
        base_limit = float(branch['rate_mw'])
        factor = system_factor
        for bus in (a, b):
            if degrees[bus] == 1 and gen_mw[bus]:
                factor = max(factor, config['network']['gen_radial_fraction'] * gen_mw[bus] / base_limit)
        lines.append(Line(branch['branch_id'], a, b, float(branch['x_pu']) / factor, base_limit * factor))
    return demand0, tuple(lines)


def _bell(t, center, spread):
    return math.exp(-((t - center) / spread) ** 2)


def _phases(seed):
    generator = random.Random(seed)
    return [generator.uniform(0, 2 * math.pi) for _ in range(5)]


def _demand(config, demand0):
    # Independently specified daily MWh and peak, split over original bus weights.
    shape = [.10 * _bell(t, 34, 9) + .12 * _bell(t, 49, 15) + .34 * _bell(t, 78, 10)
             for t in range(96)]
    mean_shape = sum(shape) / 96
    mean_mw = config['energy_gwh'] * 1000 / 24
    slope = (config['peak_mw'] - mean_mw) / (max(shape) - mean_shape)
    total = sum(demand0.values())
    return tuple({b: p / total * (mean_mw + slope * (value - mean_shape))
                  for b, p in demand0.items()} for value in shape)


def _hydrology(stations, settings, phases):
    reaches = []
    raw_reaches = _table(ATLAS / 'reaches.csv')
    upstreams = {s['id']: s for s in stations}
    for i, link in enumerate(raw_reaches):
        u, d = link['upstream'], link['downstream']
        if (u, d) != (stations[i]['id'], stations[i + 1]['id']):
            raise ClearingError('表外河段归并后的梯级边与厂站顺序不符')
        length = int(link['travel_quarters'])
        # Explicit synthetic pre-day steady release history in arrival order.
        base = upstreams[u]['mean_flow_m3s'] * settings['runoff_scale']
        reaches.append(Reach(u, d, length, (base * 900.,) * length))
    if len(reaches) != 18:
        raise ClearingError('梯级须含18段连续河道')
    reservoirs, quantities = [], []
    previous = 0.
    for i, station in enumerate(stations):
        source = station['mean_flow_m3s']
        increment = source - previous
        if increment < 0:
            raise ClearingError('断面均值出现负区间来水，须复核来源或明确取水边界')
        previous = source
        q = source * settings['runoff_scale']
        local = increment * settings['runoff_scale']
        quantities.append(tuple(local * (1 + .038 * math.sin(2 * math.pi * t / 96 + phases[0] - i * .09))
                                for t in range(96)))
        active = station['storage_m3']
        capacity = active * settings['season_storage_max_pu']
        initial = active * settings['storage_start_pu']
        terminal_min = max(0., initial - settings['terminal_drawdown_days'] * q * 86400.)
        terminal_max = min(capacity, initial + settings['terminal_fill_days'] * q * 86400.)
        # One m³/kWh is 1/3.6 m³/s per MW at constant head.
        factor = station['consumption_m3_kwh'] / 3.6
        max_release = max(4.0 * source, 1.6 * factor * station['capacity_mw'])
        reservoirs.append(Reservoir(
            station['id'], 0., capacity, initial, factor, max_release,
            min_release_m3s=.095 * q, max_release_m3s=max_release,
            regulation=station['model'], terminal_min_m3=terminal_min,
            terminal_max_m3=terminal_max, min_bypass_m3s=.006 * q,
        ))
    inflows = tuple({s['id']: quantities[i][t] for i, s in enumerate(stations)} for t in range(96))
    return Cascade(tuple(reservoirs), tuple(reaches), inflows, require_all_hydro=True)


def _resources(config, settings, stations):
    rs, assets = [], []
    for s in stations:
        cap = s['capacity_mw']
        # Before a company submission is applied, seed a registered naïve curve.
        count = 10 if s['model'] == 'reservoir' else 5
        steps = [round(cap * i / count, 2) for i in range(count + 1)]
        blocks = tuple((b - a, float(i * 20)) for i, (a, b) in enumerate(zip(steps, steps[1:])))
        initial = min(.65 * cap, .7 * s['mean_flow_m3s'] * settings['runoff_scale'] * 3.6 / s['consumption_m3_kwh'])
        rs.append(Resource(s['id'], s['bus'], 'provincial_hydro', 'hydro', cap, 0.,
                           ramp_mw_15min=.65 * cap, initial_mw=initial, segments=blocks))
        assets.append({'id': s['id'], 'name': s['name'], 'bus': s['bus'], 'capacity_mw': cap,
                       'kind': 'hydro', 'role': 'provincial_hydro', 'model': s['model']})
    # Draft Annex 2, (10)-(11). Coal's wet season starts in June.
    min_up = 4 * (72 if 6 <= settings['month'] <= 10 else 120)
    min_down = 4 * 24
    for rid, name, bus, cap, first_price in config['coal']:
        minimum = round(cap * .35, 2)
        curve = research_curve('coal', cap, first_price, coal_minimum_mw=minimum)
        initial = minimum if rid in ('COAL1', 'COAL3') else 0.
        # Signed, synthetic pre-day statuses have completed their minimum durations.
        age = min_up + 16 if initial else min_down + 16
        rs.append(Resource(rid, bus, 'public_coal', 'thermal', cap, first_price, minimum,
                           cap * .28, initial, min_up, min_down, age, cap * 72., segments=curve))
        assets.append({'id': rid, 'name': name, 'bus': bus, 'capacity_mw': cap,
                       'kind': 'thermal', 'role': 'public_coal'})
    for rid, name, bus, cap in config['gas']:
        rs.append(Resource(rid, bus, 'gas', 'fixed', cap, 0.))
        assets.append({'id': rid, 'name': name, 'bus': bus, 'capacity_mw': cap,
                       'kind': 'fixed', 'role': 'gas'})
    for rid, name, bus, cap in config['pv']:
        validate_curve('renewable', cap, [(0, cap, 0)])
        rs.append(Resource(rid, bus, 'central_renewable', 'renewable', cap, 0.,
                           segments=((float(cap), 0.),)))
        assets.append({'id': rid, 'name': name, 'bus': bus, 'capacity_mw': cap,
                       'kind': 'renewable', 'role': 'central_renewable'})
    return tuple(rs), assets


def build_case(scenario='wet_low', seed=39):
    """Build the signed market D-1 forecasts. No realized RT data participates."""
    if scenario not in SCENARIOS or type(seed) is not int:
        raise ClearingError('未识别的大渡河场景或随机种子')
    config = json.loads((ATLAS / 'scenarios.json').read_text(encoding='utf-8'))
    settings = config['scenarios'][scenario]
    stations = _catalog()
    demand0, lines = _network(stations, config)
    demand = _demand(settings, demand0)
    phases = _phases(seed)
    cascade = _hydrology(stations, settings, phases)
    resources, assets = _resources(config, settings, stations)
    available, plans, expected = [], [], []
    for t in range(96):
        hour = (t + .5) / 4
        daylight = max(0., math.sin(math.pi * (hour - 6) / 12)) if 6 < hour < 18 else 0.
        values = {s['id']: round(s['capacity_mw'] * (.93 + .015 * math.sin(phases[1] + i)), 3)
                  for i, s in enumerate(stations)}
        values.update({rid: cap * .97 for rid, _, _, cap, _ in config['coal']})
        for i, (rid, _, _, cap) in enumerate(config['pv']):
            cloud = .96 + .04 * math.sin(i * .51 + phases[2] + t / 16)
            values[rid] = cap * settings['pv_peak_pu'] * daylight ** 1.35 * cloud
        plan = {}
        for i, (rid, _, _, cap) in enumerate(config['gas']):
            values[rid] = cap * (.96 - i * .02)
            plan[rid] = settings['gas_base_mw'] + (settings['gas_peak_mw'] - settings['gas_base_mw']) * _bell(t, 78 + i, 10)
        available.append(values)
        plans.append(plan)
        # Exogenous synthetic price expectation frozen the day before any solve.
        reference = settings['reference_yuan_mwh']
        mid = reference + 170 * _bell(t, 78, 12) + 45 * _bell(t, 34, 9) - 78 * daylight
        expected.append({s['id']: round(mid + 5 * math.sin(s['bus'] * .6 + phases[2]), 2) for s in stations})
    case = Case(tuple(demand0), lines, resources, demand, tuple(available),
                cascade=cascade, fixed_schedule=tuple(plans))
    value, downstream = {}, 0.
    for s in reversed(stations):
        # Expected value of this water at all downstream plants, incl. its present location.
        downstream += .78 * settings['reference_yuan_mwh'] / (1000 * s['consumption_m3_kwh'])
        value[s['id']] = round(downstream, 8)
    month = settings['month']
    declarations = {r.id: participation_rule(Participant(r.role), month).bid_mode.value for r in resources}
    meta = {
        'schema': 'dadu19-v1', 'scenario': scenario, 'scenario_label': settings['label'],
        'month': month, 'seed': seed, 'stations': stations, 'assets': assets,
        'expected_price_yuan_mwh': expected, 'water_value_yuan_m3': value,
        'water_value_basis': '由当前位置至末站各站的水能价值之和，季节外生参考价乘合成兑现系数0.78。',
        'declared_participation': declarations, 'rules_pdf_sha256': RULE_PDF_SHA256,
        'market_source_commit': config['market_source_commit'], 'atlas_source_commit': config['atlas_source_commit'],
        'atlas_blobs': config['atlas_blobs'], 'study_type': 'synthetic_lossless_dc_dadu19',
        'source_note': config['study'], 'load_energy_gwh': settings['energy_gwh'], 'load_peak_mw': settings['peak_mw'],
        'network_assumption': config['network'], 'hydrology_assumption': config['hydrology'],
        'hydraulic_limits_note': '河段时滞按3.5 m/s四舍五入；生态下泄、旁泄、库容和期末边界为合成调度场景。',
        'gas_plan_note': '两台气机执行外生非市场保供计划，无报价及现货现金收入。',
        'coal_duration_note': '草案附件2：6—10月最小开机72小时，其余月120小时，全年最小停机24小时。合成期初两开两停，均已运行或停运超过各自最短时间。',
        'pv_category': '普通集中式光伏，无配储，模拟主动申报全量0元/MWh，实际可发受分时预测约束',
        'issued_at': {'market_day_ahead': 'D-1 08:30', 'company_information': 'D-1 08:45',
                      'company_price_forecast': 'D-1 08:45'},
        'topology_sha256': {name: _digest(TOPOLOGY / name) for name in ('buses.csv', 'branches.csv')},
        'asset_data_sha256': {name: _digest(ATLAS / name) for name in ('stations.csv', 'reaches.csv', 'scenarios.json')},
    }
    return case, meta


def company_snapshot(case, meta):
    """Separately issued D-1 inflow view. No simulated actuals or cleared prices."""
    phase = _phases(meta['seed'] + 101)[0]
    inflow = tuple({key: value * (1 + .025 * math.sin(phase + t / 16 + int(key[1:]) / 7))
                    for key, value in row.items()} for t, row in enumerate(case.cascade.natural_inflow_m3s))
    return replace(case, cascade=replace(case.cascade, natural_inflow_m3s=inflow))


def forecast_callback(meta):
    """Issue a window from its current boundary observation; persist/decay into its tail.

    Only the lead-zero observation is synthesized. No future realized cell is
    accessed. The callback always receives the original signed market case.
    """
    phases = _phases(meta['seed'] + 503)
    stress = meta['scenario'] == 'forecast_error'

    def source(case, t, stop):
        if not 0 <= t < stop <= 96:
            raise ClearingError('实时窗口索引越界')
        # Synthetic measurements released at the start of t; observation timing is explicit.
        load_change = .009 * math.sin(t / 9 + phases[0])
        solar_change = .035 * math.sin(t / 6 + phases[1])
        local_change = .028 * math.sin(t / 13 + phases[2])
        if stress:
            load_change += .055 * _bell(t, 78, 7)
            solar_change -= .40 * _bell(t, 50, 6)
        demand, availability, inflows = list(case.demand), list(case.available), list(case.cascade.natural_inflow_m3s)
        for k in range(t, stop):
            decay = .75 ** (k - t)
            demand[k] = {b: mw * (1 + decay * load_change) for b, mw in case.demand[k].items()}
            availability[k] = dict(case.available[k])
            for rid in ('PV1', 'PV2', 'PV3', 'PV4'):
                availability[k][rid] = min(2205., max(0., availability[k][rid] * (1 + decay * solar_change)))
            inflows[k] = {
                rid: q * (1 + decay * (local_change + (
                    .55 * _bell(t, 45, 6) if stress and rid in ('D03', 'D10') else 0.
                ))) for rid, q in case.cascade.natural_inflow_m3s[k].items()
            }
        return replace(case, demand=tuple(demand), available=tuple(availability),
                       cascade=replace(case.cascade, natural_inflow_m3s=tuple(inflows)))

    return source
