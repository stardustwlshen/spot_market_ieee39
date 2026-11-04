"""The next manual table edit must not be discarded when its input is rebased."""
import copy
import json

import pytest

from test_input_recovery_ui import scenario, item, open_app


def edit_table(at, key, *, rows=None, added=None, deleted=None):
    at.session_state[key] = {
        'edited_rows': rows or {}, 'added_rows': added or [], 'deleted_rows': deleted or []}
    at.run()
    assert not at.exception


@pytest.mark.parametrize('uid,curve', [
    ('C1', 'segments'), ('CCGT', 'segments'), ('GT', 'segments'), ('H1', 'segments'),
    ('W1', 'segments'), ('PV1', 'segments'),
    ('ES1', 'dis_segments'), ('ES1', 'ch_segments'),
    ('PS1', 'gen_segments'), ('PS1', 'pump_segments'),
])
def test_invalid_then_corrected_and_repeated_table_edits_are_saved(scenario, uid, curve):
    at = open_app()
    item(at.selectbox, '模拟机组').set_value(uid).run()
    current = 'quote_current:默认'
    widget = f'quote_edit:{current}:{uid}:{curve}'
    initial = copy.deepcopy(at.session_state[current])
    original = initial['units'][uid][curve][0]['price']
    # The last valid edit differs from the archive so frozen-input checks cannot
    # accidentally pass by falling back to the original bid version.
    final = original - 10.
    for price in (-1000., original, -1001., final):
        edit_table(at, widget, rows={0: {'报价（元/MWh）': price}})
        assert at.session_state[current]['units'][uid][curve][0]['price'] == price
        saved = json.loads((scenario.scenario_path('默认') / 'current_quote.json').read_bytes())['draft']
        assert saved['units'][uid][curve][0]['price'] == price
        assert item(at.button, '▶ 开始运行').disabled == (price < -50)
        assert {k:v for k,v in saved['units'].items() if k != uid} == {
            k:v for k,v in initial['units'].items() if k != uid}
    from smm.live_runner import freeze_inputs
    from smm.config import load_config
    from smm.bids import load_bids
    path = scenario.scenario_path('默认') / 'current_quote.json'
    durable = path.read_bytes()
    saved = json.loads(durable)['draft']
    directory = scenario.RUNS_DIR / f'frozen_{uid}_{curve}'
    frozen = freeze_inputs(path.parent, directory, bids=saved)
    assert json.loads((directory / 'submitted_bids.json').read_bytes()) == saved
    assert load_bids(load_config(root=frozen))['units'] == saved['units']
    assert path.read_bytes() == durable
    # A new session cannot resurrect the invalid edit or the original archive.
    fresh = open_app()
    item(fresh.selectbox, '模拟机组').set_value(uid).run()
    assert fresh.session_state[current]['units'][uid][curve][0]['price'] == final
    assert not item(fresh.button, '▶ 开始运行').disabled


def test_scene_round_trip_keeps_continuous_edits_and_freezes_durable_current(scenario):
    from smm.live_runner import freeze_inputs
    from smm.config import load_config
    from smm.bids import load_bids

    at = open_app()
    def snapshot(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
                if p.is_file() and not p.name.endswith('.lock')}
    source = scenario.scenario_path('默认')
    original = snapshot(source)
    scenario.create_scenario('场景A', source='默认')
    scenario.create_scenario('场景B', source='默认')
    at.run()  # Refresh the selector after preparing two independent copies.

    def change(scene, values):
        item(at.selectbox, '当前场景').set_value(scene).run()
        item(at.selectbox, '模拟机组').set_value('C1').run()
        for value in values:
            edit_table(at, f'quote_edit:quote_current:{scene}:C1:segments',
                       rows={0: {'报价（元/MWh）': value}})
            assert at.session_state[f'quote_current:{scene}']['units']['C1']['segments'][0]['price'] == value
        return scenario.scenario_path(scene) / 'current_quote.json'

    a = change('场景A', [-1., -2.])
    saved_a = a.read_bytes()
    b = change('场景B', [-11., -12.])
    saved_b = b.read_bytes()
    assert a.read_bytes() == saved_a
    item(at.selectbox, '当前场景').set_value('场景A').run()
    assert at.session_state['quote_current:场景A']['units']['C1']['segments'][0]['price'] == -2.
    a = change('场景A', [-3., -4.])
    assert b.read_bytes() == saved_b
    saved_a = a.read_bytes()
    durable = json.loads(saved_a)['draft']
    assert durable == at.session_state['quote_current:场景A']
    directory = scenario.RUNS_DIR / 'frozen_scene_round_trip'
    frozen = freeze_inputs(a.parent, directory, bids=durable)
    assert json.loads((directory / 'submitted_bids.json').read_bytes()) == durable
    assert load_bids(load_config(root=frozen))['units'] == durable['units']
    assert a.read_bytes() == saved_a and b.read_bytes() == saved_b
    assert snapshot(source) == original
    fresh = open_app()
    for scene, expected in [('场景A', -4.), ('场景B', -12.), ('场景A', -4.)]:
        item(fresh.selectbox, '当前场景').set_value(scene).run()
        assert fresh.session_state[f'quote_current:{scene}']['units']['C1']['segments'][0]['price'] == expected
        assert not item(fresh.button, '▶ 开始运行').disabled
    assert snapshot(source) == original


