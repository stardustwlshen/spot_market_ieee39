"""A fresh folder should diagnose missing package data and broken native runtime."""
from pathlib import Path
import errno
import hashlib

import pytest
from types import SimpleNamespace

from scripts import launch_sichuan as launcher


def _tree(path):
    for name in ('app/四川.py','data/case39/buses.csv','data/case39/branches.csv'):
        f=path/name
        f.parent.mkdir(exist_ok=True,parents=True)
        f.touch()


def test_sichuan_preflight_does_not_require_legacy_entry(tmp_path,monkeypatch):
    _tree(tmp_path)
    import pyomo.environ as p
    monkeypatch.setattr(launcher.importlib,'import_module',lambda _:None)
    monkeypatch.setattr(p,'SolverFactory',lambda _:SimpleNamespace(available=lambda **_:True))
    assert launcher.check(tmp_path)==[]
    assert 'app/主页.py' in launcher.check(tmp_path,'guangdong')[0]


def test_missing_network_asset_is_diagnosed_before_loading_dependencies(tmp_path,monkeypatch):
    _tree(tmp_path)
    (tmp_path/'data/case39/branches.csv').unlink()
    errors=launcher.check(tmp_path)
    assert len(errors)==1 and 'data/case39/branches.csv' in errors[0]


def test_present_but_broken_shared_library_is_not_a_pass(tmp_path,monkeypatch):
    _tree(tmp_path)
    def simulate(name):
        if name=='highspy':
            raise OSError('missing runtime library')
    monkeypatch.setattr(launcher.importlib,'import_module',simulate)
    errors=launcher.check(tmp_path)
    assert len(errors)==1 and 'highspy' in errors[0] and '无法加载' in errors[0]


def test_pyomo_backend_unavailable_is_not_a_pass(tmp_path,monkeypatch):
    _tree(tmp_path)
    import pyomo.environ as p
    monkeypatch.setattr(launcher.importlib,'import_module',lambda _:None)
    monkeypatch.setattr(p,'SolverFactory',lambda _:SimpleNamespace(available=lambda **_:False))
    assert 'HiGHS 无法用于 Pyomo' in launcher.check(tmp_path)[0]


def test_launch_rejects_occupied_loopback_without_announcing_url(monkeypatch, capsys):
    import socket
    from scripts import launch_sichuan as launch
    monkeypatch.setattr(launch, 'check', lambda *_:[])
    monkeypatch.setattr(launch,'python_for',lambda _,python,__ :python)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('must not start Streamlit on an occupied port')
    monkeypatch.setattr(launch.subprocess,'call',forbidden)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as other:
        other.bind(('127.0.0.1',0))
        other.listen()
        port=other.getsockname()[1]
        assert launch.main(['--port',str(port)])==2
    messages=capsys.readouterr()
    assert not messages.out
    assert '已被占用，未启动' in messages.err and '--port' in messages.err


def test_post_probe_failure_is_streamlit_failure_and_legacy_is_unaffected(monkeypatch, capsys):
    import sys
    from scripts import launch_sichuan as launch
    monkeypatch.setattr(launch,'check',lambda *_:[])
    monkeypatch.setattr(launch,'python_for',lambda _,python,__:python)
    calls=[]
    def lost_race(cmd, **kw):
        calls.append(cmd)
        print('Port 8501 is not available',file=sys.stderr)
        return 1
    monkeypatch.setattr(launch.subprocess,'call',lost_race)
    probes=[]
    monkeypatch.setattr(launch,'_bind_loopback',lambda port:probes.append(port))
    assert launch.main([])==1
    first=capsys.readouterr()
    assert 'http://' not in first.out
    assert 'Port 8501 is not available' in first.err
    assert launch.main(['--profile','guangdong'])==1
    assert probes==[8501]
    assert [Path(cmd[4]).name for cmd in calls]==['四川.py','主页.py']


def test_default_port_skips_only_occupied_listeners_and_identifies_checkout(monkeypatch, capsys):
    probes, launches = [], []
    def simulated_bind(port):
        probes.append(port)
        if port in (8501,8502):
            raise OSError(errno.EADDRINUSE, 'occupied')

    monkeypatch.setattr(launcher, '_bind_loopback', simulated_bind)
    monkeypatch.setattr(launcher, 'check', lambda *_:[])
    monkeypatch.setattr(launcher, 'python_for', lambda root,python,platform:python)
    monkeypatch.setattr(launcher.subprocess, 'call', lambda cmd, **kw: launches.append((cmd,kw)) or 0)
    assert launcher.main([]) == 0
    assert probes == [8501,8502,8503]
    [(cmd, kw)] = launches
    assert cmd[cmd.index('--server.port')+1] == '8503'
    assert cmd[cmd.index('--server.address')+1] == '127.0.0.1'
    assert Path(cmd[4]) == kw['cwd'] / 'app' / '四川.py'
    expected = launcher.view_source_id(kw['cwd'])
    assert kw['env']['SICHUAN_UI_SOURCE_ID'] == expected
    messages = capsys.readouterr()
    assert expected in messages.out and str(Path(cmd[4])) in messages.out
    assert '8503' in messages.out and 'http://' not in messages.out


def test_permission_error_does_not_trigger_port_search(monkeypatch):
    seen = []
    def denied(port):
        seen.append(port)
        raise PermissionError(errno.EACCES, 'restricted by policy')
    monkeypatch.setattr(launcher, '_bind_loopback', denied)
    with pytest.raises(ValueError, match='无法绑定本机端口 8501'):
        launcher.choose_sichuan_port(None)
    assert seen == [8501]


def test_all_default_ports_occupied_bounded_and_explicit_ports_not_replaced(monkeypatch):
    seen = []
    def busy(port):
        seen.append(port)
        raise OSError(errno.EADDRINUSE, 'occupied')
    monkeypatch.setattr(launcher, '_bind_loopback', busy)
    with pytest.raises(ValueError, match='8501–8510'):
        launcher.choose_sichuan_port(None)
    assert seen == list(range(8501,8511))
    seen.clear()
    with pytest.raises(ValueError, match='8504 已被占用'):
        launcher.choose_sichuan_port(8504)
    assert seen == [8504]
    seen.clear()
    with pytest.raises(ValueError, match='1–65535'):
        launcher.choose_sichuan_port(0)
    assert seen == []


def test_view_source_id_is_cross_platform_and_tracks_content(tmp_path):
    view = tmp_path/'app'/'sichuan_v5_dadu_view.py'
    view.parent.mkdir()
    view.write_bytes(b'new view\r\n')
    expected = hashlib.sha256(b'new view\n').hexdigest()[:12]
    assert launcher.view_source_id(tmp_path) == expected
    view.write_bytes(b'new view\n')
    assert launcher.view_source_id(tmp_path) == expected
    view.write_bytes(b'older view\n')
    assert launcher.view_source_id(tmp_path) != expected


def test_check_reports_view_identity_without_a_port_probe(monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'check', lambda *_:[])
    monkeypatch.setattr(launcher, 'python_for', lambda root,python,platform:python)
    def forbidden(*_args, **_kw):
        raise AssertionError('preflight must not bind or launch')
    monkeypatch.setattr(launcher, '_bind_loopback', forbidden)
    monkeypatch.setattr(launcher.subprocess, 'call', forbidden)
    assert launcher.main(['--check']) == 0
    assert '结果视图版本 · ' in capsys.readouterr().out
