"""Own a fresh worker directory without ever rewriting a previous run.

Only the live launcher parent may hand a matching ``starting`` reservation to
its child. The execution lock remains held for the entire child run.
"""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path

from .process_control import file_lock, process_state


class RunDirectoryError(RuntimeError):
    pass


def _reservation(directory, expected):
    try:
        meta = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RunDirectoryError('结果目录已存在且不是有效的新运行预留；旧文件未修改') from exc
    if not isinstance(meta, dict) or meta.get('status') != 'starting':
        raise RunDirectoryError('结果目录不是 starting 预留，禁止复用历史运行')
    if any(meta.get(key) != value for key, value in expected.items()):
        raise RunDirectoryError('预留的场景、模式、报价版本或时段数与本次请求不匹配')
    started = meta.get('started')
    try:
        if not isinstance(started, str):
            raise ValueError()
        datetime.fromisoformat(started)
    except ValueError as exc:
        raise RunDirectoryError('运行预留缺少有效的开始时间') from exc
    owner = meta.get('launcher_identity')
    if (not isinstance(owner, dict) or owner.get('pid') != os.getppid()
            or process_state(owner) != 'alive'):
        raise RunDirectoryError('不能验证运行预留属于当前存活的启动父进程')
    allowed = {'meta.json', '.meta.lock', '.execution.lock', 'scenario_snapshot',
               'run.log', 'worker.json', 'worker.pid', 'submitted_bids.json',
               'frozen_input_provenance.json', 'cancel.request',
               # The launcher writes these before spawning for legacy 400 MW adoption.
               'reserve_policy_migration.json', 'reserve_source_modes.yaml',
               'launch_warning.json', 'launch_error.log',
               f'worker.json.{os.getppid()}.tmp',
               f'launch_warning.json.{os.getppid()}.tmp'}
    if any(p.name not in allowed for p in directory.iterdir()):
        raise RunDirectoryError('运行预留中已有结果或未知文件，禁止覆盖')
    worker_path = directory / 'worker.json'
    if worker_path.exists():
        try:
            worker = json.loads(worker_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RunDirectoryError('运行预留的工作进程身份文件无效') from exc
        if (not isinstance(worker, dict) or worker.get('pid') != os.getpid()
                or process_state(worker) != 'alive'):
            raise RunDirectoryError('运行目录已分配给另一个工作进程')
    return meta


@contextmanager
def admit_run_directory(directory, *, scenario, mode, bid_version=None,
                        periods=None, created_here=False):
    """Yield existing reservation metadata, or {} for our exclusive new folder."""
    directory = Path(directory)
    expected = dict(scenario=scenario, mode=mode, bid_version=bid_version, periods=periods)
    if not created_here:
        try:
            directory.mkdir(parents=True, exist_ok=False)
            created_here = True
        except FileExistsError:
            # Inspect before opening a lock file: rejected history stays byte-for-byte intact.
            _reservation(directory, expected)
    elif any(directory.iterdir()):
        raise RunDirectoryError('新运行目录不为空，禁止覆盖')
    with file_lock(directory / '.execution.lock'):
        metadata = {} if created_here else _reservation(directory, expected)
        yield metadata
