import pytest
from scoped_kkt import certificate

def fixture(name,cost):
 return dict(variables=[dict(kind='C',lb=None,ub=None)],rows=[],objective=dict(minimize=True,constant=0.,terms=[[0,cost]])),dict(primal=[0.],dual=[],objective=0.,bound=0.),dict(variables=[name],constraints=[])
def test_only_identified_angle_allowed():
 z=certificate(*fixture("angle['6',206]",1.21718e-8));assert z['passed']and z['strict_legacy_1e_8_pass']is False
@pytest.mark.parametrize('name',["angle['6',205]","angle['7',206]",'power[C1,206]'])
def test_no_blanket_missing_bound_relaxation(name):
 with pytest.raises(ValueError):certificate(*fixture(name,1.21718e-8))
def test_no_second_relaxation():
 with pytest.raises(ValueError):certificate(*fixture("angle['6',206]",1.1e-7))
