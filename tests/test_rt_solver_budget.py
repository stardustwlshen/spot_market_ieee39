"""Shared solver elapsed accounting; no expensive solver needed."""
from types import SimpleNamespace
import pytest
from smm import scuc, rolling
from test_da_stages import tiny_case


class Clock:
    value=0.
    def now(self):return self.value


class Solver:
    def __init__(self,clock,duration,fail=False):
        self.clock=clock;self.duration=duration;self.fail=fail;self.highs_options={'time_limit':180.}
        self.calls=0
    def solve(self,*args,**kwargs):
        self.calls+=1;self.clock.value+=self.duration
        if self.fail:raise RuntimeError('solver failed')
        return 'solution'


def test_three_stages_share_one_180_second_budget(monkeypatch):
    clock=Clock();monkeypatch.setattr(scuc.time,'perf_counter',clock.now)
    m=SimpleNamespace(_solver_budget=scuc.SolverBudget(180.))
    limits=[]
    for phase,duration,reserve in [('scuc',160.,True),('formal_sced',7.,False),('auxiliary_pricing',5.,False)]:
        opt=Solver(clock,duration)
        scuc._solve_with_budget(m,opt,phase=phase,reserve_lp=reserve)
        limits.append(opt.highs_options['time_limit'])
    assert limits==[170.,20.,13.]
    assert m._solver_budget.report()['solver_elapsed_s']==172.
    assert m._solver_budget.report()['remaining_solver_s']==8.


def test_warm_subcalls_consume_same_budget_and_preserve_lp_reserve(monkeypatch):
    clock=Clock();monkeypatch.setattr(scuc.time,'perf_counter',clock.now)
    m=SimpleNamespace(_solver_budget=scuc.SolverBudget(180.))
    for phase in ('warm_relaxation','warm_commitment'):
        scuc._solve_with_budget(m,Solver(clock,10.),phase=phase,reserve_lp=True)
    opt=Solver(clock,1.)
    scuc._solve_with_budget(m,opt,phase='scuc',reserve_lp=True)
    assert opt.highs_options['time_limit']==150.


def test_exhaustion_blocks_next_solver_and_overrun_is_not_executable(monkeypatch):
    clock=Clock();monkeypatch.setattr(scuc.time,'perf_counter',clock.now)
    m=SimpleNamespace(_solver_budget=scuc.SolverBudget(180.))
    with pytest.raises(RuntimeError,match='not executable'):
        scuc._solve_with_budget(m,Solver(clock,181.),phase='formal_sced')
    next_solver=Solver(clock,1.)
    with pytest.raises(RuntimeError,match='exhausted'):
        scuc._solve_with_budget(m,next_solver,phase='auxiliary_pricing')
    assert next_solver.calls==0


def test_failed_solver_elapsed_is_charged_and_da_unbudgeted_unchanged(monkeypatch):
    clock=Clock();monkeypatch.setattr(scuc.time,'perf_counter',clock.now)
    m=SimpleNamespace(_solver_budget=scuc.SolverBudget(180.))
    with pytest.raises(RuntimeError,match='solver failed'):
        scuc._solve_with_budget(m,Solver(clock,4.,True))
    assert m._solver_budget.used_s==4.
    opt=Solver(clock,2.);opt.highs_options['time_limit']=600.
    assert scuc._solve_with_budget(SimpleNamespace(),opt)=='solution'
    assert opt.highs_options['time_limit']==600.


def test_rolling_budget_failure_never_advances_actual_state(monkeypatch):
    case=tiny_case(reserve=0)
    da=SimpleNamespace(case=case)
    monkeypatch.setattr(rolling,'_da_plan_arrays',lambda da:{})
    calls=[]
    def fail(*args,**kwargs):raise RuntimeError('RT shared solver budget exhausted')
    monkeypatch.setattr(rolling,'run_rt_period',fail)
    monkeypatch.setattr(rolling,'advance',lambda *args:calls.append('advance'))
    with pytest.raises(RuntimeError,match='budget exhausted'):
        rolling.run_rolling(da,periods=1,log=lambda *a:None)
    assert calls==[]


@pytest.mark.parametrize('auxiliary',[False,True])
def test_small_rt_pipeline_uses_shared_ledger_with_or_without_auxiliary(auxiliary):
    case=tiny_case(reserve=0);case.stage='RT'
    case.cfg.mode['auxiliary_pricing']['enabled']=auxiliary
    case.cfg.base['solver']['time_limit_s']=5.
    result=rolling.clear_window(case)
    report=result['solver_budget']
    assert report['total_solver_budget_s']==5.
    assert report['reserve_lp_s']==.5  # short test budgets use min(10,10% total)
    assert [call['phase'] for call in report['calls']]==(['scuc','formal_sced','auxiliary_pricing'] if auxiliary else ['scuc','formal_sced'])
    assert report['solver_elapsed_s']==pytest.approx(sum(c['elapsed_s'] for c in report['calls']),abs=1e-12,rel=0)
    if auxiliary:
        assert result['model']._solver_budget is result['pricing_model']._solver_budget
    assert result['build_time_s']>=0
