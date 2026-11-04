"""Browsing preserves exact bid semantics; only explicit edits change model inputs."""
import copy
import json

import pytest
from streamlit.testing.v1 import AppTest

from test_input_recovery_ui import scenario, item, files, PAGE


def setup_app(scenario, mutate=None, uid='C1'):
    from smm.config import load_config
    from smm.bids import load_bids
    cfg = load_config(root=scenario.scenario_path('默认'))
    draft = load_bids(cfg)
    if mutate:
        mutate(draft)
    key = f"bid_draft:默认:{draft['version']}"
    initial = copy.deepcopy(draft)
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state[key] = draft
    at.session_state['quote_source:默认'] = draft['version']
    at.session_state['quote_unit:默认'] = uid
    before = files(cfg.root)
    at.run()
    assert not at.exception
    return at, 'quote_current:默认', initial, before


def exact(value):
    # JSON comparison preserves missing/null and treats unchanged NaN as equal.
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize('variant', ['normal', 'missing', 'explicit_null'])
def test_all_unit_navigation_preserves_exact_draft_and_archives(scenario, variant):
    def mutate(draft):
        for uid, unit in draft['units'].items():
            for curve in ('segments', 'dis_segments', 'ch_segments', 'gen_segments', 'pump_segments'):
                for segment in unit.get(curve, []):
                    segment['source_note'] = {'unit': uid, 'research': True}
        if variant in ('missing', 'explicit_null'):
            draft['units']['C2'].pop('noload_cost', None)
            for uid, field in [('ES1', 'soc_end'), ('PS1', 'vup_end')]:
                if variant == 'missing':
                    draft['units'][uid].pop(field, None)
                else:
                    draft['units'][uid][field] = None
    at, key, initial, before = setup_app(scenario, mutate)
    for uid in initial['units']:
        item(at.selectbox, '模拟机组').select(uid).run()
        assert not at.exception
        assert exact(at.session_state[key]) == exact(initial), uid
        if variant != 'normal' and uid == 'C2':
            assert item(at.number_input, '空载报价（元/h）').value == 6500.0
            assert 'noload_cost' not in at.session_state[key]['units'][uid]
        if variant == 'missing' and uid == 'PS1':
            assert item(at.number_input, '日末上库库容（万m³，日前硬约束）').value == 200.0
            assert 'vup_end' not in at.session_state[key]['units'][uid]
        if variant == 'explicit_null' and uid in ('PS1', 'ES1'):
            label = '申报日末上库库容' if uid == 'PS1' else '申报期末 SOC'
            assert not item(at.checkbox, label).value
    assert files(scenario.scenario_path('默认')) == before


def test_single_edit_launch_preserves_every_other_unit_and_omitted_field(scenario, monkeypatch):
    import common
    captured = []
    def start_run(*args, **kwargs):
        captured.append(copy.deepcopy(kwargs['bids']))
        return scenario.RUNS_DIR / 'captured-only'
    monkeypatch.setattr(common, 'start_run', start_run)
    def mutate(draft):
        draft['units']['C2'].pop('noload_cost', None)
        draft['units']['PS1'].pop('vup_end', None)
        draft['units']['ES1'].pop('soc_end', None)
        draft['units']['C1']['segments'][0]['source_note'] = 'retain segment metadata'
    at, key, initial, before = setup_app(scenario, mutate)
    for uid in initial['units']:
        item(at.selectbox, '模拟机组').select(uid).run()
    item(at.selectbox, '模拟机组').select('C1').run()
    amount = item(at.number_input, '空载报价（元/h）').value + 1
    item(at.number_input, '空载报价（元/h）').set_value(amount).run()
    assert not item(at.button, '▶ 开始运行').disabled
    item(at.button, '▶ 开始运行').click().run()
    assert not at.exception
    assert len(captured) == 1
    expected = copy.deepcopy(initial['units'])
    expected['C1']['noload_cost'] = amount
    assert exact(captured[0]['units']) == exact(expected)
    assert files(scenario.scenario_path('默认')) == before
    assert not scenario.RUNS_DIR.exists()


@pytest.mark.parametrize('invalid', [None, 'abc', float('nan')], ids=['null', 'text', 'nan'])
def test_invalid_noload_remains_exact_until_explicit_numeric_repair(scenario, invalid):
    def mutate(draft):
        draft['units']['C1']['noload_cost'] = invalid
    at, key, initial, before = setup_app(scenario, mutate)
    assert item(at.number_input, '空载报价（元/h）').value is None
    assert exact(at.session_state[key]) == exact(initial)
    assert any('原草稿未被替换' in e.value for e in at.error)
    assert item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    item(at.number_input, '空载报价（元/h）').set_value(6001.0).run()
    assert not at.exception
    assert at.session_state[key]['units']['C1']['noload_cost'] == 6001.0
    assert not item(at.button, '▶ 开始运行').disabled
    assert not any(button.label == '另存报价版本' for button in at.button)
    expected = copy.deepcopy(initial['units'])
    expected['C1']['noload_cost'] = 6001.0
    assert exact(at.session_state[key]['units']) == exact(expected)
    assert files(scenario.scenario_path('默认')) == before


