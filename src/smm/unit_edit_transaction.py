"""Recoverable, reader-locked scene input transactions (never historical runs)."""
from contextlib import contextmanager
import base64
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading

from .process_control import file_lock

_LOCAL = threading.local()
JOURNAL = '.unit_edit_pending.json'


def atomic_bytes(path, content):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.unit-edit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _recover(root):
    journal = root / JOURNAL
    if not journal.exists():
        return
    records = json.loads(journal.read_bytes())
    allowed = {'config/units.yaml', 'config/generated_research_costs.json', 'current_quote.json'}
    if not isinstance(records, dict) or not set(records) <= allowed:
        raise ValueError('机组保存恢复记录无效，请保留场景并检查，未继续读取混合输入。')
    # Refuse to overwrite independent changes made after an interrupted commit.
    for relative, record in records.items():
        path = root / relative
        current = path.read_bytes() if path.exists() else None
        old = base64.b64decode(record['old']) if record['old'] is not None else None
        new = base64.b64decode(record['new'])
        if current not in (old, new):
            raise ValueError('未完成的机组保存与外部修改冲突，请恢复备份后继续。')
    for relative, record in records.items():
        path = root / relative
        if record['old'] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_bytes(path, base64.b64decode(record['old']))
    journal.unlink()


@contextmanager
def scene_input_lock(root, *, recover=False):
    """Reentrant per-thread lock; freeze/load never read half a unit transaction."""
    root = Path(root)
    identity = str(root.resolve())
    held = getattr(_LOCAL, 'held', set())
    if identity in held:
        yield
        return
    # Reader synchronization must not add files to read-only scenarios/snapshots.
    locks = Path(tempfile.gettempdir()) / ('smm-scene-input-locks-' + str(getattr(os, 'getuid', lambda: 'user')()))
    if locks.is_symlink():
        raise ValueError('输入锁目录不能是符号链接')
    locks.mkdir(mode=0o700, exist_ok=True)
    lock = locks / (hashlib.sha256(identity.encode()).hexdigest() + '.lock')
    with file_lock(lock, blocking=True):
        _LOCAL.held = held | {identity}
        try:
            if (root / JOURNAL).exists():
                if not recover:
                    raise ValueError('发现未完成的机组保存。请先恢复上次完整输入，禁止读取混合参数。')
                _recover(root)
            yield
        finally:
            _LOCAL.held = held


def commit_inputs(root, replacements):
    """Caller holds scene_input_lock and input-writer locks; rollback any exception."""
    root = Path(root)
    journal = root / JOURNAL
    records = {}
    for relative, new in replacements.items():
        path = root / relative
        old = path.read_bytes() if path.exists() else None
        records[relative] = dict(old=base64.b64encode(old).decode() if old is not None else None,
                                 new=base64.b64encode(new).decode())
    atomic_bytes(journal, json.dumps(records, allow_nan=False).encode())
    try:
        for relative, new in replacements.items():
            atomic_bytes(root / relative, new)
        journal.unlink()
    except BaseException:
        _recover(root)
        raise


def recover_scene_unit_edit(root):
    """Explicit recovery takes the same writer locks as commit, never races quotes."""
    root = Path(root)
    with file_lock(root / '.cost_archive.lock', blocking=True), file_lock(root / 'current_quote.lock', blocking=True), scene_input_lock(root, recover=True):
        pass
