"""Saved water lookahead is a forecast, never realized cash or a reachability proof."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from common import get_result
from value_views import hydro_lookahead_details


def forecast(**changes):
    return dict(dict(window_start=0, tau=7, unit='H1', reference_source='intraday',
                     reference_source_label='intraday@0', reference_round=0, reference_fallback_reason=None,
                     state=17000., reference_recovery_volume=900., minimum_window_end_volume=17100.,
                     terminal_target=18000., projected_terminal_volume=17900.,
                     actual_projected_shortfall=100., projected_terminal_shortfall=100.,
                     discharge_mwh_per_native_unit=2.5, projected_terminal_shortfall_mwh=250.,
                     penalty_yuan_per_discharge_mwh=300., penalty_cost=75000.,
                     forecast_only=True, active=True, slack_zero_means_target_met=True), **changes)


def archive(tmp_path, rows=None):
    rows = [forecast()] if rows is None else rows
    pd.DataFrame(rows).to_csv(tmp_path / 'rt_hydro_terminal_lookahead.csv', index=False)
    (tmp_path / 'summary.json').write_text(json.dumps({'realtime': {'lookahead_h': 2}}))
    return tmp_path


def render(directory, *, function='render_hydro_terminal_lookahead', window=None):
    code = (f"import sys\nsys.path.insert(0,{str(ROOT / 'app')!r})\n"
            "from common import get_result,period_labels\n"
            f"from value_views import {function}\n"
            f"R=get_result({{'dir':{str(directory)!r},'mode':'full'}})\n")
    code += (f"{function}(R,'hydro_probe',window_start={window!r})" if function == 'render_hydro_terminal_lookahead'
             else f"{function}(R,period_labels(96),'hydro_probe')")
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception, [x.message for x in at.exception]
    return at


def captions(at):
    return ' '.join(x.value for x in at.caption)


def test_loader_reads_new_csv_and_does_not_mutate_saved_values(tmp_path):
    archive(tmp_path)
    file = tmp_path / 'rt_hydro_terminal_lookahead.csv'
    before = hashlib.sha256(file.read_bytes()).hexdigest()
    loaded = get_result({'dir': str(tmp_path), 'mode': 'full'})['rt_hydro_terminal_lookahead']
    assert loaded.iloc[0]['penalty_cost'] == 75000.
    assert loaded.iloc[0]['projected_terminal_volume'] == 17900.
    assert hashlib.sha256(file.read_bytes()).hexdigest() == before


def test_missing_and_empty_forecast_files_are_distinct_and_not_zero(tmp_path):
    metadata = {'dir': str(tmp_path), 'mode': 'full'}
    assert get_result(metadata)['rt_hydro_terminal_lookahead'] is None
    at = render(tmp_path)
    assert '未记录水电参考续程前瞻' in captions(at)
    assert not at.dataframe
    pd.DataFrame(columns=['window_start', 'unit', 'penalty_cost']).to_csv(
        tmp_path / 'rt_hydro_terminal_lookahead.csv', index=False)
    assert get_result(metadata)['rt_hydro_terminal_lookahead'].empty
    at = render(tmp_path)
    assert '文件已保存' in captions(at) and '不补造零短缺或零成本' in captions(at)
    assert not at.dataframe


def test_forecast_csv_updates_and_removals_invalidate_result_cache(tmp_path):
    archive(tmp_path)
    metadata = {'dir': str(tmp_path), 'mode': 'full'}
    assert get_result(metadata)['rt_hydro_terminal_lookahead'].iloc[0].penalty_cost == 75000.
    archive(tmp_path, [forecast(penalty_cost=65000.)])
    assert get_result(metadata)['rt_hydro_terminal_lookahead'].iloc[0].penalty_cost == 65000.
    (tmp_path / 'rt_hydro_terminal_lookahead.csv').unlink()
    assert get_result(metadata)['rt_hydro_terminal_lookahead'] is None


def test_table_preserves_sources_fallbacks_and_separate_units_without_recomputation():
    frame = pd.DataFrame([forecast(), forecast(unit='H2', reference_source='day_ahead',
                     reference_source_label='day_ahead', reference_round=None,
                     reference_fallback_reason='missing_Qout', penalty_cost=12.345)])
    before = frame.copy(deep=True)
    shown = hydro_lookahead_details(frame)
    assert shown['参考来源'].tolist() == ['日内（轮次起点：第1段）', '日前']
    assert shown['参考回退原因'].tolist() == ['无（已记录）', 'missing_Qout']
    assert shown['本窗口前瞻优化成本 元'].tolist() == [75000., 12.345]
    assert shown['模型前瞻短缺松弛 万m³'].tolist() == [100., 100.]
    assert shown['模型前瞻短缺 可放电MWh'].tolist() == [250., 250.]
    pd.testing.assert_frame_equal(frame, before)


def test_missing_forecast_fields_are_not_filled_from_active_scene_or_zero():
    shown = hydro_lookahead_details(pd.DataFrame([{'unit': 'H1', 'reference_source': 'intraday'}]))
    assert shown.iloc[0]['参考来源'] == '日内（轮次见记录）'
    assert shown.iloc[0]['参考回退原因'] == '未记录'
    assert pd.isna(shown.iloc[0]['本窗口前瞻优化成本 元'])
    assert pd.isna(shown.iloc[0]['参考续程预计日末库容 万m³'])
    assert pd.isna(shown.iloc[0]['日内轮次起点时段'])


def test_saved_forecast_is_not_labelled_executed_cost_or_joint_feasibility(tmp_path):
    at = render(archive(tmp_path, [forecast(), forecast(window_start=1, tau=8, penalty_cost=80000.)]))
    text = captions(at)
    assert '不是已执行末态' in text and '梯级联合可达性的证明' in text
    assert '不跨窗口加总前瞻优化成本' in text and '不作为实际成本或市场结算付款' in text
    assert '存量不再乘时段长度' in text and '日末延续价值仍为0' in text
    assert '软阈值不是硬物理库容下限' in text and '不重复添加该项' in text
    assert len(at.dataframe) == 1
    assert at.dataframe[0].value['本窗口前瞻优化成本 元'].tolist() == [75000., 80000.]
    assert not at.metric


def test_inactive_zero_slack_not_mislabelled_target_met(tmp_path):
    at = render(archive(tmp_path, [forecast(active=False, slack_zero_means_target_met=False,
                     projected_terminal_shortfall=0., projected_terminal_shortfall_mwh=0., penalty_cost=0.)]))
    assert '不能仅凭零松弛认定预计目标满足' in captions(at)
    shown = at.dataframe[0].value.iloc[0]
    assert shown['前瞻项启用'] == '否' and shown['零松弛可判预测目标满足'] == '否'
    assert shown['按预计库容计算的不足 万m³'] == 100.


def test_window_filter_uses_only_saved_matching_row_and_no_fake_terminal_zero(tmp_path):
    path = archive(tmp_path, [forecast(), forecast(window_start=1, tau=8, penalty_cost=80000.)])
    at = render(path, window=1)
    assert at.dataframe[0].value['窗口起点时段'].tolist() == [1]
    assert at.dataframe[0].value['本窗口前瞻优化成本 元'].tolist() == [80000.]
    at = render(path, window=88)
    assert not at.dataframe
    assert '本窗口没有对应记录' in captions(at) and '由原日末目标接管' in captions(at)


def test_unfilterable_archive_discloses_missing_window_instead_of_misassigning(tmp_path):
    row = forecast()
    del row['window_start']
    at = render(archive(tmp_path, [row]), window=1)
    assert any('无法筛出当前窗口' in x.value for x in at.warning)
    assert pd.isna(at.dataframe[0].value.iloc[0]['窗口起点时段'])


@pytest.mark.parametrize('function', ['render_rt_values', 'render_window_browser'])
def test_existing_rt_views_include_separate_forecast_table_from_saved_file(tmp_path, function):
    archive(tmp_path)
    pd.DataFrame([dict(window_start=0, tau=7, unit='H1', kind='V', kind_label='水电库容',
        value_native=0., value_yuan_per_mwh=0., state=17000., state_da=17100., delta=-100.,
        state_reference=17100., delta_from_reference=-100., reference_source='intraday@0',
        value_source='intraday@0', reference_round=0, reference_fallback_reason=None,
        lower=16900., upper=17300., band_binding=False, band_slack=0., soft=False, fallback=False,
        terminal_policy='soft', planned_terminal_soft=False)]).to_csv(tmp_path / 'rt_value_windows.csv', index=False)
    at = render(tmp_path, function=function)
    assert any('参考续程预计日末短缺' in x.value for x in at.markdown)
    shown = next(x.value for x in at.dataframe if '本窗口前瞻优化成本 元' in x.value)
    assert shown.iloc[0]['本窗口前瞻优化成本 元'] == 75000.
    assert any('实时日末政策' in x.value for x in at.dataframe)
