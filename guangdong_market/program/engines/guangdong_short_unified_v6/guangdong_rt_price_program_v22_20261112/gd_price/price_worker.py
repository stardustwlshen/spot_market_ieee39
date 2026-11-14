"""Private file protocol worker using official highspy; no solver fallback.

HiGHS row_dual is sensitivity to the row RHS in the original objective sense.
Only optimal continuous models provide duals. Mixed integer incumbents never do.
"""
import json
import math
import sys
import time


def transport_reserve(remaining_s, preparation_s):
    if not math.isfinite(remaining_s) or not math.isfinite(preparation_s) or preparation_s < 0:
        raise ValueError('HiGHS timing inputs must be finite and preparation time nonnegative')
    available=max(0.,remaining_s)
    return min(available*.5,max(.25,available*.05,2*preparation_s))


OPTIMIZE_CALLS=0

def solve(data):
    global OPTIMIZE_CALLS
    warmstart=data.get("warmstart",False)
    if type(warmstart)is not bool:raise ValueError("warmstart must be bool when supplied")
    import highspy as hs
    import numpy as np
    h=hs.Highs()
    def checked(status, action):
        if status != hs.HighsStatus.kOk:
            raise RuntimeError('HiGHS rejected '+action+': '+str(status))
    options=data['options']
    for name in ('output_flag','threads','presolve','mip_rel_gap','mip_abs_gap','dual_feasibility_tolerance','simplex_scale_strategy'):
        checked(h.setOptionValue(name,options[name]),'option '+name)
    applied={name:h.getOptionValue(name)[1] for name in ('output_flag','threads','presolve','mip_rel_gap','mip_abs_gap','dual_feasibility_tolerance','simplex_scale_strategy')}
    status,value=h.getOptionValue('dual_feasibility_tolerance')
    checked(status,'read back dual_feasibility_tolerance')
    if options['dual_feasibility_tolerance']!=1e-9 or value!=options['dual_feasibility_tolerance']:
        raise ValueError('R53 precision-only worker requires exact dual_feasibility_tolerance=1e-9 readback')
    status,scale=h.getOptionValue('simplex_scale_strategy')
    checked(status,'read back simplex_scale_strategy')
    if type(options['simplex_scale_strategy']) is not int or options['simplex_scale_strategy']!=0 or scale!=0:
        raise ValueError('R53 v3 requires simplex_scale_strategy=0 readback')
    n=len(data['variables']); rows=data['rows']; obj=data['objective']
    model=hs.HighsLp(); model.num_col_=n;model.num_row_=len(rows)
    costs=[0.]*n
    for i,c in obj['terms']:costs[i]+=c
    model.col_cost_=costs
    model.col_lower_=[-hs.kHighsInf if v['lb'] is None else v['lb'] for v in data['variables']]
    model.col_upper_=[hs.kHighsInf if v['ub'] is None else v['ub'] for v in data['variables']]
    model.row_lower_=[-hs.kHighsInf if row['lb'] is None else row['lb'] for row in rows]
    model.row_upper_=[hs.kHighsInf if row['ub'] is None else row['ub'] for row in rows]
    model.sense_=hs.ObjSense.kMinimize if obj['minimize'] else hs.ObjSense.kMaximize
    model.offset_=obj['constant']
    model.a_matrix_.format_=hs.MatrixFormat.kRowwise
    starts=[0]; indices=[];values=[]
    for row in rows:
        for i,c in row['terms']:indices.append(i);values.append(c)
        starts.append(len(indices))
    model.a_matrix_.start_=starts;model.a_matrix_.index_=indices;model.a_matrix_.value_=values
    model.integrality_=[hs.HighsVarType.kContinuous if v['kind']=='C' else hs.HighsVarType.kInteger for v in data['variables']]
    mip=any(v['kind']!='C' for v in data['variables'])
    checked(h.passModel(model),'linear model')
    if warmstart and mip:
        start=[(i,v['start']) for i,v in enumerate(data['variables']) if v['start'] is not None]
        if start:checked(h.setSolution(len(start),np.array([i for i,_ in start],dtype=np.int32),np.array([x for _,x in start],dtype=float)),'MIP start')
    available=data['deadline']-time.perf_counter()
    # User limit is per native solve; preparation/transport have separate grace.
    reserve=300.;remaining=options['time_limit']
    if remaining!=3600.:raise ValueError('current policy requires native time_limit=3600')
    timing=dict(wall_time_limit_s=options['time_limit']+300.,solver_limit_scope='each_native_solve',mip_gap_applicable=mip,native_time_limit_s=max(0.,remaining),
        transport_reserve_s=reserve,preparation_elapsed_s=data['preparation_elapsed_s'])
    base=dict(version=1,backend='highs',solver_version=h.version(),time_settings=timing,native_settings=applied)
    if available<remaining:return dict(**base,status='maxTimeLimit',has_primal=False,native_status='startup_grace_exceeded_before_native_run',optimize_calls=OPTIMIZE_CALLS)
    checked(h.setOptionValue('time_limit',remaining),'time_limit')
    applied['time_limit']=h.getOptionValue('time_limit')[1]
    if applied['time_limit']!=3600.:raise ValueError('HiGHS time limit readback')
    OPTIMIZE_CALLS+=1
    run_status=h.run()
    if run_status==hs.HighsStatus.kError:raise RuntimeError('HiGHS failed to run')
    raw_status=h.getModelStatus()
    status={hs.HighsModelStatus.kOptimal:'optimal',hs.HighsModelStatus.kInfeasible:'infeasible',
        hs.HighsModelStatus.kUnbounded:'unbounded',hs.HighsModelStatus.kUnboundedOrInfeasible:'infeasibleOrUnbounded',
        hs.HighsModelStatus.kTimeLimit:'maxTimeLimit'}.get(raw_status,'unknown')
    sol=h.getSolution(); info=h.getInfo()
    available=bool(sol.value_valid and info.primal_solution_status==hs.SolutionStatus.kSolutionStatusFeasible)
    result=dict(**base,status=status,has_primal=bool(available and (status=='optimal' or (mip and status=='maxTimeLimit'))),
        primal_witness_available=available,native_status=h.modelStatusToString(raw_status),
        dual_valid=bool(not mip and status=='optimal' and sol.dual_valid),
        native_mip_gap=float(info.mip_gap) if mip and math.isfinite(info.mip_gap) else None)
    result['native_numerical_diagnostics']={name:getattr(info,name) for name in ('max_dual_infeasibility','num_dual_infeasibilities','max_primal_infeasibility','num_primal_infeasibilities','simplex_iteration_count')}
    if available and (status=='optimal' or (mip and status=='maxTimeLimit')):
        objective=float(info.objective_function_value)
        bound=float(info.mip_dual_bound) if mip else objective
        if not math.isfinite(bound):raise RuntimeError('HiGHS feasible incumbent has no finite dual bound; no acceptance')
        result.update(primal=list(sol.col_value),objective=objective,bound=bound,
            dual=list(sol.row_dual) if result['dual_valid'] else None)
    result['optimize_calls']=OPTIMIZE_CALLS
    return result