def test_add_edit_delete_segments_do_not_replay_or_drop_rows(scenario):
    at = open_app()
    current = 'quote_current:默认'
    widget = f'quote_edit:{current}:C1:segments'
    initial = copy.deepcopy(at.session_state[current]['units']['C1']['segments'])
    n = len(initial)
    last = initial[-1]
    middle = (last['p_start'] + last['p_end']) / 2
    edit_table(at, widget, rows={n - 1: {'终点（MW）': middle}}, added=[{
        '起点（MW）': middle, '终点（MW）': last['p_end'], '报价（元/MWh）': last['price'] + 10}])
    assert len(at.session_state[current]['units']['C1']['segments']) == n + 1
    edit_table(at, widget, rows={n: {'报价（元/MWh）': last['price'] + 20}})
    assert at.session_state[current]['units']['C1']['segments'][-1]['price'] == last['price'] + 20
    edit_table(at, widget, rows={n - 1: {'终点（MW）': last['p_end']}}, deleted=[n])
    assert at.session_state[current]['units']['C1']['segments'] == initial
    at.run()
    assert at.session_state[current]['units']['C1']['segments'] == initial
    assert not item(at.button, '▶ 开始运行').disabled


def test_legacy_startup_state_table_accepts_consecutive_corrections(scenario):
    modes = scenario.read_yaml('默认', 'modes')
    modes['MKT']['startup_cost_mode'] = 'state_dependent'
    scenario.write_yaml('默认', 'modes', modes)
    at = open_app()
    current = 'quote_current:默认'
    widget = f'quote_edit:{current}:C1:startup_states'
    original = at.session_state[current]['units']['C1']['startup_cost']['hot']
    for value in (-1., original, original - 1, original - 2):
        edit_table(at, widget, rows={0: {'hot': value}})
        assert at.session_state[current]['units']['C1']['startup_cost']['hot'] == value
        assert item(at.button, '▶ 开始运行').disabled == (value < 0)


def test_rule_hints_use_effective_type_overrides_and_explain_internal_block(scenario):
    from smm.config import load_config
    from quote_simulation import quote_rule_notes
    cfg = load_config(root=scenario.scenario_path('默认'))
    cfg.mode['bid_rules'].setdefault('by_type', {})['coal'] = {
        'price_min': -25., 'price_max': 550., 'segments_max': 7}
    text = '\n'.join(quote_rule_notes(cfg, 'C1', 'coal', 'segments'))
    assert '-25～550 元/MWh' in text and '1～7 段' in text
    assert '0～290 MW' in text and '不计入上述市场段数' in text
    assert '报价仍进入优化' in text and '不是结算电价范围' in text
    assert '17.8 MW' in text and '646 MW' in text
    at = open_app()
    hints = '\n'.join(x.value for x in at.caption)
    assert '-50～800 元/MWh' in hints and '内部最小出力块' in hints
    item(at.selectbox, '模拟机组').set_value('ES1').run()
    hints = '\n'.join(x.value for x in at.caption)
    assert '严格递减' in hints and '购电意愿' in hints


@pytest.mark.parametrize('uid,field,label,values', [
    ('C1', 'noload_cost', '空载报价（元/h）', [6001., 6002., 6003., 6004.]),
    ('C1', 'startup_cost_yuan', '单次启动报价（元/次）', [100., 101., 102., 103.]),
    ('ES1', 'soc_end', '期末 SOC（0–1，日前硬约束）', [.51, .52, .53, .54]),
    ('PS1', 'vup_end', '日末上库库容（万m³，日前硬约束）', [190., 191., 192., 193.]),
])
def test_consecutive_scalar_edits_survive_restart_and_exact_freeze(scenario, uid, field, label, values):
    from smm.live_runner import freeze_inputs
    from smm.config import load_config
    from smm.bids import load_bids

    at = open_app()
    item(at.selectbox, '模拟机组').select(uid).run()
    key = 'quote_current:默认'
    original = copy.deepcopy(at.session_state[key])
    path = scenario.scenario_path('默认') / 'current_quote.json'
    for value in values:
        item(at.number_input, label).set_value(value).run()
        assert not at.exception
        assert at.session_state[key]['units'][uid][field] == value
        assert json.loads(path.read_bytes())['draft']['units'][uid][field] == value
        assert not item(at.button, '▶ 开始运行').disabled
    fresh = open_app()
    item(fresh.selectbox, '模拟机组').select(uid).run()
    assert item(fresh.number_input, label).value == values[-1]
    draft = copy.deepcopy(fresh.session_state[key])
    expected = copy.deepcopy(original)
    expected['units'][uid][field] = values[-1]
    assert draft == expected
    durable = path.read_bytes()
    directory = scenario.RUNS_DIR / f'freeze_scalar_{field}'
    frozen = freeze_inputs(path.parent, directory, bids=draft)
    assert json.loads((directory / 'submitted_bids.json').read_bytes()) == draft
    assert load_bids(load_config(root=frozen))['units'] == draft['units']
    assert path.read_bytes() == durable
