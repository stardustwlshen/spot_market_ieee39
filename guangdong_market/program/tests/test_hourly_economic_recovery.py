"""Use the real export bridge and Streamlit UI, not a stubbed error result."""
import hashlib
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1] / 'app'


def saved_pair(path, hour, *, money='100', resource='G1'):
    start, end = [f'2026-01-01T{i:02d}:00:00' for i in (hour, hour+1)]
    execution = path / f'EXECUTION_H{hour}.json'
    execution.write_text(json.dumps(dict(
        schema='gd.live_host.execution_view.v3', run_id='fixture', origin='2026-01-01T00:00:00',
        cells=list(range(hour*4, (hour+1)*4)), executed_end=(hour+1)*4,
        rt_clock_mode='provincial_15min_execution', values={}, source_pins={},
    )))
    result = dict(schema='gd.live_host.PRICE.hour.v3', run_id='fixture', hour=hour,
                  executed_cells=4, input_binding={'RT_physical_interval_minutes': 15},
                  source_pins={str(execution): hashlib.sha256(execution.read_bytes()).hexdigest()},
                  rows=[dict(resource=resource, node='1', technology='coal', direction='discharge',
                             start=start, end=end, QDA_MWh='4', simulated_executed_MWh='5', deviation_MWh='1',
                             DA_hour_price='20', RT_hour_price='30', energy_amount_CNY=money)])
    p = path / f'HOUR{hour}.json'
    p.write_text(json.dumps(result))
    return result, dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def run_ui(pairs):
    hours, exports = zip(*pairs)
    status = dict(hours=list(hours), hour_exports=list(exports), completed_windows=4*len(hours))
    return AppTest.from_string(f"""
import sys,json
sys.path.insert(0,{str(ROOT)!r})
from gd_full_day_ui import render_status
render_status(json.loads({json.dumps(status)!r}))
""").run()


def test_rejected_saved_economics_reports_source_and_keeps_hour_tab_usable(tmp_path):
    valid = saved_pair(tmp_path, 0)
    rejected = saved_pair(tmp_path, 1, money='NaN')
    app = run_ui([valid, rejected])
    assert not app.exception
    assert any('01:00–02:00' in m.value and 'nonfinite economics input' in m.value for m in app.error)
    assert not app.dataframe  # no total across a rejected hour
    app.radio(key='full_day_result_mode').set_value('分小时').run()
    assert not app.exception
    assert any('本小时经济性数据不完整' in m.value for m in app.warning)
    assert '发电成本缺失' in app.dataframe[-2].value['模拟收益数据缺项'][0] or '已执行启停成本缺失' in app.dataframe[-2].value['模拟收益数据缺项'][0]
    app.selectbox(key='full_day_hour').select_index(1).run()
    assert not app.exception
    assert any('nonfinite economics input' in m.value for m in app.error)
    assert not any("'rows'" in m.value for m in app.error)
    details = app.dataframe[-1].value
    assert list(details['实时电量（MWh）']) == ['5']
    assert list(details['日前小时电价（元/MWh）']) == ['20']
    assert list(details['实时小时电价（元/MWh）']) == ['30']
    assert '电能量收入（元）' not in details


def test_partial_cumulative_never_treats_a_missing_unit_hour_as_zero(tmp_path):
    app = run_ui([saved_pair(tmp_path, 0), saved_pair(tmp_path, 1, resource='G2')])
    assert not app.exception
    assert any('不按零补齐' in m.value for m in app.warning)
    table = app.dataframe[-1].value
    assert set(table['机组']) == {'G1','G2'}
    assert table['电能量收入（元）'].isna().all()
    assert table['模拟收益（月度分摊前，元）'].isna().all()


def test_partial_cumulative_can_report_component_known_for_all_hours(tmp_path):
    app = run_ui([saved_pair(tmp_path, 0), saved_pair(tmp_path, 1)])
    assert not app.exception
    assert any('不按零补齐' in m.value for m in app.warning)
    table = app.dataframe[-1].value
    assert str(table['电能量收入（元）'][0]) == '200'
    assert table['模拟收益（月度分摊前，元）'].isna().all()
