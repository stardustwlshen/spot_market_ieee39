"""Error recipes reach DA/ID/RT without mutating inputs or user forecasts."""
import copy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import yaml

from smm import scenario
from smm.bids import default_bids, save_bids
from smm.config import ROOT, load_config
from smm.forecast import generate_profiles, apply_profile_overrides, forecast_view
from smm.pipeline import prepare
from smm.profile_sources import override_sources
from smm.resource_error_scenarios import apply_error_scenarios
from smm.typical_scenarios import prepare_typical, create_typical, MANIFEST


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    root = tmp_path / 'source'
    scenario._copy_tree(ROOT, root)
    cfg = load_config(root=root)
    bids = default_bids(cfg)
    bids['units']['C1']['segments'][-1]['price'] += 13
    save_bids(cfg, bids)
    (root / 'current_quote.json').write_text(json.dumps({'schema': 1, 'draft': bids}), encoding='utf-8')
    return root


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def config_with_errors(root, **choices):
    cfg = load_config(root=root)
    cfg.base['forecast'] = {**(cfg.base.get('forecast') or {}), 'error_scenarios': {'version': 1, **choices}}
    return cfg


@pytest.mark.parametrize('model', ['legacy_v1', 'multiscale_v2'])
def test_no_selection_and_explicit_configured_keep_legacy_bytes(source, model):
    cfg = load_config(root=source)
    cfg.base['forecast'] = {'model': model}
    expected = apply_profile_overrides(cfg, generate_profiles(cfg))
    cfg.base['forecast']['error_scenarios'] = {'version': 1, 'load': 'configured', 'wind': 'configured', 'pv': 'configured'}
    actual = apply_profile_overrides(cfg, generate_profiles(cfg))
    pd.testing.assert_frame_equal(expected, actual)


@pytest.mark.parametrize('model', ['legacy_v1', 'multiscale_v2'])
@pytest.mark.parametrize('columns', [[], ['load_actual'], ['W1_actual'], ['W1_id'],
    ['load_actual', 'load_da', 'W1_actual', 'W1_rt'],
    [f'{name}_{stage}' for name in ('load', 'W1', 'W2', 'PV') for stage in ('actual', 'da', 'id', 'rt')]])
def test_upload_matrix_preserves_every_explicit_value_and_uses_final_actual(source, columns, model):
    cfg = config_with_errors(source, load='lag', wind='overestimate', pv='underestimate')
    cfg.base['forecast']['model'] = model
    generated = generate_profiles(cfg)
    # Resource IDs are configuration-defined, not inferred from position.
    pv_id = next(u['id'] for u in cfg.units['renewable'] if u['type'] == 'pv')
    columns = [c.replace('PV_', pv_id + '_') for c in columns]
    upload = generated[columns].copy()
    for column in columns:
        upload[column] *= .7
    if columns:
        upload.to_csv(source / 'data/profile_overrides.csv', index=False)
    before = hashes(source)
    result = apply_profile_overrides(cfg, generated)
    for column in columns:
        np.testing.assert_allclose(result[column], upload[column], rtol=0, atol=1e-12)
    expected = apply_error_scenarios(cfg, result, protected_columns=columns)
    pd.testing.assert_frame_equal(result, expected)
    for column in generated:
        if column.endswith('_actual') and column not in columns:
            np.testing.assert_array_equal(result[column], generated[column])
    assert hashes(source) == before


def test_invalid_uploaded_prediction_fails_without_truncation(source):
    cfg = config_with_errors(source, wind='normal')
    cap = next(u['cap'] for u in cfg.units['renewable'] if u['id'] == 'W1')
    upload = pd.DataFrame({'W1_da': [cap + 1.] * cfg.T})
    with pytest.raises(ValueError, match='W1_da'):
        apply_profile_overrides(cfg, generate_profiles(cfg), upload)


def test_error_only_keeps_actual_quote_bytes_and_preview_matches_pipeline(source):
    cfg = load_config(root=source)
    path = source / 'current_quote.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    record['draft']['units']['W1']['avail_pmax'] = [123.456] * cfg.T
    path.write_text(json.dumps(record), encoding='utf-8')
    before = hashes(source)
    base = prepare_typical(source)
    preview = prepare_typical(source, error_scenarios={'load': 'underestimate', 'wind': 'normal', 'pv': 'lag'})
    assert preview['availability_changes'] == []
    assert preview['bids'] == record['draft']
    for col in base['profiles']:
        if col.endswith('_actual'):
            np.testing.assert_array_equal(base['profiles'][col], preview['profiles'][col])
    assert any('avail_pmax' in warning for warning in preview['source_warnings'])
    created = create_typical('error_only', preview)
    prepared = prepare(load_config(root=created), verbose=False, read_only=True)
    np.testing.assert_array_equal(prepared.ren_fc['W1'], np.full(cfg.T, 123.456))
    for column in preview['profiles']:
        if column.endswith(('_actual', '_da', '_id', '_rt')):
            np.testing.assert_allclose(prepared.profiles[column], preview['profiles'][column], atol=1e-10)
    assert prepared.profiles.attrs['generation_metadata']['error_definition'] == 'forecast_minus_actual'
    assert hashes(source) == before


