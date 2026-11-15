"""Single-unit fixed-speed pumped hydro, provincial 15-minute physical interface.
GD 2026 energy rules pp.110-111: fixed pumping, binary mode, water conservation,
and soft DA reservoir bands. Numeric declarations here remain research inputs.
No automatic reserve qualification, fabricated DA trajectory, or battery SOC proxy.
"""
import math
from .numeric_boundary import check_state
from copy import deepcopy
import pyomo.environ as pyo


def require(ok,msg):
    if not ok:raise ValueError(msg)


def finite(x):
    require(not isinstance(x,bool) and math.isfinite(float(x)), 'finite numeric declaration required')
    return float(x)


def duration_steps(parameters, kind):
    """15-minute research defaults; explicit declarations override, never official values."""
    hours=finite(parameters.get('min_'+kind+'_time_h', .5 if kind!='off' else .25))
    require(hours>=0, 'nonnegative pumped minimum duration required')
    return int(math.ceil(hours/.25-1e-12))


def attach(m, inventory, declarations, *, duration_h=.25):
    require(abs(duration_h-.25)<1e-12,'provincial 15min pumped grid required')
    require(not hasattr(m,'pumped'),'pumped block already attached')
    ts=tuple(m.T); require(ts and ts==tuple(range(len(ts))),'dense local ticks required')
    require(set(inventory)==set(declarations),'all retained pumped stations require explicit declarations')
    data={};units=[]
    for station,record in inventory.items():
        p=deepcopy(record['parameters']);d=deepcopy(declarations[station])
        for kind in ('gen','pump','off'):duration_steps(p,kind)
        require(p.get('enabled') is True,'disabled station must be explicitly excluded before attach')
        require(p['variable_speed'] is False,'variable-speed pumping requires a separate validated model')
        require(type(p['n_units']) is int and p['n_units']>0,'positive integer unit count')
        bus=p['bus'];bus=bus if bus in m.B else str(bus);require(bus in m.B,'pumped node absent')
        for key in ('unit_gen_min','unit_gen_max','unit_pump','e_gen','e_pump','v_total','vup_min','vup_max','vdn_min','vdn_max','start_cost_gen','start_cost_pump'):
            finite(p[key]);require(p[key]>=0,'negative pumped parameter')
        require(0<p['unit_gen_min']<=p['unit_gen_max'] and p['unit_pump']>0 and 0<p['e_gen']<=p['e_pump'],'invalid pumped power or efficiency')
        require(d.get('source') and d.get('basis')=='research','explicit parameter/source declaration required')
        for key in ('gen_bid_yuan_mwh','pump_bid_yuan_mwh','band_m3','band_penalty_yuan_m3'):
            finite(d[key])
        require(d['band_m3']>=0 and d['band_penalty_yuan_m3']>0,'nonnegative band and positive slack penalty required')
        stage=d.get('stage','RT');require(stage in ('DA','RT'),'known market stage')
        if stage=='RT':require(set(d['da_upper_m3'])==set(ts) and set(d['da_lower_m3'])==set(ts),'complete DA reservoir reference required')
        up=finite(record['upper_m3']);lo=finite(record['lower_m3']);total=p['v_total']*10000
        require(abs(up+lo-total)<=1e-5,'initial water mass mismatch')
        d['_initial_water_checks']=[check_state(value,lower=p[mn]*10000,upper=p[mx]*10000,capacity_scale=p[mx]*10000,unit='m3') for value,mn,mx in [(up,'vup_min','vup_max'),(lo,'vdn_min','vdn_max')]]
        if stage=='RT':
            for t in ts:
                u=finite(d['da_upper_m3'][t]);l=finite(d['da_lower_m3'][t]);require(abs(u+l-total)<1e-5,'DA reference water mass mismatch')
        else:
            require(len(ts) in (96,98),'DA requires 96 day periods with optional two representative points')
            require(abs(finite(d['terminal_upper_m3'])+finite(d['terminal_lower_m3'])-total)<1e-5,'DA terminal water mass mismatch')
        names=[f'{station}_{i+1}' for i in range(p['n_units'])]
        require(set(record['modes'])==set(names),'all physical unit modes required')
        require(not ({'gen','pump'}<=set(record['modes'].values())),'same-node pumped optimization unit cannot inherit simultaneous generation and pumping')
        require(set(d['initial_units'])==set(names),'complete inherited unit state required')
        for unit in names:
            z=d['initial_units'][unit]
            require(type(z.get('mode_age_steps')) is int and z['mode_age_steps']>=0,'explicit inherited pumped mode_age_steps required; do not infer from missing history')
            for key in ('gen_mw','pump_mw'):finite(z.get(key))
            require(record['modes'][unit] in ('off','gen','pump'),'known initial mode')
            initial=record['modes'][unit]
            gen_bounds=(p['unit_gen_min'],p['unit_gen_max']) if initial=='gen' else (0.,0.)
            pump_bounds=(p['unit_pump'],p['unit_pump']) if initial=='pump' else (0.,0.)
            z['_initial_power_checks']=[check_state(z[key],lower=bounds[0],upper=bounds[1],capacity_scale=scale,unit='MW') for key,bounds,scale in [('gen_mw',gen_bounds,p['unit_gen_max']),('pump_mw',pump_bounds,p['unit_pump'])]]
            require(z['last_active_mode'] in ('off','gen','pump'),'inherited last active mode required')
            require(record['modes'][unit]=='off' or z['last_active_mode']==record['modes'][unit],'last active mode contradicts current mode')
            for k in ('gen_starts_used','pump_starts_used','opposite_mode_block_steps'):
                require(type(z[k]) is int and z[k]>=0,'nonnegative inherited counters required')
            require(type(p['switch_gap']) is int and p['switch_gap']>=0,'switch gap is explicit 15min research count')
            for kind in ('gen','pump'):
                cap=p['max_'+kind+'_starts'];require(type(cap)is int and cap>=z[kind+'_starts_used'],'daily start budget invalid')
            units.append((station,unit))
        data[station]=dict(p=p,d=d,bus=bus,up=up,lo=lo,total=total,names=names,initial_modes=deepcopy(record['modes']))
    b=m.pumped=pyo.Block();b.S=pyo.Set(initialize=tuple(data));b.U=pyo.Set(initialize=units,dimen=2);b.T=pyo.Set(initialize=ts)
    b.gen_mode=pyo.Var(b.U,b.T,domain=pyo.Binary);b.pump_mode=pyo.Var(b.U,b.T,domain=pyo.Binary)
    b.gen_start=pyo.Var(b.U,b.T,domain=pyo.Binary);b.pump_start=pyo.Var(b.U,b.T,domain=pyo.Binary)
    b.gen=pyo.Var(b.U,b.T,domain=pyo.NonNegativeReals);b.pump=pyo.Var(b.U,b.T,domain=pyo.NonNegativeReals)
    b.upper=pyo.Var(b.S,b.T,domain=pyo.NonNegativeReals,bounds=lambda _,s,t:(data[s]['p']['vup_min']*10000,data[s]['p']['vup_max']*10000))
    b.lower=pyo.Var(b.S,b.T,domain=pyo.NonNegativeReals,bounds=lambda _,s,t:(data[s]['p']['vdn_min']*10000,data[s]['p']['vdn_max']*10000))
    b.band_excess=pyo.Var(b.S,b.T,domain=pyo.NonNegativeReals)
    b.physical=pyo.ConstraintList();b.starts=pyo.ConstraintList();b.water=pyo.ConstraintList();b.band=pyo.ConstraintList()
    for s,z in data.items():
        p,d=z['p'],z['d']
        # Guangdong DA objective defines a same-station, same-node optimization
        # unit. Rule (21), printed p56, prohibits mixed pumping/generation in it.
        # Pairwise existing binaries impose exact aggregate mode exclusivity;
        # no new integer variable can leak into fixed-integer SCED/LMP.
        for t in ts:
            for u in z['names']:
                for v in z['names']:
                    if u!=v:b.physical.add(b.gen_mode[s,u,t]+b.pump_mode[s,v,t]<=1)
        for u in z['names']:
            initial=inventory[s]['modes'][u];state=d['initial_units'][u]
            residual=max(0,duration_steps(p,initial)-state['mode_age_steps'])
            for j in ts[:residual]:
                b.gen_mode[s,u,j].fix(int(initial=='gen'));b.pump_mode[s,u,j].fix(int(initial=='pump'))
            for t in ts:
                b.physical.add(b.gen[s,u,t]>=p['unit_gen_min']*b.gen_mode[s,u,t]);b.physical.add(b.gen[s,u,t]<=p['unit_gen_max']*b.gen_mode[s,u,t])
                b.physical.add(b.pump[s,u,t]==p['unit_pump']*b.pump_mode[s,u,t]);b.physical.add(b.gen_mode[s,u,t]+b.pump_mode[s,u,t]<=1)
                for kind,opposite in (('gen','pump'),('pump','gen')):
                    mode=getattr(b,kind+'_mode');start=getattr(b,kind+'_start');prev=int(initial==kind) if t==0 else mode[s,u,t-1]
                    b.starts.add(start[s,u,t]>=mode[s,u,t]-prev);b.starts.add(start[s,u,t]<=mode[s,u,t]);b.starts.add(start[s,u,t]<=1-prev)
                    for j in range(t,min(len(ts),t+duration_steps(p,kind))):
                        b.physical.add(mode[s,u,j]>=start[s,u,t])
                    # A shutdown of either active mode requires a genuine off interval.
                    for j in range(t,min(len(ts),t+duration_steps(p,'off'))):
                        b.physical.add(b.gen_mode[s,u,j]+b.pump_mode[s,u,j]<=1-(prev-mode[s,u,t]))
                    for j in range(max(0,t-p['switch_gap']),t):b.physical.add(mode[s,u,t]+getattr(b,opposite+'_mode')[s,u,j]<=1)
                    if state['last_active_mode']==opposite and t<state['opposite_mode_block_steps']:mode[s,u,t].fix(0)
            for kind in ('gen','pump'):b.starts.add(sum(getattr(b,kind+'_start')[s,u,t] for t in ts)+state[kind+'_starts_used']<=p['max_'+kind+'_starts'])
        gen_step=finite(p.get('gen_up_step_mw',min(2,p['n_units'])*p['unit_gen_max']))
        pump_step=finite(p.get('pump_release_step_mw',p['unit_pump']))
        require(gen_step>0 and pump_step>0,'positive optimization-unit ramp steps required')
        for t in ts:
            old_gen=sum(d['initial_units'][u]['gen_mw'] for u in z['names']) if t==0 else sum(b.gen[s,u,t-1] for u in z['names'])
            old_pump=sum(d['initial_units'][u]['pump_mw'] for u in z['names']) if t==0 else sum(b.pump[s,u,t-1] for u in z['names'])
            # Guangdong p56: generation upward only; signed pumping increases
            # by at most one adjacent fixed-speed block. Opposite directions free.
            b.physical.add(sum(b.gen[s,u,t] for u in z['names'])-old_gen<=gen_step)
            b.physical.add(old_pump-sum(b.pump[s,u,t] for u in z['names'])<=pump_step)
            delta=duration_h*sum(10000/p['e_pump']*b.pump[s,u,t]-10000/p['e_gen']*b.gen[s,u,t] for u in z['names'])
            prevu=z['up'] if t==0 else b.upper[s,t-1];prevl=z['lo'] if t==0 else b.lower[s,t-1]
            b.water.add(b.upper[s,t]==prevu+delta);b.water.add(b.lower[s,t]==prevl-delta)
            if d.get('stage','RT')=='DA':
                b.band_excess[s,t].fix(0)
                if t==95:
                    b.water.add(b.upper[s,t]==d['terminal_upper_m3']);b.water.add(b.lower[s,t]==d['terminal_lower_m3'])
                continue
            for var,key in ((b.upper,'da_upper_m3'),(b.lower,'da_lower_m3')):
                b.band.add(var[s,t]-d[key][t]<=d['band_m3']+b.band_excess[s,t]);b.band.add(d[key][t]-var[s,t]<=d['band_m3']+b.band_excess[s,t])
    b.net=pyo.Expression(b.S,b.T,rule=lambda _,s,t:sum(b.gen[s,u,t]-b.pump[s,u,t] for u in data[s]['names']))
    segments=[(s,k) for s,z in data.items() for k in range(len(z['d'].get('gen_segments',[])))]
    b.SEG=pyo.Set(initialize=segments,dimen=2);b.gen_segment=pyo.Var(b.SEG,b.T,domain=pyo.NonNegativeReals);b.segment_constraints=pyo.ConstraintList()
    for s,z in data.items():
        curve=z['d'].get('gen_segments')
        if not curve:continue
        require(sum(finite(x['width_mw']) for x in curve)==z['p']['unit_gen_max']*z['p']['n_units'],'segment widths must cover station')
        require(all(x['width_mw']>0 for x in curve) and all(finite(curve[k]['price_yuan_mwh'])<=finite(curve[k+1]['price_yuan_mwh']) for k in range(len(curve)-1)),'convex generation bid required')
        for t in ts:
            b.segment_constraints.add(sum(b.gen[s,u,t] for u in z['names'])==sum(b.gen_segment[s,k,t] for k in range(len(curve))))
            for k,x in enumerate(curve):b.segment_constraints.add(b.gen_segment[s,k,t]<=x['width_mw'])
    b.cost=pyo.Expression(expr=sum(duration_h*((0 if data[s]['d'].get('gen_segments') else data[s]['d']['gen_bid_yuan_mwh'])*b.gen[s,u,t]-data[s]['d']['pump_bid_yuan_mwh']*b.pump[s,u,t])+data[s]['p']['start_cost_gen']*b.gen_start[s,u,t]+data[s]['p']['start_cost_pump']*b.pump_start[s,u,t] for s,u in units for t in ts)+sum(data[s]['d']['band_penalty_yuan_m3']*b.band_excess[s,t] for s in data for t in ts)+sum(duration_h*data[s]['d']['gen_segments'][k]['price_yuan_mwh']*b.gen_segment[s,k,t] for s,k in segments for t in ts))
    for node in m.B:
        for t in ts:m.other_injection[node,t].set_value(m.other_injection[node,t].expr+sum(b.net[s,t] for s in data if data[s]['bus']==node))
    m.other_cost.set_value(m.other_cost.expr+b.cost)
    m._pumped_data=data
    return b


