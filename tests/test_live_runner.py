import json
import datetime as dt
from types import SimpleNamespace
import pandas as pd
import pytest
from smm.live_runner import ProgressWriter, RunCancelled, atomic_json, read_live, freeze_inputs
from smm.config import load_config
from smm.bids import default_bids


def test_atomic_progress_visible_before_completion_and_cancel(tmp_path, monkeypatch):
    p=ProgressWriter(tmp_path,96)
    assert read_live(tmp_path)['executed_periods']==0
    table=pd.DataFrame([{'period':0,'P_mw':12.}])
    monkeypatch.setattr('smm.rolling.rt_tables',lambda da,rr:{k:table for k in ('units','prices','system','storage','lines')})
    da=SimpleNamespace(tables={'prices':pd.DataFrame([{'period':0,'lmp':300.,'optional_field':float('nan')}])})
    p({'phase':'da_done','da':da})
    rr=SimpleNamespace(rt=[{'t':0,'snap':{'P':{'C1':[12.,13.]}},'prices':{}}])
    p({'phase':'rt_window','rr':rr})
    live=read_live(tmp_path)
    assert live['executed_periods']==1 and not live['settled']
    assert live['da_prices'][0]['optional_field'] is None
    assert live['lookahead'][0]['status']=='已执行'
    assert live['lookahead'][1]['status']=='前瞻计划（未执行）'
    (tmp_path/'cancel.request').touch()
    with pytest.raises(RunCancelled):p.check_cancel()
    assert read_live(tmp_path)['executed_periods']==1
    assert not list(tmp_path.glob('*.tmp'))


def test_snapshot_bid_isolation_and_reject_invalid(tmp_path):
    cfg=load_config(); bids=default_bids(cfg)
    snap=freeze_inputs(cfg.root,tmp_path,bids)
    submitted=json.loads((tmp_path/'submitted_bids.json').read_text())
    uid=next(iter(bids['units']));bids['units'][uid]['manual_test_marker']='changed later'
    assert 'manual_test_marker' not in submitted['units'][uid]
    assert (snap/'config/modes.yaml').read_bytes()==(cfg.root/'config/modes.yaml').read_bytes()
    with pytest.raises(FileExistsError):freeze_inputs(cfg.root,tmp_path,bids)


def test_launch_freezes_inputs_and_blocks_duplicate(tmp_path, monkeypatch):
    import sys,os
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    import common
    from smm import scenario
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    scenario.ensure_default()
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    captured=[]
    real_spawn=common.subprocess.Popen
    def fake_spawn(*args,**kwargs):
        if args and args[0][0]=="git":return real_spawn(*args,**kwargs)
        captured.append(args);return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess,'Popen',fake_spawn)
    cfg=common.scen_config(scenario.DEFAULT)
    bids=default_bids(cfg)
    d=common.start_run(scenario.DEFAULT,'full',bids['version'],4,bids)
    with pytest.raises(RuntimeError,match='已有运行'):
        common.start_run(scenario.DEFAULT,'full',bids['version'],4,bids)
    assert len(captured)==1
    assert (d/'submitted_bids.json').exists()
    common.stop_run({'dir':str(d)})
    meta=json.loads((d/'meta.json').read_text());meta['dir']=str(d)
    assert common.run_status(meta)=='cancelling'
    assert (d/'cancel.request').exists()


