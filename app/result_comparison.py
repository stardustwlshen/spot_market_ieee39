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
