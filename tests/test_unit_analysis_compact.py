from copy import deepcopy
import pandas as pd
from streamlit.testing.v1 import AppTest
from test_unit_overview import sample
from unit_overview import build_unit_overview, compact_profit_table, COMPACT_PROFIT_COLUMNS, comparison_figure, POWER


def test_compact_columns_values_and_no_mutation():
    data = build_unit_overview(sample(), 'G', id_tau=0)
    before = deepcopy(data['profit_period'])
    table = compact_profit_table(data)
    assert list(table) == ['时间', *COMPACT_PROFIT_COLUMNS.values()]
    assert table.shape == (4, 6)
    assert table.iloc[0, 1:].tolist() == [50., 10., 60., 66., -6.]
    pd.testing.assert_frame_equal(data['profit_period'], before)
    assert 'cost_source_kind' in data['profit_period']


def test_missing_cost_and_cash_only_never_become_zero():
    result = sample()
    for key in ('operating_profit_by_unit', 'operating_profit_by_period'):
        result[key]['cost_available'] = False
    table = compact_profit_table(build_unit_overview(result, 'G'))
    assert table.iloc[:, -2:].isna().all().all()
    result.pop('operating_profit_by_period')
    table = compact_profit_table(build_unit_overview(result, 'G'))
    assert table.iloc[:, -2:].isna().all().all()
    assert table.iloc[:, 1:4].notna().all().all()


def test_partial_execution_table_does_not_invent_remaining_periods():
    data = build_unit_overview(sample(executed=2), 'G')
    assert len(compact_profit_table(data)) == 2
    assert data['comparison'][POWER['rt']].isna().sum() == 2


def test_chart_stage_selection_leaves_missing_gaps():
    data = build_unit_overview(sample(executed=2), 'G')
    fig = comparison_figure(data['comparison'], ('rt',))
    assert len(fig.data) == 1
    assert fig.data[0].name == POWER['rt']
    assert not fig.data[0].connectgaps


def test_primary_view_has_no_tabs_and_details_are_collapsed():
    def app():
        import streamlit as st
        from unit_overview import render_unit_overview
        render_unit_overview(st.session_state['result'], prefix='compact')
    at = AppTest.from_function(app, default_timeout=30)
    at.session_state['result'] = sample(executed=2)
    at.run()
    assert not at.exception
    assert len(at.tabs) == 0
    assert {'完整明细', '运行状态', '价格与成本来源'} <= {item.label for item in at.expander}
    assert all(not item.proto.expanded for item in at.expander)
    assert any('收入' in str(c) for d in at.dataframe for c in d.value.columns) or at.warning
    assert any('尚未完成全天结算' in item.value for item in at.warning)
    for stage in ('日前', '日内', '实时', '对比'):
        at.radio(key='compact:stage').set_value(stage).run()
        assert not at.exception
    assert len([item for item in at.selectbox if item.key == 'compact:unit']) == 1

def compact_app(result):
    def app():
        import streamlit as st
        from unit_overview import render_unit_overview
        render_unit_overview(st.session_state['result'], prefix='scope')
    at = AppTest.from_function(app, default_timeout=30)
    at.session_state['result'] = result
    return at.run()


def test_full_rows_need_saved_completeness_metadata():
    result = sample()
    at = compact_app(result)
    assert not at.exception
    assert any('全天结算状态未核实' in item.value for item in at.warning)
    assert not any('全天结算已完成' in item.value for item in at.caption)
    result['summary'].update(completed_scope='full_day', executed_periods=4,
                             settlement_scope='full_day', settlement_calculated=True, settled=True)
    at = compact_app(result)
    assert not at.exception
    assert any('全天结算已完成' in item.value for item in at.caption)
    result['summary']['executed_periods'] = 3
    at = compact_app(result)
    assert any('全天结算状态未核实' in item.value for item in at.warning)


def test_da_only_and_disabled_id_have_no_empty_stage_choices():
    result = sample(intraday=False)
    result['rt_units'] = pd.DataFrame()
    at = compact_app(result)
    assert not at.exception
    assert at.radio(key='scope:stage').options == ['日前']
    assert any('仅日前计划，无实时执行与偏差结算' in item.value for item in at.caption)
    assert any('日内未启用' in item.value for item in at.caption)
    assert not any('尚未完成' in item.value for item in at.warning)
    at = compact_app(sample(intraday=False))
    assert at.radio(key='scope:stage').options == ['对比', '日前', '实时']
    assert not any(item.key == 'scope:id_tau' for item in at.selectbox)


def test_round_selection_survives_hidden_selector():
    at = compact_app(sample())
    at.selectbox(key='scope:id_tau').select(2).run()
    at.radio(key='scope:stage').set_value('实时').run()
    assert not any(item.key == 'scope:id_tau' for item in at.selectbox)
    at.radio(key='scope:stage').set_value('日内').run()
    assert not at.exception
    assert at.selectbox(key='scope:id_tau').value == 2


def test_full_rows_do_not_override_false_or_missing_settlement_flags():
    for field in ('settlement_scope', 'settlement_calculated', 'settled'):
        for missing in (False, True):
            result = sample()
            result['summary'].update(completed_scope='full_day', executed_periods=4,
                                     settlement_scope='full_day', settlement_calculated=True, settled=True)
            if missing:
                result['summary'].pop(field)
            else:
                result['summary'][field] = 'partial' if field == 'settlement_scope' else False
            at = compact_app(result)
            assert not at.exception
            assert not any('全天结算已完成' in item.value for item in at.caption)
            assert any('全天结算状态未核实' in item.value for item in at.warning)
