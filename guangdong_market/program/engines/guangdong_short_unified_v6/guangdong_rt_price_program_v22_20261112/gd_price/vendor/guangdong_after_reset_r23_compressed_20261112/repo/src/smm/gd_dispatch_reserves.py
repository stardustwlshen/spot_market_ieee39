"""Guangdong 2026 safety-reserve constraint component, not a whole market.

DA operating up/down reserve, separate primary-frequency reserve, and RT 10-min
emergency reserve. No capacity auction or compensation is implemented here.
Ports must come from the correct physical dispatch model and information set.
"""
from __future__ import annotations
from numbers import Real
import math
import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn

STAGES = {"DA_SCUC", "DA_SCED", "RT_SCED"}


def _number(x, label, *, positive=False):
    if isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(float(x)):
        raise ValueError(f"{label}: finite number required")
    x=float(x)
    if x < 0 or (positive and x <= 0):raise ValueError(f"{label}: invalid sign")
    return x


def _series(mapping, periods, label, *, numerical=False, positive=False):
    if not isinstance(mapping,dict) or set(mapping)!=set(periods):
        raise ValueError(f"{label}: exact period coverage required")
    if numerical:return {t:_number(mapping[t],label,positive=positive) for t in periods}
    for t,expr in mapping.items():
        repn=generate_standard_repn(expr,compute_values=True)
        if not repn.is_linear() or not math.isfinite(float(repn.constant or 0)) or any(not math.isfinite(float(c)) for c in repn.linear_coefs):
            raise ValueError(f"{label}[{t}]: finite linear expression required")
    return dict(mapping)


