"""2026 scanned-rule thermal merit mechanism; Guangdong-only research projection.
No solver, no cross-region trade, no qualification creation or production dispatch.
"""
from collections import Counter
from datetime import datetime, timedelta
import math

class RuleInputError(ValueError): pass

def num(x, positive=False):
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or (positive and x==0):
        raise RuleInputError('finite nonnegative numeric input required')
    return x

def clear_hour(units, *, demand_mw, local_floor_mw, full_control_area_kmax,
               as_of, hour_start, previous_raw_marginal=None,
               safety_excluded=(), floor_crossing_policy='exclude_crossing_unit',
               final_tie_policy='resource_id_research', mode='research'):
    if mode!='research':
        raise RuleInputError('official direct source/full regional clearing/operator certification unavailable')
    d=num(demand_mw,True); floor=num(local_floor_mw)
    if floor>d: raise RuleInputError('floor exceeds demand')
    km=num(full_control_area_kmax,True)
    now=datetime.fromisoformat(as_of); start=datetime.fromisoformat(hour_start)
    if now>start-timedelta(minutes=30): raise RuleInputError('T-30 preparation deadline missed')
    if floor_crossing_policy!='exclude_crossing_unit' or final_tie_policy!='resource_id_research':
        raise RuleInputError('unsupported research ambiguity policy')
    ids=[u['resource_id'] for u in units]
    if len(set(ids))!=len(ids): raise RuleInputError('duplicate resource')
    if set(safety_excluded)-set(ids): raise RuleInputError('unknown safety exclusion')
    ranked=[]
    for u in units:
        if u['region']!='Guangdong': raise RuleInputError('province-only scope')
        if u['kind']!='thermal': raise RuleInputError('this implementation does not qualify other technologies')
        if not u.get('qualification_evidence') or u.get('qualified') is not True:
            raise RuleInputError('missing explicit qualification declaration')
        if datetime.fromisoformat(u['available_at'])>now: raise RuleInputError('future input')
        k=num(u['k'],True); k1=num(u['k1']); bid=num(u['bid'])
        ratio=num(u['declared_ratio']); cap=num(u['capacity_excluding_outage_mw'],True)
        if k>km or not .03<=ratio<=.075 or not 3<=bid<=15:
            raise RuleInputError('invalid k/bid/thermal ratio (dynamic floor requires new input version)')
        if u.get('stable_online') is not True: raise RuleInputError('transition/offline cannot supply AGC')
        if u['resource_id'] not in safety_excluded:
            ranked.append(dict(u,P=k/km,sorting_price=bid/(k/km)))
    ranked.sort(key=lambda u:(u['sorting_price'],-u['P'],-u['k1'],u['resource_id']))
    selected=[]; awards={}; trace=[]
    for u in ranked:
        selected.append(u); counts=Counter(v['plant_id'] for v in selected)
        # Article49 Guangdong Qu=Qr=0. Recompute all awards when plant count changes.
        awards={v['resource_id']:min(v['declared_ratio']*v['capacity_excluding_outage_mw'],
                                    .2*d/counts[v['plant_id']]) for v in selected}
        trace.append({'added':u['resource_id'],'awards_mw':dict(awards),'total_mw':sum(awards.values())})
        if sum(awards.values())>=d-1e-9: break
    total=sum(awards.values()); unmet=max(0,d-total)
    cumulative=0; reserved=[]
    for u in selected:
        if cumulative<floor-1e-9: reserved.append(u['resource_id'])
        cumulative+=awards[u['resource_id']]
    setters=[u for u in selected if u['resource_id'] not in reserved]
    raw=setters[-1]['sorting_price'] if setters else previous_raw_marginal
    if raw is not None: num(raw)
    price=None if raw is None or unmet>1e-9 else min(raw,15)
    return {'awards_mw':awards,'ranked_ids':[u['resource_id'] for u in ranked],
            'reserved_non_price_setters':reserved,'raw_marginal':raw,'price':price,
            'price_source':'current' if setters else ('previous_raw' if raw is not None else 'unresolved'),
            'unmet_mw':unmet,'trace':trace,'safety_excluded':list(safety_excluded),
            'hour_start':hour_start,'binding_end':(start+timedelta(hours=1)).isoformat(),
            'research_only':True,'formal_publication_allowed':False,'dispatch_authorized':False,
            'scope':'Guangdong local projection, NOT regional market outcome',
            'assumptions':[floor_crossing_policy,final_tie_policy,'synthetic/local demand, external full-area kmax',
                           'security exclusions are supplied, NOT independently certified']}

def sced_bounds(result, baseline, *, positive_reserve_requirement=400, accident_reserve_requirement=400):
    if positive_reserve_requirement!=400 or accident_reserve_requirement!=400:
        raise RuleInputError('original scenario reserve requirements must remain 400/400')
    if result['unmet_mw']>1e-9: raise RuleInputError('AGC shortage cannot become schedule')
    out={}
    for rid,q in result['awards_mw'].items():
        b=baseline[rid]
        if b.get('agc_deducted'): raise RuleInputError('double deduction')
        lo=num(b['pmin'])+q; hi=num(b['pmax'])-q
        if not lo<=num(b['power'])<=hi: raise RuleInputError('fixed SCUC headroom insufficient')
        out[rid]={'pmin':lo,'pmax':hi,'agc_up_mw':q,'agc_down_mw':q,'agc_deducted':True}
    return {'bounds':out,'positive_reserve_requirement':400,'accident_reserve_requirement':400,
            'reserve_feasibility_certified':False,'dispatch_authorized':False}

def gross_mileage(mileage_mw, price, performance, *, designated_test=False):
    if type(designated_test) is not bool: raise RuleInputError('designated_test must be bool')
    a,b,c=(num(x) for x in (mileage_mw,price,performance))
    if b>15: raise RuleInputError('clearing price exceeds 15')
    product=a*b*c
    if not math.isfinite(product): raise RuleInputError('nonfinite gross compensation')
    return {'gross_compensation':0 if designated_test else product,
            'payable':False,'excludes_assessments_and_cost_allocation':True}

def execution_view(hour_results):
    if not 3<=len(hour_results)<=6: raise RuleInputError('formal rolling horizon is 3-6 hours')
    first=hour_results[0]
    if num(first['unmet_mw'])>1e-9 or first['price'] is None:
        raise RuleInputError('first hour unresolved; no binding view')
    starts=[datetime.fromisoformat(r['hour_start']) for r in hour_results]
    if any(b-a!=timedelta(hours=1) for a,b in zip(starts,starts[1:])): raise RuleInputError('noncontiguous horizon')
    return {'first_hour':hour_results[0],'lookahead_only':hour_results[1:],'dispatch_authorized':False}
