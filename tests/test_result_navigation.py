"""Result organization preserves partial scope, anomalies, and archived quantities."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from result_navigation import MAIN_SECTIONS, comparison_frame


def test_system_comparison_keeps_missing_rt_blank_and_negative_storage():
    result={'da_system':pd.DataFrame({'period':[0,1,2],'load_mw':[100.,110.,120.],'shed_mw':[0.,0.,0.]}),
            'rt_system':pd.DataFrame({'period':[0,1],'load_mw':[105.,111.],'shed_mw':[0.,1.]}),
            'da_unit_awards':pd.DataFrame({'period':[0,0,1,1,2,2],'unit':['G','S']*3,'P_mw':[110.,-10.,120.,-10.,130.,-10.]}),
            'rt_units':pd.DataFrame({'period':[0,0,1,1],'unit':['G','S']*2,'P_mw':[115.,-10.,120.,-10.]})}
    actual=comparison_frame(result)
    assert actual['日前保存净供给 MW'].tolist()==[100.,110.,120.]
    assert actual['实时已执行保存净供给 MW'].iloc[:2].tolist()==[105.,110.]
    assert np.isnan(actual['实时已执行负荷 MW'].iloc[2])
    assert actual['实时已执行切负荷 MW'].iloc[1]==1.


def test_system_comparison_rejects_duplicate_periods():
    with pytest.raises(ValueError,match='时段重复'):
        comparison_frame({'da_system':pd.DataFrame({'period':[0,0],'load_mw':[1.,2.]})})


def test_navigation_order_and_unmet_requirements_are_visible(tmp_path):
    summary={'executed_periods':2,'expected_periods':96,'completed_scope':'debug_prefix',
             'checks':{'rt_line_overload_max_mw':5.,'rt_line_overload_count':1,'network_feasible':False}}
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom result_navigation import build_result_navigation\nbuild_result_navigation({summary!r},{{}},directory={str(tmp_path)!r})"
    at=AppTest.from_string(code,default_timeout=30).run()
    assert not at.exception
    assert [tab.label for tab in at.tabs]==['系统总览', '机组分析', '价格分析', '网络与安全']
    assert MAIN_SECTIONS == [tab.label for tab in at.tabs]
    assert any('5.0000 MW' in alert.value for alert in at.error)
    assert any('2 时段' in info.value for info in at.info)
    assert any('未记录' in info.value for info in at.info)
    advanced=next(x for x in at.expander if x.label=='高级诊断与技术记录（按需查看）')
    assert not advanced.proto.expanded


@pytest.mark.parametrize('rows', [
    {'period':[0,0,1,1],'unit':['G1','G2','G1','G2'],'P_mw':[70.,float('nan'),70.,30.]},
    {'period':[0,1,1],'unit':['G1','G1','G2'],'P_mw':[70.,70.,30.]},
])
def test_partial_saved_supply_is_unknown_not_a_silent_subtotal(rows):
    result={'da_system':pd.DataFrame({'period':[0,1],'load_mw':[100.,100.]}),
            'da_unit_awards':pd.DataFrame(rows)}
    frame=comparison_frame(result)
    assert pd.isna(frame['日前保存净供给 MW'].iloc[0])
    assert frame['日前保存净供给 MW'].iloc[1]==100.


@pytest.mark.parametrize('change,confirmed', [
    ({}, True),
    ({'settled': False}, False),
    ({'settled': None}, False),
    ({'settlement_calculated': False}, False),
    ({'settlement_calculated': None}, False),
    ({'settlement_scope': 'debug_prefix'}, False),
    ({'settlement_scope': None}, False),
    ({'executed_periods': 2}, False),
    ({'completed_scope': 'debug_prefix'}, False),
])
def test_full_day_settlement_caption_requires_explicit_saved_evidence(tmp_path, change, confirmed):
    summary = dict(expected_periods=96, executed_periods=96, completed_scope='full_day',
                   settlement_scope='full_day', settlement_calculated=True, settled=True)
    summary.update(change)
    summary = {key: value for key, value in summary.items() if value is not None}
    code = f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom result_navigation import build_result_navigation\nbuild_result_navigation({summary!r},{{}},directory={str(tmp_path)!r})"
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception
    hidden = {x.value for detail in at.expander for x in detail.caption}
    visible = [x.value for x in at.caption if x.value not in hidden]
    if confirmed:
        assert any('96/96' in text and '· 全天结算' in text for text in visible)
    else:
        assert any('未确认全天结算' in text for text in visible)
        assert not any('· 全天结算' in text for text in visible)