def safe_response(result):
    """Preserve status, encode unavailable nonfinite values explicitly, never invent numbers."""
    records=[]
    def visit(v,path):
        if isinstance(v,float) and not math.isfinite(v):
            records.append(dict(path=path,symbol='NaN'if math.isnan(v)else '+Infinity'if v>0 else '-Infinity',reason='native non-finite numeric value; represented as JSON null, not zero or a certificate'))
            return None
        if isinstance(v,dict):return {k:visit(x,path+'/'+str(k).replace('~','~0').replace('/','~1'))for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [visit(x,path+'/'+str(i))for i,x in enumerate(v)]
        return v
    out=visit(result,'')
    out['nonfinite_fields']=records
    out['serialization_policy']='explicit_null_with_original_nonfinite_symbol_v1'
    bad=any(z['path'].split('/')[1]in('primal','dual','objective','bound')for z in records)
    out['finite_solution_payload']=not bad
    if bad:
        # native_status/status stay untouched, but no numerical acceptance can be inferred.
        out['has_primal']=False;out['dual_valid']=False
        out['solution_payload_rejection']='nonfinite primal/dual/objective/bound; not an acceptable certificate'
    return out

def write_complete(path,result):
    import os
    from pathlib import Path
    target=Path(path)
    if target.exists():raise FileExistsError('refuse overwrite response evidence: '+str(target))
    encoded=json.dumps(safe_response(result),allow_nan=False,separators=(',',':'))+'\n'
    temporary=target.with_name(target.name+'.writing')
    with temporary.open('x',encoding='utf-8')as stream:
        stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    # Exclusive final create prevents racing over a separately completed result.
    os.link(temporary,target);temporary.unlink()

if __name__=='__main__':
    try:
        with open(sys.argv[1],encoding='utf-8')as stream:result=solve(json.load(stream))
    except Exception as exc:result=dict(version=1,error=f'{type(exc).__name__}: {exc}',optimize_calls=OPTIMIZE_CALLS)
    write_complete(sys.argv[2],result)
