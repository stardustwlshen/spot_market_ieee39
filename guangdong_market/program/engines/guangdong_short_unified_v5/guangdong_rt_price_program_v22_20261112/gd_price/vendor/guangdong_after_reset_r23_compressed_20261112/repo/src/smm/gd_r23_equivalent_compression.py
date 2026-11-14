"""Data-only guarded equivalent integrality/serialization compression.
The original model/payload is never modified. No solver is called here.
"""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json,math

def require(ok,msg):
    if not ok:raise ValueError(msg)
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def terms_dict(terms):
    d={}
    for i,c in terms:d[i]=d.get(i,0.)+c
    return {i:c for i,c in d.items()if c!=0}
def signature(row):return (tuple(sorted(terms_dict(row['terms']).items())),row['lb'],row['ub'])
def _fixed(v):return v['lb']is not None and v['lb']==v['ub']
def _integer(v):return v['kind']in ('B','I')or(_fixed(v)and v['lb']==round(v['lb']))
def _expected(q,idx,terms,lb,ub):
    d={};constant=0.
    for name,c in terms.items():
        i=idx[name];v=q['variables'][i]
        if _fixed(v):constant+=c*v['lb']
        else:d[i]=d.get(i,0.)+c
    return signature({'terms':list(d.items()),'lb':None if lb is None else lb-constant,'ub':None if ub is None else ub-constant})

def guards(q,n,case):
    idx={name:i for i,name in enumerate(n['variables'])};rows={name:row for name,row in zip(n['constraints'],q['rows'])}
    thermal=[];thermal_reasons=[];bess=[];battery_reasons={};mapping=[];T=len(case['energy_inputs']['horizon']['periods']);U=case['energy_inputs']['thermal']
    try:
        require(len([z for z in rows if z.startswith('phase_balance[')])==4*len(U)*T,'complete phase recurrence inventory')
        k=0
        for g,d in U.items():
            require(d['initial_online']in(0,1),'integer initial thermal state');ns=len(case['curves'][g]['startup']);nd=len(case['curves'][g]['shutdown'])
            for t in range(T):
                for v in ('start','stop'):
                    z=q['variables'][idx[f'{v}[{g},{t}]']];require(_integer(z)and z['lb']>=0 and z['ub']<=1,'retained binary events')
                for v in ('stable','off','online'):
                    z=q['variables'][idx[f'{v}[{g},{t}]']];require(z['kind']=='B'and z['lb']==0 and z['ub']==1,'original binary phase bounds')
                a={f'stable[{g},{t}]':1.,f'stop[{g},{t}]':1.};b={f'off[{g},{t}]':1.,f'start[{g},{t}]':1.}
                if t:a[f'stable[{g},{t-1}]']=-1.;b[f'off[{g},{t-1}]']=-1.
                if t>=ns:a[f'start[{g},{t-ns}]']=-1.
                if t>=nd:b[f'stop[{g},{t-nd}]']=-1.
                c={f'stable[{g},{t}]':1.,f'off[{g},{t}]':1.}
                for kind,length in (('start',ns),('stop',nd)):
                    for j in range(max(0,t-length+1),t+1):c[f'{kind}[{g},{j}]']=1.
                for tt,rhs in ((a,d['initial_online']if t==0 else 0.),(b,1-d['initial_online']if t==0 else 0.),(c,1.),({f'online[{g},{t}]':1.,f'off[{g},{t}]':1.},1.)):
                    k+=1;require(signature(rows[f'phase_balance[{k}]'])==_expected(q,idx,tt,rhs,rhs),'exact original phase recursion')
                thermal.extend(idx[f'{v}[{g},{t}]']for v in ('stable','off','online'))
    except (ValueError,KeyError)as exc:thermal=[];thermal_reasons.append(str(exc))
    cost=terms_dict(q['objective']['terms']);day=datetime.fromisoformat(case['contract']['day_start'])
    for uid,d in (case.get('batteries')or{}).items():
        chosen=[];lift=[]
        try:
            require(all(x==0 for x in d['minimum_mode_h'].values())and all(x==0 for x in d['preparation_h'].values()),'zero mode residence/preparation')
            require(not any(z.startswith('battery.c_readiness[')for z in rows),'no readiness constraints')
            modeids=set();allowed=set()
            for t in range(T):
                cm=f'battery.charge_mode[{uid},{t}]';dm=f'battery.discharge_mode[{uid},{t}]';hour=(day+timedelta(minutes=5*t)).replace(minute=0,second=0,microsecond=0).isoformat();hd=f"battery.hour_direction[{uid},'{hour}']"
                hv=q['variables'][idx[hd]];require(_integer(hv)and hv['lb'] is not None and hv['ub'] is not None and 0<=hv['lb']<=hv['ub']<=1,'retained binary hour direction')
                for name in(cm,dm):
                    j=idx[name];v=q['variables'][j]
                    require((v['kind']=='B'and v['lb']==0 and v['ub']==1)if d['availability'][t]else(_fixed(v)and v['lb']==0),'original availability/mode bounds')
                    require(cost.get(j,0.)==0,'no mode objective coefficient');modeids.add(j)
                    if v['kind']=='B':chosen.append(j)
                patterns=[({f'battery.charge[{uid},{t}]':1.,cm:-d['charge_max_mw']},None,0.),({f'battery.discharge[{uid},{t}]':1.,dm:-d['discharge_max_mw']},None,0.),({cm:1.,dm:1.},None,1.),({cm:1.,hd:1.},None,1.),({dm:1.,hd:-1.},None,0.)]
                for direct,mode in (('charge',cm),('discharge',dm)):
                    bp=d[direct+'_curve']['breakpoints_mw']
                    for j in range(len(bp)-1):patterns.append(({f'battery.segment[{uid},{direct},{j},{t}]':1.,mode:-(bp[j+1]-bp[j])},None,0.))
                allowed.update(_expected(q,idx,tt,lo,hi)for tt,lo,hi in patterns)
                lift.append({'charge':idx[cm],'discharge':idx[dm],'hour_direction':idx[hd],'available':d['availability'][t]})
            seen=set()
            for name,row in rows.items():
                if any(j in modeids and c!=0 for j,c in row['terms']):
                    require(name.startswith(('battery.c_power[','battery.c_hour[','battery.c_segment[')),'additional mode constraint family')
                    key=signature(row);require(key in allowed,'additional or changed mode constraint');seen.add(key)
            required={key for key in allowed if any(j in modeids for j,c in key[0])}
            require(required<=seen,'missing original mode power/hour/segment linkage')
            bess.extend(chosen);mapping.extend(lift);battery_reasons[uid]={'eligible':True,'relaxed':len(chosen)}
        except (ValueError,KeyError)as exc:battery_reasons[uid]={'eligible':False,'reason':str(exc)}
    return {'thermal_relax':thermal,'battery_relax':bess,'battery_lift':mapping,'thermal_fallback_reasons':thermal_reasons,'battery_guards':battery_reasons}

