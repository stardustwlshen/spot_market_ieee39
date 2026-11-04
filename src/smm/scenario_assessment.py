"""Transparent necessary-condition screens; never a clearing/feasibility certificate.

All supply bounds are deliberately optimistic: transmission, startup, ramping,
reserve response and storage chronology can only make adequacy worse. A failed
bound proves a deficiency; a passed bound does not prove feasible dispatch.
"""
from __future__ import annotations

import numpy as np

STAGES = ('actual', 'da', 'id', 'rt')


def assess_boundaries(case, *, model_data=None):
    from .case import reserve_requirement
    from .fixed_load import tie_mode, tie_profiles
    from .scuc import prepare

    md = model_data or prepare(case)
    cfg, profiles = case.cfg, case.profiles
    hours = md.T * md.dt
    thermal_mw = sum(float(u['pmax']) for u in md.thermal.values())
    hydro_mw = sum(float(u['pmax']) for u in md.hydro_units.values())
    battery_mw = sum(float(u['pmax']) for u in md.storage.values())
    pumped_mw = sum(float(u['N'] * u['unit_gen_max']) for u in md.pumped.values())
    hydro_bounds = []
    for uid, h in md.hydro.items():
        cap = sum(float(md.hydro_units[j]['pmax']) for j in h['units'])
        limits = {'nameplate_mwh': cap * hours}
        if h.get('e_day') is not None:
            limits['declared_daily_mwh'] = float(h['e_day'])
        # Cascades receive upstream inflow. Omitting that constraint is an
        # optimistic bound; treating own inflow as total would be a false fail.
        if not h.get('upstream'):
            limits['water_terminal_mwh'] = max(0., (float(h['v0']) - float(h['v_end'])) /
                (.36 * float(h['water_rate'])) + float(h['qin']) * hours / float(h['water_rate']))
        hydro_bounds.append(dict(unit=uid, upper_mwh=min(limits.values()), limits=limits,
                                 upstream_water_bound_omitted=bool(h.get('upstream'))))
    hydro_energy = sum(row['upper_mwh'] for row in hydro_bounds)
    stock_bounds = []
    for uid, d in md.storage.items():
        terminal = float(d['soc_min'] if d['soc_end'] is None else d['soc_end'])
        upper = max(0., (float(d['soc0']) - terminal) * float(d['ecap']) * float(d['eta_d']))
        stock_bounds.append(dict(unit=uid, type='battery', net_energy_upper_mwh=upper,
                                 terminal_declared=d['soc_end'] is not None))
    for uid, d in md.pumped.items():
        terminal = float(d['v_lo'] if d['v_end'] is None else d['v_end'])
        upper = max(0., (float(d['init']['V']) - terminal) * float(d['e_gen']))
        stock_bounds.append(dict(unit=uid, type='pumped', net_energy_upper_mwh=upper,
                                 terminal_declared=d['v_end'] is not None))
    # Charging energy is not free primary supply. With unchanged terminal
    # inventory, storage contributes <=0 net MWh, despite its positive MW.
    stock_energy = sum(row['net_energy_upper_mwh'] for row in stock_bounds)
    dispatchable_mw = thermal_mw + hydro_mw + battery_mw + pumped_mw
    wind_mw = sum(float(d['cap']) for d in md.ren.values() if d.get('type') == 'wind')
    pv_mw = sum(float(d['cap']) for d in md.ren.values() if d.get('type') == 'pv')
    local_generation_mw = thermal_mw + hydro_mw + wind_mw + pv_mw
    tie_supply = sum((np.asarray(v[1], float) for v in case.tie.values()), start=np.zeros(md.T))
    fixed = [tie_profiles(cfg, profiles, u) for u in cfg.units.get('tie', [])] if tie_mode(cfg) == 'fixed_load' else []
    stages = []
    net_curves = {}
    for stage in STAGES:
        demand = profiles[f'load_{stage}'].to_numpy(float).copy()
        demand += sum((item[f"{u['id']}_{stage}"] for u, item in zip(cfg.units.get('tie', []), fixed)), start=np.zeros(md.T))
        ren = (sum((np.asarray(v, float) for v in case.ren_fc.values()), start=np.zeros(md.T)) if stage == 'da' else
               sum((profiles[f'{uid}_{stage}'].to_numpy(float) for uid in md.ren), start=np.zeros(md.T)))
        net = demand - ren - tie_supply
        net_curves[stage] = net
        rup, _, _ = reserve_requirement(cfg, demand, ren)
        # Every dispatchable MW is generously considered reserve-capable;
        # positive deficit still proves energy/reserve cannot both be served.
        reserve_capacity = dispatchable_mw + max(1., float(cfg.mode['reserve'].get('mu_RE', 0.))) * ren + tie_supply
        reserve_deficit = np.maximum(demand + rup - reserve_capacity, 0.)
        energy_upper = thermal_mw * hours + hydro_energy + ren.sum() * md.dt + tie_supply.sum() * md.dt + stock_energy
        instant_deficit = np.maximum(net - dispatchable_mw, 0.)
        stages.append(dict(stage=stage, demand_peak_mw=float(demand.max()), demand_mwh=float(demand.sum() * md.dt),
            renewable_mwh=float(ren.sum() * md.dt),
            renewable_available_to_demand_ratio=float(ren.sum()/demand.sum()) if demand.sum()>0 else None,
            net_load_peak_mw=float(net.max()),
            net_load_min_mw=float(net.min()), net_ramp_max_abs_mw=float(np.max(np.abs(np.diff(net)))) if len(net)>1 else 0.,
            optimistic_supply_energy_upper_mwh=float(energy_upper),
            unavoidable_energy_shortfall_lower_mwh=float(max(0., demand.sum() * md.dt - energy_upper)),
            instantaneous_shortfall_lower_mw=float(instant_deficit.max()),
            energy_or_up_reserve_shortfall_lower_mw=float(reserve_deficit.max()),
            energy_or_up_reserve_deficient_periods=int(np.count_nonzero(reserve_deficit > 1e-6))))
    failed = any(row[key] > 1e-6 for row in stages for key in
                 ('unavoidable_energy_shortfall_lower_mwh', 'instantaneous_shortfall_lower_mw', 'energy_or_up_reserve_shortfall_lower_mw'))
    return dict(schema=1, tier='pressure_required' if failed else 'unverified_candidate',
        label='压力场景：必要条件未满足' if failed else '常规候选：尚需全天出清验证',
        scope='optimistic_necessary_conditions_only_not_dispatch_or_probability',
        physical_feasibility_certified=False, horizon_hours=hours,
        capacities_mw=dict(thermal=thermal_mw, hydro=hydro_mw, battery=battery_mw, pumped=pumped_mw,
                           renewable=sum(float(d['cap']) for d in md.ren.values()), wind=wind_mw, pv=pv_mw),
        renewable_local_generation_nameplate_share=(wind_mw+pv_mw)/local_generation_mw if local_generation_mw>0 else None,
        nameplate_share_denominator='local_thermal_hydro_wind_pv_excludes_storage_and_external_tie',
        hydro_energy_bounds=hydro_bounds, storage_net_energy_bounds=stock_bounds, stages=stages,
        da_net_load_error_rmse_mw=float(np.sqrt(np.mean((net_curves['da'] - net_curves['actual'])**2))),
        caveats=['未考虑输电、机组启停/爬坡、备用资格与响应、储能逐段能量/抽发互斥，供给上界偏乐观。',
                 '任一阶段失败提示对应边界压力；ID/RT整日曲线不等于滚动首时段的实际执行结果。',
                 '丰枯水×新能源×负荷为合成敏感性设计，不代表季节联合概率或真实典型日。',
                 '常规场景须通过全天DA及96段RT，并分别核验切负荷、线路越限、备用和库存约束。'])


def combination_matrix(split_renewables=False):
    """Explicit 2×3×3 factorial coverage, including all crossed corners."""
    from .typical_scenarios import RENEWABLE_LEVELS, LOAD_LEVELS
    if split_renewables:
        return [dict(id=f'{season}_w{wind:g}_pv{pv:g}_load{load:g}', season=season,
                     renewable_multiplier=1., wind_multiplier=wind, pv_multiplier=pv, load_multiplier=load,
                     wind_level=wname, pv_level=pname, load_level=lname)
                for season in ('wet', 'dry')
                for wname, wind in RENEWABLE_LEVELS.items()
                for pname, pv in RENEWABLE_LEVELS.items()
                for lname, load in LOAD_LEVELS.items()]
    return [dict(id=f'{season}_re{renewable:g}_load{load:g}', season=season,
                 renewable_multiplier=renewable, load_multiplier=load,
                 renewable_level=rname, load_level=lname)
            for season in ('wet', 'dry')
            for rname, renewable in RENEWABLE_LEVELS.items()
            for lname, load in LOAD_LEVELS.items()]
