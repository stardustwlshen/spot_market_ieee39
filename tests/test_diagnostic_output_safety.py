"""Diagnostic preflight is read-only and precedes expensive research work."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location('diagnostic_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name', ['fd_value_sign', 'audit_causal_v3'])
def test_import_does_not_load_config(name, monkeypatch):
    import smm.config
    monkeypatch.setattr(smm.config, 'load_config', lambda *a, **k: pytest.fail('import must not calculate'))
    load_script(name)


@pytest.mark.parametrize('filename', ['fd_sign_wet.json', 'fd_snap_wet.json'])
def test_either_existing_fd_output_prevents_all_work(filename, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'outputs' / filename
    path.parent.mkdir()
    path.write_bytes(b'old evidence')
    module = load_script('fd_value_sign')
    monkeypatch.setattr(module, 'load_config', lambda *a, **k: pytest.fail('must preflight first'))
    with pytest.raises(FileExistsError):
        module.main([])
    assert path.read_bytes() == b'old evidence'
    assert list(path.parent.iterdir()) == [path]


def test_existing_statistics_prevents_generation(tmp_path, monkeypatch):
    path = tmp_path / 'statistics.json'
    path.write_bytes(b'old evidence')
    module = load_script('audit_causal_v3')
    monkeypatch.setattr(module, 'load_config', lambda: pytest.fail('must preflight first'))
    with pytest.raises(FileExistsError):
        module.main([str(path)])
    assert path.read_bytes() == b'old evidence'


def test_missing_statistics_parent_prevents_generation(tmp_path, monkeypatch):
    module = load_script('audit_causal_v3')
    monkeypatch.setattr(module, 'load_config', lambda: pytest.fail('must preflight first'))
    with pytest.raises(FileNotFoundError):
        module.main([str(tmp_path / 'absent/result.json')])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('args', [['../escape'], ['wet', 'bad'], ['dry', '-1'], ['wet', '95']])
def test_fd_invalid_inputs_do_not_solve_or_create_outputs(args, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = load_script('fd_value_sign')
    monkeypatch.setattr(module, 'prepare', lambda *a, **k: pytest.fail('invalid input must not prepare'))
    with pytest.raises(SystemExit):
        module.main(args)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name,count', [('fd_value_sign', 2), ('audit_causal_v3', 1)])
def test_all_result_writes_use_exclusive_creation(name, count):
    tree = ast.parse((ROOT / 'scripts' / (name + '.py')).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert not any(n.func.attr == 'write_text' for n in calls)
    writes = [n for n in calls if n.func.attr == 'open']
    assert len(writes) == count
    assert all(n.args[0].value == 'x' for n in writes)


@pytest.mark.parametrize('name', ['fd_value_sign', 'audit_causal_v3'])
def test_output_created_after_preflight_is_not_overwritten(name, tmp_path):
    # Execute the actual final write blocks in isolation, avoiding expensive models.
    tree = ast.parse((ROOT / 'scripts' / (name + '.py')).read_text())
    writes = [node for node in ast.walk(tree) if isinstance(node, ast.With)]
    assert writes
    for index, node in enumerate(writes):
        path = tmp_path / f'raced_{index}.json'
        path.write_bytes(b'external result appeared during calculation')
        namespace = {'out': path, 'snapshot_output': path, 'output': path}
        with pytest.raises(FileExistsError):
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-write-block>', 'exec'), namespace)
        assert path.read_bytes() == b'external result appeared during calculation'
