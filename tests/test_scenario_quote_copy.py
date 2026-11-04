"""Copies retain the current workspace; new defaults never inherit old drafts."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from smm import scenario
from smm.bids import validate_bids
from smm.config import ROOT, load_config
from smm.unit_editor import apply_unit_edit, prepare_unit_edit, unit_template


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenes')
    monkeypatch.syspath_prepend(str(ROOT / 'app'))
    root = scenario.create_scenario('原场景')
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    state = {}
    cfg = load_config(root=root)
    key, _, _, _ = get_scene_working_draft(state, root.name, cfg)
    state[key + ':meta']['history']['旧版独立编辑'] = copy.deepcopy(state[key])
    state[key + ':meta']['history']['旧版独立编辑']['units']['C2']['noload_cost'] += 11
    state[key]['units']['C1']['noload_cost'] += 17
    state[key + ':manual'] = ['C1']
    persist_scene_working_draft(state, root.name, cfg)
    return root


@pytest.mark.parametrize('typical', [False, True])
def test_clone_preserves_workspace_and_typical_references_exactly(source, typical):
    if typical:
        # Legacy metadata/reference files must be copied, never regenerated.
        for relative, content in {
            'config/typical_scenario.json': b'{"schema": 1, "legacy": true}\n',
            'data/typical_baseline_profiles.csv': b'period,load_actual\n1,1234\n',
            'data/typical_source_overrides.csv': b'period,load_actual\n1,4321\n',
            'data/typical_source_current_quote.json': b'{"legacy_source": true}\n',
        }.items():
            (source / relative).write_bytes(content)
    before = snapshot(source)
    target = scenario.create_scenario('副本', source=source.name)
    assert (target / 'current_quote.json').read_bytes() == before['current_quote.json']
    for relative, content in before.items():
        if relative.startswith(('config/', 'data/', 'bids/')):
            assert (target / relative).read_bytes() == content
    assert snapshot(source) == before
    assert not (target / 'current_quote.lock').exists()

    # Exercise the real workspace reader and writer after rename. History keys
    # may mention the source, but cannot couple either scene's active editor.
    from bid_workspace import get_scene_working_draft, persist_scene_working_draft
    original = json.loads(before['current_quote.json'])
    states = {}
    for root in (source, target):
        state = states[root] = {}
        cfg = load_config(root=root)
        key, draft, baseline, info = get_scene_working_draft(state, root.name, cfg)
        assert draft == original['draft'] and baseline == original['baseline']
        assert info['history'] == original['history']
        assert state[key + ':manual'] == original['manual']
        assert not [issue for issue in validate_bids(cfg, draft, require_complete=True)
                    if issue.level == 'error']
        draft['units']['C1']['noload_cost'] += 3 if root == target else 5
        untouched = source if root == target else target
        untouched_before = snapshot(untouched)
        persist_scene_working_draft(state, root.name, cfg)
        assert snapshot(untouched) == untouched_before
    for root, delta in ((source, 5), (target, 3)):
        fresh = get_scene_working_draft({}, root.name, load_config(root=root))[1]
        assert fresh['units']['C1']['noload_cost'] == original['draft']['units']['C1']['noload_cost'] + delta
    assert snapshot(source) | {'current_quote.json': before['current_quote.json']} == before


def test_copy_without_current_quote_keeps_saved_archive_fallback(source):
    from bid_workspace import get_scene_working_draft
    (source / 'current_quote.json').unlink()
    before = snapshot(source)
    target = scenario.create_scenario('无草稿副本', source=source.name)
    assert not (target / 'current_quote.json').exists()
    expected = get_scene_working_draft({}, source.name, load_config(root=source))[1]
    assert get_scene_working_draft({}, target.name, load_config(root=target))[1] == expected
    assert snapshot(source) == before


def test_new_defaults_use_repository_config_without_either_current_quote(source, monkeypatch):
    # A repository draft is not the basis for "new from project defaults" either.
    repo = source.parent.parent / 'repository'
    scenario._copy_tree(ROOT, repo)
    (repo / 'current_quote.json').write_bytes(b'{"repository_draft": true}\n')
    monkeypatch.setattr(scenario, 'ROOT', repo)
    original, repository = snapshot(source), snapshot(repo)
    target = scenario.create_scenario('新版默认')
    assert not (target / 'current_quote.json').exists()
    assert (target / 'config/units.yaml').read_bytes() == repository['config/units.yaml']
    assert scenario.scenario_meta(target.name)['source'] == '仓库 config/'
    assert snapshot(source) == original and snapshot(repo) == repository


def test_duplicate_copy_requests_never_overwrite(source, monkeypatch):
    before = snapshot(source)
    barrier = threading.Barrier(2)
    real_copy = scenario._copy_tree

    def simultaneous(*args, **kwargs):
        barrier.wait(timeout=10)  # Both callers have passed the initial exists check.
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(scenario, '_copy_tree', simultaneous)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(scenario.create_scenario, '同名副本', source.name) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=30))
            except FileExistsError:
                outcomes.append('exists')
    target = scenario.scenario_path('同名副本')
    assert outcomes.count(target) == outcomes.count('exists') == 1
    assert (target / 'current_quote.json').read_bytes() == before['current_quote.json']
    target_before = snapshot(target)
    with pytest.raises(FileExistsError):
        scenario.create_scenario(target.name, source.name)
    assert snapshot(target) == target_before and snapshot(source) == before


def test_failed_quote_copy_removes_only_its_claimed_destination(source, monkeypatch):
    before = snapshot(source)
    real_copy = scenario.shutil.copy2

    def fail_quote(src, dst, *args, **kwargs):
        if Path(src).name == 'current_quote.json':
            Path(dst).write_bytes(b'partial')
            raise OSError('injected quote copy failure')
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(scenario.shutil, 'copy2', fail_quote)
    with pytest.raises(OSError, match='quote copy failure'):
        scenario.create_scenario('复制失败', source.name)
    assert not scenario.scenario_path('复制失败').exists()
    assert snapshot(source) == before
    with pytest.raises(FileExistsError):
        scenario._copy_tree(source, source)
    assert snapshot(source) == before


def test_clone_waits_for_unit_edit_and_keeps_matching_current_quote(source, monkeypatch):
    from smm import unit_edit_transaction as tx
    plan = prepare_unit_edit(source, 'storage', unit_template('storage', 'NEW_STORE', 31))
    wrote_units, finish, clone_started = threading.Event(), threading.Event(), threading.Event()
    real_write, real_copy = tx.atomic_bytes, scenario._copy_tree

    def slow(path, content):
        real_write(path, content)
        if Path(path) == source / 'config/units.yaml':
            wrote_units.set()
            assert finish.wait(10)

    def copying(*args, **kwargs):
        clone_started.set()
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(tx, 'atomic_bytes', slow)
    monkeypatch.setattr(scenario, '_copy_tree', copying)
    with ThreadPoolExecutor(2) as pool:
        edited = pool.submit(apply_unit_edit, plan)
        assert wrote_units.wait(10)
        copied = pool.submit(scenario.create_scenario, '编辑后副本', source.name)
        try:
            assert clone_started.wait(10)
            assert not copied.done()
        finally:
            finish.set()
        edited.result(timeout=30)
        target = copied.result(timeout=30)
    cfg = load_config(root=target)
    assert 'NEW_STORE' in {unit['id'] for unit in cfg.units['storage']}
    assert (target / 'current_quote.json').read_bytes() == (source / 'current_quote.json').read_bytes()
    from bid_workspace import get_scene_working_draft
    draft = get_scene_working_draft({}, target.name, cfg)[1]
    assert 'NEW_STORE' in draft['units']
    assert not [issue for issue in validate_bids(cfg, draft, require_complete=True) if issue.level == 'error']
