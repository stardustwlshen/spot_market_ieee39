"""Pure candidate-price contract; no publication or settlement authority."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

def aware(t):
    if not isinstance(t,datetime) or t.utcoffset() is None: raise ValueError('aware datetime required')
def dec(x):
    if not isinstance(x,Decimal) or not x.is_finite(): raise ValueError('finite Decimal required')
    return x
@dataclass(frozen=True)
class PriceLimitSpec:
    region:str
    stage:str
    unit:str
    lower:Decimal
    upper:Decimal
    effective_from:datetime
    effective_to:datetime|None
    source_url:str
    source_id:str
    source_status:str
    as_of_search:datetime
    no_expiry_known:bool
    override_status:str
    dynamic_current_verified:bool
    def validate(self,at,stage):
        aware(at);aware(self.effective_from);aware(self.as_of_search)
        if self.effective_to is not None:
            aware(self.effective_to)
            if self.effective_to<=self.effective_from:raise ValueError('invalid effective interval')
        if type(self.no_expiry_known)is not bool or type(self.dynamic_current_verified)is not bool:raise ValueError('boolean policy flags required')
        if self.region!='GD' or self.unit!='CNY/MWh' or self.stage not in ('DA','RT','both') or stage not in ('DA','RT') or self.stage not in (stage,'both'):raise ValueError('scope mismatch')
        if dec(self.lower)>dec(self.upper) or not self.source_url or not self.source_id:raise ValueError('invalid limits/source')
        if self.source_status not in ('official_published','synthetic') or self.override_status not in ('not_found','known','conflict'):raise ValueError('invalid policy status')
        if self.override_status!='not_found':raise ValueError('replacement spec required')
        if at<self.effective_from or self.effective_to is not None and at>=self.effective_to:raise ValueError('outside effective period')
        if self.effective_to is None and not self.no_expiry_known:raise ValueError('expiry unresolved')

def price_candidate(*,spec,status,run_id,node,start,end,stage,raw_price=None,evidence_id=None,evidence_sha256=None):
    aware(start);aware(end)
    if start.minute%15 or start.second or start.microsecond:raise ValueError('quarter boundary required')
    if not run_id or not node or end-start!=timedelta(minutes=15):raise ValueError('invalid interval/identity')
    if status not in ('success','confirmed_missing'):raise ValueError('unsafe or invalid native state')
    spec.validate(start,stage)
    if spec.effective_to is not None and end>spec.effective_to:raise ValueError('interval straddles expiry')
    out=dict(run_id=run_id,node=node,start=start,end=end,stage=stage,status=status,raw_price=raw_price,bounded_candidate_price=None,official_market_price=None,settlement_price=None,publication='external verified official release required',limit_source=spec,limit_applied=False)
    if status=='confirmed_missing':
        if raw_price is not None:raise ValueError('missing raw price must be None')
        out['route']='pending; explicit same-run verified-success fallback contract required; no automatic DA/cross-run substitution';return out
    raw=dec(raw_price)
    if not evidence_id or not isinstance(evidence_sha256,str) or len(evidence_sha256)!=64 or any(c not in '0123456789abcdef' for c in evidence_sha256):raise ValueError('independent raw evidence required')
    bounded=max(spec.lower,min(spec.upper,raw))
    out.update(bounded_candidate_price=bounded,limit_applied=bounded!=raw,evidence_id=evidence_id,evidence_sha256=evidence_sha256,basis='published_policy_basis' if spec.source_status=='official_published' else 'illustrative')
    return out

def hourly_candidate(quarters):
    if len(quarters)!=4:raise ValueError('four quarters required')
    q=sorted(quarters,key=lambda x:x['start']);first=q[0]
    for i,x in enumerate(q):
        if any(x[k]!=first[k]for k in ('node','run_id','stage')) or x['start']!=first['start']+timedelta(minutes=15*i) or x['end']-x['start']!=timedelta(minutes=15):raise ValueError('duplicate/gap/mixed identity')
    if first['start'].minute!=0 or first['start'].second or first['start'].microsecond:raise ValueError('calendar hour required')
    if any(x['status'] not in ('success','confirmed_missing')for x in q):raise ValueError('invalid status')
    vals=[]
    for x in q:
        rebuilt=price_candidate(spec=x['limit_source'],status=x['status'],run_id=x['run_id'],node=x['node'],start=x['start'],end=x['end'],stage=x['stage'],raw_price=x['raw_price'],evidence_id=x.get('evidence_id'),evidence_sha256=x.get('evidence_sha256'))
        if rebuilt['bounded_candidate_price']!=x['bounded_candidate_price']:raise ValueError('tampered bounded candidate')
        vals.append(rebuilt['bounded_candidate_price'])
    return dict(node=first['node'],run_id=first['run_id'],start=first['start'],end=q[-1]['end'],bounded_hourly_candidate_price=None if any(x is None for x in vals)else sum((dec(x)for x in vals),Decimal(0))/4,official_market_price=None,settlement_price=None)
