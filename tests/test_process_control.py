"""Native Linux coverage plus explicit mocked macOS/Windows branches, not native OS certification."""
import builtins
import copy
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
from smm import process_control as pc
from smm.live_runner import atomic_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import common


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from smm import scenario
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    return scenario


def test_native_identity_and_read_only_liveness(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A status check must never send a signal')
    monkeypatch.setattr(os, 'kill', forbidden)
    identity = pc.process_identity()
    assert pc.process_state(identity) == 'alive'
    stale = {**identity, 'create_time': identity['create_time'] - 1}
    assert pc.process_state(stale) == 'dead'
    foreign = copy.deepcopy(identity);foreign['scope']['host'] += '-foreign'
    assert pc.process_state(foreign) == 'unknown'
    for value in [None, {}, {'pid': True}, {'pid': -1}, {**identity, 'create_time': float('nan')}]:
        assert pc.process_state(value) == 'unknown'


def test_native_child_remains_alive_after_repeated_checks_and_exit_is_detected():
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)']) as child:
        identity = pc.process_identity(child.pid)
        for _ in range(5):
            assert pc.process_state(identity) == 'alive'
            assert child.poll() is None
        child.wait(timeout=5)
        assert pc.process_state(identity) == 'dead'


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_nonlinux_scope_never_reads_procfs(monkeypatch, platform):
    monkeypatch.setattr(pc, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(pc.os, 'readlink', lambda *a: pytest.fail('non-Linux scope read procfs'))
    assert pc.process_scope()['pid_namespace'] is None
    assert pc.process_state(pc.process_identity()) == 'alive'


def test_linux_without_pid_namespace_symlink_is_supported(monkeypatch):
    def missing(*a): raise FileNotFoundError('/proc/self/ns/pid')
    monkeypatch.setattr(pc.os, 'readlink', missing)
    assert pc.process_scope()['pid_namespace'] is None
    assert pc.process_state(pc.process_identity()) == 'alive'


def test_access_denied_is_unknown_and_zombie_is_dead(monkeypatch):
    identity = pc.process_identity()
    monkeypatch.setattr(pc, 'process_scope', lambda: identity['scope'])
    def denied(pid):raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, 'Process', denied)
    assert pc.process_state(identity) == 'unknown'
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(
        create_time=lambda:identity['create_time'], is_running=lambda:True, status=lambda:psutil.STATUS_ZOMBIE))
    assert pc.process_state(identity) == 'dead'


def test_native_lock_contention_and_exception_release(tmp_path):
    p = tmp_path / 'launch.lock'
    with pc.file_lock(p):
        with pytest.raises(RuntimeError, match='正在处理'):
            with pc.file_lock(p):pass
    with pytest.raises(ValueError):
        with pc.file_lock(p):raise ValueError('test')
    with pc.file_lock(p):pass
    assert p.exists()


def test_windows_lock_byte_zero_unlock_and_no_fcntl(tmp_path, monkeypatch):
    events = []
    fake = SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3)
    def locking(fd, mode, size):
        events.append((os.lseek(fd, 0, os.SEEK_CUR), mode, size))
    fake.locking = locking
    monkeypatch.setitem(sys.modules, 'msvcrt', fake)
    monkeypatch.setattr(pc, 'os', SimpleNamespace(name='nt'))
    original = builtins.__import__
    def guarded(name, *a, **kw):
        if name == 'fcntl':pytest.fail('Windows imported fcntl')
        return original(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', guarded)
    path = tmp_path / '跨平台 lock'
    path.write_bytes(b'previous bytes')
    with pytest.raises(ValueError):
        with pc.file_lock(path):raise ValueError('test')
    assert events == [(0, fake.LK_NBLCK, 1), (0, fake.LK_UNLCK, 1)]
    assert path.read_bytes() == b'previous bytes'


def _fake_spawn(monkeypatch, after=None):
    real = common.subprocess.Popen
    def fake(cmd, **kw):
        if cmd[0] == 'git':return real(cmd, **kw)
        if after:after(cmd, kw)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess, 'Popen', fake)


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_launch_without_procfs_and_status_retains_exclusion(isolated, monkeypatch, platform):
    monkeypatch.setattr(pc, 'sys', SimpleNamespace(platform=platform))
    real = os.readlink
    def guard(path, *a, **kw):
        if str(path).startswith('/proc/'):raise AssertionError('unexpected proc access')
        return real(path, *a, **kw)
    monkeypatch.setattr(os, 'readlink', guard)
    _fake_spawn(monkeypatch)
    d = common.start_run(isolated.DEFAULT, 'full', 1, 1)
    assert common.run_status({**json.loads((d/'meta.json').read_text()), 'dir':str(d)}) == 'starting'
    with pytest.raises(pc.LaunchError, match='已有运行'):
        common.start_run(isolated.DEFAULT, 'full', 1, 1)


