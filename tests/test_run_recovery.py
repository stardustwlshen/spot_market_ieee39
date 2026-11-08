"""Recovery never converts unprovable ownership into permission to launch."""
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import psutil
import pytest

from smm import process_control as pc, run_recovery as recovery
from smm.live_runner import atomic_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import common


TEST_BOOT_SESSION = {'version': 1, 'source': 'test-kernel-boot-session', 'sha256': 'a' * 64}


@pytest.fixture(autouse=True)
def simulated_boot_session(monkeypatch):
    """Algorithm premise only, never a claim of native Windows reboot support.

    Actual OS process identities, machine ownership and file locks remain real.
    Native evidence support/failure is tested independently in machine-identity tests.
    """
    real = pc.process_scope
    monkeypatch.setattr(pc, 'process_scope', lambda: {**real(), 'boot_identity': dict(TEST_BOOT_SESSION)})


def record(tmp_path, *, previous_boot=True, status='running'):
    directory = tmp_path / 'runs' / 'scene' / 'interrupted'
    directory.mkdir(parents=True)
    identity = pc.process_identity()
    # Coherent synthetic prior session; this is not a real reboot test.
    if previous_boot:
        session = identity['scope'].get('boot_identity')
        from smm.machine_identity import valid_evidence
        assert valid_evidence(session), 'test requires the explicit simulated-session fixture'
        identity['scope']['boot_time'] -= 100000
        identity['create_time'] = identity['scope']['boot_time'] + 100
        identity['scope']['boot_identity'] = {**session, 'sha256': ('2' if session['sha256'] == '1' * 64 else '1') * 64}
    meta = {'scenario': '默认', 'mode': 'full', 'status': status, 'stage': 'RT',
            'process_identity': identity, 'started': '2026-01-01T00:00:00', 'bid_version': 3,
            'periods': None, 'executed_periods': 3}
    atomic_json(directory / 'meta.json', meta)
    return directory, meta


def recover(directory):
    evidence = recovery.inspect_recovery(directory)
    return recovery.recover_run(directory, directory.parent.parent, expected_revision=evidence['revision'])


def test_explicit_old_boot_recovery_preserves_all_artifacts_and_audit(tmp_path):
    directory, meta = record(tmp_path)
    for name in ('live.json', 'run.log', 'cancel.request', 'submitted_bids.json',
                 'scenario_snapshot/config/fixed_costs.yaml', 'da/prices.csv', 'summary.json'):
        path = directory / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text('original bytes: ' + name)
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert pc.process_state(meta['process_identity']) == 'unknown'
    assert common.run_status({**meta, 'dir': str(directory)}) == 'cancelling'
    evidence = recovery.inspect_recovery(directory)
    assert evidence['eligible'] and evidence['reason'] == 'previous_boot'
    updated = recover(directory)
    assert updated['status'] == 'failed'
    assert updated['recovery']['previous_meta'] == meta
    assert updated['recovery']['cancel_requested']
    assert updated['recovery']['evidence']['proofs'][0]['reason'] == 'previous_boot'
    assert updated['executed_periods'] == 3
    assert not updated['settled'] and not updated['settlement_calculated']
    assert updated['completed_scope'] is None
    assert common.run_status({**updated, 'dir': str(directory)}) == 'failed'
    for path, value in before.items():
        if path.name != 'meta.json':
            assert (directory / path).read_bytes() == value
    after = (directory / 'meta.json').read_bytes()
    assert recovery.recover_run(directory, directory.parent.parent, expected_revision=evidence['revision']) == updated
    assert (directory / 'meta.json').read_bytes() == after


