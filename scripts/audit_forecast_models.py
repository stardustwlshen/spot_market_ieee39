"""Reproduce generator-only comparisons. Does NOT solve or alter a scenario."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from smm.config import load_config
from smm.forecast import generate_profiles


def audit(root, output, seeds=128, start_seed=20260715):
    cfg = load_config(root=root)
    records = []
    example = {}
    for model in ('legacy_v1', 'multiscale_v2'):
        scenario = copy.deepcopy(cfg)
        scenario.base['forecast'] = {**(scenario.base.get('forecast') or {}), 'model': model}
        for seed in range(start_seed, start_seed + seeds):
            profiles = generate_profiles(scenario, seed)
            if seed == start_seed:
                example[model] = profiles
            for unit in scenario.units['renewable']:
                uid, cap = unit['id'], float(unit['cap'])
                actual = profiles[f'{uid}_actual'].to_numpy()
                for stage in ('actual', 'da', 'id', 'rt'):
                    values = profiles[f'{uid}_{stage}'].to_numpy()
                    error = (values - actual) / cap
                    delta = np.diff(values) / cap
                    records.append(dict(model=model, seed=seed, unit=uid, stage=stage,
                        mse_by_cap=float(np.mean(error ** 2)), nrmse_by_cap=float(np.sqrt(np.mean(error ** 2))),
                        mean_abs_step_mw=float(np.mean(abs(delta)) * cap), max_abs_step_mw=float(np.max(abs(delta)) * cap),
                        rms_second_difference_mw=float(np.sqrt(np.mean(np.diff(values, n=2) ** 2))),
                        boundary_fraction=float(np.mean((values == 0.) | (values == cap)))))
    raw = pd.DataFrame(records)
    groups = raw.groupby(['model', 'unit', 'stage'], sort=False)
    summary = groups.agg(mean_daily_nrmse=('nrmse_by_cap', 'mean'), pooled_mse=('mse_by_cap', 'mean'),
        mean_abs_15min_change_mw=('mean_abs_step_mw', 'mean'), mean_daily_max_15min_change_mw=('max_abs_step_mw', 'mean'),
        mean_second_difference_rms_mw=('rms_second_difference_mw', 'mean'), boundary_fraction=('boundary_fraction', 'mean')).reset_index()
    summary['pooled_nrmse'] = np.sqrt(summary.pop('pooled_mse'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    raw.to_csv(output / 'per_seed_metrics.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    for model, profiles in example.items():
        profiles.to_csv(output / f'example_{model}.csv', index=False)
    (output / 'scope.json').write_text(json.dumps(dict(seed_count=seeds, start_seed=start_seed,
        last_seed=start_seed + seeds - 1, base_root=str(Path(root).resolve()),
        scope='Generated synthetic inputs only; no overrides, bids or market clearing used.',
        limits=['Not a real-data forecasting benchmark.', 'Population sigma is not an enforced daily nRMSE.',
                'V2 stages share a nested error path; perfect cross-stage correlation holds before clipping.',
                'Both models construct joint offline scenarios conditional on simulated truth.']), indent=2) + '\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, sharey=True, constrained_layout=True)
    cap = next(float(unit['cap']) for unit in cfg.units['renewable'] if unit['id'] == 'W1')
    for ax, model in zip(axes, ('legacy_v1', 'multiscale_v2')):
        p = example[model]
        hours = np.arange(len(p)) * cfg.dt
        ax.plot(hours, p.W1_actual, label='Simulated actual availability', color='#b45309', linewidth=1.6)
        ax.plot(hours, p.W1_da, label='Day-ahead availability forecast', color='#2563eb', linewidth=1.6)
        ax.set_title(model + f' | W1 | seed {start_seed}')
        ax.set_ylabel('MW')
        ax.set_ylim(0, cap)
        ax.grid(alpha=.2)
        ax.legend(loc='upper right')
    axes[-1].set_xlabel('Hour (15-minute inputs, not dispatch results)')
    fig.savefig(output / 'W1_before_after.png', dpi=150)
    plt.close(fig)
    print(summary.to_string(index=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True, help='A NEW output directory, never a run directory')
    parser.add_argument('--seeds', type=int, default=128)
    parser.add_argument('--start-seed', type=int, default=20260715)
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error('--seeds must be positive')
    audit(args.root, args.output, args.seeds, args.start_seed)
