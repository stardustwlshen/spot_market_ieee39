"""Secure and economically equivalent DA equal-offer allocation, not RT policy."""
from dataclasses import replace

import pytest

from app.sichuan_v5_energy import Case, Line, Resource, day_ahead, realtime


def two_node(transfer=60., clean_a_price=200., rename=False):
    a,b=('Z','A') if rename else ('A','B')
    resources = (
        Resource(a,1,'central_renewable','renewable',120.,clean_a_price,
                 segments=((60.,clean_a_price),(60.,500.))),
        Resource(b,2,'central_renewable','renewable',50.,200.,
                 segments=((40.,200.),(10.,500.))),
        Resource('C',2,'public_coal','thermal',80.,200.,segments=((80.,200.),)),
    )
    if rename:
        resources=tuple(reversed(resources))
    return (Case((1,2),(Line('T',1,2,.1,transfer),),resources,
                 ({1:0.,2:80.},)*96,({a:60.,b:40.},)*96),a,b)


@pytest.mark.parametrize('rename',(False,True))
@pytest.mark.parametrize('transfer,expected',[(60.,(48.,32.,0.)),(30.,(30.,40.,10.))])
def test_feasible_proportions_or_clean_priority_under_line_constraint(transfer,expected,rename):
    c,a,b=two_node(transfer=transfer,rename=rename)
    old=day_ahead(c,time_limit_s=30)
    rows=day_ahead(c,time_limit_s=30,equal_offer_priority=True)
    for base,row in zip(old,rows):
        assert [row['power'][x] for x in (a,b,'C')]==pytest.approx(expected,abs=1e-6)
        assert row['raw_nodal_prices']==base['raw_nodal_prices']
        assert row['online']==base['online']
        assert row['flows']['T']==pytest.approx(expected[0],abs=1e-6)
        assert not any(row['shed'].values())
        evidence=row['equal_offer_dispatch']
        assert evidence['pricing_source']=='original_fixed_commitment_lp'
        assert abs(evidence['whole_day_primary_cost_difference_cny'])<=1e-5
        group=next(g for g in evidence['share_groups'] if g['price']==200.)
        assert {s['unit']:s['width_mw'] for s in group['segments']}=={a:60.,b:40.}


def test_one_cent_price_order_cannot_be_overturned():
    c,a,b=two_node(clean_a_price=200.01)
    fixed=[{"online":{"C":1}} for _ in range(96)]
    original=day_ahead(c,time_limit_s=30,fixed_commitment=fixed)
    rows=day_ahead(c,time_limit_s=30,fixed_commitment=fixed,equal_offer_priority=True)
    assert all(row['power'][a]==pytest.approx(0.,abs=1e-6) for row in rows)
    assert all(row['raw_nodal_prices']==base['raw_nodal_prices'] for row,base in zip(rows,original))


def test_rt_does_not_apply_tie_or_emit_da_policy():
    c,_,_=two_node()
    plain=day_ahead(c,time_limit_s=30)
    tied=day_ahead(c,time_limit_s=30,equal_offer_priority=True)
    r=realtime(c,tied,time_limit_s=30,executed_periods=1)
    assert r==realtime(c,plain,time_limit_s=30,executed_periods=1)
    assert 'equal_offer_dispatch' not in r[0]


def test_distinct_renewable_roles_do_not_share_segment_quotas():
    c,a,b=two_node()
    c=replace(c, resources=tuple(replace(r,role='competitive_pv') if r.id==b else r for r in c.resources))
    rows=day_ahead(c,time_limit_s=30,equal_offer_priority=True)
    assert all(row['power']['C']==pytest.approx(0.,abs=1e-6) for row in rows)
    assert all(not row['equal_offer_dispatch']['share_groups'] for row in rows)
