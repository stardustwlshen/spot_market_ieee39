"""Regression coverage for recoverable invalid bids and version-isolated run drafts."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'app'))
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    return sc


def item(elements, label):
    return next(x for x in elements if x.label == label)


def test_equipment_change_keeps_bid_repair_page_accessible(scenario):
    from smm.config import load_config
    from smm.bids import bid_dir
    cfg = load_config(root=scenario.scenario_path('默认'))
    before = {p.name: p.read_bytes() for p in bid_dir(cfg).glob('*.json')}
    units = scenario.read_yaml('默认', 'units')
    units['thermal'][0]['pmax'] -= 1
    scenario.write_yaml('默认', 'units', units)
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    assert item(at.button, '提交为新版本').disabled
    assert item(at.button, '保存机组/储能/水电参数')
    assert any('违规' in x.value for x in at.error)
    assert {p.name: p.read_bytes() for p in bid_dir(cfg).glob('*.json')} == before


def test_latest_resolves_version_without_destroying_previous_draft(scenario):
    from smm.config import load_config
    from smm.bids import load_bids, save_bids
    cfg = load_config(root=scenario.scenario_path('默认'))
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=30).run()
    assert not at.exception
    old = load_bids(cfg)['version']
    key = f'bid_draft:默认:{old}'
    original = at.session_state[key]
    original['note'] = 'unsaved draft must survive'
    save_bids(cfg, load_bids(cfg), note='new saved version')
    new = load_bids(cfg)['version']
    at.run()
    assert not at.exception
    assert item(at.selectbox, '报价版本').value == '最新'
    assert at.session_state[f'bid_draft:默认:{new}']['version'] == new
    assert at.session_state[key]['note'] == 'unsaved draft must survive'
    item(at.selectbox, '报价版本').select(old).run()
    assert not at.exception
    assert at.session_state[key]['note'] == 'unsaved draft must survive'


@pytest.mark.parametrize('cap', [None, 0.0, 1200.0])
def test_hydro_total_outflow_cap_roundtrip_and_source(scenario, cap):
    units = scenario.read_yaml('默认', 'units')
    units['hydro'][0]['qout_max'] = None
    units['hydro'][0]['qout_max_source'] = ''
    scenario.write_yaml('默认', 'units', units)
    page = str(ROOT / 'app/pages/1_边界条件.py')
    at = AppTest.from_file(page, default_timeout=30).run()
    assert not at.exception
    label = '总出库流量上限（m³/s；留空=未建模）'
    assert item(at.number_input, label).value is None
    before = (scenario.scenario_path('默认') / 'config/units.yaml').read_bytes()
    item(at.number_input, label).set_value(cap).run()
    source = '合成研究假设：仅供测试，非真实工程资料'
    item(at.text_input, '总出库上限来源 / 研究假设说明').set_value(source).run()
    assert not at.exception
    assert (scenario.scenario_path('默认') / 'config/units.yaml').read_bytes() == before
    assert any('发电过机流量 + 弃水流量' in x.value for x in at.caption)
    item(at.button, '保存机组/储能/水电参数').click().run()
    assert not at.exception
    saved = scenario.read_yaml('默认', 'units')['hydro'][0]
    assert saved['qout_max'] == cap
    assert saved['qout_max_source'] == source
    at = AppTest.from_file(page, default_timeout=30).run()
    assert not at.exception
    assert item(at.number_input, label).value == cap
    assert item(at.text_input, '总出库上限来源 / 研究假设说明').value == source
    item(at.number_input, label).set_value(None).run()
    item(at.button, '保存机组/储能/水电参数').click().run()
    assert not at.exception
    assert scenario.read_yaml('默认', 'units')['hydro'][0]['qout_max'] is None


@pytest.mark.parametrize('uniform', [False, True])
def test_startup_editor_modes_preserve_unit_heterogeneity_and_archives(scenario, uniform):
    import json
    from smm.bids import bid_dir, load_bids
    from smm.config import load_config
    modes = scenario.read_yaml('默认', 'modes')
    modes['MKT']['startup_cost_mode'] = 'uniform_per_unit' if uniform else 'state_dependent'
    scenario.write_yaml('默认', 'modes', modes)
    cfg = load_config(root=scenario.scenario_path('默认'))
    directory = bid_dir(cfg)
    before = {p.name: p.read_bytes() for p in directory.glob('*.json')}
    original = load_bids(cfg)
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    table = next(x.value for x in at.dataframe if 'pmin' in x.value.columns)
    scalar_label = '单次启动报价（元/次）'
    if uniform:
        assert '单次启动费上限（元/次）' in table
        assert not {'启动费_冷', '启动费_温', '启动费_热'} & set(table)
        assert table['单次启动费上限（元/次）'].nunique() > 1
        first = item(at.number_input, scalar_label).value
        item(at.number_input, scalar_label).set_value(first - 1).run()
        item(at.selectbox, '选择报价主体').select('CCGT').run()
        assert item(at.number_input, scalar_label).value == original['units']['CCGT'].get('startup_cost_yuan', original['units']['CCGT']['startup_cost']['hot'])
        item(at.selectbox, '选择报价主体').select('C1').run()
        assert item(at.number_input, scalar_label).value == first - 1
        assert {p.name: p.read_bytes() for p in directory.glob('*.json')} == before
        item(at.button, '提交为新版本').click().run()
        assert not at.exception
        new = load_bids(cfg)
        assert new['units']['C1']['startup_cost_yuan'] == first - 1
        assert new['units']['C1']['startup_cost'] == original['units']['C1']['startup_cost']
        assert new['units']['CCGT'] == original['units']['CCGT']
        assert all((directory / p).read_bytes() == content for p, content in before.items())
    else:
        assert {'启动费_冷', '启动费_温', '启动费_热'} <= set(table)
        assert not any(x.label == scalar_label for x in at.number_input)
        assert {p.name: p.read_bytes() for p in directory.glob('*.json')} == before


def test_research_bid_preview_requires_acceptance_and_preserves_manual_units(scenario):
    import copy
    import shutil
    shutil.copy2(ROOT / 'config/bid_cost_basis.yaml', scenario.scenario_path('默认') / 'config/bid_cost_basis.yaml')
    shutil.copy2(ROOT / 'config/fixed_costs.yaml', scenario.scenario_path('默认') / 'config/fixed_costs.yaml')
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    key = f"draft_默认_{item(at.selectbox, '基于报价版本').value}"
    initial = copy.deepcopy(at.session_state[key])
    bid_files = {p: p.read_bytes() for p in scenario.scenario_path('默认').rglob('bids_v*.json')}
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    assert at.session_state[key] == initial
    assert at.session_state[key + ':preview']['candidate']['_cost_basis']
    item(at.button, '采用此预览为草稿（仍未保存）').click().run()
    assert not at.exception
    assert at.session_state[key]['units']['C1'] != initial['units']['C1']
    assert at.session_state[key]['units']['CCGT'] == initial['units']['CCGT']
    assert not at.session_state.get(key + ':manual', [])
    assert all(p.read_bytes() == data for p, data in bid_files.items())
    current = item(at.number_input, '空载费 元/h').value
    item(at.number_input, '空载费 元/h').set_value(current + 1).run()
    assert 'C1' in at.session_state[key + ':manual']
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    assert any('受保护' in x.value for x in at.error)
    assert key + ':preview' not in at.session_state
    assert all(p.read_bytes() == data for p, data in bid_files.items())


def test_research_preview_missing_local_worksheet_fails_without_fallback(scenario):
    path = scenario.scenario_path('默认') / 'config/bid_cost_basis.yaml'
    path.unlink(missing_ok=True)
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    assert any('未生成预览' in x.value and '不回退' in x.value for x in at.error)


def test_strategy_preview_cannot_overwrite_newer_manual_edits(scenario):
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    item(at.radio, '报价生成依据').set_value('seed策略平移（非成本依据）').run()
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    assert not item(at.button, '采用此预览为草稿（仍未保存）').disabled
    item(at.number_input, '空载费 元/h').set_value(item(at.number_input, '空载费 元/h').value + 1).run()
    assert not at.exception
    assert item(at.button, '采用此预览为草稿（仍未保存）').disabled
    assert any('预览已过期' in x.value for x in at.warning)


def test_fixed_operating_cost_edit_does_not_rewrite_bids_or_offer_basis(scenario):
    import shutil
    import yaml
    root = scenario.scenario_path('默认')
    shutil.copy2(ROOT / 'config/fixed_costs.yaml', root / 'config/fixed_costs.yaml')
    shutil.copy2(ROOT / 'config/bid_cost_basis.yaml', root / 'config/bid_cost_basis.yaml')
    before = {p: p.read_bytes() for p in root.rglob('bids_v*.json')}
    basis = (root / 'config/bid_cost_basis.yaml').read_bytes()
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not item(at.button, '采用此预览为草稿（仍未保存）').disabled
    item(at.number_input, '固定边际成本参数（元/MWh发电）').set_value(281.0).run()
    item(at.button, '仅保存该主体运行成本（不改报价）').click().run()
    assert not at.exception
    assert item(at.button, '采用此预览为草稿（仍未保存）').disabled
    profile = yaml.safe_load((root / 'config/fixed_costs.yaml').read_text())
    assert profile['units']['C1']['scalar_marginal_cost_yuan_per_mwh'] == 281
    assert profile['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == 297
    assert not {'sha256', 'available', 'input_file'} & set(profile)
    assert all(p.read_bytes() == data for p, data in before.items())
    assert (root / 'config/bid_cost_basis.yaml').read_bytes() == basis
    fixed_bytes = (root / 'config/fixed_costs.yaml').read_bytes()
    item(at.radio, '报价生成依据').set_value('seed策略平移（非成本依据）').run()
    item(at.multiselect, '生成报价的主体').set_value(['C1']).run()
    item(at.button, '生成报价预览（不采用、不保存）').click().run()
    assert not at.exception
    item(at.button, '采用此预览为草稿（仍未保存）').click().run()
    assert not at.exception
    assert (root / 'config/fixed_costs.yaml').read_bytes() == fixed_bytes
    assert all(p.read_bytes() == data for p, data in before.items())


def test_missing_fixed_costs_are_unknown_and_incomplete_save_is_blocked(scenario):
    root = scenario.scenario_path('默认')
    path = root / 'config/fixed_costs.yaml'
    path.unlink(missing_ok=True)
    at = AppTest.from_file(str(ROOT / 'app/pages/1_边界条件.py'), default_timeout=30).run()
    assert not at.exception
    assert item(at.number_input, '固定边际成本参数（元/MWh发电）').value is None
    assert any('成本及利润为未知，不是0' in x.value for x in at.info)
    item(at.text_area, '运行成本来源 / 假设说明').set_value('显式研究参数，非真实电厂').run()
    item(at.button, '仅保存该主体运行成本（不改报价）').click().run()
    assert not at.exception
    assert any('运行成本未保存' in x.value for x in at.error)
    assert not path.exists()
