"""Real Streamlit interaction coverage for forms, cancel, save and navigation."""
import json
import shutil
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

from smm.config import ROOT, load_config
from smm.unit_editor import TYPES
from test_unit_editor import scene, input_bytes

sys.path.insert(0, str(ROOT / 'app'))


def item(elements, label):
    return next(x for x in elements if x.label == label)


def app(scene):
    return AppTest.from_string(f'''
from pathlib import Path
import streamlit as st
from smm.config import load_config
from bid_workspace import get_scene_working_draft, persist_scene_working_draft
from unit_editor import render_unit_editor
cfg = load_config(root=Path({str(scene)!r}))
get_scene_working_draft(st.session_state, '测试', cfg)
persist_scene_working_draft(st.session_state, '测试', cfg)
render_unit_editor(cfg, '测试')
''', default_timeout=30).run()


@pytest.mark.parametrize('kind', TYPES)
def test_add_all_types_check_then_save(scene, kind):
    at = app(scene)
    assert not at.exception
    item(at.radio, '机组操作').set_value('新增机组').run()
    item(at.selectbox, '新增类型').set_value(kind).run()
    item(at.text_input, '新机组编号（不能重复）').set_value('UI_'+kind)
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    before = input_bytes(scene)
    item(at.button, '检查参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene) == before
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert any('已保存' in x.value for x in at.success)
    saved = json.loads((scene / 'current_quote.json').read_bytes())
    assert 'UI_'+kind in saved['draft']['units']
    assert at.session_state['quote_current:测试']['units']['UI_'+kind] == saved['draft']['units']['UI_'+kind]


def test_cancel_does_not_save_and_reopen_does_not_replay_old_widgets(scene):
    at = app(scene)
    before = input_bytes(scene)
    item(at.button, '核对技术参数').click().run()
    item(at.number_input, '最大功率（MW）').set_value(99.)
    item(at.button, '取消编辑').click().run()
    assert not at.exception and input_bytes(scene) == before
    item(at.button, '核对技术参数').click().run()
    assert item(at.number_input, '最大功率（MW）').value == 646.


def test_copy_duplicate_invalid_and_capacity_save_require_explicit_reset(scene):
    at = app(scene)
    item(at.radio, '机组操作').set_value('复制机组').run()
    item(at.selectbox, '机组模板').set_value('ES1').run()
    item(at.text_input, '新机组编号（不能重复）').set_value('ES1')
    item(at.button, '核对技术参数').click().run()
    before = input_bytes(scene)
    item(at.button, '保存机组').click().run()
    assert not at.exception and any('已存在' in x.value for x in at.error)
    assert input_bytes(scene) == before
    item(at.button, '取消编辑').click().run()
    item(at.text_input, '新机组编号（不能重复）').set_value('ES_COPY')
    item(at.button, '核对技术参数').click().run()
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert any(u['id'] == 'ES_COPY' for u in load_config(root=scene).units['storage'])
    item(at.radio, '机组操作').set_value('编辑现有机组').run()
    item(at.selectbox, '要编辑的机组').set_value('C1').run()
    item(at.button, '核对技术参数').click().run()
    item(at.number_input, '最大功率（MW）').set_value(700.)
    before = input_bytes(scene)
    item(at.button, '保存机组').click().run()
    assert not at.exception and any('当前报价' in x.value for x in at.error)
    assert input_bytes(scene) == before
    item(at.checkbox, '仅为此机组重新生成模拟报价（旧报价完整备份）').check()
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert load_config(root=scene).units['thermal'][0]['pmax'] == 700.


def test_other_page_updates_preserved_on_save(scene):
    at = app(scene)
    item(at.button, '核对技术参数').click().run()
    path = scene / 'config/base.yaml'
    path.write_text(path.read_text() + '\n# concurrent change\n')
    before = input_bytes(scene)
    item(at.button, '保存机组').click().run()
    assert not at.exception and any('另一页面' in x.value for x in at.error)
    assert input_bytes(scene) == before


@pytest.mark.parametrize('broken', ['bad_yaml', 'invalid_efficiency', 'missing_key'])
def test_old_invalid_config_is_friendly_and_preserved(tmp_path, monkeypatch, broken):
    from smm import scenario as sc
    import yaml
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    root = sc.ensure_default()
    p = root / 'config/units.yaml'
    if broken == 'bad_yaml':
        p.write_text('storage: [\n')
    else:
        units = yaml.safe_load(p.read_text())
        if broken == 'invalid_efficiency': units['storage'][0]['eta_d'] = 1.2
        else: del units['thermal'][0]['ramp']
        p.write_text(yaml.safe_dump(units))
    before = input_bytes(root)
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    assert not at.exception
    assert any('原文件已保留' in x.value for x in at.error)
    assert any('场景' in x.label for x in at.sidebar.selectbox)
    assert input_bytes(root) == before


def test_actual_main_page_save_refreshes_quote_and_later_cancel_is_readonly(tmp_path, monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    root = sc.ensure_default()
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    item(at.radio, '机组操作').set_value('新增机组').run()
    item(at.selectbox, '新增类型').set_value('storage').run()
    item(at.text_input, '新机组编号（不能重复）').set_value('MAIN_NEW')
    item(at.button, '核对技术参数').click().run()
    item(at.button, '保存机组').click().run()
    assert not at.exception
    assert any('MAIN_NEW' in x for x in item(at.selectbox, '模拟机组').options)
    assert 'MAIN_NEW' in at.session_state['quote_current:默认']['units']
    before = input_bytes(root)
    item(at.button, '核对技术参数').click().run()
    item(at.button, '取消编辑').click().run()
    assert not at.exception and input_bytes(root) == before


def test_runtime_restart_flag_disables_mutations_without_busy_claim(tmp_path, monkeypatch):
    monkeypatch.setenv('IEEE39_LAUNCH_ROOT', str(tmp_path / 'other-checkout'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60)
    at.session_state['runtime_needs_restart'] = True
    at.run()
    assert not at.exception
    assert item(at.button, '核对技术参数').disabled
    assert item(at.button, '▶ 开始运行').disabled
    assert not any('已有后台运行' in x.value for x in at.info)
    assert any('重启' in x.value for x in at.info)


def test_interrupted_save_can_be_explicitly_recovered_in_main_page(tmp_path, monkeypatch):
    import base64
    from smm import scenario as sc
    from smm.unit_edit_transaction import JOURNAL
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    root = sc.ensure_default()
    path = root / 'config/units.yaml'
    before = path.read_bytes()
    after = before + b'\n# interrupted update\n'
    (root / JOURNAL).write_text(json.dumps({'config/units.yaml': {
        'old': base64.b64encode(before).decode(), 'new': base64.b64encode(after).decode()}}))
    path.write_bytes(after)
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    assert not at.exception and path.read_bytes() == after
    item(at.button, '恢复未完成的机组保存').click().run()
    assert not at.exception and path.read_bytes() == before
    assert not (root / JOURNAL).exists()
    assert item(at.button, '核对技术参数')


def test_invalid_legacy_node_can_be_repaired_without_widget_crash(scene):
    import yaml
    path = scene / 'config/units.yaml'
    units = yaml.safe_load(path.read_bytes())
    units['thermal'][0]['bus'] = 999
    path.write_text(yaml.safe_dump(units))
    at = app(scene)
    assert not at.exception
    item(at.selectbox, '要编辑的机组').set_value('C1').run()
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    item(at.selectbox, '接入节点').set_value(31)
    item(at.button, '保存机组').click().run()
    assert not at.exception and not at.error
    assert load_config(root=scene).units['thermal'][0]['bus'] == 31
