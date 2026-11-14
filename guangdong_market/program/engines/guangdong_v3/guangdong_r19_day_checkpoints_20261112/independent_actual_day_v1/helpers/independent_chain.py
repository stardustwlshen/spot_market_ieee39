"""Independent realized-scope state and cutoff arithmetic, no producer replay."""
from copy import deepcopy
from datetime import datetime,timedelta
from hashlib import sha256
import math,json

def require(ok,message):
    if not ok:raise ValueError(message)

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return sha256(canonical(v)).hexdigest()
def stamp(v):
    t=datetime.fromisoformat(v);require(t.utcoffset()==timedelta(hours=8),'Shanghai logical clock');return t

def equal(a,b,path='root'):
    if isinstance(a,dict):
        require(isinstance(b,dict) and set(a)==set(b),'key inventory '+path)
        for k in a:equal(a[k],b[k],path+'.'+str(k))
    elif isinstance(a,list):
        require(isinstance(b,list) and len(a)==len(b),'list shape '+path)
        for k,(x,y) in enumerate(zip(a,b)):equal(x,y,path+f'[{k}]')
    elif type(a) in (int,float) and type(b) in (int,float):require(math.isfinite(a) and math.isfinite(b) and abs(a-b)<=1e-6,'numeric mismatch '+path)
    else:require(type(a)==type(b) and a==b,'value mismatch '+path)

def evolve(state,cell,technical):
    dt=1/12;tol=1e-6
    require(state['at']==cell['start'] and stamp(cell['end'])-stamp(cell['start'])==timedelta(minutes=5),'exact next cell')
    result=deepcopy(state);result['at']=cell['end']
    for g,t in technical['thermal'].items():
        old=state['thermal'][g];r=cell['thermal'][g]
        # The actual audited first two windows/preparation exercise stable/off
        # states only. Reject any hidden transition rather than borrow producer logic.
        require(r['phase'] in ('stable','off') and old['phase']==r['phase'],'unexpected transition outside actual two-window audit scope')
        equal(old['power_mw'],r['begin_mw'],'thermal begin '+g);equal(r['mean_mw'],r['end_mw'],'thermal mean '+g)
        require(-t['ramp_down_mw_per_h']*dt-tol<=r['end_mw']-old['power_mw']<=t['ramp_up_mw_per_h']*dt+tol,'thermal signed ramp '+g)
        if r['phase']=='off':equal(r['end_mw'],0.,'offline power')
        age=old['state_age_h']+dt;online=int(r['phase']=='stable')
        result['thermal'][g]=dict(phase=r['phase'],online=online,state_age_h=age,power_mw=r['end_mw'],curve_step=None,curve_length=None,residence_remaining_h=max(0.,t['min_up_h' if online else 'min_down_h']-age))
        if 'age_begin_h' in r:equal(r['age_begin_h'],old['state_age_h'],'thermal age start')
        if 'age_end_h' in r:equal(r['age_end_h'],age,'thermal age end')
    start=stamp(cell['start']);end=stamp(cell['end']);date=start.date().isoformat();enddate=end.date().isoformat()
    hour=start.replace(minute=0,second=0,microsecond=0).isoformat();endhour=end.replace(minute=0,second=0,microsecond=0).isoformat()
    for b,t in technical['batteries'].items():
        old=state['batteries'][b];r=cell['batteries'][b];c=r['charge_mw'];d=r['discharge_mw'];mode=r['mode'];sign={'idle':0,'charge':-1,'discharge':1}[mode]
        require(-tol<=c<=t['charge_max_mw']+tol and -tol<=d<=t['discharge_max_mw']+tol,'battery rate '+b)
        require(abs(c)+abs(d)<=tol if mode=='idle' else abs(d if mode=='charge' else c)<=tol,'battery mode '+b)
        equal(d-c,r['power_mw'],'signed power '+b)
        require(-t['ramp_down_mw_per_h']*dt-tol<=r['power_mw']-old['power_mw']<=t['ramp_up_mw_per_h']*dt+tol,'battery signed ramp '+b)
        energy=old['energy_mwh']+(t['eta_charge']*c-d/t['eta_discharge'])*dt
        require(t['energy_min_mwh']-tol<=energy<=t['energy_max_mwh']+tol,'battery bounds '+b)
        if 'energy_begin_mwh' in r:equal(r['energy_begin_mwh'],old['energy_mwh'],'energy begin '+b)
        if 'energy_end_mwh' in r:equal(r['energy_end_mwh'],energy,'energy end '+b)
        direction=old['hour_direction'] if old['calendar_hour']==hour else 0
        require(r['selected_hour_direction_permission'] in (-1,0,1) and (not sign or sign==r['selected_hour_direction_permission']),'direction permission '+b)
        require(not sign or direction in (0,sign),'same-hour reversal '+b)
        if sign:direction=sign
        if mode!=old['mode']:
            require(old['mode_age_h']+tol>=t['minimum_mode_h'][old['mode']],'minimum mode residence')
            require(t['preparation_h'].get(mode,0.)==0.,'unexpected nonzero battery preparation')
        used=deepcopy(old['daily_used_internal_mwh']);throughput=(t['eta_charge']*c+d/t['eta_discharge'])*dt
        used[date]=used.get(date,0.)+throughput;used.setdefault(enddate,0.)
        budget=t['cycle_budget'].get(date)
        require(budget is not None or used[date]<=tol,'missing date budget')
        if budget:require(used[date]<=budget['limit_internal_mwh']+tol,'daily throughput budget')
        result['batteries'][b]=dict(energy_mwh=energy,power_mw=r['power_mw'],mode=mode,mode_age_h=dt if mode!=old['mode'] else old['mode_age_h']+dt,hour_direction=direction if endhour==hour else 0,calendar_hour=endhour,daily_used_internal_mwh=used,cumulative_internal_mwh=old['cumulative_internal_mwh']+throughput)
        if 'mode_age_end_h' in r:equal(r['mode_age_end_h'],result['batteries'][b]['mode_age_h'],'mode age '+b)
        if 'hour_direction' in r:equal(r['hour_direction'],result['batteries'][b]['hour_direction'],'hour direction '+b)
        if 'daily_used_internal_mwh' in r:equal(r['daily_used_internal_mwh'],used,'date ledger '+b)
    return result

