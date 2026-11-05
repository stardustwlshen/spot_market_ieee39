"""Planned soft endpoints never masquerade as feasibility-band bindings."""
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from value_views import _planned_terminal_soft_mask


def row(**updates):
    return dict(dict(window_start=92, tau=95, unit='H1', kind='V', kind_label='水电库容',
        value_native=0., value_yuan_per_mwh=0., state=14979., state_da=15000., delta=-21.,
        state_reference=15000., delta_from_reference=-21., reference_source='day_ahead',
        value_source='day_ahead', reference_round=None, reference_fallback_reason=None,
        lower=15000., upper=24000., band_binding=True, band_slack=21., soft=True, fallback=False,
        terminal_policy='soft', planned_terminal_soft=True), **updates)


def render(function, rows):
    code=(f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\n"
          "import pandas as pd\nfrom common import period_labels\n"
          f"from value_views import {function}\n"
          f"frame=pd.DataFrame({rows!r})\nbefore=frame.copy(deep=True)\n"
          "R={'rt_value_windows':frame,'summary':{'realtime':{'lookahead_h':2}},"
          "'rt_fallbacks':pd.DataFrame(columns=['t','reason'])}\n"
          f"{function}(R,period_labels(96),'binding_probe')\n"
          "pd.testing.assert_frame_equal(frame,before)\n")
    at=AppTest.from_string(code,default_timeout=30).run()
    assert not at.exception,[x.message for x in at.exception]
    return at


def binding_traces(at):
    figures=[json.loads(x.proto.spec) for x in at.get('plotly_chart')]
    return [trace for figure in figures for trace in figure['data'] if trace.get('name')=='可行带绑定']


def test_only_explicit_saved_planned_flag_changes_legacy_presentation():
    frame=pd.DataFrame({'planned_terminal_soft':[True,'True','true',False,'False',None]},index=[9,2,8,3,7,4])
    assert _planned_terminal_soft_mask(frame).tolist()==[True,True,True,False,False,False]
    old=pd.DataFrame({'terminal_policy':['soft'],'soft':[True]})
    assert not _planned_terminal_soft_mask(old).iloc[0]


def test_soft_only_shortfall_has_no_feasibility_band_marker():
    at=render('render_rt_values',[row()])
    assert binding_traces(at)==[]
    assert len(at.get('plotly_chart'))==1


def test_mixed_chart_keeps_intermediate_band_but_removes_planned_endpoint():
    at=render('render_rt_values',[
        row(window_start=87,tau=94,planned_terminal_soft=False,soft=False),row()])
    traces=binding_traces(at)
    assert len(traces)==1 and traces[0]['x']==['23:30']


@pytest.mark.parametrize('flag',[False,'False',None])
def test_historical_or_explicit_hard_binding_is_preserved(flag):
    saved=row(terminal_policy='hard')
    if flag is None:
        saved.pop('planned_terminal_soft')
    else:
        saved['planned_terminal_soft']=flag
    traces=binding_traces(render('render_rt_values',[saved]))
    assert len(traces)==1 and traces[0]['x']==['23:45']


def test_mixed_table_marks_soft_binding_not_applicable_without_hiding_numeric_values():
    at=render('render_window_browser',[row(),row(unit='H2',terminal_policy='hard',planned_terminal_soft=False)])
    table=next(x.value for x in at.dataframe if '带绑定' in x.value)
    assert table['带绑定'].tolist()==['不适用（计划日末软目标）','是']
    assert table['带/目标松弛'].tolist()==[21.,21.]
    assert table['窗口末计划状态'].tolist()==[14979.,14979.]
    assert table['应急重求解记录'].tolist()==['否','否']
    assert any('不按可行带绑定判读' in x.value for x in at.caption)


def test_old_table_remains_unchanged_without_planned_flag():
    saved=row();saved.pop('planned_terminal_soft')
    at=render('render_window_browser',[saved])
    table=next(x.value for x in at.dataframe if '带绑定' in x.value)
    assert table['带绑定'].tolist()==['是']
    assert not any('不按可行带绑定判读' in x.value for x in at.caption)
