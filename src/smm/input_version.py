"""Read-only comparison of saved scene inputs and the exact launch request.

This evidence is captured before freezing. A frozen effective config alone cannot
prove which source files or launch overrides produced a historical run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

SCHEMA = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bid_digest(bids):
    # Default quotations get a creation timestamp on each read. It is not input.
    return digest({key: value for key, value in bids.items() if key != 'created'})


def run_options(mode='full', periods=None, regulation=None):
    return dict(mode=mode, periods=None if mode == 'da' or not periods else int(periods),
                regulation=copy.deepcopy(regulation))


def valid_options(options):
    return (isinstance(options, dict) and set(options) == {'mode', 'periods', 'regulation'}
            and options['mode'] in ('full', 'da')
            and (options['periods'] is None or
                 (isinstance(options['periods'], int) and not isinstance(options['periods'], bool) and options['periods'] > 0))
            and (options['regulation'] is None or isinstance(options['regulation'], dict)))


def _current_quote(root):
    path = Path(root) / 'current_quote.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('当前报价必须是本场景普通文件')
    quote = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(quote, dict) or not isinstance(quote.get('draft'), dict):
        raise ValueError('当前报价存档不能核对')
    return dict(draft={k: v for k, v in quote['draft'].items() if k != 'created'},
                manual=sorted(quote.get('manual', [])))


def source_version(root, *, bids=None, bid_version=None, options=None, quote_policy='workspace'):
    """Call under the scene lock. Never save defaults or rewrite current quotes."""
    from .live_runner import file_fingerprint
    from .config import load_config
    from .bids import bid_dir, default_bids, load_bids, fill_missing_bids, effective_bids
    cfg = load_config(root=root)
    quote = _current_quote(root)
    if bids is None:
        if quote_policy == 'workspace' and quote is not None:
            bids = quote['draft']
        elif list(bid_dir(cfg).glob('bids_v*.json')):
            bids = load_bids(cfg, bid_version if quote_policy == 'archived' else None)
        else:
            bids = default_bids(cfg)
    resolved = effective_bids(cfg, fill_missing_bids(cfg, bids))
    return dict(schema=SCHEMA, source=file_fingerprint(root, ('config', 'data', 'bids')),
                current_quote=digest(quote), resolved_bids=bid_digest(resolved),
                quote_policy=quote_policy, bid_version=bid_version,
                run_options=copy.deepcopy(options),
                scope='saved config/data/bids, current quote and manual markers, resolved bids, launch overrides')


def compare_source_version(directory, root, *, options=None):
    """Return equal/changed/unknown. Absence is never equality; no fallback hashes."""
    path = Path(directory) / 'frozen_input_provenance.json'
    result = dict(status='unknown', changes=[], message='来源无法核对：旧运行缺少源输入清单或有效运行覆盖记录。')
    if not path.is_file():
        return {**result, 'message': '本次历史运行未记录源输入清单，无法核对当前输入是否一致；仍展示冻结历史。'}
    try:
        evidence = json.loads(path.read_text(encoding='utf-8')).get('source_version')
        required = {'schema', 'source', 'current_quote', 'resolved_bids', 'quote_policy', 'run_options'}
        if not isinstance(evidence, dict) or not required <= set(evidence) or evidence['schema'] != SCHEMA:
            return result
        if not isinstance(evidence['source'].get('files'), dict) or not evidence['source']['files']:
            return result
        essential = {'config/base.yaml', 'config/units.yaml', 'config/modes.yaml',
                     'data/case39/buses.csv', 'data/case39/branches.csv'}
        if not essential <= set(evidence['source']['files']):
            return result
        def valid_hash(value):
            return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
        if (not valid_hash(evidence['current_quote']) or not valid_hash(evidence['resolved_bids'])
                or not all(valid_hash(v) for v in evidence['source']['files'].values())
                or evidence['quote_policy'] not in ('archived', 'workspace')):
            return result
        if any(evidence.get(key) is False for key in ('effective_config_matches_source',
                'effective_bids_match_source', 'effective_profiles_match_source', 'effective_network_matches_source')):
            message = ('本次CLI有效网络与源网络不一致，冻结网络证据不足；无法核对与当前输入是否一致。'
                       if evidence.get('frozen_network_verified') is False else
                       '来源无法核对：本次含未完整记录覆盖关系的内存配置、人工报价、曲线或有效网络。')
            return {**result, 'message': message}
        if root is None or not (Path(root) / 'config/base.yaml').is_file():
            return {**result, 'message': '来源无法核对：找不到此运行对应的当前场景；仍可查看冻结历史。'}
        from .unit_edit_transaction import scene_input_lock
        from .live_runner import file_fingerprint
        changes = []
        with scene_input_lock(root):
            files = file_fingerprint(root, ('config', 'data', 'bids'))['files']
            old = evidence['source']['files']
            changes.extend(sorted(key for key in set(old) | set(files) if old.get(key) != files.get(key)))
            try:
                current_quote = digest(_current_quote(root))
            except (OSError, ValueError, TypeError):
                changes.append('当前报价已损坏 / 无法解析')
            else:
                if current_quote != evidence['current_quote']:
                    changes.append('当前报价 / 人工报价标记')
            if valid_options(options) and valid_options(evidence['run_options']) and options != evidence['run_options']:
                changes.append('运行模式 / 实时范围 / 运行覆盖')
            # A changed source file is definitive even if its new content fails validation.
            if changes:
                return dict(status='changed', changes=changes,
                            message='输入已变化，待重新运行：此图仍是历史运行，不会随当前输入改写。')
            current = source_version(root, bid_version=evidence.get('bid_version'),
                                     quote_policy=evidence['quote_policy'], options=options)
        if current['resolved_bids'] != evidence['resolved_bids']:
            changes.append('实际解析报价')
        if valid_options(options) and valid_options(evidence['run_options']) and options != evidence['run_options']:
            changes.append('运行模式 / 实时范围 / 运行覆盖')
        if changes:
            return dict(status='changed', changes=changes,
                        message='输入已变化，待重新运行：此图仍是历史运行，不会随当前输入改写。')
        if not valid_options(evidence['run_options']):
            return {**result, 'message': '已保存场景与报价一致；历史运行缺少有效模式 / 范围记录，无法核对运行范围。'}
        if not valid_options(options):
            frozen = evidence['run_options']
            scope = '仅日前' if frozen['mode'] == 'da' else ('完整单日' if frozen['periods'] is None else f"前 {frozen['periods']} 个实时时段")
            return {**result, 'message': f'已保存场景与报价与本次冻结来源一致；冻结运行范围：{scope}。当前会话尚未确认运行范围，未比较当前运行选项；这不表示冻结来源损坏。'}
        return dict(status='equal', changes=[], message='输入一致：已保存场景、当前报价与运行模式 / 范围均与本次冻结来源相同。')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
        return {**result, 'message': '来源暂不能核对；仍展示冻结历史，不使用当前输入补齐。', 'detail': str(exc)}
