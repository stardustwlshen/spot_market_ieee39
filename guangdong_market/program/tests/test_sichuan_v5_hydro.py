from dataclasses import replace

import pytest

from app.sichuan_v5_hydro import (
    Cascade, CascadeState, Reach, Reservoir, STEP_SECONDS, advance_cascade,
    attach_cascade_constraints, initial_cascade_state, validate_cascade,
)


def example(lag=2):
    upper = Reservoir('U', 0, 20_000, 6000, .5, 10, max_release_m3s=8)
    lower = Reservoir('D', 0, 20_000, 1000, 1.0, 15)
    path = Reach('U', 'D', lag, tuple([900., 1800.][:lag] + [0.] * max(0, lag - 2)))
    return Cascade((upper, lower), (path,), ({'U': 1., 'D': 0.},)*6)


def mass(state):
    return sum(state.volume_m3.values()) + sum(sum(a) for a in state.in_transit_m3.values())


def test_causal_delay_spill_and_initial_transit_conserve_water():
    c = example()
    s = initial_cascade_state(c)
    first = advance_cascade(c, s, {'U': 4, 'D': 1}, {'U': 1, 'D': 0})
    assert first.turbine_m3s == {'U': 2, 'D': 1}
    assert first.upstream_arrival_m3 == {'U': 0, 'D': 900}
    assert first.state.in_transit_m3 == {('U','D'): (1800,2700)}
    assert first.downstream_exit_m3 == {'D': 900}
    assert first.state.volume_m3 == {'U': 4200, 'D': 1000}
    assert mass(first.state) == mass(s) + STEP_SECONDS - 900
    second = advance_cascade(c, first.state, {'U': 2, 'D': 0}, {'U': 0, 'D': 0})
    assert second.upstream_arrival_m3['D'] == 1800
    third = advance_cascade(c, second.state, {'U': 0, 'D': 0}, {'U': 0, 'D': 0})
    assert third.upstream_arrival_m3['D'] == 2700  # t=0 turbine plus spill arrives at t=2
    assert mass(third.state) == mass(s) + 3*STEP_SECONDS - 900


def test_no_implicit_transit_and_reject_unhandled_topology_or_negative_flow():
    c = example()
    with pytest.raises(ValueError, match='期初在途'):
        validate_cascade(replace(c, reaches=(Reach('U','D',2,(0,)),)))
    with pytest.raises(ValueError, match='时滞'):
        validate_cascade(replace(c, reaches=(Reach('U','D',0,()),)))
    with pytest.raises(ValueError, match='成环'):
        validate_cascade(replace(c, reaches=(*c.reaches,Reach('D','U',1,(0,)))))
    extra = Reservoir('X',0,20_000,0,.5,0)
    with pytest.raises(ValueError, match='分流'):
        validate_cascade(replace(c,reservoirs=(*c.reservoirs,extra),
            natural_inflow_m3s=({'U':0,'D':0,'X':0},),reaches=(*c.reaches,Reach('U','X',1,(0,)))))
    with pytest.raises(ValueError, match='来水'):
        validate_cascade(replace(c, natural_inflow_m3s=({'U':-1,'D':0},)))


def test_reject_volume_and_combined_turbine_spill_bound_violations():
    c = example()
    s = initial_cascade_state(c)
    with pytest.raises(ValueError, match='总出流'):
        advance_cascade(c,s,{'U':10,'D':0},{'U':4,'D':0})
    with pytest.raises(ValueError, match='弃水上限'):
        advance_cascade(c,s,{'U':0,'D':0},{'U':11,'D':0})
    with pytest.raises(ValueError, match='期末库容'):
        advance_cascade(c,s,{'U':0,'D':4},{'U':0,'D':0})


def build(p, c, begin, stop, state=None):
    m=p.ConcreteModel()
    m.power=p.Var(('U','D'),range(begin,stop),domain=p.NonNegativeReals,bounds=(0,8))
    attach_cascade_constraints(m,c,begin,stop,lambda r,t:m.power[r,t],state=state)
    return m


def test_pyomo_fixed_actions_agree_with_commit_ledger_and_roll_uses_actuals():
    p=pytest.importorskip('pyomo.environ')
    solver=p.SolverFactory('appsi_highs')
    solver.options['threads']=1
    if not solver.available(False):
        pytest.skip('HiGHS not installed')
    c = example()
    m = build(p,c,0,4)
    values = [(4,1,1),(2,0,0),(0,0,0),(1,0,0)]
    for t, (u,d,spill_u) in enumerate(values):
        m.power['U',t].fix(u); m.power['D',t].fix(d)
        m.cascade_spill['U',t].fix(spill_u);m.cascade_spill['D',t].fix(0)
    m.obj=p.Objective(expr=sum(m.cascade_spill.values()))
    solver.solve(m)
    s = initial_cascade_state(c)
    for t, (u,d,spill_u) in enumerate(values):
        step=advance_cascade(c,s,{'U':u,'D':d},{'U':spill_u,'D':0})
        assert [p.value(m.cascade_volume[r,t]) for r in ('U','D')] == pytest.approx(
            [step.state.volume_m3[r] for r in ('U','D')])
        assert p.value(m.cascade_arrival['D',t]) == pytest.approx(step.upstream_arrival_m3['D'])
        s=step.state

    # Executed first action deliberately differs from a stale t=0 forecast.
    actual=advance_cascade(c,initial_cascade_state(c),{'U':0,'D':1},{'U':0,'D':0}).state
    rt=build(p,c,1,4,actual)
    assert p.value(rt.cascade_arrival['D',1])==1800
    assert p.value(rt.cascade_arrival['D',2])==0
    assert ('U',0) not in rt.cascade_volume
    with pytest.raises(ValueError, match='零时初值'):
        build(p,c,1,4)
    with pytest.raises(ValueError, match='窗口起点'):
        build(p,c,1,4,initial_cascade_state(c))


def test_forecast_tail_and_travel_time_extend_without_wrapping():
    p=pytest.importorskip('pyomo.environ')
    c=example(lag=5)
    model=build(p,c,0,2)
    assert list(model.cascade_natural) == [('U',0),('U',1),('D',0),('D',1)]
    assert [p.value(model.cascade_arrival['D',t]) for t in range(2)]==[900,1800]
    actual=advance_cascade(c,initial_cascade_state(c),{'U':1,'D':0},{'U':0,'D':0}).state
    assert actual.in_transit_m3[('U','D')] == (1800,0,0,0,450)
    rt=build(p,c,1,6,actual)
    assert [p.value(rt.cascade_arrival['D',t]) for t in range(1,6)] == [1800,0,0,0,450]
    with pytest.raises(ValueError, match='队列'):
        build(p,c,1,6,CascadeState(1,actual.volume_m3,{('U','D'):(1,)}))


def test_coupling_limits_downstream_generation_until_delayed_upstream_arrival():
    p=pytest.importorskip('pyomo.environ')
    solver=p.SolverFactory('appsi_highs')
    solver.options['threads']=1
    if not solver.available(False):
        pytest.skip('HiGHS not installed')
    c=Cascade((Reservoir('U',0,7200,7200,1,0),Reservoir('D',0,7200,0,1,0)),
              (Reach('U','D',1,(0,)),),({'U':0,'D':0},)*2)
    m=build(p,c,0,2)
    m.obj=p.Objective(expr=2*m.power['D',0]+m.power['D',1],sense=p.maximize)
    solver.solve(m)
    assert p.value(m.power['D',0])==pytest.approx(0)
    assert p.value(m.power['U',0])==pytest.approx(8)
    assert p.value(m.power['D',1])==pytest.approx(8)
