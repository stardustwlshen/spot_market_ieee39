"""Private file-protocol child. Never import this module from the application."""
import json
import math
import sys
import time


def transport_reserve(remaining_s, preparation_s):
    """Reserve wall time for native exit, response transfer and parent validation.

    Measured model-preparation time is a size/load proxy. Keep at least half of
    the remaining budget available for native optimization even for short calls.
    This is a heuristic reserve, never an extension of the parent's deadline.
    """
    if not math.isfinite(remaining_s) or not math.isfinite(preparation_s) or preparation_s < 0:
        raise ValueError('COPT timing inputs must be finite and preparation time nonnegative')
    available = max(0., remaining_s)
    return min(available * .5, max(.25, available * .05, 2 * preparation_s))


def solve(data):
    started = time.perf_counter()
    import coptpy as cp
    C = cp.COPT
    env = cp.Envr()
    model = env.createModel('smm')
    options = data['options']
    model.setParam('Logging', int(options['output_flag']))
    model.setParam('Threads', options['threads'])
    if 'presolve' in options:
        if type(options['presolve']) is not int or options['presolve'] not in (0, 1, 2, 3):
            raise ValueError('Invalid explicit COPT presolve setting')
        model.setParam('Presolve', options['presolve'])
    if 'mip_rel_gap' in options:
        model.setParam('RelGap', data['gap_settings']['native_copt_rel_gap'])
        model.setParam('AbsGap', data['gap_settings']['native_copt_abs_gap'])
    variables = [model.addVar(lb=-C.INFINITY if v['lb'] is None else v['lb'],
                             ub=C.INFINITY if v['ub'] is None else v['ub'], vtype=v['kind'])
                 for v in data['variables']]
    def expr(terms):
        return cp.LinExpr([variables[i] for i, _ in terms], [c for _, c in terms])
    rows = []
    for row in data['rows']:
        lhs = expr(row['terms'])
        refs = []
        if row['lb'] is not None and row['lb'] == row['ub']:
            refs.append(model.addConstr(lhs == row['lb']))
        else:
            if row['lb'] is not None:
                refs.append(model.addConstr(lhs >= row['lb']))
            if row['ub'] is not None:
                refs.append(model.addConstr(lhs <= row['ub']))
        rows.append(refs)
    obj = data['objective']
    model.setObjective(expr(obj['terms']) + obj['constant'], C.MINIMIZE if obj['minimize'] else C.MAXIMIZE)
    mip = any(v['kind'] != 'C' for v in data['variables'])
    if data['warmstart'] and mip:
        start = [(v, rec['start']) for v, rec in zip(variables, data['variables']) if rec['start'] is not None]
        if start:
            model.setMipStart([v for v, _ in start], [x for _, x in start])
            model.loadMipStart()
    available = data['deadline'] - time.perf_counter()
    reserve = transport_reserve(available, data['preparation_elapsed_s'])
    remaining = available - reserve
    timing = dict(wall_time_limit_s=options['time_limit'], native_time_limit_s=max(0., remaining),
                  transport_reserve_s=reserve, preparation_elapsed_s=data['preparation_elapsed_s'])
    if remaining <= 0:
        return dict(version=1, status='maxTimeLimit', has_primal=False, time_settings=timing)
    model.setParam('TimeLimit', remaining)
    model.solve()
    status = {C.OPTIMAL:'optimal', C.INFEASIBLE:'infeasible', C.UNBOUNDED:'unbounded',
              C.INF_OR_UNB:'infeasibleOrUnbounded', C.TIMEOUT:'maxTimeLimit'}.get(model.status, 'unknown')
    available = bool(model.hasmipsol if mip else model.haslpsol)
    result = dict(version=1, status=status, has_primal=available, time_settings=timing)
    if available and (status == 'optimal' or (mip and status == 'maxTimeLimit')):
        objective = model.objval
        result.update(primal=[v.x for v in variables], objective=objective,
                      bound=model.bestbnd if mip else objective,
                      dual=[sum(c.pi for c in refs) for refs in rows] if not mip and status == 'optimal' else None)
    return result


if __name__ == '__main__':
    try:
        with open(sys.argv[1], encoding='utf-8') as f:
            result = solve(json.load(f))
    except Exception as exc:
        result = dict(version=1, error=f'{type(exc).__name__}: {exc}')
    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        json.dump(result, f, allow_nan=False)
