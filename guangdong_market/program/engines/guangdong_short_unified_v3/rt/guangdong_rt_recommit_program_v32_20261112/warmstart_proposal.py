"""Pure next-version proposal only. Never launches an LP or modifies frozen runtime."""
from copy import deepcopy
import math

def fixed_integer_recourse(current,previous):
    if len(previous)!=len(current['variables']):raise ValueError('mapped full current vector required')
    q=deepcopy(current)
    for v,x in zip(q['variables'],previous):
        if not math.isfinite(x):raise ValueError('finite witness required')
        if v['kind']!='C':
            if abs(x-round(x))>1e-7:raise ValueError('integer witness required')
            x=float(round(x))
            if v['lb'] is not None and x<v['lb']-1e-8 or v['ub'] is not None and x>v['ub']+1e-8:raise ValueError('prefix/domain conflict')
            v.update(kind='C',lb=x,ub=x)
        v['start']=x
    return q

def transplant_only(current,recourse_primal):
    """Caller MUST full-primal/integer audit against current before using output."""
    if len(recourse_primal)!=len(current['variables']) or not all(math.isfinite(x)for x in recourse_primal):raise ValueError('finite full primal required')
    q=deepcopy(current)
    for v,x in zip(q['variables'],recourse_primal):v['start']=x
    q['warmstart']=True
    return q

def mathematical_identity(q):
    z=deepcopy(q);z.pop('warmstart',None)
    for v in z['variables']:v.pop('start',None)
    return z
