"""Provincial synthetic AGC ranking LP, not a Southern official auction replica."""
from copy import deepcopy
from datetime import datetime
import math
import pyomo.environ as p
from smm.gd_regulation_math import sorting_price,normalize_performance,validate_mileage_offer_price,mileage_payment
from smm import highs_solver as hs

def need(ok,msg):
    if not ok:raise ValueError(msg)
def dt(s):
    t=datetime.fromisoformat(s);need(t.utcoffset()is not None,'aware timestamp');return t

def build(case):
    need(case['mode']=='synthetic_research_only','official AGC market procurement not reconstructed')
    need(case['scope']=='Guangdong_provincial_only','no interregional market')
    need(case['positive_reserve_mw']==400 and case['accident_reserve_mw']==400,'retain separate original400 research requirements')
    need(dt(case['available_at'])<=dt(case['cutoff'])<=dt(case['start'])<dt(case['end']),'causal source timing')
    need(case['demand_mw']>=0 and case['full_control_area_k_max']>0,'explicit demand/performance basis')
    units=case['units'];cost={};cap={}
    for g,u in units.items():
        need(u['raw_min_mw']<=u['baseline_power_mw']<=u['raw_max_mw'],'SCUC witness bounds')
        need(u['basis']=='raw_before_AGC','do not deduct twice')
        need(u['kind']in('thermal','battery','wind','solar'),'kind explicit')
        eligible=u['kind']=='thermal'and u['stable_online']and u['qualification']=='synthetic_declared_eligible'
        if not eligible:
            need(u['agc_capacity_mw']==0,'unqualified/offline/nonthermal positive award forbidden');cap[g]=0.;cost[g]=0.;continue
        need(u['qualification_source']and u['performance_source']and u['bid_source'],'explicit synthetic input provenance')
        validate_mileage_offer_price(u['bid_yuan_per_mw'],current_floor_yuan_per_mw=case['current_floor_yuan_per_mw'])
        cost[g]=sorting_price(u['bid_yuan_per_mw'],normalize_performance(u['sorting_k'],control_area_k_max=case['full_control_area_k_max']))
        cap[g]=min(u['agc_capacity_mw'],u['tracking_up_mw'],u['tracking_down_mw'],u['raw_max_mw']-u['baseline_power_mw'],u['baseline_power_mw']-u['raw_min_mw'])
        need(cap[g]>=0,'negative AGC capability')
    m=p.ConcreteModel();m.G=p.Set(initialize=list(units));m.q=p.Var(m.G,domain=p.NonNegativeReals,bounds=lambda _,g:(0,cap[g]));m.accident=p.Var(m.G,domain=p.NonNegativeReals)
    m.demand=p.Constraint(expr=sum(m.q[g]for g in m.G)==case['demand_mw'])
    m.accident_cap=p.Constraint(m.G,rule=lambda _,g:m.accident[g]<=units[g]['ramp_10min_mw']if units[g]['kind']=='thermal'and units[g]['stable_online']and units[g]['accident_eligible']else m.accident[g]<=0)
    m.accident_head=p.Constraint(m.G,rule=lambda _,g:m.accident[g]+m.q[g]<=units[g]['raw_max_mw']-units[g]['baseline_power_mw'])
    m.accident_requirement=p.Constraint(expr=sum(m.accident[g]for g in m.G)>=400)
    m.positive_requirement=p.Constraint(expr=sum(units[g]['raw_max_mw']-units[g]['baseline_power_mw']-m.q[g]for g in m.G if units[g]['kind']=='thermal'and units[g]['stable_online']and units[g]['positive_eligible'])>=400)
    m.objective=p.Objective(expr=sum(cost[g]*m.q[g]for g in m.G));m.dual=p.Suffix(direction=p.Suffix.IMPORT)
    return m,dict(sorting_price=cost,eligible_capacity_mw=cap,objective_meaning='synthetic fractional capacity allocation using sorting-price weights; NOT official mileage procurement cost, settlement price, or revenue',scope=case['scope'],formal=False)

def template(case):
    m,meta=build(case);opts=hs.validate_options(dict(threads=1,mip_rel_gap=.001,mip_abs_gap=0.,time_limit=60.,output_flag=False));payload,vs,cs=hs.serialize_model(m,opts)
    return payload,dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]),meta

