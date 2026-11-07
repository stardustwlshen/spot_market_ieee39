"""One durable current quotation per scene, independent from saved bid versions."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone


def editable_bids(cfg, version=None):
    """Read invalid old bids for repair; validate again before execution."""
    from smm.bids import bid_dir, default_bids, fill_missing_bids
    files = sorted(bid_dir(cfg).glob('bids_v*.json'), key=lambda p: int(p.stem.split('_v')[-1]))
    if not files:
        return default_bids(cfg)
    path = files[-1] if version is None else bid_dir(cfg) / f'bids_v{version}.json'
    return fill_missing_bids(cfg, json.loads(path.read_text(encoding='utf-8')))


def _json(value):
    # Invalid legacy values (including NaN) remain recoverable until repaired.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)


def quotation_changed(draft, baseline):
    return (_json({k: v for k, v in draft.items() if k != 'created'}) !=
            _json({k: v for k, v in baseline.items() if k != 'created'}))


def scene_draft_key(name):
    return 'quote_current:' + name


def _latest_basis(cfg):
    from smm.bids import bid_dir
    versions = sorted(int(p.stem.split('_v')[-1]) for p in bid_dir(cfg).glob('bids_v*.json'))
    return editable_bids(cfg), (f'场景已保存报价 v{versions[-1]}' if versions else '场景缺省报价')


def _legacy_drafts(state, name):
    """Retain all old data; a source pointer, not this enumeration, selects activity."""
    prefixes = (f'draft_{name}_', f'bid_draft:{name}:', f'quote_recovered:{name}:')
    return {key: copy.deepcopy(state[key]) for key in sorted(state)
            if key.startswith(prefixes) and isinstance(state[key], dict)
            and isinstance(state[key].get('units'), dict)}


def _active_legacy_key(state, name, latest, *, has_saved=True):
    source = state.get(f'quote_source:{name}')
    if source is None:
        return None
    if isinstance(source, int):
        return f'bid_draft:{name}:{source}'
    if source == '最新':
        return f'bid_draft:{name}:{latest["version"] if has_saved else "default"}'
    recovered = f'quote_recovered:{name}:{source}'
    return recovered if recovered in state else source


def _digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _read_current(cfg):
    path = cfg.root / 'current_quote.json'
    data = path.read_bytes() if path.exists() else None
    if data is None:
        return None, None
    record = json.loads(data)
    if (not isinstance(record, dict) or record.get('schema') != 1
            or not isinstance(record.get('draft'), dict) or not isinstance(record['draft'].get('units'), dict)
            or not isinstance(record.get('baseline'), dict) or not isinstance(record['baseline'].get('units'), dict)
            or not isinstance(record.get('history', {}), dict) or not isinstance(record.get('basis_label'), str)):
        raise ValueError('当前报价存档格式无效，原文件已保留，请修复后继续。')
    return record, _digest(data)


def get_scene_working_draft(state, name, cfg):
    """Initialize once from the scene; preserve edits across navigation and restarts."""
    key = scene_draft_key(name)
    if key not in state or key + ':meta' not in state:
        record, etag = _read_current(cfg)
        if record is None:
            history = _legacy_drafts(state, name)
            try:
                latest, latest_label = _latest_basis(cfg)
            except (OSError, ValueError, KeyError, TypeError):
                # An explicit older active editor remains valid even if a new
                # unrelated archive is broken. Never replace it with defaults.
                source = state.get(f'quote_source:{name}')
                if source is None or source == '最新':
                    raise
                active = _active_legacy_key(state, name, {})
                if active not in history:
                    raise
                latest, latest_label = copy.deepcopy(history[active]), '旧编辑保留时的报价'
            else:
                active = _active_legacy_key(state, name, latest, has_saved=latest_label != '场景缺省报价')
            draft, baseline, origin, basis_label = latest, latest, '', latest_label
            manual = []
            if active in history:
                draft = history[active]
                try:
                    if latest_label == '场景缺省报价':
                        baseline, basis_label = latest, latest_label
                    else:
                        from smm.bids import bid_dir
                        version = int(draft['version'])
                        if not (bid_dir(cfg) / f'bids_v{version}.json').exists():
                            raise FileNotFoundError('旧编辑的原报价版本不存在')
                        baseline = editable_bids(cfg, version)
                        basis_label = f'场景已保存报价 v{version}'
                except (ValueError, KeyError, TypeError, FileNotFoundError):
                    # No original baseline survives: use the retained original
                    # as the per-unit reset point; never invent unrelated values.
                    baseline = copy.deepcopy(draft)
                    basis_label = '旧编辑保留时的报价'
                origin = '已接续升级前正在编辑的报价；其余旧编辑仅备份，不参与运行。'
                manual = state.get(active + ':manual', [])
                manual = sorted(set(manual) | {uid for uid, bid in draft['units'].items()
                    if _json(bid) != _json(baseline['units'].get(uid))})
            elif history:
                origin = '发现旧编辑，但没有可靠的当前编辑标记；已全部备份，本页从场景最新已保存报价开始。'
            record = dict(schema=1, draft=copy.deepcopy(draft), baseline=copy.deepcopy(baseline),
                          basis_label=basis_label, origin=origin, history=history, manual=copy.deepcopy(manual),
                          initial_latest=copy.deepcopy(latest), saved_at=None)
        else:
            # Another browser may have created the durable current quote while
            # this one still holds old unsaved editors. Archive every variant,
            # including same-key/different-content conflicts, before refreshing.
            added = False
            history = record.setdefault('history', {})
            for old_key, old_draft in _legacy_drafts(state, name).items():
                archive_key = old_key
                if old_key in history and _json(history[old_key]) != _json(old_draft):
                    archive_key += ':' + hashlib.sha256(_json(old_draft).encode()).hexdigest()[:12]
                if archive_key not in history:
                    history[archive_key] = old_draft
                    added = True
            if added:
                record['origin'] = ('本页使用已自动保存的当前报价。本会话升级前的旧编辑已另存完整备份，'
                                    '未覆盖当前报价；可展开“保留的旧编辑”找回所选机组。')
        state[key] = copy.deepcopy(record['draft'])
        state[key + ':manual'] = copy.deepcopy(record.get('manual', []))
        state[key + ':meta'] = {k: copy.deepcopy(v) for k, v in record.items() if k not in ('draft', 'manual')}
        state[key + ':etag'] = etag
        # A compact editor may render immediately on a read-only visit. Keep an
        # in-memory checkpoint so only real edits (including rerun actions) save.
        state[key + ':checkpoint'] = _json({**state[key + ':meta'],
            'draft': state[key], 'manual': state[key + ':manual']})
    info = state[key + ':meta']
    if key + ':checkpoint' not in state:
        # A session opened before checkpoint support must also remain read-only
        # on its first compact visit, without discarding its retained edits.
        state[key + ':checkpoint'] = _json({
            **{k: v for k, v in info.items() if k not in ('newer_basis', 'latest_error')},
            'draft': state[key], 'manual': state.get(key + ':manual', [])})
    # A scene's saved file may change in another tool/tab. Never swap the user's
    # current bid under live widgets; show the pinned basis instead.
    try:
        latest, _ = _latest_basis(cfg)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        info['latest_error'] = f'{type(exc).__name__}: {exc}'
        info['newer_basis'] = False
    else:
        info.pop('latest_error', None)
        # Display labels also describe scene copies and technical edits; they
        # are not source identities. An explicitly retained older version must
        # still warn even when initial_latest already captured the newer one.
        initial_latest = info.get('initial_latest')
        if initial_latest is None:
            initial_latest = info['baseline']
        info['newer_basis'] = (latest.get('version') != info['baseline'].get('version') or
                               quotation_changed(latest, initial_latest))
    return key, state[key], info['baseline'], info


def persist_scene_working_draft(state, name, cfg, *, only_if_changed=False):
    """Atomically save only current workspace data, with concurrent-edit detection."""
    from smm.process_control import file_lock
    key = scene_draft_key(name)
    info = state[key + ':meta']
    record = {k: copy.deepcopy(v) for k, v in info.items() if k not in ('newer_basis', 'latest_error')}
    record.update(draft=copy.deepcopy(state[key]), manual=copy.deepcopy(state.get(key + ':manual', [])))
    if only_if_changed and state.get(key + ':checkpoint') == _json(record):
        return
    path = cfg.root / 'current_quote.json'
    with file_lock(cfg.root / 'current_quote.lock'):
        current = path.read_bytes() if path.exists() else None
        if _digest(current) != state.get(key + ':etag'):
            raise ValueError('另一页面已更新此场景的当前报价。本页编辑仍保留，但未覆盖对方；请先下载本页报价备份，再刷新核对。')
        if current is not None and _json(json.loads(current)) == _json(record):
            state[key + ':checkpoint'] = _json(record)
            return
        record['saved_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        data = _json(record).encode('utf-8')
        handle, temporary = tempfile.mkstemp(prefix='.current_quote-', suffix='.tmp', dir=cfg.root)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        state[key + ':etag'] = _digest(data)
        info['saved_at'] = record['saved_at']
        state[key + ':checkpoint'] = _json(record)


def quotation_backup(state, name):
    """A real portable ZIP: current quote, complete old edits and readable index."""
    key = scene_draft_key(name)
    info = state[key + ':meta']
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('当前报价.json', _json(state[key]))
        archive.writestr('基础报价.json', _json(info['baseline']))
        index = ['报价备份：所有 JSON 均保留完整原值，未合并不同草稿。',
                 '在工作台展开“从文件恢复报价（仅所选机组）”，上传其中一个报价 JSON，可核对后仅恢复所选机组。',
                 f'当前基础：{info["basis_label"]}', '']
        for number, (old_key, draft) in enumerate(info.get('history', {}).items(), 1):
            filename = f'旧编辑/报价{number}.json'
            archive.writestr(filename, _json(draft))
            index.append(f'{filename}：{old_key}')
        archive.writestr('说明.txt', '\n'.join(index))
    return buffer.getvalue()


def uninitialized_legacy_backup(state, name):
    """Download-only rescue when no trustworthy current workspace can be loaded."""
    history = _legacy_drafts(state, name)
    if not history:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        lines = ['当前报价初始化失败。这些旧编辑未被覆盖；请保留此备份并修复报价文件后恢复。']
        for number, (key, draft) in enumerate(history.items(), 1):
            filename = f'旧编辑/报价{number}.json'
            archive.writestr(filename, _json(draft))
            lines.append(f'{filename}：{key}')
        archive.writestr('说明.txt', '\n'.join(lines))
    return buffer.getvalue()
