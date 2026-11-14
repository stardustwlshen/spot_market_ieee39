"""Independent R17 coefficient oracle. Standard library only; no producer imports.
Re-derives the linear equations from independently source-checked inputs.
Generic equations are compared as a multiset, so row ordering is immaterial.
"""
from collections import Counter
from decimal import Decimal
import math

SERVICES=('positive','negative','primary','accident')

def reconstruct(inp, kind):
    price=kind=='PRICE'; cols={}; rows={}; obj={}; fixed={'zero':0.}; serial=0
    units=inp['resources']; bats=[r for r,u in units.items() if u['kind']=='battery']; gens=[r for r in units if r not in bats]
    N=8 if price else 24; physical='lift' if price else 'power'
    def column(n,lb=None,ub=None,f=None):
        assert n not in cols
        cols[n]={'lb':lb if f is None else f,'ub':ub if f is None else f,'fixed':f is not None,'continuous':True}
        if f is not None:fixed[n]=f
    def equation(t,lo=None,hi=None,n=None):
        nonlocal serial
        if n is None:serial+=1;n='generic[%d]'%serial
        constant=math.fsum(v*fixed[k] for k,v in t.items() if k in fixed)
        clean={k:v for k,v in t.items() if k not in fixed and v!=0}
        assert n not in rows
        rows[n]={'terms':clean,'lb':None if lo is None else lo-constant,'ub':None if hi is None else hi-constant}
    def ramp(v,prev,up,down):
        if isinstance(prev,str):
            equation({v:1,prev:-1},hi=up/12);equation({v:-1,prev:1},hi=down/12)
        else:
            equation({v:1},hi=prev+up/12);equation({v:-1},hi=down/12-prev)
    def rawpower(r,j):return inp['da_cells'][j]['batteries' if r in bats else 'thermal'][r]['power_mw' if r in bats else 'mean_mw']
    column('zero',0.,0.,0.)
    qinfo={}
    if price:
        for r,u in units.items():
            for q in range(8):
                zz=u['rows'][q*3:q*3+3];ref=math.fsum(rawpower(r,j) for j in range(q*3,q*3+3))/3
                modes={z.get('mode') for z in zz}-{'idle',None};phase={z.get('phase') for z in zz}
                eligible=bool(modes) if r in bats else phase=={'stable'}
                low=math.fsum(z['min_mw'] for z in zz)/3;high=math.fsum(z['max_mw'] for z in zz)/3
                a,b=(max(low,ref-.1*abs(ref)),min(high,ref+.1*abs(ref))) if eligible else (ref,ref)
                mode=next(iter(modes),'idle');qinfo[r,q]=(eligible,low,high,mode)
                column(f'power[{r},{q}]',a,b)
    for r,u in units.items():
        for j,z in enumerate(u['rows']):
            f=None
            if price and (not qinfo[r,j//3][0] or z.get('phase') in ('startup','shutdown')):f=rawpower(r,j)
            column(f'{physical}[{r},{j}]',z['min_mw'],z['max_mw'],f)
    for r in bats:
        u=units[r];initial=inp['projection']['state']['batteries'][r]['energy_mwh']
        column(f'energy[{r},0]',f=initial)
        for j in range(1,25):column(f'energy[{r},{j}]',u['energy_min_mwh'],u['energy_max_mwh'])
    for r in gens:
        for t in range(N):
            zz=units[r]['rows'][3*t:3*t+3] if price else [units[r]['rows'][t]]
            for s in SERVICES:column(f'reserve[{r},{t},{s}]',0.,math.fsum(z[s+'_cap_mw'] for z in zz)/len(zz))
    for r,u in units.items():
        state=inp['projection']['state']['batteries' if r in bats else 'thermal'][r];prev=state['power_mw']
        for j,z in enumerate(u['rows']):
            v=f'{physical}[{r},{j}]'
            if z.get('phase') in ('startup','shutdown'):
                equation({prev:1},lo=z['begin_mw'],hi=z['begin_mw']) if isinstance(prev,str) else equation({},lo=z['begin_mw']-prev,hi=z['begin_mw']-prev)
                prev=z['end_mw']
            else:ramp(v,prev,z['ramp_up_mw_h'],z['ramp_down_mw_h']);prev=v
            if r in bats:
                factor=u['eta_charge'] if z['mode']=='charge' else 1/u['eta_discharge']
                equation({f'energy[{r},{j+1}]':1,f'energy[{r},{j}]':-1,v:factor/12},lo=0,hi=0)
            elif not price:
                for s in SERVICES:equation({f'reserve[{r},{j},{s}]':1,v:-1 if s=='negative' else 1},hi=-z['min_mw'] if s=='negative' else z['max_mw'])
        if r in bats:
            equation({f'energy[{r},24]':1},lo=u['terminal_energy_mwh'],hi=u['terminal_energy_mwh'],n=f'rolling_terminal[{r}]')
            for date in sorted({c['start'][:10] for c in inp['da_cells']}):
                budget=u['cycle_budget'][date]['limit_internal_mwh']-state['daily_used_internal_mwh'].get(date,0.)
                equation({f'{physical}[{r},{j}]':(-u['eta_charge'] if u['rows'][j]['mode']=='charge' else 1/u['eta_discharge'])/12 for j,c in enumerate(inp['da_cells']) if c['start'][:10]==date},hi=budget,n=f'daily_throughput[{r},{date}]')
            k=288-3*inp['window']['index']
            if 0<k<24:equation({f'energy[{r},{k}]':1},lo=inp['midnight_targets'][r],hi=inp['midnight_targets'][r],n=f'midnight[{r}]')
        if price:
            for q in range(8):
                equation({f'power[{r},{q}]':1,**{f'lift[{r},{j}]':-1/3 for j in range(3*q,3*q+3)}},lo=0,hi=0)
                if r in gens:
                    for s in SERVICES:equation({f'reserve[{r},{q},{s}]':1,f'power[{r},{q}]':-1 if s=='negative' else 1},hi=-qinfo[r,q][1] if s=='negative' else qinfo[r,q][2])
    for t in range(N):
        ids=range(3*t,3*t+3) if price else [t]
        for s in SERVICES:equation({f'reserve[{r},{t},{s}]':1 for r in gens},lo=math.fsum(inp['reserve'][j][s+'_mw'] for j in ids)/len(ids))
    for r,u in units.items():
        for t in range(N):
            mode=qinfo[r,t][3] if price else u['rows'][t].get('mode');sign=-1 if mode=='charge' else 1
            curve=u['original_curves']['charge' if sign==-1 else 'discharge'] if r in bats else u['original_offer']
            terms={f'power[{r},{t}]':sign}
            last=Decimal(0);previous_cost=-math.inf
            for k,z in enumerate(curve):
                assert Decimal(str(z['p_start']))==last and z['p_end']>z['p_start'];last=Decimal(str(z['p_end']))
                assert sign*z['price']>=previous_cost;previous_cost=sign*z['price']
                width=float(Decimal(str(z['p_end']))-Decimal(str(z['p_start']))) if price else z['p_end']-z['p_start']
                v=f'segment[{r},{t},{k}]';column(v,0,width);terms[v]=-1;obj[v]=sign*z['price']*(.25 if price else 1/12)
            equation(terms,lo=0,hi=0)
    def injection(n,t):
        ids=range(3*t,3*t+3) if price else [t]
        return math.fsum(inp['fixed_injection_mw'][n][j] for j in ids)/len(ids)-math.fsum(inp['load_mw'][n][j] for j in ids)/len(ids)
    for t in range(N):
        rhs=-math.fsum(injection(n,t) for n in inp['nodes']);equation({f'power[{r},{t}]':1 for r in units},lo=rhs,hi=rhs,n=f'balance[{t}]')
    for line,z in inp['network'].items():
        for t in range(N):
            rhs=math.fsum(z['ptdf'][n]*injection(n,t) for n in inp['nodes']);j=3*t if price else t
            terms={f'power[{r},{t}]':z['ptdf'][u['node']] for r,u in units.items()}
            equation(terms,hi=z['upper_mw'][j]-rhs,n=f'line_upper[{line},{t}]')
            equation(terms,lo=z['lower_mw'][j]-rhs,n=f'line_lower[{line},{t}]')
    return {'columns':cols,'rows':rows,'objective':{'terms':obj,'constant':0.,'minimize':True}}


def normalized(row):
    """Orient every inequality lower-first and sign-normalize equations."""
    lo,hi=row['lb'],row['ub'];tt=row['terms'];sign=1
    if lo is None and hi is not None:sign=-1
    elif lo==hi and tt and tt[sorted(tt)[0]]<0:sign=-1
    return {'terms':{k:sign*v for k,v in tt.items() if v!=0},'lb':lo if sign==1 else (None if hi is None else -hi),'ub':hi if sign==1 else (None if lo is None else -lo)}

def signature(row):
    z=normalized(row)
    return (tuple(sorted(z['terms'])),z['lb'] is None,z['ub'] is None)

def compare(actual,expected):
    assert set(actual['columns'])==set(expected['columns']),'column inventory'
    maximum=0.
    def near(a,b,why):
        nonlocal maximum
        if a is None or b is None:assert a is b,why;return
        e=abs(a-b);maximum=max(maximum,e);assert e<=1e-8,(why,a,b)
    for n,e in expected['columns'].items():
        a=actual['columns'][n]
        for k in ('lb','ub'):near(a[k],e[k],n+' '+k)
        assert a['fixed']==e['fixed'] and a['continuous']==e['continuous'],n+' fixed/domain'
    def match(a,e,n):
        a,e=normalized(a),normalized(e)
        assert set(a['terms'])==set(e['terms']),(n,'term inventory')
        for k in a['terms']:near(a['terms'][k],e['terms'][k],n+' '+k)
        for k in ('lb','ub'):near(a[k],e[k],n+' '+k)
    aa={n:r for n,r in actual['rows'].items() if not n.startswith('rows[')}
    ee={n:r for n,r in expected['rows'].items() if not n.startswith('generic[')}
    assert set(aa)==set(ee),('named constraints',set(aa)^set(ee))
    for n in aa:match(aa[n],ee[n],n)
    amap={};emap={}
    for n,r in actual['rows'].items():
        if n.startswith('rows['):amap.setdefault(signature(r),[]).append(r)
    for n,r in expected['rows'].items():
        if n.startswith('generic['):emap.setdefault(signature(r),[]).append(r)
    assert set(amap)==set(emap),('generic supports',set(amap)^set(emap))
    for s in amap:
        assert len(amap[s])==len(emap[s]),('row count',s)
        sortkey=lambda r:(normalized(r)['lb'] or 0,normalized(r)['ub'] or 0,tuple(sorted(normalized(r)['terms'].items())))
        for a,e in zip(sorted(amap[s],key=sortkey),sorted(emap[s],key=sortkey)):match(a,e,str(s))
    assert set(actual['objective']['terms'])==set(expected['objective']['terms']),'objective support'
    for n,v in expected['objective']['terms'].items():near(actual['objective']['terms'][n],v,'objective '+n)
    near(actual['objective']['constant'],0.,'objective constant');assert actual['objective']['minimize'] is True
    return {'columns':len(expected['columns']),'rows':len(expected['rows']),'objective_terms':len(expected['objective']['terms']),'maximum_absolute_difference':maximum}
