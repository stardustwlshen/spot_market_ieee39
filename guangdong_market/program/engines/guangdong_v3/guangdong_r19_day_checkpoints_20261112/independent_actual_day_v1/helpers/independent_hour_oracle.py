"""Independent hourly arithmetic and chronology. No production imports or authority."""
from datetime import datetime,timedelta
from math import fsum,isfinite

def require(value,message):
    if not value:raise ValueError(message)
def stamp(value):
    result=datetime.fromisoformat(value);require(result.tzinfo is not None,'timezone required');return result
def close(a,b,label,tol=1e-7):
    require(isfinite(a) and isfinite(b) and abs(a-b)<=tol,label)
def key(row):return row['kind'],row['resource'],row['direction'],row['node']
def first_hour(executed,frozen):
    require(len(executed)==len(frozen)==4,'four executed and frozen quarters required')
    quarters=sorted(executed.values(),key=lambda q:stamp(q['start']));start=stamp(quarters[0]['start'])
    require(start.minute==start.second==start.microsecond==0,'aligned full hour')
    require(set(executed)==set(frozen),'exact executed/frozen inventory')
    maps=[];command_ids=[];receipt_ids=[];price_models=[]
    for i,q in enumerate(quarters):
        at=start+timedelta(minutes=15*i);end=at+timedelta(minutes=15)
        require(stamp(q['start'])==at and stamp(q['end'])==end,'contiguous quarter coverage')
        p=frozen[q['start']]
        require(stamp(p['start'])==at and stamp(p['end'])==end and stamp(p['available_at'])==at-timedelta(minutes=15),'cutoff-frozen first quarter only')
        require(stamp(q['recorded_at'])>=end,'unavailable quarter')
        require(p['price_model_sha256']==q['price_model_sha256'],'price model binding')
        require(p['lookahead_prices_frozen']==0 and q['lookahead_energy_counted'] is False and q['price_dispatch_used_as_execution'] is False,'no lookahead or price dispatch execution')
        require(q['actual_metered_energy_mwh'] is None and q['official_settlement_yuan'] is None,'research only')
        require(len(q['command_ids'])==len(q['receipt_ids'])==3,'three cells per quarter')
        command_ids.extend(q['command_ids']);receipt_ids.extend(q['receipt_ids']);price_models.append(q['price_model_sha256'])
        rows={key(r):r for r in q['rows']};require(len(rows)==len(q['rows'])>0,'duplicate or empty resource rows')
        require(not maps or set(rows)==set(maps[0]),'resource direction inventory mismatch')
        for (kind,resource,direction,node),r in rows.items():
            require(kind in ('thermal','battery') and direction in (('export',) if kind=='thermal' else ('export','import')),'resource direction')
            require(node in p['prices_yuan_per_mwh'],'node missing')
            close(r['rt_price_yuan_per_mwh'],p['prices_yuan_per_mwh'][node],'frozen nodal price')
            actual,da=r['simulated_executed_mwh'],r['day_ahead_plan_mwh']
            require(isfinite(actual) and isfinite(da),'finite quantities')
            require((min(actual,da)>=-1e-9) if direction=='export' else (max(actual,da)<=1e-9),'signed energy directions')
            close(actual-da,r['deviation_mwh'],'energy deviation')
            close((actual-da)*r['rt_price_yuan_per_mwh'],r['quarter_diagnostic_yuan'],'quarter diagnostic')
            require(r['settlement_yuan'] is None and r['quarter_settlement_eligibility'] is None,'unproved settlement qualification')
        for (kind,r,d,n) in rows:
            if kind=='battery':require((kind,r,'import',n) in rows and (kind,r,'export',n) in rows,'both storage directions required')
        maps.append(rows)
    require(len(set(command_ids))==len(set(receipt_ids))==12,'twelve distinct command/receipt IDs')
    result=[]
    for k in maps[0]:
        rs=[m[k] for m in maps];mean=fsum(r['rt_price_yuan_per_mwh'] for r in rs)/4
        actual=fsum(r['simulated_executed_mwh'] for r in rs);da=fsum(r['day_ahead_plan_mwh'] for r in rs)
        result.append(dict(kind=k[0],resource=k[1],direction=k[2],node=k[3],price_mean=mean,actual_mwh=actual,DA_mwh=da,deviation_mwh=actual-da,hourly_yuan=mean*(actual-da),quarter_sum_yuan=fsum(r['quarter_diagnostic_yuan'] for r in rs)))
    return dict(start=start.isoformat(),end=(start+timedelta(hours=1)).isoformat(),rows=result,hourly_total_yuan=fsum(r['hourly_yuan'] for r in result),formal_bill=None,whole_day=False)
