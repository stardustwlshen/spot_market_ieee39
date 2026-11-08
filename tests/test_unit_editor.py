"""Explicit unit edits preserve scenario isolation, real costs and old snapshots."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import threading

import pytest
import yaml

from smm.config import ROOT, load_config
from smm.unit_editor import TYPES, unit_template, prepare_unit_edit, apply_unit_edit, scene_fingerprint, catalog


@pytest.fixture
def scene(tmp_path):
    root = tmp_path / 'scene'
    for directory in ('config', 'data', 'bids'):
        shutil.copytree(ROOT / directory, root / directory)
    return root


def input_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for folder in ('config', 'data', 'bids')
            for p in (root / folder).rglob('*') if p.is_file()} | (
            {'current_quote.json': (root / 'current_quote.json').read_bytes()} if (root / 'current_quote.json').exists() else {})


@pytest.mark.parametrize('kind', TYPES)
def test_all_types_add_with_cost_bid_and_no_unrelated_mutation(scene, kind):
    from smm.profit import load_fixed_cost_profile
    from smm.bids import load_bids, validate_bids
    from smm.bid_cost_basis import research_cost_worksheet
    old = load_bids(load_config(root=scene))
    before = input_bytes(scene)
    unit = unit_template(kind, 'NEW_'+kind, 31, 100.)
    plan = prepare_unit_edit(scene, TYPES[kind][0], unit)
    assert input_bytes(scene) == before  # Check/cancel does not save anything.
    apply_unit_edit(plan)
    cfg = load_config(root=scene)
    saved = json.loads((scene / 'current_quote.json').read_bytes())
    assert {k: v for k, v in saved['draft']['units'].items() if k != unit['id']} == old['units']
    assert not [i for i in validate_bids(cfg, saved['draft'], require_complete=True) if i.level == 'error']
    assert unit['id'] in load_fixed_cost_profile(cfg)['units']
    assert unit['id'] in research_cost_worksheet(cfg)['renewable_vom' if kind in ('wind','pv') else TYPES[kind][0]]
    assert (scene / 'config/fixed_costs.yaml').read_bytes() == before['config/fixed_costs.yaml']
    assert (scene / 'config/bid_cost_basis.yaml').read_bytes() == before['config/bid_cost_basis.yaml']
    assert all((scene / key).read_bytes() == content for key, content in before.items() if key.startswith('bids/'))


@pytest.mark.parametrize('kind,update,match', [
    ('coal', {'pmin': 120.}, 'pmin'), ('gas_ccgt', {'ramp': 0.}, 'ramp'),
    ('gas_gt', {'init': {'online': False, 'p0': 10., 'hours': 2}}, 'p0'),
    ('storage', {'ecap': 0}, 'ecap'), ('storage', {'eta_d': 1.1}, '效率'),
    ('storage', {'soc0': 1.1}, 'SOC'), ('pumped', {'vup0': 99999.}, 'vup0'),
    ('pumped', {'e_gen': 15.}, '效率'), ('pumped', {'n_units': 1.5}, '整数'),
    ('pumped', {'max_gen_starts': -1}, '整数'), ('hydro', {'ramp': 0.}, 'ramp'),
    ('hydro', {'zones': [[90, 110]]}, 'zones'), ('hydro', {'ut': -1}, '非负'),
    ('hydro', {'init_units_on': 0, 'init_p_unit': 5}, '0 MW'),
    ('wind', {'profile': 'unknown'}, '预测曲线'), ('pv', {'cap': float('nan')}, '有限'),
    ('coal', {'bus': 999}, '母线'), ('coal', {'id': 'ES1'}, '已存在'),
    ('coal', {'id': 'H2'}, '已存在'),
])
def test_invalid_parameters_fail_before_saving(scene, kind, update, match):
    before = input_bytes(scene)
    unit = unit_template(kind, 'INVALID', 31)
    unit.update(update)
    with pytest.raises((ValueError, TypeError), match=match):
        prepare_unit_edit(scene, TYPES[kind][0], unit)
    assert input_bytes(scene) == before


def test_hydrology_and_dependency_checks(scene):
    unit = unit_template('hydro', 'H_NEW', 31)
    unit['seasons']['dry']['qeco'] = 100
    unit['qout_max'] = 10
    with pytest.raises(ValueError, match='qeco'):
        prepare_unit_edit(scene, 'hydro', unit)
    unit['qout_max'] = None
    unit['upstream'] = 'H_NEW'
    with pytest.raises(ValueError, match='自身'):
        prepare_unit_edit(scene, 'hydro', unit)


def test_edit_keeps_real_costs_and_untouched_quotes_then_explicit_reset(scene):
    from smm.profit import load_fixed_cost_profile
    from smm.bids import load_bids
    fixed = scene / 'config/fixed_costs.yaml'
    data = yaml.safe_load(fixed.read_bytes())
    data.update(source_kind='observed', source='user provided verified costs')
    data['units']['C1']['fixed_online_cost_yuan_per_h'] = 1234.
    fixed.write_text(yaml.safe_dump(data))
    before_cost = fixed.read_bytes()
    before_bid = load_bids(load_config(root=scene))
    unit = next(u for g,u in catalog(scene) if u['id'] == 'C1')
    unit['pmax'] = 700.
    before = input_bytes(scene)
    with pytest.raises(ValueError, match='当前报价'):
        prepare_unit_edit(scene, 'thermal', unit, original_id='C1')
    assert input_bytes(scene) == before
    apply_unit_edit(prepare_unit_edit(scene, 'thermal', unit, original_id='C1', reset_bid=True))
    result = json.loads((scene / 'current_quote.json').read_bytes())
    assert result['history']
    assert fixed.read_bytes() == before_cost
    assert load_fixed_cost_profile(load_config(root=scene))['units']['C1']['fixed_online_cost_yuan_per_h'] == 1234
    assert result['draft']['units']['C1']['segments'][-1]['p_end'] == 700
    assert result['draft']['units']['C2'] == before_bid['units']['C2']


def test_update_only_selected_generated_capacity_and_preserve_custom_basis(scene):
    from smm.research_cost_setup import prepare_generated_cost_setup, apply_generated_cost_setup, read_generated_costs
    from smm.profit import load_fixed_cost_profile
    cfg = load_config(root=scene)
    apply_generated_cost_setup(prepare_generated_cost_setup(cfg))
    before = read_generated_costs(scene)
    unit = next(u for g,u in catalog(scene) if u['id'] == 'C1')
    unit['pmax'] = 700.
    apply_unit_edit(prepare_unit_edit(scene, 'thermal', unit, original_id='C1', reset_bid=True))
    after = read_generated_costs(scene)
    assert after['evidence']['C1']['capacity_mw'] == 700
    assert after['profile']['units']['C1']['fixed_online_cost_yuan_per_h'] == pytest.approx(6000*700/646)
    assert after['profile']['units']['C2'] == before['profile']['units']['C2']
    assert after['evidence']['C2'] == before['evidence']['C2']
    # A changed origin invalidates old overlays and cannot be revived by editing another unit.
    path = scene / 'config/fixed_costs.yaml'
    real = yaml.safe_load(path.read_bytes())
    real.update(source_kind='observed', source='verified replacement')
    path.write_text(yaml.safe_dump(real))
    add = unit_template('storage', 'NEW_STORE', 31)
    apply_unit_edit(prepare_unit_edit(scene, 'storage', add))
    assert load_fixed_cost_profile(load_config(root=scene))['units']['C2'] == real['units']['C2']


def test_concurrent_changes_reject_and_preserve_other_scene_and_snapshots(scene, tmp_path):
    from smm.live_runner import freeze_inputs
    other = tmp_path / 'other'
    shutil.copytree(scene, other)
    frozen = freeze_inputs(scene, tmp_path / 'run')
    frozen_before = input_bytes(frozen)
    other_before = input_bytes(other)
    first = prepare_unit_edit(scene, 'storage', unit_template('storage', 'NEW_ONE', 31))
    second = prepare_unit_edit(scene, 'storage', unit_template('storage', 'NEW_TWO', 31))
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(apply_unit_edit, plan) for plan in (first, second)]
        results = []
        for f in futures:
            try: f.result(timeout=30); results.append('ok')
            except RuntimeError: results.append('conflict')
    assert sorted(results) == ['conflict', 'ok']
    assert input_bytes(other) == other_before and input_bytes(frozen) == frozen_before


def test_quote_change_after_form_open_is_not_overwritten(scene):
    plan = prepare_unit_edit(scene, 'storage', unit_template('storage', 'NEW_ONE', 31))
    (scene / 'current_quote.json').write_text('{"changed": true}')
    before = input_bytes(scene)
    with pytest.raises(RuntimeError, match='另一页面'):
        apply_unit_edit(plan)
    assert input_bytes(scene) == before


def test_transaction_rolls_back_replace_failure_and_recovers_crash(scene, monkeypatch):
    import base64
    from smm import unit_edit_transaction as tx
    from smm.bids import default_bids
    plan = prepare_unit_edit(scene, 'storage', unit_template('storage', 'NEW_ONE', 31))
    before = input_bytes(scene)
    real = tx.atomic_bytes
    failed = False
    def once(path, content):
        nonlocal failed
        if Path(path).name == 'current_quote.json' and not failed:
            failed = True
            raise OSError('injected disk failure')
        return real(path, content)
    monkeypatch.setattr(tx, 'atomic_bytes', once)
    with pytest.raises(OSError, match='disk'):
        apply_unit_edit(plan)
    assert input_bytes(scene) == before
    assert not (scene / tx.JOURNAL).exists()
    monkeypatch.setattr(tx, 'atomic_bytes', real)
    relative = 'config/units.yaml'
    old = (scene / relative).read_bytes()
    new = plan['replacements'][relative]
    (scene / tx.JOURNAL).write_text(json.dumps({relative: {'old': base64.b64encode(old).decode(), 'new': base64.b64encode(new).decode()}}))
    (scene / relative).write_bytes(new)
    with pytest.raises(ValueError, match='未完成'):
        load_config(root=scene)
    tx.recover_scene_unit_edit(scene)
    assert 'NEW_ONE' not in {u['id'] for u in load_config(root=scene).units['storage']}
    assert input_bytes(scene) == before


def test_freeze_waits_for_coherent_transaction(scene, tmp_path, monkeypatch):
    from smm import unit_edit_transaction as tx
    from smm.live_runner import freeze_inputs
    plan = prepare_unit_edit(scene, 'storage', unit_template('storage', 'NEW_ONE', 31))
    wrote_units, finish = threading.Event(), threading.Event()
    real = tx.atomic_bytes
    def slow(path, content):
        real(path, content)
        if Path(path) == scene / 'config/units.yaml':
            wrote_units.set()
            assert finish.wait(10)
    monkeypatch.setattr(tx, 'atomic_bytes', slow)
    with ThreadPoolExecutor(2) as pool:
        saved = pool.submit(apply_unit_edit, plan)
        assert wrote_units.wait(10)
        frozen = pool.submit(freeze_inputs, scene, tmp_path / 'run')
        assert not frozen.done()
        finish.set()
        saved.result(timeout=30)
        snap = frozen.result(timeout=30)
    cfg = load_config(root=snap)
    assert 'NEW_ONE' in {u['id'] for u in cfg.units['storage']}
    from smm.profit import load_fixed_cost_profile
    assert 'NEW_ONE' in load_fixed_cost_profile(cfg)['units']


def test_saved_all_types_complete_small_market_pipeline(scene):
    """Eight-period two-node integration, not an IEEE39 full-day certificate."""
    import numpy as np
    import pandas as pd
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm.case import build_da_case
    from smm.full import run_full
    from smm.profit import load_fixed_cost_profile
    # Fresh small disk-backed scene. Every resource enters through the editor API.
    shutil.rmtree(scene / 'bids')
    (scene / 'bids').mkdir()
    raw = {key: [] for key in ('thermal', 'hydro', 'storage', 'pumped', 'renewable', 'tie')}
    (scene / 'config/units.yaml').write_text(yaml.safe_dump(raw))
    base = yaml.safe_load((scene / 'config/base.yaml').read_bytes())
    base.update(periods=8, load_scale=.025, line_scale={}, line_capacity_multiplier=1.)
    base['solver'].update(time_limit_s=20, mip_rel_gap=0.)  # Keep suite-wide HiGHS thread pool size.
    (scene / 'config/base.yaml').write_text(yaml.safe_dump(base))
    modes = yaml.safe_load((scene / 'config/modes.yaml').read_bytes())
    modes['MKT']['reserve'].update(up_requirement_mode='formula',include_ctg=False, rho_L=0., rho_RE=0., rho_L_dn=0., rho_RE_dn=0.)
    modes['MKT']['intraday']['enabled'] = False
    (scene / 'config/modes.yaml').write_text(yaml.safe_dump(modes))
    (scene / 'data/case39/buses.csv').write_text('bus,load_mw\n31,50\n32,50\n')
    (scene / 'data/case39/branches.csv').write_text('branch_id,from_bus,to_bus,x_pu,rate_mw\nTEST,31,32,0.1,1000\n')
    for index, kind in enumerate(TYPES):
        capacity = 100. if kind in ('coal','gas_ccgt','gas_gt') else 30.
        unit = unit_template(kind, 'TEST_'+kind, 31 if index % 2 else 32, capacity, energy=60.)
        apply_unit_edit(prepare_unit_edit(scene, TYPES[kind][0], unit))
    cfg = load_config(root=scene)
    bids = json.loads((scene / 'current_quote.json').read_bytes())['draft']
    result = run_full(cfg, build_da_case(cfg, build_network(cfg), generate_profiles(cfg), bids), log=lambda *a: None)
    assert len(result['rr'].rt) == cfg.T == 8
    assert result['settled'] is False
    assert result['settlement_calculated'] is True
    assert result['settlement_scope'] == 'illustrative_prefix'
    assert set(result['rt']['units']['unit']) >= {'TEST_'+kind for kind in TYPES}
    assert result['settle']['reconciliation']['residual'].abs().max() < 1e-6
    assert np.isfinite(result['rt']['units']['P_mw']).all()
    assert set(load_fixed_cost_profile(cfg)['units']) >= {'TEST_'+kind for kind in TYPES}


@pytest.mark.parametrize('kind', TYPES)
def test_edit_each_type_keeps_current_quote_and_id(scene, kind):
    unit = unit_template(kind, 'EDIT_'+kind, 31, 100.)
    apply_unit_edit(prepare_unit_edit(scene, TYPES[kind][0], unit))
    before = json.loads((scene / 'current_quote.json').read_bytes())['draft']['units']
    unit['bus'] = 32
    apply_unit_edit(prepare_unit_edit(scene, TYPES[kind][0], unit, original_id=unit['id']))
    assert next(u for g,u in catalog(scene) if u['id'] == unit['id'])['bus'] == 32
    after = json.loads((scene / 'current_quote.json').read_bytes())['draft']['units']
    assert after == before


def test_disabling_and_reenabling_keeps_prior_quote_in_history(scene):
    unit = next(u for g,u in catalog(scene) if u['id'] == 'ES1')
    unit['enabled'] = False
    apply_unit_edit(prepare_unit_edit(scene, 'storage', unit, original_id='ES1'))
    record = json.loads((scene / 'current_quote.json').read_bytes())
    assert 'ES1' not in record['draft']['units']
    assert any('ES1' in old['units'] for old in record['history'].values())
    unit['enabled'] = True
    apply_unit_edit(prepare_unit_edit(scene, 'storage', unit, original_id='ES1'))
    assert 'ES1' in json.loads((scene / 'current_quote.json').read_bytes())['draft']['units']


def test_no_id_or_type_change_during_edit(scene):
    before = input_bytes(scene)
    unit = next(u for g,u in catalog(scene) if u['id'] == 'C1')
    unit['id'] = 'OTHER'
    with pytest.raises(ValueError, match='不能改编号'):
        prepare_unit_edit(scene, 'thermal', unit, original_id='C1')
    unit.update(id='C1', type='gas_gt')
    with pytest.raises(ValueError, match='不能改类型'):
        prepare_unit_edit(scene, 'thermal', unit, original_id='C1')
    assert input_bytes(scene) == before


def test_no_symlink_write_during_unit_edit(scene, tmp_path):
    original = input_bytes(scene)
    external = tmp_path / 'external-cost.yaml'
    external.write_bytes((scene / 'config/fixed_costs.yaml').read_bytes())
    (scene / 'config/fixed_costs.yaml').unlink()
    try:
        (scene / 'config/fixed_costs.yaml').symlink_to(external)
    except OSError as exc:
        if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
            assert external.read_bytes() == original['config/fixed_costs.yaml']
            pytest.skip('Windows user lacks symbolic-link privilege (WinError 1314)')
        raise
    with pytest.raises(ValueError, match='符号链接'):
        prepare_unit_edit(scene, 'storage', unit_template('storage','NEW',31))
    assert external.read_bytes() == original['config/fixed_costs.yaml']


def test_recovery_refuses_to_overwrite_external_current_quote(scene):
    import base64
    from smm.unit_edit_transaction import JOURNAL, recover_scene_unit_edit
    units = scene / 'config/units.yaml'
    old_units = units.read_bytes()
    new_units = old_units + b'\n# incomplete\n'
    quote = scene / 'current_quote.json'
    old_quote, new_quote, external = b'{"v":1}', b'{"v":2}', b'{"external":3}'
    (scene / JOURNAL).write_text(json.dumps({
        'config/units.yaml': {'old': base64.b64encode(old_units).decode(), 'new': base64.b64encode(new_units).decode()},
        'current_quote.json': {'old': base64.b64encode(old_quote).decode(), 'new': base64.b64encode(new_quote).decode()}}))
    units.write_bytes(new_units)
    quote.write_bytes(external)
    before = input_bytes(scene)
    with pytest.raises(ValueError, match='外部修改冲突'):
        recover_scene_unit_edit(scene)
    assert input_bytes(scene) == before
    assert (scene / JOURNAL).exists()


def test_node_only_edit_does_not_migrate_existing_research_cost(scene):
    from smm.profit import load_fixed_cost_profile
    before = load_fixed_cost_profile(load_config(root=scene))['units']['C2']
    unit = next(u for g,u in catalog(scene) if u['id'] == 'C2')
    unit['bus'] = 31
    apply_unit_edit(prepare_unit_edit(scene, 'thermal', unit, original_id='C2'))
    assert load_fixed_cost_profile(load_config(root=scene))['units']['C2'] == before


def test_pending_journal_never_recovers_during_preview_or_save(scene):
    import base64
    from smm.unit_edit_transaction import JOURNAL, recover_scene_unit_edit
    unit = unit_template('storage', 'PENDING_NEW', 31)
    plan = prepare_unit_edit(scene, 'storage', unit)
    original = input_bytes(scene)
    path = scene / 'config/units.yaml'
    old = path.read_bytes()
    new = plan['replacements']['config/units.yaml']
    journal = scene / JOURNAL
    journal.write_text(json.dumps({'config/units.yaml': {
        'old': base64.b64encode(old).decode(), 'new': base64.b64encode(new).decode()}}))
    path.write_bytes(new)
    interrupted = input_bytes(scene)
    journal_bytes = journal.read_bytes()
    for operation in (
        lambda: prepare_unit_edit(scene, 'storage', unit),
        lambda: apply_unit_edit(plan),
    ):
        with pytest.raises(ValueError, match='未完成'):
            operation()
        assert input_bytes(scene) == interrupted
        assert journal.read_bytes() == journal_bytes
    recover_scene_unit_edit(scene)
    assert input_bytes(scene) == original
    assert not journal.exists()
    # A new explicit preview/save can proceed after the deliberate recovery.
    apply_unit_edit(prepare_unit_edit(scene, 'storage', unit))
    assert any(u['id'] == 'PENDING_NEW' for u in load_config(root=scene).units['storage'])
