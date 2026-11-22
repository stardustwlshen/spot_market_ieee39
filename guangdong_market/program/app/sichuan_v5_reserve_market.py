"""Sichuan single-segment reserve research clearing from explicit awarded AGC evidence.

Authorities: Sichuan spot V5.0 consultation draft §3.4, and separately issued
2025-12-12 ancillary trading rules arts. 74–86, not an issued V5.0 settlement.
Single-zone preliminary merit order; no AGC optimization, performance/mileage,
actual activations, security certification, or official billing.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

D = Decimal
ZERO = D(0)
HOUR = D(1)
KINDS = frozenset(('hydro','coal','independent_storage','vpp'))
NEW_KINDS = frozenset(('independent_storage','vpp'))
ReserveKind = Literal['hydro','coal','independent_storage','vpp']
ISSUED_RULE_SOURCE = 'https://scb.nea.gov.cn/dtyw/tzgg/202512/P020251222570561450248.pdf'


def _number(value: D, label: str) -> D:
    if not isinstance(value, D) or not value.is_finite() or value < 0:
        raise ValueError(f'{label}: finite nonnegative Decimal required')
    return value


def _identifier(value: str, label: str) -> None:
    if not isinstance(value,str) or not value or value != value.strip():
        raise ValueError(f'{label}: explicit identifier required')


@dataclass(frozen=True, slots=True)
class RegulationAward:
    resource_id: str
    hold_up_mw: D
    hold_down_mw: D
    reserve_credit_mw: D
    evidence_id: str

    def __post_init__(self):
        _identifier(self.resource_id,'resource_id')
        _identifier(self.evidence_id,'prior regulation evidence_id')
        for k in ('hold_up_mw','hold_down_mw','reserve_credit_mw'):
            _number(getattr(self,k),k)
        if self.reserve_credit_mw > self.hold_up_mw+self.hold_down_mw:
            raise ValueError('reserve credit exceeds documented directional regulation capacity')


@dataclass(frozen=True, slots=True)
class ReserveOffer:
    resource_id: str
    kind: ReserveKind
    offer_mw: D
    price_cny_per_mw_h: D | None
    available_mw: D
    day_ahead_min_mw: D
    day_ahead_max_mw: D
    physical_up_limit_mw: D
    eligible_verified: bool
    installed_mw: D | None = None
    seasonal_or_better: bool = False
    ramp_mw_per_min: D | None = None
    charge_rating_mw: D | None = None
    discharge_rating_mw: D | None = None
    certified_duration_h: D | None = None
    available_deliverable_mwh: D | None = None
    response_minutes: D | None = None
    exclusive_mode: Literal['energy','regulation','reserve'] | None = None
    supplemental_eligible: bool = False

    def __post_init__(self):
        _identifier(self.resource_id,'resource_id')
        if not isinstance(self.kind,str) or self.kind not in KINDS:
            raise ValueError('unsupported reserve role')
        for k in ('offer_mw','available_mw','day_ahead_min_mw','day_ahead_max_mw','physical_up_limit_mw'):
            _number(getattr(self,k),k)
        for k in ('price_cny_per_mw_h','installed_mw','ramp_mw_per_min','charge_rating_mw','discharge_rating_mw',
                  'certified_duration_h','available_deliverable_mwh','response_minutes'):
            if getattr(self,k) is not None:
                _number(getattr(self,k),k)
        for k in ('eligible_verified','seasonal_or_better','supplemental_eligible'):
            if type(getattr(self,k)) is not bool:
                raise ValueError(f'{k}: explicit Boolean required')
        if self.day_ahead_max_mw < self.day_ahead_min_mw:
            raise ValueError('day-ahead minimum exceeds maximum')
        if self.price_cny_per_mw_h is None and self.offer_mw:
            raise ValueError('priced bid required for positive offer')
        if self.kind=='independent_storage' and (self.offer_mw or self.supplemental_eligible) and self.exclusive_mode!='reserve':
            raise ValueError('independent storage must expressly select reserve for the segment')
        if self.exclusive_mode is not None and self.exclusive_mode not in ('energy','regulation','reserve'):
            raise ValueError('unsupported exclusive mode')
        if self.supplemental_eligible and self.kind not in ('coal','hydro'):
            raise ValueError('supplemental allocation in this slice only supports hydro/coal')


@dataclass(frozen=True, slots=True)
class ReserveAward:
    regular_mw: D
    supplemental_mw: D
    regular_price_cny_per_mw_h: D | None
    supplemental_price_cny_per_mw_h: D | None

    @property
    def reserve_mw(self) -> D:
        return self.regular_mw+self.supplemental_mw


@dataclass(frozen=True, slots=True)
class ReserveResult:
    net_demand_mw: D
    unmet_mw: D
    ordinary_marginal_price_cny_per_mw_h: D | None
    new_type_cap_mw: D
    awards: dict[str,ReserveAward]
    excluded: dict[str,str]
    energy_holds: dict[str,tuple[D,D,D]]
    energy_excluded_storage: frozenset[str]
    duration_hours: D
    regulation_stage_evidence_id: str
    notes: tuple[str,...]


def _capability(offer: ReserveOffer) -> tuple[D,D,D] | str:
    """Submitted minimum/max and independently evidenced physical capacity."""
    if not offer.eligible_verified:
        return 'registration_or_stable_delivery_not_verified'
    floor = ceiling = ZERO
    physical=offer.physical_up_limit_mw
    if offer.kind=='hydro':
        minimum = D(50) if offer.seasonal_or_better else D(450)
        if offer.installed_mw is None or offer.installed_mw<minimum:
            return 'hydro_installed_capacity_or_seasonal_qualification_not_met'
        floor,ceiling=offer.available_mw*D('.1'),offer.available_mw*D('.3')
    elif offer.kind=='coal':
        if offer.installed_mw is None or offer.installed_mw<D(300) or offer.ramp_mw_per_min is None:
            return 'coal_capacity_or_ramp_qualification_not_met'
        floor,ceiling=offer.ramp_mw_per_min*15,offer.ramp_mw_per_min*30
    elif offer.kind=='independent_storage':
        if (offer.charge_rating_mw is None or offer.charge_rating_mw<5
            or offer.discharge_rating_mw is None or offer.discharge_rating_mw<5
            or offer.certified_duration_h is None or offer.certified_duration_h<HOUR
            or offer.available_deliverable_mwh is None):
            return 'storage_power_duration_or_start_energy_not_verified'
        floor,ceiling=D(5),offer.available_mw
        physical=min(physical,offer.discharge_rating_mw,offer.available_deliverable_mwh/offer.certified_duration_h)
    else:
        if (offer.available_mw<5 or offer.certified_duration_h is None or offer.certified_duration_h<2
            or offer.response_minutes is None or offer.response_minutes>15):
            return 'vpp_reduction_duration_or_response_not_verified'
        floor,ceiling=D(5),offer.available_mw
    return floor,ceiling,min(physical,offer.available_mw)


def clear_reserve(
    gross_demand_mw: D, system_regulation_credit_mw: D,
    offers: Iterable[ReserveOffer], regulation_awards: Iterable[RegulationAward], *,
    duration_hours: D, regulation_stage_evidence_id: str, price_cap_cny_per_mw_h: D = D('126.8'),
    new_type_cap_fraction: D = D('.20'), new_type_cap_reference_mw: D | None = None,
) -> ReserveResult:
    """Clear evidenced inputs for one published segment, preliminarily.

    System and resource regulation credit are explicit to avoid guessing from
    symmetric holds. No rate/settlement is fabricated if no ordinary award.
    Art. 81 supplement requires a reviewed eligible capacity and valid price.
    Default new-type cap base is net segment demand (explicit modeling choice);
    official segment duration and any operator-specific cap base are inputs.
    """
    _identifier(regulation_stage_evidence_id,'completed regulation stage evidence_id')
    for label,value in (
        ('gross_demand_mw',gross_demand_mw),('system_regulation_credit_mw',system_regulation_credit_mw),
        ('duration_hours',duration_hours),('price_cap_cny_per_mw_h',price_cap_cny_per_mw_h),
        ('new_type_cap_fraction',new_type_cap_fraction),
    ):
        _number(value,label)
    if not ZERO<duration_hours<=24 or duration_hours % D('.25') or new_type_cap_fraction>1:
        raise ValueError('duration must be 15-minute aligned within one day and cap fraction <= 1')
    reg:dict[str,RegulationAward]={}
    for item in regulation_awards:
        if not isinstance(item,RegulationAward) or item.resource_id in reg:
            raise ValueError('invalid or duplicate verified regulation award')
        reg[item.resource_id]=item
    if system_regulation_credit_mw > sum((r.reserve_credit_mw for r in reg.values()),ZERO):
        raise ValueError('system regulation credit is not supported by the award evidence')
    inputs:dict[str,ReserveOffer]={}
    for item in offers:
        if not isinstance(item,ReserveOffer) or item.resource_id in inputs:
            raise ValueError('invalid or duplicate reserve offer')
        price=item.price_cny_per_mw_h
        if price is not None and (price>price_cap_cny_per_mw_h or price % D('.1')):
            raise ValueError('reserve price exceeds cap or violates CNY 0.1 per MW-hour tick')
        if item.kind=='independent_storage' and item.resource_id in reg and item.exclusive_mode!='regulation':
            a=reg[item.resource_id]
            if a.reserve_credit_mw or a.hold_up_mw or a.hold_down_mw:
                raise ValueError('independent storage cannot combine regulation with reserve or energy')
        inputs[item.resource_id]=item
    with localcontext(prec=34):
        net=max(ZERO,gross_demand_mw-system_regulation_credit_mw)
        cap_basis = net if new_type_cap_reference_mw is None else _number(new_type_cap_reference_mw,'new_type_cap_reference_mw')
        new_cap=cap_basis*new_type_cap_fraction
        remaining,new_remaining=net,new_cap
        skipped={}
        candidates=[]
        for o in inputs.values():
            cap=_capability(o)
            if isinstance(cap,str):
                skipped[o.resource_id]=cap
                continue
            minimum,maximum,physical=cap
            if o.offer_mw and not minimum<=o.offer_mw<=maximum:
                skipped[o.resource_id]='bid_capacity_outside_type_bounds'
                continue
            a=reg.get(o.resource_id)
            credit=a.reserve_credit_mw if a is not None else ZERO
            directional=(a.hold_up_mw+a.hold_down_mw) if a is not None else ZERO
            # Preserve both physical directional holds without subtracting
            # their sum a second time from the *submitted* reserve bid.
            span=o.day_ahead_max_mw-o.day_ahead_min_mw
            if directional>span:
                raise ValueError(f'{o.resource_id}: directional regulation holds exceed energy span')
            headroom=max(ZERO,min(span-max(credit,directional),physical-(a.hold_up_mw if a else ZERO)))
            effective=max(ZERO,min(o.offer_mw-credit,headroom))
            candidates.append((o,effective,minimum,maximum,headroom,max(ZERO,span-credit)))
        ordinary={}
        marginal=None
        priced=(c for c in candidates if c[1])
        for o,effective,*unused in sorted(priced,key=lambda c:(c[0].price_cny_per_mw_h,-c[0].offer_mw,c[0].resource_id)):
            award=min(effective,remaining,new_remaining) if o.kind in NEW_KINDS else min(effective,remaining)
            if award:
                ordinary[o.resource_id]=award
                marginal=o.price_cny_per_mw_h
                remaining-=award
                if o.kind in NEW_KINDS:
                    new_remaining-=award
        supplement={}
        if remaining and marginal is not None:
            qualifying=[(o,minimum,min(maximum-awarded,headroom-awarded),spec_remaining-awarded)
                        for o,_,minimum,maximum,headroom,spec_remaining in candidates
                        if o.supplemental_eligible for awarded in (ordinary.get(o.resource_id,ZERO),)]
            for o,minimum,limit,_ in sorted(qualifying,key=lambda c:(-c[3],c[0].resource_id)):
                award=min(limit,remaining)
                if award>0 and award>=minimum:
                    supplement[o.resource_id]=award
                    remaining-=award
        awards={k:ReserveAward(ordinary.get(k,ZERO),supplement.get(k,ZERO),
                               marginal if k in ordinary else None, marginal*D('.5') if k in supplement else None)
                for k in sorted(set(ordinary)|set(supplement))}
        # The market adapter must handle new entrants explicitly rather than
        # silently mapping VPP/independent storage into a generator constraint.
        holds={k:(reg[k].hold_up_mw if k in reg else ZERO,reg[k].hold_down_mw if k in reg else ZERO,
                  awards[k].reserve_mw if k in awards else ZERO)
               for k in sorted(set(reg)|set(awards))}
        storage=frozenset(o.resource_id for o in inputs.values() if o.kind=='independent_storage'
                          and o.exclusive_mode in ('regulation','reserve'))
    notes=(
        'single_zone_preliminary_no_network_security_or_regional_requirement',
        'exact_price_and_offer_ties_use_resource_id_research_order',
        'nonnegative_reserve_offers_required_in_this_research_slice',
        'new_type_cap_reference_net_demand_assumption' if new_type_cap_reference_mw is None else 'new_type_cap_reference_supplied',
        'supplement_minimum_applied_to_each_increment_conservative_research_choice',
        'no_regulation_mileage_or_cash_settlement',
    )
    if remaining and marginal is None:
        notes+=('no_ordinary_marginal_price_supplement_not_priced_or_allocated',)
    return ReserveResult(net,remaining,marginal,new_cap,awards,skipped,holds,storage,duration_hours,regulation_stage_evidence_id,notes)