def prior_value(prior,resource,t):
    h=(t-stamp(prior['epoch'])).total_seconds()/3600
    phase=int(sha256(f"{prior['seed']}:{resource}".encode()).hexdigest()[:8],16)/(2**32)*2*math.pi
    scale=prior['resources'][resource]['scale_mw'];family=prior['family']
    if family=='load_mw':v=scale*(.91+.09*math.cos(2*math.pi*(h-18)/24)+.025*math.sin(4*math.pi*h/24+phase)+.0002*h)
    elif family=='wind_mw':v=scale*(.38+.10*math.sin(2*math.pi*h/18+phase)+.035*math.cos(2*math.pi*h/7+phase))
    elif family=='solar_mw':v=scale*.72*max(0.,math.sin(math.pi*((h%24)-6)/12)) if 6<=h%24<=18 else 0.
    else:v=scale*(.70+.05*math.cos(2*math.pi*h/24+phase)+.0001*h)
    return round(v,9)

def check_forecast(source,inp):
    families={};cutoff=stamp(inp['window']['cutoff']);start=stamp(inp['window']['start'])
    for family in ('load_mw','wind_mw','solar_mw','boundary_tie_mw'):
        saved=source['contract']['sources']['prior:'+family];raw=saved['artifact'];prior=json.loads(raw)
        require(sha256(raw).hexdigest()==saved['sha256']==inp['vintages'][family]['prior_sha256'],'prior binding '+family)
        require(prior['uses_future_actual'] is False and all(stamp(prior[k])<=cutoff for k in ('published_at','available_at','information_end')) and stamp(saved['available_at'])<=cutoff,'prior cutoff '+family)
        vintage=inp['vintages'][family]
        require(vintage['issued_at']==vintage['available_at']==inp['window']['cutoff'] and vintage['uses_future_actual'] is False and vintage['is_measurement'] is False,'synthetic vintage clock '+family)
        values={n:[0.]*24 for n in inp['nodes']}
        for r,info in prior['resources'].items():
            knots=[prior_value(prior,r,start+timedelta(minutes=15*j)) for j in range(9)]
            for j in range(24):
                q,k=divmod(j,3);values[info['bus']][j]+=knots[q]+(knots[q+1]-knots[q])*(k+.5)/3
        families[family]=values
    equal(inp['load_mw'],families['load_mw'],'independently reconstructed load forecast')
    fixed={n:[sum(families[f][n][j] for f in ('wind_mw','solar_mw','boundary_tie_mw')) for j in range(24)] for n in inp['nodes']}
    equal(inp['fixed_injection_mw'],fixed,'independently reconstructed exogenous injections')
    return dict(passed=True,families=4,nodes=len(inp['nodes']),cells=24,uses_future_actual=False)
