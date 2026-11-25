"""A fresh folder should diagnose missing package data and broken native runtime."""
from pathlib import Path
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
    monkeypatch.setattr(launch,'check_port',lambda port:probes.append(port))
    assert launch.main([])==1
    first=capsys.readouterr()
    assert 'http://' not in first.out
    assert 'Port 8501 is not available' in first.err
    assert launch.main(['--profile','guangdong'])==1
    assert probes==[8501]
    assert [Path(cmd[4]).name for cmd in calls]==['四川.py','主页.py']
