"""Curtailment comes from physical dispatch, not prices, overlapping windows or deficits."""
from copy import deepcopy
import ast
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from curtailment_views import curtailment_report, _summary_table


@pytest.fixture
def result(tmp_path):
    config = tmp_path / 'scenario_snapshot' / 'config'
    config.mkdir(parents=True)
    (config / 'base.yaml').write_text(yaml.safe_dump(dict(dt_h=.25, periods=2, rule_set='MKT')), encoding='utf-8')
    (config / 'units.yaml').write_text(yaml.safe_dump({'renewable': [
        dict(id='W1', type='wind'), dict(id='W2', type='wind'), dict(id='PV1', type='pv')]}), encoding='utf-8')
    (config / 'modes.yaml').write_text(yaml.safe_dump({'MKT': {'realtime': {'first_period_actual': True}}}), encoding='utf-8')
    rows = [dict(period=t, unit=u, type=typ, bus=30, avail_mw=a, P_mw=p, curtail_mw=a-p)
            for t, u, typ, a, p in [(0, 'W1', 'wind', 100., 80.), (1, 'W1', 'wind', 60., 30.),
                                  (0, 'W2', 'wind', 40., 40.), (1, 'W2', 'wind', 20., 20.),
                                  (0, 'PV1', 'pv', 0., 0.), (1, 'PV1', 'pv', 40., 30.)]]
    return dict(snapshot_root=str(config.parent), full=True, summary={'executed_periods': 1},
                da_unit_awards=pd.DataFrame(rows), rt_units=pd.DataFrame(rows)[lambda d: d.period == 0].copy(),
                da_nodal_prices=pd.DataFrame({'period': [0, 1]}), rt_prices=pd.DataFrame({'period': [0]}))


def item(report, stage='日前', kind='wind'):
    return report['stages'][stage]['summary'].set_index('type').loc[kind]


def test_wind_solar_energy_rate_and_executed_scope(result):
    original = deepcopy(result)
    report = curtailment_report(result)
    wind, solar = item(report), item(report, kind='pv')
    assert wind.curtail_mwh == pytest.approx(12.5)
    assert wind.avail_mwh == pytest.approx(55.)
    assert wind.P_mwh == pytest.approx(42.5)
    assert wind.rate_pct == pytest.approx(50 / 220 * 100)
    assert solar.curtail_mwh == pytest.approx(2.5)
    assert item(report, '实时').curtail_mwh == pytest.approx(5.)
    assert report['stages']['实时']['scope'] == '实时已执行 1 段'
    assert np.isnan(item(report, '实时', 'pv').rate_pct)  # Zero availability is not 0%.
    assert '模拟实际' in report['stages']['实时']['basis']
    assert '申报可用预测' in report['stages']['日前']['basis']
    for key in ('da_unit_awards', 'rt_units'):
        pd.testing.assert_frame_equal(result[key], original[key])


def test_rt_forecast_flag_never_claims_actual(result):
    p = Path(result['snapshot_root']) / 'config' / 'modes.yaml'
    p.write_text(yaml.safe_dump({'MKT': {'realtime': {'first_period_actual': False}}}), encoding='utf-8')
    assert '超短期预测' in curtailment_report(result)['stages']['实时']['basis']
    p.unlink()
    assert '口径未记录' in curtailment_report(result)['stages']['实时']['basis']


def test_uses_frozen_non_quarter_hour_duration(result):
    p = Path(result['snapshot_root']) / 'config' / 'base.yaml'
    p.write_text(yaml.safe_dump(dict(dt_h=.5, periods=2, rule_set='MKT')), encoding='utf-8')
    report = curtailment_report(result)
    assert item(report).curtail_mwh == pytest.approx(25.)


@pytest.mark.parametrize('duration', [None, 0, -1, float('nan'), float('inf'), 'bad'])
def test_unknown_duration_never_uses_live_config_or_zero(result, duration):
    p = Path(result['snapshot_root']) / 'config' / 'base.yaml'
    p.write_text(yaml.safe_dump(dict(dt_h=duration, periods=2, rule_set='MKT')), encoding='utf-8')
    report = curtailment_report(result)
    assert np.isnan(item(report).curtail_mwh)
    assert item(report).rate_pct == pytest.approx(50 / 220 * 100)
    assert report['stages']['日前']['detail'].curtail_mw.sum() == pytest.approx(60.)


