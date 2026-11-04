"""Bootstrap regressions: no network installation is performed in these tests."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_launcher', ROOT / 'scripts' / 'launch_app.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
MANIFEST = ROOT / 'requirements-app.txt'


def seeded_environment(tmp_path, *, ready=False, created=False):
    directory = launcher.environment_path(tmp_path, MANIFEST)
    python = launcher.environment_python(directory)
    python.parent.mkdir(parents=True)
    python.write_text('test executable', encoding='utf-8')
    (directory / '.ieee39-launcher.json').write_text('{}')
    if created:
        (directory / '.venv-created.json').write_text('{}')
    if ready:
        (directory / '.ready.json').write_text('{}')
    return directory, python


def test_manifest_matches_probe_imports():
    assert set(launcher.pinned_requirements(MANIFEST)) == set(launcher.IMPORTS)
    assert '-r requirements-app.txt' in (ROOT / 'requirements.txt').read_text()


@pytest.mark.parametrize('bad', ['streamlit>=1.0', '-r somewhere.txt', 'unreviewed==1.0', ''])
def test_manifest_mismatch_is_explicit(tmp_path, bad):
    manifest = tmp_path / 'requirements-app.txt'
    manifest.write_text(bad)
    with pytest.raises(launcher.LaunchFailure):
        launcher.pinned_requirements(manifest)


def test_rejects_python_311_before_creating_environment(monkeypatch):
    monkeypatch.setattr(launcher.sys, 'version_info', (3, 11, 10))
    with pytest.raises(launcher.LaunchFailure, match='3.12'):
        launcher.validate_python()


def test_interpreter_paths_preserve_spaces_and_unicode(tmp_path):
    directory = tmp_path / '中文 project'
    assert launcher.environment_python(directory, True) == directory / 'Scripts/python.exe'
    assert launcher.environment_python(directory, False) == directory / 'bin/python'


def test_environment_fingerprint_changes_on_dependency_update(tmp_path):
    manifest = tmp_path / 'requirements-app.txt'
    manifest.write_text('old')
    old = launcher.environment_path(tmp_path, manifest)
    manifest.write_text('new')
    assert old != launcher.environment_path(tmp_path, manifest)


def test_child_env_ignores_activation_and_install_destination(monkeypatch):
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX',
                'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PIP_REQUIREMENT', 'PIP_NO_DEPS'):
        monkeypatch.setenv(key, 'unexpected')
    monkeypatch.setenv('PIP_CONFIG_FILE', 'user-pip.ini')
    monkeypatch.setenv('PIP_INDEX_URL', 'https://example.test/simple')
    env = launcher.child_environment()
    assert not any(key in env for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV',
                                         'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PIP_REQUIREMENT', 'PIP_NO_DEPS'))
    assert env['PIP_CONFIG_FILE'] == os.devnull
    assert env['PIP_REQUIRE_VIRTUALENV'] == 'true'
    assert env['PIP_INDEX_URL'] == 'https://example.test/simple'


def test_lock_rejects_concurrent_and_releases_after_failure(tmp_path):
    lock = tmp_path / 'launcher.lock'
    with launcher.launcher_lock(lock):
        with pytest.raises(launcher.LaunchFailure):
            with launcher.launcher_lock(lock):
                pass
    with launcher.launcher_lock(lock):
        pass


def test_occupied_port_is_not_taken_over():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(launcher.LaunchFailure, match='端口'):
            launcher.ensure_port_available(listener.getsockname()[1])
        assert listener.fileno() >= 0


def test_check_does_not_create_missing_environment(tmp_path):
    with pytest.raises(launcher.LaunchFailure, match='尚未'):
        launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO(), check_only=True)
    assert not (tmp_path / '.local-app').exists()


def test_unknown_environment_directory_is_preserved(tmp_path):
    directory = launcher.environment_path(tmp_path, MANIFEST)
    directory.mkdir(parents=True)
    sentinel = directory / 'someone-elses-file'
    sentinel.write_bytes(b'keep this exactly')
    with pytest.raises(launcher.LaunchFailure, match='未覆盖或删除'):
        launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO())
    assert sentinel.read_bytes() == b'keep this exactly'


def test_partial_venv_is_recreated_before_install(tmp_path, monkeypatch):
    directory, _ = seeded_environment(tmp_path)
    calls = []
    monkeypatch.setattr(launcher, 'run_logged', lambda command, *a, **k: calls.append(command))
    monkeypatch.setattr(launcher, 'validate_environment', lambda *a: None)
    launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO())
    assert calls[0][1:3] == ['-m', 'venv']
    assert calls[1][1:4] == ['-m', 'pip', 'install']
    assert (directory / '.venv-created.json').exists()
    assert (directory / '.ready.json').exists()


def test_pip_failure_is_retriable_and_keeps_user_data(tmp_path, monkeypatch):
    directory, _ = seeded_environment(tmp_path, created=True)
    protected = []
    for folder in ('scenarios', 'runs', 'outputs', 'bids', 'config', '.venv'):
        target = tmp_path / folder / 'keep.dat'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'original\x00bytes')
        protected.append(target)
    calls = []

    def fail_install(command, *a, **k):
        calls.append(command)
        raise launcher.LaunchFailure('network failed')

    monkeypatch.setattr(launcher, 'run_logged', fail_install)
    with pytest.raises(launcher.LaunchFailure, match='重新双击'):
        launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO())
    assert not (directory / '.ready.json').exists()
    assert all(path.read_bytes() == b'original\x00bytes' for path in protected)
    monkeypatch.setattr(launcher, 'run_logged', lambda command, *a, **k: calls.append(command))
    monkeypatch.setattr(launcher, 'validate_environment', lambda *a: None)
    launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO())
    assert (directory / '.ready.json').exists()
    assert all(path.read_bytes() == b'original\x00bytes' for path in protected)


def test_ready_environment_checks_without_reinstall(tmp_path, monkeypatch):
    directory, python = seeded_environment(tmp_path, ready=True, created=True)
    with patch.object(launcher, 'run_logged') as run, patch.object(launcher, 'validate_environment') as probe:
        assert launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO()) == python
        run.assert_not_called()
        probe.assert_called_once()


def test_damaged_ready_environment_is_not_silently_overwritten(tmp_path):
    seeded_environment(tmp_path, ready=True, created=True)
    with patch.object(launcher, 'run_logged') as run, patch.object(launcher, 'validate_environment', side_effect=launcher.LaunchFailure('bad')):
        with pytest.raises(launcher.LaunchFailure, match='未覆盖环境'):
            launcher.prepare_environment(tmp_path, MANIFEST, io.StringIO())
        run.assert_not_called()


def test_version_detection_ignores_inputs_and_does_not_migrate(tmp_path):
    (tmp_path / 'requirements-app.txt').write_text('pins')
    code = tmp_path / 'app' / 'main.py'
    code.parent.mkdir()
    code.write_text('first code')
    scene = tmp_path / 'scenarios' / '旧场景' / 'current_quote.json'
    scene.parent.mkdir(parents=True)
    scene.write_bytes(b'custom quote')
    (tmp_path / '.local-app').mkdir()
    first = launcher.note_source_version(tmp_path)
    scene.write_bytes(b'new custom quote')
    assert launcher.source_digest(tmp_path) == first
    code.write_text('new code')
    output = io.StringIO()
    with redirect_stdout(output):
        assert launcher.note_source_version(tmp_path) != first
    assert '已有场景' in output.getvalue()
    assert scene.read_bytes() == b'new custom quote'


def test_probe_uses_same_solver_as_market():
    code = launcher.probe_script(launcher.pinned_requirements(MANIFEST), Path('/temporary env'))
    compile(code, '<probe>', 'exec')
    assert 'appsi_highs' in code
    assert 'sys.prefix != sys.base_prefix' in code


def test_streamlit_receives_explicit_runtime_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'ensure_port_available', lambda port: None)
    python = tmp_path / '中文 env' / 'bin/python'
    with patch.object(launcher.subprocess, 'Popen', side_effect=OSError('injected before spawn')) as spawn:
        with pytest.raises(OSError):
            launcher.start_server(python, tmp_path, 8501, io.StringIO(), no_browser=True, initial_digest='source-sha')
    args = spawn.call_args.kwargs
    assert args['env']['IEEE39_LAUNCH_SOURCE_SHA256'] == 'source-sha'
    assert args['env']['IEEE39_LAUNCH_ROOT'] == str(tmp_path)
    assert args['env']['IEEE39_LAUNCH_INTERPRETER'] == str(python)
    assert args['cwd'] == tmp_path
    assert spawn.call_args.args[0][0] == str(python)


def test_source_identity_excludes_secrets(tmp_path):
    (tmp_path / 'requirements-app.txt').write_text('pins')
    streamlit = tmp_path / '.streamlit'
    streamlit.mkdir()
    secret = streamlit / 'secrets.toml'
    secret.write_text('a secret')
    digest = launcher.source_digest(tmp_path)
    secret.write_text('changed secret')
    assert launcher.source_digest(tmp_path) == digest


def test_incomplete_source_is_friendly(monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'source_digest', None)
    with patch.object(launcher, 'prepare_environment') as install:
        assert launcher.main(['--install-only']) == 1
        install.assert_not_called()
    assert '项目文件不完整' in capsys.readouterr().out


def test_invalid_port_fails_without_installation(monkeypatch, capsys):
    with patch.object(launcher, 'prepare_environment') as install:
        assert launcher.main(['--port', '65536']) == 1
        install.assert_not_called()
    assert '端口须为' in capsys.readouterr().out


@pytest.mark.skipif(os.name == 'nt', reason='bash wrapper is only for POSIX hosts')
def test_shell_wrappers_have_valid_syntax():
    subprocess.run(['bash', '-n', str(ROOT / 'start.sh'), str(ROOT / 'start.command')], check=True)


def test_windows_wrapper_rejects_bad_working_directory():
    text = (ROOT / 'start.bat').read_text()
    assert 'cd /d "%~dp0"\nif errorlevel 1' in text
    assert 'scripts\\launch_app.py %*' in text