def test_pre_spawn_failure_is_recorded_and_can_retry(isolated, monkeypatch):
    from smm import live_runner
    real = live_runner.freeze_inputs
    monkeypatch.setattr(live_runner, 'freeze_inputs', lambda *a, **kw: (_ for _ in ()).throw(ValueError('invalid offer')))
    with pytest.raises(pc.LaunchError) as error:
        common.start_run(isolated.DEFAULT, 'full', 1, 1)
    assert not error.value.process_started
    d = error.value.directory
    assert json.loads((d/'meta.json').read_text())['status'] == 'failed'
    assert 'ValueError: invalid offer' in (d/'launch_error.log').read_text()
    monkeypatch.setattr(live_runner, 'freeze_inputs', real)
    _fake_spawn(monkeypatch)
    assert common.start_run(isolated.DEFAULT, 'full', 1, 1) != d


def test_post_spawn_record_failure_never_marks_worker_failed(isolated, monkeypatch):
    real = Path.write_text
    def fail_pid(self, *a, **kw):
        if self.name == 'worker.pid':raise PermissionError('pid write denied')
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, 'write_text', fail_pid)
    _fake_spawn(monkeypatch)
    with pytest.raises(pc.LaunchError) as error:
        common.start_run(isolated.DEFAULT, 'full', 1, 1)
    assert error.value.process_started
    d = error.value.directory
    assert json.loads((d/'meta.json').read_text())['status'] == 'starting'
    assert (d/'launch_warning.json').exists()
    with pytest.raises(pc.LaunchError, match='已有运行'):
        common.start_run(isolated.DEFAULT, 'full', 1, 1)


def test_parent_does_not_overwrite_fast_worker_terminal_meta(isolated, monkeypatch):
    def finish(cmd, kw):
        d = Path(cmd[cmd.index('--run-dir')+1])
        m = json.loads((d/'meta.json').read_text());m.update(status='done', stage='worker finished')
        atomic_json(d/'meta.json', m)
        assert kw['env']['PYTHONIOENCODING'] == 'utf-8'
    _fake_spawn(monkeypatch, finish)
    d = common.start_run(isolated.DEFAULT, 'full', 1, 1)
    assert json.loads((d/'meta.json').read_text())['status'] == 'done'


@pytest.mark.parametrize('status', ['running', 'starting', 'cancelling', 'done', 'failed'])
def test_legacy_or_terminal_records_are_not_rewritten(tmp_path, status):
    m = {'status':status, 'pid':os.getpid(), 'pid_namespace':'legacy-unverified', 'dir':str(tmp_path)}
    atomic_json(tmp_path/'meta.json', m)
    (tmp_path/'worker.pid').write_text('malformed PID')
    before = (tmp_path/'meta.json').read_bytes()
    assert common.run_status(m) == status
    assert (tmp_path/'meta.json').read_bytes() == before


def test_dead_identity_releases_run_but_foreign_or_incomplete_identity_does_not(tmp_path):
    identity=pc.process_identity();identity['create_time']-=1
    m={'status':'running','process_identity':identity,'dir':str(tmp_path)}
    atomic_json(tmp_path/'meta.json',m)
    assert common.run_status(m)=='failed'
    m['process_identity']['scope']['host']+='-foreign'
    atomic_json(tmp_path/'meta.json',m)
    assert common.run_status(m)=='running'


