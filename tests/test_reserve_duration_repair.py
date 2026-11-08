"""Fresh scenes keep 0.25; only a missing key can be inserted, and only after a backup."""
from pathlib import Path
import sys

import pytest
import yaml
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'src'))


def _yaml():
    parser = YAML()
    parser.preserve_quotes = True
    parser.width = 4096
    return parser


def _set_duration(root, value, *, present=True):
    path = root / 'config' / 'modes.yaml'
    document = _yaml().load(path.read_text(encoding='utf-8'))
    base = yaml.safe_load((root / 'config' / 'base.yaml').read_text(encoding='utf-8'))
    reserve = document[base['rule_set']]['reserve']
    if present:
        reserve['T_dur_h'] = value
    else:
        reserve.pop('T_dur_h', None)
    path.write_text('', encoding='utf-8')
    _yaml().dump(document, path)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    created = sc.ensure_default()
    return sc, created


def test_fresh_default_uses_real_loader_and_keeps_template_duration(scene):
    sc, root = scene
    from smm.config import load_config
    from common import scen_config
    loaded = load_config(root=root)
    assert loaded.mode['reserve']['T_dur_h'] == 0.25
    assert loaded.mode['intraday']['enabled'] is False
    assert scen_config(sc.DEFAULT).mode['reserve']['T_dur_h'] == 0.25
    assert not (root / '.unit_edit_pending.json').exists()


def test_missing_duration_is_explicit_and_repair_inserts_only_that_key(scene):
    sc, root = scene
    from smm.config import load_config
    from smm.reserve_duration_repair import inspect_scene_duration, repair_missing_reserve_duration
    original = yaml.safe_load((root / 'config' / 'modes.yaml').read_text(encoding='utf-8'))
    _set_duration(root, None, present=False)
    info = inspect_scene_duration(root)
    assert info['state'] == '缺键' and info['repair_allowed'] is True
    assert '状态 缺键' in info['message'] and '当前值 None' in info['message']
    with pytest.raises(ValueError, match='状态 缺键'):
        load_config(root=root)
    backup = repair_missing_reserve_duration(root)
    restored = yaml.safe_load(backup.read_text(encoding='utf-8'))
    assert 'T_dur_h' not in restored['MKT']['reserve']
    loaded = load_config(root=root)
    assert loaded.mode['reserve']['T_dur_h'] == 0.25
    assert loaded.mode['intraday']['enabled'] is False
    after = yaml.safe_load((root / 'config' / 'modes.yaml').read_text(encoding='utf-8'))
    assert after['MKT']['reserve']['fixed_up_mw'] == original['MKT']['reserve']['fixed_up_mw']
    assert after['MKT']['intraday']['enabled'] is original['MKT']['intraday']['enabled']
    assert not inspect_scene_duration(root)['repair_allowed']


@pytest.mark.parametrize('value,state', [(0.0, '已有值'), ('0.25', '已有值'), (None, '显式空值'), (float('nan'), '已有值')])
def test_existing_illegal_duration_is_not_rewritten(scene, value, state):
    sc, root = scene
    from smm.reserve_duration_repair import inspect_scene_duration, repair_missing_reserve_duration
    _set_duration(root, value, present=True)
    before = (root / 'config' / 'modes.yaml').read_bytes()
    info = inspect_scene_duration(root)
    assert info['state'] == state and info['repair_allowed'] is False
    assert '当前值' in info['message'] and '类型' in info['message']
    with pytest.raises(ValueError, match='未改配置'):
        repair_missing_reserve_duration(root)
    assert (root / 'config' / 'modes.yaml').read_bytes() == before
    assert not (root / 'config' / 'backups').exists()


def test_custom_positive_duration_is_kept(scene):
    sc, root = scene
    from smm.config import load_config
    from smm.reserve_duration_repair import repair_missing_reserve_duration
    _set_duration(root, 0.5, present=True)
    before = (root / 'config' / 'modes.yaml').read_bytes()
    assert load_config(root=root).mode['reserve']['T_dur_h'] == 0.5
    with pytest.raises(ValueError, match='未改配置'):
        repair_missing_reserve_duration(root)
    assert (root / 'config' / 'modes.yaml').read_bytes() == before


def test_pending_unit_edit_blocks_duration_repair(scene):
    sc, root = scene
    from smm.reserve_duration_repair import repair_missing_reserve_duration
    _set_duration(root, None, present=False)
    journal = root / '.unit_edit_pending.json'
    journal.write_text('{"config/modes.yaml": {"old": null, "new": ""}}', encoding='utf-8')
    before_modes = (root / 'config' / 'modes.yaml').read_bytes()
    before_journal = journal.read_bytes()
    with pytest.raises(ValueError, match='未完成的机组保存'):
        repair_missing_reserve_duration(root)
    assert journal.read_bytes() == before_journal
    assert (root / 'config' / 'modes.yaml').read_bytes() == before_modes


def test_quote_step_can_repair_only_a_missing_duration(scene, monkeypatch):
    sc, root = scene
    _set_duration(root, None, present=False)
    from streamlit.testing.v1 import AppTest
    from test_single_day_workspace import item
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    details = [error.value for error in at.error]
    details.extend(code.value for code in at.code)
    for expander in at.expander:
        details.extend(code.value for code in expander.code)
    assert any('状态 缺键' in text and '当前值 None' in text for text in details)
    assert item(at.button, '备份并补上缺失的备用持续时间 0.25 小时')
    item(at.button, '备份并补上缺失的备用持续时间 0.25 小时').click().run()
    assert not at.exception
    assert not any('场景配置不能读取' in error.value for error in at.error)
    from smm.config import load_config
    assert load_config(root=root).mode['reserve']['T_dur_h'] == 0.25
    assert list((root / 'config' / 'backups').glob('modes-before-missing-tdur-*.yaml'))
