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
