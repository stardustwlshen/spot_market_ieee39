"""OS-owned process exclusion; no PID-based stale lock deletion.
Windows locks are acquired by the child before any market execution.
"""
from pathlib import Path
import os, json, time, subprocess, uuid

class GuardBusy(RuntimeError):
    pass

def acquire(path):
    path = Path(path)
    f = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            # A persistent sentinel byte; never truncate/unlink a live lock file.
            if path.stat().st_size == 0:
                f.write(b'0'); f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise GuardBusy('已有进程持有运行锁；不得重复执行') from exc
        elif os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise GuardBusy('已有进程持有运行锁；不得重复执行') from exc
        else:
            raise RuntimeError('Unsupported process-lock platform')
        return f
    except BaseException:
        f.close(); raise

def atomic_marker(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x', encoding='utf-8') as f:
        json.dump(value, f); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)

def claim_windows_worker(out):
    """Call before loading/executing market work. Keep returned file alive."""
    if os.name != 'nt':
        raise RuntimeError('Windows worker handshake only')
    out = Path(out).resolve()
    token = os.environ['GD_GUARD_TOKEN']
    guard_path = Path(os.environ['GD_GUARD_PATH']).resolve()
    try:
        guard = acquire(guard_path)
    except BaseException as exc:
        atomic_marker(out / 'LOCK_FAILED.json', dict(token=token, error=str(exc), native_calls=0))
        raise
    atomic_marker(out / 'LOCK_READY.json', dict(token=token, pid=os.getpid(), guard_path=str(guard_path)))
    return guard

def launch_windows(cmd, out, guard_path, log, env=None, timeout=30):
    """READY is proof of exclusive child ownership, not proof of solver success.
    A timeout is uncertain: do not retry or kill. Existing native inventory gates remain.
    """
    if os.name != 'nt':
        raise RuntimeError('Windows launcher only')
    out = Path(out).resolve()
    # Early diagnostic only. Child lock is the decisive race-safe exclusion.
    probe = acquire(guard_path); probe.close()
    token = uuid.uuid4().hex
    child_env = {**os.environ, **(env or {}), 'GD_GUARD_TOKEN':token,
                 'GD_GUARD_PATH':str(Path(guard_path).resolve())}
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, close_fds=True,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=child_env)
    atomic_marker(out / 'LAUNCH_PID.json', dict(pid=proc.pid, token=token, retry_allowed=False))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for name in ('LOCK_READY.json', 'LOCK_FAILED.json'):
            path = out / name
            if path.exists():
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('token') != token:
                    raise RuntimeError('Worker lock handshake identity mismatch; no retry')
                if name == 'LOCK_FAILED.json':
                    raise GuardBusy(value['error'])
                if value.get('pid') != proc.pid:
                    raise RuntimeError('Worker PID mismatch; no retry')
                return proc
        if proc.poll() is not None:
            raise RuntimeError('Worker exited before lock handshake; inspect logs, no automatic retry')
        time.sleep(.05)
    raise RuntimeError('Worker lock handshake pending/unknown; inspect LAUNCH_PID and logs, do not retry')

def process_namespace():
    import platform
    if os.name == 'posix':
        try:
            return 'pidns:' + os.readlink('/proc/self/ns/pid')
        except OSError:
            pass
    return 'host:' + platform.system() + ':' + platform.node()

def pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        # os.kill(pid, 0) on Windows is not a POSIX existence probe.
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenProcess(0x1000, False, int(pid))
        if not handle:
            # Access denied/unknown are not proof of death.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path('/proc') / str(int(pid)) / 'stat'
        return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return True



def observe_run(out):
    """Non-destructive liveness evidence; never unlock/delete or authorize resume.

    A PID alone cannot prove identity (reuse, copied results, another machine).
    Only inspect a lock in the recorded launch namespace. Missing launch/namespace
    information stays unknown; the recovery/native-call inventory remains separate.
    """
    out = Path(out)
    try:
        record = json.loads((out / 'PID.json').read_text())
        if record.get('process_namespace') != process_namespace():
            return dict(state='unknown', reason='launch_namespace_missing_or_different')
        lock_path = out / 'PROCESS.guard'
        if not lock_path.is_file():
            return dict(state='unknown', reason='run_lock_missing')
        try:
            guard = acquire(lock_path)
        except GuardBusy:
            return dict(state='lock_held', reason='run_lock_owned')
        guard.close()
        return dict(state='interrupted', reason='run_lock_free_without_exit', restart_safe=False)
    except (OSError, ValueError, RuntimeError):
        return dict(state='unknown', reason='liveness_evidence_unavailable')
