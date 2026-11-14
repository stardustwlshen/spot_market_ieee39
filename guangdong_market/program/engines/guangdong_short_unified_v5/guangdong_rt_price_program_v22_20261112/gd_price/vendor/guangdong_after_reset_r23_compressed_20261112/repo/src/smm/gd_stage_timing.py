"""Explicit provincial-reference stage timing and information-set validation.

No independent intraday energy market, auctions, or settlement is executed here.
Cross-midnight horizons are never silently truncated to the scenario day.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timedelta
from numbers import Real
import math
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ClearingEvent:
    stage: str
    issue_at: datetime
    completion_deadline: datetime
    execute_start: datetime
    execute_end: datetime
    horizon_end: datetime
    information_cutoff: datetime
    rule_reference: str
    def validate(self):
        for name in ('issue_at','completion_deadline','execute_start','execute_end','horizon_end','information_cutoff'):
            _aware(getattr(self,name),name)
        local=self.execute_start.astimezone(ZoneInfo('Asia/Shanghai'))
        if local.second or local.microsecond or local.minute%15:
            raise ValueError('execution must begin on a Shanghai quarter-hour boundary')
        if not isinstance(self.rule_reference,str) or not self.rule_reference.strip():raise ValueError('event rule reference required')
        if self.stage=='energy_realtime':
            if self.issue_at!=self.execute_start-timedelta(minutes=15) or self.completion_deadline!=self.execute_start or self.execute_end!=self.execute_start+timedelta(minutes=15):
                raise ValueError('RT event must use T-15 issue and one quarter execution')
        elif self.stage=='regulation_formal':
            if local.minute or self.issue_at!=self.execute_start-timedelta(minutes=45) or self.completion_deadline!=self.execute_start-timedelta(minutes=30) or self.execute_end!=self.execute_start+timedelta(hours=1) or self.horizon_end!=self.execute_start+timedelta(hours=6):
                raise ValueError('formal regulation event must use T-45/T-30, first hour, six-hour horizon')
        else:raise ValueError('unsupported clearing stage')
        if self.information_cutoff!=self.issue_at:raise ValueError('information cutoff must equal the declared issuance gate')
        seconds=(self.horizon_end-self.execute_start).total_seconds()
        if self.horizon_end<self.execute_end or not 900<=seconds<=86400 or seconds%900:
            raise ValueError('positive complete quarter-hour horizon required')
        return self
    def delivery_starts(self):
        self.validate()
        count=int((self.horizon_end-self.execute_start).total_seconds()/900)
        return tuple(self.execute_start+timedelta(minutes=15*i) for i in range(count))


def _aware(x,label):
    if not isinstance(x,datetime) or x.tzinfo is None or x.utcoffset() is None:
        raise ValueError(f"{label}: timezone-aware datetime required")
    return x


def provincial_realtime_schedule(day, *, rt_lookahead_h, rt_horizon_source,
                                 agc_enabled, agc_operation_evidence):
    """24 formal AGC and 96 RT events for an explicit Shanghai operating day.

    Published AGC timing is T-45, completion by T-30, 6h horizon; first hour binds.
    RT horizon is required because provincial/operational source selection is not
    safely inferred by this scheduling component. AGC technical operation must
    be explicitly confirmed or deliberately excluded from a component test.
    """
    if isinstance(day,str):
        try:day=date.fromisoformat(day)
        except ValueError as exc:raise ValueError("invalid operation day") from exc
    if type(day) is not date:raise ValueError("operation day must be a date, not timestamp")
    if isinstance(rt_lookahead_h,bool) or not isinstance(rt_lookahead_h,Real) or not math.isfinite(float(rt_lookahead_h)):
        raise ValueError("explicit finite RT horizon required")
    horizon=float(rt_lookahead_h)
    if horizon<.25 or horizon>24 or abs(horizon*4-round(horizon*4))>1e-9:
        raise ValueError("RT horizon must be positive quarter-hours within supported 24h domain")
    if not isinstance(rt_horizon_source,str) or not rt_horizon_source.strip():raise ValueError("RT horizon source/assumption required")
    if type(agc_enabled) is not bool:raise ValueError("explicit AGC enabled Boolean required")
    if agc_enabled and (not isinstance(agc_operation_evidence,str) or not agc_operation_evidence.strip()):
        raise ValueError("AGC operation evidence or explicit simulation assumption required")
    origin=datetime.combine(day,datetime.min.time(),tzinfo=ZoneInfo('Asia/Shanghai'))
    events=[]
    for t in range(96):
        start=origin+timedelta(minutes=15*t)
        issue=start-timedelta(minutes=15)
        events.append(ClearingEvent('energy_realtime',issue,start,start,start+timedelta(minutes=15),start+timedelta(hours=horizon),issue,'GDE2026 6.1/6.2; horizon: '+rt_horizon_source))
    if agc_enabled:
        for hour in range(24):
            start=origin+timedelta(hours=hour);issue=start-timedelta(minutes=45)
            events.append(ClearingEvent('regulation_formal',issue,start-timedelta(minutes=30),start,start+timedelta(hours=1),start+timedelta(hours=6),issue,'SREG2026 41/57/60 + SOPS2026 p4; '+agc_operation_evidence))
    return tuple(sorted((event.validate() for event in events),key=lambda e:(e.issue_at,e.stage)))


def validate_forecast_vintage(event, *, published_at, delivery_starts, values, provenance,
                              research_mode=False):
    """Never relabel synthetic future-actual curves as causal online forecasts.

    Caller-provided provenance still needs source verification; timestamps alone
    do not prove that a source actually existed at that time.
    """
    if not isinstance(event,ClearingEvent):raise ValueError("clearing event required")
    event.validate()
    published=_aware(published_at,'forecast publication')
    if published>event.information_cutoff:raise ValueError("forecast published after information cutoff")
    if not isinstance(delivery_starts,(list,tuple)) or not isinstance(values,(list,tuple)):
        raise ValueError("explicit timestamp and value sequences required")
    stamps=tuple(_aware(x,'delivery timestamp') for x in delivery_starts)
    if stamps!=event.delivery_starts() or len(values)!=len(stamps):raise ValueError("complete exact forecast horizon required; no day-end truncation")
    if any(isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(float(v)) for v in values):raise ValueError("finite forecast values required")
    if type(research_mode) is not bool:raise ValueError("research_mode must be explicit Boolean")
    known={'published_causal_forecast','offline_from_full_actual','perfect_current_actual'}
    if provenance not in known:raise ValueError("unknown forecast provenance")
    if provenance!='published_causal_forecast' and not research_mode:
        raise ValueError("future-actual/perfect-measurement research input is not an online causal forecast")
    return {'timestamps_valid':True,'provenance':provenance,'research_only':provenance!='published_causal_forecast',
            'online_source_authenticity_verified':False,'periods':len(stamps),
            'information_cutoff':event.information_cutoff.isoformat()}
