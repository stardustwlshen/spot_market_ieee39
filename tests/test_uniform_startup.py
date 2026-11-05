"""Each thermal unit has its own one-time startup bid, independent of outage class."""
from copy import deepcopy
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import bids, scuc, results
from smm.da import run_da
from test_da_stages import tiny_case


def uniform_case(hours=1.,dt=.25):
    c=tiny_case(reserve=0);c.cfg.mode['startup_cost_mode']='uniform_per_unit';c.cfg.base['dt_h']=dt
    c.cfg.mode['realtime']['interval_min']=60*dt  # Preserve the intended startup-cost/dt comparison on a valid clock.
    for unit,cost in zip(c.cfg.units['thermal'],[100.,175.]):
        unit.update(ut=dt,dt=dt)
        unit['init'].update(online=False,p0=0.,hours=hours)
        c.bids['units'][unit['id']]['startup_cost_yuan']=cost
    c.fixed={'u':{('CHEAP',0):1,('LOCAL',0):1}}
    c.cfg.base['solver']['warm_start']=False
    return c


@pytest.mark.parametrize('hours',[1.,8.,72.,100.])
@pytest.mark.parametrize('dt',[.25,.5])
def test_uniform_fee_heterogeneous_and_not_multiplied_by_dt(hours,dt):
    c=uniform_case(hours,dt)
    r=run_da(c,verbose=False);m=r.model
    assert r.costs['thermal_startup']==pytest.approx(275.,abs=1e-8,rel=0)
    assert pyo.value(scuc.thermal_startup_cost(m))==275.
    assert len(m.GST)==len(m.y)==0
    assert all(v.fixed and pyo.value(v)==0 for v in m.ycold.values())
    assert not hasattr(m,'c_ystate') and not hasattr(m,'c_ystate_lower')
    assert m.c_minup.active and m.c_mindn.active and m.c_rup.active
    assert r.case.bids['units']['CHEAP']['startup_cost_yuan']==100.
    assert r.case.bids['units']['LOCAL']['startup_cost_yuan']==175.


def test_legacy_hot_migration_is_detached_explicit_and_idempotent():
    c=uniform_case()
    for i,unit in enumerate(c.cfg.units['thermal']):
        b=c.bids['units'][unit['id']];b.pop('startup_cost_yuan');b['startup_cost']['hot']=50.+i
    before=deepcopy(c.bids)
    effective=bids.effective_bids(c.cfg,c.bids)
    assert c.bids==before
    assert bids.effective_bids(c.cfg,effective)==effective
    r=run_da(c,verbose=False)
    assert c.bids==before and r.costs['thermal_startup']==101.
    assert r.case.bids['units']['CHEAP']['startup_cost_source']=='legacy_hot_migration'
    assert r.model._md.thermal['CHEAP']['startup_cost_source']=='legacy_hot_migration'


def test_explicit_scalar_does_not_silently_use_legacy_hot():
    c=uniform_case();c.bids['units']['CHEAP']['startup_cost']['hot']=0
    assert bids.resolve_startup_bid(c.cfg,c.bids['units']['CHEAP'])['cost_yuan']==100
    r=run_da(c,verbose=False)
    assert r.costs['thermal_startup']==275.


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf'),True,'100',None])
def test_invalid_uniform_bid_rejected(value):
    c=uniform_case();c.bids['units']['CHEAP']['startup_cost_yuan']=value
    assert any(x.level=='error' for x in bids.validate_bids(c.cfg,c.bids))
    with pytest.raises(ValueError):scuc.build_model(c)


def test_uniform_bid_retains_per_unit_hot_technical_cap():
    c=uniform_case();c.bids['units']['CHEAP']['startup_cost_yuan']=c.cfg.units['thermal'][0]['startup']['hot']+1
    assert any(x.level=='error' and x.rule=='启动费上限' for x in bids.validate_bids(c.cfg,c.bids))


def test_missing_mode_keeps_detailed_state_model():
    c=uniform_case();c.cfg.mode.pop('startup_cost_mode')
    m=scuc.build_model(c)
    assert len(m.GST)==4 and hasattr(m,'c_ystate_lower')
    assert all(d['startup_cost_mode']=='state_dependent' for d in m._md.thermal.values())


def test_uniform_explicit_scalar_allows_missing_legacy_states_but_rejects_typo():
    c=uniform_case()
    c.bids['units']['CHEAP']['startup_cost']={'hot':0.}
    assert not [x for x in bids.validate_bids(c.cfg,c.bids) if x.level=='error']
    c.bids['units']['CHEAP']['startup_cost']['typo']=0.
    assert any(x.level=='error' and x.rule=='启动费状态' for x in bids.validate_bids(c.cfg,c.bids))