def attach_reserves(model, *, stage, periods, thermal, pumped, requirements,
                    period_hours, source_notes, primary_granularity, pumped_system_capacity=None,
                    penalties_yuan_per_mw_h=None,
                    block_name="gd_reserve"):
    """Attach a standalone block. Does not alter the active objective.

    Required port keys are exact, avoiding silently ignored legacy settings.
    All capacity expressions must already encode physical availability/status.
    """
    if stage not in STAGES:raise ValueError("unsupported Guangdong reserve stage")
    if not isinstance(periods,(tuple,list)) or not periods or any(type(t) is not int for t in periods) or len(set(periods))!=len(periods) or list(periods)!=sorted(periods):
        raise ValueError("periods must be an ordered unique integer list")
    expected_granularity={"DA_SCUC":"optimization_unit","DA_SCED":"generating_unit","RT_SCED":"plant"}[stage]
    if primary_granularity != expected_granularity:
        raise ValueError("primary reserve granularity must match the documented stage; do not aggregate min across units")
    periods=list(periods);hours=_series(period_hours,periods,"period hours",numerical=True,positive=True)
    if not isinstance(block_name,str) or not block_name or hasattr(model,block_name):raise ValueError("reserve block name already exists or invalid")
    if not isinstance(thermal,dict) or not isinstance(pumped,dict) or set(thermal)&set(pumped):raise ValueError("resource ports must be distinct mappings")
    for uid in (*thermal,*pumped):
        if not isinstance(uid,str) or not uid:raise ValueError("resource ID required")
    required={"up_mw","primary_mw","deduction_mw"}
    if stage.startswith("DA"):required|={"down_mw","system_demand_minus_tie_mw"}
    if not isinstance(requirements,dict) or set(requirements)!=required:raise ValueError("explicit stage reserve requirements required")
    req={k:_series(v,periods,k,numerical=k!="system_demand_minus_tie_mw") for k,v in requirements.items()}
    notes=required|{"thermal_capacity_semantics","pumped_capacity_semantics","period_hours"}
    if stage=="RT_SCED":notes|={"thermal_10min_ramp","primary_penalty","emergency_penalty"}
    if not isinstance(source_notes,dict) or not notes<=set(source_notes) or any(not isinstance(source_notes[k],str) or not source_notes[k].strip() for k in notes):
        raise ValueError("all required physical/policy sources must be explicit")
    penalties={}
    if stage=="RT_SCED":
        if not isinstance(penalties_yuan_per_mw_h,dict) or set(penalties_yuan_per_mw_h)!={"primary","emergency"}:raise ValueError("RT reserve penalties must be explicit")
        penalties={k:_number(v,k,positive=True) for k,v in penalties_yuan_per_mw_h.items()}
    elif penalties_yuan_per_mw_h is not None:raise ValueError("DA hard reserve component has no implicit soft penalties")
    tp={};pp={};system_caps={}
    for uid,port in thermal.items():
        keys={"power","online_max","online_min"}|({"ramp_10min_mw"} if stage=="RT_SCED" else set())
        if not isinstance(port,dict) or set(port)!=keys:raise ValueError(f"{uid}: exact thermal port keys required")
        tp[uid]={k:_series(v,periods,f"{uid}.{k}",numerical=k=="ramp_10min_mw") for k,v in port.items()}
    for uid,port in pumped.items():
        keys={"net_power","primary_capacity"}
        keys|=({"generation_power","pumping_power","pumping_mode","emergency_generation_capacity","generation_cap_upper_mw","pumping_cap_upper_mw","minimum_pumping_mw"}
               if stage=="RT_SCED" else {"plant_id"})
        if not isinstance(port,dict) or set(port)!=keys:raise ValueError(f"{uid}: exact pumped port keys required")
        pp[uid]={k:_series(v,periods,f"{uid}.{k}",numerical=k in {"generation_cap_upper_mw","pumping_cap_upper_mw","minimum_pumping_mw"},positive=k=="minimum_pumping_mw") for k,v in port.items() if k!="plant_id"}
        if stage.startswith("DA"):
            if not isinstance(port["plant_id"],str) or not port["plant_id"].strip():raise ValueError("pumped plant_id required")
            pp[uid]["plant_id"]=port["plant_id"]
        if stage=="RT_SCED":
            for t,z in pp[uid]["pumping_mode"].items():
                if isinstance(z,Real):
                    if isinstance(z,bool) or float(z) not in (0.,1.):raise ValueError("pumping mode must be binary")
                elif not getattr(z,"is_binary",lambda:False)():raise ValueError("pumping mode must be a binary variable or 0/1 constant")
    if stage.startswith("DA"):
        if not isinstance(pumped_system_capacity,dict) or set(pumped_system_capacity)!={p['plant_id'] for p in pp.values()}:
            raise ValueError("DA system pumped capacity must cover each plant exactly once, separately from primary units")
        for plant,record in pumped_system_capacity.items():
            if not isinstance(record,dict) or set(record)!={"max_generation_mw","max_pumping_mw"}:raise ValueError("explicit per-plant available generation/pumping caps required")
            system_caps[plant]={k:_series(v,periods,plant+'.'+k,numerical=True) for k,v in record.items()}
    elif pumped_system_capacity is not None:
        raise ValueError("DA pumped system capacity must not be supplied as RT online capacity")
    # All validation precedes modification of the caller's model.
    b=pyo.Block(concrete=True)
    b.constant_zero=pyo.Expression(expr=0.)
    b.T=pyo.Set(initialize=periods,ordered=True);b.G=pyo.Set(initialize=list(tp));b.PS=pyo.Set(initialize=list(pp))
    b.primary_thermal=pyo.Var(b.G,b.T,domain=pyo.NonNegativeReals)
    b.primary_pumped=pyo.Var(b.PS,b.T,domain=pyo.NonNegativeReals)
    b.c_thermal_power_nonnegative=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:(0.,b.constant_zero+tp[g]['power'][t],None))
    b.c_thermal_min_nonnegative=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:(0.,b.constant_zero+tp[g]['online_min'][t],None))
    b.c_thermal_primary_head=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:b.primary_thermal[g,t]+tp[g]['power'][t]<=tp[g]['online_max'][t])
    b.c_thermal_primary_cap=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:b.primary_thermal[g,t]<=.06*tp[g]['online_max'][t])
    b.c_pumped_primary_head=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:b.primary_pumped[p,t]+pp[p]['net_power'][t]<=pp[p]['primary_capacity'][t])
    b.c_pumped_primary_cap=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:b.primary_pumped[p,t]<=.10*pp[p]['primary_capacity'][t])
    b.primary_short=pyo.Var(b.T,domain=pyo.NonNegativeReals)
    b.emergency_short=pyo.Var(b.T,domain=pyo.NonNegativeReals)
    if stage.startswith('DA'):
        for t in periods:b.primary_short[t].fix(0.);b.emergency_short[t].fix(0.)
    b.c_primary=pyo.Constraint(b.T,rule=lambda _,t:sum(b.primary_thermal[g,t] for g in tp)+sum(b.primary_pumped[p,t] for p in pp)+b.primary_short[t]>=req['primary_mw'][t])
    if stage.startswith('DA'):
        b.c_up=pyo.Constraint(b.T,rule=lambda _,t:(0.,b.constant_zero+sum(tp[g]['online_max'][t] for g in tp)+sum(system_caps[p]['max_generation_mw'][t] for p in system_caps)-req['deduction_mw'][t]-req['system_demand_minus_tie_mw'][t]-req['up_mw'][t],None))
        b.c_down=pyo.Constraint(b.T,rule=lambda _,t:(None,b.constant_zero+sum(tp[g]['online_min'][t] for g in tp)-sum(system_caps[p]['max_pumping_mw'][t] for p in system_caps)-req['system_demand_minus_tie_mw'][t]+req['down_mw'][t],0.))
        b.penalty_cost=pyo.Expression(expr=0.)
    else:
        b.emergency_thermal=pyo.Var(b.G,b.T,domain=pyo.NonNegativeReals)
        b.emergency_pumped=pyo.Var(b.PS,b.T,domain=pyo.NonNegativeReals)
        b.c_emergency_head=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:b.emergency_thermal[g,t]+tp[g]['power'][t]<=tp[g]['online_max'][t])
        b.c_emergency_ramp=pyo.Constraint(b.G,b.T,rule=lambda _,g,t:b.emergency_thermal[g,t]<=tp[g]['ramp_10min_mw'][t])
        b.c_gen_nonnegative=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(0.,b.constant_zero+pp[p]['generation_power'][t],None))
        b.c_pump_nonnegative=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(0.,b.constant_zero+pp[p]['pumping_power'][t],None))
        b.c_gen_capacity_nonnegative=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(0.,b.constant_zero+pp[p]['emergency_generation_capacity'][t],None))
        b.c_pump_excludes_gen_capacity=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(None,b.constant_zero+pp[p]['emergency_generation_capacity'][t]-pp[p]['generation_cap_upper_mw'][t]*(1-pp[p]['pumping_mode'][t]),0.))
        b.c_pumped_gen_head=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(None,b.constant_zero+pp[p]['generation_power'][t]-pp[p]['emergency_generation_capacity'][t],0.))
        b.c_pump_mode=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(None,b.constant_zero+pp[p]['pumping_power'][t]-pp[p]['pumping_cap_upper_mw'][t]*pp[p]['pumping_mode'][t],0.))
        b.c_pump_positive_when_active=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(0.,b.constant_zero+pp[p]['pumping_power'][t]-pp[p]['minimum_pumping_mw'][t]*pp[p]['pumping_mode'][t],None))
        b.c_net_power=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:(0.,b.constant_zero+pp[p]['net_power'][t]-pp[p]['generation_power'][t]+pp[p]['pumping_power'][t],0.))
        b.c_pumped_emergency=pyo.Constraint(b.PS,b.T,rule=lambda _,p,t:b.emergency_pumped[p,t]==pp[p]['emergency_generation_capacity'][t]-pp[p]['generation_power'][t]+pp[p]['pumping_power'][t])
        b.c_emergency=pyo.Constraint(b.T,rule=lambda _,t:sum(b.emergency_thermal[g,t] for g in tp)+sum(b.emergency_pumped[p,t] for p in pp)+b.emergency_short[t]>=req['up_mw'][t]+req['deduction_mw'][t])
        b.penalty_cost=pyo.Expression(expr=sum(hours[t]*(penalties['primary']*b.primary_short[t]+penalties['emergency']*b.emergency_short[t]) for t in periods))
    b._source_notes=dict(source_notes)
    b._policy_scope={'stage':stage,'rule_reference':'GDE2026 DA(2)-(4);RT(2),(8)',
        'conventional_hydro_included':False,'battery_added_to_rule_reserve_sum':False,
        'capacity_auction':False,'compensation_ledger':False,'penalty_unit':'yuan/(MW*h)',
        'requirements':req,'period_hours':hours,'primary_granularity':primary_granularity,'pumped_system_capacity':system_caps,
        'interpretation_notes':['Pumped set follows adjacent PP definitions, not apparent NP typo.',
            'Primary variables represent reserved capacity feasibility, not maximal ability or payable service.',
            'Ports must supply stage-specific online/available capacity; no inferred response certification.',
            'RT supports same-plant same-mode operation only; mixed generating/pumping plant states are not supported.']}
    model.add_component(block_name,b)
    if stage.startswith('DA'):
        # Structural identity guard for the later rule-based LMP omission. A
        # normal mutable demand update keeps its symbolic expression unchanged;
        # replacing a row with an unrelated bound must not inherit permission.
        b._lmp_omission_fingerprints={row.name:str(row.expr) for component in (b.c_up,b.c_down) for row in component.values()}
    return b
