"""Direct quote editing remains read-only until an actual edit or launch."""
from copy import deepcopy
import json

import pytest

from test_single_day_workspace import scene, app, step, item, files


def test_browse_all_units_and_cost_preview_does_not_create_current_quote(scene):
    before = files(scene.SCEN_DIR)
    at = step(app(), '3 报价')
    original = deepcopy(at.session_state['quote_current:默认'])
    assert not any(x.label == '编辑机组报价' for x in at.checkbox)
    for uid in original['units']:
        item(at.selectbox, '模拟机组').set_value(uid).run()
        assert not at.exception
        assert at.session_state['quote_current:默认'] == original
    item(at.selectbox, '模拟机组').set_value('C1').run()
    item(at.button, '按成本生成预览').click().run()
    assert not at.exception
    assert 'quote_current:默认:preview' in at.session_state
    assert at.session_state['quote_current:默认'] == original
    assert files(scene.SCEN_DIR) == before
    assert not (scene.scenario_path('默认') / 'current_quote.json').exists()


def test_edit_persists_across_unit_step_and_browser_changes(scene):
    at = step(app(), '3 报价')
    key = 'quote_current:默认'
    expected = deepcopy(at.session_state[key])
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    expected['units']['C1']['noload_cost'] = 6123.
    item(at.selectbox, '模拟机组').set_value('ES1').run()
    item(at.number_input, '期末 SOC（0–1，日前硬约束）').set_value(.55).run()
    expected['units']['ES1']['soc_end'] = .55
    step(at, '4 运行'); step(at, '3 报价')
    assert item(at.selectbox, '模拟机组').value == 'ES1'
    assert at.session_state[key] == expected
    saved = files(scene.SCEN_DIR)
    fresh = step(app(), '3 报价')
    item(fresh.selectbox, '模拟机组').set_value('C1').run()
    assert not fresh.exception
    assert fresh.session_state[key] == expected
    assert item(fresh.number_input, '空载报价（元/h）').value == 6123.
    assert files(scene.SCEN_DIR) == saved


@pytest.mark.parametrize('uid,curve', [('C1', 'segments'), ('ES1', 'dis_segments'),
                                      ('ES1', 'ch_segments')])
def test_direct_table_edits_keep_invalid_values_then_save_corrections(scene, uid, curve):
    at = step(app(), '3 报价')
    item(at.selectbox, '模拟机组').set_value(uid).run()
    key = 'quote_current:默认'
    original = deepcopy(at.session_state[key])
    for price in (-1000., original['units'][uid][curve][0]['price']):
        at.session_state[f'quote_edit:{key}:{uid}:{curve}'] = {
            'edited_rows': {0: {'报价（元/MWh）': price}}, 'added_rows': [], 'deleted_rows': []}
        at.run()
        assert not at.exception
        assert at.session_state[key]['units'][uid][curve][0]['price'] == price
        saved = json.loads((scene.scenario_path('默认') / 'current_quote.json').read_bytes())['draft']
        assert saved == at.session_state[key]
        assert all(saved['units'][other] == bid for other, bid in original['units'].items() if other != uid)
        step(at, '4 运行')
        box = item(at.checkbox, '已核对合约卡，确认按此启动')
        if not box.value:
            box.check().run()
        assert item(at.button, '开始单日出清').disabled == (price < -50)
        step(at, '3 报价')


def test_new_saved_version_cannot_replace_current_editor(scene):
    at = step(app(), '3 报价')
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    original = deepcopy(at.session_state['quote_current:默认'])
    from smm.bids import bid_dir
    from smm.config import load_config
    path = bid_dir(load_config(root=scene.scenario_path('默认')))
    newer = deepcopy(original)
    version = max(int(p.stem.split('_v')[-1]) for p in path.glob('bids_v*.json')) + 1
    newer['version'] = version
    newer['units']['C1']['noload_cost'] = 7777.
    (path / f'bids_v{version}.json').write_text(json.dumps(newer), encoding='utf-8')
    before = files(scene.SCEN_DIR)
    at.run()
    assert not at.exception
    assert at.session_state['quote_current:默认'] == original
    assert any('不会自动替换' in warning.value for warning in at.warning)
    assert files(scene.SCEN_DIR) == before


def test_failed_autosave_keeps_editor_and_does_not_create_default_archive(scene, monkeypatch):
    import quote_simulation
    import single_day_workspace
    at = step(app(), '3 报价')
    original = deepcopy(at.session_state['quote_current:默认'])
    before = files(scene.SCEN_DIR)
    def fail(*args, **kwargs):
        raise OSError('test write failure')
    monkeypatch.setattr(quote_simulation, 'persist_scene_working_draft', fail)
    monkeypatch.setattr(single_day_workspace, 'persist_scene_working_draft', fail)
    starts = []
    monkeypatch.setattr(single_day_workspace, 'start_run', lambda *a, **k: starts.append(a))
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    expected = deepcopy(original)
    expected['units']['C1']['noload_cost'] = 6123.
    assert at.session_state['quote_current:默认'] == expected
    assert any('未自动保存' in error.value for error in at.error)
    step(at, '4 运行')
    item(at.checkbox, '已核对合约卡，确认按此启动').check().run()
    item(at.button, '开始单日出清').click().run()
    assert not at.exception and not starts
    assert at.session_state['quote_current:默认'] == expected
    assert files(scene.SCEN_DIR) == before


