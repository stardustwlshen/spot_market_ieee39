import copy
from pathlib import Path
import shutil
import sys

import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest

from smm.config import ROOT, load_config
from smm.forecast import generate_profiles
from smm.forecast_settings import (apply_model_change, generation_metadata, input_fingerprint,
                                    model_name, prepare_model_change)


@pytest.fixture
def scene(tmp_path):
    root = tmp_path / 'scene'
    (root / 'config').mkdir(parents=True)
    (root / 'data').mkdir()
    for name in ('base.yaml', 'units.yaml', 'modes.yaml'):
        shutil.copy2(ROOT / 'config' / name, root / 'config' / name)
    # Explicit historical scene: the repository now defaults to multiscale_v2.
    path = root / 'config/base.yaml'
    base = yaml.safe_load(path.read_text()); base.pop('forecast', None)
    path.write_text(yaml.safe_dump(base, allow_unicode=True))
    (root / 'bids').mkdir()
    (root / 'bids/keep.json').write_text('{"do_not_touch": true}')
    (root / 'runs/old').mkdir(parents=True)
    (root / 'runs/old/profiles.csv').write_text('historical bytes\n')
    return root


def bytes_in(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_preview_readonly_and_explicit_apply_only_changes_model(scene):
    original = bytes_in(scene)
    old_cfg = load_config(root=scene)
    prepared = prepare_model_change(scene, 'multiscale_v2')
    assert bytes_in(scene) == original
    assert model_name(old_cfg) == 'legacy_v1'
    apply_model_change(scene, prepared)
    new_cfg = load_config(root=scene)
    assert model_name(new_cfg) == 'multiscale_v2'
    new_base = copy.deepcopy(new_cfg.base)
    new_base.pop('forecast')
    assert new_base == old_cfg.base
    for name, value in original.items():
        if name != 'config/base.yaml':
            assert (scene / name).read_bytes() == value
    # Explicitly reverting is possible; no intermediate result is rewritten.
    revert = prepare_model_change(scene, 'legacy_v1')
    apply_model_change(scene, revert)
    pd.testing.assert_frame_equal(generate_profiles(load_config(root=scene)), generate_profiles(old_cfg))


def test_uploaded_values_and_file_and_declared_bids_never_rewritten(scene):
    override = pd.DataFrame({'W1_actual': [123.456789] * 96,
                             'W1_da': [345.678901] * 96, 'PV1_rt': [0.] * 96})
    override.to_csv(scene / 'data/profile_overrides.csv', index=False)
    original = bytes_in(scene)
    prepared = prepare_model_change(scene, 'multiscale_v2')
    for column in override:
        pd.testing.assert_series_equal(prepared['profiles'][column], override[column])
    apply_model_change(scene, prepared)
    for name in ('data/profile_overrides.csv', 'bids/keep.json', 'runs/old/profiles.csv'):
        assert (scene / name).read_bytes() == original[name]
    metadata = generation_metadata(load_config(root=scene))
    assert set(metadata['explicit_override_columns']) == set(override)
    assert metadata['model'] == 'multiscale_v2'


@pytest.mark.parametrize('changed', ['config/base.yaml', 'config/units.yaml', 'config/modes.yaml', 'data/profile_overrides.csv'])
def test_stale_preview_rejects_newer_input_without_overwrite(scene, changed):
    prepared = prepare_model_change(scene, 'multiscale_v2')
    path = scene / changed
    path.write_text(path.read_text() + '\n# changed\n' if path.exists() else 'new upload\n')
    original = bytes_in(scene)
    with pytest.raises(RuntimeError, match='重新预览'):
        apply_model_change(scene, prepared)
    assert all((scene / name).read_bytes() == value for name, value in original.items())


def test_wrong_scene_and_invalid_candidate_rejected(scene, tmp_path):
    prepared = prepare_model_change(scene, 'multiscale_v2')
    with pytest.raises(ValueError, match='另一场景'):
        apply_model_change(tmp_path, prepared)
    prepared['model'] = 'unknown'
    with pytest.raises(ValueError, match='未知'):
        apply_model_change(scene, prepared)


def test_failed_atomic_replace_preserves_original_configuration(scene, monkeypatch):
    import smm.forecast_settings as module
    prepared = prepare_model_change(scene, 'multiscale_v2')
    old = (scene / 'config/base.yaml').read_bytes()
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk error')))
    with pytest.raises(OSError, match='disk error'):
        apply_model_change(scene, prepared)
    assert (scene / 'config/base.yaml').read_bytes() == old
    assert not list((scene / 'config').glob('.forecast-model-*.tmp'))


def control_app(scene, disabled=False, bids=None):
    sys.path.insert(0, str(ROOT / 'app'))
    app = f'''from smm.config import load_config
from forecast_controls import render_forecast_model_control
cfg = load_config(root={str(scene)!r})
render_forecast_model_control(cfg, 'test', disabled={disabled!r}, bids={bids!r})
'''
    return AppTest.from_string(app, default_timeout=30).run()


def item(elements, label):
    return next(x for x in elements if x.label == label)


def test_visible_control_requires_preview_and_explicit_confirmation(scene):
    original = bytes_in(scene)
    at = control_app(scene)
    assert not at.exception
    assert any('旧版' in x.value for x in at.caption)
    assert bytes_in(scene) == original
    item(at.button, '预览风光曲线变更').click().run()
    assert not at.exception
    assert item(at.button, '采用预览曲线模型').disabled
    assert bytes_in(scene) == original
    item(at.checkbox, '我确认改变该场景未来运行的合成风光输入').check().run()
    assert not item(at.button, '采用预览曲线模型').disabled
    item(at.button, '采用预览曲线模型').click().run()
    assert not at.exception
    assert model_name(load_config(root=scene)) == 'multiscale_v2'
    assert any('新版' in x.value for x in at.caption)
    assert 'forecast_preview:test' not in at.session_state


def test_active_run_disables_control_and_stale_preview_is_not_adopted(scene):
    at = control_app(scene, disabled=True)
    assert not at.exception
    assert item(at.button, '预览风光曲线变更').disabled
    at = control_app(scene)
    item(at.button, '预览风光曲线变更').click().run()
    path = scene / 'config/base.yaml'
    path.write_text(path.read_text() + '\n# newer scene\n')
    at.run()
    assert not at.exception
    assert item(at.button, '采用预览曲线模型').disabled
    assert any('重新预览' in x.value for x in at.warning)


def test_ui_warns_declared_da_availability_still_takes_precedence(scene):
    bids = {'units': {'W1': {'avail_pmax': [400.] * 96}}}
    at = control_app(scene, bids=bids)
    item(at.button, '预览风光曲线变更').click().run()
    assert not at.exception
    assert any('申报值' in x.value and 'W1' in x.value for x in at.warning)


def test_main_run_page_mounts_control_before_start_button():
    source = (ROOT / 'app/pages/2_运行.py').read_text()
    assert source.index('render_forecast_model_control(cfg, name, bids=draft, disabled=active)') < source.index('▶ 开始运行')
