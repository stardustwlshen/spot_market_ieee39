"""Participation is persisted input, never the UI's current display selection.

These regressions build real DA cases and SCUC variables without running a
solver. All edits and transaction backups live in disposable scenario copies.
"""
import copy
import json
import shutil

import numpy as np
import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn
import pytest
import yaml

from smm.bids import load_bids
from smm.case import build_da_case
from smm.config import ROOT, load_config
from smm.forecast import apply_profile_overrides, generate_profiles
from smm.network import build_network
from smm.scuc import build_model
from smm.unit_editor import (
    TYPES, apply_unit_edit, catalog, prepare_unit_edit, scene_fingerprint,
    unit_template,
)


MODEL_SETS = {
    'thermal': 'G', 'renewable': 'W', 'hydro': 'HS',
    'storage': 'S', 'pumped': 'PS',
}
POWER_VARIABLES = {
    'thermal': ('P',), 'renewable': ('Pw',), 'hydro': ('Ph',),
    'storage': ('Pc', 'Pd'), 'pumped': ('Pg', 'Pp'),
}


@pytest.fixture
def scene(tmp_path):
    root = tmp_path / 'participation-scene'
    for directory in ('config', 'data', 'bids'):
        shutil.copytree(ROOT / directory, root / directory)
    return root


