"""A compact workflow must preserve input files and independent daily quotes."""
from pathlib import Path
import json
from test_series_workspace_ui import app,button


def item(elements,label):
    return next(e for e in elements if e.label==label)


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_create_panel_prioritizes_scene_date_and_days(tmp_path):
    at=app(tmp_path)
    assert not at.exception
    assert item(at.selectbox,'选择场景')
    assert item(at.number_input,'模拟几天')
    for label in ('更多设置（可选）','场景管理（可选）','更多工具'):
        assert not item(at.expander,label).proto.expanded
    management=item(at.expander,'场景管理（可选）')
    assert button(management,'另建新版默认方案')
    assert item(management.text_input,'新方案名称')
    assert item(item(at.expander,'更多设置（可选）').text_input,'模拟名称')
    assert not list(tmp_path.glob('series_tasks/*/manifest.json'))


def test_browsing_units_and_network_before_quotes_is_readonly(tmp_path):
    at=app(tmp_path)
    item(at.number_input,'模拟几天').set_value(2)
    button(at,'创建模拟').click().run()
    assert not at.exception
    assert not item(at.expander,'调整当天场景（可选）').proto.expanded
    assert not item(at.expander,'起始状态 · 待确认').proto.expanded
    before=snapshot(tmp_path/'series_tasks')
    for uid in ('H1','ES1','PS1','C1'):
        at.selectbox(key='series_bid_unit').select(uid).run()
        assert not at.exception
        assert any('最大' in c.value or '机组台数' in c.value for c in at.caption)
    at.selectbox(key='series_peer_unit').select('PV1').run()
    assert not at.exception
    assert any(s.value=='PV1 · 机组信息' for s in at.subheader)
    assert not any(r.label=='输入图显示' for r in at.radio)
    assert not any(s.label=='输入曲线口径' for s in at.selectbox)
    days=at.selectbox(key='series_edit_date').options
    at.selectbox(key='series_edit_date').select(days[1]).run()
    at.selectbox(key='series_edit_date').select(days[0]).run()
    at.run()
    assert not at.exception
    assert snapshot(tmp_path/'series_tasks')==before
    m=json.loads(next(tmp_path.glob('series_tasks/*/manifest.json')).read_text())
    assert all(d['bid_package'] is None for d in m['days'])
    assert not list(tmp_path.glob('series_tasks/*/launch.json'))


def test_daily_quotes_stay_independent_and_unit_selection_survives(tmp_path):
    at=app(tmp_path)
    item(at.number_input,'模拟几天').set_value(2)
    button(at,'创建模拟').click().run()
    at.selectbox(key='series_bid_unit').select('ES1').run()
    button(at,'生成这天的报价草稿').click().run()
    assert not at.exception
    assert at.selectbox(key='series_bid_unit').value=='ES1'
    button(at,'确认该日全部机组报价').click().run()
    root=next((tmp_path/'series_tasks').iterdir())
    before=json.loads((root/'manifest.json').read_text())
    day1,day2=[d['date'] for d in before['days']]
    at.selectbox(key='series_edit_date').select(day2).run()
    assert not at.exception
    assert not any(b.label=='确认该日全部机组报价' for b in at.button)
    button(at,'生成这天的报价草稿').click().run()
    assert not at.exception
    after=json.loads((root/'manifest.json').read_text())
    assert after['days'][0]==before['days'][0]
    assert after['days'][1]['bid_package'] is not None
    assert after['days'][1]['bid_receipt'] is None
    at.selectbox(key='series_edit_date').select(day1).run()
    assert button(at,'确认该日全部机组报价').disabled
    assert not list(root.glob('launch.json'))


def test_switching_tasks_uses_frozen_unit_parameters_and_own_quotes(tmp_path):
    from unittest.mock import patch
    from smm import scenario as sc
    at=app(tmp_path)
    button(at,'创建模拟').click().run()
    first=at.selectbox(key='series_task').value
    button(at,'生成这天的报价草稿').click().run()
    with patch.object(sc,'SCEN_DIR',tmp_path/'scenarios'):
        sc.create_scenario('alternate',source=sc.DEFAULT)
        units=sc.read_yaml('alternate','units')
        unit=next(u for u in units['thermal'] if u['id']=='C1')
        old_ramp=unit['ramp']
        unit['ramp']=old_ramp+1
        sc.write_yaml('alternate','units',units)
    at.run()
    at.selectbox(key='series_create_source').select('alternate').run()
    button(at,'创建模拟').click().run()
    assert not at.exception
    second=at.selectbox(key='series_task').value
    assert second!=first
    button(at,'生成这天的报价草稿').click().run()
    before=snapshot(tmp_path/'series_tasks')
    for selected,expected in ((first,old_ramp),(second,old_ramp+1),(first,old_ramp)):
        at.selectbox(key='series_task').select(selected).run()
        assert not at.exception
        at.selectbox(key='series_bid_unit').select('C1').run()
        assert any('爬坡率（MW/min；水电为每台）：'+str(expected) in c.value for c in at.caption)
    assert snapshot(tmp_path/'series_tasks')==before
    assert not list(tmp_path.glob('series_tasks/*/launch.json'))