def test_streamlit_displays_partial_without_settlement(tmp_path,monkeypatch):
    import sys,os
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    d=scenario.RUNS_DIR/'fixture'/'partial';d.mkdir(parents=True)
    atomic_json(d/'meta.json',{'scenario':'fixture','mode':'full','status':'running','pid':os.getpid(),
                             'started':'2099-01-01','periods':96,'stage':'RT','bid_version':2})
    atomic_json(d/'live.json',{'phase':'rt_window','executed_periods':1,'total_periods':96,'settled':False,
      'executed':{'prices':[{'period':0,'lmp':300.,'lmp_raw':300.}],
                  'units':[{'period':0,'unit':'C1','type':'coal','P_mw':20.}],
                  'system':[{'period':0,'load_mw':20.}]},
      'lookahead':[{'period':1,'power_mw':21.,'status':'前瞻计划（未执行）'}]})
    root=Path(__file__).resolve().parents[1]
    at=AppTest.from_file(str(root/'app/pages/2_运行.py'),default_timeout=60).run()
    assert not at.exception
    assert any('实时已执行：1 / 96' in x.value for x in at.subheader)
    assert any('已执行前缀' in x.value and '不是完整日结算' in x.value for x in at.caption)
    assert next(x for x in at.button if x.label=='▶ 开始运行').disabled
    assert len(at.dataframe)>=3
    assert len(at.get("vega_lite_chart"))>=2  # System charts plus selected-unit diagnostics.


def test_streamlit_simulation_updates_only_selected_draft_without_touching_v2(tmp_path, monkeypatch):
    import copy
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    from smm.bids import load_bids
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root=scenario.scenario_path('默认'))
    saved = load_bids(cfg)
    before = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    at = AppTest.from_file(str(root / 'app/pages/2_运行.py'), default_timeout=60).run()
    key = next(x for x in at.number_input if x.label == '空载报价（元/h）').key.removeprefix('quote_edit:').removesuffix(':C1:noload')
    initial = copy.deepcopy(at.session_state[key])
    next(x for x in at.button if x.label == '生成随机报价预览').click().run()
    assert at.session_state[key] == initial
    next(x for x in at.button if x.label == '采用预览报价').click().run()
    assert not at.exception
    changed = at.session_state[key]
    assert changed['units']['C1'] != initial['units']['C1']
    assert all(changed['units'][uid] == bid for uid, bid in initial['units'].items() if uid != 'C1')
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not scenario.RUNS_DIR.exists()


def test_invalid_regulation_rejected_before_snapshot_or_launch(tmp_path):
    cfg=load_config()
    with pytest.raises(ValueError,match='缺少字段'):
        freeze_inputs(cfg.root,tmp_path,regulation={'enabled':True})
    assert not (tmp_path/'scenario_snapshot').exists()


def test_current_ui_is_energy_only_and_historical_regulation_cannot_start(tmp_path,monkeypatch):
    import sys,copy
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'app'))
    import common
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    at=AppTest.from_file(str(root/'app/pages/2_运行.py'),default_timeout=60).run()
    assert not at.exception
    assert not any('调频' in x.label for x in at.number_input)
    assert not any('调频' in x.label for x in at.checkbox)
    with pytest.raises(ValueError,match='历史归档'):
        common.start_run(scenario.DEFAULT,'full',2,4,regulation={'enabled':True})


def test_other_pid_namespace_does_not_mark_live_run_failed(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    import common
    meta={'dir':str(tmp_path),'status':'running','pid':999999999,'pid_namespace':'different-namespace'}
    atomic_json(tmp_path/'meta.json',meta)
    assert common.run_status(meta)=='running'
    assert json.loads((tmp_path/'meta.json').read_text())['status']=='running'


def test_stage_diagnostics_remain_separate_from_executed_prefix(tmp_path,monkeypatch):
    stages={stage:{'units':[{'period':0,'unit':'C1','type':'coal','P_mw':power,'on':1,'q_reg_mw':None}],
                   'summary':{'objective':power,'objective_kind':kind}}
            for stage,power,kind in [('scuc_preplan',100.,'energy'),('agc_auxiliary',120.,'score_only'),('energy_sced',110.,'energy')]}
    writer=ProgressWriter(tmp_path)
    writer({'phase':'da_stage','stage_diagnostics':stages})
    assert read_live(tmp_path)['da_stages']['agc_auxiliary']['units'][0]['P_mw']==120.
    monkeypatch.setattr('smm.rolling.rt_tables',lambda da,rr:{'units':pd.DataFrame([{'period':0,'unit':'C1','P_mw':110.}])})
    rr=SimpleNamespace(rt=[{'t':0,'snap':{'P':{'C1':[110.]}},'prices':{},'stage_diagnostics':stages}])
    writer({'phase':'rt_window','rr':rr,'da':object()})
    live=read_live(tmp_path)
    assert live['executed']['units'][0]['P_mw']==110.
    assert live['stage_history']['0']['scuc_preplan']['units'][0]['P_mw']==100.
    assert live['stage_history']['0']['agc_auxiliary']['units'][0]['q_reg_mw'] is None


def test_cancel_at_completed_da_stage_preserves_stage_snapshot(tmp_path):
    p=ProgressWriter(tmp_path)
    (tmp_path/'cancel.request').touch()
    with pytest.raises(RunCancelled):
        p({'phase':'da_stage','stage_diagnostics':{'scuc_preplan':{'units':[],'summary':{'status':'done'}}}})
    assert read_live(tmp_path)['da_stages']['scuc_preplan']['summary']['status']=='done'
    assert read_live(tmp_path)['settled'] is False


def test_immediate_restart_uses_distinct_run_directory(tmp_path,monkeypatch):
    from smm import scenario
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path)
    first=scenario.new_run_dir('retry','full')
    second=scenario.new_run_dir('retry','full')
    assert first != second and first.exists() and second.exists()


