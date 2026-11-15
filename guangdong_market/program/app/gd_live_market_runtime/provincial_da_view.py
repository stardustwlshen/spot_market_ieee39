"""Read-only energy-preserving DA physical references, no raw archive mutation."""
import math

def aggregate_physical(cells):
    if not isinstance(cells,list) or len(cells)%3:
        raise ValueError('explicit complete five-minute DA physical archive required')
    def average(xs):
        if isinstance(xs[0],dict):
            if any(set(x)!=set(xs[0]) for x in xs):raise ValueError('DA physical schemas differ')
            return {k:average([x[k]for x in xs])for k in xs[0]}
        if all(type(x) in (int,float) for x in xs):return math.fsum(xs)/len(xs)
        if all(x==xs[0] for x in xs):return xs[0]
        # Non-numeric state metadata is not a valid dispatch reference.
        return None
    return [average(cells[t:t+3])for t in range(0,len(cells),3)]
