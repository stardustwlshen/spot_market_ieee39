"""VRE readiness and explicitly synthetic participating-DA arithmetic only."""
from decimal import Decimal
from r47_price import aware,dec

def vre_readiness(data):
    missing=[]
    if data.get('participation') not in ('participates','does_not_participate','unknown'):raise ValueError('participation enum required')
    if data['participation']=='unknown':missing.append('participation')
    mapping=data.get('mapping',{})
    for k in ('unit','resource','node','source'):
        if not mapping.get(k):missing.append('mapping.'+k)
    if mapping.get('identity_kind')!='real_unit':missing.append('real_unit_identity (bus aggregation is synthetic only)')
    if data.get('quantity_kind')!='metered':missing.append('metered_quantity')
    if data.get('actual_meter') is None:missing.append('actual_meter')
    if data.get('financial_DA') is None:missing.append('financial_DA')
    if data.get('contracts') is None:missing.append('contracts (unknown is not zero)')
    elif not data.get('contracts_source'):missing.append('contracts_source (including known zero)')
    if data.get('price_kind')!='verified_settlement':missing.append('verified_settlement_prices')
    separate={k:data.get(k) for k in ('monthly_mechanism','green_certificates','AGC_mileage')}
    route='needs_nonDA_rule_mapping'if data['participation']=='does_not_participate'else 'needs_inputs'if missing else 'external formal validation required'
    return dict(amount=None,missing=missing,route=route,formal_bill=None,payable=False,separate_unclosed_components=separate)

def synthetic_participating_DA(*,participation,node,start,end,quantity_kind,params,contracts,contracts_source):
    """Each scalar has value/unit/source/node/start/end; fixture only, never payable."""
    if participation=='does_not_participate':return dict(route='needs_nonDA_rule_mapping',amount=None,formal_bill=None,payable=False)
    if participation!='participates':return dict(route='participation_unknown',amount=None,formal_bill=None,payable=False)
    aware(start);aware(end)
    if end<=start or not node or quantity_kind!='synthetic_metered_fixture':raise ValueError('explicit synthetic metered fixture required')
    if contracts is None or not contracts_source:raise ValueError('contract inventory and source required; unknown != zero')
    def value(item,unit,at_node=None):
        expected_node=node if at_node is None else at_node
        if not isinstance(item,dict) or any(item.get(k)!=v for k,v in [('node',expected_node),('start',start),('end',end),('unit',unit)]) or not item.get('source'):raise ValueError('parameter interval/node/unit/source mismatch')
        return dec(item['value'])
    qda=value(params['QDA'],'MWh');qm=value(params['Qmeter'],'MWh');pda=value(params['PDA'],'CNY/MWh');prt=value(params['PRT'],'CNY/MWh')
    if qda<0 or qm<0:raise ValueError('negative VRE generation quantity')
    lt=Decimal(0);ql=Decimal(0)
    seen=set()
    for c in contracts:
        cid=c.get('contract_id');ref=c.get('reference_node')
        if not cid or cid in seen or not ref:raise ValueError('unique contract_id and reference_node required')
        seen.add(cid)
        if c.get('reference_market')not in ('DA','RT') or c['Pref'].get('reference_market')!=c['reference_market']:raise ValueError('explicit matching reference_market required')
        q=value(c['Q'],'MWh');pc=value(c['Pc'],'CNY/MWh');pref=value(c['Pref'],'CNY/MWh',ref);ql+=q;lt+=q*(pc+pda-pref)
    da=(qda-ql)*pda;rt=(qm-qda)*prt
    return dict(route='synthetic_participating_DA_arithmetic_only',LT=lt,DA=da,RT=rt,amount=lt+da+rt,formal_bill=None,payable=False,monthly_mechanism=None,green_certificates=None,AGC_mileage=None)

def bind_r45_vre(path,expected_sha,bus):
    """Read hash-pinned already-verified simulated receipts; no official promotion."""
    import json,hashlib
    from pathlib import Path
    from datetime import datetime,timedelta
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('R45 source hash mismatch')
    d=json.loads(raw);qs=d['executed_quarters']
    if len(qs)!=4:raise ValueError('four executed quarters required')
    first=datetime.fromisoformat(qs[0]['window']['start']);aware(first);run=None;out=[]
    for i,q in enumerate(qs):
        w=q['window'];start=datetime.fromisoformat(w['start']);end=datetime.fromisoformat(w['delivery_end']);aware(start);aware(end)
        if start!=first+timedelta(minutes=15*i) or end-start!=timedelta(minutes=15) or w['index']!=qs[0]['window']['index']+i:raise ValueError('quarter gap')
        commands=q['command_ids'];receipts=q['receipt_ids']
        if len(commands)!=3 or len(set(commands))!=3 or receipts!=[c+'-SIMULATED-RECEIPT'for c in commands]:raise ValueError('receipt identity mismatch')
        prefix=commands[0].rsplit('-W',1)[0]
        if run is None:run=prefix
        if prefix!=run or commands!=[f'{run}-W{w["index"]}-RT-{k}'for k in range(3)]:raise ValueError('mixed run/command scope')
        v=q['renewable'][str(bus)]
        if v['actual_metered_mwh']is not None or q['market_price']is not None or q['settlement_price']is not None:raise ValueError('unexpected official/metered data')
        nums={k:dec(Decimal(str(v[k])))for k in ('synthetic_available_mwh','simulated_dispatched_mwh','simulated_curtailed_mwh','physical_DA_reference_mwh')}
        if any(nums[k]<0 for k in nums) or abs(nums['synthetic_available_mwh']-nums['simulated_dispatched_mwh']-nums['simulated_curtailed_mwh'])>Decimal('0.00000001'):raise ValueError('available != dispatched + curtailed')
        readiness=vre_readiness(dict(participation=v['financial_DA_participation'],mapping={'unit':'bus_aggregate_'+str(bus),'resource':'VRE_bus_'+str(bus),'node':str(bus),'source':expected_sha,'identity_kind':'synthetic_aggregate'},quantity_kind='simulated_executed',actual_meter=None,financial_DA=None,contracts=None,price_kind='raw_only'))
        status=q['raw_price_status']
        if status=='unpriced_pending':
            if q['raw_unclipped_PRICE'] is not None:raise ValueError('pending price must be None')
            raw_price=None;readiness['missing'].append('raw_PRICE_pending')
        elif status=='available_raw_only':raw_price=dec(Decimal(str(q['raw_unclipped_PRICE'][str(bus)])))
        else:raise ValueError('unknown price status')
        out.append(dict(window=w['index'],start=w['start'],end=w['delivery_end'],bus=str(bus),run_id=run,command_ids=commands,receipt_ids=receipts,quantities=nums,raw_price=raw_price,quantity_kind='simulated_executed',readiness=readiness))
    return dict(source_sha256=expected_sha,rows=out,formal_bill=None,payable=False)
