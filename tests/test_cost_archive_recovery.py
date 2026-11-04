"""Old scenes can explicitly adopt missing research archives, never overwrite."""
from pathlib import Path
import shutil

import pytest
import yaml

from smm import cost_archive as ca
from smm.bid_simulation import research_cost_bid_preview
from smm.config import ROOT, load_config


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and p.name != '.cost_archive.lock'}


@pytest.fixture
def roots(tmp_path):
    source, target = tmp_path / 'repository', tmp_path / '旧场景'
    shutil.copytree(ROOT / 'config', source / 'config')
    shutil.copytree(ROOT / 'config', target / 'config')
    for name in ca.COST_FILES:
        (target / 'config' / name).unlink()
    (target / 'current_quote.json').write_bytes(b'user current quote must stay exact')
    (target / 'bids').mkdir()
    (target / 'bids/user.json').write_bytes(b'user bid archive must stay exact')
    return target, source


def test_plan_is_read_only_and_import_enables_cost_preview(roots):
    target, source = roots
    before, original = snapshot(target), snapshot(source)
    with pytest.raises(ValueError, match='缺少显式成本研究档案'):
        research_cost_bid_preview(load_config(root=target), selected_unit_ids=['C1'])
    plan = ca.prepare_cost_archive_import(target, source)
    assert snapshot(target) == before
    assert plan.missing == ca.COST_FILES
    assert ca.apply_cost_archive_import(plan) == list(ca.COST_FILES)
    assert all((target / name).read_bytes() == content for name, content in before.items())
    assert snapshot(source) == original
    for name in ca.COST_FILES:
        assert (target / 'config' / name).read_bytes() == (source / 'config' / name).read_bytes()
    out = research_cost_bid_preview(load_config(root=target), selected_unit_ids=['C1'])
    assert out['units']['C1']['segments'][1]['price'] == 280
    done = snapshot(target)
    assert ca.apply_cost_archive_import(ca.prepare_cost_archive_import(target, source)) == []
    assert snapshot(target) == done


@pytest.mark.parametrize('existing', ca.COST_FILES)
def test_existing_cost_archive_keeps_exact_bytes_and_values(roots, existing):
    target, source = roots
    content = (source / 'config' / existing).read_bytes() + b'\n# user custom archive stays exact\n'
    if existing == 'fixed_costs.yaml':
        content = content.replace(b': 280,', b': 281,', 1)
    (target / 'config' / existing).write_bytes(content)
    plan = ca.prepare_cost_archive_import(target, source)
    assert existing not in plan.missing
    ca.apply_cost_archive_import(plan)
    assert (target / 'config' / existing).read_bytes() == content
    if existing == 'fixed_costs.yaml':
        out = research_cost_bid_preview(load_config(root=target), selected_unit_ids=['C1'])
        assert out['units']['C1']['segments'][1]['price'] == 281


@pytest.mark.parametrize('changed', ['source', 'scene', 'new_cost'])
def test_changed_plan_cannot_write_stale_assumptions(roots, changed):
    target, source = roots
    plan = ca.prepare_cost_archive_import(target, source)
    if changed == 'source':
        path = source / 'config/fixed_costs.yaml'
    elif changed == 'scene':
        path = target / 'config/base.yaml'
    else:
        path = target / 'config/fixed_costs.yaml'
        shutil.copy2(source / 'config/fixed_costs.yaml', path)
    path.write_bytes(path.read_bytes() + b'\n# changed by another user\n')
    before = snapshot(target)
    with pytest.raises(RuntimeError, match='已变化'):
        ca.apply_cost_archive_import(plan)
    assert snapshot(target) == before


def test_partial_failure_keeps_complete_files_and_retry_only_fills_remaining(roots, monkeypatch):
    target, source = roots
    link = ca.os.link
    def fail_second(src, dst):
        if Path(dst).name == 'bid_cost_basis.yaml':
            raise PermissionError('test write failure')
        return link(src, dst)
    monkeypatch.setattr(ca.os, 'link', fail_second)
    with pytest.raises(RuntimeError, match='已完整补充：fixed_costs.yaml'):
        ca.apply_cost_archive_import(ca.prepare_cost_archive_import(target, source))
    assert (target / 'config/fixed_costs.yaml').read_bytes() == (source / 'config/fixed_costs.yaml').read_bytes()
    assert not (target / 'config/bid_cost_basis.yaml').exists()
    assert not list((target / 'config').glob('.cost-*'))
    monkeypatch.setattr(ca.os, 'link', link)
    plan = ca.prepare_cost_archive_import(target, source)
    assert plan.missing == ('bid_cost_basis.yaml',)
    assert ca.apply_cost_archive_import(plan) == ['bid_cost_basis.yaml']


def test_atomic_publish_does_not_overwrite_racing_writer(roots, monkeypatch):
    target, source = roots
    link = ca.os.link
    def competing_write(src, dst):
        Path(dst).write_bytes(b'concurrent user-owned data')
        return link(src, dst)
    monkeypatch.setattr(ca.os, 'link', competing_write)
    with pytest.raises(RuntimeError, match='未全部完成'):
        ca.apply_cost_archive_import(ca.prepare_cost_archive_import(target, source))
    assert (target / 'config/fixed_costs.yaml').read_bytes() == b'concurrent user-owned data'
    assert not (target / 'config/bid_cost_basis.yaml').exists()
    assert not list((target / 'config').glob('.cost-*'))


def test_failed_stage_write_leaves_no_partial_destination(roots, monkeypatch):
    target, source = roots
    before = snapshot(target)
    plan = ca.prepare_cost_archive_import(target, source)
    def failure(fd):
        raise OSError('test failed fsync')
    monkeypatch.setattr(ca.os, 'fsync', failure)
    with pytest.raises(RuntimeError, match='已完整补充：无'):
        ca.apply_cost_archive_import(plan)
    assert snapshot(target) == before
    assert not list((target / 'config').glob('.cost-*'))


def test_custom_resource_mismatch_fails_without_creating_any_files(roots):
    target, source = roots
    path = target / 'config/units.yaml'
    data = yaml.safe_load(path.read_text())
    data['thermal'][0]['id'] = 'custom_unit'
    path.write_text(yaml.safe_dump(data))
    before = snapshot(target)
    with pytest.raises(ValueError, match='custom_unit'):
        ca.prepare_cost_archive_import(target, source)
    assert snapshot(target) == before


@pytest.mark.parametrize('kind', ['malformed', 'bad_yaml', 'not_assumptions', 'dangling_link'])
def test_invalid_archive_or_occupied_path_is_not_replaced(roots, kind):
    target, source = roots
    path = target / 'config/fixed_costs.yaml'
    if kind == 'malformed':
        path.write_text('user: broken archive\n')
    elif kind == 'bad_yaml':
        path.write_text('units: [invalid: YAML:\n')
    elif kind == 'not_assumptions':
        other = source / 'config/fixed_costs.yaml'
        other.write_text(other.read_text().replace('research_assumption_not_observed_plant_data', 'unknown'))
    else:
        path.symlink_to(target / 'does-not-exist')
    before = snapshot(target)
    with pytest.raises(ValueError):
        ca.prepare_cost_archive_import(target, source)
    assert snapshot(target) == before
    assert not (target / 'config/bid_cost_basis.yaml').exists()
