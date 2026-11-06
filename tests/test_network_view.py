"""Network-view data provenance, missing values, time alignment and interaction."""
from pathlib import Path
import copy
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from network_view import (network_data, recorded_periods, result_snapshot,
                          topology_figure, resource_table, branch_table)
from smm.config import load_config
from smm.network import build_network


def fixture_result():
    return {'da_system': pd.DataFrame({'period': [0, 4], 'load_mw': [1000., 4000.]}),
            'da_unit_awards': pd.DataFrame({'period': [0, 4], 'unit': ['ES1', 'ES1'],
                                          'bus': [19, 19], 'type': ['storage', 'storage'], 'P_mw': [-42., 120.]}),
            'da_nodal_prices': pd.DataFrame({'period': [0, 4], 'bus': [19, 19], 'lmp': [30., 45.]}),
            'da_line_flows': pd.DataFrame({'period': [0, 4], 'line': ['L2-3', 'L2-3'],
                                          'flow_mw': [-123., 222.], 'rate_mw': [700., 700.]}),
            'rt_prices': pd.DataFrame({'period': [0, 3], 'bus': [19, 19], 'load_mw': [12., 99.],
                                      'lmp': [39., 48.], 'shed_mw': [0., 2.]}),
            'rt_units': pd.DataFrame({'period': [0], 'unit': ['ES1'], 'bus': [19],
                                     'type': ['storage'], 'P_mw': [-17.]}),
            'rt_lines': pd.DataFrame({'period': [0], 'line': ['L2-3'], 'flow_mw': [0.]})}


def test_current_network_matches_model_and_all_resources():
    cfg = load_config()
    data = network_data(ROOT)
    expected = build_network(cfg)
    assert len(data['buses']) == 39
    assert len(data['branches']) == 46
    assert data['branches'].kind.value_counts().to_dict() == {'line': 35, 'trafo': 11}
    pd.testing.assert_series_equal(data['branches'].rate_eff, expected.branches.rate_mw, check_names=False)
    resources = data['resources'].set_index('unit')
    assert not resources.loc['H2', 'enabled']
    assert resources.loc['PS1', 'capacity_mw'] == 600
    assert resources.loc['ES1', 'capacity_mw'] == 100
    groups = ('thermal','renewable','hydro','storage','pumped') + (('tie',) if cfg.base.get('tie_line_mode', 'supply') == 'supply' else ())
    assert set(resources[resources.enabled].index) == {u['id'] for group in groups for u in cfg.units[group]}
    assert resources.loc['PV2', 'capacity_mw'] == resources.loc['PV3', 'capacity_mw'] == 750


def test_da_load_is_period_total_times_frozen_share_not_base_load():
    data = network_data(ROOT, base={**load_config().base, 'tie_line_mode': 'supply'})
    original = copy.deepcopy(data)
    snapshot = result_snapshot(data, fixture_result(), 'da', 4)
    buses = snapshot['buses']
    assert buses.load_mw.sum() == pytest.approx(4000)
    assert buses.loc[buses.bus == 3, 'load_mw'].iloc[0] == pytest.approx(4000 * 322 / buses.base_load_mw.sum())
    assert not buses.load_mw.equals(buses.base_load_mw)
    assert snapshot['resources'].set_index('unit').loc['ES1', 'P_mw'] == 120
    for key in ('buses', 'branches', 'resources'):
        pd.testing.assert_frame_equal(data[key], original[key])


def test_rt_never_fills_missing_period_or_resource_with_zero_or_da():
    result = fixture_result()
    data = network_data(ROOT)
    snapshot = result_snapshot(data, result, 'rt', 3)
    buses = snapshot['buses'].set_index('bus')
    assert buses.loc[19, 'load_mw'] == 99
    assert buses.loc[19, 'shed_mw'] == 2
    assert np.isnan(buses.loc[3, 'load_mw'])
    assert snapshot['resources'].P_mw.isna().all()
    assert snapshot['branches'].flow_mw.isna().all()
    assert result_snapshot(data, result, 'da', 2)['buses'].load_mw.isna().all()
    assert recorded_periods({'prices': result['rt_prices'], 'units': result['rt_units']}) == [0, 3]


