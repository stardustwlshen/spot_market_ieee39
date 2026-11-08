"""Research defaults activate as separate versions, never rewriting user archives."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import sys

import pytest
import yaml

from smm.config import ROOT, load_config
from smm.profit import load_fixed_cost_profile
from smm.bid_cost_basis import research_cost_worksheet
from smm.bid_simulation import research_cost_bid_preview
from smm.research_cost_setup import (FILENAME, prepare_generated_cost_setup,
    apply_generated_cost_setup, initialize_missing_cost_defaults, read_generated_costs)
from smm.research_defaults import research_record_hash


@pytest.fixture
def scene(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    return tmp_path


def originals(root):
    return {p: p.read_bytes() for p in (root / 'config').iterdir() if p.name != FILENAME}


def test_explicit_generation_changes_legacy_defaults_not_original_files(scene):
    before = originals(scene)
    cfg = load_config(root=scene)
    plan = prepare_generated_cost_setup(cfg)
    assert plan['changed'] and originals(scene) == before
    assert not (scene / 'config' / FILENAME).exists()
    apply_generated_cost_setup(plan)
    profile = load_fixed_cost_profile(cfg)
    assert profile['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == pytest.approx(277.4619066987628)
    assert profile['units']['C2']['fixed_online_cost_yuan_per_h'] == pytest.approx(6000 * 725 / 646)
    assert profile['units']['ES1'] == profile['units']['ES3']
    assert originals(scene) == before
    assert not prepare_generated_cost_setup(cfg)['changed']
    bid = research_cost_bid_preview(cfg, selected_unit_ids=['C2'])
    assert bid['units']['C2']['segments'][1]['price'] == 277.46


@pytest.mark.parametrize('kind', ['modified', 'observed'])
def test_custom_or_observed_costs_never_replaced(scene, kind):
    path = scene / 'config/fixed_costs.yaml'
    profile = yaml.safe_load(path.read_text())
    if kind == 'modified':
        profile['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] = 451
    else:
        profile['source_kind'] = 'observed'
    path.write_text(yaml.safe_dump(profile))
    before = path.read_bytes()
    cfg = load_config(root=scene)
    plan = prepare_generated_cost_setup(cfg)
    assert 'C2' in plan['preserved_units']
    apply_generated_cost_setup(plan)
    assert load_fixed_cost_profile(cfg)['units']['C2'] == profile['units']['C2']
    assert path.read_bytes() == before


def test_new_units_and_missing_archives_use_parameters_without_id_lookup(scene):
    units_path = scene / 'config/units.yaml'
    units = yaml.safe_load(units_path.read_text())
    units['thermal'][0]['id'] = 'NEW_COAL'
    units_path.write_text(yaml.safe_dump(units))
    for name in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
        (scene / 'config' / name).unlink()
    initialize_missing_cost_defaults(scene)
    cfg = load_config(root=scene)
    assert load_fixed_cost_profile(cfg)['units']['NEW_COAL']['scalar_marginal_cost_yuan_per_mwh'] == 280
    assert research_cost_worksheet(cfg)['thermal']['NEW_COAL']['heat_rate'] == 9
    assert not (scene / 'config/fixed_costs.yaml').exists()


def test_new_scene_initialization_only_fills_missing_records(scene):
    path = scene / 'config/fixed_costs.yaml'
    fixed = yaml.safe_load(path.read_text())
    del fixed['units']['ES1']
    path.write_text(yaml.safe_dump(fixed))
    initialize_missing_cost_defaults(scene)
    current = load_fixed_cost_profile(load_config(root=scene))
    assert current['units']['ES1']['discharge_degradation_yuan_per_mwh'] == 25
    assert current['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == 297


def test_later_user_edit_wins_over_active_research_overlay(scene):
    cfg = load_config(root=scene)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    path = scene / 'config/fixed_costs.yaml'
    fixed = yaml.safe_load(path.read_text())
    fixed['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] = 499
    path.write_text(yaml.safe_dump(fixed))
    assert load_fixed_cost_profile(cfg)['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == 499


def test_later_observed_source_label_cannot_be_overlaid_or_mislabeled(scene):
    cfg = load_config(root=scene)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    path = scene / 'config/fixed_costs.yaml'
    fixed = yaml.safe_load(path.read_text())
    fixed['source_kind'] = 'observed'
    fixed['source'] = 'User confirmed measured costs'
    path.write_text(yaml.safe_dump(fixed))
    profile = load_fixed_cost_profile(cfg)
    assert profile['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == 297
    assert profile['source_kind'] == 'observed'
    assert 'C2' in prepare_generated_cost_setup(cfg)['preserved_units']


def test_missing_only_initialization_preserves_inherited_generated_values(scene):
    cfg = load_config(root=scene)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    before = load_fixed_cost_profile(cfg)['units']['C1']
    path = scene / 'config/units.yaml'
    units = yaml.safe_load(path.read_text())
    units['thermal'][0]['pmax'] = 700
    path.write_text(yaml.safe_dump(units))
    initialize_missing_cost_defaults(scene)
    assert load_fixed_cost_profile(load_config(root=scene))['units']['C1'] == before


def test_missing_only_preserves_old_units_and_generates_new_units(scene):
    apply_generated_cost_setup(prepare_generated_cost_setup(load_config(root=scene)))
    path = scene / 'config/units.yaml'
    units = yaml.safe_load(path.read_text())
    units['thermal'][0]['pmax'] = 700
    added = dict(units['thermal'][0], id='NEW_COAL')
    units['thermal'].append(added)
    path.write_text(yaml.safe_dump(units))
    initialize_missing_cost_defaults(scene)
    profile = load_fixed_cost_profile(load_config(root=scene))
    assert profile['units']['C1']['fixed_online_cost_yuan_per_h'] == 6000
    assert profile['units']['NEW_COAL']['fixed_online_cost_yuan_per_h'] == pytest.approx(6000 * 700 / 646)
    assert profile['unit_sources']['C1']['evidence']['capacity_mw'] == 646
    assert profile['unit_sources']['NEW_COAL']['evidence']['capacity_mw'] == 700


def test_missing_cash_record_and_preserved_quote_have_separate_evidence(scene):
    apply_generated_cost_setup(prepare_generated_cost_setup(load_config(root=scene)))
    path = scene / 'config/units.yaml'
    units = yaml.safe_load(path.read_text())
    units['thermal'][0]['pmax'] = 700
    path.write_text(yaml.safe_dump(units))
    path = scene / 'config/fixed_costs.yaml'
    fixed = yaml.safe_load(path.read_text())
    del fixed['units']['C1']
    path.write_text(yaml.safe_dump(fixed))
    initialize_missing_cost_defaults(scene)
    data = read_generated_costs(scene)
    assert data['evidence']['C1']['capacity_mw'] == 700
    assert data['evidence']['C1']['record_hash'] == research_record_hash(data['profile']['units']['C1'])
    assert data['worksheet_evidence']['thermal']['C1']['capacity_mw'] == 646
    assert data['worksheet_evidence']['thermal']['C1']['record_hash'] == research_record_hash(data['worksheet']['thermal']['C1'])


def test_linked_config_directory_is_never_written(scene, tmp_path):
    alias = tmp_path / 'alias'
    alias.mkdir()
    before = originals(scene)
    try:
        (alias / 'config').symlink_to(scene / 'config', target_is_directory=True)
    except OSError as exc:
        if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
            assert originals(scene) == before
            assert not (scene / 'config' / FILENAME).exists()
            pytest.skip('Windows user lacks symbolic-link privilege (WinError 1314)')
        raise
    with pytest.raises(ValueError, match='目录链接'):
        prepare_generated_cost_setup(load_config(root=alias))
    assert originals(scene) == before
    assert not (scene / 'config' / FILENAME).exists()


@pytest.mark.parametrize('value', [[], None, 42, 'unexpected'])
def test_invalid_generated_json_shape_has_business_error(scene, value):
    (scene / 'config' / FILENAME).write_text(json.dumps(value))
    with pytest.raises(ValueError, match='不完整'):
        read_generated_costs(scene)


def test_old_archive_import_cannot_shadow_effective_generated_costs(scene):
    from smm.cost_archive import prepare_cost_archive_import
    for name in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
        (scene / 'config' / name).unlink()
    initialize_missing_cost_defaults(scene)
    with pytest.raises(ValueError, match='页面生成流程'):
        prepare_cost_archive_import(scene, ROOT)
    assert load_fixed_cost_profile(load_config(root=scene))['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == pytest.approx(277.4619066987628)


def test_mixed_worksheet_never_labels_generated_missing_unit_as_observed(scene):
    path = scene / 'config/bid_cost_basis.yaml'
    worksheet = yaml.safe_load(path.read_text())
    worksheet.update(source_kind='observed', source='User confirmed offer basis')
    del worksheet['thermal']['C1']
    path.write_text(yaml.safe_dump(worksheet))
    before = path.read_bytes()
    cfg = load_config(root=scene)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    result = research_cost_worksheet(cfg)
    assert result['source_kind'] == 'mixed_explicit_and_synthetic_costs'
    assert result['unit_sources']['C1']['source_kind'] == 'research_assumption_not_observed_plant_data'
    assert result['unit_sources']['C2']['source_kind'] == 'observed'
    assert result['thermal']['C2'] == worksheet['thermal']['C2']
    assert path.read_bytes() == before


@pytest.mark.parametrize('existing_overlay', [True, False])
def test_create_scene_inherits_generated_costs_or_fills_missing(scene, monkeypatch, existing_overlay):
    from smm import scenario
    shutil.copytree(ROOT / 'data/case39', scene / 'data/case39')
    if existing_overlay:
        apply_generated_cost_setup(prepare_generated_cost_setup(load_config(root=scene)))
    else:
        for name in ('fixed_costs.yaml', 'bid_cost_basis.yaml'):
            (scene / 'config' / name).unlink()
    before = originals(scene)
    monkeypatch.setattr(scenario, 'ROOT', scene)
    monkeypatch.setattr(scenario, 'SCEN_DIR', scene / 'scenes')
    target = scenario.create_scenario('新场景')
    effective = load_fixed_cost_profile(load_config(root=target))
    assert effective['units']['C2']['scalar_marginal_cost_yuan_per_mwh'] == pytest.approx(277.4619066987628)
    assert originals(scene) == before
    if existing_overlay:
        assert (target / 'config' / FILENAME).read_bytes() == (scene / 'config' / FILENAME).read_bytes()
    else:
        assert not (target / 'config/fixed_costs.yaml').exists()


def test_versions_and_concurrent_activation_are_idempotent(scene):
    cfg = load_config(root=scene)
    plan = prepare_generated_cost_setup(cfg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: apply_generated_cost_setup(plan), range(2)))
    assert len(set(paths)) == 1
    assert len(list((scene / 'research_cost_versions').glob('*.json'))) == 1
    original_version = next((scene / 'research_cost_versions').glob('*.json')).read_bytes()
    units_path = scene / 'config/units.yaml'
    units = yaml.safe_load(units_path.read_text())
    units['thermal'][0]['pmax'] = 700
    units_path.write_text(yaml.safe_dump(units))
    apply_generated_cost_setup(prepare_generated_cost_setup(load_config(root=scene)))
    assert len(list((scene / 'research_cost_versions').glob('*.json'))) == 2
    assert original_version in [p.read_bytes() for p in (scene / 'research_cost_versions').glob('*.json')]


def test_failed_activation_can_retry_without_losing_previous_version(scene, monkeypatch):
    import smm.research_cost_setup as setup
    plan = prepare_generated_cost_setup(load_config(root=scene))
    replace = setup.os.replace
    def fail(*args):
        raise OSError('simulated atomic activation failure')
    monkeypatch.setattr(setup.os, 'replace', fail)
    with pytest.raises(OSError):
        apply_generated_cost_setup(plan)
    assert not (scene / 'config' / FILENAME).exists()
    assert not list((scene / 'config').glob('.research-cost-*'))
    monkeypatch.setattr(setup.os, 'replace', replace)
    apply_generated_cost_setup(plan)
    assert load_fixed_cost_profile(load_config(root=scene))['available']


def test_modified_generated_record_and_stale_plan_are_rejected(scene):
    cfg = load_config(root=scene)
    plan = prepare_generated_cost_setup(cfg)
    path = scene / 'config/fixed_costs.yaml'
    path.write_bytes(path.read_bytes() + b'\n# external revision\n')
    with pytest.raises(RuntimeError):
        apply_generated_cost_setup(plan)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    active = scene / 'config' / FILENAME
    active.write_text(active.read_text().replace('280', '281', 1))
    with pytest.raises(ValueError, match='修改'):
        load_fixed_cost_profile(cfg)
