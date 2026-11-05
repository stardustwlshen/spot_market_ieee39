"""Historical directories and unrelated starting records are never writable input."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from smm import run_admission as admission
from smm.process_control import process_identity

ROOT=Path(admission.__file__).parents[2]

def tree(path):
    return {p.relative_to(path).as_posix():p.read_bytes() for p in path.rglob('*') if p.is_file()}


def reserve(d, **changes):
    d.mkdir(exist_ok=True)
    meta=dict(scenario='默认',mode='full',bid_version=None,periods=None,status='starting',
              started='2026-11-05T00:00:00',launcher_identity={'pid':os.getppid()})
    meta.update(changes);(d/'meta.json').write_text(json.dumps(meta));return meta


def enter(d, **kw):
    return admission.admit_run_directory(d,scenario='默认',mode='full',**kw)


@pytest.mark.parametrize('meta',[[],None,{'status':'done'},{'status':'failed'},{'status':'running'},{}])
def test_existing_history_never_changed(tmp_path,meta):
    d=tmp_path/'old';d.mkdir();(d/'meta.json').write_text(json.dumps(meta));(d/'summary.json').write_text('original')
    before=tree(d)
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pytest.fail('history accepted')
    assert tree(d)==before


def test_new_directory_created_exclusively(tmp_path):
    d=tmp_path/'new'
    with enter(d) as meta:
        assert meta=={} and d.is_dir()
        (d/'summary.json').write_text('finished')
    before=tree(d)
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


@pytest.mark.parametrize('changes',[{'scenario':'other'},{'mode':'da'},{'bid_version':2},{'periods':8},
 {'started':7},{'started':'invalid'},{'launcher_identity':None},{'launcher_identity':{'pid':-1}}])
def test_reservation_must_match_request_and_parent(tmp_path,monkeypatch,changes):
    d=tmp_path/'new';reserve(d,**changes);before=tree(d)
    monkeypatch.setattr(admission,'process_state',lambda _: 'alive')
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


@pytest.mark.parametrize('state',['unknown','dead'])
def test_unknown_or_dead_parent_cannot_authorize(tmp_path,monkeypatch,state):
    d=tmp_path/'new';reserve(d);before=tree(d)
    monkeypatch.setattr(admission,'process_state',lambda _:state)
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


def test_ui_reservation_is_accepted_and_rechecked_under_lock(tmp_path,monkeypatch):
    d=tmp_path/'new';meta=reserve(d);calls=[]
    monkeypatch.setattr(admission,'process_state',lambda identity:calls.append(identity) or 'alive')
    with enter(d) as result:
        assert result==meta
        # A second child cannot enter even before the first status write.
        with pytest.raises(RuntimeError,match='另一个'):
            with enter(d):pass
    assert len(calls)>=3


@pytest.mark.parametrize('name',['summary.json','live.json','provenance.json','unknown.txt'])
def test_outputs_inside_starting_reservation_rejected(tmp_path,monkeypatch,name):
    d=tmp_path/'new';reserve(d);(d/name).write_text('old');before=tree(d)
    monkeypatch.setattr(admission,'process_state',lambda _: 'alive')
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


def test_foreign_worker_identity_rejected(tmp_path,monkeypatch):
    d=tmp_path/'new';reserve(d);(d/'worker.json').write_text(json.dumps({'pid':-1}));before=tree(d)
    monkeypatch.setattr(admission,'process_state',lambda _:'alive')
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


def test_cli_rejects_history_before_initial_status_or_freeze(tmp_path):
    d=tmp_path/'old';d.mkdir()
    (d/'meta.json').write_text(json.dumps(dict(status='done',scenario='historical_scene',mode='full')))
    (d/'summary.json').write_text('original');(d/'scenario_snapshot').mkdir()
    before=tree(d)
    result=subprocess.run([sys.executable,str(ROOT/'scripts/run_scenario.py'),'--scenario','默认','--mode','da','--run-dir',str(d)],capture_output=True,text=True)
    assert result.returncode!=0 and '禁止复用历史运行' in result.stderr
    assert tree(d)==before


def test_real_parent_identity_handoff_without_solver(tmp_path):
    d=tmp_path/'reserved';reserve(d,launcher_identity=process_identity())
    script='''from smm.run_admission import admit_run_directory
import sys
with admit_run_directory(sys.argv[1],scenario='默认',mode='full') as meta:
    assert meta['status']=='starting'
print('admitted')
'''
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    result=subprocess.run([sys.executable,'-c',script,str(d)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'admitted' in result.stdout


@pytest.mark.parametrize('name',['cancel.request','launch_warning.json','launch_error.log',
 f'worker.json.{os.getppid()}.tmp',f'launch_warning.json.{os.getppid()}.tmp'])
def test_ui_control_and_current_parent_atomic_files_are_allowed(tmp_path,monkeypatch,name):
    d=tmp_path/'new';reserve(d);(d/name).write_text('in progress')
    monkeypatch.setattr(admission,'process_state',lambda _: 'alive')
    with enter(d) as meta:assert meta['status']=='starting'
    assert (d/name).read_text()=='in progress'


@pytest.mark.parametrize('name',['worker.json.99999999.tmp','summary.json.1.tmp','meta.json.99999999.tmp'])
def test_arbitrary_or_foreign_temporary_files_are_not_allowed(tmp_path,monkeypatch,name):
    d=tmp_path/'new';reserve(d);(d/name).write_text('unknown');before=tree(d)
    monkeypatch.setattr(admission,'process_state',lambda _: 'alive')
    with pytest.raises(admission.RunDirectoryError):
        with enter(d):pass
    assert tree(d)==before


def test_cli_starting_cancel_preserves_request_and_stops_before_freeze(tmp_path):
    d=tmp_path/'reserved';reserve(d,mode='da',launcher_identity=process_identity())
    (d/'cancel.request').write_text('cancel before launch')
    result=subprocess.run([sys.executable,str(ROOT/'scripts/run_scenario.py'),'--scenario','默认','--mode','da','--run-dir',str(d)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    saved=json.loads((d/'meta.json').read_text())
    assert saved['status']=='stopped' and saved['executed_periods']==0
    assert (d/'cancel.request').read_text()=='cancel before launch'
    assert not (d/'scenario_snapshot').exists()
