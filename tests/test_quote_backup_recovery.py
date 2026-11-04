"""Downloaded quotes remain usable without hidden legacy-history prerequisites."""
import copy
import io
import json

import pytest
import streamlit as st

from test_input_recovery_ui import scenario, item, open_app, files

LABEL = '恢复报价 JSON（仅所选机组）'


def supply_upload(monkeypatch, value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
    original = st.file_uploader
    monkeypatch.setattr(st, 'file_uploader', lambda label, *a, **kw:
                        io.BytesIO(data) if label == LABEL else original(label, *a, **kw))


@pytest.mark.parametrize('uid,curve', [('C1', 'segments'), ('CCGT', 'segments'),
    ('GT', 'segments'), ('W1', 'segments'), ('PV1', 'segments'), ('H1', 'segments'),
    ('ES1', 'dis_segments'), ('PS1', 'gen_segments')])
def test_regular_scene_can_restore_downloaded_quote_after_refresh(scenario, monkeypatch, uid, curve):
    at = open_app()
    key = 'quote_current:默认'
    item(at.selectbox, '模拟机组').select(uid).run()
    original = copy.deepcopy(at.session_state[key])
    before = files(scenario.scenario_path('默认'))
    assert any(x.label == LABEL for x in at.get('file_uploader'))
    backup = copy.deepcopy(original)
    for segment in backup['units'][uid][curve]:
        segment['price'] += 10.
    other = 'H1' if uid != 'H1' else 'C1'
    backup['units'][other]['segments'][0]['price'] = 99.
    supply_upload(monkeypatch, backup)
    at.run()
    assert not at.exception
    assert at.session_state[key] == original  # Uploading is only a preview.
    item(at.button, '恢复文件中本机组报价').click().run()
    assert not at.exception
    expected = copy.deepcopy(original)
    expected['units'][uid] = backup['units'][uid]
    assert at.session_state[key] == expected
    record = json.loads((scenario.scenario_path('默认') / 'current_quote.json').read_bytes())
    assert record['draft'] == expected
    assert original in record['history'].values()
    assert uid in record['manual']
    assert files(scenario.scenario_path('默认')) == before
    # Retained upload cannot replay and overwrite a later manual edit.
    price = backup['units'][uid][curve][0]['price'] + 10.
    at.session_state[f'quote_edit:{key}:{uid}:{curve}'] = {
        'edited_rows': {i: {'报价（元/MWh）': row['price'] + 10.}
                        for i, row in enumerate(backup['units'][uid][curve])},
        'added_rows': [], 'deleted_rows': []}
    at.run()
    at.run()
    assert at.session_state[key]['units'][uid][curve][0]['price'] == price
    fresh = open_app()
    assert fresh.session_state[key]['units'][uid][curve][0]['price'] == price
    assert not item(fresh.button, '▶ 开始运行').disabled, [x.value for x in fresh.dataframe if '说明' in x.value.columns]


@pytest.mark.parametrize('payload', [b'{broken', {}, {'units': {}},
    {'units': {'C1': []}}, {'units': {'C1': {'segments': [1]}}},
    {'units': {'C1': {'segments': [], 'startup_cost': 1}}},
    {'units': {'C1': {'mode': []}}}, {'units': {'C1': {'mode': {}}}}])
def test_bad_backup_is_friendly_and_never_changes_current(scenario, monkeypatch, payload):
    at = open_app()
    before = (scenario.scenario_path('默认') / 'current_quote.json').read_bytes()
    supply_upload(monkeypatch, payload)
    at.run()
    assert not at.exception
    assert any('恢复文件不能读取' in x.value for x in at.error)
    assert not any(x.label == '恢复文件中本机组报价' for x in at.button)
    assert (scenario.scenario_path('默认') / 'current_quote.json').read_bytes() == before


def test_recovery_cannot_overwrite_a_newer_tab(scenario, monkeypatch):
    first = open_app()
    second = open_app()
    key = 'quote_current:默认'
    backup = copy.deepcopy(second.session_state[key])
    backup['units']['C1']['noload_cost'] = 4321.
    item(first.number_input, '空载报价（元/h）').set_value(6001.).run()
    path = scenario.scenario_path('默认') / 'current_quote.json'
    before = path.read_bytes()
    supply_upload(monkeypatch, backup)
    second.run()
    item(second.button, '恢复文件中本机组报价').click().run()
    assert not second.exception
    assert any('当前报价未自动保存' in x.value for x in second.error)
    assert item(second.button, '▶ 开始运行').disabled
    assert second.session_state[key]['units']['C1']['noload_cost'] == 4321.
    assert path.read_bytes() == before


def test_recovered_invalid_bid_is_visible_and_repairable_not_runnable(scenario, monkeypatch):
    at = open_app()
    key = 'quote_current:默认'
    backup = copy.deepcopy(at.session_state[key])
    backup['units']['C1']['noload_cost'] = -1.
    supply_upload(monkeypatch, backup)
    at.run()
    item(at.button, '恢复文件中本机组报价').click().run()
    assert not at.exception
    assert item(at.number_input, '空载报价（元/h）').value == -1.
    assert item(at.button, '▶ 开始运行').disabled
    item(at.number_input, '空载报价（元/h）').set_value(5001.).run()
    assert not item(at.button, '▶ 开始运行').disabled


@pytest.mark.parametrize('invalid_mode', [[], {}, None])
def test_invalid_legacy_mode_can_be_deliberately_repaired(scenario, invalid_mode):
    at = open_app()
    path = scenario.scenario_path('默认') / 'current_quote.json'
    record = json.loads(path.read_bytes())
    record['draft']['units']['C1']['mode'] = invalid_mode
    path.write_text(json.dumps(record, ensure_ascii=False))
    at = open_app()
    assert not at.exception
    assert any('当前申报模式无效' in x.value for x in at.warning)
    assert item(at.button, '▶ 开始运行').disabled
    assert json.loads(path.read_bytes())['draft']['units']['C1']['mode'] == invalid_mode
    item(at.button, '切换该机组为报量报价').click().run()
    assert not at.exception
    assert not item(at.button, '▶ 开始运行').disabled
    assert json.loads(path.read_bytes())['draft']['units']['C1']['mode'] == 'price_quantity'
