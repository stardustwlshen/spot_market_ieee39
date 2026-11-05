"""Planned RT terminal penalties are neither emergency fallback nor settlement."""
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from value_views import terminal_policy_details, terminal_policy_note, terminal_value_note


def row(**changes):
    data = dict(window_start=88, tau=95, unit='ES1', kind='E', kind_label='储能SOC电量',
                value_native=0., value_yuan_per_mwh=0., state=101., state_da=100., delta=1.,
                state_reference=100., delta_from_reference=1., reference_source='day_ahead',
                value_source='day_ahead', reference_round=None, reference_fallback_reason=None,
                lower=100., upper=100., band_binding=False, band_slack=1., soft=True, fallback=False,
                terminal_policy='soft', planned_terminal_soft=True,
                terminal_penalty_yuan_per_discharge_mwh=1000., discharge_energy_factor=.9,
                deviation_discharge_mwh=.9, terminal_penalty_cost=900.)
    return dict(data, **changes)


def render(function='render_window_browser', rows=None, failures=None, *, trajectories=False):
    rows = [row()] if rows is None else rows
    summary = {'realtime': {'lookahead_h': 2}}
    if failures is not None:
        summary['intraday'] = {'failed_rounds': failures, 'failure_count': len(failures)}
    code = (f"import sys\nsys.path.insert(0,{str(ROOT / 'app')!r})\n"
            "import pandas as pd\nfrom common import period_labels\n"
            f"from value_views import {function}\n"
            f"R={{'rt_value_windows':pd.DataFrame({rows!r}), 'summary':{summary!r},"
            "'rt_fallbacks':pd.DataFrame(columns=['t','reason'])}\n"
            "R['da_value_curves']=pd.DataFrame([{'continuation_value_scope':'no_cross_day_residual'}])\n")
    if trajectories:
        code += "R['da_storage']=pd.DataFrame([{'unit':'ES1','period':95,'E_mwh':100.}])\n"
        code += "R['rt_storage']=pd.DataFrame([{'unit':'ES1','period':95,'E_mwh':101.}])\n"
    code += f"{function}(R,period_labels(96),'soft_policy')"
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception, [x.message for x in at.exception]
    return at


def captions(at):
    return ' '.join(x.value for x in at.caption)


def test_saved_policy_and_penalty_fields_are_shown_without_recomputing_or_mutating():
    frame = pd.DataFrame([row(), row(unit='PS1', discharge_energy_factor=10.,
                                   deviation_discharge_mwh=20., terminal_penalty_cost=20000.)])
    before = frame.copy(deep=True)
    shown = terminal_policy_details(frame)
    assert shown['实时日末政策'].tolist() == ['计划软目标', '计划软目标']
    assert shown['计罚偏差 可放电MWh'].tolist() == [.9, 20.]
    assert shown['日末偏差优化成本 元'].tolist() == [900., 20000.]
    pd.testing.assert_frame_equal(before, frame)


def test_legacy_or_partial_policy_does_not_infer_current_default_or_zero_cost():
    old = pd.DataFrame([{'unit': 'ES1', 'soft': True, 'fallback': True}])
    shown = terminal_policy_details(old)
    assert shown.iloc[0]['实时日末政策'] == '未记录'
    assert pd.isna(shown.iloc[0]['日末偏差优化成本 元'])
    assert '未明确记录' in terminal_policy_note(old)
    assert '部分记录' in terminal_policy_note(pd.DataFrame({'terminal_policy': ['soft', None, 'unknown']}))


@pytest.mark.parametrize('function', ['render_rt_values', 'render_window_browser'])
def test_planned_soft_is_not_reported_as_emergency_or_cash_settlement(function):
    at = render(function)
    text = captions(at)
    assert '从首次求解起' in text and '不是硬目标失败后的应急兜底' in text
    assert '双侧偏差' in text and '不足计成本' in text
    assert '优化成本不是实际结算付款' in text
    assert '不跨窗口累加为全天费用' in text and '存量不再乘时段长度' in text
    if function == 'render_rt_values':
        assert '日末额外延续价值为 0' in text
    assert not at.warning
    table = next(x.value for x in at.dataframe if '实时日末政策' in x.value)
    assert table.iloc[0]['计划日末软目标'] == '是'
    assert table.iloc[0]['日末偏差优化成本 元'] == 900.


