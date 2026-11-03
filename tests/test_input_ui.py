"""Business input presentation and non-mutating navigation, using actual Streamlit app."""
import ast
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'app/pages/1_边界条件.py'


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def app(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'app'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    sc.create_scenario('测试副本', source=sc.DEFAULT)
    at = AppTest.from_file(str(PAGE), default_timeout=30).run()
    assert not at.exception
    return at, tmp_path


def item(elements, label):
    return next(x for x in elements if x.label == label)


def test_business_labels_and_advanced_disclosure(app):
    at, _ = app
    assert at.title[0].value == '输入设置'
    assert [x.label for x in at.tabs] == ['场景', '负荷', '新能源', '主体报价', '设备参数', '电网限额', '模型规则', '运行设置', '抽水蓄能']
    advanced = [x for x in at.expander if x.label.startswith('高级：')]
    assert len(advanced) == 2
    assert all(not x.proto.expanded for x in advanced)
    assert any('不是账户权限设置' in x.value for x in at.info)
    assert any('不是独立备用市场' in x.value for x in at.caption)
    assert item(at.selectbox, '选择报价主体').options[0].endswith('（燃煤）')


def test_edits_discard_and_navigation_do_not_write_or_start(app):
    at, root = app
    before = snapshot(root)
    item(at.selectbox, '基于报价版本').select(1).run()
    assert not at.exception
    initial = item(at.number_input, '空载费 元/h').value
    item(at.number_input, '空载费 元/h').set_value(initial + 100).run()
    assert not at.exception
    item(at.button, '放弃修改，重新载入').click().run()
    assert not at.exception
    assert item(at.number_input, '空载费 元/h').value == initial
    assert item(at.selectbox, '基于报价版本').value == 1
    for uid in ['ES1', 'PS1', 'W1', 'C1']:
        item(at.selectbox, '选择报价主体').select(uid).run()
        assert not at.exception
    item(at.number_input, '负荷缩放系数（倍）').set_value(0.7).run()
    for scenario in ['测试副本', '默认']:
        item(at.selectbox, '当前场景').select(scenario).run()
        assert not at.exception
        assert at.session_state['scenario'] == scenario
    assert snapshot(root) == before
    assert not (root / 'runs').exists()


def test_line_capacity_display_includes_global_multiplier(app):
    at, _ = app
    from smm import scenario as sc
    base = sc.read_yaml('默认', 'base')
    base['line_capacity_multiplier'] = 1.2
    sc.write_yaml('默认', 'base', base)
    at.run()
    table = next(x.value for x in at.dataframe if '生效限额 MW' in x.value.columns)
    np.testing.assert_allclose(table['生效限额 MW'], (table.rate_mw * table['压缩系数'] * 1.2).round(1))


@pytest.mark.parametrize('bad', [-1, np.nan, np.inf, 'not-a-number'])
def test_invalid_profile_cannot_replace_saved_file(tmp_path, bad):
    # Execute the page's actual write boundary in isolation: model validation, then disk write.
    from smm.config import load_config
    from smm.forecast import generate_profiles
    cfg = load_config()
    raw = generate_profiles(cfg, cfg.base['seed'])
    path = tmp_path / 'profile_overrides.csv'
    path.write_text('original file must survive')
    errors = []
    class Stop(Exception):
        pass
    class UI:
        error = staticmethod(errors.append)
        @staticmethod
        def stop():
            raise Stop()
    # Existing input frame is valid; preserve original bytes if replacement fails validation.
    frame = pd.DataFrame({'period': range(cfg.T), 'load_actual': np.ones(cfg.T)})
    node = next(n for n in ast.parse(PAGE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'write_overrides')
    env = dict(pd=pd, np=np, ov_path=path, T=cfg.T, cfg=cfg, raw_prof=raw, st=UI, business_error=lambda summary, exc, hint: errors.append(summary + hint))
    original_read = pd.read_csv
    try:
        pd.read_csv = lambda *a, **kw: frame.copy()
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(PAGE), 'exec'), env)
        with pytest.raises(Stop):
            env['write_overrides']({'load_actual': [bad] * cfg.T})
    finally:
        pd.read_csv = original_read
    assert errors and '未保存' in errors[0]
    assert path.read_text() == 'original file must survive'
