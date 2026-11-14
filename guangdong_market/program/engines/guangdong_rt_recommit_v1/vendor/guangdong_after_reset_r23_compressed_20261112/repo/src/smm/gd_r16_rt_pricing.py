"""Independent R16 quarter price LP with DA SCED reference and 5-minute lift.

Pure construction/arithmetic grants no native, publication or dispatch authority.
The selected RT vector is a diagnostic witness, not a feasibility prerequisite.
"""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime, timezone
import math
from .gd_r15_da_rt_bridge import require, finite, close, digest
from . import gd_r16_hour_session as rt

SCHEMA = 'gd.research.r16.rt_price_input.v1'
SERVICES = ('positive', 'negative', 'primary', 'accident')
DT = 1 / 12
H = .25
POLICY = dict(schema='gd.research.r16.DA_reference_pricing_policy.v1', delta=.1,
    reference='hash_bound_R14_DAY_AHEAD_SCED_quarter_power',
    delta_source='explicit research parameter, not an official default',
    eligibility='frozen_R14_quarter_energy_price_research_assumption',
    nonpricing_reference='DA_context_interpretation_of_SCED_sentence',
    storage_band='signed_magnitude_fixed_source_mode_research_interpretation',
    selected_RT_witness='diagnostic_only_not_a_native_gate',
    official_pdf_sha256='978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf',
    rule_reference='Southern Region 2026V1.0 3.4.4.5 printed127/PDF130 and 3.4.4.11 printed130/PDF133',
    hard_network_slacks_zero=True, physical_lift='24x5-minute ramp_SOC_cycle; quarter_balance_network_reserves',
    positive_reserve_mw=400., accident_reserve_mw=400., negative_reserve_mw=0., primary_reserve_mw=0.,
    reserve_headroom='separate services, no joint positive+accident sum',
    BESS_wind_solar_reserve_credit_mw=0., AGC_eligibility_inferred=False,
    objective='original full energy offer integral; fixed startup/noload excluded',
    price_grid='8x15-minute; first only frozen, seven indicative',
    dispatch_executable=False, official_market=False, actual_telemetry=False, formal_bill=None)


def validate_price_policy(policy):
    require(policy == POLICY, 'exact frozen DA-reference delta=.1 research policy required')
    return True


def _power(cell, r, u):
    return finite(cell['batteries' if u['kind'] == 'battery' else 'thermal'][r]
                  ['power_mw' if u['kind'] == 'battery' else 'mean_mw'])


def normalize_price_inputs(inp, cells, plan, data, original, binding, policy=None):
    """Pure DTO assembly. Even authentic archived inputs cannot create capability."""
    policy = deepcopy(POLICY if policy is None else policy)
    validate_price_policy(policy)
    index = inp['window']['index']
    quarter_rows = {r: deepcopy(u['rows'][index:index+8]) for r, u in data['resources'].items()}
    da_reference = {r: deepcopy(plan['publication96']['power_mw'][r][index:index+8]) for r in inp['resources']}
    network_provenance = dict(reference_buses=deepcopy(original['energy_inputs']['reference_buses']),
        lines=deepcopy(original['energy_inputs']['lines']), sections=deepcopy(original['energy_inputs']['sections']),
        row_kinds={l: row['kind'] for l, row in data['network'].items()}, groups=deepcopy(data['groups']))
    d = dict(schema=SCHEMA, source_kind='accepted_fresh_R16_RT', source_binding=deepcopy(binding),
        rt_input=deepcopy(inp), selected_rt_cells=deepcopy(cells), policy=policy,
        R14_bindings=deepcopy(plan['source_bindings']), publication96_sha256=digest(plan['publication96']),
        R14_quarter_rows=quarter_rows, da_reference_mw=da_reference,
        network_provenance=network_provenance, storage_inventory=deepcopy(data['storage_inventory']),
        authored_at_utc=datetime.now(timezone.utc).isoformat(),
        synthetic_available_at=inp['window']['cutoff'], actual_operational_timeliness_claim=False)
    d['quarters'] = derive_quarters(d)
    d['content_sha256'] = digest(d)
    validate_price_inputs(d)
    return d


