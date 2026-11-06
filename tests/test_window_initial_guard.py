from copy import deepcopy
import numpy as np
import pytest
from multiday_fixture import micro_day
from smm import state_bridge as bridge
from smm.case import build_da_case
from smm.rolling import make_window_case
from smm.scuc import build_model

@pytest.mark.parametrize('stage',['ID','RT'])
def test_window_marker_and_nonzero_t0_state(stage):
    day,state=micro_day(with_hydro=False)
    case=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,
        init=bridge.state_to_case_init(state),require_complete_bids=True,require_actual_state=True)
    state.t=1
    for g in state.thermal.values():g['hours']+=1
    state.pumped_hist['PS1']['xg'][0]=0;state.pumped_hist['PS1']['xp'][0]=0
    window=make_window_case(case,day.cfg,1,3,1,stage=='RT',state,{},None,stage)
    assert window.actual_state_required and window.t0==1
    build_model(window)
    broken=deepcopy(window);broken.init=None
    with pytest.raises(ValueError,match='explicit window initial state'):build_model(broken)
    broken=deepcopy(window);broken.init={}
    with pytest.raises(ValueError,match='thermal'):build_model(broken)


def test_unmarked_legacy_case_remains_supported():
    day,_=micro_day(with_hydro=False)
    case=build_da_case(day.cfg,day.net,day.profiles,day.bid_data)
    assert not case.actual_state_required and case.init is None
    build_model(case)
