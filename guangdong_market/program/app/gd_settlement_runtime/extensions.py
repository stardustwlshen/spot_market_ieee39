"""Explicit research-input adapters; no formal data, authorization, or solver."""
import importlib.util,sys,math
from pathlib import Path
from datetime import datetime
from decimal import Decimal as D
B=Path(__file__).resolve().parent
name='_gd_settlement_extension_rules'
spec=importlib.util.spec_from_file_location(name,B/'vendor/rules/__init__.py',submodule_search_locations=[str(B/'vendor/rules')]);pkg=importlib.util.module_from_spec(spec);sys.modules[name]=pkg;spec.loader.exec_module(pkg)
from importlib import import_module
O=import_module(name+'.gd_october_settlement_rules');P=import_module(name+'.gd_price_research');C=import_module(name+'.gd_energy_cash')
def need(ok,msg):
    if not ok:raise ValueError(msg)
def num(x):
    need(not isinstance(x,bool),'bool not number');v=D(str(x));need(v.is_finite(),'finite number');return v
def evidence(fields,k):
    z=fields.get(k,{})
    if z.get('classification')=='missing' or 'value'not in z:return P.EvidenceValue(None,'missing',z.get('source','not supplied'),k+' missing')
    return P.EvidenceValue(float(num(z['value'])),'research',z['source'])
def missing(fields,keys):return [k for k in keys if k not in fields or fields[k].get('classification')=='missing']
def validate_fields(session,fields):
    session.check();session.fields(fields)
    for k,z in fields.items():
        need(z.get('classification')in ('measured','external_publication','research','missing'),'classification')
        if z['classification']!='missing':need(z.get('run_id')==session.run_id and z.get('source')and 'value'in z,'source/run/value: '+k)

def renewable(session,fields):
    validate_fields(session,fields)
    req=['account','participation','node_mapping'];miss=missing(fields,req)
    if miss:return session.result(status='missing',missing=miss)
    a=fields['account']['value'];part=fields['participation']['value']
    need(part=='does_not_participate_DA','explicit non-DA VRE only; do not reuse participating DA cash')
    need(a['identity_kind']in ('model_aggregate','supplied_subject'),'explicit aggregate versus subject identity')
    need(a['resource'] and a['settlement_unit'] and a['node'],'account identity')
    need(a['node']in {r['node']for r in session.h['rows']},'node outside selected source')
    if a['identity_kind']=='model_aggregate':need(any(r['resource']==a['resource']and r['node']==a['node']and r['technology']=='vre'for r in session.h['rows']),'model aggregate identity mismatch')
    else:need(a.get('registry_source'),'subject registry mapping source required; not authenticated by this API')
    start,end=map(datetime.fromisoformat,session.period);period=start.isoformat()
    key=C.SettlementKey(a['resource'],'renewable',a['settlement_unit'],a['node'],'export',period,start,end)
    q=evidence(fields,'actual_export_mwh');price=evidence(fields,'rt_price_yuan_per_mwh')
    selected=P.SelectedCashPrice('RT',a['node'],period,price,'unavailable'if price.kind=='missing'else'conditional_marginal_research',fields.get('rt_price_yuan_per_mwh',{}).get('source','missing'),session.run_id)
    z=O.calculate_price_taking_renewable_cash(keys=(key,),meters=(O.RenewableMeterInput(key,q),),prices=(C.EnergyPriceInput(key,selected,fields['node_mapping']['source']),),participation_source=fields['participation']['source'],node_mapping_source=fields['node_mapping']['source'],scope_source=fields['account']['source'],interval_scope='explicit_intervals')
    return session.result(status=z['status'],component=z,identity=a,classification_claims={k:v['classification']for k,v in fields.items()},other_components={k:dict(amount=None,status='not_implemented_or_unmapped',missing=['separate source and applicable formula required; not assumed zero'])for k in ('contracts','base_quantity','mechanism_difference','green_certificate','monthly_adjustments')},net_bill=None,participating_hour_ledger_replaced=False)

def offer_integral(cells,period):
    """Piecewise constant power over explicit subintervals; energy price times MW times h.
    This assumption must be declared, not inferred from an hourly average or dispatch endpoint.
    """
    need(isinstance(cells,list)and cells,'nonempty interval cells');cursor=datetime.fromisoformat(period[0]);end=datetime.fromisoformat(period[1]);gross=D(0);energy=D(0);out=[]
    for c in cells:
        lo=datetime.fromisoformat(c['start']);hi=datetime.fromisoformat(c['end']);need(lo.utcoffset()is not None and hi.utcoffset()is not None and lo==cursor and lo<hi<=end,'contiguous whole-hour cells')
        need(c['power_basis']=='explicit_piecewise_constant_gross_MW','no mean/endpoint substitution for nonlinear integral')
        p=num(c['gross_power_mw']);base=num(c['minimum_adjustable_mw']);need(p>=base>=0,'below-minimum/transition/offline needs separate rule, not this integral')
        hours=D(str((hi-lo).total_seconds()))/D(3600);need(type(c['include_minimum_cost'])is bool and c['minimum_cost_decision_source'],'explicit minimum cost inclusion')
        rate=num(c['minimum_price_yuan_per_mwh']);remain=p-base;variable=D(0);capacity=D(0)
        need(c['segments'] and c['curve_source'],'explicit ordered declared curve')
        for seg in c['segments']:
            w=num(seg['width_mw']);b=num(seg['price_yuan_per_mwh']);need(w>0,'positive segment width');use=min(remain,w);variable+=use*b;remain-=use;capacity+=w
        need(remain==0,'power outside offered curve');basecost=base*rate if c['include_minimum_cost']else D(0)
        amount=(variable+basecost)*hours;gross+=amount;energy+=p*hours;cursor=hi
        out.append(dict(start=c['start'],end=c['end'],duration_h=hours,gross_MWh=p*hours,above_minimum_offer_CNY=variable*hours,minimum_offer_CNY=basecost*hours,gross_offer_CNY=amount))
    need(cursor==end,'full hour required')
    return dict(gross_offer_CNY=gross,gross_energy_MWh=energy,cells=out,scope='declared piecewise constant gross output; no startup/no-load fees added')