def derive_quarters(d):
    inp, cells = d['rt_input'], d['selected_rt_cells']
    require(len(cells) == 24, 'complete selected RT cells required')
    result = {}
    for r, u in inp['resources'].items():
        require(len(d['R14_quarter_rows'][r]) == len(d['da_reference_mw'][r]) == 8, 'eight source quarters required')
        result[r] = []
        for q in range(8):
            ids = list(range(3*q, 3*q+3)); physical = [u['rows'][j] for j in ids]
            market = d['R14_quarter_rows'][r][q]
            require(type(market.get('price_eligible')) is bool and bool(market.get('eligibility_reason')), 'R14 quarter eligibility/reason missing')
            lo, hi = (math.fsum(z[k] for z in physical)/3 for k in ('min_mw', 'max_mw'))
            close(lo, market['min_mw'], 'quarter mode-masked lower capacity mismatch')
            close(hi, market['max_mw'], 'quarter mode-masked upper capacity mismatch')
            mean = math.fsum(_power(cells[j], r, u) for j in ids)/3
            ref = finite(d['da_reference_mw'][r][q])
            close(ref, math.fsum(_power(inp['da_cells'][j], r, u) for j in ids)/3, 'DA reference not equal to bound physical SCED mean')
            active_mask = [True]*3
            mode = None
            if u['kind'] == 'battery':
                modes = {z['mode'] for z in physical}
                require(modes <= {'charge','idle','discharge'} and not {'charge','discharge'} <= modes, 'opposite battery modes in quarter')
                mode = next(iter(modes - {'idle'}), 'idle')
                require(mode == market['mode'], 'R14 quarter mode mismatch')
                require(market['price_eligible'] == (mode != 'idle'), 'R14 fixed-mode research eligibility mismatch')
                active_mask = [z['mode'] != 'idle' for z in physical]
                for j, z in zip(ids, physical):
                    for cell in (inp['da_cells'][j], cells[j]):
                        row = cell['batteries'][r]
                        require(row['mode'] == z['mode'], 'source RT battery mode changed')
                        permission = row['selected_hour_direction_permission']
                        require(permission in (-1,0,1) and (z['mode']=='idle' or permission == (-1 if z['mode']=='charge' else 1)), 'source mode conflicts with hour permission')
                    if z['mode'] == 'idle':
                        require(z['min_mw'] == z['max_mw'] == 0., 'idle physical cells must remain zero')
                        close(_power(cells[j],r,u), 0., 'idle RT power must be zero')
            else:
                phases = {z['phase'] for z in physical}
                require(len(phases)==1, 'mixed thermal quarter phases unsupported')
                phase = next(iter(phases))
                require(market['price_eligible'] == (phase=='stable'), 'R14 stable thermal research eligibility mismatch')
                if phase in ('startup','shutdown'):
                    require(len({z['event_id'] for z in physical})==1 and all(z['event_id'] and z['curve_source'] for z in physical), 'mixed/missing transition event')
                for j,z in zip(ids,physical):
                    require(cells[j]['thermal'][r]['phase']==z['phase'], 'source RT thermal phase changed')
            if market['price_eligible']:
                lower, upper = max(lo,ref-.1*abs(ref)), min(hi,ref+.1*abs(ref))
            else:
                lower = upper = ref
            require(lower <= upper + 1e-9, 'empty DA reference band; do not repair')
            result[r].append(dict(da_reference_mw=ref, selected_rt_mean_mw=mean,
                physical_min_mw=lo, physical_max_mw=hi, lower_mw=lower, upper_mw=upper,
                price_eligible=market['price_eligible'], eligibility_reason=market['eligibility_reason'],
                eligibility_source_sha256=d['R14_bindings']['ieee39_native_v4b/reports/sced/input.json'],
                mode=mode, active_mask=active_mask,
                reserve_caps_mw={s: math.fsum(z[s+'_cap_mw'] for z in physical)/3 for s in SERVICES}))
    return result


