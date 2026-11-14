"""Pure R45 matrix constructors; no native adapter, result acceptance, or solve entrypoint.

R15 mathematical bodies retained, with named dated/terminal constraints and
original D96 boundary added to BOTH matrices. PRICE has separate8x15-minute
market variables and24-cell physical lift. No RT solution is required.
"""
import math
from .gd_r45_day_session import (SCHEMA, validate_window_input, derive_price_quarters,
    require, close, digest, DT)
from .gd_r15_rt_pricing import _curve, _power
from .gd_r45_price_terminal import terminal_trace
SERVICES=('positive','negative','primary','accident')
H=.25

def _add_midnight(m, inp):
    import pyomo.environ as p
    k=inp['window']['midnight_local_energy_index']
    m.midnight=p.Constraint(p.Any)
    if k is None:return
    for r,target in inp['midnight_targets'].items():
        if k==24:
            close(inp['resources'][r]['terminal_energy_mwh'],target,'W88 rolling and D96 must coincide')
        else:
            require(0<k<24,'D96 interior energy boundary required')
            m.midnight.add(r,m.energy[r,k]==target)

def build_rt_template(inp):
    """Construct a fresh 24-cell continuous fixed-state RT LP; no solve call."""
    validate_window_input(inp)
    import pyomo.environ as p
    m = p.ConcreteModel('R45_cross_midnight_RT_template')
    R = list(inp['resources']); B = [r for r in R if inp['resources'][r]['kind']=='battery']; G = [r for r in R if r not in B]
    N = inp['nodes']; L = list(inp['network']); J = range(24)
    m.zero = p.Var(bounds=(0., 0.)); m.zero.fix(0.)
    m.power = p.Var(R, J); m.energy = p.Var(B, range(25))
    m.reserve = p.Var(G, J, ('positive','negative','primary','accident'), domain=p.NonNegativeReals)
    m.rows = p.ConstraintList()
    m.rolling_terminal=p.Constraint(p.Any)
    m.daily_throughput=p.Constraint(p.Any)
    for r, u in inp['resources'].items():
        initial = inp['projection']['state']['batteries' if r in B else 'thermal'][r]
        previous = initial['power_mw']
        if r in B:
            m.energy[r,0].fix(initial['energy_mwh'])
        for j, row in enumerate(u['rows']):
            v = m.power[r,j]; v.setlb(row['min_mw']); v.setub(row['max_mw'])
            if r in G and row['phase'] in ('startup','shutdown'):
                require(row['event_id'] and row['curve_source'], 'transition evidence missing')
                close(row['mean_mw'], inp['da_cells'][j]['thermal'][r]['mean_mw'], 'bound transition mean changed')
                m.rows.add(previous+m.zero == row['begin_mw']); previous = row['end_mw']
            else:
                m.rows.add(v-previous <= row['ramp_up_mw_h']*DT)
                m.rows.add(previous-v <= row['ramp_down_mw_h']*DT); previous = v
            if r in G:
                for service in ('positive','negative','primary','accident'):
                    cap = row[service+'_cap_mw']
                    require(row['reserve_eligible'] or cap == 0, 'qualified thermal reserves only')
                    reserve = m.reserve[r,j,service]; reserve.setub(cap)
                    m.rows.add(reserve <= v-row['min_mw'] if service=='negative' else reserve <= row['max_mw']-v)
            else:
                mode = row['mode']
                require(mode == inp['da_cells'][j]['batteries'][r]['mode'], 'published storage mode mismatch')
                coef = u['eta_charge'] if mode=='charge' else 1/u['eta_discharge']
                m.energy[r,j+1].setlb(u['energy_min_mwh']); m.energy[r,j+1].setub(u['energy_max_mwh'])
                m.rows.add(m.energy[r,j+1] == m.energy[r,j]-coef*v*DT)
        if r in B:
            m.rolling_terminal.add(r, m.energy[r,24] == u['terminal_energy_mwh'])
            for date in {c['start'][:10] for c in inp['da_cells']}:
                budget = u['cycle_budget'][date]
                used = initial['daily_used_internal_mwh'].get(date, 0.)
                terms = [(-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge'])*m.power[r,j]*DT for j in J if inp['da_cells'][j]['start'][:10]==date]
                m.daily_throughput.add((r,date), m.zero+sum(terms) <= budget['limit_internal_mwh']-used)
    m.injection = p.Expression(N, J, rule=lambda _,n,j: m.zero+sum(m.power[r,j] for r in R if inp['resources'][r]['node']==n)+inp['fixed_injection_mw'][n][j]-inp['load_mw'][n][j])
    m.balance = p.Constraint(J, rule=lambda _,j: sum(m.injection[n,j] for n in N)==0)
    m.flow = p.Expression(L, J, rule=lambda _,l,j: sum(inp['network'][l]['ptdf'][n]*m.injection[n,j] for n in N))
    m.line_upper = p.Constraint(L, J, rule=lambda _,l,j: m.flow[l,j] <= inp['network'][l]['upper_mw'][j])
    m.line_lower = p.Constraint(L, J, rule=lambda _,l,j: m.flow[l,j] >= inp['network'][l]['lower_mw'][j])
    for j, req in enumerate(inp['reserve']):
        for service in ('positive','negative','primary','accident'):
            m.rows.add(m.zero+sum(m.reserve[g,j,service] for g in G) >= req[service+'_mw'])
    # The unchanged convex source offers are evaluated at every physical cell.
    keys, quotes = [], {}
    for r, u in inp['resources'].items():
        for j, row in enumerate(u['rows']):
            mode = row.get('mode')
            curve = u['original_offer'] if r in G else u['original_curves']['charge' if mode=='charge' else 'discharge']
            quotes[r,j] = curve
            keys.extend((r,j,k) for k in range(len(curve)))
    m.segment = p.Var(keys, domain=p.NonNegativeReals)
    cost = 0.
    for (r,j), curve in quotes.items():
        sign = -1 if inp['resources'][r]['rows'][j].get('mode')=='charge' else 1
        m.rows.add(sign*m.power[r,j] == sum(m.segment[r,j,k] for k in range(len(curve))))
        for k, row in enumerate(curve):
            m.segment[r,j,k].setub(row['p_end']-row['p_start'])
            cost += sign*row['price']*m.segment[r,j,k]*DT
    m.objective = p.Objective(expr=cost)
    from .gd_r45_vre import attach
    attach(m,inp,'RT')
    _add_midnight(m, inp)
    return m, dict(RT_input_prepared=False, pure_template=True, feasibility_established=False, RT_model_built=True, RT_solved=False,
        variables=sum(1 for _ in m.component_data_objects(p.Var)),
        constraints=sum(1 for _ in m.component_data_objects(p.Constraint, active=True)),
        new_native_calls=0, integer_variables=0, official_market=False)


def build_price_template(inp):
    """Fresh independent continuous matrix, not an RT clone or averaged dual."""
    validate_window_input(inp)
    terminal=terminal_trace(inp)
    d=dict(rt_input=inp,quarters=derive_price_quarters(inp))
    import pyomo.environ as p
    inp=d['rt_input']; R=list(inp['resources']); B=[r for r in R if inp['resources'][r]['kind']=='battery']; G=[r for r in R if r not in B]; N=inp['nodes']; L=list(inp['network'])
    m=p.ConcreteModel('R45_cross_midnight_independent_PRICE_template'); m.zero=p.Var(bounds=(0.,0.));m.zero.fix(0.)
    m.power=p.Var(R,range(8));m.lift=p.Var(R,range(24));m.energy=p.Var(B,range(25))
    m.reserve=p.Var(G,range(8),SERVICES,domain=p.NonNegativeReals);m.rows=p.ConstraintList()
    m.rolling_terminal=p.Constraint(p.Any)
    m.daily_throughput=p.Constraint(p.Any)
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
            pass  # R22 timestamp PRICE endpoint semantics
            for date in {c['start'][:10] for c in inp['da_cells']}:
                budget=u['cycle_budget'][date];used=initial['daily_used_internal_mwh'].get(date,0.)
                terms=[(-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge'])*m.lift[r,j]*DT for j in range(24) if inp['da_cells'][j]['start'][:10]==date]
                m.daily_throughput.add((r,date),m.zero+sum(terms)<=budget['limit_internal_mwh']-used)
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
    from .gd_r45_vre import attach
    attach(m,inp,'PRICE')
    _add_price_midnight(m, inp)
    return m,dict(pure_template=True,feasibility_established=False,schema=SCHEMA+'.matrix',price_input_sha256=digest(d),quarters=8,lift_cells=24,
        variables=sum(1 for _ in m.component_data_objects(p.Var)),constraints=sum(1 for _ in m.component_data_objects(p.Constraint,active=True)),
        fresh_independent_matrix=True,integer_variables=0,hard_network=True,dispatch_executable=False,official_market=False,
        new_native_calls=0,selected_RT_witness_is_required_feasible=False,terminal_trace=terminal)



def _add_price_midnight(m,inp):
    import pyomo.environ as p
    m.midnight=p.Constraint(p.Any)
    k=inp['window']['midnight_local_energy_index']
    if k is not None:
        for r,target in inp['midnight_targets'].items():m.midnight.add(r,m.energy[r,k]==target)
