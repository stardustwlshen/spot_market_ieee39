"""Explicit repair for a missing reserve duration. Existing values are never rewritten."""
from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import yaml

ASSUMPTION_HOURS = 0.25  # Existing template assumption: 15-minute reserve energy, not a new parameter.


def inspect_scene_duration(root):
    """Read one scene. A failed load does not change files."""
    root = Path(root)
    modes_path = root / 'config' / 'modes.yaml'
    journal = (root / '.unit_edit_pending.json').is_file()
    info = dict(path=str(modes_path), journal=journal, readable=False, present=False,
                value=None, value_type=None, state='未能读取', rule=None, ok=False,
                repair_allowed=False, message='')
    if journal:
        info['message'] = '存在未完成的机组保存。请使用“恢复未完成的机组保存”，不要删除事务标记。'
        return info
    try:
        modes = yaml.safe_load(modes_path.read_text(encoding='utf-8'))
        base_file = root / 'config' / 'base.yaml'
        base = yaml.safe_load(base_file.read_text(encoding='utf-8')) if base_file.is_file() else {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        info['message'] = str(exc)
        return info
    info['readable'] = True
    rule = base.get('rule_set') if isinstance(base, dict) else None
    block = modes.get(rule) if isinstance(modes, dict) and isinstance(rule, str) else None
    reserve = block.get('reserve') if isinstance(block, dict) else None
    from .input_validation import reserve_duration_facts
    facts = reserve_duration_facts(reserve if isinstance(reserve, dict) else None)
    info.update(facts)
    info['rule'] = rule
    try:
        from .config import load_config
        load_config(root=root)
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        info['ok'] = False
        info['message'] = str(exc)
    else:
        info['ok'] = True
        info['message'] = ''
    info['repair_allowed'] = bool(
        not info['ok'] and info['readable'] and isinstance(reserve, dict)
        and facts['state'] == '缺键' and not facts['present']
        and '状态 缺键' in info['message'] and 'reserve.T_dur_h' in info['message'])
    return info


def repair_missing_reserve_duration(root):
    """Insert only the documented 0.25 h when the key is absent. Backup first."""
    root = Path(root)
    info = inspect_scene_duration(root)
    if info['journal']:
        raise ValueError(info['message'] + '未改配置。')
    if not info['repair_allowed']:
        raise ValueError('不能补写备用持续时间：' + (info['message'] or info['state']) + '。未改配置。')
    source = root / 'config' / 'modes.yaml'
    original = source.read_bytes()
    from ruamel.yaml import YAML
    parser = YAML()
    parser.preserve_quotes = True
    parser.width = 4096
    document = parser.load(original.decode('utf-8'))
    rule = info['rule']
    reserve = document[rule]['reserve']
    if 'T_dur_h' in reserve:
        raise ValueError('写入前发现 T_dur_h 已存在，未改配置。')
    reserve['T_dur_h'] = ASSUMPTION_HOURS
    stream = StringIO()
    parser.dump(document, stream)
    candidate = stream.getvalue().encode('utf-8')
    parsed = yaml.safe_load(candidate.decode('utf-8'))
    before = yaml.safe_load(original.decode('utf-8'))
    if parsed[rule]['reserve'].get('T_dur_h') != ASSUMPTION_HOURS:
        raise ValueError('补写结果不是 0.25，未替换原文件。')
    if parsed[rule]['reserve'].get('fixed_up_mw') != before[rule]['reserve'].get('fixed_up_mw'):
        raise ValueError('其他备用项会变化，未替换原文件。')
    if parsed[rule]['intraday'].get('enabled') != before[rule]['intraday'].get('enabled'):
        raise ValueError('日内开关会变化，未替换原文件。')
    backup_dir = root / 'config' / 'backups'
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = backup_dir / f'modes-before-missing-tdur-{stamp}.yaml'
    with backup.open('xb') as handle:
        handle.write(original)
    from .unit_edit_transaction import atomic_bytes, scene_input_lock
    with scene_input_lock(root):
        if source.read_bytes() != original or (root / '.unit_edit_pending.json').exists():
            raise RuntimeError('原文件或事务标记已变化，未继续写入。备份已保留：' + str(backup))
        atomic_bytes(source, candidate)
    from .config import load_config
    loaded = load_config(root=root)
    if loaded.mode['reserve']['T_dur_h'] != ASSUMPTION_HOURS:
        raise RuntimeError('补写后仍不能按 0.25 加载。备份保留：' + str(backup))
    return backup
