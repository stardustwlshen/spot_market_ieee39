"""One current quote per scene; archives never become hidden market inputs."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from test_input_recovery_ui import scenario, item, files, key_for, open_app, PAGE


def current_key(name='默认'):
    return f'quote_current:{name}'


def quote_notice(at):
    return '\n'.join(element.value for collection in (at.caption, at.info, at.warning)
                     for element in collection)


def scene_config(scenario, name='默认'):
    from smm.config import load_config
    return load_config(root=scenario.scenario_path(name))


def saved_bid(scenario, name='默认'):
    from smm.bids import load_bids
    return load_bids(scene_config(scenario, name))


def archive_with_change(scenario, uid='C2', amount=17, name='默认'):
    from smm.bids import load_bids, save_bids
    cfg = scene_config(scenario, name)
    newer = load_bids(cfg)
    newer['units'][uid]['noload_cost'] += amount
    save_bids(cfg, newer, note='external saved archive')
    return load_bids(cfg)


def assert_no_source_or_version_controls(at):
    assert not any('报价来源' in widget.label or '报价版本' in widget.label
                   for widget in [*at.selectbox, *at.radio])
    assert not any(button.label == '另存报价版本' for button in at.button)


def test_fresh_scene_uses_latest_saved_and_does_not_select_unmarked_history(scenario):
    from smm.bids import load_bids
    cfg = scene_config(scenario)
    latest = archive_with_change(scenario)
    original_files = files(cfg.root)
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    history = {}
    for index, source in enumerate((f"bid_draft:默认:{latest['version']}",
                                    'bid_draft:默认:最新', 'draft_默认_1')):
        history[source] = copy.deepcopy(latest)
        history[source]['units']['C1']['noload_cost'] += index + 111
        history[source]['note'] = f'preserve-unmarked-history-{index}'
        at.session_state[source] = copy.deepcopy(history[source])
    at.run()
    assert not at.exception
    assert_no_source_or_version_controls(at)
    assert key_for(at) == current_key()
    assert at.session_state[current_key()] == latest
    assert item(at.number_input, '空载报价（元/h）').value == latest['units']['C1']['noload_cost']
    assert str(latest['version']) in quote_notice(at)
    assert any('历史' in message.value or '旧' in message.value for message in [*at.info, *at.warning])
    for key, draft in history.items():
        assert at.session_state[key] == draft
    payload = (cfg.root / 'current_quote.json').read_text()
    assert all(draft['note'] in payload for draft in history.values())
    assert at.get('download_button')  # Retained work is accessible without restoring it into inputs.
    assert load_bids(cfg) == latest
    assert files(cfg.root) == original_files


@pytest.mark.parametrize('source_kind', ['version', 'latest', 'legacy', 'recovered'])
def test_only_explicit_active_legacy_draft_migrates_without_changing_originals(scenario, source_kind):
    baseline = saved_bid(scenario)
    version = baseline['version']
    source = version if source_kind == 'version' else '最新' if source_kind == 'latest' else f'draft_默认_{version}'
    legacy_key = f'bid_draft:默认:{version}' if source_kind in ('version', 'latest') else source
    legacy = copy.deepcopy(baseline)
    legacy['units']['C1']['noload_cost'] += 13
    legacy['note'] = 'explicit active draft'
    expected = copy.deepcopy(legacy)
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state[legacy_key] = copy.deepcopy(legacy)
    at.session_state['quote_source:默认'] = source
    if source_kind == 'recovered':
        recovered_key = f'quote_recovered:默认:{legacy_key}'
        expected['units']['C1']['noload_cost'] += 19
        expected['note'] = 'newer visible recovered edits'
        at.session_state[recovered_key] = copy.deepcopy(expected)
    original_files = files(scenario.scenario_path('默认'))
    at.run()
    assert not at.exception
    assert_no_source_or_version_controls(at)
    assert at.session_state[current_key()] == expected
    assert item(at.number_input, '空载报价（元/h）').value == expected['units']['C1']['noload_cost']
    assert at.session_state[legacy_key] == legacy
    assert '接续' in quote_notice(at) or '迁移' in quote_notice(at) or '恢复' in quote_notice(at)
    amount = expected['units']['C1']['noload_cost'] + 1
    item(at.number_input, '空载报价（元/h）').set_value(amount).run()
    assert not at.exception
    assert at.session_state[current_key()]['units']['C1']['noload_cost'] == amount
    assert at.session_state[legacy_key] == legacy
    if source_kind == 'recovered':
        assert at.session_state[recovered_key] == expected
    assert files(scenario.scenario_path('默认')) == original_files


def test_stale_latest_pointer_cannot_resurrect_an_older_draft(scenario):
    old = saved_bid(scenario)
    newer = archive_with_change(scenario)
    old['units']['C1']['noload_cost'] += 123
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state[f"bid_draft:默认:{old['version']}"] = copy.deepcopy(old)
    at.session_state['quote_source:默认'] = '最新'
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == newer
    assert at.session_state[f"bid_draft:默认:{old['version']}"] == old


def test_scene_navigation_and_fresh_session_keep_independent_current_edits(scenario):
    scenario.create_scenario('测试副本', source='默认')
    base = saved_bid(scenario)
    other = archive_with_change(scenario, uid='C1', amount=77, name='测试副本')
    originals = files(scenario.SCEN_DIR)
    at = open_app()
    item(at.number_input, '空载报价（元/h）').set_value(base['units']['C1']['noload_cost'] + 5).run()
    first = copy.deepcopy(at.session_state[current_key()])
    item(at.selectbox, '当前场景').select('测试副本').run()
    assert not at.exception
    assert at.session_state[current_key('测试副本')] == other
    item(at.number_input, '空载报价（元/h）').set_value(other['units']['C1']['noload_cost'] + 9).run()
    second = copy.deepcopy(at.session_state[current_key('测试副本')])
    item(at.selectbox, '当前场景').select('默认').run()
    assert not at.exception
    assert at.session_state[current_key()] == first
    assert item(at.number_input, '空载报价（元/h）').value == first['units']['C1']['noload_cost']
    item(at.selectbox, '当前场景').select('测试副本').run()
    assert at.session_state[current_key('测试副本')] == second
    assert files(scenario.SCEN_DIR) == originals
    # A fresh browser session must use the durable current draft, not the old archive.
    fresh = open_app()
    assert fresh.session_state[current_key()] == first
    item(fresh.selectbox, '当前场景').select('测试副本').run()
    assert fresh.session_state[current_key('测试副本')] == second
    assert files(scenario.SCEN_DIR) == originals


def test_new_external_archive_does_not_replace_current_or_its_reset_baseline(scenario):
    baseline = saved_bid(scenario)
    at = open_app()
    amount = baseline['units']['C1']['noload_cost'] + 11
    item(at.number_input, '空载报价（元/h）').set_value(amount).run()
    edited = copy.deepcopy(at.session_state[current_key()])
    newer = archive_with_change(scenario, uid='C1', amount=500)
    original_files = files(scenario.scenario_path('默认'))
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == edited
    assert item(at.number_input, '空载报价（元/h）').value == amount
    assert any('新' in x.value and ('保存' in x.value or '版本' in x.value) for x in at.warning)
    item(at.button, '恢复该机组原报价').click().run()
    assert not at.exception
    assert at.session_state[current_key()]['units'] == baseline['units']
    assert at.session_state[current_key()]['version'] == baseline['version']
    assert saved_bid(scenario) == newer
    assert files(scenario.scenario_path('默认')) == original_files
    fresh = open_app()
    assert fresh.session_state[current_key()]['units'] == baseline['units']


def test_repeated_launches_freeze_current_edits_and_preserve_unselected_units(scenario, monkeypatch):
    import common
    from smm.live_runner import atomic_json
    baseline = saved_bid(scenario)
    original_files = files(scenario.scenario_path('默认'))
    real_spawn = common.subprocess.Popen
    spawned = []
    def fake_spawn(*args, **kwargs):
        if args and args[0][0] == 'git':
            return real_spawn(*args, **kwargs)
        spawned.append((args, kwargs))
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess, 'Popen', fake_spawn)
    at = open_app()
    first_amount = baseline['units']['C1']['noload_cost'] + 15
    item(at.number_input, '空载报价（元/h）').set_value(first_amount).run()
    item(at.button, '▶ 开始运行').click().run()
    assert not at.exception
    first_run = Path(at.session_state['watch_run'])
    first_frozen = (first_run / 'submitted_bids.json').read_bytes()
    first = json.loads(first_frozen)
    assert first['units']['C1']['noload_cost'] == first_amount
    assert item(at.button, '▶ 开始运行').disabled
    assert item(at.number_input, '空载报价（元/h）').disabled
    meta = json.loads((first_run / 'meta.json').read_text())
    meta['status'] = 'done'
    atomic_json(first_run / 'meta.json', meta)
    at.run()
    assert not at.exception
    assert not item(at.button, '▶ 开始运行').disabled
    assert item(at.number_input, '空载报价（元/h）').value == first_amount
    second_amount = first_amount + 10
    item(at.number_input, '空载报价（元/h）').set_value(second_amount).run()
    item(at.button, '▶ 开始运行').click().run()
    assert not at.exception
    second_run = Path(at.session_state['watch_run'])
    assert second_run != first_run
    second = json.loads((second_run / 'submitted_bids.json').read_text())
    assert second['units']['C1']['noload_cost'] == second_amount
    assert len(spawned) == 2
    assert (first_run / 'submitted_bids.json').read_bytes() == first_frozen
    for frozen in (first, second):
        assert all(frozen['units'][uid] == bid for uid, bid in baseline['units'].items() if uid != 'C1')
    assert at.session_state[current_key()]['units']['C1']['noload_cost'] == second_amount
    assert files(scenario.scenario_path('默认')) == original_files


def test_scene_without_saved_archive_starts_from_default_without_creating_a_version(scenario):
    from smm.bids import bid_dir, default_bids
    cfg = scene_config(scenario)
    for path in bid_dir(cfg).glob('bids_v*.*'):
        path.unlink()
    at = open_app()
    expected = default_bids(cfg)
    assert at.session_state[current_key()]['units'] == expected['units']
    assert '缺省' in quote_notice(at)
    assert not list(bid_dir(cfg).glob('bids_v*.*'))
    assert (cfg.root / 'current_quote.json').exists()
    assert not item(at.button, '▶ 开始运行').disabled


def test_history_download_preserves_exact_independent_edits_without_merging(scenario):
    import io
    import zipfile
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft, quotation_backup
    cfg = scene_config(scenario)
    original = saved_bid(scenario)
    older = copy.deepcopy(original)
    older['units']['C1']['noload_cost'] += 1
    older['units']['ES1'].pop('soc_end', None)
    second = copy.deepcopy(original)
    second['units']['ES1']['soc_end'] = None
    second['units']['C2']['noload_cost'] += 2
    state = {'draft_默认_1': older, 'bid_draft:默认:最新': second}
    before = copy.deepcopy(state)
    key, draft, _, _ = get_scene_working_draft(state, '默认', cfg)
    persist_scene_working_draft(state, '默认', cfg)
    assert draft == original
    assert all(state[key] == value for key, value in before.items())
    record = json.loads((cfg.root / 'current_quote.json').read_text())
    assert record['history'] == before
    with zipfile.ZipFile(io.BytesIO(quotation_backup(state, '默认'))) as backup:
        current = json.loads(backup.read('当前报价.json'))
        archived = [json.loads(backup.read(name)) for name in backup.namelist()
                    if name.startswith('旧编辑/') and name.endswith('.json')]
        assert current == original
        assert len(archived) == 2
        assert older in archived and second in archived
        manifest = backup.read('说明.txt').decode()
        assert all(source in manifest for source in before)
    # A subsequent session has one current quote and the same complete history.
    fresh = {}
    new_key, new_draft, _, info = get_scene_working_draft(fresh, '默认', cfg)
    assert new_key == key and new_draft == original
    assert info['history'] == before


def test_concurrent_session_cannot_overwrite_saved_edits_and_can_back_up_local_work(scenario):
    import io
    import zipfile
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft, quotation_backup
    cfg = scene_config(scenario)
    first, second = {}, {}
    key, _, _, _ = get_scene_working_draft(first, '默认', cfg)
    persist_scene_working_draft(first, '默认', cfg)
    get_scene_working_draft(second, '默认', cfg)
    first[key]['units']['C1']['noload_cost'] += 31
    persist_scene_working_draft(first, '默认', cfg)
    durable = (cfg.root / 'current_quote.json').read_bytes()
    second[key]['units']['C2']['noload_cost'] += 37
    local = copy.deepcopy(second[key])
    with pytest.raises(ValueError, match='另一页面|冲突'):
        persist_scene_working_draft(second, '默认', cfg)
    assert (cfg.root / 'current_quote.json').read_bytes() == durable
    assert second[key] == local
    with zipfile.ZipFile(io.BytesIO(quotation_backup(second, '默认'))) as backup:
        assert json.loads(backup.read('当前报价.json')) == local
    fresh = {}
    assert get_scene_working_draft(fresh, '默认', cfg)[1] == first[key]


def test_ui_concurrent_conflict_preserves_both_edits_and_blocks_launch(scenario):
    cfg = scene_config(scenario)
    first = open_app()
    second = open_app()
    item(first.number_input, '空载报价（元/h）').set_value(6123.).run()
    assert not first.exception
    durable = (cfg.root / 'current_quote.json').read_bytes()
    item(second.number_input, '空载报价（元/h）').set_value(6456.).run()
    assert not second.exception
    assert item(second.button, '▶ 开始运行').disabled
    assert second.session_state[current_key()]['units']['C1']['noload_cost'] == 6456.
    assert first.session_state[current_key()]['units']['C1']['noload_cost'] == 6123.
    assert (cfg.root / 'current_quote.json').read_bytes() == durable
    assert any('覆盖' in e.value or '保存' in e.value for e in second.error)
    assert second.get('download_button')
    assert not scenario.RUNS_DIR.exists()


def test_failed_durable_save_retains_prior_file_and_current_edit_and_blocks_run(scenario, monkeypatch):
    import bid_workspace
    cfg = scene_config(scenario)
    at = open_app()
    durable = (cfg.root / 'current_quote.json').read_bytes()
    original_replace = bid_workspace.os.replace
    def fail_current_replace(source, target, *args, **kwargs):
        if Path(target).name == 'current_quote.json':
            raise PermissionError('test blocked current quote persistence')
        return original_replace(source, target, *args, **kwargs)
    monkeypatch.setattr(bid_workspace.os, 'replace', fail_current_replace)
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    assert not at.exception
    assert item(at.button, '▶ 开始运行').disabled
    assert at.session_state[current_key()]['units']['C1']['noload_cost'] == 6123.
    assert (cfg.root / 'current_quote.json').read_bytes() == durable
    assert not list(cfg.root.glob('.current_quote-*.tmp'))
    assert any('保存' in e.value for e in at.error)
    assert not scenario.RUNS_DIR.exists()
    monkeypatch.setattr(bid_workspace.os, 'replace', original_replace)
    at.run()
    assert not at.exception
    assert not item(at.button, '▶ 开始运行').disabled
    assert json.loads((cfg.root / 'current_quote.json').read_text())['draft']['units']['C1']['noload_cost'] == 6123.


@pytest.mark.parametrize('content', ['{ broken json', 'null', '[]',
                                     '{"schema": 999, "draft": {}}'])
def test_invalid_durable_current_fails_closed_without_fallback_or_rewrite(scenario, content):
    path = scenario.scenario_path('默认') / 'current_quote.json'
    path.write_text(content)
    original = files(scenario.scenario_path('默认'))
    at = open_app()
    assert item(at.button, '▶ 开始运行').disabled
    assert at.error
    assert path.read_text() == content
    assert files(scenario.scenario_path('默认')) == original
    assert not scenario.RUNS_DIR.exists()


def test_history_file_preview_and_explicit_restore_change_only_selected_unit(scenario, monkeypatch):
    import io
    import streamlit as st
    original = saved_bid(scenario)
    old = copy.deepcopy(original)
    old['units']['C1']['noload_cost'] += 101
    old['units']['C2']['noload_cost'] += 202
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state['draft_默认_1'] = old
    at.run()
    assert not at.exception
    before = copy.deepcopy(at.session_state[current_key()])
    original_files = files(scenario.scenario_path('默认'))
    real_uploader = st.file_uploader
    def selected_file(*args, **kwargs):
        real_uploader(*args, **kwargs)
        return io.BytesIO(json.dumps(old).encode())
    monkeypatch.setattr(st, 'file_uploader', selected_file)
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == before
    assert not item(at.button, '恢复文件中本机组报价').disabled
    item(at.button, '恢复文件中本机组报价').click().run()
    assert not at.exception
    expected = copy.deepcopy(before)
    expected['units']['C1'] = copy.deepcopy(old['units']['C1'])
    assert at.session_state[current_key()] == expected
    assert at.session_state['draft_默认_1'] == old
    record = json.loads((scenario.scenario_path('默认') / 'current_quote.json').read_text())
    assert record['draft'] == expected
    assert before in record['history'].values()
    assert old in record['history'].values()
    assert files(scenario.scenario_path('默认')) == original_files
    assert not scenario.RUNS_DIR.exists()


def test_broken_external_archive_does_not_block_healthy_current_in_same_or_new_session(scenario):
    from smm.bids import bid_dir
    cfg = scene_config(scenario)
    at = open_app()
    item(at.number_input, '空载报价（元/h）').set_value(6123.).run()
    current = copy.deepcopy(at.session_state[current_key()])
    broken = bid_dir(cfg) / 'bids_v999.json'
    broken.write_text('{ unreadable external archive')
    original_files = files(cfg.root)
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == current
    assert item(at.number_input, '空载报价（元/h）').value == 6123.
    assert not item(at.button, '▶ 开始运行').disabled
    assert any('读取' in message.value for message in at.warning)
    fresh = open_app()
    assert fresh.session_state[current_key()] == current
    assert not item(fresh.button, '▶ 开始运行').disabled
    assert fresh.get('download_button')
    assert files(cfg.root) == original_files


def test_broken_latest_without_current_does_not_fall_back_to_older_saved_quote(scenario):
    from smm.bids import bid_dir
    cfg = scene_config(scenario)
    broken = bid_dir(cfg) / 'bids_v999.json'
    broken.write_text('{ unreadable newest archive')
    original_files = files(cfg.root)
    at = open_app()
    assert item(at.button, '▶ 开始运行').disabled
    assert at.error
    assert not (cfg.root / 'current_quote.json').exists()
    assert files(cfg.root) == original_files
    assert not scenario.RUNS_DIR.exists()


def test_existing_current_retains_new_session_history_and_same_key_conflicts(scenario):
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    cfg = scene_config(scenario)
    baseline = saved_bid(scenario)
    legacy_key = f"bid_draft:默认:{baseline['version']}"
    first_old = copy.deepcopy(baseline)
    first_old['units']['C1']['noload_cost'] += 100
    first_old['note'] = 'first retained historical edit'
    first_state = {legacy_key: copy.deepcopy(first_old)}
    key, _, _, _ = get_scene_working_draft(first_state, '默认', cfg)
    first_state[key]['units']['C2']['noload_cost'] += 7
    persist_scene_working_draft(first_state, '默认', cfg)
    current = copy.deepcopy(first_state[key])
    second_old = copy.deepcopy(baseline)
    second_old['units']['C1']['noload_cost'] += 200
    second_old['note'] = 'different historical edit under same key'
    second_state = {legacy_key: copy.deepcopy(second_old), 'quote_source:默认': baseline['version']}
    _, draft, _, info = get_scene_working_draft(second_state, '默认', cfg)
    assert draft == current  # A durable current quote always wins over legacy pointers.
    assert second_state[legacy_key] == second_old
    assert len(info['history']) == 2
    assert first_old in info['history'].values()
    assert second_old in info['history'].values()
    assert info.get('origin')
    persist_scene_working_draft(second_state, '默认', cfg)
    _, restored, _, restored_info = get_scene_working_draft({}, '默认', cfg)
    assert restored == current
    assert restored_info['history'] == info['history']
    # Repeated rerenders do not accumulate duplicate copies of the same legacy edit.
    get_scene_working_draft(second_state, '默认', cfg)
    assert len(second_state[key + ':meta']['history']) == 2


def test_explicit_latest_migrates_old_default_key_when_no_saved_files_exist(scenario):
    from smm.bids import bid_dir, default_bids
    cfg = scene_config(scenario)
    for path in bid_dir(cfg).glob('bids_v*.*'):
        path.unlink()
    old_current = default_bids(cfg)
    old_current['units']['C1']['noload_cost'] += 19
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state['bid_draft:默认:default'] = copy.deepcopy(old_current)
    at.session_state['quote_source:默认'] = '最新'
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == old_current
    assert at.session_state['bid_draft:默认:default'] == old_current
    assert at.session_state[current_key() + ':meta']['basis_label'] == '场景缺省报价'
    assert not at.session_state[current_key() + ':meta']['newer_basis']
    assert '场景已保存报价 v1' not in quote_notice(at)
    assert '接续' in quote_notice(at)
    assert not list(bid_dir(cfg).glob('bids_v*.*'))
    fresh = open_app()
    assert fresh.session_state[current_key()] == old_current


@pytest.mark.parametrize('source_kind', ['version', 'legacy', 'recovered'])
def test_broken_new_archive_preserves_explicit_active_legacy_draft(scenario, source_kind):
    from smm.bids import bid_dir
    cfg = scene_config(scenario)
    original = saved_bid(scenario)
    version = original['version']
    source = version if source_kind == 'version' else f'draft_默认_{version}'
    legacy_key = f'bid_draft:默认:{version}' if source_kind == 'version' else source
    legacy = copy.deepcopy(original)
    legacy['units']['C1']['noload_cost'] += 23
    expected = copy.deepcopy(legacy)
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state[legacy_key] = copy.deepcopy(legacy)
    at.session_state['quote_source:默认'] = source
    if source_kind == 'recovered':
        expected['units']['C1']['noload_cost'] += 29
        at.session_state[f'quote_recovered:默认:{legacy_key}'] = copy.deepcopy(expected)
    (bid_dir(cfg) / 'bids_v999.json').write_text('{ unreadable external newer archive')
    original_files = files(cfg.root)
    at.run()
    assert not at.exception
    assert at.session_state[current_key()] == expected
    assert item(at.number_input, '空载报价（元/h）').value == expected['units']['C1']['noload_cost']
    assert not item(at.button, '▶ 开始运行').disabled
    assert at.session_state[legacy_key] == legacy
    assert '接续' in quote_notice(at)
    assert any('读取' in message.value for message in at.warning)
    assert files(cfg.root) == original_files
    assert json.loads((cfg.root / 'current_quote.json').read_text())['draft'] == expected


@pytest.mark.parametrize('pointer', [None, '最新'])
def test_broken_latest_without_reliable_active_pointer_offers_real_legacy_backup(scenario, pointer):
    import io
    import zipfile
    from bid_workspace import uninitialized_legacy_backup
    from smm.bids import bid_dir
    cfg = scene_config(scenario)
    original = saved_bid(scenario)
    old = copy.deepcopy(original)
    old['units']['C1']['noload_cost'] += 33
    old['units']['ES1'].pop('soc_end', None)
    legacy_key = f"bid_draft:默认:{original['version']}"
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state[legacy_key] = copy.deepcopy(old)
    if pointer is not None:
        at.session_state['quote_source:默认'] = pointer
    (bid_dir(cfg) / 'bids_v999.json').write_text('{ unreadable newest archive')
    original_files = files(cfg.root)
    at.run()
    assert not at.exception
    assert item(at.button, '▶ 开始运行').disabled
    assert at.error
    assert current_key() not in at.session_state
    assert at.session_state[legacy_key] == old
    assert not (cfg.root / 'current_quote.json').exists()
    assert any('不要刷新或关闭' in message.value for message in at.warning)
    assert any(widget.label == '下载保留的旧编辑' for widget in at.get('download_button'))
    with zipfile.ZipFile(io.BytesIO(uninitialized_legacy_backup(at.session_state, '默认'))) as backup:
        archived = [json.loads(backup.read(name)) for name in backup.namelist() if name.endswith('.json')]
        assert archived == [old]
        assert legacy_key in backup.read('说明.txt').decode()
    assert files(cfg.root) == original_files
    assert not scenario.RUNS_DIR.exists()
