"""Read-only archive selection must preserve hour identity and widget isolation."""
import hashlib
import json
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

from gd_provincial_page import _checked_saved_hour


def _fixture(tmp_path):
    output = tmp_path / 'day/live_market_sessions/attempt'
    path = output / 'pipeline/price/HOUR0.json'
    path.parent.mkdir(parents=True)
    h = dict(schema='gd.live_host.PRICE.hour.v3', hour=0, run_id='r0',
             branch=str(output / 'pipeline/dispatch'), rows=[dict(
                 resource='C1', technology='coal', direction='discharge', node='1',
                 start='2026-01-01T00:00:00', end='2026-01-01T01:00:00',
                 QDA_MWh='4', simulated_executed_MWh='5', deviation_MWh='1',
                 DA_hour_price='20', RT_hour_price='30', energy_amount_CNY='150',
             )])
    path.write_text(json.dumps(h), encoding='utf-8')
    export = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    status = dict(completed_windows=4, exit={'status':'complete_research'},
                  quarters=[dict(quarter=i,raw_prices={'1':30}) for i in range(4)],
                  hours=[h], hour_exports=[export])
    return output, status


def test_saved_selection_is_opt_in_and_not_the_live_widget(tmp_path):
    out, status = _fixture(tmp_path)
    found = dict(root=str(tmp_path), block_new_start=False, warnings=[], days=[], tasks=[
        dict(output=str(out), kind='RT', state='first_hour_saved', name='attempt', status=status)])
    app = AppTest.from_string(f"""
import sys,json
sys.path.insert(0,{str(APP)!r})
import gd_saved_tasks
from gd_provincial_page import _render_saved_jobs
from gd_settlement_panel import render_energy_hour
from unittest.mock import patch
found=json.loads({json.dumps(found)!r})
status=found['tasks'][0]['status']
# A live result for the same run must coexist with the read-only view.
render_energy_hour(status['hours'][0],export=status['hour_exports'][0])
with patch.object(gd_saved_tasks,'discover',lambda *args:found):
 _render_saved_jobs({str(tmp_path)!r},None)
""").run()
    app.selectbox(key='prov_saved_job_choice').select_index(1).run()
    assert not app.exception and not app.error
    assert app.checkbox(key='prov_saved_hour_details').value is False
    assert len([c for c in app.caption if '不会继续求解' in c.value]) == 0
    app.checkbox(key='prov_saved_hour_details').check().run()
    assert not app.exception and not app.error
    assert app.selectbox(key='prov_saved_hour').options == ['00:00–01:00']
    app.selectbox(key='energy_unit_r0_0saved').select('C1').run()
    assert not app.exception and not app.error
    assert app.selectbox(key='energy_unit_r0_0').value == '全部'
    assert any('不会继续求解' in c.value for c in app.caption)
    assert list(app.dataframe[-1].value['日前小时电价（元/MWh）']) == ['20']
    assert list(app.dataframe[-1].value['实时小时电价（元/MWh）']) == ['30']
    assert 'prov_day' not in app.session_state


def test_hour_drilldown_rejects_wrong_branch_or_changed_export(tmp_path):
    out, status = _fixture(tmp_path)
    hour, export = _checked_saved_hour(str(out),status,0)
    assert hour['run_id'] == 'r0'
    with pytest.raises(ValueError, match='任务或时段不匹配'):
        _checked_saved_hour(str(out.with_name('other')),status,0)
    Path(export['path']).write_text(json.dumps(hour) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='已核验的导出文件不一致'):
        _checked_saved_hour(str(out),status,0)