def compress(q,n,case):
    require(q['objective']['minimize']is True,'minimum objective');require(len(n['variables'])==len(q['variables'])and len(n['constraints'])==len(q['rows']),'full dimensions')
    g=guards(q,n,case);relax=set(g['thermal_relax']+g['battery_relax']);used={j for row in q['rows']for j,c in row['terms']if c};used.update(j for j,c in q['objective']['terms']if c)
    removed={i:v['lb']for i,v in enumerate(q['variables'])if i not in used and _fixed(v)}
    kept=[i for i in range(len(q['variables']))if i not in removed];reindex={old:new for new,old in enumerate(kept)}
    vv=[]
    for i in kept:
        v=dict(q['variables'][i])
        if i in relax:v['kind']='C'
        vv.append(v)
    rr=[];row_original=[];aliases=[];omitted=[];seen={}
    for i,row in enumerate(q['rows']):
        key=signature(row)
        if not key[0]:
            require((row['lb']is None or row['lb']<=0)and(row['ub']is None or row['ub']>=0),'constant-only contradiction cannot be omitted');omitted.append(i);continue
        if key in seen:aliases.append([i,seen[key]]);continue
        seen[key]=len(rr);row_original.append(i);rr.append({'terms':[[reindex[j],c]for j,c in key[0]],'lb':row['lb'],'ub':row['ub']})
    compact={k:deepcopy(v)for k,v in q.items()if k not in ('variables','rows','objective')};compact.update(variables=vv,rows=rr,objective={'terms':[[reindex[j],c]for j,c in q['objective']['terms']if c],'constant':q['objective']['constant'],'minimize':True})
    names={'variables':[n['variables'][i]for i in kept],'constraints':[n['constraints'][i]for i in row_original]}
    plan={'schema':'gd.research.r23.equivalent_compression.v1','original_payload_sha256':digest(q),'original_names_sha256':digest(n),'kept_columns':kept,'removed_fixed_columns':removed,'kept_rows_original_indices':row_original,'duplicate_row_aliases':aliases,'omitted_tautology_rows':omitted,'guard':g,'original_integer_count':sum(v['kind']!='C'for v in q['variables']),'compressed_integer_count':sum(v['kind']!='C'for v in vv),'mathematics':'only equivalent type compression, unused-fixed column elimination, tautology removal and exact duplicate row removal'}
    return compact,names,plan

