"""Conservative provenance for persisted synthetic profiles.

Only a matching whole-file digest permits regeneration. Missing/changed evidence
never authorizes guessing whether a column was uploaded or synthesized.
"""
from __future__ import annotations

import hashlib
import json


def override_sources(cfg, columns=None):
    from .input_validation import read_input_csv
    path = cfg.root / 'data/profile_overrides.csv'
    disk_columns = list(read_input_csv(path).columns) if path.exists() else []
    columns = disk_columns if columns is None else list(columns)
    values = [c for c in columns if c not in ('period', 'time')]
    protected, generated, configured = values.copy(), [], {}
    status, warnings, quote_columns = 'explicit_uploads', [], []
    manifest_path = cfg.root / 'config/typical_scenario.json'
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            proof = manifest.get('profile_sources', {})
            valid = (proof.get('version') == 1 and path.exists()
                     and proof.get('override_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
                     and set(proof.get('protected_columns', [])) | set(proof.get('generated_columns', [])) == set(values)
                     and not set(proof.get('protected_columns', [])) & set(proof.get('generated_columns', [])))
            if valid:
                protected = list(proof['protected_columns'])
                generated = list(proof['generated_columns'])
                configured = proof.get('configured_forecasts', {})
                valid = all(c in configured and len(configured[c]) == cfg.T
                            for c in generated if c.endswith(('_da', '_id', '_rt')))
            if valid:
                status = 'verified_generated_columns'
                quote_columns = list(proof.get('quote_columns', []))
            else:
                protected, generated, configured = values.copy(), [], {}
                status = 'unverified_preserve_all'
                warnings.append('原典型场景缺少可核验的列来源，或曲线文件已变化；保留所有已有列，误差选择仅作用于缺失预测列。')
        except (OSError, ValueError, TypeError, KeyError):
            protected, generated, configured = values.copy(), [], {}
            status = 'unverified_preserve_all'
            warnings.append('曲线来源记录无法核验；保守保留已有列。')
    locked_forecasts = [c for c in protected if c.endswith(('_da', '_id', '_rt')) and c not in quote_columns]
    if locked_forecasts:
        warnings.append('以下已有预测列受保护，误差场景不会应用到这些列：' + '、'.join(locked_forecasts))
    return dict(status=status, protected_columns=protected, generated_columns=generated,
                configured_forecasts=configured, warnings=warnings, quote_columns=quote_columns)


def column_source_rows(cfg, columns, protected_columns=(), quote_columns=(), *, limited=False):
    protected, quotes = set(protected_columns), set(quote_columns)
    rows = []
    names = {'load': '负荷', **{u['id']: u['id'] for u in cfg.units['renewable']}}
    stages = {'actual': '实际', 'da': '日前预测', 'id': '日内预测', 'rt': '实时预测'}
    for column in columns:
        if '_' not in column or column.rsplit('_', 1)[1] not in stages:
            continue
        name, stage = column.rsplit('_', 1)
        source = ('报价可用量优先' if column in quotes else
                  ('既有列（来源未核验）' if limited else '上传列') if column in protected else '系统生成')
        rows.append({'列': column, '对象': names.get(name, name), '阶段': stages[stage],
                     '来源': source, '误差场景可重生成': stage != 'actual' and column not in protected | quotes})
    return rows
