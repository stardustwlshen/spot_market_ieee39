"""Developer benchmark parses all requests before work and never writes inputs."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_benchmark():
    spec = importlib.util.spec_from_file_location('scuc_benchmark_entry', ROOT / 'scripts/bench_scuc.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_does_not_prepare_or_solve(monkeypatch):
    from smm import config, pipeline, scuc
    def unexpected(*args, **kwargs):
        pytest.fail('Import must not prepare inputs or solve')
    monkeypatch.setattr(config, 'load_config', unexpected)
    monkeypatch.setattr(pipeline, 'prepare', unexpected)
    monkeypatch.setattr(scuc, 'build_model', unexpected)
    load_benchmark()


@pytest.mark.parametrize('args', [[], ['base', 'unknown'], ['--help']])
def test_invalid_request_or_help_does_no_work(args, monkeypatch):
    benchmark = load_benchmark()
    monkeypatch.setattr(benchmark, 'load_config', lambda: pytest.fail('Must validate before work'))
    with pytest.raises(SystemExit) as error:
        benchmark.main(args)
    assert error.value.code == (0 if args == ['--help'] else 2)


def test_benchmark_prepares_read_only_inputs(monkeypatch):
    benchmark = load_benchmark()
    captured = {}
    sentinel = object()
    def prepare(cfg, **kwargs):
        captured.update(kwargs)
        captured['storage_binary'] = cfg.base['solver'].get('storage_binary')
        return sentinel
    class StopBeforeSolve(Exception):
        pass
    def build(case):
        assert case is sentinel
        raise StopBeforeSolve
    monkeypatch.setattr(benchmark, 'prepare', prepare)
    monkeypatch.setattr(benchmark.scuc, 'build_model', build)
    with pytest.raises(StopBeforeSolve):
        benchmark.main(['relaxed'])
    assert captured == {'bid_version': 1, 'verbose': False, 'read_only': True, 'storage_binary': False}
