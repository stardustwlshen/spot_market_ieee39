"""Independent arithmetic; no production modules, solver, Pyomo, or capability creation."""
import math
from decimal import Decimal


def require(ok, message):
    if not ok:
        raise ValueError(message)


def close(a, b, message, tolerance=1e-8):
    require(math.isfinite(a) and math.isfinite(b) and abs(a-b) <= tolerance, message)


def band(reference, lo, hi, eligible, delta=.1):
    require(all(math.isfinite(x) for x in (reference, lo, hi, delta)), 'finite band')
    require(type(eligible) is bool and lo <= hi and delta == .1, 'declared policy')
    lower, upper = ((reference-abs(reference)*delta, reference+abs(reference)*delta)
                    if eligible else (reference, reference))
    lower, upper = max(lo, lower), min(hi, upper)
    require(lower <= upper, 'empty physical/DA band')
    return lower, upper


def offer_integral(curve, power, sign=1):
    """Original decimal endpoints and full integral from zero, signed charge bid."""
    magnitude = Decimal(str(power))*sign
    require(magnitude >= 0, 'offer direction')
    value = Decimal(0)
    filled = []
    previous = Decimal(0)
    for row in curve:
        start, end, price = (Decimal(str(row[k])) for k in ('p_start', 'p_end', 'price'))
        require(start == previous and end > start, 'original contiguous endpoints')
        previous = end
        amount = min(max(magnitude-start, Decimal(0)), end-start)
        filled.append(float(amount))
        value += amount*price*sign
    require(magnitude <= previous, 'offer covers power')
    return float(value), filled


def lmps(balance_raw, upper_raw, lower_raw, network, nodes):
    require(set(upper_raw) == set(lower_raw) == set(network), 'all lines and sections')
    require(all(v <= 1e-8 for v in list(upper_raw.values())+list(lower_raw.values())), 'less-equal RHS dual sign')
    return {n: balance_raw/.25 + math.fsum((upper_raw[l]-lower_raw[l])/.25*network[l]['ptdf'][n]
                                         for l in network) for n in nodes}


def certificate(request, response, names):
    """Exhaustive direct row and column KKT, without production audit functions."""
    x, y = response['primal'], response['dual']
    require(len(x) == len(request['variables']) == len(names['variables']), 'full primal')
    require(len(y) == len(request['rows']) == len(names['constraints']), 'full dual')
    require(len(set(names['variables'])) == len(x) and len(set(names['constraints'])) == len(y), 'unique names')
    require(all(math.isfinite(v) for v in x+y), 'finite witness')
    require(request['objective']['minimize'] is True and all(v['kind']=='C' for v in request['variables']), 'continuous minimum')
    cost = [0.]*len(x)
    for i, value in request['objective']['terms']:
        cost[i] += value
    reduced = cost[:]
    objective = request['objective']['constant'] + math.fsum(c*v for c, v in zip(cost, x))
    dual_objective = request['objective']['constant']
    primal_residual = complementarity = sign_residual = 0.
    details = []
    def examine(value, dual, lo, hi):
        violation = max(0., 0. if lo is None else lo-value, 0. if hi is None else value-hi)
        selected = lo if dual >= 0 else hi
        if selected is None:
            return violation, 0., abs(dual), 0.
        return violation, abs((value-selected)*dual), 0., selected*dual
    for name, row, dual in zip(names['constraints'], request['rows'], y):
        activity = math.fsum(c*x[i] for i,c in row['terms'])
        for i,c in row['terms']:
            reduced[i] -= dual*c
        residual, comp, sign, contribution = examine(activity, dual, row['lb'], row['ub'])
        primal_residual=max(primal_residual,residual); complementarity=max(complementarity,comp); sign_residual=max(sign_residual,sign)
        dual_objective += contribution
        details.append(dict(name=name, activity=activity, primal_residual=residual, complementarity=comp))
    for name, var, value, rc in zip(names['variables'], request['variables'], x, reduced):
        residual, comp, sign, contribution = examine(value, rc, var['lb'], var['ub'])
        primal_residual=max(primal_residual,residual); complementarity=max(complementarity,comp); sign_residual=max(sign_residual,sign)
        dual_objective += contribution
    tolerance=1e-5+1e-9*max(1.,abs(objective))
    close(response['objective'], objective, 'primal objective', tolerance)
    if 'bound' in response:
        close(response['bound'], objective, 'reported bound', tolerance)
    require(primal_residual <= 1e-6, 'full primal violation')
    require(sign_residual <= 1e-8, 'dual sign on missing bound')
    require(complementarity <= tolerance and abs(objective-dual_objective) <= tolerance, 'complementarity/strong duality')
    return dict(primal_objective=objective, dual_objective=dual_objective,
                max_primal_violation=primal_residual, max_complementarity=complementarity,
                max_dual_sign_violation=sign_residual, absolute_gap=abs(objective-dual_objective),
                variables=len(x), rows=len(y), checks='every column and every row', details=details)