@pytest.mark.parametrize('uid,mode', [('H1', 'planned'), ('ES1', 'quantity_only'),
                                     ('ES1', 'self_schedule'), ('PS1', 'quantity_only'),
                                     ('PS1', 'dispatch')])
def test_non_price_mode_requires_explicit_single_unit_conversion(scenario, uid, mode):
    def mutate(draft):
        draft['units'][uid]['mode'] = mode
        if mode == 'self_schedule':
            draft['units'][uid]['self_schedule'] = [0.0] * 96
    at, key, initial, before = setup_app(scenario, mutate, uid)
    assert exact(at.session_state[key]) == exact(initial)
    assert item(at.button, '按成本生成预览').disabled
    assert item(at.radio, '随机报价依据').disabled
    assert item(at.button, '生成随机报价预览').disabled
    assert any('未自动改变运行模式' in w.value for w in at.warning)
    # Streamlit data editors expose their disabled flag on the protobuf.
    editors = [e for e in at.dataframe if getattr(e.proto, 'editing_mode', 0)]
    assert editors
    assert all(e.proto.disabled for e in editors)
    item(at.selectbox, '模拟机组').select('C1').run()
    item(at.selectbox, '模拟机组').select(uid).run()
    assert exact(at.session_state[key]) == exact(initial)
    item(at.button, '切换该机组为报量报价').click().run()
    assert not at.exception
    changed = at.session_state[key]
    expected = copy.deepcopy(initial['units'])
    expected[uid]['mode'] = 'price_quantity'
    assert exact(changed['units']) == exact(expected)
    assert not item(at.button, '按成本生成预览').disabled
    # Migrated changes are protected; replacement requires an explicit opt-in.
    item(at.checkbox, '允许模拟报价替换该机组手工修改').check().run()
    item(at.button, '按成本生成预览').click().run()
    assert not at.exception
    assert key + ':preview' in at.session_state
    assert not item(at.button, '采用预览报价').disabled
    assert exact(at.session_state[key]['units']) == exact(expected)
    assert files(scenario.scenario_path('默认')) == before


def test_explicit_mode_conversion_fills_only_missing_selected_unit_curves(scenario):
    from smm.config import load_config
    from smm.bids import default_bids
    def mutate(draft):
        unit = draft['units']['ES1']
        unit['mode'] = 'self_schedule'
        unit['self_schedule'] = [0.0] * 96
        unit.pop('dis_segments', None)
        unit['ch_segments'] = []
    at, key, initial, before = setup_app(scenario, mutate, 'ES1')
    assert exact(at.session_state[key]) == exact(initial)
    assert item(at.button, '按成本生成预览').disabled
    item(at.button, '切换该机组为报量报价').click().run()
    assert not at.exception
    cfg = load_config(root=scenario.scenario_path('默认'))
    defaults = default_bids(cfg)['units']['ES1']
    expected = copy.deepcopy(initial['units'])
    expected['ES1']['mode'] = 'price_quantity'
    for curve in ('dis_segments', 'ch_segments'):
        expected['ES1'][curve] = defaults[curve]
    assert exact(at.session_state[key]['units']) == exact(expected)
    item(at.checkbox, '允许模拟报价替换该机组手工修改').check().run()
    item(at.button, '按成本生成预览').click().run()
    assert not at.exception
    assert key + ':preview' in at.session_state
    assert files(scenario.scenario_path('默认')) == before


def test_explicit_curve_edit_retains_segment_metadata_and_other_units(scenario):
    def mutate(draft):
        draft['units']['C1']['segments'][0]['source_note'] = {'reference': 'research', 'confidence': None}
    at, key, initial, before = setup_app(scenario, mutate)
    price = initial['units']['C1']['segments'][0]['price'] + 1
    at.session_state[f'quote_edit:{key}:C1:segments'] = {
        'edited_rows': {0: {'报价（元/MWh）': price}}, 'added_rows': [], 'deleted_rows': []}
    at.run()
    assert not at.exception
    expected = copy.deepcopy(initial['units'])
    expected['C1']['segments'][0]['price'] = price
    assert exact(at.session_state[key]['units']) == exact(expected)
    assert files(scenario.scenario_path('默认')) == before
