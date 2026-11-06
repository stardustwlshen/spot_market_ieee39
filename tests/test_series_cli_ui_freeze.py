"""CLI/direct-worker freeze state must match persistence guards. No solver calls."""
from copy import deepcopy
from pathlib import Path
import pytest
from smm import series_workspace as ws, series_ui_adapter as adapter
from test_series_workspace_ui import app, button

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_execution_allowed(monkeypatch):
    from smm import series
    def reject(*args, **kwargs):
        raise AssertionError('This UI freeze regression must not launch or solve')
    monkeypatch.setattr(adapter,'launch',reject)
    monkeypatch.setattr(series,'run_series',reject)


def prepared_task(tmp_path):
    root=ws.create_task(tmp_path/'series_tasks',ROOT,title='CLI冻结回归',
        start_date='2026-07-15',day_count=2,timezone='Asia/Shanghai',root_seed='7')
    m=ws.load_task(root)
    m['initial_state']=adapter.research_initial(root,m)
    m['initial_state_source']='research_assumption'
    ws.atomic_json(root/'manifest.json',m)
    return root,m


@pytest.mark.parametrize('marker',['run/series_identity.json','launch.json'])
def test_run_marker_freezes_ui_and_backend(tmp_path,marker):
    root,m=prepared_task(tmp_path)
    p=root/marker;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    (root/'run/day_2026-07-15').mkdir(parents=True,exist_ok=True)
    before=(root/'manifest.json').read_bytes()
    at=app(tmp_path)
    assert not at.exception
    assert ws._begun(root)
    if marker.startswith('run/'):
        assert not (root/'launch.json').exists()
    for label in ('保存该日场景','随机生成所选天','把这天场景复制过去',
                  '校验并保存初态','校验并保存完整初态',
                  '复制基础方案报价到这天','生成这天的报价草稿'):
        assert button(at,label).disabled,label
    assert next(x for x in at.selectbox if x.label=='输入与预测方式').disabled
    assert next(x for x in at.text_area if x.label=='完整研究初态 JSON').disabled
    assert next(x for x in at.checkbox if x.label=='起始时已开机').disabled
    assert any('初态已冻结' in x.value for x in at.info)
    changed=deepcopy(m);changed['days'][0]['load_multiplier']=1.1
    with pytest.raises(ValueError,match='已冻结'):
        ws.update_task(root,changed,expected_revision=m['revision'])
    assert (root/'manifest.json').read_bytes()==before


def test_cli_identity_keeps_only_future_day_bids_editable(tmp_path):
    root,m=prepared_task(tmp_path)
    (root/'run/day_2026-07-15').mkdir(parents=True)
    (root/'run/series_identity.json').write_text('{}')
    at=app(tmp_path)
    next(x for x in at.selectbox if x.label=='当前编辑日期').set_value('2026-07-16').run()
    assert not at.exception
    assert button(at,'保存该日场景').disabled
    assert button(at,'校验并保存初态').disabled
    assert not button(at,'生成这天的报价草稿').disabled
    button(at,'生成这天的报价草稿').click().run()
    assert not at.exception
    saved=ws.load_task(root)
    assert saved['days'][0]==m['days'][0]
    assert saved['days'][1]['bid_package'] is not None
    assert saved['days'][1]['bid_receipt'] is None
    assert not button(at,'确认该日全部机组报价').disabled
    assert not (root/'launch.json').exists()


def test_unstarted_editable_unconfirmed_cannot_launch(tmp_path):
    root,m=prepared_task(tmp_path)
    at=app(tmp_path)
    assert not at.exception and not ws._begun(root)
    for label in ('保存该日场景','随机生成所选天','校验并保存初态','校验并保存完整初态'):
        assert not button(at,label).disabled,label
    assert not next(x for x in at.selectbox if x.label=='输入与预测方式').disabled
    assert not next(x for x in at.text_area if x.label=='完整研究初态 JSON').disabled
    next(x for x in at.number_input if x.label=='风电倍率').set_value(.75)
    button(at,'保存该日场景').click().run()
    assert not at.exception and ws.load_task(root)['days'][0]['wind_multiplier']==.75
    button(at,'生成这天的报价草稿').click().run()
    assert not at.exception
    assert ws.bid_status(ws.load_task(root)['days'][0])=='待确认'
    button(at,'检查输入').click().run()
    assert not at.exception
    assert button(at,'开始连续模拟').disabled
    assert any('unconfirmed' in x.value for x in at.error)
    assert not (root/'launch.json').exists() and not (root/'run').exists()