def executed_state(m,t):
    """Only caller-selected executed first period; never silently use horizon end."""
    require(t in m.pumped.T,'executed tick absent');b=m.pumped;out={}
    for s,z in m._pumped_data.items():
        states={}
        for u in z['names']:
            gm=pyo.value(b.gen_mode[s,u,t]);pm=pyo.value(b.pump_mode[s,u,t]);require(abs(gm-round(gm))<1e-6 and abs(pm-round(pm))<1e-6,'accepted integer physical witness required')
            mode='gen' if gm>.5 else 'pump' if pm>.5 else 'off';prev=z['d']['initial_units'][u]
            last=prev['last_active_mode'];remaining=prev['opposite_mode_block_steps']
            old_mode=z['initial_modes'][u];age=prev['mode_age_steps']
            for j in b.T:
                if j>t:break
                active='gen' if pyo.value(b.gen_mode[s,u,j])>.5 else 'pump' if pyo.value(b.pump_mode[s,u,j])>.5 else 'off'
                age=age+1 if active==old_mode else 1;old_mode=active
                if active!='off':last=active;remaining=z['p']['switch_gap']
                else:remaining=max(0,remaining-1)
            states[u]=dict(mode=mode,mode_age_steps=age,last_active_mode=last,opposite_mode_block_steps=remaining,gen_mw=pyo.value(b.gen[s,u,t]),pump_mw=pyo.value(b.pump[s,u,t]),gen_starts_used=prev['gen_starts_used']+round(sum(pyo.value(b.gen_start[s,u,j])for j in b.T if j<=t)),pump_starts_used=prev['pump_starts_used']+round(sum(pyo.value(b.pump_start[s,u,j])for j in b.T if j<=t)))
        out[s]=dict(upper_m3=pyo.value(b.upper[s,t]),lower_m3=pyo.value(b.lower[s,t]),net_power_mw=pyo.value(b.net[s,t]),band_excess_m3=pyo.value(b.band_excess[s,t]),units=states,executed_local_tick=t)
    return out
