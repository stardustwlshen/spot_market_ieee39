"""Single-segment preliminary Sichuan AGC clearing (no real mileage ledger).

Issued 2025-12-12 Sichuan ancillary trading rules, arts. 53–65; the spot V5.0
consultation draft §§3.4/7.2 supplies the sequencing boundary, not a new tariff.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_FLOOR, localcontext
from fractions import Fraction as F
from typing import Literal

SOURCE = 'https://scb.nea.gov.cn/dtyw/tzgg/202512/P020251222570561450248.pdf'
Z = D(0)
KINDS = frozenset(('hydro','coal','independent_storage','vpp'))
NEW = frozenset(('independent_storage','vpp'))
BOUNDS = {'hydro':(D('.10'),D('.20')), 'coal':(D('.05'),D('.075')),
          'independent_storage':(Z,D(1)), 'vpp':(Z,D('.20'))}


def _decimal(x, label):
    if not isinstance(x,D) or not x.is_finite() or x<0:
        raise ValueError(f'{label}: finite nonnegative Decimal required')
    return x


def _id(x,label):
    if not isinstance(x,str) or not x or x.strip()!=x:
        raise ValueError(f'{label}: explicit identifier required')


def _out(x:F) -> D:
    # Downward rounding prevents Decimal conversion from breaking a physical cap.
    with localcontext(prec=34,rounding=ROUND_FLOOR):
        return D(x.numerator)/D(x.denominator)


@dataclass(frozen=True, slots=True)
class AgcOffer:
    resource_id: str
    kind: Literal['hydro','coal','independent_storage','vpp']
    declared_mw: D
    mileage_offer_cny_per_mw: D | None
    verified_kh: D | None
    kh_evidence_id: str | None
    independently_verified_eligibility: bool
    rated_mw: D
    available_mw: D
    up_headroom_mw: D
    down_headroom_mw: D
    energy_span_mw: D
    certified_second_round_floor_mw: D | None = None
    second_round_floor_evidence_id: str | None = None
    largest_hydro_machine_mw: D | None = None
    charge_rating_mw: D | None = None
    discharge_rating_mw: D | None = None
    soc_available_verified: bool = False
    response_minutes: D | None = None
    adjustment_per_min_mw: D | None = None
    daily_actions: int | None = None
    storage_mode: Literal['energy','regulation','reserve'] | None = None

    def __post_init__(self):
        _id(self.resource_id,'resource_id')
        if not isinstance(self.kind,str) or self.kind not in KINDS:
            raise ValueError('unknown technology')
        for k in ('declared_mw','rated_mw','available_mw','up_headroom_mw','down_headroom_mw','energy_span_mw'):
            _decimal(getattr(self,k),k)
        for k in ('mileage_offer_cny_per_mw','verified_kh','certified_second_round_floor_mw',
                  'largest_hydro_machine_mw','charge_rating_mw','discharge_rating_mw',
                  'response_minutes','adjustment_per_min_mw'):
            if getattr(self,k) is not None:
                _decimal(getattr(self,k),k)
        for k in ('kh_evidence_id','second_round_floor_evidence_id'):
            if getattr(self,k) is not None:
                _id(getattr(self,k),k)
        for k in ('independently_verified_eligibility','soc_available_verified'):
            if type(getattr(self,k)) is not bool:
                raise ValueError(f'{k}: explicit Boolean required')
        if self.daily_actions is not None and (type(self.daily_actions) is not int or self.daily_actions<0):
            raise ValueError('nonnegative integer daily_actions required')
        if self.storage_mode not in (None,'energy','regulation','reserve'):
            raise ValueError('unknown storage product')


@dataclass(frozen=True, slots=True)
class AgcAward:
    first_mw: D
    second_mw: D
    first_price_cny_per_mw: D | None
    second_price_cny_per_mw: D | None
    hold_up_mw: D
    hold_down_mw: D
    reserve_credit_mw: D
    evidence_id: str
    kh_evidence_id: str
    second_round_floor_evidence_id: str | None


@dataclass(frozen=True, slots=True)
class RegulationResult:
    demand_mw: D
    unmet_mw: D
    first_raw_marginal_cny_per_mw: D | None
    first_price_cny_per_mw: D | None
    second_price_cny_per_mw: D | None
    second_price_source: str | None
    historical_price_evidence_id: str | None
    system_regulation_credit_mw: D
    awarded: dict[str,AgcAward]
    excluded: dict[str,str]
    second_round_not_allocated: dict[str,str]
    stage_evidence_id: str
    notes: tuple[str,...]


def _eligible(o:AgcOffer, min_kh:D):
    if not o.independently_verified_eligibility:
        return 'registration_interconnection_or_agc_qualification_unverified'
    if o.verified_kh is None or not o.kh_evidence_id or o.verified_kh<min_kh or not o.verified_kh:
        return 'history_performance_missing_or_below_threshold'
    if o.kind=='hydro' and (o.largest_hydro_machine_mw is None or o.largest_hydro_machine_mw<40):
        return 'hydro_machine_below_40mw_or_unverified'
    if o.kind=='coal' and o.rated_mw<300:
        return 'coal_station_below_300mw'
    if o.kind=='independent_storage':
        if o.storage_mode!='regulation':
            return 'storage_not_declared_exclusively_for_agc'
        if (not o.soc_available_verified or o.charge_rating_mw is None or o.charge_rating_mw<5
            or o.discharge_rating_mw is None or o.discharge_rating_mw<5):
            return 'storage_power_or_soc_qualification_unverified'
    if o.kind=='vpp' and (o.available_mw<5 or o.response_minutes is None or o.response_minutes>1
                         or o.adjustment_per_min_mw is None or F(o.adjustment_per_min_mw)<F(o.available_mw)/10
                         or o.daily_actions is None or o.daily_actions<50):
        return 'vpp_capacity_response_ramp_or_action_count_unverified'
    return None


def _tie_fill(group, available:F, newcomer:F):
    """Weighted max-min fill, exactly rational; cap events suspend saturated members."""
    got={key:F(0) for key in group}
    remaining=available
    newcomers=newcomer
    while remaining:
        active={k:(w,cap,is_new) for k,(w,cap,is_new) in group.items()
                if got[k]<cap and (not is_new or newcomers)}
        if not active:
            break
        total=sum(w for w,_,_ in active.values())
        new_weight=sum((w for w,_,n in active.values() if n),F(0))
        step=min(remaining/total,*((cap-got[k])/w for k,(w,cap,_) in active.items()))
        if new_weight:
            step=min(step,newcomers/new_weight)
        if not step:
            raise ArithmeticError('invalid zero allocation step')
        for k,(w,_,is_new) in active.items():
            inc=w*step
            got[k]+=inc
            remaining-=inc
            if is_new:
                newcomers-=inc
    return got,remaining,newcomers


def clear_regulation(
    demand_mw:D, offers:Iterable[AgcOffer], *, stage_evidence_id:str,
    historical_first_round_price:D|None=None, historical_price_evidence_id:str|None=None,
    bid_cap:D=D('5'), clear_cap:D=D('5'), minimum_kh:D=Z,
    single_share:D=D('.30'), new_share:D=D('.30'),
) -> RegulationResult:
    """Clear one reviewed baseline segment, without claiming AC/network safety.

    A single awarded capacity is conservatively held in each direction, only
    when both headrooms and their shared span support it; count it once for
    reserve procurement, never the sum of the two signed directions.
    """
    _id(stage_evidence_id,'stage_evidence_id')
    for k,v in dict(demand_mw=demand_mw,bid_cap=bid_cap,clear_cap=clear_cap,minimum_kh=minimum_kh,
                    single_share=single_share,new_share=new_share).items():
        _decimal(v,k)
    if any(v>D(1) for v in (single_share,new_share)):
        raise ValueError('shares may not exceed 1')
    if historical_first_round_price is not None:
        _decimal(historical_first_round_price,'historical_first_round_price')
        _id(historical_price_evidence_id,'historical_price_evidence_id')
        if historical_first_round_price>clear_cap:
            raise ValueError('historical price above configured clearing cap')
    elif historical_price_evidence_id is not None:
        raise ValueError('historical price evidence without price')
    resources,excluded,caps,floors,rank={},{},{},{},{}
    for o in offers:
        if not isinstance(o,AgcOffer) or o.resource_id in resources:
            raise ValueError('invalid or duplicate AGC offer')
        k=o.resource_id; resources[k]=o
        quote=o.mileage_offer_cny_per_mw
        if quote is not None and (quote>bid_cap or F(quote)*100%1):
            raise ValueError('mileage quote outside configured bounds or off 0.01 CNY/MW tick')
        if F(o.declared_mw)%1:
            raise ValueError('declared MW must be in whole-MW bid increments')
        issue=_eligible(o,minimum_kh)
        if issue:
            excluded[k]=issue
            continue
        low,high=(F(o.available_mw)*F(p) for p in BOUNDS[o.kind])
        floors[k]=(low,high)
        caps[k]=min(F(demand_mw)*F(single_share),F(o.available_mw),F(o.up_headroom_mw),
                    F(o.down_headroom_mw),F(o.energy_span_mw)/2)
        if o.declared_mw:
            if quote is None:
                excluded[k]='positive_quantity_without_mileage_quote'; continue
            if not low<=F(o.declared_mw)<=high:
                excluded[k]='declared_capacity_outside_technology_bounds'; continue
            if not caps[k]:
                excluded[k]='no_disjoint_directional_headroom'; continue
            rank[k]=F(quote)/F(o.verified_kh)
    remaining=F(demand_mw); newcomer=remaining*F(new_share)
    one,two={},{}
    raw=None
    for score in sorted(set(rank.values())):
        if not remaining: break
        group={k:(F(resources[k].declared_mw),min(caps[k],F(resources[k].declared_mw)),resources[k].kind in NEW)
               for k,x in rank.items() if x==score}
        awards,remaining,newcomer=_tie_fill(group,remaining,newcomer)
        for k,value in awards.items():
            if value:
                one[k]=value;raw=score
    price=None if raw is None else min(raw,F(clear_cap))
    # No invented substitute: if no first-round winner, the issued rule uses
    # the latest evidenced first-round price instead of 0.5 times a guess.
    possible_second=(price/2 if price is not None else
                     F(historical_first_round_price) if historical_first_round_price is not None else None)
    not_allocated={}
    if remaining:
        candidates=[resources[k] for k in floors if k not in one]
        candidates.sort(key=lambda o:(-o.verified_kh,-o.rated_mw,o.resource_id))
        for o in candidates:
            if not remaining: break
            k=o.resource_id
            floor=o.certified_second_round_floor_mw
            if possible_second is None:
                not_allocated[k]='no_evidenced_first_round_price';continue
            if floor is None or not o.second_round_floor_evidence_id or not floor:
                not_allocated[k]='no_certified_positive_second_round_floor';continue
            f=F(floor);low,high=floors[k]
            if f!=low or not low<=f<=high:
                not_allocated[k]='floor_disagrees_with_issued_capacity_formula';continue
            cap=min(caps[k],newcomer) if o.kind in NEW else caps[k]
            if f>cap or f>remaining:
                not_allocated[k]='fixed_floor_exceeds_available_headroom_cap_or_shortage';continue
            if k in excluded and excluded[k]!='positive_quantity_without_mileage_quote':
                not_allocated[k]='invalid_first_round_capacity_cannot_be_reused';continue
            two[k]=f;remaining-=f
            if o.kind in NEW:newcomer-=f
    published={}
    with localcontext(prec=34,rounding=ROUND_FLOOR):
        for k in sorted(set(one)|set(two)):
            first=_out(one.get(k,F(0)));second=_out(two.get(k,F(0)))
            mw=first+second
            published[k]=AgcAward(first,second,_out(price) if k in one else None,
                                 _out(possible_second) if k in two else None,
                                 mw,mw,mw,stage_evidence_id+':'+k,resources[k].kh_evidence_id,
                                 resources[k].second_round_floor_evidence_id if k in two else None)
        credit=sum((a.reserve_credit_mw for a in published.values()),Z)
        unserved=max(Z,demand_mw-credit)
    second_source=None if not two else 'half_current_capped_first_round' if price is not None else 'latest_evidenced_first_round'
    return RegulationResult(demand_mw,unserved,_out(raw) if raw is not None else None,
                            _out(price) if price is not None else None,
                            _out(possible_second) if two else None,second_source,
                            historical_price_evidence_id if second_source=='latest_evidenced_first_round' else None,
                            credit,published,excluded,not_allocated,
                            stage_evidence_id,(
        'preliminary_single_zone_no_section_network_or_required_award_checks',
        'external_eligibility_evidence_covers_art53_K_threshold_distinct_from_historical_Kh',
        'symmetric_research_holds_capped_by_independent_up_down_and_shared_span_credit_counted_once',
        'quantity_bid_is_integer_mw_awards_proportional_not_rounded_to_mw',
        'second_round_floor_skipped_if_it_would_overprocure_research_assumption',
        'same_kh_and_rated_mw_overall_ties_second_round_ordered_by_id',
        'no_mileage_or_cash_settlement',
    ))