@pytest.mark.parametrize('mutation,reason', [
    ('foreign', 'foreign_machine'), ('legacy', 'legacy_machine_identity'),
    ('missing_current', 'local_machine_unavailable'), ('same_session', 'unproven_boot'),
    ('future_boot', 'inconsistent_boot'), ('created_after_boot', 'inconsistent_boot'),
    ('malformed', 'invalid_identity'), ('foreign_platform', 'foreign_machine'),
    ('foreign_host', 'foreign_machine'),
])
def test_reboot_unknown_cases_cannot_release(tmp_path, monkeypatch, mutation, reason):
    directory, meta = record(tmp_path)
    identity = meta['process_identity']
    scope = identity['scope']
    if mutation == 'foreign':scope['machine_identity']['sha256'] = '2' * 64
    if mutation == 'legacy':scope.pop('machine_identity')
    if mutation == 'missing_current':
        current = pc.process_scope();current['machine_identity'] = None
        monkeypatch.setattr(pc, 'process_scope', lambda: current)
    if mutation == 'same_session':scope['boot_identity'] = pc.process_scope()['boot_identity']
    if mutation == 'future_boot':
        scope['boot_time'] = pc.process_scope()['boot_time'] + 100
        identity['create_time'] = scope['boot_time'] + 5
    if mutation == 'created_after_boot':identity['create_time'] = pc.process_scope()['boot_time'] + 100
    if mutation == 'malformed':identity['pid'] = True
    if mutation == 'foreign_platform':scope['platform'] = 'other-os'
    if mutation == 'foreign_host':scope['host'] += '-foreign'
    atomic_json(directory / 'meta.json', meta)
    before = (directory / 'meta.json').read_bytes()
    evidence = recovery.inspect_recovery(directory)
    assert not evidence['eligible'] and evidence['reason'] == reason
    with pytest.raises(RuntimeError):recover(directory)
    assert (directory / 'meta.json').read_bytes() == before


@pytest.mark.parametrize('state,eligible', [('alive', False), ('denied', False), ('absent', True), ('reused', True)])
def test_current_boot_alive_dead_reuse_and_access_denied(tmp_path, monkeypatch, state, eligible):
    directory, meta = record(tmp_path, previous_boot=False)
    identity = meta['process_identity']
    if state in ('denied', 'absent'):
        def proc(pid):raise (psutil.AccessDenied(pid) if state == 'denied' else psutil.NoSuchProcess(pid))
        monkeypatch.setattr(psutil, 'Process', proc)
    if state == 'reused':
        monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: identity['create_time'] + 1))
    assert recovery.inspect_recovery(directory)['eligible'] is eligible
    monkeypatch.setattr(os, 'kill', lambda *a, **kw: pytest.fail('recovery must never signal a PID'))
    if eligible:assert recover(directory)['status'] == 'failed'
    else:
        with pytest.raises(RuntimeError):recover(directory)


def test_conflicting_new_alive_worker_blocks_old_meta(tmp_path):
    directory, meta = record(tmp_path)
    atomic_json(directory / 'worker.json', pc.process_identity())
    assert recovery.inspect_recovery(directory)['reason'] == 'alive'
    with pytest.raises(RuntimeError):recover(directory)


def test_launcher_only_may_prove_prior_boot_but_not_detached_child_same_boot(tmp_path):
    directory, meta = record(tmp_path)
    meta['launcher_identity'] = meta.pop('process_identity')
    atomic_json(directory / 'meta.json', meta)
    assert recovery.inspect_recovery(directory)['eligible']
    meta['launcher_identity'] = pc.process_identity()
    meta['launcher_identity']['create_time'] -= 1
    atomic_json(directory / 'meta.json', meta)
    assert recovery.inspect_recovery(directory)['reason'] == 'launcher_only'


def test_stale_preview_and_completed_worker_are_never_overwritten(tmp_path):
    directory, meta = record(tmp_path)
    expected = recovery.inspect_recovery(directory)['revision']
    meta['status'] = 'done'
    atomic_json(directory / 'meta.json', meta)
    with pytest.raises(RuntimeError, match='已变化'):
        recovery.recover_run(directory, directory.parent.parent, expected_revision=expected)
    assert json.loads((directory / 'meta.json').read_text())['status'] == 'done'


