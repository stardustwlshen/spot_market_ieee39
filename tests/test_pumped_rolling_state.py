"""Executed startup budgets and cross-window pumped switching history."""
from copy import deepcopy
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc, rolling
from test_storage_reserve import pumped_case


def case_at(t, used=0, gap=1, history=None):
    c=pumped_case(mode='idle',enabled=False)
    c.stage='RT';c.t0=t
    c.cfg.units['pumped'][0].update(max_gen_starts=1,max_pump_starts=1,switch_gap=gap)
    state=rolling.initial_state(c.cfg)
    c.init=dict(thermal=deepcopy(state.thermal),hydro={},storage={},pumped=deepcopy(state.pumped),
                pumped_used={'PS1':dict(gen=used,pump=0)},pumped_hist=history or {})
    return c


def test_rt_remaining_daily_start_budget_blocks_second_start():
    c=case_at(1,used=1)
    m=scuc.build_model(c)
    assert pyo.value(m.c_psg_max['PS1'].upper)==0
    assert m._pumped_remaining_starts['PS1']['gen']==0
    m.sg['PS1',0].value=1
    assert pyo.value(m.c_psg_max['PS1'].body)>pyo.value(m.c_psg_max['PS1'].upper)
    c.init['pumped_used']['PS1']['gen']=2
    with pytest.raises(ValueError,match='超过日上限'):scuc.build_model(c)


def test_advance_counts_only_first_executed_count_increase_not_sg():
    c=case_at(0)
    c.cfg.base['periods']=3
    s=rolling.initial_state(c.cfg)
    def snap(ng):
        return dict(u={g:np.array([v['online']]) for g,v in s.thermal.items()},
                    P={g:np.array([v['p0']]) for g,v in s.thermal.items()}, E={},Vh={},Ph={},delta={},
                    Vu={'PS1':np.array([110.,110.])},ngen={'PS1':np.array([ng,99])},npump={'PS1':np.array([0,99])},
                    xg={'PS1':np.array([int(ng>0),1])},xp={'PS1':np.array([0,1])},sg={'PS1':np.array([99.,99.])})
    s1=rolling.advance(s,snap(1),c.cfg)
    assert s1.pumped_used['PS1']==dict(gen=1.,pump=0.)
    assert np.isnan(s1.pumped_hist['PS1']['xg'][1])
    s2=rolling.advance(s1,snap(0),c.cfg)
    assert s2.pumped_used['PS1']['gen']==1
    with pytest.raises(ValueError,match='超过日上限'):
        rolling.advance(s2,snap(1),c.cfg)


def test_gap_two_reads_actual_two_periods_ago_not_assumed_idle():
    c=case_at(2,gap=2,history={'PS1':{'xg':[1.,0.],'xp':[0.,0.]}})
    m=scuc.build_model(c)
    m.xp['PS1',0].value=1
    assert pyo.value(m.c_pgap1['PS1',0,2].body)>pyo.value(m.c_pgap1['PS1',0,2].upper)
    for bad in ([],[float('nan')]):
        c.init['pumped_hist']['PS1']['xg']=bad
        with pytest.raises(ValueError,match='工况历史'):scuc.build_model(c)


def test_before_day_history_uses_declared_initial_mode():
    c=case_at(0,gap=2)
    c.cfg.units['pumped'][0]['init_mode']='gen'
    c.init['pumped']['PS1'].update(ng=1,np=0,xg=1,xp=0)
    m=scuc.build_model(c)
    m.xp['PS1',0].value=1
    assert pyo.value(m.c_pgap1['PS1',0,2].body)>1