def test_reselection_restores_configured_baseline_and_never_accumulates(source):
    original = prepare_typical(source, wind_multiplier=.8, load_multiplier=.9)
    first = create_typical('first', prepare_typical(source, wind_multiplier=.8, load_multiplier=.9,
                                                  error_scenarios={'load': 'overestimate', 'wind': 'normal'}))
    second_preview = prepare_typical(first, wind_multiplier=.8, load_multiplier=.9,
                                    error_scenarios={'load': 'underestimate', 'wind': 'lag'})
    direct = prepare_typical(source, wind_multiplier=.8, load_multiplier=.9,
                             error_scenarios={'load': 'underestimate', 'wind': 'lag'})
    pd.testing.assert_frame_equal(second_preview['profiles'], direct['profiles'], check_exact=False, atol=1e-9, rtol=1e-12)
    second = create_typical('second', second_preview)
    restored = prepare_typical(second, wind_multiplier=.8, load_multiplier=.9,
                              error_scenarios={'load': 'configured', 'wind': 'configured'})
    pd.testing.assert_frame_equal(restored['profiles'], original['profiles'], check_exact=False, atol=1e-9, rtol=1e-12)
    proof = override_sources(load_config(root=second))
    assert proof['status'] == 'verified_generated_columns'
    assert 'W1_id' in proof['generated_columns']


def test_protected_upload_survives_multiple_saved_copies(source):
    cfg = load_config(root=source)
    pd.DataFrame({'load_id': [3456.789] * cfg.T}).to_csv(source / 'data/profile_overrides.csv', index=False)
    first = create_typical('first', prepare_typical(source, error_scenarios={'load': 'normal'}))
    second = create_typical('second', prepare_typical(first, error_scenarios={'load': 'overestimate'}))
    frame = prepare(load_config(root=second), read_only=True, verbose=False).profiles
    np.testing.assert_array_equal(frame.load_id, np.full(cfg.T, 3456.789))
    assert 'load_id' in override_sources(load_config(root=second))['protected_columns']


def test_all_uploaded_forecasts_report_no_application(source):
    frame = generate_profiles(load_config(root=source))
    frame[[c for c in frame if c.endswith(('_actual', '_da', '_id', '_rt'))]].to_csv(source / 'data/profile_overrides.csv', index=False)
    preview = prepare_typical(source, error_scenarios={'load': 'normal', 'wind': 'normal', 'pv': 'normal'})
    assert all(row['变化时段数'] == 0 and row['受保护'] for row in preview['error_effect_summary'])
    assert all(not row['applied_columns'] for row in preview['generation_metadata']['error_application'])
    assert any('不会应用' in warning for warning in preview['source_warnings'])


def test_saved_recipe_can_change_without_generated_columns_masking_it(source):
    created = create_typical('first', prepare_typical(source, wind_multiplier=.8, error_scenarios={'wind': 'overestimate'}))
    cfg = config_with_errors(created, wind='underestimate')
    result = apply_profile_overrides(cfg, generate_profiles(cfg))
    direct = prepare_typical(source, wind_multiplier=.8, error_scenarios={'wind': 'underestimate'})
    np.testing.assert_allclose(result.W1_id, direct['profiles'].W1_id)
    cfg.base['forecast']['error_scenarios'] = {'wind': 'configured'}
    restored = apply_profile_overrides(cfg, generate_profiles(cfg))
    np.testing.assert_allclose(restored.W1_id, prepare_typical(source, wind_multiplier=.8)['profiles'].W1_id)


def test_combined_weather_and_error_keeps_quote_confirmation_and_price_segments(source):
    cfg = load_config(root=source)
    bids = json.loads((source / 'current_quote.json').read_text(encoding='utf-8'))['draft']
    bids['units']['W1']['avail_pmax'] = [500.] * cfg.T
    preview = prepare_typical(source, wind_multiplier=.8, error_scenarios={'wind': 'underestimate'}, bids=bids)
    assert preview['availability_changes'] == ['W1']
    assert preview['bids']['units']['W1']['avail_pmax'] == [400.] * cfg.T
    with pytest.raises(ValueError):
        create_typical('unconfirmed', preview, bids=bids)
    created = create_typical('confirmed', preview, bids=bids, confirm_availability=True)
    case = prepare(load_config(root=created), read_only=True, verbose=False)
    np.testing.assert_allclose(case.ren_fc['W1'], 400.)
    assert case.bids['units']['C1']['segments'] == bids['units']['C1']['segments']


