"""Presentation time grid shared by exported figures and saved-result viewers."""
from pathlib import Path
import math


def period_label(period, dt_h=0.25):
    seconds = round(float(period) * float(dt_h) * 3600)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02d}:{minutes:02d}' + (f':{seconds:02d}' if seconds else '')


def result_time_grid(result):
    """Use frozen inputs, never current config or an executed RT prefix length."""
    base = {}
    if result.get('snapshot_root'):
        path = Path(result['snapshot_root']) / 'config/base.yaml'
        if path.exists():
            import yaml
            base = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    summary = result.get('summary') or {}
    n = base.get('periods', summary.get('expected_periods', 96))
    if isinstance(n, bool) or not math.isfinite(float(n)) or float(n) <= 0 or not float(n).is_integer():
        raise ValueError('保存的时段数无效')
    dt = base.get('dt_h', summary.get('dt_h', 24 / float(n)))
    if isinstance(dt, bool) or not math.isfinite(float(dt)) or float(dt) <= 0:
        raise ValueError('保存的时间步长无效')
    return int(n), float(dt)


def result_time_axis(result):
    return f'时刻（每{result_time_grid(result)[1] * 60:g}分钟）'
