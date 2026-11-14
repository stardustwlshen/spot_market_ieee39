"""Reconstruct the selected RT point in the PRICE LP, without production helpers."""
import math
from independent_price_math import offer_integral,require,close

def check(inp,request,names,reported):
    rt=inp['rt_input'];cells=inp['selected_rt_cells'];values={'zero':0.};cost=0.
    for r,u in rt['resources'].items():
        battery=u['kind']=='battery';family='batteries' if battery else 'thermal';field='power_mw' if battery else 'mean_mw'
        for j,c in enumerate(cells):values[f'lift[{r},{j}]']=c[family][r][field]
        for q in range(8):
            power=math.fsum(values[f'lift[{r},{j}]'] for j in range(3*q,3*q+3))/3;values[f'power[{r},{q}]']=power
            modes={z.get('mode') for z in u['rows'][3*q:3*q+3]}-{'idle',None};mode=next(iter(modes),'discharge');sign=-1 if mode=='charge' else 1
            curve=u['original_curves']['charge' if sign==-1 else 'discharge'] if battery else u['original_offer'];v,fill=offer_integral(curve,power,sign);cost+=.25*v
            for k,amount in enumerate(fill):values[f'segment[{r},{q},{k}]']=amount
            if not battery:
                for s in ('positive','negative','primary','accident'):values[f'reserve[{r},{q},{s}]']=math.fsum(cells[j]['reserve_awards_mw'][r][s] for j in range(3*q,3*q+3))/3
        if battery:
            values[f'energy[{r},0]']=rt['projection']['state']['batteries'][r]['energy_mwh']
            for j,c in enumerate(cells):values[f'energy[{r},{j+1}]']=c['batteries'][r]['energy_end_mwh']
    require(set(values)==set(names['variables']),'independent complete selected PRICE vector')
    vector=[values[n] for n in names['variables']];residuals=[]
    def add(kind,name,value,lo,hi):
        violation=max(0.,0. if lo is None else lo-value,0. if hi is None else value-hi);residuals.append(dict(kind=kind,name=name,value=value,lower=lo,upper=hi,violation=violation))
    for n,v,x in zip(names['variables'],request['variables'],vector):add('variable',n,x,v['lb'],v['ub'])
    for n,r in zip(names['constraints'],request['rows']):add('row',n,math.fsum(c*vector[i] for i,c in r['terms']),r['lb'],r['ub'])
    failures=[r for r in residuals if r['violation']>1e-6]
    require(reported['diagnostic_only'] is True and reported['creates_acceptance'] is False and reported['native_gate'] is False,'non-gating diagnostic scope')
    require(reported['selected_RT_feasible']==(not failures),'selected feasibility result')
    require(len(reported['residuals'])==len(residuals),'every diagnostic row and column')
    for a,b in zip(reported['residuals'],residuals):
        for k in ('kind','name','lower','upper'):require(a[k]==b[k],'diagnostic descriptor '+k)
        for k in ('value','violation'):close(a[k],b[k],'diagnostic arithmetic '+k,1e-7)
    require([(x['kind'],x['name']) for x in reported['failures']]==[(x['kind'],x['name']) for x in failures],'complete independent failure inventory')
    close(reported['max_violation'],max(x['violation'] for x in residuals),'maximum selected violation',1e-7)
    close(reported['selected_RT_quarter_offer_cost_yuan'],cost,'selected original offer cost',1e-6)
    return dict(selected_RT_feasible=not failures,failures=failures,cost_yuan=cost,checked_rows_and_columns=len(residuals),native_gate=False)
