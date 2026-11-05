"""Final-result scope and physical-unit UI regressions, without any optimization."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from final_views import DA, RT, DIFF, kpi_compare, state_compare, state_ranking, value_compare


RESOURCES = [('E', '储能SOC电量', 'ES1', 'storage', 'unit', 'E_mwh', 100., 'MWh'),
             ('Vu', '抽蓄上库库容', 'PS1', 'pumped', 'unit', 'V_up', 200., '万m³'),
             ('V', '水电库容', 'H1', 'hydro', 'station', 'V_1e4m3', 300., '万m³')]


def make_result(executed=8, expected=96, scope='debug_prefix'):
    """Distinct nonzero values expose wrong scopes, units, and zero-filling."""
    result = {'summary': dict(executed_periods=executed, expected_periods=expected, completed_scope=scope),
              'settlement_detail': pd.DataFrame([dict(entity='ES1', period=0, q_da=1., q_rt=2., lmp_da=10., lmp_rt=2., da_amount=240. / expected, rt_amount=48. / expected, total=288. / expected)]),
              'settlement_by_entity': pd.DataFrame([dict(entity='ES1', da_amount=10., rt_amount=2., total=12.)]),
              'settlement_reconciliation': pd.DataFrame([dict(residual=0.)])}
    prices = pd.DataFrame([dict(period=t, bus=b, lmp=30. + t + b, load_mw=100.)
                           for t in range(expected) for b in (1, 2)])
    result['da_nodal_prices'] = prices
    result['rt_prices'] = prices[prices.period < executed].assign(lmp=lambda f: f.lmp + 2.)
    awards = pd.DataFrame([dict(period=t, unit=uid, type=typ, P_mw=20. + t)
                           for _, _, uid, typ, *_ in RESOURCES for t in range(expected)])
    result['da_unit_awards'] = awards
    result['rt_units'] = awards[awards.period < executed].assign(P_mw=lambda f: f.P_mw + 1.)
    curves, windows = [], []
    for kind, label, uid, typ, idcol, statecol, base, _ in RESOURCES:
        result['da_' + typ] = pd.DataFrame([{'period': t, idcol: uid, statecol: base + t} for t in range(expected)])
        result['rt_' + typ] = pd.DataFrame([{'period': t, idcol: uid, statecol: base + t + .5} for t in range(executed)])
        curves.extend(dict(kind=kind, kind_label=label, unit=uid, period=t, value_yuan_per_mwh=40. + t)
                      for t in range(expected))
        windows.extend(dict(kind=kind, kind_label=label, unit=uid, window_start=t, tau=min(t + 7, expected - 1),
                            value_yuan_per_mwh=45. + min(t + 7, expected - 1),
                            state=base + min(t + 7, expected - 1) + .5,
                            state_da=base + min(t + 7, expected - 1), delta=.5,
                            band_binding=False, soft=False, fallback=False)
                       for t in range(executed))
    result['da_value_curves'] = pd.DataFrame(curves)
    result['rt_value_windows'] = pd.DataFrame(windows)
    return result


def end_columns(table):
    return [c for c in table.columns if c.endswith(' 日前') or c.endswith(' 实时') or c.endswith(' 实时−日前')]


def test_prefix_endpoint_is_period_end_not_day_end_or_period_start():
    result = make_result()
    ranking = state_ranking(state_compare(result))
    assert end_columns(ranking) == [f'已执行前缀末（第8段末，02:00） {stage}' for stage in (DA, RT, DIFF)]
    assert not any('日末' in c for c in ranking)
    kpi = kpi_compare(result)
    names = kpi[kpi['指标'].str.contains('ES1|PS1|H1')]['指标']
    assert len(names) == 3
    assert all('已执行前缀末（第8段末，02:00）' in name for name in names)
    assert ranking['已执行前缀末（第8段末，02:00） 实时'].tolist() == [107.5, 207.5, 307.5]


@pytest.mark.parametrize('expected', [24, 48, 96])
def test_only_verified_complete_day_keeps_day_end_labels(expected):
    result = make_result(expected, expected, 'full_day')
    assert end_columns(state_ranking(state_compare(result))) == [f'日末 {stage}' for stage in (DA, RT, DIFF)]
    names = kpi_compare(result)['指标']
    assert all('日末' in name for name in names[names.str.contains('ES1|PS1|H1')])


@pytest.mark.parametrize('summary', [
    {}, {'expected_periods': 96},
    {'completed_scope': 'full_day', 'executed_periods': 8, 'expected_periods': 96},
    {'completed_scope': 'full_day', 'executed_periods': 96, 'expected_periods': 96},
    {'completed_scope': 'debug_prefix', 'executed_periods': 8, 'expected_periods': 8},
    {'completed_scope': 'full_day', 'executed_periods': 8},
    {'completed_scope': 'full_day', 'executed_periods': 8, 'expected_periods': -1},
])
def test_unknown_or_conflicting_scope_never_claims_day_end(summary):
    result = make_result()
    result['summary'] = summary
    ranking = state_ranking(state_compare(result))
    assert not any('日末' in c for c in ranking)


def test_missing_scope_on_full_length_archive_is_not_inferred():
    result = make_result(96)
    result['summary'].pop('completed_scope')
    assert not any('日末' in c for c in state_ranking(state_compare(result)))


def test_noncontiguous_execution_is_not_a_prefix_or_full_day():
    result = make_result(8, 8, 'full_day')
    result['rt_prices'] = result['rt_prices'][result['rt_prices'].period != 3]
    columns = end_columns(state_ranking(state_compare(result)))
    assert all('已执行末' in c and '前缀' not in c and '日末' not in c for c in columns)


def test_metadata_free_state_table_uses_only_saved_endpoint():
    states = state_compare(make_result())
    states.attrs.clear()
    assert end_columns(state_ranking(states)) == [f'已执行末（第8段末） {stage}' for stage in (DA, RT, DIFF)]


@pytest.mark.parametrize('missing', ['rt_endpoint', 'da_endpoint', 'all_rt_values'])
def test_missing_state_remains_unknown_not_previous_endpoint_or_zero(missing):
    result = make_result()
    if missing == 'rt_endpoint':
        result['rt_storage'] = result['rt_storage'][result['rt_storage'].period != 7]
    elif missing == 'da_endpoint':
        result['da_storage'] = result['da_storage'][result['da_storage'].period != 7]
    else:
        result['rt_storage']['E_mwh'] = np.nan
    table = state_ranking(state_compare(result)).set_index('entity')
    endpoint = [c for c in end_columns(table) if c.endswith(' 实时')][0]
    assert pd.isna(table.loc['ES1', endpoint])
    assert pd.isna(table.loc['ES1', endpoint.replace(' 实时', ' 实时−日前')])
    kpi = kpi_compare(result)
    row = kpi[kpi['指标'].str.startswith('ES1 ')].iloc[0]
    assert pd.isna(row[RT]) and pd.isna(row[DIFF])
    if missing == 'all_rt_values':
        assert pd.isna(table.loc['ES1', '最大偏差'])
        assert pd.isna(table.loc['ES1', '最大偏差时段'])


def test_value_comparison_carries_physical_units_and_unknowns():
    result = make_result()
    out = value_compare(result)
    assert out.groupby('主体')['状态单位'].first().to_dict() == {'ES1': 'MWh', 'PS1': '万m³', 'H1': '万m³'}
    result['rt_value_windows'].loc[0, 'kind'] = 'unknown'
    result['rt_value_windows'].loc[0, 'delta'] = np.nan
    result['rt_value_windows'].loc[0, 'value_yuan_per_mwh'] = np.nan
    out = value_compare(result)
    assert out.loc[0, '状态单位'] == '未记录'
    assert pd.isna(out.loc[0, 'ΔE/ΔV（实时−日前）'])
    assert pd.isna(out.loc[0, '实际使用−日前 元/MWh'])


def render_result(executed=8, expected=96, scope='debug_prefix', mutation=''):
    code = f'''
import sys
sys.path.insert(0, {str(ROOT / 'tests')!r})
sys.path.insert(0, {str(ROOT / 'app')!r})
import streamlit as st
import common
from test_final_result_scope_units import make_result
from final_views import render_final
R = make_result({executed}, {expected}, {scope!r})
{mutation}
original_show_fig = common.show_fig
st.session_state['captured_figures'] = {{}}
def capture(fig, key=None, **kwargs):
    st.session_state['captured_figures'][key] = {{
        'layout': fig.layout.to_plotly_json(),
        'traces': [{{'name': t.name, 'axis': t.yaxis or 'y', 'y': list(t.y) if t.y is not None else []}} for t in fig.data]
    }}
    original_show_fig(fig, key=key, **kwargs)
common.show_fig = capture
try:
    render_final(R, buses=None, ll_da=None, ll_rt=None, report={{}}, lbl=common.period_labels({expected}), sel={{}})
finally:
    common.show_fig = original_show_fig
'''
    return AppTest.from_string(code, default_timeout=30).run()


def test_render_prefix_scope_labels_and_separated_value_state_axes():
    at = render_result()
    assert not at.exception
    assert any('8 段 / 预期 96 段' in w.value and '02:00' in w.value for w in at.warning)
    figs = at.session_state['captured_figures']
    for _, _, uid, _, _, _, _, unit in RESOURCES:
        value = figs['final_value_' + uid]
        state = figs['final_value_state_' + uid]
        assert value['layout']['yaxis']['title']['text'] == '价值（元/MWh）'
        assert value['layout']['yaxis2']['title']['text'] == '价值差（元/MWh）'
        assert len(value['traces']) == 3
        assert [t['y'] for t in value['traces'] if t['axis'] == 'y2'] == [[5.] * 8]
        assert state['layout']['yaxis']['title']['text'] == f'状态（{unit}）'
        assert unit in state['layout']['yaxis2']['title']['text']
        assert [t['y'] for t in state['traces'] if t['axis'] == 'y2'] == [[.5] * 8]
    frames = [x.value for x in at.dataframe if '状态单位' in x.value]
    assert len(frames) == 2 and all(f['状态单位'].nunique() == 1 for f in frames)
    assert all('日末' not in c for x in at.dataframe for c in x.value.columns)
    # Repeated render must not keep stale charts or cause duplicate widget IDs.
    at.run()
    assert not at.exception
    assert set(at.session_state['captured_figures']) == set(figs)
    at.selectbox(key='final_energy_unit').select('H1').run()
    assert not at.exception
    at.selectbox(key='final_energy_unit').select('ES1').run()
    assert not at.exception
    assert set(at.session_state['captured_figures']) == set(figs)


@pytest.mark.parametrize('expected', [24, 96])
def test_render_full_day_has_no_false_96_period_warning(expected):
    at = render_result(expected, expected, 'full_day')
    assert not at.exception and not at.warning
    assert any('日末 日前' in x.value for x in at.dataframe)


def test_unknown_state_unit_is_disclosed_not_assigned_to_currency_axis():
    at = render_result(mutation="R['rt_value_windows']['kind'] = 'unknown'")
    assert not at.exception
    assert any('状态单位未记录' in x.value for x in at.info)
    figs = at.session_state['captured_figures']
    assert not any(key.startswith('final_value_state_') for key in figs)


def test_missing_values_render_as_gaps_not_zeros():
    at = render_result(mutation="R['rt_value_windows']['value_yuan_per_mwh'] = float('nan')\nR['rt_value_windows']['delta'] = float('nan')")
    assert not at.exception
    for key, fig in at.session_state['captured_figures'].items():
        if key.startswith('final_value_'):
            assert all(pd.isna(y) for t in fig['traces'] if t['axis'] == 'y2' for y in t['y'])


def test_da_only_no_execution_stops_before_final_comparisons():
    at = render_result(0, 96, 'da_only')
    assert not at.exception
    assert not at.session_state['captured_figures']
    assert any('仅日前结果' in x.value for x in at.info)


def with_tie(result):
    for stage, key in [('da', 'da_unit_awards'), ('rt', 'rt_units')]:
        periods = result['summary']['expected_periods' if stage == 'da' else 'executed_periods']
        tie = pd.DataFrame([dict(period=t, unit='TIE1', type='tie', P_mw=80.) for t in range(periods)])
        result[key] = pd.concat([result[key], tie], ignore_index=True)
    return result


@pytest.mark.parametrize('missing', ['whole_unit', 'one_period', 'missing_power', 'duplicate'])
def test_missing_import_energy_is_unknown_not_zero_or_subtotal(missing):
    result = with_tie(make_result())
    if missing == 'whole_unit':
        result['rt_units'] = result['rt_units'][result['rt_units'].type != 'tie']
    elif missing == 'one_period':
        result['rt_units'] = result['rt_units'][~((result['rt_units'].type == 'tie') & (result['rt_units'].period == 7))]
    elif missing == 'missing_power':
        result['rt_units'].loc[result['rt_units'].type == 'tie', 'P_mw'] = np.nan
    else:
        result['rt_units'] = pd.concat([result['rt_units'], result['rt_units'][result['rt_units'].type == 'tie'].head(1)])
    row = kpi_compare(result).set_index('指标').loc['区外来电']
    assert row[DA] == 160.
    assert pd.isna(row[RT]) and pd.isna(row[DIFF])


def test_saved_zero_import_and_signed_storage_remain_real_measurements():
    result = with_tie(make_result())
    result['rt_units'].loc[result['rt_units'].type == 'tie', 'P_mw'] = 0.
    for key in ['da_unit_awards', 'rt_units']:
        result[key].loc[(result[key].type == 'storage') & (result[key].period < 4), 'P_mw'] = -10.
    kpi = kpi_compare(result).set_index('指标')
    assert kpi.loc['区外来电', RT] == 0. and kpi.loc['区外来电', DIFF] == -160.
    assert kpi.loc['储能充电/抽水', DA] == 10.
    assert kpi.loc['储能充电/抽水', RT] == 10.


@pytest.mark.parametrize('case', ['missing_total', 'missing_all', 'empty', 'nan', 'inf', 'bad_number', 'missing_detail', 'missing_reconciliation'])
def test_settlement_incomplete_evidence_never_crashes_or_passes(case):
    from final_views import settlement_identity
    result = make_result()
    if case == 'missing_total':
        result['settlement_by_entity'] = result['settlement_by_entity'].drop(columns='total')
    elif case == 'missing_all':
        for key in ('settlement_detail', 'settlement_by_entity', 'settlement_reconciliation'):
            result.pop(key)
    elif case == 'empty':
        result['settlement_by_entity'] = result['settlement_by_entity'].iloc[:0]
    elif case in ('nan', 'inf', 'bad_number'):
        result['settlement_by_entity']['total'] = {'nan': np.nan, 'inf': np.inf, 'bad_number': 'invalid'}[case]
    elif case == 'missing_detail':
        result.pop('settlement_detail')
    else:
        result.pop('settlement_reconciliation')
    out = settlement_identity(result)
    assert out['通过（<1 元）'] is None
    assert out['未验证项']
    assert '逐主体最大误差 元' not in out or out['逐主体最大误差 元'] == 0.


def test_settlement_known_failures_are_preserved_even_with_another_missing_check():
    from final_views import settlement_identity
    result = make_result()
    result['settlement_by_entity']['total'] += 2.
    result.pop('settlement_reconciliation')
    out = settlement_identity(result)
    assert out['通过（<1 元）'] is False
    assert out['逐主体最大误差 元'] == 2.
    assert out['未验证项']


def test_settlement_complete_verified_zero_errors_pass():
    from final_views import settlement_identity
    out = settlement_identity(make_result())
    assert out['通过（<1 元）'] is True
    assert '未验证项' not in out
    assert len([key for key in out if key.endswith('元')]) == 3


def test_missing_total_settlement_ui_warns_and_reaches_later_sections():
    at = render_result(mutation="R['settlement_by_entity'] = R['settlement_by_entity'].drop(columns='total')")
    assert not at.exception
    assert any('结算恒等式未完整验证' in x.value and 'total' in x.value for x in at.warning)
    frame = next(x.value for x in at.dataframe if '检查项' in x.value)
    assert frame.set_index('检查项').loc['通过（<1 元）', '值'] == '未验证'
    assert any('资金平衡校核' in x.value for x in at.markdown)
    assert at.dataframe[-1].value.iloc[0, 0] == 0.


def test_value_caption_uses_saved_resource_window_sources_not_intraday_switch():
    at = render_result(mutation="R['summary']['intraday'] = {'enabled': True}")
    assert not at.exception
    caption = next(x.value for x in at.caption if 'rt_value_windows.csv' in x.value)
    assert '各资源、各窗口' in caption
    assert '日前、日内轮次或回退' in caption
    assert '不能仅因开启日内就认定全部取最新日内轮次' in caption
    assert '缺失字段为未记录，不自行推断' in caption
    for field in ('reference_source', 'value_source', 'reference_round', 'reference_fallback_reason'):
        assert field in caption
    assert '不开日内时实际使用值等于日前' not in caption
    assert '开日内（realtime.handoff_source=intraday）时取最近一轮' not in caption
