"""Nonempty pumped startup/history survives strict JSON and next-window rebuild."""
import json
import pytest
from dataclasses import fields
import numpy as np
import pyomo.environ as pyo
from smm import rolling,scuc
from smm.full import actual_state_evidence
from test_pumped_rolling_state import case_at


def test_nonempty_pumped_advance_serialize_and_rebuild_next_window():
    case=case_at(0,gap=2);case.cfg.base['periods']=3
    state=rolling.initial_state(case.cfg)
    sn=dict(u={g:np.array([v['online']]) for g,v in state.thermal.items()},
            P={g:np.array([v['p0']]) for g,v in state.thermal.items()},E={},Vh={},Ph={},delta={},
            Vu={'PS1':np.array([110.])},ngen={'PS1':np.array([1])},npump={'PS1':np.array([0])},
            xg={'PS1':np.array([1])},xp={'PS1':np.array([0])})
    nxt=rolling.advance(state,sn,case.cfg)
    assert np.isnan(nxt.pumped_hist['PS1']['xg'][1])
    saved=json.loads(json.dumps(actual_state_evidence(nxt),allow_nan=False))
    assert set(saved)=={f.name for f in fields(rolling.SysState)}
    assert saved['pumped_used']['PS1']=={'gen':1.,'pump':0.}
    assert saved['pumped_hist']['PS1']=={'xg':[1.],'xp':[0.]}
    case.t0=saved['t'];case.stage='RT';case.T=1
    case.init={k:v for k,v in saved.items() if k!='t'}
    rebuilt=scuc.build_model(case)  # no optimizer required
    assert rebuilt._pumped_remaining_starts['PS1']['gen']==0
    assert pyo.value(rebuilt.c_psg_max['PS1'].upper)==0
    rebuilt.xp['PS1',0].value=1
    assert pyo.value(rebuilt.c_pgap1['PS1',0,1].body)>pyo.value(rebuilt.c_pgap1['PS1',0,1].upper)

    nxt.pumped_hist['PS1']['xg'][0]=float('nan')
    with pytest.raises(ValueError,match='non-finite'):
        actual_state_evidence(nxt)
