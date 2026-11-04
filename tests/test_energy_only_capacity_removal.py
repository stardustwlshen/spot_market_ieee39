"""Legacy non-energy payment fields never leak into current UI or cash accounting."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import sys

import pandas as pd
import yaml

from smm.bids import default_bids
from smm.config import load_config
from smm.profit import analyze
from test_operating_profit import sample

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from unit_input_view import field_rows, parameter_rows, unit_information_sections


def test_default_parameters_are_energy_only_but_physical_capacities_remain():
    cfg = load_config()
    assert all('capacity_price' not in unit for units in cfg.units.values()
               if isinstance(units, list) for unit in units)
    assert cfg.units['pumped'][0]['unit_gen_max'] > 0
    assert cfg.units['storage'][0]['ecap'] > 0
    assert cfg.mode['reserve']['storage_reserve'] is True
    assert cfg.base['line_capacity_multiplier'] > 0


def test_legacy_fields_hidden_without_modifying_scene_or_quote(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    path = tmp_path / 'config/units.yaml'
    data = yaml.safe_load(path.read_text())
    data['pumped'][0]['capacity_price'] = 99999
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    cfg = load_config(root=tmp_path)
    units_before = deepcopy(cfg.units)
    sections = unit_information_sections(cfg, cfg.units['pumped'][0]['id'])
    assert 'capacity_price' not in str(sections) and '99999' not in str(sections)
    assert cfg.units == units_before and path.read_bytes() == before
    quote = default_bids(cfg)
    cfg.units['pumped'][0].pop('capacity_price')
    assert default_bids(cfg) == quote
    legacy = {'pmax': 600, 'archive': {'capacity_price': 99999, 'capacity_fee_per_day': 100},
              'capacity_revenue_yuan': 200, 'capacity_payment_yuan': 300}
    unchanged = deepcopy(legacy)
    assert field_rows(legacy) == [{'参数': '最大功率（MW）', '数值': '600', '原始字段': 'pmax'}]
    assert len(parameter_rows(legacy)) == 1 and legacy == unchanged


def test_capacity_metadata_cannot_increase_cash_profit(tmp_path):
    result, _ = sample(tmp_path)
    before = analyze(result)
    for units in result['da'].case.cfg.units.values():
        for unit in units:
            unit.update(capacity_price=1e9, capacity_revenue_yuan=1e12)
    after = analyze(result)
    pd.testing.assert_frame_equal(before['by_unit'], after['by_unit'])
    assert before['summary'] == after['summary']
    assert not any('capacity' in column for column in after['by_period'])


def test_pumped_comparison_accepts_current_and_legacy_summaries_without_capacity_output():
    spec = importlib.util.spec_from_file_location('compare_pumped_energy', ROOT / 'scripts/compare_pumped.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = {
        'spread': dict(da_lw_avg=1, rt_lw_avg=2, da_lw_hourly=[1,2], rt_lw_hourly=[2,3],
                       da_lmp_max=2, da_lmp_min=1, rt_lmp_max=3, rt_lmp_min=2),
        'reconciliation': {}, 'da': dict(objective=1, gap=.01, curtail_mwh=0),
        'realtime': dict(curtail_mwh=0, shed_mwh=0),
        'running_hours': {stage: dict(thermal_unit_hours=1, thermal_starts=0,
            hydro_gen_hours={}, pumped_gen_hours=1, pumped_pump_hours=1) for stage in ('da','rt')},
        'pumped': {'PS1': dict(da=dict(gen_mwh=100,pump_mwh=120),
            rt=dict(gen_mwh=101,pump_mwh=119),energy_net_revenue=-50)}}
    current = module.metrics(summary)
    summary['pumped']['PS1']['capacity_fee_per_day'] = 1e10
    assert module.metrics(summary) == current
    assert current['PS1_电能量净收入_元'] == -50
    assert not any('容量电' in key or 'capacity' in key for key in current)