def test_two_real_launcher_processes_create_exactly_one_worker(isolated, tmp_path):
    helper = tmp_path / 'launcher.py'
    helper.write_text('''import sys, json, os
from pathlib import Path
sys.path[:0] = [sys.argv[1]+'/src', sys.argv[1]+'/app']
import common
from smm import scenario
scenario.SCEN_DIR=Path(sys.argv[2]);scenario.RUNS_DIR=Path(sys.argv[3])
original=common.subprocess.Popen
def spawn(cmd, **kw):
    if cmd[0]=='git':return original(cmd, **kw)
    return original([sys.executable,'-c','import time;time.sleep(3)'], **kw)
common.subprocess.Popen=spawn
try:
    d=common.start_run('默认','full',1,1)
    print(json.dumps({'launched':str(d)}))
except Exception as exc:
    print(json.dumps({'blocked':str(exc)}))
''', encoding='utf-8')
    args=[sys.executable,str(helper),str(ROOT),str(isolated.SCEN_DIR),str(isolated.RUNS_DIR)]
    children=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    results=[json.loads(c.communicate(timeout=20)[0].strip().splitlines()[-1]) for c in children]
    assert sum('launched' in r for r in results)==1,results
    assert sum('blocked' in r for r in results)==1,results
    assert len(list(isolated.RUNS_DIR.rglob('worker.json')))==1
    meta=isolated.list_runs()[0]
    deadline=time.monotonic()+8
    while common.run_status(meta)!='failed' and time.monotonic()<deadline:
        time.sleep(.1)
    assert common.run_status(meta)=='failed'


def test_launch_ui_distinguishes_post_spawn_uncertainty(monkeypatch):
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_string('''from input_workflow import launch_error
from smm.process_control import LaunchError
from pathlib import Path
launch_error(LaunchError('记录已启动进程', PermissionError('denied'), directory=Path('/example/run'), process_started=True))
''').run()
    assert not at.exception
    assert '后台进程已创建' in at.error[0].value
    assert '未启动新的有效运行' not in at.error[0].value
    assert at.expander[0].proto.expanded


def test_dead_status_write_failure_preserves_reservation(tmp_path, monkeypatch):
    from smm import live_runner
    identity=pc.process_identity();identity['create_time']-=1
    m={'status':'running','process_identity':identity,'dir':str(tmp_path)}
    atomic_json(tmp_path/'meta.json',m)
    monkeypatch.setattr(live_runner,'atomic_json',lambda *a,**kw:(_ for _ in ()).throw(PermissionError('read-only')))
    assert common.run_status(m)=='running'
    assert json.loads((tmp_path/'meta.json').read_text())['status']=='running'


def test_missing_process_dependency_fails_before_launch_with_actionable_message(isolated, monkeypatch):
    monkeypatch.setattr(pc,'_psutil',lambda:(_ for _ in ()).throw(RuntimeError('缺少进程检查依赖 psutil，请安装 requirements.txt')))
    with pytest.raises(pc.LaunchError,match='psutil') as error:
        common.start_run(isolated.DEFAULT,'full',1,1)
    assert error.value.stage=='检查启动环境'
    assert error.value.directory is None and not error.value.process_started
    assert isolated.list_runs()==[]


def test_windows_spawn_omits_posix_session_flag(isolated, monkeypatch):
    captured=[]
    monkeypatch.setattr(common,'os',SimpleNamespace(name='nt',environ=os.environ))
    _fake_spawn(monkeypatch,lambda cmd,kw:captured.append(kw))
    common.start_run(isolated.DEFAULT,'full',1,1)
    assert 'start_new_session' not in captured[0]
    assert captured[0]['env']['PYTHONUTF8']=='1'


