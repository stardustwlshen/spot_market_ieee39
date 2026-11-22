from dataclasses import replace
from pathlib import Path

import pytest

from app.sichuan_v5_energy import Case, Line, Resource, day_ahead, intraday, realtime
from app.sichuan_v5_intraday import Update, amended_case


def _case():
    return Case((1,2),(Line('L',1,2,.1,60.),),
        (Resource('A',1,'public_coal','thermal',120,20,initial_mw=60,min_up_quarters=4),
         Resource('B',2,'public_coal','thermal',60,70,startup_yuan=20)),
        ({1:0.,2:55.},)*96,({},)*96)


def test_intraday_does_not_look_ahead_and_changes_commitment_only_on_effective_event(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'app'))
    case=_case()
    da=day_ahead(case,time_limit_s=30)
    baseline=realtime(case,da,executed_periods=5,time_limit_s=30)
    result=[]
    actual=realtime(case,da,executed_periods=5,intraday_update=Update(3,1.2),
                    intraday_result=lambda *args:result.append(args),time_limit_s=30)
    assert actual[:3]==baseline[:3]
    assert baseline[3]['online']['B']==0
    assert actual[3]['online']['B']==1
    assert actual[3]['demand'][2]==66
    assert actual[3]['power']['B']==pytest.approx(6)
    assert len(result)==1
    start,carry,rows=result[0]
    assert start==3 and carry['quarter']==2
    assert carry['power']==actual[2]['power'] and carry['online_age']['A']>=4
    assert [r['quarter'] for r in rows]==list(range(3,96))
    assert all('raw_nodal_prices' not in row and 'net_amount' not in row for row in rows)


def test_state_age_at_handoff_is_enforced():
    case=_case()
    original=case.resources[1]
    case=replace(case,resources=(case.resources[0],replace(original,min_up_quarters=4,minimum_mw=1)))
    carry={'quarter':0,'online':{'A':1,'B':1},'online_age':{'A':100,'B':1},
           'power':{'A':54.,'B':1.},'energy':{},'hydro_used':{}}
    plan=intraday(case,1,carry,time_limit_s=30)
    assert [x['online']['B'] for x in plan[:4]]==[1,1,1,0]
    with pytest.raises(ValueError,match='上一时段'):
        intraday(case,2,carry,time_limit_s=30)


def test_invalid_same_or_out_of_bounds_update():
    with pytest.raises(ValueError,match='非零'):
        Update(4,1.)
    with pytest.raises(ValueError,match='0至95'):
        Update(96,1.1)
    change=amended_case(_case(),Update(1,1.1))
    assert change.demand[:2]==({1:0.,2:55.},{1:0.,2:60.5})


def test_impossible_remaining_boundary_is_not_adopted(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'app'))
    case=_case()
    case=replace(case,demand=({1:0.,2:115.},)*96)
    da=day_ahead(case,time_limit_s=30)
    published=[]
    with pytest.raises(ValueError,match='可靠性松弛，不采用'):
        realtime(case,da,executed_periods=3,intraday_update=Update(2,1.2),
                 intraday_result=lambda *args:published.append(args),time_limit_s=30)
    assert published==[]
