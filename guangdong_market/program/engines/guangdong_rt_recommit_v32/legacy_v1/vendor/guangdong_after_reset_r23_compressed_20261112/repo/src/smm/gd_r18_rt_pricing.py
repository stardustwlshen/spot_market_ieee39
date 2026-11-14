"""Independent R18 quarter price LP with DA SCED reference and 5-minute lift.

Pure construction/arithmetic grants no native, publication or dispatch authority.
The selected RT vector is a diagnostic witness, not a feasibility prerequisite.
"""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime, timezone
import math
from weakref import WeakKeyDictionary
from .gd_r15_da_rt_bridge import require, finite, close, digest
from . import gd_r18_day_session as rt

SCHEMA = 'gd.research.r18.rt_price_input.v1'
SERVICES = ('positive', 'negative', 'primary', 'accident')
DT = 1 / 12
H = .25
POLICY = dict(schema='gd.research.r18.DA_reference_pricing_policy.v1', delta=.1,
    reference='R17_bound_DA96_and_archived_physical_SCED_tail_mean_q96_102',
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
    require(digest(plan)==inp['contract_sha256'], 'exact R17 contract required')
    index = inp['window']['index']
    quarter_rows = {r: [deepcopy(z['resources'][r]) for z in inp['references']] for r in inp['resources']}
    da_reference = {r: [z['reference_mw'] for z in quarter_rows[r]] for r in inp['resources']}
    network_provenance = dict(reference_buses=deepcopy(original['energy_inputs']['reference_buses']),
        lines=deepcopy(original['energy_inputs']['lines']), sections=deepcopy(original['energy_inputs']['sections']),
        row_kinds={l: row['kind'] for l, row in data['network'].items()}, groups=deepcopy(data['groups']))
    d = dict(schema=SCHEMA, source_kind='accepted_fresh_R18_RT', source_binding=deepcopy(binding),
        rt_input=deepcopy(inp), selected_rt_cells=deepcopy(cells), policy=policy,
        contract_sha256=inp['contract_sha256'], references_sha256=digest(inp['references']),
        R14_quarter_rows=quarter_rows, da_reference_mw=da_reference,
        network_provenance=network_provenance, storage_inventory=deepcopy(data['storage_inventory']),
        authored_at_utc=datetime.now(timezone.utc).isoformat(),
        synthetic_available_at=inp['window']['cutoff'], actual_operational_timeliness_claim=False)
    d['quarters'] = derive_quarters(d)
    d['content_sha256'] = digest(d)
    validate_price_inputs(d)
    return d


def derive_quarters(d):
    """R17 source references include archived physical means for q96..102."""
    inp, cells = d['rt_input'], d['selected_rt_cells']
    require(len(cells) == 24, 'complete selected RT cells required')
    result = deepcopy(rt.derive_price_quarters(inp))
    for r, rows in result.items():
        for q, z in enumerate(rows):
            z['selected_rt_mean_mw'] = math.fsum(_power(cells[j],r,inp['resources'][r]) for j in range(3*q,3*q+3))/3
    return result


def validate_price_inputs(d):
    require(d['schema']==SCHEMA and d['source_kind']=='accepted_fresh_R18_RT', 'distinct R18 price DTO required')
    validate_price_policy(d['policy']); rt.validate_window_input(d['rt_input'])
    require(digest({k:v for k,v in d.items() if k!='content_sha256'})==d['content_sha256'], 'price DTO mutated')
    inp = d['rt_input']; R=set(inp['resources'])
    require(type(inp['window']['index']) is int and inp['window']['index'] in range(96), 'R18 day window required')
    require(d['contract_sha256']==inp['contract_sha256'] and d['references_sha256']==digest(inp['references']), 'R17 contract/reference binding mismatch')
    require(d['R14_quarter_rows']=={r:[z['resources'][r] for z in inp['references']] for r in R}, 'reference resource rows changed')
    require(d['da_reference_mw']=={r:[z['resources'][r]['reference_mw'] for z in inp['references']] for r in R}, 'reference power changed')
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


def build_price_template(d):
    """Independent R18 matrix with original midnight target and dated budgets."""
    validate_price_inputs(d)
    model, audit = rt.build_price_template(d['rt_input'])
    audit = dict(audit, schema=SCHEMA+'.matrix', price_input_sha256=digest(d))
    return model, audit


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


_PRICE_TICKETS = WeakKeyDictionary()

class PriceInputTicket:
    __slots__ = ('__weakref__',)


def _price_ticket(d, ticket):
    """Authenticate same live RT session, cap, run and exact sealed price DTO."""
    require(type(ticket) is PriceInputTicket and ticket in _PRICE_TICKETS,
            'fresh typed R18 price input ticket required')
    t = _PRICE_TICKETS[ticket]
    require(digest(d)==t['input_sha256'], 'price input ticket DTO mutated')
    rt._ticket(d['rt_input'], t['rt_ticket'])
    from . import gd_r18_priced_native_adapter as adapter
    source = adapter.accepted_rt_source(t['rt_cap'])
    adapter.assert_rt_run(t['run'],t['rt_cap'])
    require(digest(source['input'])==digest(d['rt_input'])
            and digest(source['cells'])==digest(d['selected_rt_cells'])
            and digest(source['binding'])==digest(d['source_binding']),
            'price ticket source differs from accepted live RT')
    validate_price_inputs(d)
    return t


def prepare_price_inputs(inp,cells,plan,data,original,binding,policy=None,*,rt_ticket,rt_cap,run):
    """Issue price capability only from the accepted live R18 RT transaction."""
    rt._ticket(inp,rt_ticket)
    from . import gd_r18_priced_native_adapter as adapter
    source=adapter.accepted_rt_source(rt_cap)
    adapter.assert_rt_run(run,rt_cap)
    require(digest(source['input'])==digest(inp) and digest(source['cells'])==digest(cells)
            and digest(source['binding'])==digest(binding), 'accepted RT source mismatch')
    d=normalize_price_inputs(inp,cells,plan,data,original,binding,policy)
    ticket=PriceInputTicket()
    _PRICE_TICKETS[ticket]=dict(rt_ticket=rt_ticket,rt_cap=rt_cap,run=run,
        input_sha256=digest(d),model=None)
    _price_ticket(d,ticket)
    return d,ticket


def build_price_model(d, price_ticket):
    t=_price_ticket(d,price_ticket)
    require(t['model'] is None,'one independent PRICE model per live ticket')
    model,audit=build_price_template(d)
    t['model']=model
    t['model_sha256']=rt.model_fingerprint(model,'PRICE')
    return model,audit


def reconstruct_price_cells(d, names, primal):
    """Pure PRICE lift replay, never RT commands or physical reserve awards.

    Returns (24 explicitly non-executable cells, terminal physical state).
    Primal values are numerical inputs only and convey no native acceptance.
    """
    from .gd_r15_da_rt_bridge import integrate_cell
    validate_price_inputs(d)
    require(len(names['variables'])==len(primal)
            and len(set(names['variables']))==len(primal),'unique PRICE primal names required')
    values=dict(zip(names['variables'],primal));inp=d['rt_input']
    required={f'lift[{r},{j}]' for r in inp['resources'] for j in range(24)}
    required.update(f'energy[{r},{j}]' for r,u in inp['resources'].items()
                    if u['kind']=='battery' for j in range(25))
    require(required<=set(values),'complete PRICE lift and energy primal required')
    for name in required:finite(values[name])
    state=deepcopy(inp['projection']['state']);cells=[]
    for j,da in enumerate(inp['da_cells']):
        cell=deepcopy(da)
        cell.pop('reserve_awards_mw',None)
        cell.update(origin='independent_PRICE_lift_not_RT_command',dispatch_executable=False)
        for r,u in inp['resources'].items():
            value=values[f'lift[{r},{j}]'];physical=u['rows'][j]
            require(physical['min_mw']-1e-6<=value<=physical['max_mw']+1e-6,
                    'PRICE lift outside source physical bounds')
            if not d['quarters'][r][j//3]['price_eligible']:
                close(value,_power(da,r,u),'nonpricing PRICE lift changed from source')
        for g,row in cell['thermal'].items():
            value=values[f'lift[{g},{j}]']
            if row['phase'] in ('startup','shutdown'):
                close(value,row['mean_mw'],'PRICE transition curve mean changed')
            else:
                row.update(begin_mw=state['thermal'][g]['power_mw'],mean_mw=value,end_mw=value)
            row['age_begin_h']=state['thermal'][g]['state_age_h']
        for b,row in cell['batteries'].items():
            value=values[f'lift[{b},{j}]']
            row.update(power_mw=value,charge_mw=-value if row['mode']=='charge' else 0.,
                discharge_mw=value if row['mode']=='discharge' else 0.,
                energy_begin_mwh=values[f'energy[{b},{j}]'],energy_end_mwh=values[f'energy[{b},{j+1}]'])
            close(state['batteries'][b]['energy_mwh'],row['energy_begin_mwh'],'PRICE initial energy continuity')
        state=integrate_cell(state,cell,inp['technical'])
        for g,row in cell['thermal'].items():
            row.update(age_end_h=state['thermal'][g]['state_age_h'],
                residence_remaining_h=state['thermal'][g]['residence_remaining_h'])
        for b,row in cell['batteries'].items():
            close(state['batteries'][b]['energy_mwh'],row['energy_end_mwh'],'PRICE SOC integration differs')
            row.update(mode_age_end_h=state['batteries'][b]['mode_age_h'],
                hour_direction=state['batteries'][b]['hour_direction'],
                daily_used_internal_mwh=deepcopy(state['batteries'][b]['daily_used_internal_mwh']))
            if j+1==inp['window']['midnight_local_energy_index']:
                close(row['energy_end_mwh'],inp['midnight_targets'][b],'PRICE original midnight target mismatch')
        cells.append(cell)
    for r,u in inp['resources'].items():
        for q,z in enumerate(d['quarters'][r]):
            mean=math.fsum(values[f'lift[{r},{j}]'] for j in range(3*q,3*q+3))/3
            require(z['lower_mw']-1e-6<=mean<=z['upper_mw']+1e-6,'PRICE quarter band mismatch')
        if u['kind']=='battery':
            close(state['batteries'][r]['energy_mwh'],u['terminal_energy_mwh'],'PRICE rolling terminal mismatch')
    return cells,state
