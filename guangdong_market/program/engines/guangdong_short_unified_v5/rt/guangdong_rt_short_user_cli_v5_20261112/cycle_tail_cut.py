"""Research-only necessary remaining-cycle reachability. Append rows, never relax inputs."""
from copy import deepcopy
from datetime import datetime,timedelta
import math

def append_cycle_tail_rows(q,names,case,declarations):
    out=deepcopy(q); ns=deepcopy(names); ix={v:i for i,v in enumerate(ns['variables'])}
    n=1+max(int(k.rsplit(',',1)[1][:-1]) for k in ix if k.startswith('battery.energy['))
    begin=datetime.fromisoformat(case['contract']['domain_start']); end=begin+timedelta(minutes=5*n)
    day=(end-timedelta(microseconds=1)).date().isoformat()
    declared=declarations['days'][day]['artifact']['storage']; checks=[]
    for u,b in case['batteries'].items():
        d=declared[u]; at=datetime.fromisoformat(d['day_end_at'])
        expected=datetime.fromisoformat(day+'T00:00:00+08:00')+timedelta(days=1)
        if at!=expected:raise ValueError('tail day target date mismatch')
        if at<=end:
            checks.append(dict(unit=u,day=day,rows_added=0,reason='exact target within horizon'));continue
        ledger=b['cycle_budget'][day]
        if ledger['limit_cycles']!=d['limit_cycles']:raise ValueError('tail daily cycle limit mismatch')
        target=d['energy_mwh']; limit=2*b['energy_capacity_mwh']*ledger['limit_cycles'];used=ledger['used_internal_mwh']
        if not all(math.isfinite(x) for x in (target,limit,used)):raise ValueError('nonfinite cycle declaration')
        eta=math.sqrt(b['roundtrip_efficiency']); terms=[];ticks=[]
        for t in range(n):
            if (begin+timedelta(minutes=5*t)).date().isoformat()!=day:continue
            ticks.append(t);terms.extend([[ix[f'battery.charge[{u},{t}]'],eta/12],[ix[f'battery.discharge[{u},{t}]'],1/(12*eta)]])
        for sign in (1,-1):
            out['rows'].append(dict(lb=None,ub=limit-used+sign*target,terms=terms+[[ix[f'battery.energy[{u},{n-1}]'],sign]]))
            ns['constraints'].append(f'cycle_tail_necessary[{u},{day},{"plus" if sign==1 else "minus"}]')
        checks.append(dict(unit=u,day=day,rows_added=2,ticks=ticks,target=target,used_before=used,limit=limit))
    assert out['variables']==q['variables'] and out['objective']==q['objective'] and out['rows'][:len(q['rows'])]==q['rows']
    return out,ns,dict(schema='gd.research.cycle_tail_necessary.v1',formal=False,begin=begin.isoformat(),end=end.isoformat(),tail_day=day,rows_added=len(out['rows'])-len(q['rows']),checks=checks)

def added_row_violations(old,new,primal,tolerance=1e-7):
    values=[]
    for i,row in enumerate(new['rows'][len(old['rows']):],len(old['rows'])):
        value=math.fsum(primal[j]*a for j,a in row['terms']);v=max(0,value-row['ub'])
        values.append(dict(row=i,value=value,upper=row['ub'],violation=v,passed=v<=tolerance))
    return values
