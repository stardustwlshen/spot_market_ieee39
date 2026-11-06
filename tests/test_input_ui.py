"""Single-unit workspace presentation and non-mutating navigation."""
import hashlib
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'app/pages/2_运行.py'


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*')
            if p.is_file() and p.name not in {'current_quote.json', 'current_quote.lock'}}


def item(elements, label):
    return next(x for x in elements if x.label == label)


@pytest.fixture
def app(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'app'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    sc.create_scenario('测试副本', source=sc.DEFAULT)
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception
    return at, tmp_path


def test_single_unit_workspace_has_information_and_no_unrelated_editors(app):
    at, _ = app
    assert at.title[0].value == '单机报价模拟'
    assert 'C1 · 燃煤' in item(at.selectbox, '模拟机组').options
    assert any(x.value == 'C1 · 机组信息' for x in at.subheader)
    assert any(x.value == '市场运行情况' for x in at.subheader)
    assert [x.label for x in at.tabs] == ['机组与报价', '系统接线图与全网']
    assert not at.text_area  # No raw JSON editor in the everyday workflow.
    assert not any(x.label in {'负荷缩放系数（倍）', '保存机组/储能/水电参数'}
                   for x in [*at.number_input, *at.button])
    assert any('技术与运行约束' in x.value for x in at.markdown)
    assert any('独立核算成本' in x.value for x in at.markdown)


def test_edits_reset_and_navigation_do_not_write_or_start(app):
    at, root = app
    before = snapshot(root)
    assert not any(widget.label == '报价来源' for widget in at.selectbox)
    initial = item(at.number_input, '空载报价（元/h）').value
    item(at.number_input, '空载报价（元/h）').set_value(initial + 100).run()
    item(at.button, '恢复该机组原报价').click().run()
    assert not at.exception
    assert item(at.number_input, '空载报价（元/h）').value == initial
    assert not any(button.label == '另存报价版本' for button in at.button)
    for uid in ['ES1', 'PS1', 'W1', 'C1']:
        item(at.selectbox, '模拟机组').select(uid).run()
        assert not at.exception
        assert any(x.value == f'{uid} · 机组信息' for x in at.subheader)
    for scenario in ['测试副本', '默认']:
        item(at.selectbox, '当前场景').select(scenario).run()
        assert not at.exception
        assert at.session_state['scenario'] == scenario
    assert snapshot(root) == before
    assert not (root / 'runs').exists()


def test_every_unit_physical_parameter_is_losslessly_available_without_editing(app):
    from smm.config import load_config
    from smm import scenario as sc
    from unit_input_view import field_rows, unit_information_sections
    at, root = app
    before = snapshot(root)
    cfg = load_config(root=sc.scenario_path('默认'))
    for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie'):
        for unit in cfg.units.get(family, []):
            sections = unit_information_sections(cfg, unit['id'])
            rows = [row for title, records in sections.items() for row in records
                    if title in {'主体信息', '技术与运行约束', '初始状态', '季节约束',
                                 '技术档案费用参数（不是独立核算成本）'}]
            actual = {row['原始字段']: row['数值'] for row in rows}
            expected = {row['原始字段']: row['数值'] for row in field_rows(unit)}
            assert expected.items() <= actual.items()
    assert snapshot(root) == before
