"""Operator-facing selection and supply labels, without rerunning optimization."""
import hashlib
import os
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))


def test_repeated_result_selection_does_not_revert_or_require_double_click():
    records = [dict(dir=name, scenario=name, mode='full', status='done') for name in ('A', 'B', 'C', 'D')]
    code = f"from result_navigation import select_saved_result\nselect_saved_result({records!r})"
    at = AppTest.from_string(code, default_timeout=30).run()
    for name in ('B', 'C', 'D', 'A', 'C', 'A'):
        at.selectbox[0].set_value(name).run()
        assert not at.exception
        assert at.selectbox[0].value == name
        assert at.session_state['result_sel'] == name
        at.run()  # unrelated rerun/refresh must keep the selected run
        assert at.selectbox[0].value == name


def render_system(rows):
    tables = dict(units=rows, system=[dict(period=1, load_mw=100)])
    code = f"from market_views import system_metrics\nsystem_metrics({tables!r})"
    return AppTest.from_string(code, default_timeout=30).run()


def test_supply_card_labels_archived_tie_once():
    at = render_system([dict(period=1, unit='G1', type='coal', P_mw=70),
                        dict(period=1, unit='TIE', type='tie', P_mw=40),
                        dict(period=1, unit='ES1', type='storage', P_mw=-10)])
    assert not at.exception
    metric = next(x for x in at.metric if x.label == '已保存净供给（含来电） MW')
    assert metric.value == '100.00'
    assert any('不能再次相加' in x.value for x in at.caption)
    assert not any('另在边界计划中' in x.value for x in at.caption)


def test_missing_tie_is_not_claimed_zero_or_complete():
    at = render_system([dict(period=1, unit='G1', type='coal', P_mw=70)])
    assert not at.exception
    assert any(x.label == '已保存主体净出力 MW' for x in at.metric)
    assert any('不能据此推断来电为零' in x.value for x in at.caption)


@pytest.mark.parametrize('rows', [
    [dict(period=1, unit='G1', type='coal', P_mw=70), dict(period=1, unit='G2', type='coal', P_mw=None)],
    [dict(period=0, unit='G2', type='coal', P_mw=30), dict(period=1, unit='G1', type='coal', P_mw=70)],
])
def test_incomplete_saved_power_is_not_a_reported_total(rows):
    at = render_system(rows)
    assert not at.exception
    assert next(x for x in at.metric if '净出力' in x.label).value == '未记录完整'


def test_real_run_switches_keep_quality_alerts_and_survive_page_reentry(monkeypatch, tmp_path):
    """Optional real archived evidence, never substitute a fixture for a solver run."""
    evidence = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only 96-period archives')
    evidence = Path(evidence)
    names = ['final_c1f829a_baseline_full96', 'final_c1f829a_dry_nominal_full96',
             'pv2152_dry_lowre_highload_full96', 'pv2152_wet_highre_lowload_full96']
    paths = [evidence / name for name in names]
    assert all((path / 'summary.json').is_file() for path in paths)
    watched = [file for path in paths for file in path.rglob('*') if file.is_file()]
    before = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in watched}
    import common
    from smm import scenario
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    records = [dict(dir=str(path), scenario=path.name, mode='full', status='done') for path in paths]
    monkeypatch.setattr(common, 'all_results', lambda: records)
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    for index in (1, 2, 3, 0, 2):
        target = str(paths[index])
        at.selectbox[0].set_value(target).run()
        assert not at.exception, [x.message for x in at.exception]
        assert at.selectbox[0].value == target
        if index == 2:
            assert any('1.979%' in x.value for x in at.warning)
            assert any('237.2283 MW' in x.value for x in at.error)
        else:
            assert not any('237.2283 MW' in x.value for x in at.error)
    at.switch_page('pages/2_运行.py').run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert at.selectbox[0].value == str(paths[2])
    assert any('237.2283 MW' in x.value for x in at.error)
    # An explicit request from the run page wins over an older remembered run.
    at.switch_page('pages/2_运行.py').run()
    at.session_state['result_sel'] = str(paths[0])
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert at.selectbox[0].value == str(paths[0])
    assert not any('237.2283 MW' in x.value for x in at.error)
    assert before == {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in watched}