@pytest.mark.parametrize('mutation', ['row', 'station', 'column', 'nan', 'duplicate', 'mismatch', 'negative', 'type'])
def test_bad_rows_invalidate_affected_total_not_solar(result, mutation):
    d = result['da_unit_awards']
    if mutation == 'row':
        d = d.drop(index=0)
    elif mutation == 'station':
        d = d[d.unit != 'W1']
    elif mutation == 'column':
        # Missing whole dispatch column affects both resources, unlike a single cell.
        d.loc[d.type == 'wind', 'P_mw'] = np.nan
    elif mutation == 'nan':
        d.loc[0, 'curtail_mw'] = np.nan
    elif mutation == 'duplicate':
        d = pd.concat([d, d.iloc[[0]]], ignore_index=True)
    elif mutation == 'mismatch':
        d.loc[0, 'curtail_mw'] = 99.
    elif mutation == 'negative':
        d.loc[0, ['P_mw', 'curtail_mw']] = [120., -20.]
    else:
        d.loc[0, 'type'] = 'pv'
    result['da_unit_awards'] = d
    report = curtailment_report(result)
    assert np.isnan(item(report).curtail_mwh)
    assert item(report, kind='pv').curtail_mwh == pytest.approx(2.5)
    bad = report['stages']['日前']['detail'].query('not valid')
    assert not bad.empty
    assert bad[['avail_mw', 'P_mw', 'curtail_mw']].isna().all().all()


def test_missing_entire_stage_does_not_fabricate_zero(result):
    result['rt_units'] = None
    report = curtailment_report(result)
    assert np.isnan(item(report, '实时').curtail_mwh)
    assert np.isnan(item(report, '实时', 'pv').curtail_mwh)


def test_missing_periods_detected_against_frozen_da_and_executed_rt(result):
    result['da_unit_awards'] = result['da_unit_awards'].query('period == 0')
    result['da_nodal_prices'] = pd.DataFrame({'period': [0]})
    result['summary']['executed_periods'] = 2
    report = curtailment_report(result)
    assert np.isnan(item(report).curtail_mwh)
    assert np.isnan(item(report, '实时').curtail_mwh)


def test_summary_and_future_window_values_cannot_replace_physical_quantities(result):
    result['summary'].update(da={'curtail_mwh': 8888}, realtime={'curtail_mwh': 9999})
    result['rt_window_plans'] = result['rt_units'].assign(curtail_mw=99999)
    report = curtailment_report(result)
    assert item(report).curtail_mwh == pytest.approx(12.5)
    assert item(report, '实时').curtail_mwh == pytest.approx(5.)


def test_legacy_export_without_curtailment_column_can_derive_it(result):
    result['da_unit_awards'] = result['da_unit_awards'].drop(columns='curtail_mw')
    assert item(curtailment_report(result)).curtail_mwh == pytest.approx(12.5)


def test_rounding_noise_only_is_normalized(result):
    result['da_unit_awards'].loc[2, ['P_mw', 'curtail_mw']] = [40.00001, -.00001]
    report = curtailment_report(result)
    assert item(report).valid
    assert report['stages']['日前']['detail'].query("unit == 'W2' and period == 0").curtail_mw.iloc[0] == 0.


def test_missing_snapshot_retains_unknown_mwh_and_rt_basis(result):
    result['snapshot_root'] = '/does/not/exist'
    report = curtailment_report(result)
    assert np.isnan(item(report).curtail_mwh)
    assert '口径未记录' in report['stages']['实时']['basis']


def test_no_configured_solar_is_not_missing_solar(result):
    p = Path(result['snapshot_root']) / 'config' / 'units.yaml'
    p.write_text(yaml.safe_dump({'renewable': [dict(id='W1', type='wind'), dict(id='W2', type='wind')]}), encoding='utf-8')
    for key in ('da_unit_awards', 'rt_units'):
        result[key] = result[key].query("type == 'wind'")
    assert item(curtailment_report(result), kind='pv').status == '未配置'
    p.unlink()
    assert item(curtailment_report(result), kind='pv').status == '数据缺失或异常'


def render(result, mount_code=None):
    metadata = {key: value for key, value in result.items() if not isinstance(value, pd.DataFrame)}
    code = f"import sys, json, pandas as pd\nsys.path.insert(0, {str(ROOT / 'app')!r})\nR = json.loads({json.dumps(metadata)!r})\n"
    for key, value in result.items():
        if isinstance(value, pd.DataFrame):
            code += f"R[{key!r}] = pd.DataFrame(json.loads({value.to_json(orient='records')!r}))\n"
    code += 'from curtailment_views import curtailment_report, curtailment_overview, curtailment_dashboard\nreport = curtailment_report(R)\n'
    code += mount_code if mount_code is not None else 'curtailment_overview(report)\ncurtailment_dashboard(report)'
    return AppTest.from_string(code, default_timeout=30).run()


