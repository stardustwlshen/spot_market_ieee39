"""Provincial compressed DA98: two modeled points, not an executable next-day path."""
from copy import deepcopy

def extend(case):
    loads=case['energy_inputs']['load_mw']; totals={t:sum(v for (b,j),v in loads.items() if j==t) for t in range(96)}
    valley=min(totals,key=lambda t:(totals[t],t));peak=min(totals,key=lambda t:(-totals[t],t))
    # A flat day still has distinct representative roles, even when source times coincide.
    ordered=sorted([(valley,'valley'),(peak,'peak')],key=lambda x:(x[0],x[1]))
    mapping=[dict(tick=96+i,source_day_quarter=t,role='representative',kind=kind,settle=False,objective_duration_h=.25,state_link_basis='compressed_index_recurrence_not_physical_adjacent_clock',weight_basis='provincial_formula_delta_t_15min',selection_basis='user_next_day_equals_same_day_first_total_load_extremum') for i,(t,kind) in enumerate(ordered)]
    def walk(z):
        if isinstance(z,dict):
            if z and all(type(k)is int for k in z) and set(z)==set(range(96)):
                return {**deepcopy(z),**{x['tick']:deepcopy(z[x['source_day_quarter']]) for x in mapping}}
            if z and all(isinstance(k,tuple) and len(k)==2 and type(k[1])is int for k in z):
                out=deepcopy(z)
                for b in {k[0] for k in z}:
                    for x in mapping:out[b,x['tick']]=deepcopy(z[b,x['source_day_quarter']])
                return out
            return {k:walk(v) for k,v in z.items()}
        if isinstance(z,list):return [walk(v) for v in z]
        if isinstance(z,tuple):return tuple(walk(v) for v in z)
        return deepcopy(z)
    out=walk(case);out['representative_mapping']=mapping
    return out,mapping