def test_run_primary_action_precedes_collapsed_advanced_options(scene):
    at = step(app(), '4 运行')
    advanced = item(at.expander, '高级设置')
    assert not advanced.proto.expanded
    assert item(advanced.radio, '运行范围')
    assert item(advanced.checkbox, '仅试跑前几个实时时段')
    assert item(advanced.button, '编辑高级参数')
    assert not any(button.label == '开始单日出清' for button in advanced.button)
    labels = [button.label for button in at.button]
    assert labels.index('开始单日出清') < labels.index('编辑高级参数')
    assert any(c.value == '实际执行阶段' for c in at.caption)


def test_readonly_checkpoint_handles_optional_legacy_archive_fields(scene):
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    from smm.config import load_config
    cfg = load_config(root=scene.scenario_path('默认'))
    state = {}
    get_scene_working_draft(state, '默认', cfg)
    persist_scene_working_draft(state, '默认', cfg)
    path = cfg.root / 'current_quote.json'
    record = json.loads(path.read_bytes())
    for field in ('manual', 'saved_at', 'origin'):
        record.pop(field, None)
    path.write_text(json.dumps(record), encoding='utf-8')
    before = path.read_bytes()
    fresh = {}
    get_scene_working_draft(fresh, '默认', cfg)
    persist_scene_working_draft(fresh, '默认', cfg, only_if_changed=True)
    assert path.read_bytes() == before


def test_integer_legacy_quotes_are_not_rewritten_by_float_widgets(scene):
    from smm.bids import bid_dir
    from smm.config import load_config
    path = bid_dir(load_config(root=scene.scenario_path('默认')))
    latest = max(path.glob('bids_v*.json'), key=lambda p: int(p.stem.split('_v')[-1]))
    def integers(value):
        if isinstance(value, dict):
            return {k: integers(v) for k, v in value.items()}
        if isinstance(value, list):
            return [integers(v) for v in value]
        return int(value) if isinstance(value, float) and value.is_integer() else value
    legacy = integers(json.loads(latest.read_bytes()))
    latest.write_text(json.dumps(legacy), encoding='utf-8')
    before = files(scene.SCEN_DIR)
    at = step(app(), '3 报价')
    original = deepcopy(at.session_state['quote_current:默认'])
    for uid in ('C1', 'ES1', 'PS1', 'W1', 'C1'):
        item(at.selectbox, '模拟机组').set_value(uid).run()
        assert not at.exception
        assert json.dumps(at.session_state['quote_current:默认'], sort_keys=True) == json.dumps(original, sort_keys=True)
    step(at, '4 运行'); step(at, '3 报价')
    assert files(scene.SCEN_DIR) == before


def test_external_current_quote_conflict_retains_both_copies(scene):
    at = step(app(), '3 报价')
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    path = scene.scenario_path('默认') / 'current_quote.json'
    other = json.loads(path.read_bytes())
    other['draft']['units']['C1']['noload_cost'] = 7000.
    path.write_text(json.dumps(other), encoding='utf-8')
    before = path.read_bytes()
    item(at.number_input, '空载报价（元/h）').set_value(6124.).run()
    assert not at.exception
    assert at.session_state['quote_current:默认']['units']['C1']['noload_cost'] == 6124.
    assert path.read_bytes() == before
    assert any('未自动保存' in error.value for error in at.error)


def test_pre_checkpoint_session_keeps_unsaved_draft_without_browsing_write(scene):
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    from smm.config import load_config
    cfg = load_config(root=scene.scenario_path('默认'))
    state = {}
    key, draft, _, _ = get_scene_working_draft(state, '默认', cfg)
    state.pop(key + ':checkpoint')
    draft['units']['C1']['noload_cost'] = 6123.
    before = files(cfg.root)
    get_scene_working_draft(state, '默认', cfg)
    persist_scene_working_draft(state, '默认', cfg, only_if_changed=True)
    assert files(cfg.root) == before
    assert state[key]['units']['C1']['noload_cost'] == 6123.
    # A later actual edit still saves the retained draft, not the original bids.
    state[key]['units']['C1']['noload_cost'] = 6124.
    persist_scene_working_draft(state, '默认', cfg, only_if_changed=True)
    assert json.loads((cfg.root / 'current_quote.json').read_bytes())['draft'] == state[key]


def test_compact_reset_and_preview_accept_save_after_rerun(scene):
    at = step(app(), '3 报价')
    key = 'quote_current:默认'
    original = deepcopy(at.session_state[key])
    path = scene.scenario_path('默认') / 'current_quote.json'
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    item(at.button, '恢复该机组原报价').click().run()
    assert not at.exception
    assert at.session_state[key] == original
    assert json.loads(path.read_bytes())['draft'] == original
    item(at.button, '按成本生成预览').click().run()
    candidate = deepcopy(at.session_state[key + ':preview']['candidate'])
    assert at.session_state[key] == original
    item(at.button, '采用预览报价').click().run()
    assert not at.exception
    assert at.session_state[key] == candidate
    assert json.loads(path.read_bytes())['draft'] == candidate
    assert all(candidate['units'][uid] == unit for uid, unit in original['units'].items() if uid != 'C1')
