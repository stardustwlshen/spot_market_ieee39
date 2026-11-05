"""Server ownership safety. The only live process tests own a harmless sleeper."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dev_app_manager', ROOT / 'scripts/manage_app.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'outputs').mkdir()
    monkeypatch.setattr(manager, 'ROOT', tmp_path)
    return tmp_path


def save(directory, **changes):
    root = directory
    record = dict(format=1, root=str(root.resolve()), port=8501, address='127.0.0.1',
                  command=manager.command_for(root, 8501, '127.0.0.1'),
                  identity={'pid': 12345, 'create_time': 1.})
    record.update(changes)
    (root / 'outputs/app-process.json').write_text(json.dumps(record))
    (root / 'outputs/app.pid').write_text('12345\n')
    return record


def test_legacy_pid_never_authorizes_signal_or_deletion(root, monkeypatch):
    pid = root / 'outputs/app.pid'
    pid.write_text(str(__import__('os').getpid()))
    monkeypatch.setattr(manager, 'owned_process', lambda r: pytest.fail('must not inspect legacy PID'))
    with pytest.raises(manager.ManagementError, match='旧版'):
        manager.stop_server(root)
    assert pid.exists()


@pytest.mark.parametrize('data', ['[]','null','{broken'])
def test_corrupt_record_fails_closed(root, data):
    path = root / 'outputs/app-process.json'
    path.write_text(data)
    with pytest.raises(manager.ManagementError):
        manager.stop_server(root)
    assert path.read_text() == data


@pytest.mark.parametrize('changes', [{'root':'/another/project'}, {'command':['python','other.py']}])
def test_other_project_record_is_not_touched(root, changes):
    save(root, **changes)
    with pytest.raises(manager.ManagementError):
        manager.stop_server(root)
    assert (root / 'outputs/app.pid').exists()


def test_unknown_identity_never_signals(root, monkeypatch):
    save(root)
    monkeypatch.setattr(manager, 'process_state', lambda identity: 'unknown')
    with pytest.raises(manager.ManagementError, match='无法证明'):
        manager.stop_server(root)
    assert (root / 'outputs/app-process.json').exists()


def test_dead_identity_cleans_only_metadata(root, monkeypatch):
    save(root)
    log = root / 'outputs/app.log'; log.write_bytes(b'old logs')
    monkeypatch.setattr(manager, 'process_state', lambda identity: 'dead')
    manager.stop_server(root)
    assert not (root / 'outputs/app.pid').exists()
    assert not (root / 'outputs/app-process.json').exists()
    assert log.read_bytes() == b'old logs'


def test_actual_command_mismatch_never_signals(root, monkeypatch):
    import psutil
    record = save(root)
    monkeypatch.setattr(manager, 'process_state', lambda identity: 'alive')
    process = SimpleNamespace(create_time=lambda: 1., cmdline=lambda: [sys.executable,'another.py'],
                              terminate=lambda: pytest.fail('must not signal mismatch'))
    monkeypatch.setattr(psutil, 'Process', lambda pid: process)
    with pytest.raises(manager.ManagementError, match='实际命令'):
        manager.stop_server(root)
    assert (root / 'outputs/app-process.json').exists()


def test_stop_owned_harmless_child_requires_matching_identity_and_command(root, monkeypatch):
    command = [sys.executable, '-c', 'import time; time.sleep(30)']
    child = subprocess.Popen(command)
    try:
        monkeypatch.setattr(manager, 'command_for', lambda *args: command)
        save(root, command=command, identity=manager.process_identity(child.pid))
        manager.stop_server(root)
        child.wait(timeout=5)
        assert child.poll() is not None
        assert not (root / 'outputs/app-process.json').exists()
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=5)


def test_record_from_reused_pid_does_not_kill_current_child(root, monkeypatch):
    command = [sys.executable, '-c', 'import time; time.sleep(30)']
    child = subprocess.Popen(command)
    try:
        monkeypatch.setattr(manager, 'command_for', lambda *args: command)
        identity = manager.process_identity(child.pid)
        identity['create_time'] -= 100
        save(root, command=command, identity=identity)
        manager.stop_server(root)
        assert child.poll() is None
    finally:
        child.terminate(); child.wait(timeout=5)


def test_restart_does_not_start_when_stop_cannot_verify(root, monkeypatch):
    (root / 'outputs/app.pid').write_text('12345')
    monkeypatch.setattr(manager, 'start_server', lambda *args: pytest.fail('must not start'))
    assert manager.main(['restart']) == 1


def test_wrapper_uses_verified_manager_not_raw_kill():
    source = (ROOT / 'scripts/run_app.sh').read_text()
    assert 'scripts/manage_app.py' in source
    assert 'kill ' not in source and 'kill -0' not in source


def test_real_server_start_and_stop_preserve_prior_log(root):
    import socket
    (root / 'app').mkdir()
    (root / 'app/主页.py').write_text("import streamlit as st\nst.write('isolated smoke test')\n")
    log = root / 'outputs/app.log'; log.write_text('previous log sentinel\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    try:
        manager.start_server(root, port, '127.0.0.1')
        record = manager.read_record(root)
        assert manager.owned_process(record) is not None
        assert log.read_text().startswith('previous log sentinel')
    finally:
        if (root / 'outputs/app-process.json').exists():
            manager.stop_server(root)
    assert not (root / 'outputs/app.pid').exists()


def test_denied_stop_preserves_ownership_record(root, monkeypatch):
    import psutil
    save(root)
    def deny():
        raise psutil.AccessDenied(12345)
    monkeypatch.setattr(manager, 'owned_process', lambda record: SimpleNamespace(terminate=deny))
    with pytest.raises(manager.ManagementError, match='系统拒绝'):
        manager.stop_server(root)
    assert (root / 'outputs/app-process.json').exists()
    assert (root / 'outputs/app.pid').exists()