def test_real_da_price_and_missing_rt_tie_are_not_fabricated(monkeypatch, tmp_path):
    evidence = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only 96-period archives')
    import common
    import json
    from smm import scenario
    path = Path(evidence) / 'final_c1f829a_baseline_full96'
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    meta = dict(dir=str(path / 'da'), scenario='真实日前存档', mode='da', status='done')
    monkeypatch.setattr(common, 'all_results', lambda: [meta])
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    expected = json.loads((path / 'da/summary.json').read_text())['lmp_load_weighted_avg']
    actual = next(x.value for x in at.metric if x.label == '日前负荷电量加权结算价 · 元/MWh')
    assert actual == f'{expected:.1f}' == '187.3'
    assert any('仅日前计划；未包含实时执行' in x.value for x in at.markdown)
    assert not any(x.label == '实时 发电 / 抽水（MWh）' for x in at.metric)

    # A controlled missing-record case derived from the real result. No archive
    # is edited and this is not claimed to be a new physical simulation.
    original = common.get_result(dict(dir=str(path)))
    missing = dict(original, rt_units=original['rt_units'].query("type != 'tie'").copy())
    meta.update(dir=str(path), scenario='历史来电缺行模拟', mode='full')
    monkeypatch.setattr(common, 'get_result', lambda _meta: missing)
    at.run()
    at.radio(key='gen_stage').set_value('实时').run()
    assert not at.exception
    assert any('不用日前计划补作实时执行量' in x.value for x in at.warning)
    chart = next(json.loads(x.proto.spec) for x in at.get('plotly_chart')
                 if '分类型出力堆叠' in json.loads(x.proto.spec).get('layout', {}).get('title', {}).get('text', ''))
    assert not any(trace.get('name') == '区外来电' for trace in chart['data'])
    missing['summary'] = {**original['summary'], 'spread': {'da_lw_avg': float('nan')}}
    at.run()
    assert not at.exception
    assert next(x.value for x in at.metric if x.label == '日前 / 实时负荷电量加权价 · 元/MWh') == '未记录 / 未记录'
    assert next(x.value for x in at.metric if x.label == '逐时价差算术均值（元/MWh）') == '未记录'



@pytest.mark.parametrize('damage', ['bad_json', 'missing_prices', 'bad_csv', 'missing_column',
                                  'lmp_string', 'lmp_nan', 'lmp_inf', 'P_mw_string', 'P_mw_nan', 'P_mw_inf'])
def test_damaged_result_can_switch_to_intact_archive(monkeypatch, tmp_path, damage):
    evidence = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only 96-period archives')
    import shutil
    import common
    from smm import scenario
    intact = Path(evidence) / 'final_c1f829a_baseline_full96'
    damaged = tmp_path / 'damaged'
    damaged.mkdir()
    # Only the broken copy is edited. Test data need not duplicate large diagnostics.
    shutil.copy2(intact / 'summary.json', damaged / 'summary.json')
    for filename in ('da_nodal_prices.csv', 'da_unit_awards.csv'):
        shutil.copy2(intact / filename, damaged / filename)
    if damage == 'bad_json':
        (damaged / 'summary.json').write_text('{broken')
    elif damage == 'missing_prices':
        (damaged / 'da_nodal_prices.csv').unlink()
    elif damage == 'bad_csv':
        (damaged / 'da_nodal_prices.csv').write_text('period,bus,lmp\n0,1,"unclosed')
    elif damage == 'missing_column':
        (damaged / 'da_nodal_prices.csv').write_text('period,bus\n0,1\n')
    else:
        import pandas as pd
        field, kind = damage.rsplit('_', 1)
        filename = 'da_nodal_prices.csv' if field == 'lmp' else 'da_unit_awards.csv'
        frame = pd.read_csv(damaged / filename)
        frame[field] = frame[field].astype(object)
        frame.loc[0, field] = {'string': 'not a number', 'nan': float('nan'), 'inf': float('inf')}[kind]
        frame.to_csv(damaged / filename, index=False)
    before = {file.name: file.read_bytes() for file in damaged.iterdir()}
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    records = [dict(dir=str(path), scenario=path.name, mode='full', status='done') for path in (damaged, intact)]
    monkeypatch.setattr(common, 'all_results', lambda: records)
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    assert any('本次结果缺失或损坏' in x.value for x in at.error)
    assert at.selectbox[0].value == str(damaged)
    at.selectbox[0].set_value(str(intact)).run()
    assert not at.exception, [x.message for x in at.exception]
    assert at.selectbox[0].value == str(intact)
    assert not any('本次结果缺失或损坏' in x.value for x in at.error)
    assert before == {file.name: file.read_bytes() for file in damaged.iterdir()}