def test_dashboard_phase_station_repeated_switch_and_empty_selection(result):
    at = render(result)
    assert not at.exception
    assert next(m for m in at.metric if m.label == '日前弃风 · MWh').value == '12.50'
    assert next(m for m in at.metric if m.label == '实时弃光 · MWh').value == '0.00'
    assert any('模拟实际' in c.value and '实测' in c.value for c in at.caption)
    assert len(at.get('plotly_chart')) == 2
    table = at.dataframe[1].value
    assert set(table['场站']) == {'W1', 'W2', 'PV1'}
    at.multiselect[0].set_value(['W1']).run()
    assert not at.exception and set(at.dataframe[-1].value['主体']) == {'W1'}
    at.radio[0].set_value('实时').run()
    assert not at.exception
    assert any('实时已执行 1 段' in c.value for c in at.caption)
    at.multiselect[0].set_value([]).run()
    assert not at.exception and any('请选择至少一个' in i.value for i in at.info)
    at.radio[0].set_value('日前').run()
    assert not at.exception
    at.radio[0].set_value('实时').run()
    assert not at.exception


def test_dashboard_missing_values_are_unknown_and_warned(result):
    result['da_unit_awards'].loc[0, 'avail_mw'] = np.nan
    at = render(result)
    assert not at.exception
    assert next(m for m in at.metric if m.label == '日前弃风 · MWh').value == '未记录'
    assert any('不以零填补' in w.value for w in at.warning)
    assert pd.isna(at.dataframe[0].value.loc[0, '弃电量 MWh'])


def test_da_only_has_no_rt_choice_or_metrics(result):
    result.update(full=False, rt_units=None, rt_prices=None)
    at = render(result)
    assert not at.exception
    assert at.radio[0].options == ['日前']
    assert len(at.metric) == 2


def test_empty_history_renders_unknown_without_exception():
    at = render(dict(full=False))
    assert not at.exception
    assert all(m.value == '未记录' for m in at.metric)
    assert any('不能推断弃电为零' in i.value for i in at.info)


@pytest.mark.parametrize('full', [True, False])
def test_result_page_mounts_curtailment_in_system_overview(result, full):
    text = (ROOT / 'app/pages/3_结果.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    from result_navigation import MAIN_SECTIONS
    assert MAIN_SECTIONS[0] == '系统总览'
    main_tabs = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'main_tabs' for target in node.targets))
    assert ast.unparse(main_tabs.value) == "navigation['main']"
    # Extract the real mount's direct children from the system-overview tab,
    # not a same-named call elsewhere. This focused AppTest runs those source
    # nodes, not the full results page.
    overview = next(node for node in tree.body if isinstance(node, ast.With)
                    and ast.unparse(node.items[0].context_expr) == 'main_tabs[0]')
    selector = next(node for node in overview.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'overview_stage' for target in node.targets))
    section = next(node for node in overview.body if isinstance(node, ast.With)
                   and ast.unparse(node.items[0].context_expr) == "st.expander('弃风弃光')")
    dashboard = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'page_curtailment')
    mount = ast.With(items=deepcopy(overview.items), body=[selector, section], type_comment=None)
    mount_code = (
        "import streamlit as st\nfrom result_navigation import MAIN_SECTIONS\n"
        "main_tabs = st.tabs(MAIN_SECTIONS)\nFULL = R['full']\n"
        "SNAME = {'da': '日前', 'rt': '实时'}\ncurtailment = report\n"
        + ast.unparse(dashboard) + '\n' + ast.unparse(ast.fix_missing_locations(mount)))
    if not full:
        result.update(full=False, rt_units=None, rt_prices=None)
    at = render(result, mount_code)
    assert not at.exception
    assert [tab.label for tab in at.tabs] == MAIN_SECTIONS
    block = next(item for item in at.tabs[0].expander if item.label == '弃风弃光')
    assert next(item for item in block.metric if item.label == '日前弃风 · MWh').value == '12.50'
    assert block.dataframe[0].value.loc[0, '弃电量 MWh'] == pytest.approx(12.5)
    assert len(block.get('plotly_chart')) == 2
    assert at.radio(key='overview_stage').options == (['日前', '实时'] if full else ['日前'])
    for stage in (['rt', 'da', 'rt'] if full else ['da']):
        at.radio(key='overview_stage').set_value(stage).run()
        assert not at.exception
        block = next(item for item in at.tabs[0].expander if item.label == '弃风弃光')
        assert block.dataframe[0].value.loc[0, '弃电量 MWh'] == pytest.approx(5. if stage == 'rt' else 12.5)
        scope = '实时已执行 1 段' if stage == 'rt' else '日前计划'
        assert any(scope in caption.value for caption in block.caption)
        assert len(block.get('plotly_chart')) == 2
    assert 'DS.get(\'curtailment_mwh\', 0)' not in text
