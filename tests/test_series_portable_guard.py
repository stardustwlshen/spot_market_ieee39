from copy import deepcopy
from pathlib import Path
import builtins
import importlib
import pytest
from smm import series
from smm.process_control import file_lock
from multiday_fixture import micro_day


def test_import_does_not_require_fcntl():
    import subprocess,sys
    code="""import builtins
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
original=builtins.__import__
def without_fcntl(name,*args,**kwargs):
    if name=='fcntl': raise ImportError('Windows has no fcntl')
    return original(name,*args,**kwargs)
builtins.__import__=without_fcntl
from smm import series
assert Path(series.__file__).resolve().parent == Path(sys.argv[1]).resolve() / 'smm'
"""
    source = Path(__file__).resolve().parents[1] / 'src'
    subprocess.run([sys.executable,'-c',code,str(source)],check=True)


def test_existing_lock_excludes_runner(tmp_path):
    with file_lock(tmp_path/'series.lock'):
        with pytest.raises(RuntimeError):
            with file_lock(tmp_path/'series.lock'): pass
    with file_lock(tmp_path/'series.lock'): pass


def test_effective_thread_guard_honors_override():
    day,_=micro_day(); nextday=deepcopy(day)
    nextday.cfg.base['solver']['threads']=1
    with pytest.raises(ValueError,match='same effective HiGHS'):
        series.validate_solver_thread_budget([day,nextday])
    nextday.cfg.base['solver']['highs_extra']={'threads':4}
    with pytest.raises(ValueError,match='MIP and LP'):
        series.validate_solver_thread_budget([day,nextday])
    nextday.cfg.base['solver']['threads']=4
    assert series.validate_solver_thread_budget([day,nextday])==4
    nextday.cfg.base['solver']['highs_extra']['threads']=True
    with pytest.raises(ValueError,match='MIP and LP'):
        series.validate_solver_thread_budget([day,nextday])


def test_single_day_blocks_conflict_before_build():
    from smm.input_validation import validate_physical_inputs
    from smm.case import build_da_case
    day,_=micro_day()
    day.cfg.base['solver']['highs_extra']={'threads':1}
    with pytest.raises(ValueError,match='MIP and LP'):
        validate_physical_inputs(day.cfg)
    with pytest.raises(ValueError,match='MIP and LP'):
        build_da_case(day.cfg,day.net,day.profiles,day.bid_data)


def test_direct_model_rechecks_solver_configuration():
    from smm.case import build_da_case
    from smm.scuc import build_model
    day,_=micro_day()
    case=build_da_case(day.cfg,day.net,day.profiles,day.bid_data)
    day.cfg.base['solver']['highs_extra']={'threads':1}
    with pytest.raises(ValueError,match='MIP and LP'):build_model(case)


@pytest.mark.parametrize('value',[True,False,4.0,'4',0,-1,2147483648])
def test_thread_budget_rejects_implicit_coercion(value):
    from smm.input_validation import validate_solver_scheduler
    day,_=micro_day();day.cfg.base['solver']['threads']=value
    with pytest.raises(ValueError,match='positive integer'):validate_solver_scheduler(day.cfg)


def test_thread_default_matches_solver():
    from smm.input_validation import validate_solver_scheduler
    day,_=micro_day();day.cfg.base['solver'].pop('threads')
    assert validate_solver_scheduler(day.cfg)==4