def validate_price_inputs(d):
    require(d['schema']==SCHEMA and d['source_kind']=='accepted_fresh_R16_RT', 'distinct R16 price DTO required')
    validate_price_policy(d['policy']); rt.validate_window_input(d['rt_input'])
    require(digest({k:v for k,v in d.items() if k!='content_sha256'})==d['content_sha256'], 'price DTO mutated')
    inp = d['rt_input']; R=set(inp['resources'])
    require(R == set(d['quarters']) == set(d['R14_quarter_rows']) == set(d['da_reference_mw']), 'resource inventory mismatch')
    require(d['quarters']==derive_quarters(d), 'derived quarters changed')
    require(d['synthetic_available_at']==inp['window']['cutoff'] and d['actual_operational_timeliness_claim'] is False, 'synthetic availability mismatch')
    require(d['source_binding']['input_sha256']==digest(inp), 'RT input source binding mismatch')
    require(d['source_binding']['solution_cells_sha256']==digest(d['selected_rt_cells']), 'selected RT cells binding mismatch')
    for j,cell in enumerate(d['selected_rt_cells']):
        da=inp['da_cells'][j]
        require((cell['start'],cell['end'])==(da['start'],da['end']), 'selected RT cell time mismatch')
        for family in ('thermal','batteries'):
            require(set(cell[family])==set(da[family]), 'selected RT inventory mismatch')
    prov=d['network_provenance']
    require(not prov['groups'] and not prov['sections'], 'unsupported groups/sections cannot be silently dropped')
    require(set(prov['lines'])==set(prov['row_kinds'])==set(inp['network']), 'network inventory mismatch')
    require(all(k=='line' for k in prov['row_kinds'].values()), 'unsupported network row kind')
    # Recompute original pinned topology, never infer topology from PTDFs.
    from .gd_october_pricing_network import dc_ptdf, normalize_topology_ptdf
    lines={l:dict(from_node=z['from_bus'],to_node=z['to_bus'],susceptance_mw_per_rad=z['susceptance_mw_per_rad']) for l,z in prov['lines'].items()}
    factors=dc_ptdf(tuple(inp['nodes']), lines, prov['reference_buses'][0])
    factors,_=normalize_topology_ptdf(tuple(inp['nodes']),lines,prov['reference_buses'][0],factors)
    for l,row in inp['network'].items():
        require(set(row['ptdf'])==set(inp['nodes']), 'PTDF node inventory mismatch')
        for n in inp['nodes']: close(row['ptdf'][n],factors[l][n],'pinned PTDF mismatch')
        for q in range(8):
            for k in ('lower_mw','upper_mw'):
                require(len(set(row[k][3*q:3*q+3]))==1, 'intraquarter changing network limits unsupported')
    require(bool(d['storage_inventory'].get('pumped')), 'inactive pumped-storage inventory must be retained')
    return True


def _curve(d,r,q):
    u=d['rt_input']['resources'][r]; row=d['quarters'][r][q]
    sign=-1 if row['mode']=='charge' else 1
    curve=u['original_offer'] if u['kind']!='battery' else u['original_curves']['charge' if sign==-1 else 'discharge']
    previous=Decimal('0'); costs=[]; records=[]
    for segment in curve:
        a,b=Decimal(str(segment['p_start'])),Decimal(str(segment['p_end']))
        require(a==previous and b>a, 'unaltered contiguous original quote widths required')
        costs.append(sign*finite(segment['price'])); previous=b
        records.append((float(b-a),finite(segment['price'])))
    require(costs==sorted(costs), 'source signed offer must be convex')
    return sign, records