def inputs(root):
    """Persisted scenario content; writer locks are not saved inputs."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')
    }


def case_and_model(root):
    cfg = load_config(root=root)
    quote = root / 'current_quote.json'
    bids = (json.loads(quote.read_bytes())['draft'] if quote.exists()
            else load_bids(cfg))
    profiles = apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base['seed']))
    case = build_da_case(cfg, build_network(cfg), profiles, bids,
                         require_complete_bids=True)
    return case, build_model(case)


def resource_sets(case, model):
    """Check executable sets and dispatch variables, not only loaded YAML."""
    result = {}
    for family, name in MODEL_SETS.items():
        ids = {u['id'] for u in case.cfg.units[family]}
        assert set(getattr(model, name)) == ids
        for variable in POWER_VARIABLES[family]:
            indices = set(getattr(model, variable))
            assert indices == {(uid, t) for uid in ids for t in model.T}
        result[family] = ids
    assert set(model.J) == result['hydro']
    assert set(case.ren_fc) == result['renewable']
    result['tie'] = {u['id'] for u in case.cfg.units['tie']}
    return result


@pytest.mark.parametrize('kind', TYPES)
def test_saved_participation_round_trip_reaches_case_and_model(scene, kind):
    baseline_case, baseline_model = case_and_model(scene)
    baseline = resource_sets(baseline_case, baseline_model)
    family = TYPES[kind][0]
    uid = 'PARTICIPATION_' + kind
    selected = unit_template(kind, uid, 31, 100.)
    selected.pop('enabled')  # Old scenarios omit the flag: they must stay active.
    untouched = dict((u['id'], (group, u)) for group, u in catalog(scene))
    before = inputs(scene)

    add = prepare_unit_edit(scene, family, selected)
    assert inputs(scene) == before
    assert not (scene / 'unit_edit_versions').exists()
    apply_unit_edit(add)
    expected = copy.deepcopy(baseline)
    expected[family].add(uid)
    active_case, active_model = case_and_model(scene)
    assert resource_sets(active_case, active_model) == expected
    assert uid not in active_case.cfg.units['_disabled']
    active_quote = json.loads((scene / 'current_quote.json').read_bytes())
    assert {k: v for k, v in active_quote['draft']['units'].items() if k != uid} == baseline_case.bids['units']

    selected['enabled'] = False
    saved = inputs(scene)
    snapshot = scene_fingerprint(scene)
    pending = prepare_unit_edit(scene, family, selected, original_id=uid,
                                expected=snapshot)
    assert inputs(scene) == saved
    assert scene_fingerprint(scene) == snapshot
    # Cancel means discard the detached preview. Reopening/checking does not save.
    del pending
    dry_case, dry_model = case_and_model(scene)
    assert resource_sets(dry_case, dry_model) == expected
    assert inputs(scene) == saved

    apply_unit_edit(prepare_unit_edit(scene, family, selected, original_id=uid,
                                     expected=snapshot))
    disabled_case, disabled_model = case_and_model(scene)
    assert resource_sets(disabled_case, disabled_model) == baseline
    assert uid in disabled_case.cfg.units['_disabled']
    assert uid not in disabled_case.bids['units']
    disabled_quote = json.loads((scene / 'current_quote.json').read_bytes())
    assert disabled_quote['draft']['units'] == baseline_case.bids['units']
    assert any(uid in entry['units'] for entry in disabled_quote['history'].values())
    assert next(u for g, u in catalog(scene) if u['id'] == uid)['enabled'] is False

    selected['enabled'] = True
    saved = inputs(scene)
    restore = prepare_unit_edit(scene, family, selected, original_id=uid)
    assert inputs(scene) == saved
    apply_unit_edit(restore)
    restored_case, restored_model = case_and_model(scene)
    assert resource_sets(restored_case, restored_model) == expected
    assert uid not in restored_case.cfg.units['_disabled']
    assert uid in restored_case.bids['units']
    assert next(u for g, u in catalog(scene) if u['id'] == uid)['enabled'] is True
    assert {u['id']: (group, u) for group, u in catalog(scene) if u['id'] != uid} == untouched
    assert {k: v for k, v in restored_case.bids['units'].items() if k != uid} == baseline_case.bids['units']


def net_demand(model):
    """Demand minus fixed supply in the actual Pyomo nodal constraints."""
    return {
        bus: np.array([
            pyo.value(model.c_inj[bus, t].lower)
            - generate_standard_repn(model.c_inj[bus, t].body).constant
            for t in model.T
        ]) for bus in model.N
    }


@pytest.mark.parametrize('tie_mode', ['supply', 'fixed_load'])
def test_tie_enabled_configuration_changes_case_and_model_balance(scene, tie_mode):
    # Tie is a fixed boundary input, deliberately not a unit-editor resource.
    path = scene / 'config/base.yaml'
    base = yaml.safe_load(path.read_bytes())
    base.update(tie_line_mode=tie_mode, tie_load_allocation='base_load_share')
    path.write_text(yaml.safe_dump(base), encoding='utf-8')
    path = scene / 'config/units.yaml'
    raw = yaml.safe_load(path.read_bytes())
    target = raw['tie'][0]
    target.pop('enabled', None)
    uid, bus = target['id'], target['bus']
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    before = inputs(scene)
    active_case, active_model = case_and_model(scene)
    active_sets = resource_sets(active_case, active_model)
    assert uid in active_sets['tie']
    assert inputs(scene) == before

    target['enabled'] = False
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    disabled_case, disabled_model = case_and_model(scene)
    expected = copy.deepcopy(active_sets)
    expected['tie'].remove(uid)
    assert resource_sets(disabled_case, disabled_model) == expected
    assert uid in disabled_case.cfg.units['_disabled']
    assert uid not in disabled_case.tie
    assert not any(k.startswith(uid + '@') for k in disabled_case.fixed_load)
    if tie_mode == 'supply':
        schedule = active_case.tie[uid][1]
    else:
        schedule = sum(values for key, (_, values) in active_case.fixed_load.items()
                       if key.startswith(uid + '@'))
    assert np.max(schedule) > 0
    active_demand, disabled_demand = net_demand(active_model), net_demand(disabled_model)
    for node in active_model.N:
        delta = (-schedule if node == bus else np.zeros_like(schedule)) if tie_mode == 'supply' else schedule * active_case.net.load_share[node]
        np.testing.assert_allclose(active_demand[node] - disabled_demand[node], delta,
                                   atol=1e-9, rtol=0.)

    target['enabled'] = True
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    restored_case, restored_model = case_and_model(scene)
    assert resource_sets(restored_case, restored_model) == active_sets
    for node, values in net_demand(restored_model).items():
        np.testing.assert_allclose(values, active_demand[node], atol=1e-9, rtol=0.)
