import pytest
from multiday_fixture import micro_day
from smm.input_validation import validate_solver_scheduler

@pytest.mark.parametrize('bad',[None,False,[],4,'bad'])
def test_solver_requires_mapping(bad):
    day,_=micro_day();day.cfg.base['solver']=bad
    with pytest.raises(ValueError,match='configuration object'):validate_solver_scheduler(day.cfg)

@pytest.mark.parametrize('bad',[False,[],0,4,'bad'])
def test_highs_extra_requires_mapping(bad):
    day,_=micro_day();day.cfg.base['solver']['highs_extra']=bad
    with pytest.raises(ValueError,match='object or null'):validate_solver_scheduler(day.cfg)

@pytest.mark.parametrize('valid',[None,{}, {'threads':4}])
def test_empty_or_same_thread_highs_extra_valid(valid):
    day,_=micro_day();day.cfg.base['solver']['highs_extra']=valid
    assert validate_solver_scheduler(day.cfg)==4