def test_middle_band_is_distinct_from_terminal_soft_target():
    at = render(rows=[row(window_start=0, tau=7, planned_terminal_soft=False,
                         band_slack=0., soft=False, deviation_discharge_mwh=None, terminal_penalty_cost=None)])
    table = next(x.value for x in at.dataframe if '实时日末政策' in x.value)
    assert table.iloc[0]['计划日末软目标'] == '否'
    assert pd.isna(table.iloc[0]['日末偏差优化成本 元'])
    assert '中间参考带行不代表后缀终态预测' in captions(at)


def test_intraday_physical_failure_and_da_reference_are_separately_visible():
    failure = {'t': 12, 'phase': 'scuc', 'termination': 'infeasible', 'reason': 'physical_infeasible'}
    at = render(rows=[row(reference_fallback_reason='intraday_physical_infeasible')], failures=[failure])
    assert any('1 个日内失败轮次' in x.value and '未计为成功' in x.value for x in at.warning)
    failure_table = next(x.value for x in at.dataframe if 'phase' in x.value)
    shown = failure_table.iloc[0]
    assert shown['t'] == 12 and shown['phase'] == 'scuc'
    assert shown['求解终止原因'] == '不可行' and shown['reason'] == 'physical_infeasible'
    sources = next(x.value for x in at.dataframe if '参考回退原因' in x.value)
    assert sources.iloc[0]['轨迹来源'] == '日前'
    assert sources.iloc[0]['参考回退原因'] == 'intraday_physical_infeasible'


def test_hard_policy_and_terminal_zero_keep_separate_meanings():
    assert '原日末硬目标' in terminal_policy_note(pd.DataFrame([row(terminal_policy='hard', planned_terminal_soft=False)]))
    text = terminal_value_note({'da_value_curves': pd.DataFrame([{'continuation_value_scope': 'no_cross_day_residual'}])})
    assert '日末目标约束仍保留' not in text
    assert '软目标还是硬目标是不同口径' in text


def test_last_window_soft_target_not_labelled_hard_feasible_band():
    import json
    at = render(rows=[row(window_start=95)], trajectories=True)
    assert '对应已执行日末' in captions(at)
    figures = [json.loads(x.proto.spec) for x in at.get('plotly_chart')]
    names = [trace.get('name', '') for figure in figures for trace in figure['data']]
    assert 'τ 日末计罚目标（非硬等式）' in names
    assert 'τ 可行带' not in names


@pytest.mark.parametrize('policy,expected', [
    ({'mode': 'soft', 'penalty_yuan_per_discharge_mwh': 300}, '罚价 300 元/可放电MWh'),
    ({'mode': 'hard'}, '原硬目标政策'),
    (None, '历史场景缺少新日末政策配置时也保持hard'),
])
def test_anchor_caption_reads_actual_policy_without_changing_scene(policy, expected):
    code = (f"import sys\nsys.path.insert(0,{str(ROOT / 'app')!r})\n"
            "from smm.config import load_config\nfrom anchor_policy_controls import render_anchor_policy\n"
            "cfg=load_config()\ncfg.mode['realtime']['storage']['mode']='value'\n"
            "vm=cfg.mode['realtime'].setdefault('value_method',{})\n"
            f"vm['terminal_policy']={policy!r}\nrender_anchor_policy(cfg,'只读软政策探针',disabled=True)")
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception
    assert expected in captions(at)
    assert at.button[0].disabled
    if policy and policy['mode'] == 'soft':
        assert '日末延续价值仍为0' in captions(at)
        assert '不是市场规则或结算付款' in captions(at)