def test_saved_flow_limit_and_signed_power_are_preserved():
    data = result_snapshot(network_data(ROOT), fixture_result(), 'rt', 0)
    es1 = data['resources'].set_index('unit').loc['ES1']
    line = data['branches'].set_index('branch_id').loc['L2-3']
    assert es1.P_mw == -17
    assert line.flow_mw == 0
    assert line.loading == 0
    assert line.rate_eff == 700  # same-run saved DA rating, not present-day defaults
    shown = resource_table(data['resources'], True).set_index('机组 / 主体')
    assert shown.loc['ES1', '本时段净出力（MW）'] == '-17.00'
    assert shown.loc['C1', '本时段净出力（MW）'] == '未知'
    assert shown.loc['H2', '本时段净出力（MW）'] == '不参与运行'
    assert branch_table(data['branches'], True).set_index('支路').loc['L2-3', '潮流（MW）'] == '0.00'


def test_missing_frozen_base_and_units_do_not_borrow_current(tmp_path):
    case = tmp_path / 'data/case39'; case.mkdir(parents=True)
    for file in ('buses.csv', 'branches.csv'):
        (case/file).write_bytes((ROOT/'data/case39'/file).read_bytes())
    data = network_data(tmp_path)
    assert not data['resource_catalog_known']
    assert data['resources'].empty
    assert data['branches'].rate_eff.isna().all()
    result = result_snapshot(data, fixture_result(), 'rt', 0)
    assert result['resources'].unit.tolist() == ['ES1']  # only actually recorded resource


def test_duplicate_records_are_rejected_not_summed():
    result = fixture_result()
    result['rt_units'] = pd.concat([result['rt_units']]*2)
    with pytest.raises(ValueError, match='记录重复'):
        result_snapshot(network_data(ROOT), result, 'rt', 0)


def test_saved_unit_bus_must_match_frozen_connection():
    result = fixture_result()
    result['rt_units'].loc[0, 'bus'] = 20
    with pytest.raises(ValueError, match='冻结配置冲突'):
        result_snapshot(network_data(ROOT), result, 'rt', 0)


def test_figure_stacks_resources_and_distinguishes_transformers():
    data = network_data(ROOT)
    row = data['resources'].iloc[0].copy()
    row['unit'] = 'same_bus_test'
    data['resources'] = pd.concat([data['resources'], row.to_frame().T], ignore_index=True)
    fig = topology_figure(data, 31, 'T6-31')
    labels = [a.text for a in fig.layout.annotations]
    assert any('煤 C1<br>煤 same_bus_test' in text for text in labels)
    assert sum(trace.line.dash == 'dash' for trace in fig.data if trace.mode == 'lines') == 11
    runtime = result_snapshot(data, fixture_result(), 'da', 0)
    figure = topology_figure(runtime)
    arrow = next(a for a in figure.layout.annotations if a.showarrow)
    assert arrow.xref == 'x' and arrow.axref == 'x'


def test_app_interactions_switch_bus_branch_stage_and_sparse_periods(tmp_path):
    from streamlit.testing.v1 import AppTest
    script = f'''
from pathlib import Path
import sys
sys.path[:0] = [{str(ROOT/'app')!r}, {str(ROOT/'src')!r}, {str(ROOT/'tests')!r}]
from test_network_view import fixture_result
from network_view import render_result_network
r = fixture_result()
r['snapshot_root'] = {str(ROOT)!r}
render_result_network(r, prefix='test')
'''
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception
    at.selectbox(key='test:da:bus').set_value(19).run()
    at.select_slider(key='test:period:da').set_value(0).run()
    assert not at.exception
    assert any('-42.00' in str(table.value) for table in at.dataframe)
    at.radio(key='test:stage').set_value('rt').run()
    assert at.select_slider(key='test:period:rt').options == ['00:00 · 第1段', '00:45 · 第4段']
    at.selectbox(key='test:rt:bus').set_value(19).run()
    assert any(metric.value == '99.00 MW' for metric in at.metric)
    assert any('未知' in str(table.value) for table in at.dataframe)
    at.select_slider(key='test:period:rt').set_value(0).run()
    assert any('-17.00' in str(table.value) for table in at.dataframe)
    at.radio(key='test:stage').set_value('da').run()
    assert not at.exception
    assert any('120.00' in str(table.value) for table in at.dataframe)  # stage return resets to latest saved slot


@pytest.mark.parametrize("missing", ["missing", "empty"])
def test_app_missing_snapshot_explains_without_drawing(tmp_path, missing):
    from streamlit.testing.v1 import AppTest
    script = f'''
import sys
sys.path[:0] = [{str(ROOT/'app')!r}, {str(ROOT/'src')!r}]
from network_view import render_result_network
render_result_network({{'snapshot_root': {str(tmp_path) if missing == 'missing' else ''!r}}})
'''
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert '缺少冻结网络' in at.info[0].value
    assert not at.selectbox
