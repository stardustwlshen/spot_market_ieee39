"""Read-only UI-level archive cross-check, not a new solve or full safety audit."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

root = Path(sys.argv[1])
names = ['final_c1f829a_baseline_full96', 'final_c1f829a_dry_nominal_full96',
         'pv2152_dry_lowre_highload_full96', 'pv2152_wet_highre_lowload_full96']
report = []
for name in names:
    path = root / name
    summary = json.loads((path / 'summary.json').read_text())
    units = pd.read_csv(path / 'rt_units.csv')
    system = pd.read_csv(path / 'rt_system.csv').set_index('period')
    reserve = pd.read_csv(path / 'rt_reserve_system.csv')
    lines = pd.read_csv(path / 'rt_lines.csv')
    profits = pd.read_csv(path / 'operating_profit_by_unit.csv')
    assert sorted(system.index) == list(range(96))
    assert units.groupby('unit').period.nunique().eq(96).all()
    assert not units.duplicated(['period', 'unit']).any()
    assert lines.groupby('line').period.nunique().eq(96).all()
    balance = units.groupby('period').P_mw.sum() - system.load_mw + system.shed_mw - system.surplus_mw
    assert balance.abs().max() < .001  # CSVs retain four decimal places.
    anchors = {}
    for basename, column in [('storage', 'E_mwh'), ('pumped', 'V_up')]:
        da = pd.read_csv(path / 'da' / f'da_{basename}.csv')
        rt = pd.read_csv(path / f'rt_{basename}.csv')
        joined = da[['unit', 'period', column]].merge(rt[['unit', 'period', column]],
                    on=['unit', 'period'], suffixes=('_da', '_rt'), validate='one_to_one')
        selected = joined[(joined.period + 1) % 8 == 0]
        anchors[basename] = float((selected[column + '_da'] - selected[column + '_rt']).abs().max())
        assert anchors[basename] <= .0001
    detail = pd.read_csv(path / 'settlement_detail.csv')
    money_error = float((detail.da_amount + detail.rt_amount - detail.total).abs().max())
    assert money_error <= .00011
    relevant = ['summary.json', 'rt_units.csv', 'rt_system.csv', 'rt_lines.csv', 'rt_reserve_system.csv',
                'rt_storage.csv', 'rt_pumped.csv', 'settlement_detail.csv', 'operating_profit_by_unit.csv']
    report.append(dict(archive=name, executed_periods=len(system), units=int(units.unit.nunique()),
        lines=int(lines.line.nunique()), balance_max_abs_mw=float(balance.abs().max()),
        reserve_short_periods=int((reserve.R_up_short > 1e-5).sum()),
        reserve_short_max_mw=float(reserve.R_up_short.max()),
        reserve_short_integral_mw_h=float(reserve.R_up_short.sum() * .25),
        shed_mwh=float(system.shed_mw.sum() * .25),
        line_overload_max_mw=float((lines.flow_mw.abs()-lines.rate_mw).clip(lower=0).max()),
        curtailed_mwh=float(units.curtail_mw.sum() * .25),
        storage_anchor_max_err_mwh=anchors['storage'], pumped_anchor_max_err_1e4m3=anchors['pumped'],
        settlement_addition_max_error_yuan=money_error,
        loss_making_local_units=int((profits.simulated_operating_profit_yuan < 0).sum()),
        actual_da_gap=summary['da_summary']['scuc_gap'],
        da_energy_weighted_price=summary['spread']['da_lw_avg'],
        rt_energy_weighted_price=summary['spread']['rt_lw_avg'],
        arithmetic_period_spread=summary['spread']['lw_spread_mean'],
        sha256={name: hashlib.sha256((path/name).read_bytes()).hexdigest() for name in relevant}))
print(json.dumps({'scope':'Read-only four-decimal archived CSV and summary checks; no new optimization, AC/N-1 or platform certification.',
                  'cases':report}, ensure_ascii=False, indent=2))