def compensation(session,fields):
    validate_fields(session,fields);req=['account','offer_cells'];miss=missing(fields,req)
    if miss:return session.result(status='missing',missing=miss)
    a=fields['account']['value'];need(any(r['resource']==a['resource']and r['node']==a['node']and r['technology']in ('coal','gas')for r in session.h['rows']),'thermal hour account mismatch')
    offer=offer_integral(fields['offer_cells']['value'],session.period)
    # Auxiliary expense cannot be inferred as a percentage: signed, explicitly sourced currency deduction.
    aux=evidence(fields,'auxiliary_offer_deduction_yuan');netoffer=P.EvidenceValue(None,'missing','explicit auxiliary-cost accounting','auxiliary offer deduction missing')if aux.kind=='missing'else P.EvidenceValue(float(offer['gross_offer_CNY'])-aux.value,'research',fields['auxiliary_offer_deduction_yuan']['source'])
    need('auxiliary_offer_deduction_yuan'not in fields or fields['auxiliary_offer_deduction_yuan'].get('classification')=='missing' or bool(fields['auxiliary_offer_deduction_yuan'].get('calculation_basis')),'auxiliary currency deduction calculation_basis required; no default rate')
    q=evidence(fields,'net_export_mwh');cost=evidence(fields,'approved_cost_yuan_per_mwh');ded=evidence(fields,'minimum_cost_deduction_yuan');other=evidence(fields,'other_variable_compensation_yuan')
    if q.kind!='missing':need(0<=q.value<=float(offer['gross_energy_MWh']),'net export must not exceed gross generation')
    needed=['net_export_mwh','approved_cost_yuan_per_mwh','minimum_cost_deduction_yuan','other_variable_compensation_yuan'];costmissing=missing(fields,needed)
    operating=P.EvidenceValue(None,'missing','operating cost upstream','missing '+','.join(costmissing))if costmissing else P.EvidenceValue(q.value*cost.value-ded.value-other.value,'research','explicit cost and deductions')
    for value in (cost,ded,other):
        if value.kind!='missing':need(value.value>=0,'nonnegative approved cost/deductions')
    app=fields.get('applicability',{});v=app.get('value',{});included=v.get('included')
    # Explicit DA and RT four-quarter eligibility/exclusion declarations, never infer from online status.
    check=fields.get('hour_exclusion_review',{}).get('value')
    if included is True:
        need(isinstance(check,dict)and set(check)=={'DA','RT'}and all(isinstance(check[k],list)and len(check[k])==4 and all(type(x)is bool for x in check[k])for k in check),'eight DA/RT quarter exclusion reviews required')
        need(not any(check['DA']+check['RT']),'included hour conflicts with exclusion declaration')
    applicability=O.CashApplicability(included,app.get('source','missing applicability'),v.get('provincial_rule_source','not supplied'),None if included is not None else 'applicability/exclusions unverified')
    result=O.operating_compensation_component(operating_cost_yuan=operating,offer_cost_yuan=netoffer,market_revenue_yuan=evidence(fields,'spot_market_revenue_yuan'),coefficient=evidence(fields,'coefficient'),applicability=applicability)
    return session.result(status=result['status'],offer_integral=offer,net_offer_cost_CNY=netoffer.value,operating_cost_CNY=operating.value,compensation=result,classification_claims={k:v['classification']for k,v in fields.items()},units=dict(power='gross MW',energy='MWh',price='CNY/MWh',cost='CNY/hour interval'),not_profit=True,execution_deviation_formula_conflict='4.3.2.3 unresolved; untouched',source_section='regional4.2.2.3-.4 p149-150; provincial applicability not certified')

def declared_curve_cells(thermal, *, ordered_segments, samples, minimum_price_yuan_per_mwh, include_minimum_cost, minimum_cost_decision_source, curve_source):
    """Adapter for existing energy_inputs.thermal[g]: bid_base_mw and segments[k]
    width_mw / price_yuan_per_mwh[t]. Explicit ordered segments, timed gross samples,
    minimum-cost rule supplied independently; never substitute online/stable for eligibility.
    """
    need(len(ordered_segments)==len(set(ordered_segments))and set(ordered_segments)==set(thermal['segments']),'complete explicit segment order')
    def at(v,t):
        if isinstance(v,dict):return v[t]if t in v else v[str(t)]
        if isinstance(v,(list,tuple)):return v[t]
        return v
    out=[]
    for s in samples:
        t=s['tick'];need(type(t)is int and t>=0,'source tick required')
        out.append(dict(start=s['start'],end=s['end'],power_basis=s['power_basis'],gross_power_mw=s['gross_power_mw'],minimum_adjustable_mw=thermal['bid_base_mw'],include_minimum_cost=include_minimum_cost,minimum_cost_decision_source=minimum_cost_decision_source,minimum_price_yuan_per_mwh=at(minimum_price_yuan_per_mwh,t),segments=[dict(width_mw=thermal['segments'][k]['width_mw'],price_yuan_per_mwh=at(thermal['segments'][k]['price_yuan_per_mwh'],t))for k in ordered_segments],curve_source=curve_source))
    return out