# Freeze only the scenario module's clock, not the shared datetime module.
class _FixedRunDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 8, 12, 34, 56, 123456, tzinfo=tz)


def _freeze_run_allocation_clock(monkeypatch, tmp_path):
    from smm import scenario
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(scenario, '_dt', SimpleNamespace(datetime=_FixedRunDatetime))
    return scenario


@pytest.mark.parametrize('count', [2, 32])
def test_fixed_clock_restarts_preserve_first_run(tmp_path, monkeypatch, count):
    scenario = _freeze_run_allocation_clock(monkeypatch, tmp_path)
    first = scenario.new_run_dir('retry', 'full')
    assert first.name == '20261108-123456-123456_full'
    sentinel = first / 'saved-result.bin'
    sentinel.write_bytes(b'original result\x00must not change')
    original = sentinel.read_bytes()
    directories = [first] + [scenario.new_run_dir('retry', 'full') for _ in range(count - 1)]
    assert len(set(directories)) == count
    assert all(directory.is_dir() for directory in directories)
    assert sentinel.read_bytes() == original
    assert all(not list(directory.iterdir()) for directory in directories[1:])
    assert set(first.parent.iterdir()) == set(directories)


@pytest.mark.parametrize('existing_kind', ['directory', 'file'])
def test_fixed_clock_collision_preserves_existing_evidence(tmp_path, monkeypatch, existing_kind):
    scenario = _freeze_run_allocation_clock(monkeypatch, tmp_path)
    existing = tmp_path / 'retry' / '20261108-123456-123456_full'
    existing.parent.mkdir()
    if existing_kind == 'directory':
        existing.mkdir()
        sentinel = existing / 'saved-result.bin'
    else:
        sentinel = existing
    sentinel.write_bytes(b'historical evidence\x00unchanged')
    original = sentinel.read_bytes()
    created = scenario.new_run_dir('retry', 'full')
    assert created != existing and created.is_dir()
    assert created.name.startswith('20261108-123456-123456-')
    assert created.name.endswith('_full')
    assert sentinel.read_bytes() == original
    assert not list(created.iterdir())


def _allocate_fixed_clock_runs(root, barrier, result_queue, worker):
    import os
    import traceback
    from pathlib import Path
    from smm import scenario
    scenario.RUNS_DIR = Path(root)
    scenario._dt = SimpleNamespace(datetime=_FixedRunDatetime)
    try:
        records = []
        for index in range(8):
            barrier.wait(timeout=45)
            directory = scenario.new_run_dir('retry', 'full')
            owner = f'{worker}:{index}'
            (directory / 'owner.txt').write_text(owner, encoding='utf-8')
            records.append((str(directory), owner))
        result_queue.put(('ok', os.getpid(), records))
    except BaseException:
        result_queue.put(('error', os.getpid(), traceback.format_exc()))
        raise