def adapt_awards(case,awards,*,new_agc_result_id,stage):
    """Return separate per-cell up/down reservations for SCED, never mutate SCUC."""
    need(stage in ('day_ahead_prearrangement','intraday_formal_research'),'explicit timing stage');need(new_agc_result_id,'new result identity')
    _,meta=build(case);need(set(awards)==set(case['units']),'complete award inventory')
    need(abs(sum(awards.values())-case['demand_mw'])<=1e-6,'award demand equality')
    adjusted={};reservations={}
    for g,q in awards.items():
        u=case['units'][g];need(math.isfinite(q)and 0<=q<=meta['eligible_capacity_mw'][g]+1e-6,'award outside qualified capability')
        adjusted[g]=dict(lower_mw=u['raw_min_mw']+q,upper_mw=u['raw_max_mw']-q,basis='after_AGC_do_not_deduct_twice')
        reservations[g]=dict(up_mw={j:q for j in case['physical_indices']},down_mw={j:q for j in case['physical_indices']},source=new_agc_result_id,stage=stage,available_at=case['available_at'],start=case['start'],end=case['end'],research_only=True)
    positive=sum(u['raw_max_mw']-u['baseline_power_mw']-awards[g]for g,u in case['units'].items()if u['kind']=='thermal'and u['stable_online']and u['positive_eligible'])
    accident=sum(min(u['ramp_10min_mw'],u['raw_max_mw']-u['baseline_power_mw']-awards[g])for g,u in case['units'].items()if u['kind']=='thermal'and u['stable_online']and u['accident_eligible'])
    need(positive>=400-1e-6 and accident>=400-1e-6,'AGC cannot consume required400 reserve')
    return dict(agc_awards=reservations,adjusted_envelope=adjusted,positive_remaining_mw=positive,accident_remaining_mw=accident,scuc_inputs_mutated=False,official=False,payment=None,execution_authority=False)

def synthetic_case():
    c=dict(mode='synthetic_research_only',scope='Guangdong_provincial_only',available_at='2026-11-10T19:00:00+08:00',cutoff='2026-11-10T20:00:00+08:00',start='2026-11-11T00:00:00+08:00',end='2026-11-11T01:00:00+08:00',physical_indices=list(range(12)),demand_mw=40.,positive_reserve_mw=400.,accident_reserve_mw=400.,full_control_area_k_max=2.,current_floor_yuan_per_mw=3.,units={})
    for g,bid,k,cap in [('C1',3.,1.5,30.),('C2',4.,1.,30.),('C3',5.,1.,0.)]:
        c['units'][g]=dict(kind='thermal',stable_online=True,qualification='synthetic_declared_eligible'if cap else'unknown',qualification_source='explicit synthetic test only',performance_source='synthetic K not provincial sample normalization',bid_source='synthetic offer',raw_min_mw=100.,raw_max_mw=500.,baseline_power_mw=300.,basis='raw_before_AGC',agc_capacity_mw=cap,tracking_up_mw=cap,tracking_down_mw=cap,sorting_k=k,bid_yuan_per_mw=bid,ramp_10min_mw=200.,positive_eligible=True,accident_eligible=True)
    return c

def service_gross_statement(*,measured_mileage_mw,uniform_price_yuan_mw,settlement_performance,designated_test,adjustments,source):
    need(source and type(designated_test)is bool,'meter/performance/test provenance required')
    gross=mileage_payment(measured_mileage_mw,uniform_price_yuan_mw,settlement_performance,designated_performance_test=designated_test)
    if adjustments is None:net=None
    else:
        need(all(z['statement_id']and math.isfinite(z['signed_yuan'])for z in adjustments),'external reviewed adjustments required');need(len({z['statement_id']for z in adjustments})==len(adjustments),'duplicate adjustment');net=gross+sum(z['signed_yuan']for z in adjustments)
    return dict(gross_formula_yuan=gross,reconciled_research_yuan=net,payable_yuan=None,official_bill=False,actual_transfer=False,source=source,rule_basis='inherited reviewed Southern regulation §74 arithmetic; actual quality/exclusion statement required',capacity_award_is_NOT_measured_mileage=True,reserve_dual_is_NOT_service_fee=True)