def lift(q,plan,x):
    require(digest(q)==plan['original_payload_sha256'],'original payload/plan identity');require(len(x)==len(plan['kept_columns']),'compact witness dimension');out=[None]*len(q['variables'])
    for j,v in plan['removed_fixed_columns'].items():out[int(j)]=v
    for j,v in zip(plan['kept_columns'],x):out[j]=v
    for j in plan['guard']['thermal_relax']:
        require(abs(out[j]-round(out[j]))<=1e-6,'unique thermal phase noninteger');out[j]=round(out[j])
    for m in plan['guard']['battery_lift']:
        h=out[m['hour_direction']];require(abs(h-round(h))<=1e-6,'retained hour direction noninteger');h=round(h);require(h in (0,1),'binary hour permission')
        for j,val in ((m['charge'],1-h),(m['discharge'],h)):
            out[j]=val if m['available']else 0.
    require(all(v is not None and math.isfinite(v)for v in out),'complete finite full lift');return out

def audit(q,x,objective=None,integers=True):
    require(len(x)==len(q['variables']),'full witness dimension');require(all(type(v)in(int,float)and math.isfinite(v)for v in x),'finite numeric witness');worst=0.;integer=0.
    def residual(v,lo,hi):return max(0.,0. if lo is None else lo-v,0. if hi is None else v-hi)
    for v,z in zip(x,q['variables']):
        worst=max(worst,residual(v,z['lb'],z['ub']))
        if z['kind']!='C':integer=max(integer,abs(v-round(v)))
    for row in q['rows']:worst=max(worst,residual(math.fsum(c*x[j]for j,c in row['terms']),row['lb'],row['ub']))
    obj=q['objective']['constant']+math.fsum(c*x[j]for j,c in q['objective']['terms'])
    require(worst<=1e-6 and(not integers or integer<=1e-6),'full original primal/integer certificate failed')
    if objective is not None:require(abs(obj-objective)<=1e-6+1e-9*abs(obj),'full original objective changed')
    return {'variables':len(x),'rows':len(q['rows']),'integer_count':sum(v['kind']!='C'for v in q['variables']),'max_primal_violation':worst,'max_integer_violation':integer,'objective':obj}

