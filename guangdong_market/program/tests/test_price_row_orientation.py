import ast,pathlib,pytest
p=pathlib.Path(__file__).parents[1]/'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/review.py'
fn=next(n for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='nodal_prices')
def need(ok,msg):
    if not ok:raise ValueError(msg)
ns={'need':need};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(p),'exec'),ns)
@pytest.mark.parametrize('direction',[-1.,1.])
def test_price_invariant_under_equality_reversal(direction):
    q={'rows':[dict(lb=direction*100,ub=direction*100,terms=[[0,direction]])]};r={'dual':[direction*90]};n={'variables':["vre['1',0]"],'constraints':["balance['1',0]"]}
    prices,proof=ns['nodal_prices'](q,r,n);assert prices['1']==360;assert proof['1']['load_rhs_derivative']==direction

def test_missing_anchor_rejected():
    with pytest.raises(ValueError):ns['nodal_prices']({'rows':[dict(lb=0,ub=0,terms=[])]},{'dual':[1]},{'variables':[],'constraints':["balance['1',0]"]})
