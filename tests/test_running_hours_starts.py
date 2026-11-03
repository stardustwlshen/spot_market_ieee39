from types import SimpleNamespace as NS
import pandas as pd
import pytest
from smm.full import running_hours


def make_result(online, *, cfg_initial=False, da_initial=None, rt_initial=False):
    units=pd.DataFrame([dict(unit='G',type='gas_gt',period=t,on=value,P_mw=10.*value) for t,value in enumerate(online)])
    cfg=NS(dt=.25,units={'thermal':[{'id':'G','init':{'online':cfg_initial}}]})
    case=NS(cfg=cfg,init=None if da_initial is None else {'thermal':{'G':{'online':da_initial}}})
    return dict(da=NS(case=case,tables={'units':units}),rt={'units':units.copy()},
                rr=NS(rt=[{} for _ in online],states=[NS(thermal={'G':{'online':rt_initial}})]))


@pytest.mark.parametrize('initial,online,expected',[(False,[1],1),(True,[1],0),(False,[0,1,0,1],2),(True,[1,0,1],1)])
def test_initial_transition_and_later_starts(initial,online,expected):
    out=running_hours(make_result(online,cfg_initial=initial,rt_initial=initial))
    assert out['da']['thermal_starts']==out['rt']['thermal_starts']==expected
    assert out['rt']['thermal_unit_hours']==sum(online)*.25


def test_da_case_override_and_actual_rt_state_are_independent():
    out=running_hours(make_result([1,0,1],cfg_initial=False,da_initial=True,rt_initial=False))
    assert out['da']['thermal_starts']==1
    assert out['rt']['thermal_starts']==2


def test_missing_actual_state_is_not_assumed_off():
    res=make_result([1]);res['rr'].states=[]
    with pytest.raises(ValueError,match='actual RT initial'):
        running_hours(res)
