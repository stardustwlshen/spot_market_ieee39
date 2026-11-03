"""Chinese business workflow is readable without exposing machine schemas."""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scene(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'app'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    for filename in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
        shutil.copy2(ROOT / 'config' / filename, sc.scenario_path('默认') / 'config' / filename)
    return sc


def item(elements, label):
    return next(x for x in elements if x.label == label)


def test_cost_offer_comparison_preserves_unknown_and_internal_input(scene):
    from smm.config import load_config
    from smm.bids import load_bids
    from input_workflow import cost_bid_rows
    cfg = load_config(root=scene.scenario_path('默认'))
    bid = load_bids(cfg)
    before = copy.deepcopy(bid)
    rows = cost_bid_rows(cfg, bid, ['C1', 'ES1'])
    assert any(r['项目'] == '单次启动' and r['单位'] == '元/次' for r in rows)
    assert any(r['项目'] == '充电报价' and '不重复' in r['固定核算成本'] for r in rows)
    assert bid == before
    (cfg.root / 'config/fixed_costs.yaml').unlink()
    missing = cost_bid_rows(cfg, bid, ['C1'])
    assert missing[0]['固定核算成本'] == '未知'
    assert not any(r['固定核算成本'] == 0 for r in missing)


def test_input_preview_business_tables_are_chinese_and_technical_json_collapsed(scene):
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    assert all(any(step in x.value for x in at.markdown) for step in ('核对固定成本', '形成主体报价', '校验并保存', '冻结输入并运行'))
    assert '随机种子策略平移（非成本依据）' in item(at.radio, '报价生成依据').options
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    preview = next(x.value for x in at.dataframe if '预览价格 元/MWh' in x.value)
    assert set(preview['曲线']) == {'发电报价'}
    comparison = next(x.value for x in at.dataframe if '固定核算成本' in x.value)
    assert '当前报价' in comparison and '单位' in comparison
    details = item(at.expander, '技术详情：原始研究档案、来源与文件指纹')
    assert not details.proto.expanded
    assert len(details.json) == 1
    assert not scene.RUNS_DIR.exists()


def test_run_invalid_json_is_chinese_and_retains_saved_draft(scene):
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=30).run()
    assert not at.exception
    assert 'C1 · 燃煤' in item(at.selectbox, '报价机组/电站').options
    advanced = item(at.expander, '技术详情：高级报价文本编辑')
    assert not advanced.proto.expanded
    area = advanced.text_area[0]
    original = json.loads(area.value)
    area.set_value('{invalid json').run()
    item(at.button, '校验并保存所选主体草稿').click().run()
    assert not at.exception
    assert any('报价未保存，原草稿保持不变' in x.value for x in at.error)
    assert all('Expecting' not in x.value and 'Traceback' not in x.value for x in at.error)
    raw = item(at.expander, '技术详情（原始诊断，供排查）')
    assert not raw.proto.expanded and 'Expecting' in raw.code[0].value
    key = area.key.rsplit(':', 2)[0].removeprefix('entry:')
    assert at.session_state[key]['units']['C1'] == original
    assert not scene.RUNS_DIR.exists()


def test_changed_capacity_blocks_run_without_stack_and_keeps_input_repair_available(scene):
    units = scene.read_yaml('默认', 'units')
    units['thermal'][0]['pmax'] -= 1
    scene.write_yaml('默认', 'units', units)
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=30).run()
    assert not at.exception
    assert any('当前报价暂不能用于运行' in x.value for x in at.error)
    assert not any(x.label == '▶ 开始运行' for x in at.button)
