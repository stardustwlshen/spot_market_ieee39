"""Direct 309-cell physical LP, parametrized from tested 24-cell RT constructor."""
from common67 import *
DT=1/12

def validate_full_input(inp):
 require(inp['schema']=='gd.research.r67.frozen_full_input.v1' and len(inp['da_cells'])==309,'full309 input')
 require(inp['future_actual_used'] is False and inp['physical_horizon_cells']==309,'frozen research forecast only')
 require(all(z==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.) for z in inp['reserve']),'original 400/400')
 for r,u in inp['resources'].items():require(len(u['rows'])==309,'full physical modes')
 for family in ('load_mw','fixed_injection_mw'):require(all(len(v)==309 for v in inp[family].values()),'full exogenous coverage')
 return True

def attach_vre(m,inp):
 import pyomo.environ as p
 V=list(inp['renewable']);J=range(309);m.vre_power=p.Var(V,J,domain=p.NonNegativeReals);m.vre_curtailment=p.Var(V,J,domain=p.NonNegativeReals);m.vre_rows=p.ConstraintList();cost=0.
 for b,u in inp['renewable'].items():
  require(u['reserve_credit_mw']==u['AGC_credit_mw']==0 and u['bid_proxy_yuan_mwh']==-50. and u['M2_dispatch_research']==1000.,'same-source VRE policy')
  for j,A in enumerate(u['availability_mw']):
   require(math.isfinite(A) and A>=0,'availability');m.vre_power[b,j].setub(A);m.vre_curtailment[b,j].setub(A);m.vre_rows.add(m.vre_power[b,j]+m.vre_curtailment[b,j]==A)
   m.injection[b,j].set_value(m.injection[b,j].expr+m.vre_power[b,j]);cost+=(-50*m.vre_power[b,j]+1000*m.vre_curtailment[b,j])/12
 m.objective.set_value(m.objective.expr+cost)

def build_full_template(inp):
    """Construct a fresh 24-cell continuous fixed-state RT LP; no solve call."""
    validate_full_input(inp)
    import pyomo.environ as p
    m = p.ConcreteModel('R46_cross_midnight_RT_template')
    R = list(inp['resources']); B = [r for r in R if inp['resources'][r]['kind']=='battery']; G = [r for r in R if r not in B]
    N = inp['nodes']; L = list(inp['network']); J = range(309)
    m.zero = p.Var(bounds=(0., 0.)); m.zero.fix(0.)
    m.power = p.Var(R, J); m.energy = p.Var(B, range(310))
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
            # No arbitrary rolling terminal; original D96 enforced below.
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
    attach_vre(m,inp)
    m.midnight=p.Constraint(p.Any)
    for r,target in inp['midnight_targets'].items():m.midnight.add(r,m.energy[r,288]==target)
    return m, dict(RT_input_prepared=False, pure_template=True, feasibility_established=False, RT_model_built=True, RT_solved=False,
        variables=sum(1 for _ in m.component_data_objects(p.Var)),
        constraints=sum(1 for _ in m.component_data_objects(p.Constraint, active=True)),
        new_native_calls=0, integer_variables=0, official_market=False)