def test_fixed_clock_concurrent_processes_claim_distinct_runs(tmp_path):
    import multiprocessing
    from pathlib import Path
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(4)
    result_queue = context.Queue()
    processes = [context.Process(target=_allocate_fixed_clock_runs,
                                 args=(str(tmp_path), barrier, result_queue, worker))
                 for worker in range(4)]
    try:
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=90) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes), results
        assert all(result[0] == 'ok' for result in results), results
        assert len({result[1] for result in results}) == 4
        records = [record for _, _, batch in results for record in batch]
        assert len(records) == len({directory for directory, _ in records}) == 32
        for directory, owner in records:
            assert (Path(directory) / 'owner.txt').read_text(encoding='utf-8') == owner
        assert {str(path) for path in (tmp_path / 'retry').iterdir()} == {
            directory for directory, _ in records}
    finally:
        # Only these test-owned children can be cleaned up; never inspect or
        # signal a process belonging to an application or another test run.
        for process in processes:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=30)
                assert not process.is_alive()
                process.close()
        result_queue.close()
        result_queue.join_thread()


def test_fixed_clock_new_names_keep_history_readable_and_sorted(tmp_path, monkeypatch):
    scenario = _freeze_run_allocation_clock(monkeypatch, tmp_path)
    legacy = tmp_path / 'retry' / '20200101-000000_full'
    legacy.mkdir(parents=True)
    historical = {'scenario': 'retry', 'mode': 'full', 'status': 'failed',
                  'started': '2020-01-01T00:00:00'}
    atomic_json(legacy / 'meta.json', historical)
    original = (legacy / 'meta.json').read_bytes()
    normal = scenario.new_run_dir('retry', 'full')
    collision = scenario.new_run_dir('retry', 'full')
    for directory, started in [(normal, '2026-11-08T12:34:57'),
                               (collision, '2026-11-08T12:34:56')]:
        atomic_json(directory / 'meta.json',
                    {**historical, 'status': 'done', 'started': started})
    rows = scenario.list_runs('retry')
    assert [row['dir'] for row in rows] == [str(normal), str(collision), str(legacy)]
    assert [row['status'] for row in rows] == ['done', 'done', 'failed']
    assert (legacy / 'meta.json').read_bytes() == original


@pytest.mark.parametrize('error_type', [PermissionError, OSError])
def test_run_directory_noncollision_errors_propagate(tmp_path, monkeypatch, error_type):
    from pathlib import Path
    scenario = _freeze_run_allocation_clock(monkeypatch, tmp_path)
    error = error_type('allocation denied')
    calls = []
    def fail_mkdir(path, *args, **kwargs):
        calls.append(path)
        raise error
    monkeypatch.setattr(Path, 'mkdir', fail_mkdir)
    with pytest.raises(error_type) as caught:
        scenario.new_run_dir('retry', 'full')
    assert caught.value is error
    assert len(calls) == 1



@pytest.mark.parametrize('error_type', [PermissionError, OSError])
def test_run_directory_fallback_errors_preserve_existing_run(tmp_path, monkeypatch, error_type):
    scenario = _freeze_run_allocation_clock(monkeypatch, tmp_path)
    existing = scenario.new_run_dir('retry', 'full')
    sentinel = existing / 'saved-result.bin'
    sentinel.write_bytes(b'historical evidence\x00unchanged')
    original = sentinel.read_bytes()
    error = error_type('fallback allocation denied')
    calls = []
    def fail_mkdtemp(*args, **kwargs):
        calls.append((args, kwargs))
        raise error
    monkeypatch.setattr(scenario.tempfile, 'mkdtemp', fail_mkdtemp)
    with pytest.raises(error_type) as caught:
        scenario.new_run_dir('retry', 'full')
    assert caught.value is error
    assert calls == [((), {'prefix': '20261108-123456-123456-',
                          'suffix': '_full', 'dir': existing.parent})]
    assert sentinel.read_bytes() == original
    assert list(existing.iterdir()) == [sentinel]
    assert list(existing.parent.iterdir()) == [existing]
