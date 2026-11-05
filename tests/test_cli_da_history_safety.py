"""CLI regression tests isolate outputs and replace the expensive solver."""
from pathlib import Path
import runpy
import shutil
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    from smm import config, da, pipeline
    cfg = config.load_config()
    cfg.root = tmp_path
    shutil.copytree(ROOT / 'data/case39', tmp_path / 'data/case39')
    case = SimpleNamespace(bids={'version': 1, 'units': {}},
                           profiles=pd.DataFrame({'period': [0], 'load_da': [1.]}))
    events = []
    monkeypatch.setattr(config, 'load_config', lambda **kw: cfg)
    def prepare(*args, **kwargs):
        assert kwargs['read_only'] is True
        events.append('prepare')
        return case
    monkeypatch.setattr(pipeline, 'prepare', prepare)
    monkeypatch.setattr(da, 'run_da', lambda case: events.append('solve') or object())
    monkeypatch.setattr(pipeline, 'write_outputs', lambda result, output, **kw: events.append('write') or {})
    monkeypatch.setattr(pipeline, 'print_summary', lambda summary: None)
    def run(*args):
        monkeypatch.setattr(sys, 'argv', ['main_da.py', *args])
        return runpy.run_path(str(ROOT / 'scripts/main_da.py'), run_name='__main__')
    return cfg, events, run


def test_existing_da_output_refused_before_solver_and_bytes_preserved(cli):
    cfg, events, run = cli
    out = cfg.root / 'outputs' / f"da_{cfg.base['date']}_{cfg.rule_set}_{cfg.base['season']}_v1"
    out.mkdir(parents=True)
    (out / 'summary.json').write_bytes(b'old result')
    with pytest.raises(FileExistsError, match='--tag'):
        run()
    assert events == ['prepare']
    assert (out / 'summary.json').read_bytes() == b'old result'
    assert sorted(p.name for p in out.iterdir()) == ['summary.json']


def test_new_tag_freezes_effective_time_limit_before_solver(cli, monkeypatch):
    cfg, events, run = cli
    from smm import da
    def solve(case):
        snapshot = next((cfg.root / 'outputs').glob('*_新检查/scenario_snapshot'))
        base = yaml.safe_load((snapshot / 'config/base.yaml').read_text())
        assert base['solver']['time_limit_s'] == 12.
        assert (snapshot / 'resolved_bids.json').is_file()
        events.append('solve')
        return object()
    monkeypatch.setattr(da, 'run_da', solve)
    run('--tag', '_新检查', '--time-limit', '12', '--no-plots')
    assert events == ['prepare', 'solve', 'write']
    assert not (cfg.root / 'bids').exists()
    assert not (cfg.root / 'data/profiles').exists()


@pytest.mark.parametrize('args', [('--time-limit','0'),('--time-limit','-1'),
                                   ('--time-limit','nan'),('--time-limit','inf'),
                                   ('--tag','/../../old'),('--tag','\\old')])
def test_bad_options_fail_before_input_preparation(cli, args):
    cfg, events, run = cli
    with pytest.raises(SystemExit) as exc:
        run(*args)
    assert exc.value.code == 2
    assert events == []
    assert not (cfg.root / 'outputs').exists()


def test_solve_failure_keeps_frozen_inputs_and_does_not_overwrite_on_retry(cli, monkeypatch):
    cfg, events, run = cli
    from smm import da
    def fail(case):
        raise RuntimeError('injected solver error')
    monkeypatch.setattr(da, 'run_da', fail)
    with pytest.raises(RuntimeError, match='injected'):
        run('--tag', '_failed')
    snapshot = next((cfg.root / 'outputs').glob('*_failed/scenario_snapshot'))
    before = {p.relative_to(snapshot): p.read_bytes() for p in snapshot.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        run('--tag', '_failed')
    assert before == {p.relative_to(snapshot): p.read_bytes() for p in snapshot.rglob('*') if p.is_file()}
