"""Reconcile the executed Sichuan V5.0 synthetic spot-energy ledger, not a bill."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from sichuan_v5_settlement import SettlementInput, UniformPriceInput, settle_quarter, uniform_price


D = Decimal
ZERO = D(0)
TOL = D('0.000000001')
DT = D('.25')
PRICE_TICK = D('.01')
PRICE_SOURCES = {'rt_initial', 'rt_recomputed', 'da_proxy_rt', 'withheld'}


def _num(value, label, *, nonnegative=False, missing=False):
    if value is None and missing:
        return None
    if type(value) not in (int, float, str, D):
        raise ValueError(f'{label}: invalid numeric evidence')
    try:
        parsed = D(str(value))
    except InvalidOperation as exc:
        raise ValueError(f'{label}: invalid numeric evidence') from exc
    if not parsed.is_finite() or nonnegative and parsed < 0:
        raise ValueError(f'{label}: nonfinite or negative evidence')
    return parsed


def _key(data, target):
    if not isinstance(data, Mapping):
        raise ValueError('missing or malformed keyed market evidence')
    alt = str(target)
    if target in data and alt in data and target != alt:
        raise ValueError('ambiguous numeric/string duplicate key')
    return data.get(target, data.get(alt))


def _seq(rows, label, count=None):
    if (not isinstance(rows,(list,tuple)) or not 1<=len(rows)<=96
            or count is not None and len(rows)!=count
            or any(not isinstance(r, Mapping) or type(r.get('quarter')) is not int or r['quarter']!=i
                   for i,r in enumerate(rows))):
        raise ValueError(f'{label}: quarter rows must be an ordered executed prefix or complete DA reference')


def _time_values(row, unit, role, bus):
    if role=='user':
        demand = _num(_key(row.get('demand'),bus),'demand MW',nonnegative=True)
        shed = _num(_key(row.get('shed'),bus),'shed MW',nonnegative=True)
        if shed>demand:
            raise ValueError('executed load shedding exceeds demand')
        return ZERO, (demand-shed)*DT
    delivered = _num(_key(row.get('power'),unit),'delivered MW',nonnegative=True)*DT
    consumed = ZERO
    if role=='independent_storage':
        consumed=_num(_key(row.get('charge'),unit),'consumed MW',nonnegative=True)*DT
    return delivered, consumed


def _same(observed, expected, key):
    if observed is None or expected is None:
        if observed is not expected:
            raise ValueError(f'{key}: archived value disagrees with execution')
    elif abs(_num(observed,key)-expected)>TOL:
        raise ValueError(f'{key}: archived value disagrees with execution')


def _cap(raw, low, high):
    return None if raw is None else min(high,max(low,raw)).quantize(PRICE_TICK,rounding=ROUND_HALF_UP)


def _complete_sum(rows,key):
    values=[r[key] for r in rows]
    return None if any(v is None for v in values) else sum(values,ZERO)


def _serialized(row):
    return {k:str(v) if isinstance(v,D) else v for k,v in row.items()}


def reconcile_energy(case, rt_executed, rt_research_values, *, da_reference=None, price_reviews=None):
    """Return JSON-ready audit of only the executed intervals of a synthetic run.

    Inputs are the saved case, ordered RT execution/valuation rows and optional
    complete DA preclear. The optional price-review inputs are caller-verified
    evidence, not an authorization to source, substitute, or invent a price.
    Contradictory archived quantities, prices or money fail closed.
    """
    from sichuan_v5_demo import CATEGORY
    from sichuan_v5_rules import spot_clear_bounds

    if not isinstance(case,Mapping):
        raise ValueError('expected archived case mapping')
    buses=tuple(case.get('buses',()))
    if (not buses or any(type(b) is not int for b in buses) or len(set(buses))!=len(buses)):
        raise ValueError('invalid case nodes')
    units=case.get('resources')
    if not isinstance(units,(tuple,list)) or not units:
        raise ValueError('missing case resources')
    unit_ids=set()
    generators=[]
    for u in units:
        if (not isinstance(u,Mapping) or not isinstance(u.get('id'),str) or not u['id']
                or not isinstance(u.get('role'),str) or not isinstance(u.get('kind'),str)
                or u['id'] in unit_ids or type(u.get('bus')) is not int or u['bus'] not in buses):
            raise ValueError('invalid case resource or bus')
        unit_ids.add(u['id'])
        if u.get('role') in CATEGORY:
            role='independent_storage' if u.get('kind')=='storage' else 'generator'
            generators.append((u['id'],role,u['bus'],CATEGORY[u['role']]))
    _seq(rt_executed,'RT execution')
    _seq(rt_research_values,'RT valuation',len(rt_executed))
    if da_reference is not None:
        _seq(da_reference,'DA reference',96)
    reviews={} if price_reviews is None else _normalize_reviews(price_reviews,len(rt_executed))
    low,high=map(lambda x:D(str(x)),spot_clear_bounds())
    transactions=[]
    quarters=[]
    with localcontext(prec=34):
        for i,(rt,money) in enumerate(zip(rt_executed,rt_research_values,strict=True)):
            refs=None if da_reference is None else da_reference[i]
            raw={b:_num(_key(rt.get('raw_nodal_prices',{}),b),'raw RT nodal price',missing=True) for b in buses}
            bad=(max(_num(_key(rt.get('shed'),b),'shed MW',nonnegative=True) for b in buses)>D('0.000001')
                 or max(_num(_key(rt.get('spill'),b),'spill MW',nonnegative=True) for b in buses)>D('0.000001')
                 or _num(rt.get('reliability_reserve_short_mw'),'reliability shortfall MW',nonnegative=True)>D('0.000001'))
            source, evidence_id, incident_id, effective_raw = _review_source(reviews.get(i),raw,bad,buses)
            nodal={b:_cap(p,low,high) for b,p in effective_raw.items()}
            archived_nodal=money.get('cleared_nodal_price')
            for b in buses:
                _same(_key(archived_nodal,b),nodal[b],f'{i} cleared nodal price {b}')
            eligible_mwh={}
            for unit,role,b,category in generators:
                eligible_mwh[unit]=_time_values(rt,unit,role,b)
            uniform=uniform_price(UniformPriceInput(u,c,eligible_mwh[u][0],nodal[b]) for u,_,b,c in generators)
            _same(money.get('uniform_price'),uniform,'uniform price')
            represented=money.get('rows')
            if not isinstance(represented,(list,tuple)):
                raise ValueError('missing saved RT ledger rows')
            originals={}
            for r in represented:
                if (not isinstance(r,Mapping) or not isinstance(r.get('resource_id'),str)
                        or type(r.get('quarter')) is not int or r.get('quarter')!=i or r['resource_id'] in originals):
                    raise ValueError('duplicate, foreign or non-executed valuation row')
                originals[r['resource_id']]=r
            users=[(f'负荷-{b}','user',b,None) for b in buses
                   if _num(_key(rt.get('demand'),b),'demand MW',nonnegative=True)>0]
            expected=[*generators,*users]
            if len({u for u,*_ in expected})!=len(expected) or set(originals)!={u for u,*_ in expected}:
                raise ValueError('missing or foreign RT valuation resource')
            start=len(transactions)
            for unit,role,b,_ in expected:
                old=originals[unit]
                if old.get('role')!=role:
                    raise ValueError('recorded settlement role disagrees with case')
                delivered,consumed=_time_values(rt,unit,role,b)
                da_qty=(None,None) if refs is None else _time_values(refs,unit,role,b)
                inp=SettlementInput(i,unit,role,delivered,consumed,nodal[b] if role!='user' else None,uniform)
                result=settle_quarter(inp)
                for field,value in dict(grid_delivered_mwh=delivered,grid_consumed_mwh=consumed,rt_nodal_price=inp.rt_nodal_price,
                                        rt_uniform_price=uniform,grid_delivered_amount=result.grid_delivered_amount,
                                        grid_consumed_amount=result.grid_consumed_amount,net_amount=result.net_amount).items():
                    _same(old.get(field),value,f'{i}/{unit} {field}')
                output=dict(quarter=i,resource_id=unit,role=role,bus=b,grid_delivered_mwh=delivered,
                            grid_consumed_mwh=consumed,da_delivered_mwh=da_qty[0],da_consumed_mwh=da_qty[1],
                            raw_rt_nodal_price=raw[b],effective_raw_nodal_price=effective_raw[b],effective_rt_nodal_price=nodal[b],
                            effective_rt_uniform_price=uniform,rt_price_source=source,price_evidence_id=evidence_id,
                            incident_id=incident_id,
                            delivery_price_source=('rt_nodal' if nodal[b] is not None else 'withheld') if delivered else None,
                            consumption_price_source=('rt_uniform' if uniform is not None else 'withheld') if consumed else None,
                            grid_delivered_amount=result.grid_delivered_amount,grid_consumed_amount=result.grid_consumed_amount,
                            net_amount=result.net_amount,issues=list(result.issues))
                transactions.append(output)
            period=transactions[start:]
            unpriced=sum(r['net_amount'] is None for r in period)
            quarters.append(dict(quarter=i,valuation_status='incomplete' if unpriced or source=='withheld' or any(x is None for x in nodal.values()) else 'provisional_rt_only',
                                 rt_price_source=source,price_evidence_id=evidence_id,incident_id=incident_id,
                                 archived_valuation_status=money.get('valuation_status'),
                                 price_suppressed_due_reliability=bad,uniform_price=uniform,priced_rows=len(period)-unpriced,unpriced_rows=unpriced))
        n=len(rt_executed)
        unpriced=sum(r['net_amount'] is None for r in transactions)
        summary=dict(executed_quarters=n,full_day_coverage=n==96,
                     incomplete_quarters=sum(q['valuation_status']=='incomplete' for q in quarters),priced_rows=len(transactions)-unpriced,unpriced_rows=unpriced,
                     **{k:_complete_sum(transactions,k) for k in ('grid_delivered_mwh','grid_consumed_mwh',
                                                                 'grid_delivered_amount','grid_consumed_amount','net_amount')})
        if summary['incomplete_quarters']:
            summary['net_amount']=None
        return dict(schema='ieee39.sichuan_v5.energy_reconciliation.v1',formal_settlement=False,
                    da_reference_only=True,summary=_serialized(summary),
                    quarters=[_serialized(q) for q in quarters],transactions=[_serialized(t) for t in transactions])


def _normalize_reviews(price_reviews, count):
    """Explicit per-quarter audit representation; caller validates review lineage."""
    if not isinstance(price_reviews,(tuple,list)):
        raise ValueError('price reviews must be a sequence')
    out={}
    for review in price_reviews:
        if not isinstance(review,Mapping):
            raise ValueError('malformed price-review evidence')
        i=review.get('quarter')
        if type(i) is not int or not 0<=i<count or i in out:
            raise ValueError('price review is duplicate or outside executed quarters')
        out[i]=review
    return out


def _review_source(review, raw, bad, buses):
    if review is None:
        return ('withheld',None,None,{b:None for b in buses}) if bad or all(v is None for v in raw.values()) else ('rt_initial',None,None,raw)
    source=review.get('source')
    evidence=review.get('evidence_id')
    incident=review.get('incident_id')
    if (review.get('evidence_status')!='declared_synthetic_research' or not isinstance(source,str)
            or source not in PRICE_SOURCES or source!='rt_initial' and (not isinstance(incident,str) or not incident.strip())):
        raise ValueError('invalid verified price source or missing incident evidence')
    if 'original_raw_nodal_prices' not in review:
        raise ValueError('missing original RT vector in review evidence')
    original={b:_num(_key(review.get('original_raw_nodal_prices') or {},b),'reviewed original price',missing=True) for b in buses}
    if original!=raw:
        raise ValueError('review original RT price does not match executed raw price')
    if source=='withheld':
        if review.get('selected_nodal_prices') is not None:
            raise ValueError('withheld price review contains selected prices')
        return (source,None,incident,{b:None for b in buses})
    if not isinstance(evidence,str) or not evidence.strip():
        raise ValueError('missing verified price evidence')
    if bad:
        return ('withheld',evidence,incident,{b:None for b in buses})
    vector=review.get('selected_nodal_prices')
    if not isinstance(vector,Mapping) or set(map(str,vector))!={str(b) for b in buses}:
        raise ValueError('reviewed price vector does not match case nodes')
    effective={b:_num(_key(vector,b),'reviewed raw price') for b in buses}
    if source=='rt_initial' and effective!=raw:
        raise ValueError('reviewed initial RT price does not match executed raw price')
    return source,evidence,incident,effective