def build_price_model(d):
    """Fresh independent continuous matrix, not an RT clone or averaged dual."""
    validate_price_inputs(d)
    import pyomo.environ as p
    inp=d['rt_input']; R=list(inp['resources']); B=[r for r in R if inp['resources'][r]['kind']=='battery']; G=[r for r in R if r not in B]; N=inp['nodes']; L=list(inp['network'])
    m=p.ConcreteModel('R16_independent_DA_reference_quarter_price'); m.zero=p.Var(bounds=(0.,0.));m.zero.fix(0.)
    m.power=p.Var(R,range(8));m.lift=p.Var(R,range(24));m.energy=p.Var(B,range(25))
    m.reserve=p.Var(G,range(8),SERVICES,domain=p.NonNegativeReals);m.rows=p.ConstraintList()
    for r,u in inp['resources'].items():
        initial=inp['projection']['state']['batteries' if r in B else 'thermal'][r];previous=initial['power_mw']
        if r in B:m.energy[r,0].fix(initial['energy_mwh'])
        for j,z in enumerate(u['rows']):
            v=m.lift[r,j];v.setlb(z['min_mw']);v.setub(z['max_mw'])
            if not d['quarters'][r][j//3]['price_eligible']:v.fix(_power(inp['da_cells'][j],r,u))
            if r in G and z['phase'] in ('startup','shutdown'):
                v.fix(z['mean_mw']);m.rows.add(previous+m.zero==z['begin_mw']);previous=z['end_mw']
            else:
                m.rows.add(v-previous<=z['ramp_up_mw_h']*DT);m.rows.add(previous-v<=z['ramp_down_mw_h']*DT);previous=v
            if r in B:
                coef=u['eta_charge'] if z['mode']=='charge' else 1/u['eta_discharge']
                m.energy[r,j+1].setlb(u['energy_min_mwh']);m.energy[r,j+1].setub(u['energy_max_mwh'])
                m.rows.add(m.energy[r,j+1]==m.energy[r,j]-coef*v*DT)
        if r in B:
            m.rows.add(m.energy[r,24]==u['terminal_energy_mwh'])
            for date in {c['start'][:10] for c in inp['da_cells']}:
                budget=u['cycle_budget'][date];used=initial['daily_used_internal_mwh'].get(date,0.)
                terms=[(-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge'])*m.lift[r,j]*DT for j in range(24) if inp['da_cells'][j]['start'][:10]==date]
                m.rows.add(m.zero+sum(terms)<=budget['limit_internal_mwh']-used)
        for q,z in enumerate(d['quarters'][r]):
            v=m.power[r,q];v.setlb(z['lower_mw']);v.setub(z['upper_mw']);m.rows.add(v==sum(m.lift[r,j] for j in range(3*q,3*q+3))/3)
            if r in G:
                for s in SERVICES:
                    rv=m.reserve[r,q,s];rv.setub(z['reserve_caps_mw'][s])
                    m.rows.add(rv<=v-z['physical_min_mw'] if s=='negative' else rv<=z['physical_max_mw']-v)
    mean=lambda fam,n,q:math.fsum(inp[fam][n][3*q:3*q+3])/3
    m.injection=p.Expression(N,range(8),rule=lambda _,n,q:m.zero+sum(m.power[r,q] for r in R if inp['resources'][r]['node']==n)+mean('fixed_injection_mw',n,q)-mean('load_mw',n,q))
    m.balance=p.Constraint(range(8),rule=lambda _,q:sum(m.injection[n,q] for n in N)==0)
    m.flow=p.Expression(L,range(8),rule=lambda _,l,q:sum(inp['network'][l]['ptdf'][n]*m.injection[n,q] for n in N))
    m.line_upper=p.Constraint(L,range(8),rule=lambda _,l,q:m.flow[l,q]<=inp['network'][l]['upper_mw'][3*q])
    m.line_lower=p.Constraint(L,range(8),rule=lambda _,l,q:-m.flow[l,q]<=-inp['network'][l]['lower_mw'][3*q])
    for q in range(8):
        for s in SERVICES:m.rows.add(m.zero+sum(m.reserve[g,q,s] for g in G)>=math.fsum(inp['reserve'][j][s+'_mw'] for j in range(3*q,3*q+3))/3)
    quotes={(r,q):_curve(d,r,q) for r in R for q in range(8)}
    m.segment=p.Var([(r,q,k) for (r,q),(_,curve) in quotes.items() for k in range(len(curve))],domain=p.NonNegativeReals)
    cost=0.
    for (r,q),(sign,curve) in quotes.items():
        m.rows.add(sign*m.power[r,q]==sum(m.segment[r,q,k] for k in range(len(curve))))
        for k,(width,price) in enumerate(curve):
            m.segment[r,q,k].setub(width);cost+=sign*price*m.segment[r,q,k]*H
    m.objective=p.Objective(expr=cost)
    return m,dict(schema=SCHEMA+'.matrix',price_input_sha256=digest(d),quarters=8,lift_cells=24,
        variables=sum(1 for _ in m.component_data_objects(p.Var)),constraints=sum(1 for _ in m.component_data_objects(p.Constraint,active=True)),
        fresh_independent_matrix=True,integer_variables=0,hard_network=True,dispatch_executable=False,official_market=False,
        new_native_calls=0,selected_RT_witness_is_required_feasible=False)


def check_selected_reference_witness(d, request, names):
    """Every bound/row checked. Failure is diagnostic, never a solve prohibition."""
    values={'zero':0.};inp=d['rt_input'];cells=d['selected_rt_cells']
    for r,u in inp['resources'].items():
        for j,cell in enumerate(cells):values[f'lift[{r},{j}]']=_power(cell,r,u)
        for q,z in enumerate(d['quarters'][r]):
            values[f'power[{r},{q}]']=z['selected_rt_mean_mw']
            sign,curve=_curve(d,r,q);remaining=sign*z['selected_rt_mean_mw']
            for k,(width,_) in enumerate(curve):
                used=min(width,max(0.,remaining));values[f'segment[{r},{q},{k}]']=used;remaining-=used
            require(abs(remaining)<1e-6,'selected RT power outside original full offer')
            if u['kind']!='battery':
                for s in SERVICES:values[f'reserve[{r},{q},{s}]']=math.fsum(cells[j]['reserve_awards_mw'][r][s] for j in range(3*q,3*q+3))/3
        if u['kind']=='battery':
            values[f'energy[{r},0]']=inp['projection']['state']['batteries'][r]['energy_mwh']
            for j,cell in enumerate(cells):values[f'energy[{r},{j+1}]']=cell['batteries'][r]['energy_end_mwh']
    require(set(values)==set(names['variables']), 'diagnostic witness must cover every serialized variable')
    vector=[values[n] for n in names['variables']];failures=[];residuals=[]
    def check(kind,name,value,lb,ub):
        violation=max(0.,0. if lb is None else lb-value,0. if ub is None else value-ub)
        row=dict(kind=kind,name=name,value=value,lower=lb,upper=ub,violation=violation);residuals.append(row)
        if violation>1e-6:failures.append(row)
    for name,v,z in zip(names['variables'],request['variables'],vector):check('variable',name,z,v['lb'],v['ub'])
    for name,row in zip(names['constraints'],request['rows']):check('row',name,math.fsum(c*vector[i] for i,c in row['terms']),row['lb'],row['ub'])
    obj=request['objective'];cost=obj['constant']+math.fsum(c*vector[i] for i,c in obj['terms'])
    return dict(diagnostic_only=True,creates_acceptance=False,native_gate=False,selected_RT_feasible=not failures,
        max_violation=max(r['violation'] for r in residuals),failures=failures,residuals=residuals,
        original_RT_objective_yuan=d['source_binding']['objective_yuan'],selected_RT_quarter_offer_cost_yuan=cost,
        note='Different DA-reference pricing dispatch may be feasible; no widening or state/qualification changes allowed.')


def independent_offer_cost(d, powers):
    """Direct original breakpoint integral, independent of segment primal values."""
    total=Decimal('0');rows=[]
    for r,u in d['rt_input']['resources'].items():
        for q in range(8):
            sign=-1 if d['quarters'][r][q]['mode']=='charge' else 1
            curve=u['original_offer'] if u['kind']!='battery' else u['original_curves']['charge' if sign==-1 else 'discharge']
            magnitude=Decimal(str(sign*finite(powers[r][q])))
            require(magnitude>=Decimal('-0.000001') and magnitude<=Decimal(str(curve[-1]['p_end']))+Decimal('0.000001'),'direct offer power out of range')
            value=sum((max(Decimal('0'),min(magnitude,Decimal(str(z['p_end'])))-Decimal(str(z['p_start'])))*Decimal(str(z['price'])) for z in curve),Decimal('0'))*Decimal(sign)*Decimal('0.25')
            total+=value;rows.append(dict(resource=r,quarter=q,power_mw=powers[r][q],direct_original_offer_cost_yuan=float(value)))
    return dict(total_yuan=float(total),rows=rows,method='Decimal original breakpoint integral, independent of segment primal')


def audit_independent_offer_cost(d,names,response):
    values=dict(zip(names['variables'],response['primal']))
    powers={r:[finite(values[f'power[{r},{q}]']) for q in range(8)] for r in d['rt_input']['resources']}
    solved=independent_offer_cost(d,powers)
    selected=independent_offer_cost(d,{r:[z['selected_rt_mean_mw'] for z in rows] for r,rows in d['quarters'].items()})
    tol=1e-5+1e-9*max(1.,abs(solved['total_yuan']))
    require(abs(solved['total_yuan']-response['objective'])<=tol,'independent original offer integral mismatch')
    return dict(passed=True,original_RT_objective_yuan=d['source_binding']['objective_yuan'],selected_RT_quarter_offer_cost_yuan=selected['total_yuan'],independent_price_objective_yuan=response['objective'],independent_price_direct_cost_yuan=solved['total_yuan'],tolerance_yuan=tol,solved=solved,selected=selected,overlapping_horizon_costs_not_summable=True)


def extract_lmps(d, names, response):
    y=dict(zip(names['constraints'],response['dual']));inp=d['rt_input'];raw=[];prices={n:[] for n in inp['nodes']}
    for q in range(8):
        balance=finite(y[f'balance[{q}]']);network={}
        for l in inp['network']:
            upper=finite(y[f'line_upper[{l},{q}]']);lower=finite(y[f'line_lower[{l},{q}]'])
            require(max(upper,lower)<=1e-8,'upper-form network raw dual must be nonpositive')
            network[l]=dict(raw_upper=upper,raw_lower=lower,tau_upper=-upper/H,tau_lower=-lower/H)
        for n in inp['nodes']:
            prices[n].append(balance/H-math.fsum((z['tau_upper']-z['tau_lower'])*inp['network'][l]['ptdf'][n] for l,z in network.items()))
        raw.append(dict(raw_balance=balance,lambda_yuan_mwh=balance/H,network=network))
    return dict(prices=prices,multipliers=raw,normalization_h=H,reserve_dual_adder=False,clipped=False,
        dual_uniqueness_claim=False,dispatch_executable=False,official_price=None,official_market=False)
