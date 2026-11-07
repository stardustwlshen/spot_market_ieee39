"""Real Windows sharing locks plus non-Windows fault injection; no solver needed."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

import pytest
from smm.live_runner import atomic_json, ProgressWriter, RunCancelled


@contextmanager
def deny_delete(path):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    # Permit reading/writing, deliberately deny FILE_SHARE_DELETE.
    handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0x80,None)
    if handle==wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    closed=False
    def close():
        nonlocal closed
        if not closed:
            assert kernel.CloseHandle(handle)
            closed=True
    try:
        yield close
    finally:
        close()


@pytest.mark.skipif(os.name!='nt',reason='Windows file-sharing reproduction')
def test_real_windows_transient_reader_lock_retries_and_preserves_atomicity(tmp_path):
    target=tmp_path/'live.json';atomic_json(target,{'version':'old'})
    candidate=tmp_path/'candidate.json';candidate.write_text('{"version":"blocked"}',encoding='utf8')
    with deny_delete(target) as close:
        with pytest.raises(PermissionError):
            candidate.replace(target)  # Prove the original failure with an OS handle.
        assert json.loads(target.read_text(encoding='utf8'))=={'version':'old'}
        timer=threading.Timer(.08,close);timer.start()
        try:
            atomic_json(target,{'version':'new','payload':'中'*10000})
        finally:
            timer.join()
    assert json.loads(target.read_text(encoding='utf8'))['version']=='new'
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(os.name!='nt',reason='Windows file-sharing reproduction')
def test_real_windows_persistent_lock_does_not_abort_telemetry_but_critical_write_fails(tmp_path,capsys):
    writer=ProgressWriter(tmp_path)
    target=tmp_path/'live.json';old=target.read_bytes()
    with deny_delete(target):
        writer.state['executed_periods']=41
        writer.write()
        assert target.read_bytes()==old
        assert writer.state['executed_periods']==41
        assert json.loads((tmp_path/'progress.json').read_text(encoding='utf8'))['executed_periods']==41
        with pytest.raises(PermissionError):
            atomic_json(target,{'critical':'must not be silently discarded'})
    assert '[progress warning]' in capsys.readouterr().out
    writer.state['phase']='done';writer.write()
    saved=json.loads(target.read_text(encoding='utf8'))
    assert saved['phase']=='done' and saved['executed_periods']==41
    assert '[progress recovered]' in capsys.readouterr().out


@pytest.mark.skipif(os.name!='nt',reason='Windows file-sharing reproduction')
def test_real_locked_telemetry_preserves_cancel_and_terminal_status(tmp_path):
    writer=ProgressWriter(tmp_path)
    (tmp_path/'cancel.request').touch()
    with deny_delete(tmp_path/'live.json'),deny_delete(tmp_path/'progress.json'):
        with pytest.raises(RunCancelled):
            writer({'phase':'da_stage','stage':'scuc_preplan','stage_diagnostics':{}})
        writer.state.update(phase='stopped',settled=False)
        writer.write()  # Even terminal telemetry cannot mask worker cancellation.
        atomic_json(tmp_path/'meta.json',{'status':'stopped','settled':False})
    assert json.loads((tmp_path/'meta.json').read_text(encoding='utf8'))['status']=='stopped'
    writer.write()
    assert json.loads((tmp_path/'live.json').read_text(encoding='utf8'))['phase']=='stopped'


def test_progress_never_swallows_business_or_serialization_errors(tmp_path,monkeypatch):
    import smm.live_runner as live
    writer=ProgressWriter(tmp_path)
    writer.state['bad']=float('nan')
    with pytest.raises(ValueError):writer.write()
    del writer.state['bad']
    def cancel(*args,**kwargs):raise RunCancelled('stop')
    monkeypatch.setattr(live,'atomic_json',cancel)
    with pytest.raises(RunCancelled):writer.write()


def test_atomic_retry_is_bounded_for_permission_only(tmp_path,monkeypatch):
    attempts=[]
    def deny(*a):
        attempts.append(True)
        raise PermissionError('denied')
    monkeypatch.setattr(Path,'replace',deny)
    monkeypatch.setattr('smm.live_runner.time.sleep',lambda s:None)
    with pytest.raises(PermissionError):atomic_json(tmp_path/'critical.json',{'a':1})
    assert len(attempts)==8
    assert not list(tmp_path.glob('*.tmp'))


def test_recovery_is_reported_only_after_all_blocked_paths_recover(tmp_path,monkeypatch,capsys):
    import smm.live_runner as live
    writer=ProgressWriter(tmp_path)
    original=live.atomic_json
    def deny_small(path,value):
        if Path(path).name=='progress.json':raise PermissionError('small record locked')
        return original(path,value)
    monkeypatch.setattr(live,'atomic_json',deny_small)
    writer.write();writer.write()
    assert '[progress recovered]' not in capsys.readouterr().out
    monkeypatch.setattr(live,'atomic_json',original)
    writer.write()
    assert '[progress recovered]' in capsys.readouterr().out
