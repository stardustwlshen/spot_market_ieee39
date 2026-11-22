from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.sichuan_v5_reserve_market import RegulationAward, ReserveOffer, clear_reserve

Z = D(0)


def coal(id='C', offer='60', rate='70.1', **kw):
    data=dict(resource_id=id,kind='coal',offer_mw=D(offer),price_cny_per_mw_h=None if rate is None else D(rate),
              available_mw=D(330),day_ahead_min_mw=D(100),day_ahead_max_mw=D(330),
              physical_up_limit_mw=D(100),eligible_verified=True,installed_mw=D(330),ramp_mw_per_min=D(3))
    data.update(kw)
    return ReserveOffer(**data)


def storage(id='B', offer='20', mode='reserve', **kw):
    data=dict(resource_id=id,kind='independent_storage',offer_mw=D(offer),price_cny_per_mw_h=D(20),
              available_mw=D(50),day_ahead_min_mw=Z,day_ahead_max_mw=D(50),
              physical_up_limit_mw=D(50),eligible_verified=True,
              charge_rating_mw=D(50),discharge_rating_mw=D(50),certified_duration_h=D(1),
              available_deliverable_mwh=D(50),exclusive_mode=mode)
    data.update(kw)
    return ReserveOffer(**data)


def clear(demand,bids,reg=(),credit='0',**kw):
    return clear_reserve(D(demand),D(credit),bids,reg,duration_hours=D('4'),regulation_stage_evidence_id='agc-day-a',
                         **kw)


def test_approved_agc_credit_applied_once_and_energy_holds_preserved():
    award=RegulationAward('A',D(20),D(20),D(20),'award-1')
    r=clear('100',[coal('A','60','50.1'),coal('B','60','80.2')],[award],credit='20')
    assert r.net_demand_mw==80 and r.unmet_mw==0
    assert r.awards['A'].regular_mw==40  # not 20: don't subtract explicit AGC credit twice
    assert r.awards['B'].regular_mw==40
    assert r.ordinary_marginal_price_cny_per_mw_h==D('80.2')
    assert r.awards['A'].regular_price_cny_per_mw_h==D('80.2')
    assert r.energy_holds['A']==(D(20),D(20),D(40))
    assert r.regulation_stage_evidence_id=='agc-day-a'
    assert r.duration_hours==4


def test_market_merit_order_and_offer_tiebreak():
    r=clear('65',[coal('A',offer='60',rate='50'),coal('B',offer='80',rate='50')])
    assert set(r.awards)=={'B'} and r.awards['B'].reserve_mw==65


def test_new_types_cap_and_soc_duration():
    b=storage(available_deliverable_mwh=D(9))
    r=clear('60',[b,storage('B2','25'),coal()])
    assert r.new_type_cap_mw==12
    assert r.awards['B2'].regular_mw==12  # equal price -> largest declared bid first
    assert 'B' not in r.awards and r.awards['C'].regular_mw==48
    assert r.energy_excluded_storage=={'B','B2'}
    low=clear('30',[b],new_type_cap_reference_mw=D(50))
    assert low.new_type_cap_mw==10 and low.awards['B'].reserve_mw==9
    assert low.unmet_mw==21


def test_supplemental_ordinary_margin_unchanged_half_rate_and_remaining_span():
    standard=coal('A','60','126.8')
    supplementary=coal('B','0',None,supplemental_eligible=True,
                       day_ahead_max_mw=D(150),physical_up_limit_mw=D(50))
    short=clear('150',[standard,supplementary])
    assert short.awards['B'].supplemental_mw==50 and short.unmet_mw==40
    assert short.awards['B'].supplemental_price_cny_per_mw_h==D('63.4')
    assert short.ordinary_marginal_price_cny_per_mw_h==D('126.8')
    assert short.energy_holds['B']==(Z,Z,D(50))


def test_no_price_or_award_for_unsupplied_no_ordinary_margin():
    r=clear('80',[coal('B','0',None,supplemental_eligible=True)])
    assert not r.awards and not r.energy_holds
    assert r.unmet_mw==80 and r.ordinary_marginal_price_cny_per_mw_h is None
    assert any('not_priced' in n for n in r.notes)


@pytest.mark.parametrize('p',['126.9','126.81','70.01'])
def test_reject_bad_bid_caps_and_ticks(p):
    with pytest.raises(ValueError,match='tick'):
        clear('10',[coal(rate=p)])


def test_fail_closed_unverified_eligibility_and_bid_floor():
    r=clear('10',[coal('unverified',eligible_verified=False),coal('small',installed_mw=D(299)),
                  coal('too_little',offer='44'),storage('d',certified_duration_h=D('.5'))])
    assert not r.awards and len(r.excluded)==4 and r.unmet_mw==10
    assert r.excluded['too_little']=='bid_capacity_outside_type_bounds'


def test_regulation_stage_and_storage_exclusivity_and_evidence_are_required():
    with pytest.raises(ValueError,match='completed regulation'):
        clear_reserve(D(30),Z,[],[],duration_hours=D(2),regulation_stage_evidence_id='')
    with pytest.raises(ValueError,match='not supported'):
        clear('30',[],credit='10')
    award=RegulationAward('B',D(5),D(5),D(5),'previous-award')
    with pytest.raises(ValueError,match='cannot combine'):
        clear('30',[storage()],reg=[award],credit='5')
    with pytest.raises(ValueError,match='expressly select'):
        storage(mode='energy')
    with pytest.raises(ValueError,match='exceeds'):
        RegulationAward('A',D(1),D(1),D(3),'too-large')


def test_hydro_seasonal_and_vpp_response_thresholds():
    hydro=ReserveOffer('H','hydro',D(20),D(50),D(100),Z,D(100),D(50),True,
                       installed_mw=D(50),seasonal_or_better=True)
    vpp=ReserveOffer('V','vpp',D(15),D(46),D(40),Z,D(40),D(30),True,
                     certified_duration_h=D(2),response_minutes=D(15))
    r=clear('40',[hydro,vpp,replace(hydro,resource_id='nonseasonal',seasonal_or_better=False),
                  replace(vpp,resource_id='slow',response_minutes=D(16))])
    assert r.awards['H'].reserve_mw==20 and r.awards['V'].reserve_mw==8
    assert r.unmet_mw==12
    assert set(r.excluded)=={'nonseasonal','slow'}


def test_supplement_price_does_not_round_to_offers_tick_or_turn_zero_into_missing():
    supplement = coal('B','0',None,supplemental_eligible=True)
    exact = clear('110',[coal('A','60','70.1'),supplement])
    assert exact.awards['B'].supplemental_price_cny_per_mw_h == D('35.05')
    zero = clear('110',[coal('A','60','0'),supplement])
    assert zero.ordinary_marginal_price_cny_per_mw_h == Z
    assert zero.awards['B'].supplemental_price_cny_per_mw_h == Z
    assert zero.unmet_mw == Z
