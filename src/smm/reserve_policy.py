"""New-run reserve policy; archival Config reads retain their stored semantics."""
from __future__ import annotations
import copy
import hashlib
import io
from pathlib import Path

POLICY = 'user_fixed_up_400_20261108'


def for_new_run(cfg):
    """Return a detached effective config; never rewrite the source scene."""
    effective=copy.deepcopy(cfg)
    reserve=effective.mode['reserve']
    if 'up_requirement_mode' not in reserve:
        reserve.update(up_requirement_mode='fixed', fixed_up_mw=400., down_enabled=False)
    return effective


def policy_record(cfg):
    r=cfg.mode['reserve']
    mode=r.get('up_requirement_mode','formula')
    return dict(up_requirement_mode=mode, fixed_up_mw=r.get('fixed_up_mw') if mode=='fixed' else None,
                down_enabled=r.get('down_enabled',True))


def reserve_policy_label(cfg, *, new_run=True):
    current=for_new_run(cfg) if new_run else cfg
    r=current.mode['reserve']
    if r.get('up_requirement_mode','formula')=='fixed':
        label=f"每时段固定 {float(r['fixed_up_mw']):g} MW（总量，不叠加比例或事故项）"
    else:
        label=f"公式：{r['rho_L']:g}×负荷 + {r['rho_RE']:g}×风光" + (' + 最大事故项' if r['include_ctg'] else '')
    if new_run and 'up_requirement_mode' not in cfg.mode['reserve']:
        label+='；新运行采用用户400MW设定，原场景与旧结果保留'
    return label


def freeze_new_run_policy(snapshot, directory):
    """Called only after exclusive creation of a new run snapshot, never on archives."""
    from .config import load_config
    from .live_runner import atomic_json
    from ruamel.yaml import YAML
    snapshot,directory=Path(snapshot),Path(directory)
    cfg=load_config(root=snapshot)
    if 'up_requirement_mode' in cfg.mode['reserve']:
        return None
    source=snapshot/'config/modes.yaml'
    original=source.read_bytes()
    backup=directory/'reserve_source_modes.yaml'
    with backup.open('xb') as stream:stream.write(original)
    y=YAML();y.preserve_quotes=True
    doc=y.load(original.decode('utf-8-sig'))
    doc[cfg.rule_set]['reserve'].update(up_requirement_mode='fixed',fixed_up_mw=400.,down_enabled=False)
    stream=io.StringIO();y.dump(doc,stream)
    source.write_text(stream.getvalue(),encoding='utf-8')
    effective=load_config(root=snapshot)
    record=dict(policy=POLICY,source='user instruction 2026-11-08',
                before=policy_record(cfg),after=policy_record(effective),
                source_modes_sha256=hashlib.sha256(original).hexdigest(),
                effective_modes_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                backup_file=backup.name,scope='new run snapshot only; source scene and historical results unchanged')
    atomic_json(directory/'reserve_policy_migration.json',record)
    return record


def finalize_frozen_policy(snapshot, directory):
    """Hash the final snapshot after all independent launch overrides."""
    import json
    from .live_runner import atomic_json
    path=Path(directory)/'reserve_policy_migration.json'
    if path.exists():
        record=json.loads(path.read_text(encoding='utf-8'))
        record['effective_modes_sha256']=hashlib.sha256((Path(snapshot)/'config/modes.yaml').read_bytes()).hexdigest()
        atomic_json(path,record)


def record_cli_policy(cfg, snapshot, directory):
    """Keep original rule bytes for CLI automatic 400MW adoption as well."""
    from .config import load_config
    from .live_runner import atomic_json
    original=load_config(root=cfg.root)
    if ('up_requirement_mode' in original.mode['reserve']
            or policy_record(cfg)!=dict(up_requirement_mode='fixed',fixed_up_mw=400.,down_enabled=False)):
        return
    data=(Path(cfg.root)/'config/modes.yaml').read_bytes()
    backup=Path(directory)/'reserve_source_modes.yaml'
    with backup.open('xb') as stream:stream.write(data)
    atomic_json(Path(directory)/'reserve_policy_migration.json',dict(policy=POLICY,
        source='user instruction 2026-11-08',before=policy_record(original),after=policy_record(cfg),
        source_modes_sha256=hashlib.sha256(data).hexdigest(),
        effective_modes_sha256=hashlib.sha256((Path(snapshot)/'config/modes.yaml').read_bytes()).hexdigest(),
        backup_file=backup.name,scope='new CLI run snapshot only; source scene and historical results unchanged'))
