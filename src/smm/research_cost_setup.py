"""Versioned, explicit research defaults layered over untouched user archives."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import yaml

from .research_defaults import (ALGORITHM_VERSION, SOURCE_KIND, generate_research_defaults,
    is_known_legacy_research_record, is_known_legacy_worksheet_record, research_record_hash)
from .process_control import file_lock

FILENAME = 'generated_research_costs.json'
GROUPS = ('thermal', 'renewable_vom', 'hydro', 'storage', 'pumped')
LEGACY_FIXED_SOURCE = ('Explicit fixed synthetic cash operating cost profile; independent of bids and offer worksheets. '
    'Initial thermal marginal assumptions are 280/297/315/405/620 CNY per MWh. Fixed online and startup parameters '
    'are copied once from original technical assumptions (startup hot fee), not read from bids at runtime.')
LEGACY_SHEET_SOURCE = ('Synthetic research assumptions; EIA references support heat-rate formula/type '
                      'distinctions only, not these unit-specific values')


def known_legacy_origin(data, *, worksheet=False):
    return bool(data and data.get('source_kind') == SOURCE_KIND and
                data.get('source') == (LEGACY_SHEET_SOURCE if worksheet else LEGACY_FIXED_SOURCE))


def _origin(data, groups):
    return research_record_hash({k:v for k,v in (data or {}).items()
        if k not in (*groups, 'available', 'sha256', 'input_file')})


def _bytes(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode()


def _file(path):
    if path.parent.is_symlink() or path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f'研究成本不处理链接或非普通文件：{path}')
    return path.read_bytes() if path.exists() else None


def _yaml(path):
    content = _file(path)
    if content is None:
        return None
    try:
        result = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f'成本档案语法无效：{path}') from exc
    if not isinstance(result, dict):
        raise ValueError(f'成本档案须为对象：{path}')
    return result


def read_generated_costs(root):
    content = _file(Path(root) / 'config' / FILENAME)
    if content is None:
        return None
    overlay = json.loads(content)
    if not isinstance(overlay, dict):
        raise ValueError('已生成成本记录不完整；请核对其来源。')
    identity = {k: v for k, v in overlay.items() if k != 'fingerprint'}
    if (overlay.get('version') != ALGORITHM_VERSION or
            overlay.get('fingerprint') != hashlib.sha256(_bytes(identity)).hexdigest()):
        raise ValueError('已生成成本记录不完整或已被修改；不会覆盖，请核对其来源。')
    try:
        for uid, record in overlay['profile']['units'].items():
            if overlay['evidence'][uid]['record_hash'] != research_record_hash(record):
                raise ValueError('现金成本与生成依据不一致。')
        for group in GROUPS:
            for uid, record in overlay['worksheet'][group].items():
                if overlay['worksheet_evidence'][group][uid]['record_hash'] != research_record_hash(record):
                    raise ValueError('报价依据与生成记录不一致。')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('已生成成本记录不完整；请核对其来源。') from exc
    return overlay


def _allowed(current, expected):
    return current is None if expected is None else (current is not None and research_record_hash(current) == expected)


def merge_fixed_profile(root, original):
    overlay = read_generated_costs(root)
    if overlay is None:
        return original
    profile = copy.deepcopy(original)
    original_origin = _origin(original, ('units',)) if original.get('available') else _origin(None, ('units',))
    if not profile.get('available'):
        profile = {k: copy.deepcopy(v) for k, v in overlay['profile'].items() if k != 'units'}
        profile.update(available=True, units={})
    sources = profile.setdefault('unit_sources', {})
    for uid in profile['units']:
        sources.setdefault(uid, dict(source_kind=profile.get('source_kind', 'explicit_assumption_status_not_supplied'),
                                     source=profile.get('source'), input_file='config/fixed_costs.yaml'))
    applied = []
    for uid, record in overlay['profile']['units'].items():
        current = profile['units'].get(uid)
        if (_allowed(current, overlay['expected_fixed'][uid]) and
                (current is None or original_origin == overlay['expected_fixed_origin'])):
            profile['units'][uid] = copy.deepcopy(record)
            sources[uid] = dict(source_kind=SOURCE_KIND, generator_version=ALGORITHM_VERSION,
                                record_hash=research_record_hash(record), evidence=overlay['evidence'][uid])
            applied.append(uid)
    if applied:
        profile['base_source_kind'] = original.get('source_kind')
        profile['base_source'] = original.get('source')
        profile['source_kind'] = SOURCE_KIND if set(applied) == set(profile['units']) else 'mixed_explicit_and_synthetic_costs'
        profile['source'] = 'Explicitly generated synthetic defaults for ' + ', '.join(applied) + '; other records retain their declared unit_sources.'
    profile['applied_research_units'] = applied
    base = _file(Path(root) / 'config/fixed_costs.yaml')
    active = _file(Path(root) / 'config' / FILENAME)
    profile['sha256'] = hashlib.sha256((base or b'') + b'\0' + active).hexdigest()
    profile['input_files'] = ['config/fixed_costs.yaml', 'config/' + FILENAME]
    profile['research_defaults_version'] = overlay['fingerprint']
    return profile


def merge_cost_worksheet(root, original):
    overlay = read_generated_costs(root)
    if overlay is None:
        return original
    sheet = copy.deepcopy(original) if original is not None else {
        k: copy.deepcopy(v) for k, v in overlay['worksheet'].items() if k not in GROUPS}
    sources = sheet.setdefault('unit_sources', {})
    applied = []
    for group in GROUPS:
        records = sheet.setdefault(group, {})
        for uid in records:
            sources.setdefault(uid, dict(source_kind=sheet.get('source_kind', 'explicit_assumption_status_not_supplied'),
                                         source=sheet.get('source'), input_file='config/bid_cost_basis.yaml'))
        for uid, record in overlay['worksheet'].get(group, {}).items():
            current = records.get(uid)
            if (_allowed(current, overlay['expected_worksheet'][group][uid]) and
                    (current is None or _origin(original, GROUPS) == overlay['expected_worksheet_origin'])):
                records[uid] = copy.deepcopy(record)
                sources[uid] = dict(source_kind=SOURCE_KIND, generator_version=ALGORITHM_VERSION,
                                    evidence=overlay['worksheet_evidence'][group][uid])
                applied.append(uid)
    if applied:
        sheet['base_source_kind'] = (original or {}).get('source_kind')
        sheet['base_source'] = (original or {}).get('source')
        sheet['source_kind'] = SOURCE_KIND if set(applied) == set(sources) else 'mixed_explicit_and_synthetic_costs'
        sheet['source'] = 'Explicitly generated synthetic offer assumptions for ' + ', '.join(applied) + '; other records retain their declared unit_sources.'
    sheet['research_defaults_version'] = overlay['fingerprint']
    return sheet


def _snapshot(root):
    if (root / 'config').is_symlink() or not (root / 'config').is_dir():
        raise ValueError('研究成本只写入当前场景的普通配置目录，不处理目录链接。')
    return {name: hashlib.sha256(content).hexdigest() if content is not None else None
            for name in ('base.yaml', 'units.yaml', 'modes.yaml', 'fixed_costs.yaml', 'bid_cost_basis.yaml', FILENAME)
            for content in [_file(root / 'config' / name)]}


def prepare_generated_cost_setup(cfg, *, replace_known_research=True):
    """Plan defaults from this scene's technical parameters; original files stay exact."""
    from .profit import validate_profile
    root = Path(cfg.root)
    before = _snapshot(root)
    old = read_generated_costs(root)
    generated = generate_research_defaults(cfg)
    fixed = _yaml(root / 'config/fixed_costs.yaml')
    sheet = _yaml(root / 'config/bid_cost_basis.yaml')
    if fixed is not None:
        validate_profile(fixed)
    if sheet is not None and sheet.get('version') != 'synthetic-cost-study-v1':
        raise ValueError('原报价研究档案格式无效，未替换原始数据。')
    expected_fixed, preserved = {}, []
    profile = copy.deepcopy(generated['profile'])
    evidence = copy.deepcopy(generated['evidence'])
    for uid in list(profile['units']):
        current = (fixed or {}).get('units', {}).get(uid)
        previous = (old is not None and uid in old['profile']['units'] and _allowed(current, old['expected_fixed'][uid])
                    and (current is None or _origin(fixed, ('units',)) == old['expected_fixed_origin']))
        known = known_legacy_origin(fixed) and is_known_legacy_research_record(uid, current)
        if current is None or previous or (replace_known_research and known):
            expected_fixed[uid] = research_record_hash(current) if current is not None else None
            if previous and not replace_known_research:
                profile['units'][uid] = copy.deepcopy(old['profile']['units'][uid])
                evidence[uid] = copy.deepcopy(old['evidence'][uid])
        else:
            del profile['units'][uid]
            preserved.append(uid)
    worksheet = copy.deepcopy(generated['worksheet'])
    worksheet_evidence = {group: {
        uid: dict(copy.deepcopy(generated['evidence'][uid]), record_hash=research_record_hash(record))
        for uid, record in worksheet[group].items()} for group in GROUPS}
    expected_worksheet = {group: {} for group in GROUPS}
    for group in GROUPS:
        if sheet is not None and not isinstance(sheet.get(group), dict):
            raise ValueError(f'原报价研究档案 {group} 不是资源对象，未替换原始数据。')
        for uid in list(worksheet[group]):
            current = (sheet or {}).get(group, {}).get(uid)
            previous = (old is not None and uid in old['worksheet'].get(group, {}) and
                        _allowed(current, old['expected_worksheet'][group][uid]) and
                        (current is None or _origin(sheet, GROUPS) == old['expected_worksheet_origin']))
            known = (known_legacy_origin(sheet, worksheet=True) and
                     is_known_legacy_worksheet_record(group, uid, current))
            if current is None or previous or (replace_known_research and known):
                expected_worksheet[group][uid] = research_record_hash(current) if current is not None else None
                if previous and not replace_known_research:
                    worksheet[group][uid] = copy.deepcopy(old['worksheet'][group][uid])
                    worksheet_evidence[group][uid] = copy.deepcopy(old['worksheet_evidence'][group][uid])
            else:
                del worksheet[group][uid]
                del worksheet_evidence[group][uid]
    data = dict(version=ALGORITHM_VERSION, source_kind=SOURCE_KIND, profile=profile, worksheet=worksheet,
                expected_fixed=expected_fixed, expected_worksheet=expected_worksheet,
                expected_fixed_origin=_origin(fixed, ('units',)), expected_worksheet_origin=_origin(sheet, GROUPS),
                evidence={uid: evidence[uid] for uid in profile['units']},
                worksheet_evidence=worksheet_evidence, preserved_custom_units=preserved)
    data['fingerprint'] = hashlib.sha256(_bytes(data)).hexdigest()
    if _snapshot(root) != before:
        raise RuntimeError('机组参数或成本已变化，请重新生成。')
    has_records = bool(profile['units'] or any(worksheet[group] for group in GROUPS))
    return dict(root=str(root), snapshot=before, data=data, replace_known_research=replace_known_research,
                changed=has_records and old != data, generated_units=list(profile['units']), preserved_units=preserved)


