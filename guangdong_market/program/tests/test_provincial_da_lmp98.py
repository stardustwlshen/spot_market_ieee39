import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_provincial_da_lmp import extract,build

def witness(n=98):
    names={'variables':[f'power[G,{t}]' for t in range(n)],'constraints':[f"c_balance['1',{t}]"for t in range(n)]}
    q={'variables':[{'kind':'C','lb':0.,'ub':200.}for _ in range(n)],'rows':[{'terms':[[t,1. if t%2 else -1.]],'lb':100. if t%2 else -100.,'ub':100. if t%2 else -100.}for t in range(n)],'objective':{'minimize':True}}
    r={'status':'optimal','dual_valid':True,'primal':[100.]*n,'dual':[75. if t%2 else -75. for t in range(n)]}
    meta=[dict(tick=t,source_day_quarter=src,role='representative',settle=False,state_link_basis='compressed indexed model; not clock-contiguous trajectory',weight_basis='quarter objective coefficient per explicit model',objective_duration_h=.25)for t,src in [(96,32),(97,72)]]
    return q,names,r,meta

def test_rep_prices_never_in_delivery_account():
    q,n,r,meta=witness();z=extract(q,n,r,representative_periods=meta)
    assert set(z['raw_prices_by_tick'])==set(range(96))
    assert set(z['representative_prices_by_tick'])=={96,97}
    assert len(z['hourly_prices'])==24
    assert all(v['1']==300. for v in z['raw_prices_by_tick'].values())
    assert all(v['1']==300. for v in z['representative_prices_by_tick'].values())

def test_rep_missing_declared_weights_rejected():
    q,n,r,meta=witness()
    with pytest.raises(ValueError,match='representative timing'):extract(q,n,r)
    meta[0]['settle']=True
    with pytest.raises(ValueError,match='nonsettling'):build(q,n,r,delta=.1,network_penalty_yuan_mwh=1000.,representative_periods=meta)

def test_prior96_price_stays_compatible():
    q,n,r,meta=witness(96);z=extract(q,n,r)
    assert not z['representative_prices_by_tick']
    assert z['hourly_prices'][23]['1']==300.