def archived_primal_start(case, full, names, core):
    """Map retained source quantities, then require external full-v1 audit.
    Linear DC angle reconstruction is not an optimization or new dispatch.
    """
    import numpy as np
    from pathlib import Path
    p=Path(core)/'ieee39_native_v4b/reports/sced'
    certificate=json.loads((p/'result.json').read_text())
    hashes={Path(z['path']).name:z['sha256']for z in certificate['artifacts']}
    for name in ('input.json','names.json','response.json'):
        require(hashlib.sha256((p/name).read_bytes()).hexdigest()==hashes[name],'old SCED source artifact hash')
    require(case['contract']['source_bindings'][str((p/'input.json').resolve())]==hashes['input.json'],'current case bound to same archived source')
    old=json.loads((p/'input.json').read_text());response=json.loads((p/'response.json').read_text())
    require(response['status']=='optimal'and response['has_primal']is True,'archived optimal primal')
    ov=dict(zip(json.loads((p/'names.json').read_text())['variables'],response['primal']))
    resources=old['physical_lift']['resources'];e=case['energy_inputs'];N=len(e['horizon']['periods']);require(N==576,'complete old physical horizon')
    clean=lambda s:s.replace("'",'')
    require(len(set(map(clean,names['variables'])))==len(names['variables']),'unambiguous variable-name normalization')
    x={clean(name):v['lb']for name,v in zip(names['variables'],full['variables'])if _fixed(v)}
    buses=list(e['buses']);bi={b:i for i,b in enumerate(buses)};net=np.zeros((len(buses),N));lap=np.zeros((len(buses),len(buses)))
    for b in buses:
        for t in range(N):net[bi[b],t]=e['renewable_mw'][b,t]+e['physical_tie_mw'][b,t]-e['load_mw'][b,t]
    for g,d in e['thermal'].items():
        prev='stable'if d['initial_online']else'off'
        for t,r in enumerate(resources[g]['rows']):
            phase=r['phase'];stable=int(phase=='stable');off=int(phase=='off');P=ov[f'physical_power[{g},{t}]'];net[bi[d['bus']],t]+=P
            fields={'power':P,'stable':stable,'off':off,'online':1-off,'start':int(phase=='startup'and prev!=phase),'stop':int(phase=='shutdown'and prev!=phase),'primary':ov[f'physical_primary[{g},{t}]'],'contingency':ov[f'physical_accident[{g},{t}]']}
            for key,value in fields.items():x[f'{key}[{g},{t}]']=value
            left=max(0.,P-d['bid_base_mw'])if stable else 0.
            for s in d['segments']:
                z=min(left,s['width_mw']);x[f"segment_power[{g},{s['key']},{t}]"]=z;left-=z
            prev=phase
    for g,d in case['batteries'].items():
        for t,r in enumerate(resources[g]['rows']):
            P=ov[f'physical_power[{g},{t}]'];net[bi[d['bus']],t]+=P;charge=max(0.,-P);discharge=max(0.,P)
            fields={'charge':charge,'discharge':discharge,'energy':ov[f'physical_energy[{g},{t+1}]'],'charge_mode':int(r['mode']=='charge'),'discharge_mode':int(r['mode']=='discharge')}
            for key,value in fields.items():x[f'battery.{key}[{g},{t}]']=value
            for direction,power in (('charge',charge),('discharge',discharge)):
                left=power;points=d[direction+'_curve']['breakpoints_mw']
                for k,(lo,hi) in enumerate(zip(points,points[1:])):
                    z=min(left,hi-lo);x[f'battery.segment[{g},{direction},{k},{t}]']=z;left-=z
        for h in range(48):
            modes={r['mode']for r in resources[g]['rows'][12*h:12*h+12]};require(not {'charge','discharge'}<=modes,'archived consistent hourly direction')
            day='2026-11-11'if h<24 else'2026-11-12';x[f'battery.hour_direction[{g},{day}T{h%24:02d}:00:00+08:00]']=int('discharge'in modes)
    for line,d in e['lines'].items():
        i,j=bi[d['from_bus']],bi[d['to_bus']];b=d['susceptance_mw_per_rad'];lap[i,i]+=b;lap[j,j]+=b;lap[i,j]-=b;lap[j,i]-=b
    ix=[i for i,b in enumerate(buses)if b not in e['reference_buses']];theta=np.zeros_like(net);theta[ix,:]=np.linalg.solve(lap[np.ix_(ix,ix)],net[ix,:])
    for b in buses:
        for t in range(N):x[f'angle[{b},{t}]']=float(theta[bi[b],t])
    for line,d in e['lines'].items():
        flow=d['susceptance_mw_per_rad']*(theta[bi[d['from_bus']],:]-theta[bi[d['to_bus']],:])
        for t in range(N):x[f'flow[{line},{t}]']=float(flow[t])
    return [float(x[clean(name)])for name in names['variables']],{'source_sha256':hashes,'source_result_sha256':hashlib.sha256((p/'result.json').read_bytes()).hexdigest(),'method':'retained576 physical primal; original segments; deterministic DC linear solve; MIP start only; no decisions fixed','new_native_calls':0}

def fix_all_original_integers(q,x):
    """Exact substitution equivalent to fixing every original Pyomo integer."""
    out=deepcopy(q);fixed={i:round(x[i])for i,v in enumerate(q['variables'])if v['kind']!='C'}
    require(all(abs(x[i]-v)<=1e-6 for i,v in fixed.items()),'accepted full integer selection required')
    for i,v in fixed.items():out['variables'][i].update(kind='C',lb=float(v),ub=float(v),start=None)
    def sub(terms):
        c=math.fsum(v*fixed[i]for i,v in terms if i in fixed)
        return [[i,v]for i,v in terms if i not in fixed],c
    for row in out['rows']:
        row['terms'],c=sub(row['terms'])
        for k in ('lb','ub'):
            if row[k]is not None:row[k]-=c
    out['objective']['terms'],c=sub(out['objective']['terms']);out['objective']['constant']+=c
    out['warmstart']=False;out['dual']=True
    for v in out['variables']:v['start']=None
    return out