def apply_generated_cost_setup(plan):
    """Persist a version and atomically activate it after a deliberate UI action."""
    from .config import load_config
    root = Path(plan['root'])
    with file_lock(root / '.cost_archive.lock', blocking=True):
        fresh = prepare_generated_cost_setup(load_config(root=root),
                    replace_known_research=plan['replace_known_research'])
        if fresh != plan:
            if (read_generated_costs(root) == plan['data'] and
                    {k:v for k,v in fresh['snapshot'].items() if k != FILENAME} ==
                    {k:v for k,v in plan['snapshot'].items() if k != FILENAME}):
                return root / 'config' / FILENAME
            raise RuntimeError('参数或成本已变化，请重新核对后生成。原始成本未修改。')
        data = plan['data']
        versions = root / 'research_cost_versions'
        if versions.is_symlink():
            raise ValueError('研究成本版本目录不能是符号链接。')
        versions.mkdir(exist_ok=True)
        previous = read_generated_costs(root)
        for version_data in ([previous] if previous and previous != data else []) + [data]:
            version = versions / (version_data['fingerprint'] + '.json')
            version_content = _bytes(version_data)
            if version.exists():
                if _file(version) != version_content:
                    raise ValueError('已有研究成本版本被修改，拒绝覆盖。')
            else:
                temporary_version = None
                try:
                    with tempfile.NamedTemporaryFile(dir=versions, prefix='.version-', delete=False) as stream:
                        temporary_version = Path(stream.name)
                        stream.write(version_content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary_version, version)
                finally:
                    if temporary_version is not None:
                        temporary_version.unlink(missing_ok=True)
        content = _bytes(data)
        path = root / 'config' / FILENAME
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.research-cost-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if _snapshot(root) != plan['snapshot']:
                raise RuntimeError('保存期间参数变化，未替换活动研究成本。')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path


def initialize_missing_cost_defaults(root):
    """New scenes may generate missing records, never replace inherited records."""
    from .config import load_config
    units = _yaml(Path(root) / 'config/units.yaml') or {}
    if not any(units.get(group) for group in ('thermal', 'renewable', 'hydro', 'storage', 'pumped')):
        return
    cfg = load_config(root=root)
    plan = prepare_generated_cost_setup(cfg, replace_known_research=False)
    has_records = plan['generated_units'] or any(plan['data']['worksheet'][group] for group in GROUPS)
    if has_records and plan['changed']:
        apply_generated_cost_setup(plan)