def test_write_failure_retains_reservation_and_atomic_audit(tmp_path, monkeypatch):
    directory, meta = record(tmp_path)
    before = (directory / 'meta.json').read_bytes()
    monkeypatch.setattr(recovery, 'atomic_json', lambda *a: (_ for _ in ()).throw(PermissionError('read only')))
    with pytest.raises(PermissionError):recover(directory)
    assert (directory / 'meta.json').read_bytes() == before
    assert common.run_status({**meta, 'dir': str(directory)}) == 'running'


def test_shared_launch_and_metadata_locks_exclude_recovery(tmp_path):
    directory, meta = record(tmp_path)
    for lock in (directory.parent.parent / '.launch.lock', directory / '.meta.lock'):
        with pc.file_lock(lock):
            with pytest.raises(RuntimeError, match='正在处理'):recover(directory)
    assert recover(directory)['status'] == 'failed'


def test_recovery_excludes_concurrent_start_then_allows_exactly_one(tmp_path, monkeypatch):
    from smm import scenario
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    directory, meta = record(tmp_path)
    original_write = recovery.atomic_json
    attempts = []
    def write(path, value):
        with pytest.raises(pc.LaunchError, match='正在处理'):
            common.start_run('默认', 'full', 1, 1)
        attempts.append('blocked under recovery launch lock')
        original_write(path, value)
    monkeypatch.setattr(recovery, 'atomic_json', write)
    assert recover(directory)['status'] == 'failed'
    real = common.subprocess.Popen
    launches = []
    def spawn(cmd, **kw):
        if cmd[0] == 'git':return real(cmd, **kw)
        launches.append(cmd)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess, 'Popen', spawn)
    common.start_run('默认', 'full', 1, 1)
    with pytest.raises(pc.LaunchError, match='已有运行'):
        common.start_run('默认', 'full', 1, 1)
    assert len(launches) == len(attempts) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_real_app_reboot_cancel_recovery_and_legacy_guidance(tmp_path, monkeypatch, legacy):
    from smm import scenario
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    directory, meta = record(tmp_path)
    if legacy:meta['process_identity']['scope'].pop('machine_identity')
    atomic_json(directory / 'meta.json', meta)
    (directory / 'run.log').write_text('[DA] SCUC beginning\n')
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    assert not at.exception
    button = lambda label: next(b for b in at.button if b.label == label)
    assert button('▶ 开始运行').disabled
    button('请求取消（安全边界）').click().run()
    assert not at.exception
    assert button('▶ 开始运行').disabled
    assert (directory / 'cancel.request').exists()
    if legacy:
        assert not any(b.label == '确认进程已不存在并恢复' for b in at.button)
        assert any('缺少稳定机器身份' in x.value for x in at.warning)
        assert any('原运行机器' in x.value for x in at.info)
        assert json.loads((directory / 'meta.json').read_text())['status'] == 'running'
    else:
        assert button('确认进程已不存在并恢复').disabled
        next(c for c in at.checkbox if c.label == '确认保留全部历史文件，并将此运行标记为中断').check().run()
        button('确认进程已不存在并恢复').click().run()
        assert not at.exception
        assert not button('▶ 开始运行').disabled
        assert not any(b.label == '请求取消（安全边界）' for b in at.button)
        assert json.loads((directory / 'meta.json').read_text())['status'] == 'failed'


def test_changed_worker_is_rejected_before_recovery(tmp_path):
    directory, meta = record(tmp_path)
    expected = recovery.inspect_recovery(directory)['revision']
    atomic_json(directory / 'worker.json', pc.process_identity())
    with pytest.raises(RuntimeError, match='已变化'):
        recovery.recover_run(directory, directory.parent.parent, expected_revision=expected)


def test_symlink_escape_is_rejected(tmp_path):
    directory, meta = record(tmp_path, previous_boot=False)
    expected = recovery.inspect_recovery(directory)['revision']
    outside = tmp_path / 'elsewhere';outside.mkdir()
    atomic_json(outside / 'meta.json', meta)
    link = directory.parent / 'linked'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows lacks symlink-creation privilege (WinError 1314); no security settings changed; verified separately on Linux')
        raise
    with pytest.raises(ValueError, match='当前项目'):
        recovery.recover_run(link, directory.parent.parent, expected_revision=expected)
    assert json.loads((outside / 'meta.json').read_text()) == meta


