"""Same-scope business comparisons from saved quantities and price evidence."""
from __future__ import annotations
import numpy as np
import pandas as pd
from smm.time_grid import result_time_grid

ORDER = ['tie', 'coal', 'hydro', 'gas_ccgt', 'gas_gt', 'wind', 'pv',
         'storage_dis', 'pumped_gen', 'storage_ch', 'pumped_pump']


def type_energy_comparison(result):
    """Compare signed energy on identical periods; missing evidence stays NaN."""
    _, dt = result_time_grid(result)
    da = result.get('da_unit_awards')
    rt = result.get('rt_units')
    da = da if isinstance(da, pd.DataFrame) else pd.DataFrame()
    rt = rt if isinstance(rt, pd.DataFrame) else pd.DataFrame()
    periods = sorted(rt.period.unique()) if not rt.empty else sorted(da.period.unique()) if not da.empty else []
    expected_units = {}
    for data in (da, rt):
        if not data.empty and {'unit', 'type'} <= set(data):
            for kind, group in data.groupby('type'):
                expected_units.setdefault(kind, set()).update(group.unit.dropna())
    totals = {}
    for stage, data in [('日前电量 MWh', da), ('实时电量 MWh', rt)]:
        values = {}
        if not data.empty and {'period', 'unit', 'type', 'P_mw'} <= set(data):
            data = data[data.period.isin(periods)].copy()
            if data.duplicated(['period', 'unit']).any():
                raise ValueError('分类型电量存在重复机组时段记录')
            for kind, group in data.groupby('type'):
                required = len(periods) * len(expected_units.get(kind, set()))
                power = pd.to_numeric(group.P_mw, errors='coerce')
                valid = (len(group) == required and set(group.unit) == expected_units.get(kind)
                         and set(group.period) == set(periods) and np.isfinite(power).all())
                if kind in ('storage', 'pumped'):
                    pos, neg = ('storage_dis', 'storage_ch') if kind == 'storage' else ('pumped_gen', 'pumped_pump')
                    values[pos] = power.clip(lower=0).sum() * dt if valid else np.nan
                    values[neg] = power.clip(upper=0).sum() * dt if valid else np.nan
                else:
                    values[kind] = power.sum() * dt if valid else np.nan
        totals[stage] = pd.Series(values, dtype=float)
    table = pd.DataFrame(totals)
    table['实时−日前 MWh'] = table['实时电量 MWh'] - table['日前电量 MWh']
    table = table.reindex([k for k in ORDER if k in table.index] + sorted(set(table.index) - set(ORDER)))
    table.index.name = 'type'
    table.attrs['periods'] = periods
    table.attrs['period_hours'] = dt
    return table.reset_index()


def reserve_price_comparison(result, buses=None):
    """Actual shortage intervals only; separate dispatch, pricing, settlement."""
    from common import load_weighted
    from unit_overview import saved_price_schema
    system = result.get('rt_reserve_system')
    if not isinstance(system, pd.DataFrame) or system.empty:
        system = result.get('rt_system')
    if not isinstance(system, pd.DataFrame) or not {'period', 'R_up_short'} <= set(system):
        return pd.DataFrame()
    shortage = pd.to_numeric(system.R_up_short, errors='coerce')
    rows = system.loc[shortage.gt(1e-6), ['period', 'R_up_short']].copy()
    if rows.empty:
        return rows
    if rows.period.duplicated().any():
        raise ValueError('实时备用缺额记录时段重复')
    rows = rows.rename(columns={'R_up_short': '实时上备用缺额 MW'})
    for stage, key in [('日前', 'da_nodal_prices'), ('实时', 'rt_prices')]:
        prices = result.get(key)
        if not isinstance(prices, pd.DataFrame) or prices.empty:
            continue
        selected, _, _, known = saved_price_schema(prices, 'da' if stage == '日前' else 'rt')
        fields = [(selected, stage + ('结算价' if known else '保存价（口径未核实）'))]
        if 'lmp_raw' in prices:
            source = set(prices.pricing_source.dropna()) if 'pricing_source' in prices else set()
            label = ('辅助定价原始价' if source == {'bounded_auxiliary_pricing'} else '所选定价原始价')
            fields.append(('lmp_raw', stage + label))
        if 'dispatch_raw_lmp' in prices:
            fields.append(('dispatch_raw_lmp', stage + '调度对偶原始价'))
        for field, label in fields:
            view = prices[['period', 'bus', field] + (['load_mw'] if 'load_mw' in prices else [])].rename(columns={field: 'lmp'})
            values = load_weighted(view, buses)
            rows[label + '（节点负荷加权）元/MWh'] = rows.period.map(values)
    _, dt = result_time_grid(result)
    from common import period_labels
    n, _ = result_time_grid(result)
    labels = period_labels(n, dt)
    rows.insert(0, '时刻', rows.period.map(lambda t: labels[int(t)]))
    return rows.drop(columns='period').reset_index(drop=True)


"""Read-only comparison metrics from each archive, with no current-input fallback."""
from pathlib import Path

import numpy as np
import pandas as pd

from common import line_loading, congestion_period_count


def frozen_price_buses(result):
    path = Path(result['snapshot_root']) / 'data/case39/buses.csv'
    return pd.read_csv(path) if path.is_file() else None


def saved_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return value if np.isfinite(value) else np.nan


def comparison_metrics(result):
    summary, da = result['summary'], result['da_summary']
    loading = line_loading(result, 'da')
    metrics = {
        '日前调度目标（含罚） 万元': saved_number(da.get('objective')) / 1e4,
        '启动费 万元': saved_number(da.get('startup_cost')) / 1e4,
        '日前负荷电量加权结算价 元/MWh': saved_number(da.get('lmp_load_weighted_avg')),
        '日前节点电价最高': saved_number(da.get('lmp_max')),
        '日前节点电价最低': saved_number(da.get('lmp_min')),
        '日前弃电 MWh': saved_number(da.get('curtailment_mwh')),
        '日前阻塞时段数': congestion_period_count(loading),
    }
    if result['full']:
        spread = summary.get('spread') or {}
        loading = line_loading(result, 'rt')
        metrics.update({
            '实时负荷电量加权结算价 元/MWh': saved_number(spread.get('rt_lw_avg')),
            '逐时价差算术均值 元/MWh': saved_number(spread.get('lw_spread_mean')),
            '逐时价差绝对值算术均值 元/MWh': saved_number(spread.get('lw_spread_abs_mean')),
            '实时弃电 MWh': saved_number((summary.get('realtime') or {}).get('curtail_mwh')),
            '实时阻塞时段数': congestion_period_count(loading),
            '负荷+新能源偏差损益 万元': saved_number((summary.get('forecast_error') or {}).get('dev_pnl')) / 1e4,
            '负荷总支付 万元': -saved_number(((summary.get('settlement_by_type') or {}).get('load') or {}).get('total')) / 1e4,
            '实时时段数': result['rt_prices'].period.nunique(),
        })
    return metrics
