"""Extract energy LMPs from the accepted dispatch LP; never build or solve PRICE.

Simulation convention: settle executed five-minute energy at its own nodal LMP.
Quarter mean is for display only; mean(price)*sum(energy) is not exact settlement.
No claim of literal compliance with Guangdong independent pricing rules.
"""
import math
import re

_BALANCE = re.compile(r"c_balance\['([^']+)',(\d+)\]")

def extract(model, names, response, *, duration_h, executed_ticks, source_pins):
    """Inputs are the exact SCED request/response and corresponding dispatch names.

    duration_h is a map keyed by actual model tick, read from derived-case horizon.
    source_pins binds the serialized request, response, names and derived case.
    The caller must accept the SCED pair before invoking this extractor.
    """
    if response.get('status') != 'optimal' or response.get('dual_valid') is not True:
        raise ValueError('accepted optimal SCED LP with valid duals required')
    if not model['objective']['minimize'] or any(v['kind'] != 'C' for v in model['variables']):
        raise ValueError('fixed-integer continuous minimizing SCED required')
    ticks=tuple(executed_ticks)
    if len(ticks)!=3 or ticks!=tuple(range(ticks[0],ticks[0]+3)) or ticks[0]%3:
        raise ValueError('exact first three executed five-minute ticks required')
    if not source_pins or any(not isinstance(v,str) or len(v)!=64 for v in source_pins.values()):
        raise ValueError('request/response/names/case evidence hashes required')
    constraints=names['constraints']; dual=response['dual']; primal=response['primal']
    if len(constraints)!=len(model['rows']) or len(dual)!=len(constraints) or len(primal)!=len(model['variables']):
        raise ValueError('SCED matrix/name/witness dimensions differ')
    cells={t:{} for t in ticks}; raw={t:{} for t in ticks}; signs={t:{} for t in ticks}; seen=set()
    for i,name in enumerate(constraints):
        hit=_BALANCE.fullmatch(name)
        if not hit: continue
        bus,t=hit[1],int(hit[2])
        if t not in cells: continue
        if (bus,t) in seen: raise ValueError('duplicate nodal balance')
        seen.add((bus,t)); dt=duration_h[t]; row=model['rows'][i]
        if not math.isfinite(dt) or abs(dt-1/12)>1e-12: raise ValueError('five-minute duration must come from model horizon')
        if row['lb'] is None or row['lb']!=row['ub']: raise ValueError('nodal balance must be equality')
        # Pyomo serializes this model as exports - variable injections = -net demand.
        # Verify an actual variable-injection coefficient rather than assume dual sign.
        injections=[coef for j,coef in row['terms'] if names['variables'][j].startswith(('power[','renewable_power['))]
        if not injections or abs(abs(injections[0])-1)>1e-12 or any(abs(c-injections[0])>1e-12 for c in injections):
            raise ValueError('unreviewed balance orientation/scaling')
        if not math.isfinite(dual[i]): raise ValueError('nonfinite nodal dual')
        activity=math.fsum(primal[j]*coef for j,coef in row['terms'])
        if abs(activity-row['lb'])>1e-6: raise ValueError('SCED nodal witness infeasible')
        sign=injections[0]
        cells[t][bus]=sign*dual[i]/dt;raw[t][bus]=dual[i];signs[t][bus]=sign
    buses=set(cells[ticks[0]])
    if not buses or any(set(cells[t])!=buses for t in ticks): raise ValueError('incomplete executed nodal grid')
    mean={b:math.fsum(cells[t][b]*duration_h[t] for t in ticks)/math.fsum(duration_h[t] for t in ticks) for b in sorted(buses)}
    return dict(schema='gd.direct_sced_prices.v1',method='fixed_integer_SCED_dual',additional_native_calls=0,
                executed_ticks=list(ticks),duration_h={t:duration_h[t] for t in ticks},
                five_minute_prices=cells,raw_prices_by_tick=cells,balance_rhs_duals=raw,demand_rhs_derivative=signs,raw_prices=mean,
                quarter_price_role='duration_weighted_display_only',units='yuan/MWh',
                dual_conversion='injection_row_coefficient * balance_rhs_dual / duration_h',
                settlement_rule='sum(executed_5min_MWh * matching_5min_nodal_price)',
                source_pins=source_pins,formal_market_rule_certified=False)

def settle_executed_energy(prices, bus, power_mw):
    """Signed net injection: discharging positive, charging negative. No fees inferred."""
    ticks=prices['executed_ticks']; dt=prices['duration_h']; cells=prices['five_minute_prices']
    get=lambda d,k:d[k] if k in d else d[str(k)]
    if set(map(int,power_mw))!=set(ticks): raise ValueError('exact executed power samples required')
    if any(not math.isfinite(get(power_mw,t)) for t in ticks): raise ValueError('finite power required')
    return math.fsum(get(power_mw,t)*get(dt,t)*get(cells,t)[str(bus)] for t in ticks)

def _read(path):
    import gzip,json
    from pathlib import Path
    p=Path(path);b=p.read_bytes()
    return json.loads(gzip.decompress(b) if p.suffix=='.gz' else b)

def _raw_sha(path):
    import gzip,hashlib
    from pathlib import Path
    p=Path(path);b=p.read_bytes()
    return hashlib.sha256(gzip.decompress(b) if p.suffix=='.gz' else b).hexdigest()

def _unpack(v):
    if isinstance(v,dict) and 'tag' in v:
        if v['tag']=='dict': return {_unpack(k):_unpack(x) for k,x in v['value']}
        if v['tag']=='tuple': return tuple(_unpack(x) for x in v['value'])
        if v['tag']=='list': return [_unpack(x) for x in v['value']]
    return v

