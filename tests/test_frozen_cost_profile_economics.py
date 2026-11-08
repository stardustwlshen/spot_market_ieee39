"""JSON-only CLI costs require this run's manifest, not a copied source hash."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from result_economics import _frozen_evidence, build_unit_economics, summarize_unit_economics
from smm import spot_costs


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


def reindex(run):
    entries = []
    for name in ('fixed_cost_profile.json', 'summary.json'):
        raw = (run / name).read_bytes()
        entries.append(dict(file=name, present=True, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    dump(run / 'output_manifest.json', dict(schema_version=1, artifacts=entries))


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / 'scenario_snapshot'
    (root / 'config').mkdir(parents=True)
    units = dict(pumped=[dict(id='P', init_mode='idle', n_units=2)])
    (root / 'config/units.yaml').write_text(yaml.safe_dump(units), encoding='utf8')
    p = spot_costs.empty_profile()
    p['units']['P'] = dict(type='pumped', source='Explicit synthetic assumption', source_kind='test',
                           gen_start_yuan_per_unit_start=7, pump_start_yuan_per_unit_start=11,
                           gen_stop_yuan_per_unit_stop=5, pump_stop_yuan_per_unit_stop=13)
    yaml_raw = yaml.safe_dump(p).encode()
    p.update(available=True, sha256=hashlib.sha256(yaml_raw).hexdigest(), input_file='config/' + spot_costs.FILENAME)
    summary = dict(expected_periods=2, executed_periods=2, dt_h=.5, completed_scope='full_day',
                   operating_profit=dict(profit_basis=spot_costs.BASIS, cost_basis_version=spot_costs.VERSION,
                                         cost_profile_sha256=p['sha256'], cost_profile_available=True,
                                         all_unit_costs_available=True, unit_count=1))
    dump(tmp_path / 'fixed_cost_profile.json', p)
    dump(tmp_path / 'summary.json', summary)
    dump(tmp_path / 'rolling_actual_states.json', dict(windows=[dict(t=0, pumped={'P': dict(ng=0, np=0)})]))
    reindex(tmp_path)
    r = dict(dir=str(tmp_path), full=True, summary=summary)
    r['da_unit_awards'] = pd.DataFrame([dict(unit='P', type='pumped', period=t) for t in range(2)])
    r['rt_units'] = r['da_unit_awards'].copy()
    r['da_pumped'] = pd.DataFrame([dict(unit='P', period=t, n_gen=0, n_pump=0) for t in range(2)])
    r['rt_pumped'] = pd.DataFrame([dict(unit='P', period=t, n_gen=t, n_pump=2 if t == 0 else 0) for t in range(2)])
    r['settlement_detail'] = pd.DataFrame([
        dict(entity='P_' + label, period=t, q_da=0., q_rt=q, lmp_da=20., lmp_rt=20.,
             da_amount=0., rt_amount=q * .5 * 20., total=q * .5 * 20.)
        for t, gen, pump in [(0, 0., -3.), (1, 4., 0.)]
        for label, q in [('发电', gen), ('抽水', pump)]])
    return tmp_path, r, p, yaml_raw


def test_indexed_cli_costs_recompute_quantities_transitions_and_cash(archive):
    run, r, profile, _ = archive
    before = {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()}
    x = summarize_unit_economics(build_unit_economics(r)).set_index('phase')
    assert x.cost_available.all()
    assert x.loc['da', 'profit_yuan'] == 0
    # Two pump starts, then two pump stops plus one generation start.
    assert x.loc['rt', 'startup_cost_yuan'] == 29
    assert x.loc['rt', 'shutdown_cost_yuan'] == 26
    assert x.loc['rt', 'purchase_cost_yuan'] == 30
    assert x.loc['rt', 'gross_generation_cash_yuan'] == 40
    assert x.loc['rt', 'profit_yuan'] == -45
    assert before == {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()}


@pytest.mark.parametrize('field,value', [
    ('version', 'legacy-fixed-costs-v1'), ('profit_basis', spot_costs.LEGACY),
    ('available', False), ('available', 'true'), ('sha256', 'not-a-hash'),
    ('input_file', '../live/config/spot_market_costs.yaml'), ('exclusions', 'includes_om'),
    ('source', ''), ('unexpected_cost', 3),
])
def test_invalid_profile_contract_rejected_even_with_valid_manifest(archive, field, value):
    run, r, p, _ = archive
    p[field] = value
    dump(run / 'fixed_cost_profile.json', p); reindex(run)
    with pytest.raises(ValueError):
        build_unit_economics(r)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1., True, '7'])
def test_bad_cost_rates_rejected(archive, value):
    run, r, p, _ = archive
    p['units']['P']['gen_start_yuan_per_unit_start'] = value
    dump(run / 'fixed_cost_profile.json', p); reindex(run)
    with pytest.raises(ValueError):
        build_unit_economics(r)


@pytest.mark.parametrize('damage', ['subject', 'type', 'om', 'missing_field'])
def test_wrong_subject_type_and_mixed_legacy_costs_rejected(archive, damage):
    run, r, p, _ = archive
    rec = p['units']['P']
    if damage == 'subject': p['units']['P_other'] = p['units'].pop('P')
    elif damage == 'type':
        p['units']['P'] = dict(type='storage', source='test', source_kind='test',
                              cost_scope='no_fuel_or_defined_start_stop_costs_excluding_om')
    elif damage == 'om': rec['generation_vom_yuan_per_mwh'] = 100
    else: rec.pop('gen_stop_yuan_per_unit_stop')
    dump(run / 'fixed_cost_profile.json', p); reindex(run)
    with pytest.raises(ValueError): build_unit_economics(r)


def test_duplicate_json_fields_rejected(archive):
    run, r, _, _ = archive
    p = run / 'fixed_cost_profile.json'
    p.write_text(p.read_text(encoding='utf8').replace('{', '{"available": false,', 1), encoding='utf8')
    reindex(run)
    with pytest.raises(ValueError, match='Duplicate'): build_unit_economics(r)


@pytest.mark.parametrize('damage', ['absent', 'missing_entry', 'duplicate_entry', 'hash', 'bytes', 'present', 'schema'])
def test_manifest_must_bind_actual_archive_bytes(archive, damage):
    run, r, _, _ = archive
    path = run / 'output_manifest.json'
    manifest = json.loads(path.read_text(encoding='utf8'))
    if damage == 'absent': path.unlink()
    else:
        if damage == 'missing_entry': manifest['artifacts'] = manifest['artifacts'][1:]
        elif damage == 'duplicate_entry': manifest['artifacts'].append(manifest['artifacts'][0])
        elif damage == 'hash': manifest['artifacts'][0]['sha256'] = '0' * 64
        elif damage == 'bytes': manifest['artifacts'][0]['bytes'] += 1
        elif damage == 'present': manifest['artifacts'][0]['present'] = False
        else: manifest['schema_version'] = True
        dump(path, manifest)
    with pytest.raises(ValueError): build_unit_economics(r)


def test_same_source_hash_does_not_authenticate_changed_parameters(archive):
    run, r, p, _ = archive
    p['units']['P']['gen_start_yuan_per_unit_start'] = 999
    dump(run / 'fixed_cost_profile.json', p)  # Intentionally retain original manifest.
    with pytest.raises(ValueError, match='manifest'): build_unit_economics(r)


@pytest.mark.parametrize('field,value', [
    ('profit_basis', spot_costs.LEGACY), ('cost_basis_version', 'other'),
    ('cost_profile_sha256', '0' * 64), ('cost_profile_available', False), ('unit_count', 2),
])
def test_summary_cost_provenance_must_agree(archive, field, value):
    run, r, _, _ = archive
    r['summary']['operating_profit'][field] = value
    dump(run / 'summary.json', r['summary']); reindex(run)
    with pytest.raises(ValueError): build_unit_economics(r)


def test_supplied_summary_cannot_be_from_another_run(archive):
    _, r, _, _ = archive
    r['summary']['operating_profit']['cost_profile_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='summary'): build_unit_economics(r)


def test_unindexed_modified_summary_rejected(archive):
    run, r, _, _ = archive
    s = deepcopy(r['summary']); s['operating_profit']['unit_count'] = 20
    dump(run / 'summary.json', s)
    with pytest.raises(ValueError, match='manifest'): build_unit_economics(r)


def test_missing_record_is_unknown_and_never_filled_from_live_costs(archive, monkeypatch):
    run, r, p, yaml_raw = archive
    p['units'] = {}
    r['summary']['operating_profit']['all_unit_costs_available'] = False
    dump(run / 'fixed_cost_profile.json', p); dump(run / 'summary.json', r['summary']); reindex(run)
    (run / 'config').mkdir()
    (run / 'config' / spot_costs.FILENAME).write_bytes(yaml_raw)
    monkeypatch.chdir(run)
    x = build_unit_economics(r)
    assert not x.cost_available.any() and x.profit_yuan.isna().all()
    assert x.net_cash_yuan.notna().all()


def test_missing_record_cannot_claim_complete_costs(archive):
    run, r, p, _ = archive
    p['units'] = {}
    dump(run / 'fixed_cost_profile.json', p); reindex(run)
    with pytest.raises(ValueError, match='missing unit'): build_unit_economics(r)


def test_no_frozen_cost_file_does_not_use_live_config(archive, monkeypatch):
    run, r, _, raw = archive
    (run / 'fixed_cost_profile.json').unlink()
    (run / 'config').mkdir(); (run / 'config' / spot_costs.FILENAME).write_bytes(raw)
    monkeypatch.chdir(run)
    x = build_unit_economics(r)
    assert x.profit_yuan.isna().all()


@pytest.mark.parametrize('damage', ['syntax', 'conflict'])
def test_existing_snapshot_costs_are_not_bypassed(archive, damage):
    run, r, p, _ = archive
    path = run / 'scenario_snapshot/config' / spot_costs.FILENAME
    if damage == 'syntax': path.write_text('units: [', encoding='utf8')
    else:
        p['units']['P']['gen_start_yuan_per_unit_start'] += 1
        path.write_text(yaml.safe_dump(spot_costs.clean_profile(p)), encoding='utf8')
    with pytest.raises((ValueError, yaml.YAMLError)): build_unit_economics(r)


def test_matching_snapshot_and_export_are_accepted(archive):
    run, r, _, raw = archive
    (run / 'scenario_snapshot/config' / spot_costs.FILENAME).write_bytes(raw)
    assert build_unit_economics(r).cost_available.all()


def test_foreign_snapshot_root_rejected(archive, tmp_path):
    run, r, _, _ = archive
    foreign = run / 'other/scenario_snapshot'; (foreign / 'config').mkdir(parents=True)
    (foreign / 'config/units.yaml').write_bytes((run / 'scenario_snapshot/config/units.yaml').read_bytes())
    r['snapshot_root'] = str(foreign)
    with pytest.raises(ValueError, match='different archives'): build_unit_economics(r)


@pytest.mark.parametrize('damage', ['subject', 'type'])
def test_saved_dispatch_catalog_cannot_override_frozen_identity(archive, damage):
    _, r, _, _ = archive
    r['rt_units'][('unit' if damage == 'subject' else 'type')] = 'P_other' if damage == 'subject' else 'storage'
    with pytest.raises(ValueError, match='subjects'): build_unit_economics(r)


@pytest.mark.parametrize('field,value', [('unit', 'P_other'), ('type', 'storage'),
                                       ('profit_basis', spot_costs.LEGACY), ('cost_profile_sha256', '0' * 64)])
def test_saved_profit_report_provenance_checked_not_used_as_money(archive, field, value):
    _, r, p, _ = archive
    row = dict(unit='P', type='pumped', profit_basis=spot_costs.BASIS,
               cost_basis_version=spot_costs.VERSION, cost_profile_sha256=p['sha256'],
               simulated_operating_profit_yuan=9999999)
    row[field] = value
    r['operating_profit_by_unit'] = pd.DataFrame([row])
    with pytest.raises(ValueError): build_unit_economics(r)


def test_old_profit_numbers_never_backfill(archive):
    _, r, p, _ = archive
    r['operating_profit_by_unit'] = pd.DataFrame([dict(unit='P', type='pumped',
        profit_basis=spot_costs.BASIS, cost_basis_version=spot_costs.VERSION,
        cost_profile_sha256=p['sha256'], simulated_operating_profit_yuan=9999999)])
    x = summarize_unit_economics(build_unit_economics(r)).set_index('phase')
    assert x.loc['rt', 'profit_yuan'] == -45


def test_missing_actual_initial_does_not_borrow_da_state(archive):
    run, r, _, _ = archive
    (run / 'rolling_actual_states.json').unlink()
    x = build_unit_economics(r)
    assert not x.loc[x.phase.eq('rt'), 'cost_available'].all()
    assert summarize_unit_economics(x).set_index('phase').loc['rt', 'profit_yuan'] != -45


def test_linked_archive_evidence_rejected_without_os_privileges(archive, monkeypatch):
    run, r, _, _ = archive
    real = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == run / 'fixed_cost_profile.json' or real(p))
    with pytest.raises(ValueError, match='Linked'): build_unit_economics(r)


def test_real_cli_archive_read_only_independent_recomputation():
    selected = os.environ.get('IEEE39_SAVED_RUN_FOR_UI')
    if not selected:
        pytest.skip('Set IEEE39_SAVED_RUN_FOR_UI for the independent full-day CLI archive')
    run = Path(selected)
    assert run.is_dir(), selected
    assert not (run / 'scenario_snapshot/config' / spot_costs.FILENAME).exists()
    before = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in run.rglob('*') if p.is_file()}
    r = dict(dir=str(run), full=True, summary=json.loads((run / 'summary.json').read_text(encoding='utf8')))
    for p in run.glob('*.csv'):
        try: r[p.stem] = pd.read_csv(p)
        except pd.errors.EmptyDataError: pass
    assert r['summary']['executed_periods'] == r['summary']['expected_periods'] == 96
    profile = json.loads((run / 'fixed_cost_profile.json').read_text(encoding='utf8'))
    x = build_unit_economics(r)
    assert len(x) == len(profile['units']) * 96 * 2 and x.cost_available.all()
    totals = summarize_unit_economics(x).set_index(['unit', 'phase'])
    states = json.loads((run / 'rolling_actual_states.json').read_text(encoding='utf8'))['windows'][0]
    dt = r['summary']['dt_h']
    independent_profit = {}
    # Independently calculate every RT unit from saved cash, power and physical
    # transitions, without reading archived profit amounts or period_cost().
    for uid, rec in profile['units'].items():
        kind = rec['type']; suffix = ['_发电', '_抽水'] if kind == 'pumped' else ['_放电', '_充电'] if kind == 'storage' else ['']
        cash = r['settlement_detail'][r['settlement_detail'].entity.isin([uid + s for s in suffix])]
        generated = cash[cash.entity.eq(uid + suffix[0])].q_rt.sum() * dt
        expected_cost = rec.get('fuel_cost_yuan_per_mwh', 0) * generated
        if kind in spot_costs.THERMAL:
            on = r['rt_units'].query('unit == @uid').sort_values('period').on.to_numpy()
            changes = np.diff(np.r_[float(states['thermal'][uid]['online']), on])
            expected_cost += on.sum() * dt * rec['online_cost_yuan_per_h']
            expected_cost += np.maximum(changes, 0).sum() * rec['startup_cost_yuan_per_start']
            expected_cost += np.maximum(-changes, 0).sum() * rec['shutdown_cost_yuan_per_stop']
        elif kind == 'pumped':
            modes = r['rt_pumped'].query('unit == @uid').sort_values('period')
            for col, initial, prefix in [('n_gen', 'ng', 'gen'), ('n_pump', 'np', 'pump')]:
                changes = np.diff(np.r_[states['pumped'][uid][initial], modes[col]])
                expected_cost += np.maximum(changes, 0).sum() * rec[prefix + '_start_yuan_per_unit_start']
                expected_cost += np.maximum(-changes, 0).sum() * rec[prefix + '_stop_yuan_per_unit_stop']
        independent_profit[uid] = cash.total.sum() - expected_cost
        actual = totals.loc[(uid, 'rt')]
        assert actual.operating_cost_yuan == pytest.approx(expected_cost, abs=1e-6)
        assert actual.profit_yuan == pytest.approx(cash.total.sum() - expected_cost, abs=1e-6)
    assert totals.loc[('PS1', 'rt'), 'profit_yuan'] == pytest.approx(independent_profit['PS1'], abs=.0002)
    assert before == {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in run.rglob('*') if p.is_file()}

@pytest.mark.parametrize('report', [False, True])
@pytest.mark.parametrize('manifest', [False, True])
def test_producer_unavailable_profile_keeps_income_and_unknown_costs(archive, report, manifest):
    run, r, p, _ = archive
    p.update(available=False, units={}, sha256=None)
    r['summary']['operating_profit'].update(cost_profile_available=False, all_unit_costs_available=False,
                                           cost_profile_sha256=None)
    dump(run / 'fixed_cost_profile.json', p); dump(run / 'summary.json', r['summary']); reindex(run)
    if not manifest: (run / 'output_manifest.json').unlink()
    if report:
        r['operating_profit_by_unit'] = pd.DataFrame([dict(unit='P', type='pumped',
            profit_basis=spot_costs.BASIS, cost_basis_version=spot_costs.VERSION,
            cost_profile_sha256=np.nan, cost_available=False)])
    x = build_unit_economics(r)
    assert x.net_cash_yuan.notna().all()
    assert x.profit_yuan.isna().all() and not x.cost_available.any()
    assert summarize_unit_economics(x).set_index('phase').loc['rt', 'net_cash_yuan'] == 10


@pytest.mark.parametrize('contradiction', [False, True])
def test_known_legacy_profile_preserves_income_without_using_old_costs(archive, contradiction):
    run, r, _, _ = archive
    p = dict(source='Historical mixed assumptions', available=True, sha256='0' * 64,
             input_file='config/fixed_costs.yaml', units={'P': dict(type='pumped',
                 generation_vom_yuan_per_mwh=999, gen_start_yuan_per_unit_start=999,
                 pump_start_yuan_per_unit_start=999)})
    if not contradiction:
        r['summary']['operating_profit']['profit_basis'] = spot_costs.LEGACY
        r['summary']['operating_profit']['cost_basis_version'] = 'legacy-fixed-costs-v1'
        dump(run / 'summary.json', r['summary'])
    dump(run / 'fixed_cost_profile.json', p); reindex(run)
    if contradiction:
        with pytest.raises(ValueError, match='contradicts'): build_unit_economics(r)
    else:
        x = build_unit_economics(r)
        assert x.net_cash_yuan.notna().all() and x.profit_yuan.isna().all()


def test_snapshot_config_junction_rejected_without_system_changes(archive, monkeypatch):
    run, r, _, _ = archive
    actual = getattr(Path, 'is_junction', lambda p: False)
    monkeypatch.setattr(Path, 'is_junction',
                        lambda p: p == run / 'scenario_snapshot/config' or actual(p), raising=False)
    with pytest.raises(ValueError, match='Linked'): build_unit_economics(r)


@pytest.mark.parametrize('shape', ['minimal', 'producer', 'generated_overlay'])
@pytest.mark.parametrize('contradiction', [False, True])
def test_historical_legacy_export_shapes_preserve_only_verified_cash(archive, shape, contradiction):
    run, r, _, _ = archive
    p = dict(available=False, units={})
    if shape == 'producer':
        p.update(sha256=None, missing_reason='explicit fixed_costs.yaml absent; no bid/worksheet fallback')
    elif shape == 'generated_overlay':
        p = dict(source='Historical generated mixed costs', available=True, sha256='0' * 64,
                 input_files=['config/fixed_costs.yaml', 'config/generated_research_costs.json'],
                 units={'P': dict(type='pumped', generation_vom_yuan_per_mwh=999,
                                 gen_start_yuan_per_unit_start=999, pump_start_yuan_per_unit_start=999)})
    if not contradiction:
        r['summary']['operating_profit'].update(profit_basis=spot_costs.LEGACY,
            cost_basis_version='legacy-fixed-costs-v1', cost_profile_available=shape == 'generated_overlay',
            all_unit_costs_available=shape == 'generated_overlay')
        dump(run / 'summary.json', r['summary'])
    dump(run / 'fixed_cost_profile.json', p)
    (run / 'output_manifest.json').unlink()  # Legacy writers need not have this index.
    if contradiction:
        with pytest.raises(ValueError): build_unit_economics(r)
    else:
        x = build_unit_economics(r)
        assert x.net_cash_yuan.notna().all() and x.profit_yuan.isna().all()
        assert summarize_unit_economics(x).set_index('phase').loc['rt', 'net_cash_yuan'] == 10


def test_minimal_unavailable_legacy_cannot_hide_nonempty_costs(archive):
    run, r, _, _ = archive
    r['summary']['operating_profit'].update(profit_basis=spot_costs.LEGACY, cost_profile_available=False,
                                           all_unit_costs_available=False)
    dump(run / 'summary.json', r['summary'])
    dump(run / 'fixed_cost_profile.json', dict(available=False, units={'P': {'type': 'pumped'}}))
    with pytest.raises(ValueError): build_unit_economics(r)
