"""Portable launch exclusion and read-only, PID-reuse-safe process inspection.

Unknown ownership is deliberately not treated as a dead worker. Imported history
and old records without a creation identity must never unlock a second solve.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import math
import os
from pathlib import Path
import socket
import sys


class LaunchError(RuntimeError):
    def __init__(self, stage, cause, *, directory=None, process_started=False):
        self.stage = stage
        self.cause = cause
        self.directory = directory
        self.process_started = process_started
        super().__init__(f"{stage}：{type(cause).__name__}: {cause}")


@contextmanager
def file_lock(path, *, blocking=False):
    """OS releases this lock on close/crash. Never delete its shared inode."""
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise RuntimeError("另一个启动或状态更新正在处理，请稍后重试。") from exc
                raise
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise RuntimeError("另一个启动或状态更新正在处理，请稍后重试。") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _psutil():
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("缺少进程检查依赖 psutil，请在当前 Python 环境重新安装 requirements.txt。") from exc
    return psutil


def process_scope():
    from .machine_identity import machine_identity, boot_identity
    psutil = _psutil()
    namespace = None
    if sys.platform.startswith("linux"):
        try:
            namespace = os.readlink("/proc/self/ns/pid")
        except OSError:
            pass  # This namespace symlink is optional; retain creation identity.
    return dict(host=socket.gethostname(), platform=sys.platform,
                boot_time=psutil.boot_time(), pid_namespace=namespace,
                machine_identity=machine_identity(), boot_identity=boot_identity())


def process_identity(pid=None):
    psutil = _psutil()
    process = psutil.Process(os.getpid() if pid is None else int(pid))
    return dict(pid=process.pid, create_time=process.create_time(), scope=process_scope())


def process_state(identity):
    """Return alive, dead, or unknown without ever signalling a process."""
    if not isinstance(identity, dict):
        return "unknown"
    pid, created, scope = identity.get("pid"), identity.get("create_time"), identity.get("scope")
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            or not isinstance(created, (int, float)) or isinstance(created, bool)
            or not math.isfinite(created) or not isinstance(scope, dict)):
        return "unknown"
    try:
        psutil = _psutil()
        current = process_scope()
        from .machine_identity import valid_evidence
        machine = scope.get("machine_identity")
        if not valid_evidence(machine) or machine != current.get("machine_identity"):
            return "unknown"
        old_boot, now_boot = scope.get("boot_identity"), current.get("boot_identity")
        if old_boot is not None and (not valid_evidence(old_boot) or old_boot != now_boot):
            return "unknown"
        if any(scope.get(k) != current[k] for k in ("host", "platform", "pid_namespace")):
            return "unknown"
        boot = scope.get("boot_time")
        if not isinstance(boot, (int, float)) or not math.isfinite(boot) or abs(boot-current["boot_time"]) > 2:
            return "unknown"
        process = psutil.Process(pid)
        if abs(process.create_time() - created) > 1e-6:
            return "dead"  # This PID now belongs to another process. Do not touch it.
        if not process.is_running() or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return "dead"
        return "alive"
    except Exception as exc:
        # AccessDenied, unavailable dependency, malformed or foreign metadata do
        # not establish that the recorded worker has stopped.
        if "psutil" in locals() and isinstance(exc, psutil.NoSuchProcess):
            return "dead"
        return "unknown"
