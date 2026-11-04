"""Explicit, backed-up restoration of one active scene to external supply.

Never accepts a run/snapshot path. Ordinary reads remain backward compatible.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from . import scenario
from .config import load_config
from .fixed_load import tie_mode
from .typical_scenarios import fingerprint, MANIFEST


def prepare_supply_restore(name):
    root = scenario.scenario_path(name)
    cfg = load_config(root=root)
    before = fingerprint(root)
    if tie_mode(cfg) == 'supply':
        return dict(name=name, changed=False, fingerprint=before, removed_columns=[], changes={})
    base = scenario._yaml().load((root/'config/base.yaml').read_text(encoding='utf-8'))
    base['tie_line_mode'] = 'supply'
    base.pop('tie_load_allocation', None)
    stream = io.StringIO(); scenario._yaml().dump(base, stream)
    changes = {'config/base.yaml': stream.getvalue().encode()}
    removed = []
    override = root/'data/profile_overrides.csv'
    if override.exists():
        frame = pd.read_csv(override)
        removed = [f"{u['id']}_{stage}" for u in cfg.units.get('tie', [])
                   for stage in ('actual', 'da', 'id', 'rt') if f"{u['id']}_{stage}" in frame]
        if removed:
            changes['data/profile_overrides.csv'] = frame.drop(columns=removed).to_csv(index=False).encode()
    if (root/MANIFEST).exists():
        # The old baseline describes distributed demand, so it cannot be used
        # silently for future supply-mode compositions. Retain it in the backup.
        changes[MANIFEST] = None
    meta_path = root/'scenario.json'
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        meta.pop('typical_scenario', None)
        meta['tie_supply_restored'] = True
        changes['scenario.json'] = json.dumps(meta, ensure_ascii=False, indent=2).encode()
    # Validate the exact future input in an off-catalog copy before any write.
    with tempfile.TemporaryDirectory(prefix='tie-supply-check-') as temporary:
        work = Path(temporary)/'scene'
        scenario._copy_tree(root, work)
        for relative, content in changes.items():
            target = work/relative
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
        from .forecast import generate_profiles, apply_profile_overrides
        from .case import build_da_case
        from .network import build_network
        from .typical_scenarios import _current_bids
        target_cfg = load_config(root=work)
        profiles = apply_profile_overrides(target_cfg, generate_profiles(target_cfg))
        case = build_da_case(target_cfg, build_network(target_cfg), profiles, _current_bids(cfg, None))
        assert case.tie and not case.fixed_load if cfg.units.get('tie') else not case.fixed_load
    if fingerprint(root) != before:
        raise RuntimeError('场景输入已变化，请重新预览恢复')
    return dict(name=name, changed=True, fingerprint=before, removed_columns=removed, changes=changes,
                resets_typical_baseline=MANIFEST in changes)


def apply_supply_restore(plan):
    root = scenario.scenario_path(plan['name'])
    if fingerprint(root) != plan['fingerprint']:
        raise RuntimeError('场景输入已变化，请重新预览恢复')
    if not plan['changed']:
        return dict(changed=False, backup=None)
    # Recompute from current files; callers cannot inject arbitrary file writes.
    checked = prepare_supply_restore(plan['name'])
    if checked['fingerprint'] != plan['fingerprint']:
        raise RuntimeError('场景输入已变化，请重新预览恢复')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = root/'input_backups'/f'tie_supply_restore_{stamp}'
    backup.mkdir(parents=True, exist_ok=False)
    old = {rel: (root/rel).read_bytes() if (root/rel).exists() else None for rel in checked['changes']}
    for rel, content in old.items():
        if content is not None:
            target = backup/rel; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    # Preserve the full former typical baseline alongside its archived manifest.
    for rel in ('data/typical_baseline_profiles.csv', 'data/typical_source_overrides.csv'):
        if (root/rel).exists():
            target = backup/rel; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(root/rel, target)
    (backup/'restore_record.json').write_text(json.dumps(dict(schema=1, timestamp_utc=stamp,
        from_mode='fixed_load', to_mode='supply', removed_demand_columns=checked['removed_columns'],
        source_fingerprint=checked['fingerprint'],
        scope='active_scene_inputs_only;historical_runs_and_quotes_unchanged',
        baseline_reset=checked['resets_typical_baseline']), ensure_ascii=False, indent=2), encoding='utf-8')
    if fingerprint(root) != checked['fingerprint']:
        raise RuntimeError('备份期间场景输入已变化；未恢复，请重新预览')
    def write(relative, content):
        path = root/relative
        if content is None:
            path.unlink(missing_ok=True)
        else:
            fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream: stream.write(content)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    # Publish the semantics switch last. Any ordinary exception rolls back all
    # touched files; the durable originals are retained even after success.
    order = [rel for rel in checked['changes'] if rel != 'config/base.yaml'] + ['config/base.yaml']
    try:
        for rel in order: write(rel, checked['changes'][rel])
    except Exception:
        for rel in reversed(order): write(rel, old[rel])
        raise
    return dict(changed=True, backup=str(backup), removed_columns=checked['removed_columns'])
