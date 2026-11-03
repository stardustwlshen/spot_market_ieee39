"""Real SCUC startup-state constraints, tested with fixed commitment histories."""
from types import SimpleNamespace
import math
import pyomo.environ as pyo
import pytest
from smm import scuc
from smm.da import run_da
from test_da_stages import tiny_case


def state_lp(hours, *, endogenous=False, older_shutdown=False, bids=(100.,200.,0.), dt=.25):
    # Only the actual production eligibility constraints are isolated; no full MIP.
    t=int(round(hours/dt)) if endogenous else 0
    m=pyo.ConcreteModel()
    m.G=pyo.Set(initialize=['G']);m.T=pyo.Set(initialize=[t])
    m.GST=pyo.Set(initialize=[('G','hot'),('G','warm')],dimen=2)
    m.w=pyo.Var(['G'],range(t+1),bounds=(0,1))
    for k in range(t+1):m.w['G',k].fix(0.)
    if endogenous:m.w['G',0].fix(1.)
    if older_shutdown:
        m.w['G',t-1].fix(1.)  # older warm outage must not beat latest hot outage
    m.v=pyo.Var(m.G,m.T,bounds=(0,1));m.v['G',t].fix(1.)
    m.y=pyo.Var(m.GST,m.T,bounds=(0,1));m.ycold=pyo.Var(m.G,m.T,bounds=(0,1))
    md=SimpleNamespace(thermal={'G':dict(states=[('hot',math.ceil(8/dt),bids[0]),('warm',math.ceil(72/dt),bids[1])],
        startup_threshold_hours={'hot':8.,'warm':72.},
        init=dict(online=endogenous,hours=hours))})
    scuc._attach_startup_state_constraints(m,md,dt)
    m.obj=pyo.Objective(expr=bids[0]*m.y['G','hot',t]+bids[1]*m.y['G','warm',t]+bids[2]*m.ycold['G',t])
    scuc._solver(tiny_case(reserve=0).cfg, mip=False).solve(m)
    return {s:pyo.value(m.y['G',s,t]) for s in ('hot','warm')}|{'cold':pyo.value(m.ycold['G',t])}


@pytest.mark.parametrize('hours,expected',[(7.75,'hot'),(8.,'warm'),(71.75,'warm'),(72.,'cold')])
@pytest.mark.parametrize('endogenous',[False,True])
@pytest.mark.parametrize('bids',[(100.,200.,0.),(300.,0.,100.),(0.,300.,100.)])
def test_exact_boundaries_independent_of_nonmonotonic_prices(hours,expected,endogenous,bids):
    assert state_lp(hours,endogenous=endogenous,bids=bids)=={s:float(s==expected) for s in ('hot','warm','cold')}


def test_older_shutdown_does_not_override_latest_hot_outage():
    assert state_lp(10.,endogenous=True,older_shutdown=True,bids=(300.,0.,0.))==dict(hot=1.,warm=0.,cold=0.)


def test_real_model_legal_free_cold_bid_cannot_evade_hot_start_cost():
    case=tiny_case(reserve=0)
    case.cfg.base["solver"]["warm_start"]=False  # isolate state-cost eligibility, not warm-start heuristic
    for unit in case.cfg.units['thermal']:
        unit.update(ut=.25,dt=.25)  # separate minimum-time feasibility from startup-price eligibility
        unit['init'].update(online=False,hours=7.75,p0=0.)
        case.bids['units'][unit['id']]['startup_cost']=dict(hot=100.,warm=200.,cold=0.)
    case.fixed={'u':{('CHEAP',0):1,('LOCAL',0):1}}
    result=run_da(case,verbose=False)
    assert result.costs['thermal_startup']==pytest.approx(200.)
    assert all(pyo.value(v)==0 for v in result.model.ycold.values())


@pytest.mark.parametrize('hours,expected',[(7.8,'hot'),(8.,'warm'),(71.9,'warm'),(72.,'cold')])
def test_initial_thresholds_use_hours_not_rounded_periods(hours,expected):
    assert state_lp(hours,dt=.3)=={s:float(s==expected) for s in ('hot','warm','cold')}