def test_pipeline_metadata_uses_actual_bid_data_not_old_manifest(source):
    created = create_typical('first', prepare_typical(source, error_scenarios={'wind': 'normal'}))
    cfg = load_config(root=created)
    bids = default_bids(cfg)
    bids['units']['W1']['avail_pmax'] = [333.] * cfg.T
    frame = prepare(cfg, read_only=True, verbose=False, bid_data=bids).profiles
    assert frame.attrs['generation_metadata']['quote_override_columns'] == ['W1_da']
    rows = frame.attrs['generation_metadata']['column_sources']
    assert next(r for r in rows if r['列'] == 'W1_da')['来源'] == '报价可用量优先'
    bids['units']['W1']['avail_pmax'] = None
    assert prepare(cfg, read_only=True, verbose=False, bid_data=bids).profiles.attrs['generation_metadata']['quote_override_columns'] == []


def test_pv_night_quote_rejected_consistently_only_when_selected(source):
    cfg = config_with_errors(source, pv='normal')
    uid = next(u['id'] for u in cfg.units['renewable'] if u['type'] == 'pv')
    bids = default_bids(cfg)
    bids['units'][uid]['avail_pmax'] = [100.] * cfg.T
    with pytest.raises(ValueError, match=uid):
        prepare(cfg, read_only=True, verbose=False, bid_data=bids)
    with pytest.raises(ValueError, match=uid):
        prepare_typical(source, bids=bids, error_scenarios={'pv': 'normal'})
    cfg.base['forecast']['error_scenarios'] = {'pv': 'configured'}
    np.testing.assert_allclose(prepare(cfg, read_only=True, verbose=False, bid_data=bids).ren_fc[uid], 100.)


def test_metadata_without_bids_does_not_claim_quote_verified(source):
    from smm.forecast_settings import generation_metadata
    assert generation_metadata(load_config(root=source))['quote_context'] == 'not_evaluated'


def test_changed_override_hash_and_old_manifest_preserve_columns(source):
    created = create_typical('first', prepare_typical(source, error_scenarios={'wind': 'normal'}))
    file = created / 'data/profile_overrides.csv'
    frame = pd.read_csv(file)
    frame['W1_id'] = 222.222
    frame.to_csv(file, index=False)
    cfg = config_with_errors(created, wind='overestimate')
    assert override_sources(cfg)['status'] == 'unverified_preserve_all'
    np.testing.assert_allclose(apply_profile_overrides(cfg, generate_profiles(cfg)).W1_id, 222.222)
    with pytest.raises(ValueError):
        prepare_typical(created, error_scenarios={'wind': 'overestimate'})
    path = created / MANIFEST
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest.pop('profile_sources')
    path.write_text(json.dumps(manifest), encoding='utf-8')
    assert override_sources(cfg)['status'] == 'unverified_preserve_all'
    assert override_sources(cfg)['warnings']
    np.testing.assert_allclose(apply_profile_overrides(cfg, generate_profiles(cfg)).W1_id, 222.222)


def test_stale_preview_rejected_and_resource_choices_independent(source):
    first = prepare_typical(source, error_scenarios={'wind': 'normal'})
    both = prepare_typical(source, error_scenarios={'wind': 'normal', 'load': 'lag'})
    np.testing.assert_array_equal(first['profiles'].W1_da, both['profiles'].W1_da)
    path = source / 'config/base.yaml'
    path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
    with pytest.raises(RuntimeError):
        create_typical('stale', first)


@pytest.mark.parametrize('first_actual', [False, True])
def test_rolling_lead_interpolation_and_actual_first_switch(source, first_actual):
    preview = prepare_typical(source, error_scenarios={'wind': 'lag'})
    frame = preview['profiles']
    periods = [0, 1, 8, 16, 20]
    result = forecast_view(frame, 'W1', 0, periods, first_actual=first_actual)
    for i, t in enumerate(periods):
        weight = min(1., (max(1, t) - 1.) / 15.)
        expected = frame.W1_actual.iloc[t] if t == 0 and first_actual else (1-weight)*frame.W1_rt.iloc[t] + weight*frame.W1_id.iloc[t]
        assert result[i] == pytest.approx(expected)
