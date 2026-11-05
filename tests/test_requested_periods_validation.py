"""Do not spend a DA solve discovering an invalid requested RT prefix."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace as NS
import numpy as np
import pytest
from smm import full,rolling,scenario
from smm.config import load_config
from smm.input_validation import validate_requested_periods

ROOT=Path(full.__file__).parents[2]
BAD=[0,-1,1.5,True,False,float('nan'),float('inf'),'8']

@pytest.mark.parametrize('periods',BAD)
def test_full_api_rejects_before_da(periods,monkeypatch):
    calls=[]
    def da(*a,**kw):
        calls.append(1)
        raise AssertionError('invalid request entered DA')
    monkeypatch.setattr(full,'run_da',da)
    with pytest.raises(ValueError,match='periods'):full.run_full(NS(T=96),NS(),periods=periods)
    assert calls==[]

@pytest.mark.parametrize('periods',BAD)
def test_rolling_api_rejects_before_snapshot(periods,monkeypatch):
    def snapshot(*a,**kw):raise AssertionError('invalid request reached model snapshot')
    monkeypatch.setattr(rolling,'_da_plan_arrays',snapshot)
    with pytest.raises(ValueError,match='periods'):
        rolling.run_rolling(NS(case=NS(cfg=load_config())),periods=periods)

@pytest.mark.parametrize('periods',[None,1,8,np.int64(96),100])
def test_valid_prefix_types_and_legacy_above_day_cap_accepted(periods):
    validate_requested_periods(periods)

@pytest.mark.parametrize('value',['0','-1','1.5'])
def test_scenario_cli_rejects_before_directory_creation(value,monkeypatch):
    monkeypatch.setattr(scenario,'scenario_path',lambda *a:pytest.fail('scenario accessed before argument rejection'))
    monkeypatch.setattr(sys,'argv',['run_scenario.py','--scenario','默认','--periods',value])
    with pytest.raises(SystemExit) as exc:runpy.run_path(str(ROOT/'scripts/run_scenario.py'),run_name='__main__')
    assert exc.value.code==2