def test_real_process_recovery_owns_launch_lock(tmp_path):
    import subprocess
    directory, meta = record(tmp_path)
    ready = tmp_path / 'ready'
    helper = tmp_path / 'recover.py'
    helper.write_text('''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/src')
from smm import run_recovery as r, process_control as pc
import json
real_scope=pc.process_scope
test_boot=json.loads(sys.argv[4])
pc.process_scope=lambda:{**real_scope(),'boot_identity':test_boot}
original=r.atomic_json
def delayed(path,value):
    Path(sys.argv[3]).touch()
    time.sleep(1)
    original(path,value)
r.atomic_json=delayed
d=Path(sys.argv[2]);proof=r.inspect_recovery(d)
r.recover_run(d,d.parent.parent,expected_revision=proof['revision'])
''')
    with subprocess.Popen([sys.executable, str(helper), str(ROOT), str(directory), str(ready), json.dumps(TEST_BOOT_SESSION)]) as child:
        import time
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        with pytest.raises(RuntimeError, match='正在处理'):
            with pc.file_lock(directory.parent.parent / '.launch.lock'):pass
        with pytest.raises(RuntimeError, match='正在处理'):recover(directory)
        assert child.wait(timeout=5) == 0
    assert json.loads((directory / 'meta.json').read_text())['status'] == 'failed'


def test_legacy_same_boot_scope_never_proves_machine_ownership(tmp_path, monkeypatch):
    directory, meta = record(tmp_path, previous_boot=False)
    meta['process_identity']['scope'].pop('machine_identity')
    atomic_json(directory / 'meta.json', meta)
    monkeypatch.setattr(psutil, 'Process', lambda *a: pytest.fail('legacy ownership must not query arbitrary PID'))
    assert pc.process_state(meta['process_identity']) == 'unknown'
    assert recovery.inspect_recovery(directory)['reason'] == 'legacy_machine_identity'



def test_missing_current_boot_evidence_never_releases_old_session(tmp_path, monkeypatch):
    directory, meta = record(tmp_path, previous_boot=False)
    identity=meta['process_identity']
    identity['scope']['boot_time']-=100000
    identity['create_time']=identity['scope']['boot_time']+100
    identity['scope']['boot_identity']={'version':1,'source':'test-prior-boot','sha256':'1'*64}
    atomic_json(directory/'meta.json',meta)
    current=copy.deepcopy(pc.process_scope());current['boot_identity']=None
    monkeypatch.setattr(pc,'process_scope',lambda:current)
    monkeypatch.setattr(pc,'process_state',lambda _:pytest.fail('Unverified prior boot must not inspect a reused PID'))
    before=(directory/'meta.json').read_bytes()
    proof=recovery.inspect_recovery(directory)
    assert not proof['eligible'] and proof['reason']=='unproven_boot'
    with pytest.raises(RuntimeError,match='缺少可验证'):
        recovery.recover_run(directory,directory.parent.parent,expected_revision=proof['revision'])
    assert (directory/'meta.json').read_bytes()==before


def test_old_archive_without_boot_session_is_not_upgraded(tmp_path):
    directory,meta=record(tmp_path,previous_boot=False)
    identity=meta['process_identity']
    identity['scope']['boot_time']-=100000
    identity['create_time']=identity['scope']['boot_time']+100
    identity['scope'].pop('boot_identity',None)
    atomic_json(directory/'meta.json',meta)
    before=(directory/'meta.json').read_bytes()
    proof=recovery.inspect_recovery(directory)
    assert not proof['eligible'] and proof['reason']=='unproven_boot'
    with pytest.raises(RuntimeError):
        recovery.recover_run(directory,directory.parent.parent,expected_revision=proof['revision'])
    assert (directory/'meta.json').read_bytes()==before
