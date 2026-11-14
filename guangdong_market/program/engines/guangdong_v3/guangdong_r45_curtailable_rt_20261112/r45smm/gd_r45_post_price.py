"""R45 pure D+1 price verification and simulated-energy arithmetic.

No live acceptance, optimizer, command issue, receipt authentication, official
publication or actual settlement occurs here. Runtime must independently bind
and verify every supplied native price and executed-quarter witness.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math

PENDING_REASONS=('confirmed_PRICE_Infeasible','research_terminal_domain_unavailable')
POLICY=dict(schema='gd.research.r45.post_price_policy.v1',
    first_quarter_only=True,verification='simulated_Dplus1_or_explicit_Dplus2',
    missing_mapping='Known PRICE infeasibility and predeclared research terminal-domain unavailability map to missing prices for this research only; not an official interpretation for every preflight failure',
    source='previous genuinely successful first-quarter nodal price',
    substituted_price_is_success_anchor=False,no_history='unavailable_not_zero',
    physical_dispatch_dependent_on_price=False,official_publication=None,formal_bill=None)


def require(condition,message):
    if not condition:raise ValueError(message)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def finite(value):
    require(type(value) in (int,float) and math.isfinite(value),'finite numeric value required');return float(value)
def stamp(value):
    x=datetime.fromisoformat(value);require(x.utcoffset()==timedelta(hours=8),'Shanghai +08 logical clock required');return x
def _sha(value):
    require(type(value)is str and len(value)==64 and all(c in '0123456789abcdef'for c in value),'bound SHA256 required');return value

def _window(record,day_start=None):
    w=record['window'];i=w['index'];start,end=stamp(w['start']),stamp(w['delivery_end'])
    require(type(i)is int and 0<=i<96 and end-start==timedelta(minutes=15),'D-day quarter window required')
    require(start.minute%15==0 and start.second==start.microsecond==0,'quarter alignment')
    require(stamp(w['cutoff'])==start-timedelta(minutes=15),'original causal cutoff required')
    if day_start is not None:require(start==day_start+timedelta(minutes=15*i),'window index/day identity mismatch')
    return w,start,end

def _nodes(values):
    values=list(values);keys=[str(n)for n in values]
    require(len(set(keys))==len(keys),'duplicate/colliding node inventory')
    require(set(keys)=={str(i)for i in range(1,40)},'complete IEEE39 node inventory required')
    return set(keys)

def _success(record,historical=False):
    require(record['status']=='success','only genuine success can anchor prices')
    require(record.get('source_quarter_index',0)==0,'lookahead price cannot anchor delivery')
    occurrence=record['occurrence']
    require(occurrence.get('outcome')=='verified_native_response' and occurrence.get('native_status')=='Optimal','known verified optimal occurrence required')
    require(occurrence.get('returncode',0)==0 and occurrence.get('id'),'confirmed successful occurrence required')
    _sha(record['input_sha256']);_sha(record['model_sha256'])
    require(record.get('run_id'),'source run identity required')
    prices=record['prices'];nodes=_nodes(prices);normalized={str(n):vs for n,vs in prices.items()}
    require(all(type(vs)is list and len(vs)==8 for vs in normalized.values()),'eight-quarter native price vector required')
    # Validate the whole native vector, but select only the first delivered quarter.
    for vs in normalized.values():
        for value in vs:finite(value)
    if historical:
        require(record.get('historical')is True and record.get('verified_history')is True and bool(record.get('source_artifacts')),'explicit independently verified historical-price provenance required')
        for value in record['source_artifacts'].values():_sha(value)
    return dict(nodes=nodes,prices={n:finite(vs[0])for n,vs in normalized.items()},source=dict(
        run_id=record['run_id'],window_index=record['window']['index'],start=record['window']['start'],end=record['window']['delivery_end'],
        source_quarter_index=0,occurrence=deepcopy(occurrence),input_sha256=record['input_sha256'],model_sha256=record['model_sha256'],
        source_record_sha256=digest(record),source_artifacts=deepcopy(record.get('source_artifacts',{})),historical=historical))


def verify_prices(price_records,*,day_start,verification_at,history=(),delay_reason=None):
    """Pure validation, not independent authentication of callers' raw artifacts.

    Known pending prices do not invalidate physical execution. A missing prior
    successful price produces an explicit unavailable result, never a zero.
    """
    start=stamp(day_start);require(start.hour==start.minute==start.second==start.microsecond==0,'day midnight required')
    verified=stamp(verification_at);days=(verified.date()-start.date()).days
    require(days==1 or(days==2 and type(delay_reason)is str and bool(delay_reason.strip())),'D+1 or explicit delayed D+2 verification required')
    require(days==2 or delay_reason is None,'delay reason belongs to D+2')
    records=list(price_records);require(records,'price records required')
    first_window,first_start,_=_window(records[0],start)
    anchors=[];seen_history=set()
    for item in history:
        _,at,end=_window(item)
        require(end<=first_start,'future or overlapping historical price')
        proof=_success(item,True);key=(proof['source']['run_id'],proof['source']['occurrence']['id'])
        require(key not in seen_history,'duplicate historical price');seen_history.add(key)
        anchors.append((at,proof))
    require(len({at for at,_ in anchors})==len(anchors),'ambiguous same-time history versions; select verified version explicitly')
    anchor=max(anchors,key=lambda z:z[0])[1] if anchors else None
    out=[];previous=None;run=None;seen_occurrences=set();native_successes=0;pending=0;substituted=0;unavailable=0;chain=0;max_chain=0
    for item in records:
        w,at,end=_window(item,start);require(end<=verified,'future delivery at verification')
        require(previous is None or w['index']==previous+1,'duplicate/gap/out-of-order price quarter')
        require(item.get('run_id') and(run is None or run==item['run_id']),'mixed current run identity')
        run=item['run_id'];previous=w['index']
        if item['status']=='success':
            proof=_success(item);key=proof['source']['occurrence']['id'];require(key not in seen_occurrences,'replayed successful PRICE occurrence')
            seen_occurrences.add(key);anchor=proof;origin='genuine_successful_first_quarter';native_successes+=1;chain=0
        else:
            require(item['status']=='pending' and item.get('reason')in PENDING_REASONS,'unapproved missing-price classification')
            require(item.get('native_outcome')=='known','unknown native outcome cannot be substituted')
            require(item.get('prices')is None,'pending record cannot contain fake price')
            nodes=_nodes(item['nodes'])
            if item['reason']=='confirmed_PRICE_Infeasible':
                occurrence=item.get('occurrence',{})
                require(occurrence.get('outcome')=='verified_native_response' and occurrence.get('native_status')=='Infeasible' and occurrence.get('returncode',0)==0,'confirmed native infeasible occurrence required')
            else:require(item.get('native_price_called',False)is False,'terminal-domain rejection must not claim native PRICE call')
            if anchor is not None:
                require(anchor['nodes']==nodes,'same-node substitution inventory mismatch')
                require(stamp(anchor['source']['end'])<=at,'future success cannot supply missing price')
            pending+=1;chain+=1;max_chain=max(max_chain,chain)
            origin='substituted_previous_genuine_success'if anchor is not None else 'unavailable_no_prior_success'
            if anchor is None:unavailable+=1
            else:substituted+=1
        out.append(dict(window=deepcopy(w),status='available'if anchor is not None else 'unavailable',
            prices_yuan_per_mwh=deepcopy(anchor['prices'])if anchor is not None else None,
            price_origin=origin,price_source=deepcopy(anchor['source'])if anchor is not None else None,
            pending_reason=item.get('reason')if item['status']=='pending'else None,
            original_price_status=item['status'],original_record_sha256=digest(item),
            source_age_minutes=(at-stamp(anchor['source']['start'])).total_seconds()/60 if anchor is not None else None,
            verified_at=verification_at,availability_is_simulated=True,official_publication=None,actual_settlement_yuan=None))
    result=dict(schema='gd.research.r45.post_price_verification.v1',day_start=day_start,verification_at=verification_at,delay_reason=delay_reason,
        policy=deepcopy(POLICY),records=out,price_complete=unavailable==0,whole_day_price_complete=unavailable==0 and len(out)==96 and out[0]['window']['index']==0,
        statistics=dict(quarters=len(out),genuine_successful_PRICE_windows=native_successes,pending_PRICE_windows=pending,
            substituted_quarters=substituted,unavailable_quarters=unavailable,maximum_consecutive_pending=max_chain,
            new_native_calls_by_verifier=0,physical_commands_issued_by_verifier=0),
        boundary='pure provenance validator; caller must authenticate all native and historical artifacts',formal_bill=None)
    result['content_sha256']=digest(result);return result


def reconcile_energy(completed_quarters,price_report):
    """Pure arithmetic on runtime-verified completed quarters, never telemetry.

    Runtime must verify full command/receipt-state reintegration before setting
    execution_verified. This function cannot authenticate that flag by itself.
    """
    require(price_report.get('schema')=='gd.research.r45.post_price_verification.v1','matching post-price report required')
    require(price_report['content_sha256']==digest({k:v for k,v in price_report.items()if k!='content_sha256'}),'post-price report mutated')
    price_by={r['window']['index']:r for r in price_report['records']}
    require(len(price_by)==len(price_report['records']),'duplicate price records')
    complete=list(completed_quarters);quarters=[];seen=set();prior=None;inventory=None;ids=set()
    for item in complete:
        w,start,end=_window(item,stamp(price_report['day_start']));idx=w['index']
        require(idx not in seen and(prior is None or idx==prior+1),'duplicate/gap/out-of-order completed quarter');seen.add(idx);prior=idx
        require(item.get('execution_verified')is True,'upstream complete receipt verification required')
        rt,da=item['RT_cells'],item['DA_cells'];require(len(rt)==len(da)==3,'exact three executed cells required')
        nodes=item['nodes'];require(set(nodes)=={'thermal','battery'},'signed thermal/battery inventory required')
        normalized={f:{r:str(n)for r,n in values.items()}for f,values in nodes.items()}
        require(inventory is None or inventory==normalized,'energy resource/node inventory changed');inventory=normalized
        for j,(r,d)in enumerate(zip(rt,da)):
            require(r['start']==d['start']==(start+timedelta(minutes=5*j)).isoformat() and r['end']==d['end']==(start+timedelta(minutes=5*(j+1))).isoformat(),'cell time mismatch')
            for family,key in [('thermal','thermal'),('battery','batteries')]:require(set(r[key])==set(d[key])==set(nodes[family]),'dense energy inventory required')
        for field in ('command_ids','receipt_ids'):
            if field in item:
                require(len(item[field])==3 and len(set(item[field]))==3,'three distinct execution identities required')
                for value in item[field]:require((field,value)not in ids,'duplicate global execution identity');ids.add((field,value))
        price=price_by.get(idx);require(price is not None and price['window']==w,'executed/price window mismatch')
        prices=price['prices_yuan_per_mwh'];rows=[]
        for family,key in [('thermal','thermal'),('battery','batteries')]:
            for resource,node in normalized[family].items():
                require(prices is None or node in prices,'energy node lacks verified price')
                for direction in (('export',)if family=='thermal'else('export','import')):
                    def volume(cells):
                        ps=[finite(c[key][resource]['mean_mw'if family=='thermal'else'power_mw'])for c in cells]
                        return math.fsum(max(v,0.)if direction=='export'else min(v,0.)for v in ps)/12
                    actual,planned=volume(rt),volume(da);p=None if prices is None else finite(prices[node])
                    rows.append(dict(kind=family,resource=resource,node=node,direction=direction,simulated_executed_mwh=actual,day_ahead_plan_mwh=planned,
                        deviation_mwh=actual-planned,rt_price_yuan_per_mwh=p,quarter_diagnostic_yuan=None if p is None else(actual-planned)*p,actual_metered_energy_mwh=None,settlement_yuan=None))
        quarters.append(dict(window=deepcopy(w),start=w['start'],end=w['delivery_end'],price_origin=price['price_origin'],rows=rows,execution_evidence_authentication='upstream_runtime_required'))
    hours=[];groups={}
    for q in quarters:groups.setdefault(stamp(q['start']).replace(minute=0).isoformat(),[]).append(q)
    for hour,qs in sorted(groups.items()):
        if len(qs)!=4 or stamp(qs[0]['start']).minute!=0:continue
        if any(r['rt_price_yuan_per_mwh']is None for q in qs for r in q['rows']):continue
        maps=[{(r['kind'],r['resource'],r['direction'],r['node']):r for r in q['rows']}for q in qs]
        require(all(set(m)==set(maps[0])for m in maps),'hourly inventory mismatch');rows=[]
        for key in maps[0]:
            rs=[m[key]for m in maps];mean=math.fsum(r['rt_price_yuan_per_mwh']for r in rs)/4
            actual=math.fsum(r['simulated_executed_mwh']for r in rs);planned=math.fsum(r['day_ahead_plan_mwh']for r in rs)
            rows.append(dict(kind=key[0],resource=key[1],direction=key[2],node=key[3],hourly_arithmetic_mean_price_yuan_per_mwh=mean,
                simulated_executed_mwh=actual,day_ahead_plan_mwh=planned,research_hourly_RT_deviation_yuan=(actual-planned)*mean,official_settlement_yuan=None))
        hours.append(dict(start=hour,end=(stamp(hour)+timedelta(hours=1)).isoformat(),rows=rows,research_component_total_yuan=math.fsum(r['research_hourly_RT_deviation_yuan']for r in rows)))
    return dict(schema='gd.research.r45.posthoc_energy_component.v1',executed_quarters=quarters,hourly_components=hours,
        time_coverage=dict(completed_quarters=len(quarters),complete_hours=len(hours)),whole_day=len(quarters)==96 and len(hours)==24,
        actual_energy_ledger=False,actual_metered_energy=None,formal_bill=None,official_publication=None,
        missing_components=dict(contracts=None,ancillary_performance=None,official_qualification=None),
        scope='simulated RT-minus-DA energy; four independently sourced or explicitly substituted quarter prices arithmetically averaged; signed BESS directions separate')
