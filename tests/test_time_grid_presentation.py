"""Half-hour grids must not be mislabeled or accounted as quarter-hours."""
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from common import period_labels
from dispatch_detail import _period_label, dispatch_plan_frame
from final_views import _dt
from smm.time_grid import result_time_grid, result_time_axis
from smm.plots import _xt


def test_frozen_grid_beats_summary_and_rt_prefix(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/base.yaml').write_text('periods: 48\ndt_h: 0.5\n')
    result = {'snapshot_root': str(tmp_path), 'summary': {'expected_periods': 96},
              'rt_prices': pd.DataFrame({'period': [0, 1]})}
    assert result_time_grid(result) == (48, .5)
    assert _dt(result) == .5
    assert result_time_axis(result) == '时刻（每30分钟）'
    labels = period_labels(*result_time_grid(result))
    assert labels[4] == '02:00'
    assert labels[-1] == '23:30'


def test_legacy_and_explicit_saved_step():
    assert period_labels()[-1] == '23:45'
    assert result_time_grid({}) == (96, .25)
    assert result_time_grid({'summary': {'expected_periods': 48}}) == (48, .5)
    assert result_time_grid({'summary': {'expected_periods': 48, 'dt_h': .25}}) == (48, .25)


@pytest.mark.parametrize('dt', [0, -1, float('nan'), True])
def test_invalid_explicit_grid_is_not_silently_defaulted(dt):
    with pytest.raises(ValueError):
        result_time_grid({'summary': {'dt_h': dt}})


def test_dispatch_labels_and_energy_keep_saved_evidence():
    assert _period_label(4, .5) == '02:00（序号 4）'
    frame = pd.DataFrame({'P_mw': [10., -20.]})
    assert dispatch_plan_frame(frame, period_hours=.5).energy_mwh.tolist() == [5., -10.]
    frame['energy_mwh'] = [7., float('nan')]
    actual = dispatch_plan_frame(frame, period_hours=.5)
    assert actual.energy_mwh.iloc[0] == 7.
    assert pd.isna(actual.energy_mwh.iloc[1])


def test_truncated_plot_uses_step_not_prefix_length():
    fig, ax = plt.subplots()
    _xt(ax, 8, dt_h=.5)
    assert [x.get_text() for x in ax.get_xticklabels()] == ['00:00', '02:00', '04:00']
    plt.close(fig)


def test_half_hour_forecast_times():
    from smm.config import load_config
    from smm.forecast import generate_profiles
    cfg = load_config()
    cfg.base.update(periods=48, dt_h=.5)
    cfg.mode['realtime']['interval_min'] = 30
    frame = generate_profiles(cfg)
    assert len(frame) == 48
    assert frame.time.iloc[4] == '02:00'
    assert frame.time.iloc[-1] == '23:30'


def test_static_plot_receives_half_hour_grid(tmp_path, monkeypatch):
    from smm import plots
    seen = []
    original = plots._xt
    def capture(ax, T=96, dt_h=.25):
        original(ax, T, dt_h)
        seen.append((T, dt_h, [label.get_text() for label in ax.get_xticklabels()]))
    monkeypatch.setattr(plots, '_xt', capture)
    prices = pd.DataFrame({'period': range(48), 'bus': [1] * 48, 'lmp': [20.] * 48})
    plots.lmp_heatmap(prices, tmp_path / 'half_hour.png', dt_h=.5)
    assert seen[0][:2] == (48, .5)
    assert '02:00' in seen[0][2]
    assert '24:00' in seen[0][2]
    assert (tmp_path / 'half_hour.png').is_file()
