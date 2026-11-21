"""The user-visible completion badge and launch gate require indexed evidence."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from gd_full_day_ui import day_exit_kind, progress
from gd_saved_tasks import discover


def saved_day(tmp_path, windows=96):
    output = tmp_path / 'demo' / 'live_market_sessions' / 'attempt'
    output.mkdir(parents=True)
    branch = output / 'pipeline' / 'dispatch'
    end = {'status': 'complete_research'}
    status = dict(
        exit=end, completed_windows=windows,
        quarters=[{'quarter': i} for i in range(windows)],
        hours=[{'hour': i, 'run_id': 'demo-id', 'branch': str(branch)} for i in range(windows//4)],
        hour_exports=[{'path': str(branch.parent / 'price' / f'HOUR{i}.json')} for i in range(windows//4)],
    )
    (output / 'EXIT.json').write_text(json.dumps(end))
    (output / 'PLAN.json').write_text(json.dumps(dict(
        output=str(output), run=str(output.parent.parent / 'rt'),
        da=str(output.parent.parent / 'da'), windows=windows,
    )))
    return status


class Control:
    def __init__(self, status):
        self.record = status

    def status(self, output):
        return self.record


def test_full_day_view_and_saved_launch_gate_agree(tmp_path):
    status = saved_day(tmp_path)
    assert day_exit_kind(status) == 'complete'
    assert progress(status)['complete']
    assert progress(status)['end_time'] == '24:00'
    assert not progress(status)['failed']
    found = discover(tmp_path, Control(status))
    assert found['tasks'][0]['state'] == 'terminal_saved'
    assert not found['block_new_start']


@pytest.mark.parametrize('corruption', [
    lambda s: s['quarters'].__setitem__(-1, s['quarters'][0]),
    lambda s: s['quarters'][0].update(quarter=False),
    lambda s: s['hours'].__setitem__(-1, s['hours'][0]),
    lambda s: s['hours'][-1].update(run_id='another-run'),
    lambda s: s['hours'][-1].update(branch='/another-run/pipeline/dispatch'),
    lambda s: s['hour_exports'].__setitem__(-1, s['hour_exports'][0]),
    lambda s: s['hour_exports'].pop(),
    lambda s: s.update(completed_windows='96'),
])
def test_inconsistent_day_remains_unconfirmed_despite_successful_exit(tmp_path, corruption):
    status = saved_day(tmp_path)
    corruption(status)
    assert day_exit_kind(status) == 'unknown'
    view = progress(status)
    assert not view['complete']
    assert '不能标记完成' in view['message']
    found = discover(tmp_path, Control(status))
    assert found['tasks'][0]['state'] == 'running_or_unknown'
    assert found['block_new_start']


def test_verified_short_view_is_not_a_day_or_failure(tmp_path):
    status = saved_day(tmp_path, 4)
    verified = progress(status, first_hour_saved=True)
    assert not verified['complete']
    assert not verified['failed']
    assert verified['first_hour_complete']
    assert verified['end_time'] == '01:00'
    assert not progress(status)['first_hour_complete']
    assert day_exit_kind(status) == 'unknown'
    assert discover(tmp_path, Control(status))['block_new_start']


def test_proven_zero_launch_is_only_explicit_failure_to_unblock():
    assert day_exit_kind({'exit': {'status': 'launch_failed', 'native_calls': 0}}) == 'launch_failed'
    assert day_exit_kind({'exit': {'status': 'launch_failed', 'native_calls': False}}) == 'unknown'
    assert day_exit_kind({'exit': {'status': 'launch_unknown', 'native_calls': None}}) == 'unknown'


@pytest.mark.parametrize('relative', [True, False])
@pytest.mark.parametrize('corrupt', [True, False])
def test_base_initializer_checks_source_seed_not_cwd(tmp_path, monkeypatch, relative, corrupt):
    import hashlib
    from gd_provincial15_model import base_initializer as base

    source = tmp_path / 'scenario'
    seed = source / 'seed' / 'case.json'
    seed.parent.mkdir(parents=True)
    seed.write_bytes(b'original case')
    key = 'seed/case.json' if relative else str(seed)
    context = {'seed_pins': {key: hashlib.sha256(seed.read_bytes()).hexdigest()}}
    (source / 'SHORT_CONTEXT.json').write_text(json.dumps(context))
    foreign_cwd = tmp_path / 'elsewhere'
    (foreign_cwd / 'seed').mkdir(parents=True)
    # A matching file in the launch directory must neither replace nor rescue the source.
    (foreign_cwd / 'seed' / 'case.json').write_bytes(b'original case' if corrupt else b'wrong case')
    monkeypatch.chdir(foreign_cwd)
    monkeypatch.setattr(base, 'verify', lambda *args, **kwargs: {})
    manifest = tmp_path / 'da' / 'MANIFEST.json'
    manifest.parent.mkdir()
    manifest.write_text('{}')

    class ReachedSeedCopy(Exception):
        pass

    def stop_after_pin_check(from_dir, to_dir):
        assert from_dir == source / 'seed'
        assert to_dir == tmp_path / 'new_base' / 'seed'
        raise ReachedSeedCopy

    monkeypatch.setattr(base.shutil, 'copytree', stop_after_pin_check)
    if corrupt:
        seed.write_bytes(b'tampered case')
        with pytest.raises(ValueError, match='scenario seed source changed'):
            base.initialize(source_base=source, da_manifest=manifest, output=tmp_path/'new_base', program=None, research=True)
        assert not (tmp_path/'new_base').exists()
    else:
        with pytest.raises(ReachedSeedCopy):
            base.initialize(source_base=source, da_manifest=manifest, output=tmp_path/'new_base', program=None, research=True)
