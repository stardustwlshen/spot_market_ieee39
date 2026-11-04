"""Opt-in new defaults are discoverable, do not migrate scenes, and reject replay."""
import sys

import pytest
from streamlit.testing.v1 import AppTest

from smm.config import ROOT, load_config
from smm.bids import load_bids
from smm import scenario as sc

sys.path.insert(0, str(ROOT / 'app'))


def item(elements, label):
    return next(x for x in elements if x.label == label)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def old_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    root = sc.ensure_default()
    base = sc.read_yaml('默认', 'base')
    base.pop('forecast', None)
    sc.write_yaml('默认', 'base', base)
    units = sc.read_yaml('默认', 'units')
    units['renewable'] = [u for u in units['renewable'] if u['id'] not in {'PV2', 'PV3'}]
    sc.write_yaml('默认', 'units', units)
    (root / 'data/profile_overrides.csv').write_text('old uploaded curve\n')
    (root / 'current_quote.json').write_text('{"old_user_quote": true}\n')
    frozen = sc.RUNS_DIR / '默认/frozen/summary.json'
    frozen.parent.mkdir(parents=True)
    frozen.write_text('{"historical": true}\n')
    return root


def app():
    return AppTest.from_string('from common import scenario_selector\nname = scenario_selector()\n', default_timeout=30).run()


def test_new_default_entry_creates_switches_and_preserves_old_files(old_scene):
    before, runs = snapshot(old_scene), snapshot(sc.RUNS_DIR)
    at = app()
    assert not at.exception
    assert any(x.label == '从新版默认配置新建场景（保留旧场景）' for x in at.expander)
    assert any('新版默认：光伏 2152 MW' in x.value and 'multiscale_v2' in x.value for x in at.caption)
    assert any('当前场景：光伏 652 MW' in x.value and 'legacy_v1' in x.value for x in at.caption)
    assert sc.list_scenarios() == ['默认']  # Opening the control is read-only.
    item(at.text_input, '新版场景名称').set_value('我的新版')
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and not at.error
    assert item(at.selectbox, '当前场景').value == '我的新版'
    assert at.session_state['scenario'] == '我的新版'
    assert any('旧场景' in x.value for x in at.success)
    cfg = load_config(root=sc.scenario_path('我的新版'))
    assert cfg.base['forecast']['model'] == 'multiscale_v2'
    assert sum(u['cap'] for u in cfg.units['renewable'] if u['type'] == 'pv') == 2152
    assert {'PV2', 'PV3'} <= load_bids(cfg)['units'].keys()
    assert not (cfg.root / 'current_quote.json').exists()
    assert not (cfg.root / 'data/profile_overrides.csv').exists()
    assert snapshot(old_scene) == before and snapshot(sc.RUNS_DIR) == runs
    assert sc.scenario_meta('我的新版')['source'] == '仓库 config/'
    # Replay/double submit cannot create another copy or overwrite the first.
    saved = snapshot(cfg.root)
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and any('已存在' in x.value for x in at.error)
    assert snapshot(cfg.root) == saved and snapshot(old_scene) == before
    item(at.selectbox, '当前场景').set_value('默认').run()
    assert at.session_state['scenario'] == '默认'
    assert snapshot(old_scene) == before


@pytest.mark.parametrize('name', ['默认', '../bad', '', '   '])
def test_duplicate_or_invalid_name_never_overwrites(old_scene, name):
    before = snapshot(old_scene)
    at = app()
    item(at.text_input, '新版场景名称').set_value(name)
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and at.error
    assert snapshot(old_scene) == before
    assert sc.list_scenarios() == ['默认']
    assert at.session_state['scenario'] == '默认'


def test_restart_guard_disables_creation(old_scene):
    at = AppTest.from_string('from common import scenario_selector\nscenario_selector()\n')
    at.session_state['runtime_needs_restart'] = True
    at.run()
    assert not at.exception
    assert item(at.button, '创建并切换到新版场景').disabled
    assert sc.list_scenarios() == ['默认']


def test_invalid_old_config_can_still_create_new_without_repairing_old(old_scene):
    (old_scene / 'config/base.yaml').write_text('broken: [')
    before = snapshot(old_scene)
    at = app()
    assert not at.exception
    assert any('当前场景配置不能读取' in x.value for x in at.caption)
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and not at.error
    assert at.session_state['scenario'] == '新版高光伏'
    assert snapshot(old_scene) == before


def test_actual_run_page_can_create_select_and_navigate_back(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    old = sc.ensure_default()
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    assert not at.exception
    before = snapshot(old)  # The ordinary quote workspace has initialized its own draft.
    item(at.text_input, '新版场景名称').set_value('新版运行测试')
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and not at.error
    assert item(at.selectbox, '当前场景').value == '新版运行测试'
    assert any('PV2' in option for option in item(at.selectbox, '模拟机组').options)
    assert 'PV3' in at.session_state['quote_current:新版运行测试']['units']
    assert snapshot(old) == before
    item(at.selectbox, '当前场景').set_value('默认').run()
    assert not at.exception and at.session_state['scenario'] == '默认'
    assert snapshot(old) == before


def test_selector_also_supports_main_area_container(old_scene):
    at = AppTest.from_string('from common import scenario_selector\nscenario_selector(sidebar=False)\n').run()
    assert not at.exception
    item(at.button, '创建并切换到新版场景').click().run()
    assert not at.exception and at.session_state['scenario'] == '新版高光伏'
