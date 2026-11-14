"""Immutable first-quarter simulated energy records, not a complete settlement.

Guangdong ordinary units use the hourly mean quarter price times hourly energy.
Quarterly diagnostics do not establish conditional storage 15-minute eligibility.
Contracts, DA prices, actual meters and ancillary-service performance stay None.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo
from hashlib import sha256
from weakref import WeakKeyDictionary
from .gd_r13_pricing import canonical, require, number, stamp, accepted_price_report
from .gd_da_battery_candidate import digest as rt_source_digest

_LEDGERS=WeakKeyDictionary()


def digest(x):return sha256(canonical(x)).hexdigest()


@dataclass(eq=False)
class ResearchEnergyLedger:
    data: dict


def new_energy_ledger():
    obj=ResearchEnergyLedger(dict(schema='gd.r13.simulated_RT_energy_component.v1',frozen_prices={},executed_quarters={},official_settlement_yuan=None,whole_day=False))
    _LEDGERS[obj]=dict(seal=digest(obj.data),sources={});return obj


def _check(ledger):
    require(type(ledger)is ResearchEnergyLedger and ledger in _LEDGERS,'live ledger required')
    require(digest(ledger.data)==_LEDGERS[ledger]['seal'],'immutable energy/price ledger mutated')
    return ledger.data


def _seal(ledger):_LEDGERS[ledger]['seal']=digest(ledger.data)


def freeze_first_quarter(ledger,price_solution,rt_solution,chain):
    """At cutoff freeze only first-quarter price; never settle lookahead rows."""
    from .gd_r13_causal_rt import accepted_rt_report,pricing_source,chain_report
    d=_check(ledger);p=accepted_price_report(price_solution);r=accepted_rt_report(rt_solution);source=pricing_source(rt_solution);h=chain_report(chain)
    require(p['source_kind']=='issued_native_RT' and p['source_id']==r['physical_model_sha256'],'price requires same live native accepted RT source')
    require(p['probe']is None,'diagnostic perturbation price cannot enter ledger')
    require(p['start']==r['start'] and stamp(h['clock'])==stamp(r['cutoff'])==stamp(p['price_available_at']),'freeze must occur at live declared availability without backdating')
    key=r['start'];require(key not in d['frozen_prices'],'quarter price already frozen; overlapping/lookahead overwrite prohibited')
    require(not any(stamp(x['at'])>stamp(key)for x in h['receipts']),'cannot set price after execution')
    record=dict(start=key,end=(stamp(key)+timedelta(minutes=15)).isoformat(),available_at=p['price_available_at'],prices_yuan_per_mwh={n:vs[0]for n,vs in p['prices'].items()},price_model_sha256=p['model_sha256'],rt_report_sha256=rt_source_digest(r),rt_input_sha256=r['input_sha256'],status='frozen_research_price_not_official',lookahead_prices_frozen=0,source_clock='declared_synthetic_research_clock')
    d['frozen_prices'][key]=record;_LEDGERS[ledger]['sources'][key]=dict(rt_solution=rt_solution,price_solution=price_solution,chain=chain,da_cells=deepcopy(source['da_cells'][:3]),nodes=dict(thermal={g:u['bus']for g,u in source['energy_inputs']['thermal'].items()},battery={b:u['bus']for b,u in source['batteries'].items()}));_seal(ledger)
    return deepcopy(record)


def quarter_energy_rows(cells,da_cells,nodes,prices):
    """Pure signed first-quarter diagnostic, not proof of simulated execution."""
    require(len(cells)==len(da_cells)==3,'exact first three committed five-minute cells required')
    start=stamp(cells[0]['start'])
    require(start.minute%15==0,'quarter-aligned energy')
    for j,(r,da)in enumerate(zip(cells,da_cells)):
        require(stamp(r['start'])==start+j*timedelta(minutes=5) and stamp(r['end'])==start+(j+1)*timedelta(minutes=5) and r['start']==da['start'] and r['end']==da['end'],'exact DA/executed time identity')
    rows=[]
    for family,key in [('thermal','thermal'),('battery','batteries')]:
        require(all(set(c[key])==set(nodes[family])for c in cells+da_cells),'full resource coverage, no discarded units')
        for resource,node in nodes[family].items():
            require(node in prices,'missing frozen node price');price=number(prices[node],'price')
            directions=('export',)if family=='thermal'else ('export','import')
            for direction in directions:
                def volume(rs):
                    ps=[number(r[key][resource]['mean_mw']if family=='thermal'else r[key][resource]['power_mw'],'energy power')for r in rs]
                    return sum((max(p,0.)if direction=='export'else min(p,0.))/12 for p in ps)
                actual=volume(cells);da=volume(da_cells)
                rows.append(dict(resource=resource,node=node,kind=family,direction=direction,simulated_executed_mwh=actual,day_ahead_plan_mwh=da,deviation_mwh=actual-da,rt_price_yuan_per_mwh=price,quarter_diagnostic_yuan=(actual-da)*price,quarter_settlement_eligibility=None,settlement_yuan=None))
    return rows


def record_executed_quarter(ledger,chain,start):
    from .gd_r13_causal_rt import chain_report,accepted_rt_report
    d=_check(ledger);require(start in d['frozen_prices'],'price not frozen before execution');require(start not in d['executed_quarters'],'quarter already recorded; double count prohibited')
    binding=_LEDGERS[ledger]['sources'][start];require(binding['chain']is chain,'different execution chain')
    h=chain_report(chain);f=d['frozen_prices'][start];r=accepted_rt_report(binding['rt_solution'])
    require(rt_source_digest(r)==f['rt_report_sha256'],'frozen source report changed');accepted_price_report(binding['price_solution'])
    commands=[];receipts=[]
    for j in range(3):
        at=stamp(start)+j*timedelta(minutes=5)
        cs=[c for c in h['commands']if stamp(c['cell']['start'])==at and c['native_source_sha256']==f['rt_report_sha256']]
        require(len(cs)==1,'first-quarter command absent/overlapping/different source');cmd=cs[0]
        rs=[v for v in h['receipts']if v['command_id']==cmd['id'] and v['command_sha256']==rt_source_digest(cmd)]
        require(len(rs)==1,'simulated execution receipt absent or duplicate');receipt=rs[0]
        require(receipt['is_measurement']is False and receipt['kind']=='synthetic_zero_tracking_error_receipt','unexpected execution evidence kind')
        require(stamp(receipt['at'])==stamp(cmd['cell']['end'])<=stamp(h['clock']) and stamp(receipt['available_at'])<=stamp(h['clock']),'receipt unavailable or nonmatching cell')
        commands.append(cmd);receipts.append(receipt)
    rows=quarter_energy_rows([c['cell']for c in commands],binding['da_cells'],binding['nodes'],f['prices_yuan_per_mwh'])
    result=dict(start=start,end=f['end'],recorded_at=h['clock'],status='simulated_RT_deviation_energy_diagnostic',price_model_sha256=f['price_model_sha256'],receipt_ids=[r['id']for r in receipts],command_ids=[c['id']for c in commands],rows=rows,actual_metered_energy_mwh=None,official_settlement_yuan=None,lookahead_energy_counted=False)
    d['executed_quarters'][start]=result;_seal(ledger);return deepcopy(result)


def hourly_component(quarters):
    """Four complete source-matched quarter records; hourly, not Q-weighted price."""
    require(len(quarters)==4,'four complete quarters required for hourly component')
    start=stamp(quarters[0]['start']);require(start.minute==start.second==start.microsecond==0,'whole aligned hour required')
    for i,q in enumerate(quarters):require(stamp(q['start'])==start+i*timedelta(minutes=15) and stamp(q['end'])==start+(i+1)*timedelta(minutes=15),'gap/overlap in hourly coverage')
    def key(r):return r['kind'],r['resource'],r['direction'],r['node']
    maps=[{key(r):r for r in q['rows']}for q in quarters];require(all(set(m)==set(maps[0])for m in maps),'hour resource inventory mismatch')
    rows=[]
    for k in maps[0]:
        rs=[m[k]for m in maps];price=sum(r['rt_price_yuan_per_mwh']for r in rs)/4;actual=sum(r['simulated_executed_mwh']for r in rs);da=sum(r['day_ahead_plan_mwh']for r in rs)
        rows.append(dict(kind=k[0],resource=k[1],direction=k[2],node=k[3],hourly_arithmetic_mean_price_yuan_per_mwh=price,simulated_executed_mwh=actual,day_ahead_plan_mwh=da,research_hourly_RT_deviation_yuan=(actual-da)*price,quarter_diagnostic_sum_yuan=sum(r['quarter_diagnostic_yuan']for r in rs),price_time_basis='Guangdong2026 settlement section5.3.3 hourly arithmetic mean',metered_energy_mwh=None,official_settlement_yuan=None))
    return dict(start=start.isoformat(),end=(start+timedelta(hours=1)).isoformat(),status='hourly_simulated_RT_deviation_component_not_official_settlement',rows=rows,research_component_total_yuan=sum(r['research_hourly_RT_deviation_yuan']for r in rows))


def derive_time_coverage(report, expected_day_start=None):
    """Pure coverage metadata for live or independently validated stitched data.

    This grants no acceptance, settlement eligibility or official bill status.
    Collection order is irrelevant. Only complete executed quarter records and
    their matching frozen prices count; future indicative prices do not count.
    """
    details=dict(basis='executed_quarters_and_matching_frozen_prices_only',
        executed_quarters=0,frozen_quarters=0,day_start=None,day_end=None,
        official_settlement_complete=False,reason='incomplete_quarter_coverage')
    result=dict(whole_day=False,time_coverage=details)
    try:
        executed=report['executed_quarters'];frozen=report['frozen_prices']
        require(isinstance(executed,dict) and isinstance(frozen,dict),'invalid coverage collections')
        details.update(executed_quarters=len(executed),frozen_quarters=len(frozen))
        if not frozen:return result
        shanghai=ZoneInfo('Asia/Shanghai')
        anchor=stamp(expected_day_start or report.get('day_start') or min(frozen,key=lambda k:stamp(k))).astimezone(shanghai)
        if expected_day_start is None and report.get('day_start')is None:anchor=anchor.replace(hour=0,minute=0,second=0,microsecond=0)
        require(anchor.hour==anchor.minute==anchor.second==anchor.microsecond==0,'day anchor not Shanghai midnight')
        details.update(day_start=anchor.isoformat(),day_end=(anchor+timedelta(days=1)).isoformat())
        if len(executed)!=96 or len(frozen)!=96:return result
        expected={anchor+timedelta(minutes=15*i)for i in range(96)}
        em={stamp(k):v for k,v in executed.items()};fm={stamp(k):v for k,v in frozen.items()}
        require(len(em)==len(fm)==96 and set(em)==set(fm)==expected,'wrong day, duplicate, gap or overlapping quarter')
        command_ids=[];receipt_ids=[]
        for at in sorted(expected):
            q,p=em[at],fm[at];end=at+timedelta(minutes=15)
            require(stamp(q['start'])==stamp(p['start'])==at and stamp(q['end'])==stamp(p['end'])==end,'quarter interval mismatch')
            require(stamp(p['available_at'])<=at and stamp(q['recorded_at'])>=end,'price/quantity not available in required order')
            require(q['price_model_sha256']==p['price_model_sha256'],'frozen price model mismatch')
            require(q['status']=='simulated_RT_deviation_energy_diagnostic' and not q['lookahead_energy_counted'],'not an executed quarter record')
            require(len(q['command_ids'])==len(q['receipt_ids'])==3,'three actual simulated receipt records required')
            require(bool(q['rows']) and all(r['node']in p['prices_yuan_per_mwh'] and r['rt_price_yuan_per_mwh']==p['prices_yuan_per_mwh'][r['node']]for r in q['rows']),'recorded/frozen node price mismatch')
            command_ids.extend(q['command_ids']);receipt_ids.extend(q['receipt_ids'])
        require(len(set(command_ids))==len(set(receipt_ids))==288,'duplicate executed command or receipt')
        result['whole_day']=True;details['reason']='complete_96_executed_quarters_with_matching_frozen_prices'
    except (ValueError,KeyError,TypeError,AttributeError)as exc:details['reason']=str(exc)
    return result


def energy_ledger_report(ledger):
    d=deepcopy(_check(ledger));quarters=list(d['executed_quarters'].values());hours={}
    for q in quarters:hours.setdefault(stamp(q['start']).replace(minute=0,second=0,microsecond=0).isoformat(),[]).append(q)
    d['hourly_components']=[hourly_component(sorted(qs,key=lambda q:stamp(q['start'])))for _,qs in sorted(hours.items())if len(qs)==4]
    d.update(derive_time_coverage(d))
    d['missing_components']=dict(independent_DA_prices=None,contract_quantities=None,contract_prices=None,actual_metered_energy=None,ancillary_service_performance=None,storage_quarter_settlement_eligibility=None,official_publication=None)
    d['scope']=dict(provincial_only=True,simulated_quantity_not_meter=True,first_quarter_only=True,missing_assumed_zero=False,partial_component_not_bill=True,quarter_outputs_not_official_settlement=True,rule='Guangdong2026 settlement sections5.2,5.3.3,7.2.5,7.3.3',rule_source_class='repost_not_official_domain')
    return d


def record_available_quarters(ledger,chain):
    from .gd_r13_causal_rt import chain_report
    h=chain_report(chain);d=_check(ledger);written=[]
    for start,record in sorted(d['frozen_prices'].items()):
        if start not in d['executed_quarters'] and stamp(record['end'])<=stamp(h['clock']):written.append(record_executed_quarter(ledger,chain,start))
    return written


def pricing_callback(policy,ledger,*,time_limit_s=60.):
    """Runner callback after accepted RT and BEFORE issue at unchanged cutoff."""
    from pathlib import Path
    from .gd_r13_pricing import run_window_pricing
    def after_rt(solution,chain,output_window):
        record_available_quarters(ledger,chain)
        solved=run_window_pricing(solution,Path(output_window)/'independent_pricing',policy,time_limit_s=time_limit_s)
        freeze_first_quarter(ledger,solved,solution,chain)
        (Path(output_window)/'energy_ledger_checkpoint.json').write_bytes(canonical(energy_ledger_report(ledger)))
        return solved
    return after_rt
