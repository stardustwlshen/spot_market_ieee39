from pathlib import Path
import sys
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]

def app(tmp_path):
    text=f'''import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,{str(ROOT/'app')!r})
from common import page_setup
import series_workspace_ui as ui
ui.ROOT=Path({str(tmp_path)!r})
page_setup('出清任务测试')
# A developer's saved legacy scene is not this test's current-config fixture.
# Scope the shared scenario module override to this render, including reruns.
with patch.object(ui.sc,'SCEN_DIR',Path({str(tmp_path)!r})/'scenarios'):
    ui.render_series_workspace()
'''
    (tmp_path/'pages').symlink_to(ROOT/'app/pages',target_is_directory=True)
    entry=tmp_path/'app.py';entry.write_text(text)
    return AppTest.from_file(str(entry),default_timeout=30).run()

def button(at,label):return next(x for x in at.button if x.label==label)

def test_create_new_task_no_auto_bids_or_solve(tmp_path):
    at=app(tmp_path);assert not at.exception
    next(x for x in at.number_input if x.label=='模拟几天').set_value(2)
    button(at,'创建模拟').click().run();assert not at.exception
    # AppTest's global tab list is depth-first and includes nested initial-state tabs.
    # Assert direct children of the main workflow container, then its nested types.
    workflow = next(block for block in at.main.children.values() if block.type == 'tab_container')
    steps = list(workflow.children.values())
    assert [tab.label for tab in steps] == ['1 每日场景','2 起始状态','3 每日报价','4 检查与运行','5 查看结果']
    assert [tab.label for tab in steps[1].get('tab')] == ['火电','电池','水电','抽蓄']
    assert not list(tmp_path.glob('series_tasks/*/launch.json'))
    assert any('未报价' in str(x.value) for x in at.dataframe)
    button(at,'检查输入').click().run();assert not at.exception
    assert button(at,'开始连续模拟').disabled
    assert any('引擎尚未安装' in x.value or '初态' in x.value for x in at.error)

def test_bid_explicit_copy_edit_confirm_reload(tmp_path):
    at=app(tmp_path);button(at,'创建模拟').click().run();assert not at.exception
    button(at,'生成这天的报价草稿').click().run();assert not at.exception
    assert any('待确认' in x.value for x in at.subheader)
    button(at,'确认该日全部机组报价').click().run();assert not at.exception
    assert any('已确认' in x.value for x in at.subheader)
    assert not list(tmp_path.glob('series_tasks/*/launch.json'))
    # Plain rerun preserves persisted independent quote, not a fresh default.
    at.run();assert not at.exception
    assert button(at,'确认该日全部机组报价').disabled

def test_save_day_separate_wind_pv_and_locks(tmp_path):
    at=app(tmp_path)
    next(x for x in at.selectbox if x.label=='任务输入方式').set_value('deterministic_known_inputs_v1')
    button(at,'创建模拟').click().run()
    next(x for x in at.number_input if x.label=='风电倍率').set_value(.75)
    button(at,'保存该日场景').click().run();assert not at.exception
    import json
    m=json.loads(next(tmp_path.glob('series_tasks/*/manifest.json')).read_text())
    assert m['days'][0]['wind_multiplier']==.75 and m['days'][0]['pv_multiplier']==1.
    assert m['days'][0]['locked']==['wind_multiplier']
    button(at,'随机生成所选天').click().run();assert not at.exception
    m=json.loads(next(tmp_path.glob('series_tasks/*/manifest.json')).read_text())
    assert m['days'][0]['wind_multiplier']==.75


def test_helper_isolates_and_preserves_legacy_default(tmp_path,monkeypatch):
    import yaml
    import pytest
    from smm import scenario as sc,series_workspace as ws,series_ui_adapter as adapter
    legacy_dir=tmp_path/'legacy_scenarios'
    legacy_dir.mkdir()
    monkeypatch.setattr(sc,'SCEN_DIR',legacy_dir)
    legacy=sc.create_scenario(sc.DEFAULT)
    units_path=legacy/'config/units.yaml'
    units=yaml.safe_load(units_path.read_text())
    for row in units['hydro']:row.pop('commitment',None)
    units_path.write_text(yaml.safe_dump(units,allow_unicode=True))
    before=units_path.read_bytes()
    old_task=ws.create_task(tmp_path/'legacy_tasks',legacy,title='旧初态仍拒绝',
        start_date='2026-07-15',day_count=1,timezone='Asia/Shanghai',root_seed='1')
    with pytest.raises(ValueError,match='声明水电逐台初态'):
        adapter.research_initial(old_task,ws.load_task(old_task))
    isolated=tmp_path/'ui';isolated.mkdir()
    at=app(isolated)
    button(at,'创建模拟').click().run()
    assert not at.exception
    workflow=next(block for block in at.main.children.values() if block.type=='tab_container')
    assert [tab.label for tab in list(workflow.children.values())[1].get('tab')]==['火电','电池','水电','抽蓄']
    created=next((isolated/'series_tasks').iterdir())
    assert Path(ws.load_task(created)['source']['path'])==isolated/'scenarios'/sc.DEFAULT
    assert units_path.read_bytes()==before
    assert sc.SCEN_DIR==legacy_dir  # The render did not leak its module override.