def test_price_weighting_does_not_guess_missing_load_or_zero_demand():
    import pandas as pd
    from common import load_weighted
    from result_navigation import recorded_number
    prices = pd.DataFrame({'period': [0, 0, 1, 1], 'bus': [1, 2, 1, 2], 'lmp': [10., 30., 10., 30.]})
    assert load_weighted(prices).isna().all()
    assert load_weighted(prices, pd.DataFrame({'bus': [1], 'load_mw': [1.]})).isna().all()
    zero = prices.assign(load_mw=0.)
    assert load_weighted(zero).isna().all()
    known = prices.assign(load_mw=[1., 3., 3., 1.])
    assert load_weighted(known).tolist() == [25., 15.]
    known.loc[1, 'load_mw'] = float('nan')
    assert pd.isna(load_weighted(known).iloc[0])
    assert load_weighted(known).iloc[1] == 15.
    assert recorded_number(None) == recorded_number(float('nan')) == recorded_number(float('inf')) == '未记录'


def test_required_result_tables_are_checked_before_rendering():
    import pandas as pd
    from result_navigation import validate_display_result
    result = dict(full=False, summary={}, da_summary={},
                  da_nodal_prices=pd.DataFrame([dict(period=0, time='00:00', bus=1, lmp=30., lmp_raw=30., energy=30., congestion=0.)]),
                  da_unit_awards=pd.DataFrame([dict(period=0, unit='G1', type='coal', P_mw=10., energy_mwh=2.5, revenue_yuan=75.)]))
    validate_display_result(result)
    with pytest.raises(ValueError, match='文件缺失或为空'):
        validate_display_result({**result, 'da_nodal_prices': None})
    with pytest.raises(ValueError, match='缺少必要字段'):
        validate_display_result({**result, 'da_nodal_prices': result['da_nodal_prices'].drop(columns='lmp')})
    duplicate = pd.concat([result['da_nodal_prices']] * 2)
    with pytest.raises(ValueError, match='记录重复'):
        validate_display_result({**result, 'da_nodal_prices': duplicate})


@pytest.mark.parametrize('field', ['lmp', 'P_mw', 'energy_mwh', 'revenue_yuan'])
@pytest.mark.parametrize('bad', ['bad value', float('nan'), float('inf'), -float('inf')])
def test_invalid_mandatory_business_quantity_is_rejected(field, bad):
    import pandas as pd
    from result_navigation import validate_display_result
    result = dict(full=False, summary={}, da_summary={},
                  da_nodal_prices=pd.DataFrame([dict(period=0, time='00:00', bus=1, lmp=30., lmp_raw=None, energy=None, congestion=None)]),
                  da_unit_awards=pd.DataFrame([dict(period=0, unit='G1', type='coal', P_mw=10., energy_mwh=2.5, revenue_yuan=75.)]))
    validate_display_result(result)  # Optional raw-price diagnostic values may be unknown.
    frame = result['da_nodal_prices' if field == 'lmp' else 'da_unit_awards']
    frame[field] = frame[field].astype(object)
    frame.loc[0, field] = bad
    with pytest.raises(ValueError, match='必要数值字段 ' + field):
        validate_display_result(result)


def test_missing_entire_node_does_not_renormalize_price_weights():
    import pandas as pd
    from common import load_weighted
    frozen = pd.DataFrame({'bus': [1, 2, 3], 'load_mw': [100., 100., 100.]})
    prices = pd.DataFrame({'period': [0, 0, 1, 1, 1], 'bus': [1, 2, 1, 2, 3],
                           'lmp': [10., 30., 10., 30., 80.], 'load_mw': [1., 3., 1., 3., 2.]})
    value = load_weighted(prices, frozen)
    assert pd.isna(value.loc[0])
    assert value.loc[1] == pytest.approx(260 / 6)  # Archived actual weights, not frozen equal weights.
    entirely_missing = prices[prices.bus != 3]
    assert load_weighted(entirely_missing, frozen).isna().all()
