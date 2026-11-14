"""Independent continuous eight-quarter LP, not a clone of any SCED result.

Only stable thermal, fixed nonprice renewable and fixed physical TIE are
implemented. Source-bound inventory rejects unsupported resources before build.
All choices here are research interpretations, never official operational rules.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from weakref import WeakKeyDictionary
import pyomo.environ as pyo

from .gd_october_pricing_inputs import assert_pricing_inputs_current, canonical, num, require
from .gd_october_pricing_network import dc_ptdf, normalize_topology_ptdf
from .highs_solver import serialize_model, validate_options

_OPTIONS=validate_options(dict(time_limit=60.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.))
_MODELS=WeakKeyDictionary()
_SOURCE_FILES=('gd_october_pricing_inputs.py','gd_october_pricing_network.py','gd_october_pricing_model.py',
    'gd_october_pricing_solve.py','gd_october_pricing_storage.py','gd_october_pricing_rules.py','gd_october_rt_contract.py',
    'gd_price_research.py','highs_solver.py','highs_worker.py','highs_gap.py','gd_solve.py',
    'gd_october_pricing_native_bridge.py','gd_october_native_solve.py','gd_october_native_inputs.py','gd_october_input_bridge.py',
    'gd_october_roll.py','gd_october_sced.py','gd_october_storage.py','gd_october_storage_capacity_v3.py',
    'gd_october_ieee39_capacity_v2.py','gd_october_fixed_renewable_capacity_v4.py','gd_october_run_identity.py','gd_october_job_codec.py','gd_energy_core.py',
    'gd_october_native_snapshot.py','gd_october_pricing_thermal_v4.py','gd_october_thermal.py','gd_october_research_thermal.py','gd_october_ieee39_scenario.py',
    'gd_october_forecast_inputs.py','gd_october_research_forecast.py','gd_horizon.py')


def code_hashes():
    return {name:sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in _SOURCE_FILES}


@dataclass(frozen=True)
class PricingProbe:
    """Explicit diagnostic only; rebuild a new matrix and retain original sources."""
    node: str
    quarter: int
    increment_mw: float
    kind: str  # energy_only keeps reserve demand fixed; positive_reserve_linked_demand changes it.


@dataclass(frozen=True, eq=False)
class OctoberPricingLP:
    inputs: object
    model: object
    audit_bytes: bytes
    model_sha256: str

    @property
    def audit(self):
        import json
        return json.loads(self.audit_bytes)


def _signature(model):
    payload,variables,rows=serialize_model(model,_OPTIONS)
    # Include inactive components/parameters, domains, names, fixing and set
    # inventories as well as the active numerical matrix.
    detail=dict(payload=payload,variables=[(v.name,str(v.domain),v.fixed) for v in variables],
        constraints=[(c.name,c.active) for c in model.component_data_objects(pyo.Constraint,active=None)],
        objectives=[(o.name,o.active) for o in model.component_data_objects(pyo.Objective,active=None)],
        parameters=[(p.name,str(i),float(pyo.value(p[i]))) for p in model.component_objects(pyo.Param,active=None) for i in p],
        sets=[(s.name,[str(x) for x in s]) for s in model.component_objects(pyo.Set,active=None)],
        root_active=model.active)
    return sha256(canonical(detail)).hexdigest()


def build_october_pricing_lp(inputs, *, diagnostic_probe=None):
    d=assert_pricing_inputs_current(inputs); spec=d['spec']; params=d['parameters']
    nodes=tuple(spec['nodes']); thermal=spec['thermal']; T=range(8)
    thermal_v4=spec['schema_version']=='gd.october.pricing_numeric.v4'
    demand={n:list(d['loads'][n]) for n in nodes}
    reserve_demand=list(spec['reserves']['positive_demand_baseline_mw'])
    probe=None
    if diagnostic_probe is not None:
        p=diagnostic_probe
        require(type(p) is PricingProbe and p.node in nodes and type(p.quarter) is int and p.quarter in T,'invalid diagnostic probe identity')
        require(p.kind in {'energy_only','positive_reserve_linked_demand'},'unknown diagnostic derivative convention')
        num(p.increment_mw,'probe increment'); require(demand[p.node][p.quarter]+p.increment_mw>=0,'probe makes load negative')
        demand[p.node][p.quarter]+=p.increment_mw
        if p.kind=='positive_reserve_linked_demand': reserve_demand[p.quarter]+=p.increment_mw
        probe=dict(node=p.node,quarter=p.quarter,increment_mw=p.increment_mw,kind=p.kind)
    line_ptdf=dc_ptdf(nodes,{l:{k:v[k] for k in ('from_node','to_node','susceptance_mw_per_rad')} for l,v in spec['lines'].items()},spec['reference_node'])
    ptdf_numerical_audit=None
    if thermal_v4:
        line_ptdf,ptdf_numerical_audit=normalize_topology_ptdf(nodes,spec['lines'],spec['reference_node'],line_ptdf)
        # Bound the entire numerical correction on every price-feasible nodal
        # injection, including diagnostic loads and all storage signed bands.
        injection_bounds={n:[abs(d['fixed_injection'][n][t]-demand[n][t])+sum(g['rows'][t]['maximum_mw']for g in thermal.values()if g['node']==n)for t in T]for n in nodes}
        if 'storage'in d:
            for family in ('batteries','pumped_units'):
                for u,row in d['storage']['raw'][family].items():
                    for t in T:injection_bounds[row['node']][t]+=max(abs(x)for x in d['storage']['signed_bounds'][u][t])
        errors={l:[sum(abs(x['raw']-x['normalized'])*injection_bounds[x['node']][t]for x in ptdf_numerical_audit['changes']if x['line']==l)for t in T]for l in spec['lines']}
        worst=max([v for vs in errors.values()for v in vs]or [0.])
        require(worst<=1e-9,'topology normalization exceeds declared numerical flow tolerance')
        ptdf_numerical_audit.update(line_flow_error_bound_mw=errors,max_line_flow_error_bound_mw=worst,acceptance_bound_mw=1e-9)
    section_ptdf={s:{n:sum(w*line_ptdf[l][n] for l,w in row['line_weights'].items()) for n in nodes} for s,row in spec['sections'].items()}
    m=pyo.ConcreteModel(name='independent_october_lp_research')
    m.T=pyo.Set(initialize=T,ordered=True); m.N=pyo.Set(initialize=nodes,ordered=True)
    m.G=pyo.Set(initialize=tuple(thermal),ordered=True); m.L=pyo.Set(initialize=tuple(spec['lines']),ordered=True)
    m.S=pyo.Set(initialize=tuple(spec['sections']),ordered=True)
    m.duration_h=pyo.Param(m.T,initialize=.25,mutable=True)
    m.demand=pyo.Param(m.N,m.T,initialize=lambda _,n,t:demand[n][t],mutable=True)
    m.reserve_demand=pyo.Param(m.T,initialize=lambda _,t:reserve_demand[t],mutable=True)
    m.fixed_injection=pyo.Param(m.N,m.T,initialize=lambda _,n,t:d['fixed_injection'][n][t],mutable=True)
    m.zero=pyo.Var(bounds=(0,0),initialize=0); m.zero.fix(0)
    rows={(g,t):thermal[g]['rows'][t] for g in thermal for t in T}
    physical_lo={(g,t):r['minimum_mw']+r['agc_down_mw'] for (g,t),r in rows.items()}
    physical_hi={(g,t):r['maximum_mw']-r['agc_up_mw'] for (g,t),r in rows.items()}
    bounds={}
    for (g,t),r in rows.items():
        ref=d['reference_mw'][g][t]
        bounds[g,t]=(max(physical_lo[g,t],(1-params['delta'])*ref),min(physical_hi[g,t],(1+params['delta'])*ref)) if r['pricing_eligible'] else (ref,ref)
        require(bounds[g,t][0]<=bounds[g,t][1],'empty literal pricing power band')
    m.power=pyo.Var(m.G,m.T,bounds=lambda _,g,t:bounds[g,t])
    m.K=pyo.Set(dimen=3,initialize=[(g,t,k) for (g,t),r in rows.items() if not thermal_v4 or r['phase']=='stable' for k in range(len(r['bids']))],ordered=True)
    m.segment=pyo.Var(m.K,domain=pyo.NonNegativeReals,bounds=lambda _,g,t,k:(0,rows[g,t]['bids'][k]['width_mw']))
    def segment_row(_,g,t):
        r=rows[g,t]
        if thermal_v4 and r['phase']!='stable':return m.power[g,t]==r['fixed_power_mw']
        return m.power[g,t]==(r['source_bid_base_mw'] if thermal_v4 else 0.)+sum(m.segment[g,t,k] for k in range(len(r['bids'])))
    m.c_segments=pyo.Constraint(m.G,m.T,rule=segment_row)
    m.c_ramp_up=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]-(thermal[g]['initial_power_mw'] if t==0 else m.power[g,t-1])<=.25*rows[g,t]['ramp_up_mw_h'])
    m.c_ramp_down=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:(thermal[g]['initial_power_mw'] if t==0 else m.power[g,t-1])-m.power[g,t]<=.25*rows[g,t]['ramp_down_mw_h'])
    storage_ports=None
    if 'storage' in d:
        from .gd_october_pricing_storage import attach_pricing_storage
        storage_ports=attach_pricing_storage(m,d['storage'])
    def storage(key,t):return storage_ports[key][t] if storage_ports is not None else 0.
    m.injection=pyo.Expression(m.N,m.T,rule=lambda _,n,t:sum(m.power[g,t] for g in thermal if thermal[g]['node']==n)+m.fixed_injection[n,t]-m.demand[n,t]+(storage_ports['injection_mw'][n,t] if storage_ports is not None else 0.))
    m.c_balance=pyo.Constraint(m.T,rule=lambda _,t:sum(m.injection[n,t] for n in nodes)==0)
    m.flow=pyo.Expression(m.L,m.T,rule=lambda _,l,t:sum(line_ptdf[l][n]*m.injection[n,t] for n in nodes))
    m.section_flow=pyo.Expression(m.S,m.T,rule=lambda _,s,t:sum(section_ptdf[s][n]*m.injection[n,t] for n in nodes))
    m.line_plus=pyo.Var(m.L,m.T,domain=pyo.NonNegativeReals,bounds=lambda _,l,t:(0,spec['lines'][l]['slack_limit_mw'][t]))
    m.line_minus=pyo.Var(m.L,m.T,domain=pyo.NonNegativeReals,bounds=lambda _,l,t:(0,spec['lines'][l]['slack_limit_mw'][t]))
    m.section_plus=pyo.Var(m.S,m.T,domain=pyo.NonNegativeReals,bounds=lambda _,s,t:(0,spec['sections'][s]['slack_limit_mw'][t]))
    m.section_minus=pyo.Var(m.S,m.T,domain=pyo.NonNegativeReals,bounds=lambda _,s,t:(0,spec['sections'][s]['slack_limit_mw'][t]))
    m.c_line_upper=pyo.Constraint(m.L,m.T,rule=lambda _,l,t:m.flow[l,t]-m.line_plus[l,t]+m.line_minus[l,t]<=spec['lines'][l]['upper_mw'][t])
    m.c_line_lower=pyo.Constraint(m.L,m.T,rule=lambda _,l,t:-m.flow[l,t]+m.line_plus[l,t]-m.line_minus[l,t]<=-spec['lines'][l]['lower_mw'][t])
    m.c_section_upper=pyo.Constraint(m.S,m.T,rule=lambda _,s,t:m.section_flow[s,t]-m.section_plus[s,t]+m.section_minus[s,t]<=spec['sections'][s]['upper_mw'][t])
    m.c_section_lower=pyo.Constraint(m.S,m.T,rule=lambda _,s,t:-m.section_flow[s,t]+m.section_plus[s,t]-m.section_minus[s,t]<=-spec['sections'][s]['lower_mw'][t])
    reserve=spec['reserves']; tie=[sum(x[t] for x in d['exogenous']['boundary_tie_mw'].values()) for t in T]
    positive=[sum(physical_hi[g,t] for g in thermal if rows[g,t]['positive_eligible'])+tie[t]+sum(v['positive_capacity_mw'][t] for v in spec['renewable_reserve'].values()) for t in T]
    negative=[sum(physical_lo[g,t] for g in thermal if rows[g,t]['negative_eligible'])+tie[t]+sum(v['negative_minimum_mw'][t] for v in spec['renewable_reserve'].values()) for t in T]
    m.primary=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals)
    m.accident=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals)
    for (g,t),r in rows.items():
        if not r['primary_eligible'] or reserve['families']['primary']['status']=='explicitly_inactive':m.primary[g,t].fix(0)
        if not r['accident_eligible'] or reserve['families']['accident']['status']=='explicitly_inactive':m.accident[g,t].fix(0)
    m.c_primary_headroom=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.primary[g,t]+m.power[g,t]<=physical_hi[g,t])
    m.c_primary_fraction=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.primary[g,t]<=rows[g,t]['primary_fraction']*rows[g,t]['maximum_mw'])
    m.c_accident_headroom=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.accident[g,t]+m.power[g,t]<=physical_hi[g,t])
    m.c_accident_ramp=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.accident[g,t]<=rows[g,t]['accident_ramp_10min_mw'])
    m.accident_shortfall=pyo.Var(m.T,domain=pyo.NonNegativeReals,bounds=lambda _,t:(0,reserve['accident_slack_limit_mw'][t]))
    # Keep constant reserve contradictions as solver rows, never drop them.
    m.c_positive_reserve=pyo.Constraint(m.T,rule=lambda _,t:m.zero+positive[t]+storage('positive_capacity_mw',t)-reserve['positive_network_deduction_mw'][t]>=m.reserve_demand[t]+reserve['positive_mw'][t])
    m.c_negative_reserve=pyo.Constraint(m.T,rule=lambda _,t:m.zero+negative[t]+storage('negative_minimum_mw',t)<=reserve['negative_demand_mw'][t]-reserve['negative_mw'][t])
    m.c_primary_reserve=pyo.Constraint(m.T,rule=lambda _,t:sum(m.primary[g,t] for g in thermal)+storage('primary_mw',t)>=reserve['primary_mw'][t])
    m.c_accident_reserve=pyo.Constraint(m.T,rule=lambda _,t:sum(m.accident[g,t] for g in thermal)+storage('accident_mw',t)+m.accident_shortfall[t]>=reserve['accident_mw'][t]+reserve['accident_deduction_mw'][t])
    m.energy_cost=pyo.Expression(expr=sum(m.duration_h[t]*rows[g,t]['bids'][k]['yuan_per_mwh']*m.segment[g,t,k] for g,t,k in m.K)+
        (sum(m.duration_h[t]*(r['source_base_cost_yuan_per_h'] if r['phase']=='stable' else r['transition_cost_yuan_per_h']) for (g,t),r in rows.items()) if thermal_v4 else 0.))
    m.network_cost=pyo.Expression(expr=sum(m.duration_h[t]*params['network_penalty_yuan_per_mwh']*(sum(m.line_plus[l,t]+m.line_minus[l,t] for l in m.L)+sum(m.section_plus[s,t]+m.section_minus[s,t] for s in m.S)) for t in T))
    m.accident_cost=pyo.Expression(expr=sum(m.duration_h[t]*params['accident_reserve_penalty_yuan_per_mwh']*m.accident_shortfall[t] for t in T))
    m.objective=pyo.Objective(expr=m.energy_cost+m.network_cost+m.accident_cost+(storage_ports['energy_cost'] if storage_ports is not None else 0.),sense=pyo.minimize)
    m.dual=pyo.Suffix(direction=pyo.Suffix.IMPORT)
    audit=dict(identity=d['identity'],input_sha256=inputs.input_sha256,kind='independent_october_lp_research',
        horizon_count=8,duration_h=.25,reference_stage=spec['choices']['reference_stage'],state_stage=d['state_stage'],
        line_ptdf=line_ptdf,section_ptdf=section_ptdf,ptdf_numerical_audit=ptdf_numerical_audit,reference_node=spec['reference_node'],diagnostic_probe=probe,
        row_names=[c.name for c in m.component_data_objects(pyo.Constraint,active=True)],parameters=params,
        penalty_applicability={'network':'active','accident':'explicit_inherited_row',
            'renewable':'inactive_fixed_nonprice_output','market_hydro':'inactive_explicit_absence',
            'load':'inactive_hard_zero_slack','tie_priority':'inactive_fixed_physical_boundary'},
        reserve_positive_capacity_mw=positive,reserve_negative_minimum_mw=negative,
        reserve_positive_demand_mw=reserve_demand,source_hashes=d['source_hashes'],source_code_hashes=code_hashes(),
        official_price=None,whole_day=False,settlement_cash_created=False,published=False,
        unsupported_families=['battery','pumped','hydro','groups','transitions','load_side_reserve'])
    if storage_ports is not None:
        audit['storage']=storage_ports['audit']
        audit['reserve_constant_part_excludes_storage_baseline']=True
        audit['unsupported_families']=['hydro','groups','transitions','load_side_reserve','storage_incremental_services','storage_mode_transitions']
    if 'native_reference' in d:audit['native_reference']=d['native_reference']
    if thermal_v4:
        audit['unsupported_families'].remove('transitions')
        audit['unsupported_families']+=['thermal_shutdown','mixed_phase_quarters','initial_active_curve']
        audit.update(pricing_dispatch_executable=False,pricing_ramp_basis='quarter_mean_research_not_exact_5min_projection',thermal_objective_constant_yuan=sum(.25*(r['source_base_cost_yuan_per_h'] if r['phase']=='stable' else r['transition_cost_yuan_per_h'])for r in rows.values()))
    result=OctoberPricingLP(inputs,m,canonical(audit),_signature(m))
    _MODELS[result]=(result.model_sha256,result.audit_bytes,code_hashes())
    return result


def assert_october_pricing_structure(pricing):
    require(type(pricing) is OctoberPricingLP and pricing in _MODELS,'issued independent pricing model required')
    assert_pricing_inputs_current(pricing.inputs)
    signature,audit,sources=_MODELS[pricing]
    require(pricing.audit_bytes==audit,'pricing audit metadata changed')
    require(pricing.model_sha256==signature==_signature(pricing.model),'pricing matrix/parameters/state changed')
    require(sources==code_hashes(),'pricing implementation source changed')
    require(all(v.is_continuous() for v in pricing.model.component_data_objects(pyo.Var)),'continuous LP required')
    return pricing.audit
