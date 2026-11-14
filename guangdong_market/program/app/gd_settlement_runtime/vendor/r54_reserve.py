"""Article 33/35 same-price thermal reserve arithmetic, no market/solver/bill."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D
from typing import Optional, Tuple


def need(ok, why):
    if not ok: raise ValueError(why)
def number(x):
    need(isinstance(x,D) and x.is_finite() and x>=0,'nonnegative finite Decimal required')
    return x
@dataclass(frozen=True)
class Scope:
    start: datetime
    end: datetime
    evidence: str
    channel: str # actual_inputs | synthetic_fixture
    market_state: Optional[str] # not_started | suspended | trading
    current_rule_verified: Optional[bool]
    current_parameter_verified: Optional[bool]
    pool_complete: Optional[bool]
    lower_requirement_mw: Optional[D]
    requirement_evidence: Optional[str]
    rate_yuan_per_MWh_service: D = D('30') # published GD R6 baseline, not dynamic certification
    province: str = 'Guangdong'
@dataclass(frozen=True)
class Unit:
    resource: str
    technology: str
    rated_mw: D
    declared_upper_mw: D
    actual_mw: D
    evidence: str
    designated: Optional[bool]
    qualified: Optional[bool]
    own_cause_daily_failure: Optional[bool]
    nonself_upper_reduction: Optional[bool]
    nuclear_normal_lower_mw: Optional[D] = None


def calculate(scope: Scope, units: Tuple[Unit,...]):
    need(scope.province=='Guangdong','province-only scope')
    need(scope.channel in ('actual_inputs','synthetic_fixture'),'explicit evidence channel')
    need(scope.evidence and scope.start.utcoffset() is not None and scope.end.utcoffset() is not None and scope.end>scope.start,'aware interval and evidence')
    need(type(units) is tuple and units and len({u.resource for u in units})==len(units),'nonempty unique whole-pool units')
    number(scope.rate_yuan_per_MWh_service)
    need(scope.market_state in (None,'not_started','suspended','trading'),'unknown market state')
    for f in ('current_rule_verified','current_parameter_verified','pool_complete'):
        need(getattr(scope,f) is None or type(getattr(scope,f)) is bool,'strict scope flags')
    out=dict(formal_bill=None,payable=False,native_calls=0,channel=scope.channel,
             compensation_price_basis='administrative R6, never reserve dual or independent auction',
             service_unit='MW*h reserve supply, not generated electric MWh',rows=[],total_CNY=None,monthly_allocation=None)
    if scope.market_state=='trading':return dict(out,status='excluded_same_service_market_no_duplicate_compensation')
    missing=[]
    if scope.market_state is None:missing.append('same-service market operation state')
    if scope.current_rule_verified is not True:missing.append('applicable rule period verification')
    if scope.current_parameter_verified is not True:missing.append('current R6 parameter verification')
    if scope.pool_complete is not True:missing.append('complete compensation pool evidence')
    if scope.lower_requirement_mw is None or not scope.requirement_evidence:missing.append('actual dispatch lower requirement and evidence; not model400+400')
    else:number(scope.lower_requirement_mw)
    h=D(str((scope.end-scope.start).total_seconds()))/D(3600)
    for u in units:
        need(u.resource and u.evidence,'resource identity and source required')
        for f in ('designated','qualified','own_cause_daily_failure','nonself_upper_reduction'):
            v=getattr(u,f);need(v is None or type(v) is bool,'strict unit flags')
            if v is None:missing.append(u.resource+':'+f)
        for v in (u.rated_mw,u.declared_upper_mw,u.actual_mw):number(v)
        need(u.rated_mw>0 and u.declared_upper_mw<=u.rated_mw and u.actual_mw<=u.rated_mw,'physical nameplate domain')
        if u.technology not in ('coal','biomass','gas','oil','nuclear'):
            missing.append(u.resource+': unsupported category/heterogeneous pool; no invented VRE/BESS credit');continue
        if u.technology=='nuclear' and u.nuclear_normal_lower_mw is None:
            missing.append(u.resource+': agreement normal lower output');continue
        upper=u.rated_mw if u.nonself_upper_reduction is True else u.declared_upper_mw
        floor=D(0)
        if u.technology in ('coal','biomass'):floor=u.rated_mw*D('.5')
        if u.technology=='nuclear':
            floor=number(u.nuclear_normal_lower_mw);need(floor<=u.rated_mw,'nuclear lower domain')
        excluded=u.designated is False or u.qualified is False or u.own_cause_daily_failure is True
        unknown=any(getattr(u,f) is None for f in ('designated','qualified','own_cause_daily_failure','nonself_upper_reduction'))
        supply=None if unknown else D(0) if excluded else max(D(0),upper-max(u.actual_mw,floor))*h
        out['rows'].append(dict(resource=u.resource,service_MWh=supply,excluded=excluded,amount_CNY=None,evidence=u.evidence))
    if missing:return dict(out,status='missing',missing=missing)
    total=sum((r['service_MWh'] for r in out['rows']),D(0));cap=scope.lower_requirement_mw*h
    amount=scope.rate_yuan_per_MWh_service*min(total,cap)
    for r in out['rows']:r['amount_CNY']=amount*r['service_MWh']/total if total else D(0)
    return dict(out,status='synthetic_arithmetic' if scope.channel=='synthetic_fixture' else 'sourced_component_arithmetic_not_bill',total_service_MWh=total,cap_service_MWh=cap,total_CNY=amount,allocation_basis='same-R6 full-pool service supply proportion',rounding_policy='unrounded Decimal, no currency rounding allocation invented')
