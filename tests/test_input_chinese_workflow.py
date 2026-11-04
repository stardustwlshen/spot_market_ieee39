"""Chinese single-unit quotation workflow preserves cost and validation boundaries."""
import copy
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'app/pages/2_运行.py'


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


def test_preview_business_tables_are_chinese_and_do_not_run(scene):
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception
    assert not any(widget.label == '生成方式' for widget in at.radio)
    assert item(at.radio, '随机报价依据').options == ['当前报价', '成本报价']
    item(at.button, '按成本生成预览').click().run()
    assert not at.exception
    preview = next(x.value for x in at.dataframe if '预览报价（元/MWh）' in x.value)
    assert set(preview['曲线']) == {'发电报价'}
    assert any({'参数', '数值'} == set(x.value.columns) for x in at.dataframe)
    assert not at.json
    assert not scene.RUNS_DIR.exists()


def test_invalid_draft_is_chinese_preserved_and_blocks_save_and_start(scene):
    from smm.config import load_config
    from smm.bids import load_bids
    cfg = load_config(root=scene.scenario_path('默认'))
    saved = load_bids(cfg)
    key = f"bid_draft:默认:{saved['version']}"
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    draft = copy.deepcopy(saved)
    draft['units']['C1']['segments'][0]['price'] = -999999.
    at.session_state[key] = draft
    at.session_state['quote_source:默认'] = saved['version']
    at.run()
    assert not at.exception
    assert any('报价存在违规' in x.value for x in at.error)
    assert all('Traceback' not in x.value for x in at.error)
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert item(at.button, '▶ 开始运行').disabled
    assert at.session_state[key]['units']['C1']['segments'][0]['price'] == -999999.
    assert load_bids(cfg) == saved
    assert not scene.RUNS_DIR.exists()


def test_changed_capacity_blocks_run_and_keeps_repair_available(scene):
    units = scene.read_yaml('默认', 'units')
    units['thermal'][0]['pmax'] -= 1
    scene.write_yaml('默认', 'units', units)
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception
    assert any('报价存在违规' in x.value for x in at.error)
    assert item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    assert not item(at.button, '按成本生成预览').disabled
