from dataclasses import replace
from decimal import Decimal as D
import pytest

from app.sichuan_v5_agc import AgcOffer, clear_regulation

Z=D(0)


def coal(id,capacity='30',price='2',kh='0.5',**kw):
    args=dict(resource_id=id,kind='coal',declared_mw=D(capacity),mileage_offer_cny_per_mw=None if price is None else D(price),
              verified_kh=None if kh is None else D(kh),kh_evidence_id='published-history',
              independently_verified_eligibility=True,rated_mw=D(500),available_mw=D(500),up_headroom_mw=D(70),
              down_headroom_mw=D(70),energy_span_mw=D(140))
    args.update(kw);return AgcOffer(**args)


def battery(id='B',capacity='50',price='2',kh='.5',**kw):
    args=dict(resource_id=id,kind='independent_storage',declared_mw=D(capacity),mileage_offer_cny_per_mw=D(price),
              verified_kh=D(kh),kh_evidence_id='published-history',independently_verified_eligibility=True,
              rated_mw=D(60),available_mw=D(60),up_headroom_mw=D(60),down_headroom_mw=D(60),energy_span_mw=D(120),
              charge_rating_mw=D(60),discharge_rating_mw=D(60),soc_available_verified=True,storage_mode='regulation')
    args.update(kw);return AgcOffer(**args)


def clear(demand,offers,**kw):
    return clear_regulation(D(demand),offers,stage_evidence_id='synthetic:segment-1',**kw)


def test_rank_raw_is_separate_from_final_capped_price():
    # Raw C/Kh can exceed 5 and remains the merit sort, even though the
    # resulting payable-rate reference gets capped.
    r=clear('100',[coal('A',price='2',kh='.25'),coal('B',price='3.5',kh='.5')])
    assert r.awarded['A'].first_mw==30 and r.awarded['B'].first_mw==30
    assert r.first_raw_marginal_cny_per_mw==8 and r.first_price_cny_per_mw==5
    assert r.unmet_mw==40
    small=clear('30',[coal('A',price='2',kh='.25'),coal('B',price='3.5',kh='.5')],single_share=D(1))
    assert set(small.awarded)=={'B'} and small.awarded['B'].first_price_cny_per_mw==5


def test_same_score_actual_proportion_uses_submitted_capacity():
    r=clear('60',[coal('a','25', price='1',kh='.25'),coal('b','30')],single_share=D('.9'))
    # both available for all 55 MW; equivalent raw score 4
    assert r.awarded['a'].first_mw==25 and r.awarded['b'].first_mw==30
    p=clear('44',[coal('a','25',price='1',kh='.25'),coal('b','30')],single_share=D('.9'))
    assert p.awarded['a'].first_mw==20 and p.awarded['b'].first_mw==24
    assert p.system_regulation_credit_mw==44 and p.unmet_mw==0


def test_active_caps_are_reapportioned_with_30pct_new_type_and_single_caps():
    offers=[battery('X','50'),battery('Y','50'),coal('J','35'),coal('K','35'),coal('L','30')]
    r=clear('100',offers)
    assert r.awarded['X'].first_mw==r.awarded['Y'].first_mw==15
    assert r.awarded['J'].first_mw==r.awarded['K'].first_mw==D('24.5')
    assert r.awarded['L'].first_mw==21
    assert r.system_regulation_credit_mw==100 and r.unmet_mw==0
    # Same score but saturated unit with only 5 MW down: reallocate residual.
    s=clear('100',[replace(offers[0],down_headroom_mw=D(5)),*offers[1:]])
    assert s.awarded['X'].first_mw==5 and s.awarded['Y'].first_mw==25


def test_two_directional_holds_are_disjoint_and_count_once():
    r=clear('100',[coal('A',up_headroom_mw=D(12),down_headroom_mw=D(6),energy_span_mw=D(10))])
    a=r.awarded['A']
    assert a.first_mw==5
    assert a.hold_up_mw==a.hold_down_mw==a.reserve_credit_mw==5
    assert r.system_regulation_credit_mw==5 and a.evidence_id=='synthetic:segment-1:A'


def test_second_round_capped_price_half_and_verified_floor():
    extra=coal('extra','0',price=None,certified_second_round_floor_mw=D(25),second_round_floor_evidence_id='audited-floor')
    r=clear('100',[coal('first',price='2.01'),extra,coal('uncertain','0',price=None)])
    assert r.first_price_cny_per_mw==D('4.02') and r.second_price_cny_per_mw==D('2.01')
    assert r.awarded['extra'].second_mw==25 and r.unmet_mw==45
    assert r.second_round_not_allocated['uncertain']=='no_certified_positive_second_round_floor'


def test_no_first_round_uses_only_audited_latest_price_no_fabrication():
    extra=coal('extra','0',price=None,certified_second_round_floor_mw=D(25),second_round_floor_evidence_id='audited-floor')
    unresolved=clear('100',[extra])
    assert unresolved.second_price_cny_per_mw is None and unresolved.unmet_mw==100 and not unresolved.awarded
    assert unresolved.second_round_not_allocated['extra']=='no_evidenced_first_round_price'
    priced=clear('100',[extra],historical_first_round_price=D('2.9'),historical_price_evidence_id='published-receipt')
    assert priced.second_price_cny_per_mw==D('2.9') and priced.awarded['extra'].second_mw==25
    assert priced.historical_price_evidence_id=='published-receipt'
    assert priced.awarded['extra'].second_round_floor_evidence_id=='audited-floor'
    with pytest.raises(ValueError,match='price_evidence'):
        clear('100',[extra],historical_first_round_price=D(3))


def test_no_fractional_second_round_floor_or_pool_overprocurement():
    extra=coal('extra','0',price=None,certified_second_round_floor_mw=D(25),second_round_floor_evidence_id='audited-floor')
    r=clear('80',[extra,replace(extra,resource_id='mismatch',certified_second_round_floor_mw=D(24))],
            historical_first_round_price=D(3),historical_price_evidence_id='evidence')
    assert r.unmet_mw==80
    assert r.second_round_not_allocated['mismatch']=='floor_disagrees_with_issued_capacity_formula'
    assert r.second_round_not_allocated['extra']=='fixed_floor_exceeds_available_headroom_cap_or_shortage'


@pytest.mark.parametrize('quote', ['5.01','4.001'])
def test_bid_price_cap_tick(quote):
    with pytest.raises(ValueError,match='tick'):
        clear('100',[coal('A',price=quote)])


def test_eligibility_and_off_tick_quantity():
    r=clear('100',[coal('A',kh=None),battery('B',storage_mode='reserve'),coal('C',capacity='24')])
    assert r.unmet_mw==100 and set(r.excluded)=={'A','B','C'}
    with pytest.raises(ValueError,match='whole-MW'):
        clear('100',[coal('A',capacity='25.5')])
    with pytest.raises(ValueError,match='duplicate'):
        clear('100',[coal('A'),coal('A')])
