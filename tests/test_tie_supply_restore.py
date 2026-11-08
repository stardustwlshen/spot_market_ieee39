"""Restoration is explicit, local to active inputs, and preserves historical data."""
from pathlib import Path
import json
import shutil
import sys

import pandas as pd
import pytest
import yaml

from smm import scenario
from smm.config import ROOT, load_config
from smm.bids import default_bids, save_bids
from smm.forecast import generate_profiles, apply_profile_overrides
from smm.fixed_load import tie_mode
from smm.tie_restore import prepare_supply_restore, apply_supply_restore
from smm.typical_scenarios import prepare_typical, create_typical, MANIFEST


@pytest.fixture
def scenes(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path/'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path/'runs')
    root = scenario.create_scenario('legacy_fixed')
    path = root/'config/base.yaml'
    base = yaml.safe_load(path.read_text()); base.update(tie_line_mode='fixed_load', tie_load_allocation='base_load_share')
    path.write_text(yaml.safe_dump(base))
    cfg = load_config(root=root); profiles = generate_profiles(cfg)
    columns = ['period','time']+[f'{name}_{stage}' for name in ('load','W1','TIE') for stage in ('actual','da','id','rt')]
    profiles[columns].to_csv(root/'data/profile_overrides.csv', index=False)
    bids = default_bids(cfg); save_bids(cfg, bids)
    (root/'current_quote.json').write_text(json.dumps(dict(schema=1,draft=bids)))
    frozen = scenario.RUNS_DIR/'legacy_fixed'/'frozen'; frozen.parent.mkdir(parents=True)
    shutil.copytree(root, frozen)
    return root, frozen


def hashes(root):
    import hashlib
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_repository_default_and_existing_implicit_supply():
    cfg=load_config(); assert tie_mode(cfg)=='supply'
    cfg.base.pop('tie_line_mode'); assert tie_mode(cfg)=='supply'


def test_preview_is_read_only_apply_preserves_quotes_and_snapshots(scenes):
    root, frozen = scenes; before=hashes(root); historic=hashes(frozen)
    cfg=load_config(root=root); source=apply_profile_overrides(cfg,generate_profiles(cfg))
    plan=prepare_supply_restore(root.name)
    assert hashes(root)==before and len(plan['removed_columns'])==4
    result=apply_supply_restore(plan)
    assert result['changed'] and Path(result['backup']).is_dir()
    assert hashes(frozen)==historic
    after=load_config(root=root); assert tie_mode(after)=='supply'
    profiles=apply_profile_overrides(after,generate_profiles(after))
    for col in ('load_actual','load_da','W1_actual','W1_da','TIE_sched'):
        pd.testing.assert_series_equal(profiles[col],source[col])
    for path in ('current_quote.json','config/units.yaml','config/modes.yaml','data/case39/branches.csv'):
        assert hashes(root)[path]==before[path]
    backup=Path(result['backup'])
    assert (backup/'config/base.yaml').read_bytes()==(frozen/'config/base.yaml').read_bytes()
    assert not prepare_supply_restore(root.name)['changed']
    second=apply_supply_restore(prepare_supply_restore(root.name)); assert not second['changed'] and second['backup'] is None


def test_stale_preview_refuses_change_and_typical_baseline_archived(scenes):
    root,_=scenes
    composed=create_typical('old_composed',prepare_typical(root,load_multiplier=1.1))
    manifest=(composed/MANIFEST).read_bytes()
    plan=prepare_supply_restore(composed.name)
    quote=composed/'current_quote.json';quote.write_text(quote.read_text()+'\n')
    with pytest.raises(RuntimeError,match='已变化'):apply_supply_restore(plan)
    assert tie_mode(load_config(root=composed))=='fixed_load'
    result=apply_supply_restore(prepare_supply_restore(composed.name))
    assert not (composed/MANIFEST).exists()
    assert (Path(result['backup'])/MANIFEST).read_bytes()==manifest
    preview=prepare_typical(composed)
    assert not preview['inherited'] and preview['load_multiplier']==1.


def test_current_scene_ui_offers_restore_instead_of_conversion(scenes):
    sys.path.insert(0,str(ROOT/'app'))
    from streamlit.testing.v1 import AppTest
    def app():
        import streamlit as st
        from smm.config import load_config
        from fixed_load_controls import render_fixed_load_policy
        cfg=load_config(root=st.session_state['root'])
        render_fixed_load_policy(cfg,cfg.root.name)
    at=AppTest.from_function(app);at.session_state['root']=str(scenes[0]);at.run()
    assert not at.exception
    assert any('恢复本场景区外来电' in b.label for b in at.button)
    assert not any('改为按基准' in b.label for b in at.button)
    at.button(key='restore_tie_supply:legacy_fixed').click().run()
    assert not at.exception
    assert tie_mode(load_config(root=scenes[0]))=='supply'


def test_replace_failure_rolls_back_active_inputs_and_retains_backup(scenes,monkeypatch):
    import smm.tie_restore as module
    root,_=scenes;before=hashes(root);plan=prepare_supply_restore(root.name)
    original=module.os.replace
    calls=[]
    def fail_once(source,destination):
        calls.append(str(destination))
        if len(calls)==2:raise OSError('simulated write failure')
        return original(source,destination)
    monkeypatch.setattr(module.os,'replace',fail_once)
    with pytest.raises(OSError,match='simulated'):
        apply_supply_restore(plan)
    assert {p:h for p,h in hashes(root).items() if not p.startswith('input_backups/')}==before
    assert list((root/'input_backups').glob('*/restore_record.json'))