@pytest.mark.parametrize('mode',['no_procfs','darwin_branch','dependency_failure'])
def test_cli_worker_native_process_initialization_and_tiny_da(tmp_path,mode):
    # A real separate Python worker executes the production CLI. Only the case
    # preparation uses the existing 2-bus hydro fixture; optimization is real.
    wrapper=tmp_path/'worker.py';out=tmp_path/'中文 路径'/'run'
    wrapper.write_text('''import sys, os, runpy, copy
from pathlib import Path
from types import SimpleNamespace
root=Path(sys.argv[1]);out=Path(sys.argv[2]);testmode=sys.argv[3]
sys.path[:0]=[str(root/'src'),str(root/'tests')]
import yaml
from smm import scenario,pipeline,process_control
from test_hydro_rolling import hydro_case
case=hydro_case(T=2);case.bids['version']=1
scenario.SCEN_DIR=out.parent/'scenarios'
scene=scenario.scenario_path('测试场景');(scene/'config').mkdir(parents=True)
for name,data in [('base',case.cfg.base),('units',case.cfg.units),('modes',{case.cfg.rule_set:case.cfg.mode})]:
    (scene/'config'/f'{name}.yaml').write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
# Explicit synthetic cost records and the actual tiny-case offers must be frozen.
# Do not let input preparation silently generate different bids or skip validation.
from smm.research_defaults import generate_research_defaults
from smm.bids import save_bids
cost=generate_research_defaults(case.cfg)
for name,data in [('fixed_costs',cost['profile']),('bid_cost_basis',cost['worksheet'])]:
    (scene/'config'/f'{name}.yaml').write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
fixture_cfg=copy.deepcopy(case.cfg);fixture_cfg.root=scene
save_bids(fixture_cfg,case.bids,new_version=False)
def prepare(cfg,*a,**kw):case.cfg=cfg;return case
pipeline.prepare=prepare
if testmode=='darwin_branch':process_control.sys=SimpleNamespace(platform='darwin')
if testmode=='dependency_failure':
    def missing():raise RuntimeError('缺少进程检查依赖 psutil')
    process_control._psutil=missing
real_readlink=os.readlink
def no_proc(path,*a,**kw):
    if str(path).startswith('/proc/'):
        if testmode=='darwin_branch':raise AssertionError('macOS path must not read /proc')
        raise FileNotFoundError(2,'No such file or directory',str(path))
    return real_readlink(path,*a,**kw)
os.readlink=no_proc
sys.argv=['run_scenario.py','--scenario','测试场景','--mode','da','--run-dir',str(out)]
runpy.run_path(str(root/'scripts/run_scenario.py'),run_name='__main__')
''',encoding='utf-8')
    result=subprocess.run([sys.executable,str(wrapper),str(ROOT),str(out),mode],capture_output=True,text=True,timeout=30)
    meta=json.loads((out/'meta.json').read_text())
    if mode=='dependency_failure':
        assert result.returncode==1
        assert meta['status']=='failed' and 'psutil' in meta['error']
    else:
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert meta['status']=='done',meta
        assert meta['process_identity']['scope']['pid_namespace'] is None
        assert meta['executed_periods']==0 and meta['settled'] is False
        assert meta['completed_scope']=='da_only'
        assert (out/'da'/'summary.json').exists()
        snapshot=out/'scenario_snapshot'
        assert (snapshot/'config'/'fixed_costs.yaml').exists()
        assert (snapshot/'config'/'bid_cost_basis.yaml').exists()
        archived=json.loads(next((snapshot/'bids').rglob('bids_v1.json')).read_text())
        resolved=json.loads((out/'resolved_bids.json').read_text())
        assert archived['version']==resolved['version']==1
        assert archived['units']==resolved['units']
        assert set(resolved['units'])=={'CHEAP','LOCAL','H1'}


def test_child_exit_before_identity_capture_does_not_leave_permanent_starting(isolated,monkeypatch):
    own=pc.process_identity()
    def identity(pid=None):
        if pid is None:return own
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(pc,'process_identity',identity)
    real=common.subprocess.Popen
    def immediate(cmd,**kw):
        if cmd[0]=='git':return real(cmd,**kw)
        return SimpleNamespace(pid=999999999,poll=lambda:17)
    monkeypatch.setattr(common.subprocess,'Popen',immediate)
    d=common.start_run(isolated.DEFAULT,'full',1,1)
    meta=json.loads((d/'meta.json').read_text())
    assert meta['status']=='failed' and meta['exit_code']==17
    assert 'run.log' in meta['error']


def test_popen_failure_never_claims_process_started(isolated,monkeypatch):
    real=common.subprocess.Popen
    def failure(cmd,**kw):
        if cmd[0]=='git':return real(cmd,**kw)
        raise OSError('process resource limit')
    monkeypatch.setattr(common.subprocess,'Popen',failure)
    with pytest.raises(pc.LaunchError) as error:
        common.start_run(isolated.DEFAULT,'full',1,1)
    assert not error.value.process_started
    assert error.value.stage=='创建后台进程'
    assert json.loads((error.value.directory/'meta.json').read_text())['status']=='failed'
