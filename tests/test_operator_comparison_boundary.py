"""Cross-scene comparison uses only saved quantities and clearly fails on damage."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from result_comparison import comparison_metrics, frozen_price_buses
from chinese_labels import column_label


def test_missing_saved_weighted_price_is_not_replaced_by_period_mean(tmp_path):
    result = dict(full=False, summary={}, da_summary={}, snapshot_root=str(tmp_path),
                  da_line_flows=None, da_nodal_prices=pd.DataFrame(dict(period=[0, 1], bus=[1, 1], lmp=[10., 30.], load_mw=[100., 200.])))
    assert frozen_price_buses(result) is None
    assert np.isnan(comparison_metrics(result)['日前负荷电量加权结算价 元/MWh'])
    for value in (None, 'invalid', float('inf'), float('nan')):
        result['da_summary']['lmp_load_weighted_avg'] = value
        assert np.isnan(comparison_metrics(result)['日前负荷电量加权结算价 元/MWh'])


@pytest.fixture
def evidence():
    root = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not root:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only 96-period archives')
    return Path(root)


def open_comparison(monkeypatch, tmp_path, records, loader):
    import common
    from smm import scenario
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    monkeypatch.setattr(common, 'all_results', lambda: records)
    monkeypatch.setattr(common, 'get_result', loader)
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/4_场景对比.py').run()
    return at


def test_real_comparison_missing_history_keeps_unknown_and_never_borrows_current(evidence, monkeypatch, tmp_path):
    import common
    paths = [evidence / n for n in ('final_c1f829a_baseline_full96', 'pv2152_dry_lowre_highload_full96')]
    results = [common.get_result(dict(dir=str(p), mode='full')) for p in paths]
    for result in results:
        result['snapshot_root'] = str(tmp_path / 'missing')
        for key in ('da_nodal_prices', 'rt_prices'):
            result[key] = result[key].drop(columns='load_mw', errors='ignore')
        result['da_summary'].pop('lmp_load_weighted_avg', None)
    records = [dict(dir=str(p), scenario=p.name, mode='full', status='done') for p in paths]
    at = open_comparison(monkeypatch, tmp_path, records,
                         lambda m: deepcopy(results[[str(p) for p in paths].index(m['dir'])]))
    assert not at.exception, [x.message for x in at.exception]
    assert any('不借用当前网络' in x.value for x in at.warning)
    metrics = at.dataframe[0].value
    row = metrics[metrics['指标'] == '日前负荷电量加权结算价 元/MWh'].iloc[0]
    assert all(pd.isna(row[column_label(k)]) for k in ('A', 'B', 'B−A'))


def test_real_comparison_damaged_selection_can_recover(evidence, monkeypatch, tmp_path):
    import common
    src = evidence / 'final_c1f829a_baseline_full96'
    intact = common.get_result(dict(dir=str(src), mode='full'))
    damaged = deepcopy(intact)
    damaged['rt_prices'] = damaged['rt_prices'].query('period != 40').copy()
    keys = ['damaged', 'intact', 'also_intact']
    records = [dict(dir=k, scenario=k, mode='full', status='done') for k in keys]
    at = open_comparison(monkeypatch, tmp_path, records,
                         lambda m: deepcopy(damaged if m['dir'] == 'damaged' else intact))
    assert not at.exception
    assert any('所选结果缺失或损坏' in x.value for x in at.error)
    assert not at.dataframe
    at.selectbox[0].set_value('also_intact').run()
    assert not at.exception, [x.message for x in at.exception]
    assert not at.error and at.dataframe


def test_real_debug_prefix_remains_readable_and_comparison_does_not_subtract_full_day(evidence, monkeypatch, tmp_path):
    import common
    prefix = os.environ.get('IEEE39_OPERATOR_PREFIX')
    if not prefix:
        pytest.skip('set IEEE39_OPERATOR_PREFIX to an existing actual DA96/RT8 run')
    paths = [evidence / 'final_c1f829a_baseline_full96', Path(prefix)]
    files = [p / n for p in paths for n in ('summary.json', 'rt_prices.csv', 'rt_units.csv')]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    original_get = common.get_result
    records = [dict(dir=str(p), scenario=p.name, mode='full', status='done') for p in paths]
    at = open_comparison(monkeypatch, tmp_path, records, original_get)
    assert not at.exception, [x.message for x in at.exception]
    assert any('不计算跨范围差值' in x.value for x in at.warning)
    table = at.dataframe[0].value
    quantity = table[table['指标'] == '负荷总支付 万元'].iloc[0]
    assert pd.notna(quantity[column_label('A')]) and pd.notna(quantity[column_label('B')])
    assert pd.isna(quantity[column_label('B−A')])
    at.switch_page('pages/3_结果.py').run()
    at.selectbox(key='_result_sel_widget').set_value(str(paths[1])).run()
    assert not at.exception, [x.message for x in at.exception]
    assert next(x.value for x in at.metric if x.label == '实时执行范围') == '8 / 96 段'
    assert any('8 时段' in x.value for x in at.info)
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def test_real_topology_first_and_last_slot_and_raw_export_filter(evidence):
    src = evidence / 'final_c1f829a_baseline_full96'
    files = [src / name for name in ('rt_prices.csv', 'rt_lines.csv', 'output_manifest.json')]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    code = f'''from common import get_result
from network_view import render_result_network
render_result_network(get_result({dict(dir=str(src), mode='full')!r}), prefix='real_network')
'''
    at = AppTest.from_string(code, default_timeout=90).run()
    for stage in ('da', 'rt'):
        at.radio(key='real_network:stage').set_value(stage).run()
        at.selectbox(key=f'real_network:{stage}:bus').set_value(3).run()
        at.selectbox(key=f'real_network:{stage}:branch:3').set_value('L2-3').run()
        prices = pd.read_csv(src / ('da/da_nodal_prices.csv' if stage == 'da' else 'rt_prices.csv'))
        lines = pd.read_csv(src / ('da/da_line_flows.csv' if stage == 'da' else 'rt_lines.csv'))
        for period in (0, 95):
            at.select_slider(key=f'real_network:period:{stage}').set_value(period).run()
            assert not at.exception, [x.message for x in at.exception]
            row = prices[(prices.period == period) & (prices.bus == 3)].iloc[0]
            assert next(x.value for x in at.metric if x.label == '本时段节点电价') == f'{row.lmp:,.2f} 元/MWh'
            branch = next(x.value for x in at.dataframe if '支路' in x.value).set_index('支路').loc['L2-3']
            saved = lines[(lines.period == period) & (lines.line == 'L2-3')].iloc[0]
            assert branch['潮流（MW）'] == f'{saved.flow_mw:,.2f}'
    out = AppTest.from_string(f"from output_browser import render_output_browser\nrender_output_browser({str(src)!r})", default_timeout=90).run()
    out.selectbox[0].set_value('rt_lines.csv').run()
    next(x for x in out.multiselect if x.label == '线路').set_value(['L2-3']).run()
    next(x for x in out.multiselect if x.label == '时段').set_value([0, 95]).run()
    assert not out.exception
    assert len(out.dataframe[0].value) == 2
    assert len(out.get('download_button')) == 3
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
