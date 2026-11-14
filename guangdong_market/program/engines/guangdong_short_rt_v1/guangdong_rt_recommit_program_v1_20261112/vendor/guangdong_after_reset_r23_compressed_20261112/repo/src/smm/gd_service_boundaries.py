"""Already-established AGC results to provincial energy bounds, not an auction.

Only binding intervals are inherited. Later lookahead capacity is deliberately
not promoted to a formal award. Physical and adjusted envelopes are distinct.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from numbers import Real
import math


def _aware(x, name):
    if not isinstance(x, datetime) or x.tzinfo is None or x.utcoffset() is None:
        raise ValueError(f"{name}: timezone-aware datetime required")
    return x


def _number(x, name, nonnegative=False):
    if isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(float(x)):
        raise ValueError(f"{name}: finite number required")
    if nonnegative and x < 0:
        raise ValueError(f"{name}: nonnegative capacity required")
    return float(x)


def _text(x, name):
    if not isinstance(x,str) or not x.strip():
        raise ValueError(f"{name}: explicit source required")


@dataclass(frozen=True)
class PhysicalEnvelope:
    lower_mw: dict[tuple[str,int],float]
    upper_mw: dict[tuple[str,int],float]
    online: dict[tuple[str,int],bool]
    source: str
    capacity_basis: str


@dataclass(frozen=True)
class RegulationLookaheadSlot:
    start: datetime
    end: datetime
    capacity_mw: dict[str,float]


@dataclass(frozen=True)
class RegulationCapacityResult:
    result_id: str
    stage: str
    published_at: datetime
    binding_start: datetime
    binding_end: datetime
    lookahead_end: datetime
    capacity_mw: dict[str,float]
    source: str
    lookahead_slots: tuple[RegulationLookaheadSlot,...] = ()


@dataclass(frozen=True)
class AdjustedEnvelope:
    lower_mw: dict[tuple[str,int],float]
    upper_mw: dict[tuple[str,int],float]
    physical_lower_mw: dict[tuple[str,int],float]
    physical_upper_mw: dict[tuple[str,int],float]
    reserved_mw: dict[tuple[str,int],float]
    result_ids: dict[tuple[str,int],str]
    audit: dict


def apply_regulation_boundaries(physical, *, energy_stage, intervals,
                                information_cutoff, results, allow_nonbinding_lookahead=False):
    """Apply Pmax-Q and Pmin+Q exactly once, with explicit complete coverage.

    Upstream supplies availability-approved raw bounds and already-valid awards.
    This does not verify source authenticity, eligibility, response, or reserves.
    """
    if type(physical) is not PhysicalEnvelope:
        raise ValueError("raw PhysicalEnvelope required; adjusted bounds cannot be deducted twice")
    if energy_stage not in ('DA_SCED','RT_SCED'):
        raise ValueError("service results apply only to explicit DA_SCED/RT_SCED")
    if type(allow_nonbinding_lookahead) is not bool:
        raise ValueError("allow_nonbinding_lookahead must be explicit Boolean")
    if energy_stage!='RT_SCED' and allow_nonbinding_lookahead:
        raise ValueError("formal AGC lookahead is only an RT rolling-horizon boundary")
    _text(physical.source,'physical source')
    if physical.capacity_basis!='raw_availability_adjusted_before_services':
        raise ValueError("raw availability-adjusted before-service capacity basis required")
    cutoff=_aware(information_cutoff,'information cutoff')
    if not isinstance(intervals,dict) or not intervals:
        raise ValueError("explicit complete interval map required")
    ordered=[]
    for t,pair in intervals.items():
        if type(t) is not int or not isinstance(pair,tuple) or len(pair)!=2:
            raise ValueError("interval requires integer ID and immutable start/end pair")
        start,end=map(lambda x:_aware(x,'delivery'),pair)
        if end<=start:raise ValueError("positive delivery interval required")
        ordered.append((start,end,t))
    ordered.sort()
    if any(end>next_start for (_,end,_),(next_start,_,_) in zip(ordered,ordered[1:])):
        raise ValueError("energy intervals must not overlap")
    if energy_stage=='RT_SCED':
        if cutoff!=ordered[0][0]-timedelta(minutes=15):raise ValueError('RT boundary cutoff must be first execution T-15')
        if any(end!=next_start for (_,end,_),(next_start,_,_) in zip(ordered,ordered[1:])):
            raise ValueError('RT rolling intervals must cover consecutive quarters without gaps')
        for start,end,_ in ordered:
            local=start.astimezone(ZoneInfo('Asia/Shanghai'))
            if local.minute%15 or local.second or local.microsecond or end-start!=timedelta(minutes=15):
                raise ValueError('RT energy intervals must be Shanghai-aligned quarters')
    if not all(isinstance(x,dict) and x for x in (physical.lower_mw,physical.upper_mw,physical.online)):
        raise ValueError("nonempty complete physical mappings required")
    keys=set(physical.lower_mw)
    if keys!=set(physical.upper_mw) or keys!=set(physical.online):
        raise ValueError("physical lower/upper/status coverage differs")
    if any(not isinstance(k,tuple) or len(k)!=2 or not isinstance(k[0],str) or not k[0] or type(k[1]) is not int for k in keys):
        raise ValueError("resource/period keys required")
    resources={k[0] for k in keys}
    if keys!={(r,t) for r in resources for t in intervals}:
        raise ValueError("physical resource/period grid must be complete")
    lo={k:_number(physical.lower_mw[k],'physical lower') for k in keys}
    hi={k:_number(physical.upper_mw[k],'physical upper') for k in keys}
    if any(lo[k]>hi[k] for k in keys):raise ValueError("physical lower exceeds upper")
    if any(type(physical.online[k]) is not bool for k in keys):raise ValueError("online status must be explicit Boolean")
    if not isinstance(results,tuple) or not results:raise ValueError("explicit results tuple required, including zero awards")
    ids=[]
    stage='day_ahead_prearrangement' if energy_stage=='DA_SCED' else 'intraday_formal'
    for result in results:
        if not isinstance(result,RegulationCapacityResult):raise ValueError("typed regulation result required")
        _text(result.result_id,'result_id');_text(result.source,'result source')
        if result.stage!=stage:raise ValueError("regulation stage cannot be reused in this energy stage")
        pub=_aware(result.published_at,'result publication')
        start=_aware(result.binding_start,'binding start');end=_aware(result.binding_end,'binding end')
        lookahead=_aware(result.lookahead_end,'lookahead end')
        if not pub<=cutoff or not pub<=start or not start<end<=lookahead:
            raise ValueError("future result or invalid binding/lookahead interval")
        if stage=='intraday_formal' and abs((end-start).total_seconds()-3600)>1e-6:
            raise ValueError("formal AGC result binds exactly its first hour")
        if stage=='intraday_formal':
            local=start.astimezone(ZoneInfo('Asia/Shanghai'))
            if local.minute or local.second or local.microsecond or lookahead!=start+timedelta(hours=6) or not start-timedelta(minutes=45)<=pub<=start-timedelta(minutes=30):
                raise ValueError('formal AGC requires an hourly start, T-45 to T-30 publication, six-hour lookahead')
        if not isinstance(result.capacity_mw,dict) or set(result.capacity_mw)!=resources:
            raise ValueError("AGC result requires every physical resource, including explicit zero")
        for r,value in result.capacity_mw.items():_number(value,r+' capacity',True)
        if not isinstance(result.lookahead_slots,tuple):raise ValueError("immutable explicit lookahead slots required")
        previous_end=end
        for slot in result.lookahead_slots:
            if not isinstance(slot,RegulationLookaheadSlot):raise ValueError("typed lookahead slot required")
            a=_aware(slot.start,'lookahead slot start');b=_aware(slot.end,'lookahead slot end')
            if not end<=a<b<=lookahead or a<previous_end:
                raise ValueError("lookahead slots must be ordered, disjoint and after binding hour")
            if not isinstance(slot.capacity_mw,dict) or set(slot.capacity_mw)!=resources:
                raise ValueError("lookahead slot requires complete capacity inventory")
            for r,value in slot.capacity_mw.items():_number(value,r+' lookahead capacity',True)
            previous_end=b
        ids.append(result.result_id)
    if len(set(ids))!=len(ids):raise ValueError("duplicate result versions")
    q={};provenance={};binding={}
    for start,end,t in ordered:
        overlapping=[(r,r.capacity_mw,r.binding_start,r.binding_end,True) for r in results if r.binding_start<end and r.binding_end>start]
        if allow_nonbinding_lookahead:
            overlapping += [(r,s.capacity_mw,s.start,s.end,False) for r in results for s in r.lookahead_slots if s.start<end and s.end>start]
        if len(overlapping)!=1:raise ValueError(f"period {t}: missing or overlapping binding AGC result; lookahead is not binding")
        result,capacity,coverage_start,coverage_end,is_binding=overlapping[0]
        if energy_stage=='RT_SCED' and t==ordered[0][2] and not is_binding:
            raise ValueError('first executed RT interval requires binding formal capacity, not an expired lookahead')
        if coverage_start>start or coverage_end<end:
            raise ValueError(f"period {t}: binding result does not cover the whole interval")
        binding[t]=is_binding
        for resource in resources:
            key=(resource,t);q[key]=float(capacity[resource]);provenance[key]=result.result_id
            if not physical.online[key] and q[key]!=0:
                raise ValueError(f"{key}: offline resource has a nonzero AGC reservation")
            if lo[key]+q[key]>hi[key]-q[key]:
                raise ValueError(f"{key}: AGC reservation empties the physical energy interval")
    return AdjustedEnvelope({k:lo[k]+q[k] for k in keys},{k:hi[k]-q[k] for k in keys},lo,hi,q,provenance,
        {'energy_stage':energy_stage,'regulation_stage':stage,'rule_reference':'GDE2026 7.1',
         'physical_source':physical.source,'source_authenticity_verified':False,
         'cross_provincial_reserve_trade_mw':0.,'lookahead_promoted_to_binding':False,
         'capacity_is_binding_by_period':binding,
         'nonbinding_lookahead_used':any(not x for x in binding.values()),
         'lookahead_rule_reference':'SREG2026 60: later hours are linkage only',
         'service_payment_created':False,'adjustments_applied':1})
