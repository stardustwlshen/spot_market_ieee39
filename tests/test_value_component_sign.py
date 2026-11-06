"""A constrained stock increment may increase system costs with zero own bids."""
import numpy as np
import pyomo.environ as pyo
import pytest
from test_storage_reserve import battery_case,solve
from test_value_sign import _own_bid_terms
from smm import scuc


def test_negative_stock_value_can_come_from_system_surplus_not_storage_bids():
    c=battery_case(mode='dis',enabled=False)
    c.cfg.base['periods']=2;c.load_bus={2:np.array([100.,100.])}
    c.R_up=np.zeros(2);c.R_dn=np.zeros(2)
    c.cfg.mode['reserve']['down_enabled']=False
    c.terminal=None;c.bids['units']['ES1']['soc_end']=.45
    c.fixed={'u_dis':{('ES1',t):1 for t in range(2)},
             'u_ch':{('ES1',t):0 for t in range(2)}}
    for key,price in [('dis_segments',0.),('ch_segments',-1.)]:
        c.bids['units']['ES1'][key]=[dict(p_start=0.,p_end=100.,price=price)]
    def must_run(m):
        for t in m.T:
            for g in m.G:m.P[g,t].fix(50.)
    m=solve(c,must_run)
    obj0,own0=pyo.value(m.obj),_own_bid_terms(m)
    v=float(scuc.state_values(m)['E']['ES1']['value_native'][0])
    con=m.c_soc['ES1',1];base=con.expr;lhs,rhs=base.args;eps=.5
    objective={};own={}
    for shift in (-eps,eps):
        con.set_value(lhs==rhs+shift);scuc.solve_sced(m)
        objective[shift]=pyo.value(m.obj);own[shift]=_own_bid_terms(m)
        assert pyo.value(m.E['ES1',1])==pytest.approx(90.)
    left=-(obj0-objective[-eps])/eps
    right=-(objective[eps]-obj0)/eps
    system_value=-((objective[eps]-own[eps])-(obj0-own0))/eps
    assert own0==own[-eps]==own[eps]==0.
    assert system_value < 0.
    assert left==pytest.approx(v) and right==pytest.approx(v)
    assert system_value==pytest.approx(-m._md.storage['ES1']['eta_d']*m._pen['balance'])
    con.set_value(base)
