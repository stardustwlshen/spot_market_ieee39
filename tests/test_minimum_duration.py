"""Minimum-time requirements round up; physical initial locks cannot be overwritten."""
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc
from test_da_stages import tiny_case


def case(T=6):
    c=tiny_case(reserve=0)
    c.cfg.base['periods']=T
    c.load_bus={2:np.full(T,100.)};c.R_up=c.R_dn=np.zeros(T)
    c.cfg.base['solver']['commit_step_periods']=1
    return c


@pytest.mark.parametrize('duration,required',[(1.1,5),(1.,4),(.9,4),(.25,1)])
@pytest.mark.parametrize('kind',['up','down'])
def test_noninteger_and_exact_minimum_duration_window(duration,required,kind):
    c=case()
    for u in c.cfg.units['thermal']:u['ut' if kind=='up' else 'dt']=duration
    m=scuc.build_model(c)
    t=required-1
    for k in m.T:
        m.u['CHEAP',k].value=0 if kind=='up' else 1
        m.v['CHEAP',k].value=0;m.w['CHEAP',k].value=0
    getattr(m,'v' if kind=='up' else 'w')['CHEAP',0].value=1
    con=getattr(m,'c_minup' if kind=='up' else 'c_mindn')['CHEAP',t]
    assert pyo.value(con.body)>pyo.value(con.upper)
    if required<len(m.T):
        released=getattr(m,'c_minup' if kind=='up' else 'c_mindn')['CHEAP',required]
        assert pyo.value(released.body)<=pyo.value(released.upper)


@pytest.mark.parametrize('online',[False,True])
@pytest.mark.parametrize('hours,locked',[(.9,1),(.75,1),(.5,2),(1.,0)])
def test_initial_remaining_rounds_up_and_exact_boundaries(online,hours,locked):
    c=case()
    for u in c.cfg.units['thermal']:
        u.update(ut=1.,dt=1.)
        u['init'].update(online=online,hours=hours,p0=0.)
    m=scuc.build_model(c)
    assert [t for g,t in m.c_initial_min_duration if g=='CHEAP']==list(range(locked))
    for t in range(locked):
        assert pyo.value(m.c_initial_min_duration['CHEAP',t].lower)==int(online)


@pytest.mark.parametrize('online',[False,True])
def test_case_fixed_conflict_does_not_erase_physical_initial_lock(online):
    c=case(T=1)
    for u in c.cfg.units['thermal']:
        u.update(ut=1.,dt=1.)
        u['init'].update(online=online,hours=.9,p0=0.)
    c.fixed={'u':{('CHEAP',0):int(not online)}}
    m=scuc.build_model(c)
    constraint=m.c_initial_min_duration['CHEAP',0]
    assert abs(pyo.value(constraint.body)-pyo.value(constraint.lower))==1
    with pytest.raises(RuntimeError):scuc.solve_scuc(m)


def test_commitment_step_uses_absolute_window_period():
    c=case(T=4);c.t0=1;c.cfg.base['solver']['commit_step_periods']=4
    m=scuc.build_model(c)
    assert ('CHEAP',0) in m.c_cstep  # absolute1 cannot switch
    assert ('CHEAP',3) not in m.c_cstep  # absolute4 can switch