def build_export(prepared, actual, *, delivery_tick, source_snapshot=None):
    """Read exact dispatch artifacts; validate accepted binding and all-model KKT.

    delivery_tick is the absolute day tick of the first executed local model tick.
    Existing responses are never rewritten or re-solved.
    """
    import hashlib,importlib.util,json
    from pathlib import Path
    prepared=Path(prepared).resolve();actual=Path(actual).resolve()
    choose=lambda stem:next((p for p in [stem,Path(str(stem)+'.gz')] if p.exists()))
    req=choose(actual/'SCED/request.json');resp=choose(actual/'SCED/response.json')
    names=prepared/'names.json';case=prepared/'derived_case.json';accepted=actual/'ACCEPTED_PAIR.json'
    m,n,r=_read(req),_read(names),_read(resp);acc=_read(accepted)
    binding=acc.get('dual_certificate',{}).get('binding',{})
    if binding.get('model_sha256')!=_raw_sha(req) or binding.get('names_sha256')!=hashlib.sha256(json.dumps(n,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() or binding.get('native_response_sha256')!=_raw_sha(resp):
        raise ValueError('SCED acceptance does not bind this exact native LP response')
    kktfile=Path(__file__).parent/'lifecycle_runtime/guangdong_network_dual_certificate_v1_20261113/strict_kkt.py'
    spec=importlib.util.spec_from_file_location('_direct_sced_kkt',kktfile);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    kkt=mod.metrics(m,r,n,angle_tolerance=1e-8)
    if not kkt['passed']: raise ValueError('native SCED KKT failed; no direct prices')
    c=_unpack(_read(case)['value']);periods=c['energy_inputs']['horizon']['periods'];dt={v['index']:v['duration_h'] for v in periods};ticks=[v['index'] for v in periods if v['settle']]
    files=[req,resp,names,case,accepted,kktfile,Path(__file__).resolve()]
    pins={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    result=extract(m,n,r,duration_h=dt,executed_ticks=ticks,source_pins=pins)
    if type(delivery_tick)!=int or not 0<=delivery_tick<288 or delivery_tick%3:
        raise ValueError('absolute day delivery tick required')
    shift=delivery_tick-ticks[0];result['model_executed_ticks']=ticks
    for key in ['five_minute_prices','raw_prices_by_tick','balance_rhs_duals','demand_rhs_derivative','duration_h']:
        result[key]={int(t)+shift:v for t,v in result[key].items()}
    result['executed_ticks']=[t+shift for t in ticks]
    result.update(KKT=kkt,native_status=r.get('native_status'),context=dict(prepared=str(prepared),actual=str(actual),delivery_tick=delivery_tick,day_quarter=delivery_tick//3,SCED_response_sha256=_raw_sha(resp),run_id=acc['run_id'],RT_dual_used=True,seed_price_from_selected_DA=False),formal_bill=None,payable=False)
    if source_snapshot is not None:
        from datetime import datetime,timedelta
        sf=Path(source_snapshot).resolve();z=_read(sf)
        if z['accepted']['SCED_response_sha256']!=_raw_sha(resp) or z['request']['delivery_tick']!=delivery_tick:
            raise ValueError('source snapshot does not match accepted same-period SCED')
        if z['window']!=delivery_tick//3 or z['request'].get('rt_clock_mode')!='same_period_execution':
            raise ValueError('direct pricing requires same-period execution clock')
        # Verify executed power values against the exact primal, not only identity labels.
        for j,name in enumerate(n['variables']):
            if not name.startswith('power['):continue
            match=re.fullmatch(r'power\[([^,]+),(\d+)\]',name)
            if match and int(match[2]) in ticks:
                absolute=f"power[{match[1]},{int(match[2])+shift}]"
                if absolute not in z['values'] or abs(z['values'][absolute]-r['primal'][j])>1e-7:
                    raise ValueError('executed power/snapshot mismatch')
        result['source_pins'][str(sf)]=hashlib.sha256(sf.read_bytes()).hexdigest()
        start=datetime.fromisoformat(z['request']['origin'])+timedelta(minutes=5*delivery_tick)
        result['context'].update(native_run_id=acc['run_id'],run_id=z['accepted']['run_id'],source_snapshot=str(sf),short_run=z['branch'],selected_DA=z['selected_DA'],base_run=z['base_run'],start=start.isoformat(),end=(start+timedelta(minutes=15)).isoformat(),price_producer='gd.live_host.SCED_dual.v1',boundary_mode='direct_fixed_integer_SCED_dual')
    return result

def export(prepared, actual, output, *, delivery_tick, source_snapshot=None):
    import json
    from pathlib import Path
    value=build_export(prepared,actual,delivery_tick=delivery_tick,source_snapshot=source_snapshot)
    with Path(output).open('x') as f:json.dump(value,f,indent=2,allow_nan=False)
    return value

def verify_export(path):
    import hashlib,json
    from pathlib import Path
    stored=_read(path)
    if stored.get('schema')!='gd.direct_sced_prices.v1': raise ValueError('direct SCED schema required')
    for name,expected in stored['source_pins'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=expected:raise ValueError('direct SCED source changed: '+name)
    c=stored['context'];fresh=build_export(c['prepared'],c['actual'],delivery_tick=c['delivery_tick'],source_snapshot=c.get('source_snapshot'))
    # JSON normalizes integer keys; exact comparison forbids edited prices/intervals.
    if json.loads(json.dumps(fresh))!=stored:raise ValueError('direct SCED exported evidence does not reproduce')
    return stored
