"""A fresh folder should diagnose missing package data and broken native runtime."""
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
