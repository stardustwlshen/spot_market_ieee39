"""Read-only diagnosis and actual Streamlit archive controls."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))

from market_rule_profile import SICHUAN_V5
from sichuan_v5_archive_ui import archive_label, read_failure
from sichuan_v5_workflow import json_bytes, run


def test_real_workflow_failure_retains_original_exception_and_attached_id(tmp_path,monkeypatch):
    root=tmp_path/SICHUAN_V5.results_folder
    def forced(*_,**__):
        raise RuntimeError('日前求解失败（测试注入）')
    monkeypatch.setattr('sichuan_v5_workflow.day_ahead',forced)
    with pytest.raises(RuntimeError,match='日前求解失败') as exc:
        run(root,month=7,coal_offer=400.)
    name=exc.value.archive_name
    failed,inputs=read_failure(root,name)
    assert failed['automatic_restart'] is False and len(failed['input_sha256'])==64
    assert inputs['coal_offer']==400 and archive_label(root,name).startswith('未完成｜')
    assert not (root/name/'RT_EXECUTED.json').exists()
    p=root/name/'INPUT.json'
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='校验'):
        read_failure(root,name)


def test_archive_switch_is_readonly_and_never_refills_or_runs(tmp_path):
    root=tmp_path/SICHUAN_V5.results_folder
    name='scv5_20261122T023045Z_123456abcdef'
    path=root/name
    path.mkdir(parents=True)
    rules=SICHUAN_V5.identity()
    records={'FAILURE.json':{'rules':rules,'error':'求解器故障（演练）','failed_research':True,
                             'automatic_restart':False},
             'INPUT.json':{'rules':rules,'month':7,'coal_offer':400,'requested_periods':4}}
    for file,record in records.items():
        (path/file).write_bytes(json_bytes(record))
    originals={p.name:p.read_bytes() for p in path.iterdir()}
    app=AppTest.from_string(f"""
from pathlib import Path
import streamlit as st
from sichuan_v5_archive_ui import render_archive_picker, render_failed_archive
root=Path({str(root)!r})
chosen=render_archive_picker(root,latest_failure={name!r})
st.slider('当前报价',220,500,320,key='bid')
if chosen:
    render_failed_archive(root,chosen)
st.button('明确执行新任务',disabled=chosen is not None,key='run')
""").run()
    assert any('最近一次尝试未完成' in x.value for x in app.caption)
    app.selectbox(key='scv5_archive').select(name).run()
    assert any('求解器故障' in x.value for x in app.warning)
    assert any('400' in x.value for x in app.text)
    assert app.slider(key='bid').value==320 and app.button(key='run').disabled
    app.button(key='scv5_leave_archive').click().run()
    assert app.selectbox(key='scv5_archive').value is None and not app.button(key='run').disabled
    assert app.slider(key='bid').value==320 and not app.exception
    assert originals=={p.name:p.read_bytes() for p in path.iterdir()}


def test_malformed_and_half_written_archives_not_presented_as_diagnosed(tmp_path):
    root=tmp_path/SICHUAN_V5.results_folder
    name='scv5_20261122T023045Z_123456abcdef'
    path=root/name
    path.mkdir(parents=True)
    assert archive_label(root,name).startswith('待核验｜')
    assert read_failure(root,name) is None
    (path/'FAILURE.json').write_text('{}')
    with pytest.raises(ValueError):
        read_failure(root,name)


def test_actual_two_run_picker_disambiguates_scenarios_and_preserves_archive(tmp_path,monkeypatch):
    import json
    import market_rule_profile as profile
    monkeypatch.setattr(profile.MarketProfile,'default_results',lambda self,_:tmp_path/self.results_folder)
    root=tmp_path/SICHUAN_V5.results_folder
    page=Path(__file__).resolve().parents[1]/'app/四川.py'
    def start():
        return AppTest.from_file(str(page),default_timeout=100).run()
    app=start()
    for month,quote,load_factor in [(5,355,.97),(6,410,1.02)]:
        app.selectbox(key='scv5_month').select(month)
        app.number_input(key='scv5_bid').set_value(quote)
        app.slider(key='scv5_load').set_value(load_factor)
        app.button(key='scv5_run').click().run()
        assert not app.exception and not app.error
    snapshots={json.loads((d/'INPUT.json').read_bytes())['coal_offer']:d for d in root.iterdir()}
    before={d.name:{f.name:f.read_bytes() for f in d.iterdir()} for d in snapshots.values()}
    fresh=start()
    labels=fresh.selectbox(key='scv5_archive').options
    assert any('首小时｜5月 · C1 355元/MWh · 负荷×0.97' in x for x in labels)
    assert any('首小时｜6月 · C1 410元/MWh · 负荷×1.02' in x for x in labels)
    fresh.selectbox(key='scv5_archive').select(snapshots[355].name).run()
    assert any('355 元/MWh' in c.value for c in fresh.caption)
    assert any('本任务参与方式：' in c.value and '报量不报价' in c.value for c in fresh.caption)
    assert fresh.number_input(key='scv5_bid').value==320 and fresh.button(key='scv5_run').disabled
    assert before=={d.name:{f.name:f.read_bytes() for f in d.iterdir()} for d in root.iterdir()}
    f=snapshots[410]/'RECEIPT.json'
    wrong=json.loads(f.read_bytes())
    wrong['scenario_hint']['coal_offer']=355
    f.write_bytes(json_bytes(wrong))
    fresh.selectbox(key='scv5_archive').select(snapshots[410].name).run()
    assert any('任务摘要与已校验输入不一致' in e.value for e in fresh.error)


def test_archive_label_collision_retains_selectable_identity(tmp_path):
    root=tmp_path/SICHUAN_V5.results_folder
    names=['scv5_20261122T023045Z_111111abcdef','scv5_20261122T023045Z_222222abcdef']
    for name in names:
        (root/name).mkdir(parents=True)
    app=AppTest.from_string(f"""
from pathlib import Path
from sichuan_v5_archive_ui import render_archive_picker
render_archive_picker(Path({str(root)!r}))
""").run()
    w=app.selectbox(key='scv5_archive')
    assert len(set(w.options))==3
    for n in names:
        assert app.selectbox(key='scv5_archive').select(n).run().selectbox(key='scv5_archive').value==n
