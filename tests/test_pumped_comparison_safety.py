"""Exercise comparison orchestration without market solves or shared data writes."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('safe_pumped_comparison', ROOT / 'scripts/compare_pumped.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = SimpleNamespace(root=tmp_path, T=96, rule_set='MKT',
        base={'solver': {}, 'date': '2026-07-15', 'season': 'wet'}, units={'pumped': [{'id': 'PS1'}]})
    state = {'loaded': 0, 'prepared': [], 'solved': []}
    def load():
        state['loaded'] += 1
        return cfg
    def prepare(config, version, *, read_only):
        assert read_only is True
        state['prepared'].append(config)
        return SimpleNamespace(bids={'version': version or 1})
    def freeze(config, case, output):
        output.mkdir(exist_ok=False)
        (output / 'frozen.json').write_text(json.dumps(config.units))
    def run(config, case, **kwargs):
        assert kwargs['retain_id_models'] is False
        state['solved'].append(config)
        return {'enabled': bool(config.units['pumped'])}
    def write(result, directory, **kwargs):
        assert (directory / 'frozen.json').is_file()
        summary = dict(time_s={'total': 1}, checks={}, enabled=result['enabled'])
        (directory / 'summary.json').write_text(json.dumps(summary))
        return summary
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'load_config', load)
    monkeypatch.setattr(module, 'prepare', prepare)
    monkeypatch.setattr(module, 'freeze_cli_inputs', freeze)
    monkeypatch.setattr(module, 'run_full', run)
    monkeypatch.setattr(module, 'write_full_outputs', write)
    monkeypatch.setattr(module, 'metrics', lambda summary: {'value': int(summary['enabled'])})
    return module, cfg, state


def test_repeated_runs_preserve_all_prior_files_and_loaded_config(comparison):
    module, cfg, state = comparison
    for name in ('data/profiles/user.csv', 'bids/bids_v1.json', 'outputs/pumped_compare.csv',
                 'outputs/full_old_ps_on/summary.json'):
        path = cfg.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'preserve existing user data\n')
    before = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    module.main(['--no-plots'])
    first = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    module.main(['--no-plots'])
    directories = list((cfg.root / 'outputs/pumped_comparisons').iterdir())
    assert len(directories) == 2
    for directory in directories:
        report = json.loads((directory / 'pumped_compare.json').read_text())
        assert report['ps_off']['metrics'] == {'value': 0}
        assert report['ps_on']['metrics'] == {'value': 1}
    assert all(p.read_bytes() == value for p, value in (before | first).items())
    assert state['loaded'] == 2 and len(state['solved']) == 4
    assert cfg.units == {'pumped': [{'id': 'PS1'}]} and cfg.base == {'solver': {}, 'date': '2026-07-15', 'season': 'wet'}


def test_existing_output_fails_before_preparation_or_solve(comparison):
    module, cfg, state = comparison
    output = cfg.root / 'existing'
    output.mkdir()
    (output / 'result').write_bytes(b'old')
    with pytest.raises(FileExistsError):
        module.main(['--output', str(output)])
    assert not state['prepared'] and not state['solved']
    assert (output / 'result').read_bytes() == b'old'


@pytest.mark.parametrize('args', [['--periods', '0'], ['--periods', '97'],
    ['--time-limit', 'nan'], ['--time-limit', '-1'], ['--bid-version', '0'], ['--reuse']])
def test_invalid_arguments_create_no_files(comparison, args):
    module, cfg, state = comparison
    with pytest.raises(SystemExit) as error:
        module.main(args)
    assert error.value.code == 2
    assert not list(cfg.root.rglob('*')) and not state['solved']


def test_failed_solve_preserves_frozen_inputs_and_does_not_reuse(comparison, monkeypatch):
    module, cfg, state = comparison
    output = cfg.root / 'failed'
    def fail(*args, **kwargs):
        raise RuntimeError('injected failure')
    monkeypatch.setattr(module, 'run_full', fail)
    with pytest.raises(RuntimeError, match='injected'):
        module.main(['--output', str(output)])
    frozen = output / 'ps_off/frozen.json'
    assert frozen.read_text() == '{"pumped": []}'
    with pytest.raises(FileExistsError):
        module.main(['--output', str(output)])
    assert frozen.read_text() == '{"pumped": []}'
    assert not (output / 'pumped_compare.json').exists()


@pytest.mark.parametrize("stage", ["prepare", "first_solve", "last_solve"])
def test_changed_inputs_abort_without_reverting_external_edit(comparison, monkeypatch, stage):
    module, cfg, state = comparison
    path = cfg.root / "bids/user.json"
    path.parent.mkdir()
    path.write_text("original")
    if stage == "prepare":
        original = module.prepare
        def prepare(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_text("external edit")
            return result
        monkeypatch.setattr(module, "prepare", prepare)
    else:
        original = module.run_full
        def run(*args, **kwargs):
            result = original(*args, **kwargs)
            if stage == "first_solve" or len(state['solved']) == 2:
                path.write_text("external edit")
            return result
        monkeypatch.setattr(module, "run_full", run)
    output = cfg.root / "comparison"
    with pytest.raises(RuntimeError, match="已有证据保留"):
        module.main(["--output", str(output)])
    assert path.read_text() == "external edit"
    assert not (output / "pumped_compare.json").exists()
    assert len(state['solved']) == {"prepare": 0, "first_solve": 1, "last_solve": 2}[stage]
    assert (output / "ps_off/frozen.json").exists()


def test_default_runs_are_catalogued_without_touching_prior_results(comparison, monkeypatch):
    module, cfg, state = comparison
    monkeypatch.syspath_prepend(str(ROOT / 'app'))
    import common
    monkeypatch.setattr(common, 'ROOT', cfg.root)
    monkeypatch.setattr(common.sc, 'list_runs', lambda: [])
    legacy = cfg.root / 'outputs/full_2026-07-15_MKT_legacy'
    legacy.mkdir(parents=True)
    (legacy / 'summary.json').write_bytes(b'{"historical": true}')
    old = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    module.main(['--no-plots'])
    report = json.loads(next((cfg.root / 'outputs/pumped_comparisons').glob('*/pumped_compare.json')).read_text())
    stages = {Path(report[tag]['dir']) for tag in ('ps_off', 'ps_on')}
    assert all(path.parent == cfg.root / 'outputs' for path in stages)
    assert all(path.name.startswith('full_2026-07-15_MKT_wet_v1_comparison_') for path in stages)
    before_catalog = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    assert {Path(record['dir']) for record in common.all_results()} == stages | {legacy}
    assert all(p.read_bytes() == value for p, value in (old | before_catalog).items())


def test_explicit_output_remains_nested_and_outside_default_catalog(comparison, monkeypatch):
    module, cfg, state = comparison
    monkeypatch.syspath_prepend(str(ROOT / 'app'))
    import common
    monkeypatch.setattr(common, 'ROOT', cfg.root)
    monkeypatch.setattr(common.sc, 'list_runs', lambda: [])
    output = cfg.root / 'explicit_research'
    module.main(['--output', str(output), '--no-plots'])
    report = json.loads((output / 'pumped_compare.json').read_text())
    assert all(Path(report[tag]['dir']) == output / tag for tag in ('ps_off', 'ps_on'))
    assert common.all_results() == []
