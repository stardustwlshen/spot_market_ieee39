"""Legacy Appsi solve kwargs must carry stream/warmstart configuration explicitly."""
from types import SimpleNamespace
import pyomo.environ as pyo
import pytest
from smm import scuc
from test_da_stages import tiny_case


def prepared_model(log=False):
    c=tiny_case(reserve=0)
    c.cfg.base['solver']['log']=log
    m=scuc.build_model(c)
    for v in m.component_data_objects(pyo.Var):v.set_value(0.,skip_validation=True)
    return m


def fake_solver_factory(calls,fail_call=None):
    class Fake:
        def __init__(self):
            self.config=SimpleNamespace(warmstart=False)
            self.highs_options={}
        def solve(self,m,**kwargs):
            calls.append(kwargs)
            if len(calls)==fail_call:raise RuntimeError('synthetic infeasible start')
            return SimpleNamespace(solver=SimpleNamespace(termination_condition='optimal'),problem=SimpleNamespace(lower_bound=0.))
    return lambda *a,**kw:Fake()


@pytest.mark.parametrize('log',[False,True])
@pytest.mark.parametrize('enabled,ok',[(False,None),(True,False),(True,True)])
def test_final_mip_only_submits_a_successful_requested_warm_start(monkeypatch,log,enabled,ok):
    m=prepared_model(log)
    m._case.cfg.base['solver']['warm_start']=enabled
    calls=[]
    monkeypatch.setattr(scuc,'_solver',fake_solver_factory(calls))
    def seed(*a,**kw):
        assert enabled
        return {'ok':ok}
    monkeypatch.setattr(scuc,'warm_start',seed)
    scuc.solve_scuc(m)
    assert calls==[{'tee':log,'warmstart':bool(enabled and ok)}]


@pytest.mark.parametrize('log',[False,True])
@pytest.mark.parametrize('fail',[False,True])
def test_warm_start_subsolves_stream_explicitly_without_seed(monkeypatch,log,fail):
    m=prepared_model(log);calls=[]
    monkeypatch.setattr(scuc,'_solver',fake_solver_factory(calls,fail_call=2 if fail else None))
    info=scuc.warm_start(m)
    assert calls==[{'tee':log,'warmstart':False}]*2
    assert info['ok'] is not fail


@pytest.mark.parametrize('log',[False,True])
def test_sced_passes_tee_and_disables_mip_warm_start(monkeypatch,log):
    m=prepared_model(log);calls=[]
    monkeypatch.setattr(scuc,'_solver',fake_solver_factory(calls))
    scuc.solve_sced(m)
    assert calls==[{'tee':log,'warmstart':False}]


def test_real_legacy_wrapper_overrides_config_but_explicit_kwargs_work(monkeypatch,capfd):
    from pyomo.contrib.appsi.solvers.highs import Highs
    calls=[]
    actual=Highs._warm_start
    def spy(self):
        calls.append(True)
        return actual(self)
    monkeypatch.setattr(Highs,'_warm_start',spy)
    cfg=tiny_case(reserve=0).cfg
    cfg.base['solver']['log']=True
    m=pyo.ConcreteModel();m.x=pyo.Var(bounds=(0,None),initialize=1.)
    m.minimum=pyo.Constraint(expr=m.x>=1.)
    m.obj=pyo.Objective(expr=m.x)
    opt=scuc._solver(cfg,mip=False)
    opt.config.warmstart=True
    opt.config.stream_solver=True
    opt.solve(m)  # reproduce the old call: wrapper defaults suppress both flags
    assert calls==[]
    assert 'Running HiGHS' not in capfd.readouterr().out
    opt.solve(m,tee=True,warmstart=True)
    assert calls==[True]
    assert 'HiGHS' in capfd.readouterr().out
    assert pyo.value(m.x)==pytest.approx(1.,abs=1e-9,rel=0)
