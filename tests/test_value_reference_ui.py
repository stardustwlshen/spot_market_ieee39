"""Saved value semantics and resource references remain truthful in the UI."""
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from value_views import reference_details, terminal_value_note


def value_row(*, start=0, tau=7, source='intraday@0', reason=None):
    return dict(window_start=start, tau=tau, unit='ES1', kind='E', kind_label='储能SOC电量',
                value_native=-20., value_yuan_per_mwh=-20., state=101., state_da=80., delta=21.,
                state_reference=100., delta_from_reference=1., reference_source=source,
                value_source=source, reference_round=0 if source.startswith('intraday') else None,
                reference_fallback_reason=reason, lower=98., upper=102., band_binding=False,
                band_slack=0., soft=False, fallback=False)


def render(function, rows=None, *, fallback='empty', current_semantics=True):
    rows = [value_row()] if rows is None else rows
    code = (f"import sys\nsys.path.insert(0,{str(ROOT / 'app')!r})\n"
            "import pandas as pd\nfrom common import period_labels\n"
            f"from value_views import {function}\n"
            f"R={{'rt_value_windows':pd.DataFrame({rows!r}), 'summary':{{'realtime':{{'lookahead_h':2}}}}}}\n")
    if fallback == 'empty':
        code += "R['rt_fallbacks']=pd.DataFrame(columns=['t','reason'])\n"
    elif fallback == 'events':
        code += "R['rt_fallbacks']=pd.DataFrame([{'t':0,'reason':'hard infeasible'},{'t':0,'reason':'state_deviation_at_anchor'}])\n"
    if current_semantics:
        code += "R['da_value_curves']=pd.DataFrame([{'continuation_value_scope':'no_cross_day_residual'}])\n"
    code += f"{function}(R,period_labels(96),'probe')"
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception, [x.message for x in at.exception]
    return at


def test_current_terminal_scope_is_not_terminal_target_dual():
    R = {'da_value_curves': pd.DataFrame([dict(continuation_value_scope='no_cross_day_residual',
                                             value_native=0., terminal_target_dual_native=-114.)])}
    note = terminal_value_note(R)
    assert '日末额外延续价值为 0' in note and '不代表对偶本身为 0' in note
    assert R['da_value_curves'].iloc[0].terminal_target_dual_native == -114.


def test_old_export_does_not_invent_new_terminal_semantics():
    R = {'da_value_curves': pd.DataFrame([dict(value_native=-114.)])}
    assert '未明确记录' in terminal_value_note(R)
    assert '保持已保存数值' in terminal_value_note(R)
    assert '日末额外延续价值为 0' not in terminal_value_note(R)


def test_mixed_sources_and_fallback_are_shown_per_resource():
    first = value_row()
    second = dict(value_row(source='day_ahead', reason='missing_dual'), unit='ES2')
    frame = pd.DataFrame([first, second])
    before = frame.copy(deep=True)
    shown = reference_details(frame)
    assert shown['轨迹来源'].tolist() == ['日内（轮次起点：第1段）', '日前']
    assert shown['价值来源'].tolist() == shown['轨迹来源'].tolist()
    assert shown['参考回退原因'].tolist() == ['无（已记录）', 'missing_dual']
    assert shown['窗口末参考状态'].tolist() == [100., 100.]
    assert shown['实时−参考状态'].tolist() == [1., 1.]
    assert shown['实时−日前状态'].tolist() == [21., 21.]
    pd.testing.assert_frame_equal(frame, before)


def test_legacy_reference_fields_remain_unknown_not_day_ahead_or_zero():
    shown = reference_details(pd.DataFrame([dict(unit='ES1', state_da=80., delta=21.)]))
    assert shown.iloc[0]['轨迹来源'] == shown.iloc[0]['价值来源'] == '未记录'
    assert shown.iloc[0]['参考回退原因'] == '未记录'
    assert pd.isna(shown.iloc[0]['窗口末参考状态'])
    assert pd.isna(shown.iloc[0]['实时−参考状态'])


def test_rt_view_uses_saved_source_and_does_not_claim_da_only():
    at = render('render_rt_values', [value_row(), dict(value_row(source='day_ahead', reason='missing_dual'), unit='ES2')])
    captions = ' '.join(x.value for x in at.caption)
    assert '直接读取每个窗口保存的入模价值' in captions
    assert '实际使用值”取自日前' not in captions
    assert '带绑定本身不能证明' in captions
    assert '日末额外延续价值为 0' in captions
    shown = next(x.value for x in at.dataframe if '参考回退原因' in x.value)
    assert set(shown['轨迹来源']) == {'日前', '日内（轮次起点：第1段）'}


@pytest.mark.parametrize('fallback', ['missing', 'empty'])
def test_absent_or_empty_fallback_does_not_certify_feasibility(fallback):
    at = render('render_window_browser', fallback=fallback)
    captions = ' '.join(x.value for x in at.caption)
    assert '所有窗口硬约束可行' not in captions
    assert ('未保存' if fallback == 'missing' else '不等于完整物理可行性证明') in captions


def test_fallback_rows_do_not_overcount_windows():
    at = render('render_window_browser', fallback='events')
    assert any('2 条' in x.value and '1 个窗口' in x.value for x in at.warning)


def test_end_of_day_window_reports_its_actual_saved_length():
    at = render('render_window_browser', [value_row(start=95, tau=95)])
    captions = ' '.join(x.value for x in at.caption)
    assert '共 1 段、15 分钟' in captions
    assert '配置前瞻 2 小时' in captions
    assert '2小时前瞻' not in captions
    shown = next(x.value for x in at.dataframe if '轨迹来源' in x.value)
    assert '参考回退原因' in shown and '窗口末参考状态' in shown


def test_empty_window_archive_never_claims_no_fallback():
    at = render('render_window_browser', [], fallback='missing', current_semantics=False)
    assert any('未保存' in x.value for x in at.caption)
    assert any('没有窗口记录' in x.value for x in at.info)
    assert not any('0 个窗口' in x.value for x in at.caption)
