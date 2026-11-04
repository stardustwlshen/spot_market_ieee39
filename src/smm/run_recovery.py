"""Explicit, locked recovery of provably absent workers. Never sends signals.

The audit and terminal state are a single atomic metadata replacement, so a write
failure cannot release the reservation without its evidence. All other artifacts
are left byte-for-byte untouched. The global launch lock is shared with start_run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from . import process_control as pc
from .machine_identity import valid_evidence
from .live_runner import atomic_json

ACTIVE = ('running', 'starting', 'cancelling')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read(directory):
    raw = (directory / 'meta.json').read_bytes()
    meta = json.loads(raw)
    if not isinstance(meta, dict):
        raise ValueError('运行元数据格式无效，保留原文件')
    worker_path = directory / 'worker.json'
    worker = worker_path.read_bytes() if worker_path.exists() else None
    revision = hashlib.sha256(raw + b'\0' + (worker or b'')).hexdigest()
    return meta, worker, revision


def _proof(identity, current, *, launcher=False):
    """A launcher dying in the same boot never proves its detached child died."""
    if not isinstance(identity, dict) or not isinstance(identity.get('scope'), dict):
        return False, 'missing_identity', '缺少完整进程身份，不能确认原进程已退出。'
    scope = identity['scope']
    machine = scope.get('machine_identity')
    if not valid_evidence(machine):
        return False, 'legacy_machine_identity', '旧记录缺少稳定机器身份；仅凭主机名、PID或启动时间不能安全解锁。'
    if not valid_evidence(current.get('machine_identity')):
        return False, 'local_machine_unavailable', '当前系统无法读取稳定机器身份，暂不能验证归属。'
    if (machine != current['machine_identity'] or scope.get('platform') != current.get('platform')
            or scope.get('host') != current.get('host')):
        return False, 'foreign_machine', '记录来自其他机器或系统身份已变化；不能在本机确认原进程已退出。'
    pid, created = identity.get('pid'), identity.get('create_time')
    boot, now = scope.get('boot_time'), current.get('boot_time')
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            or not all(_number(v) and v > 0 for v in (created, boot, now)) or created < boot - 2):
        return False, 'invalid_identity', '进程创建时间或启动证据无效，不能恢复。'
    old_session, new_session = scope.get('boot_identity'), current.get('boot_identity')
    if valid_evidence(old_session) and valid_evidence(new_session) and old_session != new_session:
        if old_session['source'] != new_session['source'] or now <= boot + 2 or created >= now:
            return False, 'inconsistent_boot', '启动会话已变化，但时间证据不一致或发生回退；不能确认旧运行已退出。'
        return True, 'previous_boot', '稳定机器身份一致，系统已进入新的启动会话；原启动会话中的进程已不存在。'
    if abs(boot - now) > 2:
        return False, 'unproven_boot', '启动时间已变化，但缺少可验证的新启动会话证据；不能仅按时间推断进程死亡。'
    if scope.get('pid_namespace') != current.get('pid_namespace'):
        return False, 'foreign_namespace', 'PID命名空间不同，不能检查原进程是否仍在运行。'
    if launcher:
        return False, 'launcher_only', '仅有启动器身份；启动器退出不代表后台求解进程退出。'
    state = pc.process_state(identity)
    if state == 'dead':
        return True, 'verified_dead', '已核对同机进程身份，原进程不存在或PID已被其他进程复用。'
    if state == 'alive':
        return False, 'alive', '原进程仍在运行；请等待结束，或请求在求解窗口边界取消。'
    return False, 'unknown', '无法确认进程状态（可能权限不足或身份不可核验）；不会强制解锁。'


def _inspect(meta, worker, revision):
    result = {'eligible': False, 'revision': revision, 'status': meta.get('status'), 'proofs': []}
    if meta.get('status') not in ACTIVE:
        return {**result, 'reason': 'terminal', 'message': '该运行已结束，无需恢复。'}
    identities = []
    if 'process_identity' in meta:
        identities.append(('process_identity', meta['process_identity'], False))
    if worker is not None:
        try:
            identities.append(('worker.json', json.loads(worker), False))
        except (ValueError, UnicodeError):
            return {**result, 'reason': 'invalid_worker', 'message': 'worker.json无法读取，保留占用以避免重复求解。'}
    if not identities:
        identities.append(('launcher_identity', meta.get('launcher_identity'), True))
    try:
        current = pc.process_scope()
        result['current_scope'] = current
        for source, identity, launcher in identities:
            eligible, reason, message = _proof(identity, current, launcher=launcher)
            result['proofs'].append({'source': source, 'identity': identity, 'reason': reason})
            if not eligible:
                return {**result, 'reason': reason, 'message': message}
        return {**result, 'eligible': True, 'reason': result['proofs'][0]['reason'],
                'message': '已核实原进程不再存在，可将此记录标记为中断并解除运行占用。'}
    except Exception:
        return {**result, 'reason': 'inspection_unavailable', 'message': '系统进程证据暂时无法读取，保持占用，请稍后重试。'}


def inspect_recovery(directory):
    directory = Path(directory)
    try:
        return _inspect(*_read(directory))
    except (OSError, ValueError, UnicodeError):
        return {'eligible': False, 'reason': 'unreadable', 'message': '运行记录无法读取；保留原文件与运行占用。'}


def recover_run(directory, runs_root, *, expected_revision):
    """Explicit UI action. Recheck under both locks; reject stale/foreign input."""
    directory, runs_root = Path(directory).resolve(), Path(runs_root).resolve()
    if directory == runs_root or runs_root not in directory.parents:
        raise ValueError('只能恢复当前项目运行目录内的记录')
    with pc.file_lock(runs_root / '.launch.lock'):
        with pc.file_lock(directory / '.meta.lock'):
            meta, worker, revision = _read(directory)
            previous = meta.get('recovery')
            if (meta.get('status') == 'failed' and isinstance(previous, dict)
                    and previous.get('previous_revision') == expected_revision):
                return meta  # Repeated click after the exact same successful action.
            if not expected_revision or revision != expected_revision:
                raise RuntimeError('运行记录已变化，请刷新并重新核对，未改写任何运行状态。')
            evidence = _inspect(meta, worker, revision)
            if not evidence['eligible']:
                raise RuntimeError(evidence['message'])
            now = datetime.now(timezone.utc).isoformat()
            updated = {**meta, 'status': 'failed', 'stage': '已核验中断（非完成）',
                       'error': '已核实原后台进程不再存在；执行前缀保留，未确认完整计算或结算。',
                       'settled': False, 'settlement_calculated': False,
                       'settlement_scope': 'not_applicable', 'completed_scope': None,
                       'recovery': {'version': 1, 'action': 'confirm_absent_and_recover',
                                    'at_utc': now, 'previous_revision': revision,
                                    'previous_meta': meta, 'evidence': evidence,
                                    'cancel_requested': (directory / 'cancel.request').exists()}}
            atomic_json(directory / 'meta.json', updated)
            return updated
