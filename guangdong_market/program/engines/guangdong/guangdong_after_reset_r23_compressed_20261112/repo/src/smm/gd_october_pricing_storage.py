"""Independent 8x15 storage pricing, explicitly authored research references.

This is not the 24x5 dispatch component. Printed negative-power band conflicts
remain unresolved in gd_october_pricing_rules; magnitude bands below are a
separately named research interpretation. No inventory MP or free inventory SL.
"""
from __future__ import annotations
from datetime import timedelta
from math import isclose,sqrt
from zoneinfo import ZoneInfo
import pyomo.environ as pyo

from . import gd_october_rt_contract as c
from .gd_october_pricing_inputs import header,obj,require,num,vector,boolean
from .gd_october_pricing_rules import translated_reservoir_band

SCHEMA='gd.october.pricing_storage.v1'
SIDES=('upper_plus','upper_minus','lower_plus','lower_minus')
POLICIES=dict(reference_origin='authored_research_reference',
    power_band='signed_magnitude_relative_band_research',
    state_mapping='constant_three_steps_and_stable_full_window',
    inventory_mapping='corresponding_endpoints_same_quarter_constant_physics',
    pumping='fixed_selected_reference_research',
    bids='zero_origin_magnitude_convex_signed_cost',
    efficiency='explicit_symmetric_sqrt_roundtrip',
    reserves='current_signed_injection_zero_incremental',
    agc='explicit_zero_only',
    boundary='reference_initial_and_explicit_rolling_terminal',
    cycle_budget='inherited_dated_internal_throughput',
    reservoirs='closed_pair_zero_external_flow',
    plant_operation='no_simultaneous_pump_generate_research_printed_equation_unresolved',
    inventory_penalty='none_fixed_slack_parameters_only')
SERVICES=('positive_incremental','negative_incremental','primary','accident')


def _header(raw,fields,schema,identity,cutoff,label,native_reference=None):
    header(raw,set(fields)|{'origin'},schema,identity,cutoff,label)
    if native_reference is None:
        require(raw['origin']=='authored_research_reference',label+': only authored research reference accepted; native-selected bridge unavailable')
    else:
        from .gd_october_pricing_native_bridge import is_issued_native_reference, ORIGIN
        require(is_issued_native_reference(native_reference) and raw['origin']==ORIGIN,label+': live native bridge capability required')


def _ids(value,expected,label):
    require(type(value) is dict and set(value)==set(expected),label+': exact resource coverage required')


def _same(a,b,label):
    require(isclose(a,b,rel_tol=1e-10,abs_tol=1e-8),label+': reference arithmetic or boundary mismatch')


def _point_bounds(rows,start,fields,label):
    require(type(rows) is list and len(rows)==9,label+': nine inventory boundary points required')
    for i,r in enumerate(rows):
        obj(r,{'at',*fields},label)
        require(c._time(r['at'],label)==start+timedelta(minutes=15*i),label+': exact boundary time required')
        lo,hi=(num(r[k],label,0) for k in fields)
        require(lo<=hi,label+': reversed hard bound')


def _terminal(raw,end,unit,label):
    obj(raw,{'at','lower_'+unit,'upper_'+unit},label)
    require(c._time(raw['at'],label)==end,label+': explicit rolling terminal timestamp required')
    lo,hi=(num(raw[k],label,0) for k in ('lower_'+unit,'upper_'+unit))
    require(lo<=hi,label+': reversed terminal interval')


def _bids(bids,capacity,withdrawal,label):
    require(type(bids) is list,label+': explicit bid array required')
    require(len(bids)<=10,label+': at most ten declared bid segments allowed')
    require(bool(bids) or capacity==0,label+': missing bids for physical capacity')
    slopes=[];total=0.
    for b in bids:
        obj(b,{'width_mw','yuan_per_mwh'},label)
        width=num(b['width_mw'],label,0);require(width>0,label+': positive segment width required')
        price=num(b['yuan_per_mwh'],label);total+=width
        slopes.append(-price if withdrawal else price)
    require(slopes==sorted(slopes),label+': nonconvex signed objective slopes; no sorting allowed')
    require(total>=capacity,label+': bid widths do not cover physical maximum')


def signed_magnitude_band(*,reference_mw,mode,physical_min_mw,physical_max_mw,delta,pricing_eligible,fixed_pumping=False):
    """Named RESEARCH band; does not alter the literal-rule helper."""
    ref=num(reference_mw,'signed reference');lo=num(physical_min_mw,'physical minimum',0);hi=num(physical_max_mw,'physical maximum',0)
    delta=num(delta,'delta',0);boolean(pricing_eligible,'pricing eligibility');boolean(fixed_pumping,'fixed pumping')
    require(delta<=1 and lo<=hi,'magnitude band parameters invalid')
    require(mode in {'charge','discharge','idle','pump','generate','off'},'unknown storage mode')
    negative=mode in {'charge','pump'}
    require((ref<=0 if negative else ref>=0),'reference sign conflicts with mode')
    if mode in {'idle','off'}:
        require(ref==0,'inactive storage reference must be zero');return (0.,0.)
    mag=abs(ref);require(lo<=mag<=hi,'reference outside physical magnitude interval')
    if not pricing_eligible or (mode=='pump' and fixed_pumping):a=b=mag
    else:a,b=max(lo,(1-delta)*mag),min(hi,(1+delta)*mag)
    require(a<=b,'empty research magnitude band')
    return (-b,-a) if negative else (a,b)


def validate_storage_pricing(raw, *, source, identity, cutoff, start, nodes, inventory, delta, reference_stage, native_reference=None):
    """Strict V2 storage schema and source arithmetic, without a solver."""
    fields={'choices','reference_source','state_source','boundary_source','slack_source','batteries','pumped_units','pumped_plants'}
    _header(raw,fields,'gd.october.pricing_storage.v2' if native_reference is not None else SCHEMA,identity,cutoff,'storage',native_reference)
    obj(raw['choices'],set(POLICIES)|{'power_mapping','slack_mapping'},'storage choices')
    for k,v in POLICIES.items():require(raw['choices'][k]==('native_selected_sced_research' if k=='reference_origin' and native_reference is not None else v),'unknown/unavailable storage choice: '+k)
    require(raw['choices']['power_mapping'] in {'direct_eight_quarters','energy_mean_24x5'},'unknown storage power mapping')
    require(raw['choices']['slack_mapping'] in {'direct_quarter_fixed_parameters','constant_24x5'},'unknown four-SL mapping')
    batteries,units,plants=(raw[k] for k in ('batteries','pumped_units','pumped_plants'))
    _ids(batteries,inventory['battery'],'batteries');_ids(units,inventory['pumped'],'pumped units');_ids(plants,inventory['pumped_plants'],'pumped plants')
    require(bool(batteries or units),'V2 requires actual storage inventory; use V1 for no storage')
    resource_ids=set(batteries)|set(units)
    require(len(resource_ids)==len(batteries)+len(units),'duplicate storage resource identity')
    owners={}
    for pid,p in plants.items():
        obj(p,{'unit_ids','upper_bounds','lower_bounds','target_rows','external_upper_m3_h','external_lower_m3_h'},'plant')
        c._resources(p['unit_ids'],'plant ownership');require(bool(p['unit_ids']),'empty pumped plant is unsupported')
        for uid in p['unit_ids']:
            require(uid in units and uid not in owners,'unknown/multiply-owned pumped unit');owners[uid]=pid
        for key in ('upper_bounds','lower_bounds'):_point_bounds(p[key],start,('minimum_m3','maximum_m3'),key)
        for key in ('external_upper_m3_h','external_lower_m3_h'):
            require(not any(vector(p[key],key)), 'nonzero external water flow is unsupported')
        require(type(p['target_rows']) is list and len(p['target_rows'])==8,'eight reservoir targets required')
        for t,r in enumerate(p['target_rows']):
            obj(r,{'at','upper_target_m3','lower_target_m3','upper_width_m3','lower_width_m3'},'reservoir target')
            require(c._time(r['at'],'target')==start+timedelta(minutes=15*(t+1)),'target must name quarter endpoint')
            for k in ('upper_target_m3','lower_target_m3','upper_width_m3','lower_width_m3'):num(r[k],k,0)
    require(set(owners)==set(units),'every pumped unit needs exactly one plant')
    for family,items in (('battery',batteries),('pumped',units)):
        for uid,d in items.items():
            fields={'node','rows','services'}|({'rated_energy_mwh','roundtrip_efficiency','eta_charge','eta_discharge','energy_bounds'} if family=='battery' else {'plant_id'})
            obj(d,fields,family+'.'+uid);require(d['node'] in nodes,'unknown storage node')
            obj(d['services'],SERVICES,'storage services')
            require(all(boolean(v,'service eligibility') is False for v in d['services'].values()),'incremental storage reserve service unsupported; cannot discard eligibility')
            if family=='battery':
                rated=num(d['rated_energy_mwh'],'battery rating',0);require(rated>0,'positive battery energy rating required')
                roundtrip=num(d['roundtrip_efficiency'],'roundtrip efficiency');require(0<roundtrip<=1,'roundtrip efficiency must be in (0,1]')
                for key in ('eta_charge','eta_discharge'):
                    require(0<num(d[key],key)<=1,'one-way efficiency must be in (0,1]')
                    _same(d[key],sqrt(roundtrip),'symmetric sqrt roundtrip efficiency')
                _point_bounds(d['energy_bounds'],start,('minimum_mwh','maximum_mwh'),'battery energy')
                require(all(r['maximum_mwh']<=rated for r in d['energy_bounds']),'energy bound exceeds rating')
            else:require(d['plant_id']==owners[uid],'unit/plant ownership disagreement')
            require(type(d['rows']) is list and len(d['rows'])==8,'eight storage physical/bid rows required')
            for t,r in enumerate(d['rows']):
                fields={'charge_min_mw','charge_max_mw','discharge_min_mw','discharge_max_mw','charge_bids','discharge_bids',
                    'pricing_eligible','agc_up_mw','agc_down_mw','ramp_up_mw_h','ramp_down_mw_h'}
                if family=='pumped':fields|={'pump_m3_per_mwh','generate_m3_per_mwh'}
                obj(r,fields,'storage row');boolean(r['pricing_eligible'],'pricing eligibility')
                for key in ('charge_min_mw','charge_max_mw','discharge_min_mw','discharge_max_mw','agc_up_mw','agc_down_mw','ramp_up_mw_h','ramp_down_mw_h'):num(r[key],key,0)
                require(r['agc_up_mw']==r['agc_down_mw']==0,'nonzero storage AGC requires unsupported boundary mapping')
                for direction in ('charge','discharge'):
                    require(r[direction+'_min_mw']<=r[direction+'_max_mw'],'storage physical bounds reversed')
                    _bids(r[direction+'_bids'],r[direction+'_max_mw'],direction=='charge',direction+' bids')
                if family=='pumped':
                    for key in ('pump_m3_per_mwh','generate_m3_per_mwh'):require(num(r[key],key)>0,'positive water conversion required')
    state=source(raw['state_source'],'storage states')
    _header(state,{'stage','initial_at','initial','rows'},'gd.october.pricing_storage_states.v1',identity,cutoff,'storage states',native_reference)
    require(state['stage'] in {'DA','RT'},'independent storage state stage required')
    require(c._time(state['initial_at'],'state initial')==start,'state initial must be rolling boundary')
    _ids(state['initial'],resource_ids,'initial storage states')
    modes={}
    for uid,r in state['initial'].items():
        obj(r,{'mode','mode_elapsed_h'},'initial mode');num(r['mode_elapsed_h'],'mode age',0)
        require(r['mode'] in ({'charge','discharge','idle'} if uid in batteries else {'pump','generate','off'}),'invalid initial storage mode')
        modes[uid]=r['mode']
    require(type(state['rows']) is list and len(state['rows'])==24,'exact 24 selected storage states required')
    for i,r in enumerate(state['rows']):
        obj(r,{'start','end','modes','transition_active'},'state row')
        a,b=c._grid(start,5,24)[i];require(c._time(r['start'],'state')==a and c._time(r['end'],'state')==b,'state grid mismatch')
        _ids(r['modes'],resource_ids,'state modes');_ids(r['transition_active'],resource_ids,'state transitions')
        require(r['modes']==modes,'mixed quarter or storage mode transition unsupported in stable slice')
        require(all(boolean(x,'transition') is False for x in r['transition_active'].values()),'active storage transition unsupported')
    for pid,p in plants.items():
        live={modes[u] for u in p['unit_ids']}-{'off'}
        require(live!={'pump','generate'},'simultaneous plant pumping/generation requires unsupported formulation')
    boundary=source(raw['boundary_source'],'storage boundaries')
    _header(boundary,{'batteries','pumped_plants','initial_power'},'gd.october.pricing_storage_boundary.v1',identity,cutoff,'storage boundary',native_reference)
    _ids(boundary['batteries'],batteries,'battery boundaries');_ids(boundary['pumped_plants'],plants,'plant boundaries');_ids(boundary['initial_power'],resource_ids,'initial signed power')
    end=start+timedelta(hours=2)
    for uid,initial in boundary['initial_power'].items():
        obj(initial,{'at','mw'},'initial power');require(c._time(initial['at'],'initial power')==start,'initial power timestamp mismatch')
        p=num(initial['mw'],'initial signed power');d=(batteries if uid in batteries else units)[uid];r=d['rows'][0]
        direction='charge' if modes[uid] in {'charge','pump'} else 'discharge'
        signed_magnitude_band(reference_mw=p,mode=modes[uid],physical_min_mw=r[direction+'_min_mw'],physical_max_mw=r[direction+'_max_mw'],delta=delta,pricing_eligible=False)
    days=tuple(dict.fromkeys((start+timedelta(minutes=15*t)).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for t in range(8)))
    for uid,b in boundary['batteries'].items():
        obj(b,{'initial','terminal','cycle_budgets'},'battery boundary');obj(b['initial'],{'at','energy_mwh'},'battery initial')
        require(c._time(b['initial']['at'],'battery initial')==start,'battery initial timestamp mismatch')
        value=num(b['initial']['energy_mwh'],'initial energy',0);hard=batteries[uid]['energy_bounds'][0]
        require(hard['minimum_mwh']<=value<=hard['maximum_mwh'],'initial battery outside hard bound')
        _terminal(b['terminal'],end,'mwh','battery terminal')
        _ids(b['cycle_budgets'],days,'dated cycle budgets')
        for day,budget in b['cycle_budgets'].items():
            obj(budget,{'used_as_of','limit_cycles','used_internal_throughput_mwh'},'cycle budget')
            require(c._time(budget['used_as_of'],'used throughput')==start,'cycle carry must name rolling boundary')
            limit=num(budget['limit_cycles'],'cycle limit',0);used=num(budget['used_internal_throughput_mwh'],'used throughput',0)
            require(used<=2*batteries[uid]['rated_energy_mwh']*limit,'already exhausted cycle budget')
            require(day==days[0] or used==0,'future-day executed throughput cannot be inherited')
    for pid,p in boundary['pumped_plants'].items():
        obj(p,{'initial','upper_terminal','lower_terminal'},'plant boundary');obj(p['initial'],{'at','upper_m3','lower_m3'},'plant initial')
        require(c._time(p['initial']['at'],'plant initial')==start,'reservoir initial timestamp mismatch')
        for side in ('upper','lower'):
            value=num(p['initial'][side+'_m3'],'initial volume',0);hard=plants[pid][side+'_bounds'][0]
            require(hard['minimum_m3']<=value<=hard['maximum_m3'],'initial reservoir outside hard bound')
            _terminal(p[side+'_terminal'],end,'m3',side+' terminal')
    reference=source(raw['reference_source'],'storage reference')
    _header(reference,{'stage','grid','initial_inventory','rows'},'gd.october.pricing_storage_reference.v1',identity,cutoff,'storage reference',native_reference)
    require(reference['stage']==reference_stage,'storage reference DA/RT stage differs from explicit pricing selection')
    require(raw['reference_source']['source_id']!=raw['state_source']['source_id'],'storage reference and state sources must be independent')
    count,minutes=(8,15) if raw['choices']['power_mapping']=='direct_eight_quarters' else (24,5)
    require(reference['grid']==('eight_quarters' if count==8 else 'twenty_four_five_minute'),'reference grid/mapping choice mismatch')
    obj(reference['initial_inventory'],{'at','battery_energy_mwh','upper_m3','lower_m3'},'reference initial')
    initial=reference['initial_inventory'];require(c._time(initial['at'],'reference initial')==start,'reference initial timestamp mismatch')
    for key,ids in (('battery_energy_mwh',batteries),('upper_m3',plants),('lower_m3',plants)):_ids(initial[key],ids,'reference '+key)
    energy={uid:num(initial['battery_energy_mwh'][uid],'reference initial energy',0) for uid in batteries}
    upper={pid:num(initial['upper_m3'][pid],'reference initial volume',0) for pid in plants};lower={pid:num(initial['lower_m3'][pid],'reference initial volume',0) for pid in plants}
    for uid in batteries:_same(energy[uid],boundary['batteries'][uid]['initial']['energy_mwh'],'battery reference initial')
    for pid in plants:
        _same(upper[pid],boundary['pumped_plants'][pid]['initial']['upper_m3'],'upper reference initial');_same(lower[pid],boundary['pumped_plants'][pid]['initial']['lower_m3'],'lower reference initial')
    require(type(reference['rows']) is list and len(reference['rows'])==count,'exact storage reference grid required')
    values={uid:[] for uid in resource_ids};endpoints=[]
    for i,r in enumerate(reference['rows']):
        obj(r,{'start','end','power_mw','battery_energy_end_mwh','upper_end_m3','lower_end_m3'},'reference row')
        a,b=c._grid(start,minutes,count)[i];require(c._time(r['start'],'reference')==a and c._time(r['end'],'reference')==b,'reference time coverage mismatch')
        for key,ids in (('power_mw',resource_ids),('battery_energy_end_mwh',batteries),('upper_end_m3',plants),('lower_end_m3',plants)):_ids(r[key],ids,'reference '+key)
        t=i if count==8 else i//3;dt=minutes/60
        for uid in resource_ids:
            p=num(r['power_mw'][uid],'reference power');values[uid].append(p);d=(batteries if uid in batteries else units)[uid];q=d['rows'][t]
            direction='charge' if modes[uid] in {'charge','pump'} else 'discharge'
            signed_magnitude_band(reference_mw=p,mode=modes[uid],physical_min_mw=q[direction+'_min_mw'],physical_max_mw=q[direction+'_max_mw'],delta=delta,pricing_eligible=False)
            if uid in batteries:
                energy[uid]+=dt*(d['eta_charge']*max(-p,0)-max(p,0)/d['eta_discharge'])
                _same(energy[uid],num(r['battery_energy_end_mwh'][uid],'reference energy',0),'battery reference recurrence')
            else:
                transfer=dt*(q['pump_m3_per_mwh']*max(-p,0)-q['generate_m3_per_mwh']*max(p,0));pid=owners[uid]
                upper[pid]+=transfer;lower[pid]-=transfer
        for pid in plants:
            _same(upper[pid],num(r['upper_end_m3'][pid],'upper reference',0),'upper reference recurrence');_same(lower[pid],num(r['lower_end_m3'][pid],'lower reference',0),'lower reference recurrence')
        if count==8 or i%3==2:
            for uid in batteries:
                hard=batteries[uid]['energy_bounds'][t+1];require(hard['minimum_mwh']<=energy[uid]<=hard['maximum_mwh'],'reference energy endpoint outside hard bounds')
            for pid in plants:
                for side,vals in (('upper',upper),('lower',lower)):
                    hard=plants[pid][side+'_bounds'][t+1];require(hard['minimum_m3']<=vals[pid]<=hard['maximum_m3'],'reference reservoir endpoint outside hard bounds')
            endpoints.append(dict(battery_energy_mwh=dict(energy),upper_m3=dict(upper),lower_m3=dict(lower)))
    mapped={uid:vs if count==8 else [sum(vs[i:i+3])/3 for i in range(0,24,3)] for uid,vs in values.items()}
    signed_bounds={}
    for uid in resource_ids:
        d=(batteries if uid in batteries else units)[uid];mode=modes[uid];direction='charge' if mode in {'charge','pump'} else 'discharge';signed_bounds[uid]=[]
        for t,q in enumerate(d['rows']):
            signed_bounds[uid].append(signed_magnitude_band(reference_mw=mapped[uid][t],mode=mode,physical_min_mw=q[direction+'_min_mw'],physical_max_mw=q[direction+'_max_mw'],delta=delta,pricing_eligible=q['pricing_eligible'],fixed_pumping=uid in units))
    fixed_sl={pid:{side:[] for side in SIDES} for pid in plants}
    if not plants:require(raw['slack_source'] is None,'absent pumped plants require explicit null SL source')
    else:
        sl=source(raw['slack_source'],'four fixed research SL')
        _header(sl,{'stage','grid','rows','meaning'},'gd.october.pricing_storage_slacks.v1',identity,cutoff,'four fixed research SL',native_reference)
        require(sl['stage']=='RT' and sl['meaning']==('live_native_selected_ED_slacks' if native_reference is not None else 'authored_research_slacks_not_solved_ed'),'four SL must explicitly remain authored RT research, not solved ED')
        count_sl,minutes_sl=(8,15) if raw['choices']['slack_mapping']=='direct_quarter_fixed_parameters' else (24,5)
        require(sl['grid']==('eight_quarters' if count_sl==8 else 'twenty_four_five_minute'),'slack grid/mapping mismatch')
        require(type(sl['rows']) is list and len(sl['rows'])==count_sl,'four SL exact coverage required')
        for i,r in enumerate(sl['rows']):
            obj(r,{'at','values'},'four SL row');require(c._time(r['at'],'SL endpoint')==start+timedelta(minutes=minutes_sl*(i+1)),'SL endpoint mismatch');_ids(r['values'],plants,'four SL plants')
            for pid,v in r['values'].items():
                obj(v,SIDES,'four SL sides')
                for side,x in v.items():num(x,'fixed SL',0)
            if count_sl==24 and i%3:
                require(r['values']==sl['rows'][i-i%3]['values'],'changing four SL within quarter: no averaging or endpoint substitution')
            if count_sl==8 or i%3==2:
                for pid in plants:
                    for side in SIDES:fixed_sl[pid][side].append(r['values'][pid][side])
    return dict(raw=raw,modes=modes,reference_mw=mapped,reference_endpoints=endpoints,signed_bounds=signed_bounds,
        boundary=boundary,fixed_slacks=fixed_sl,owners=owners,cycle_days=list(days),
        source_provenance=dict(reference='native_selected_sced_research' if native_reference is not None else 'authored_research_reference',slacks=('live_native_selected_ED_slacks' if native_reference is not None else 'authored_research_slacks_not_solved_ed') if plants else 'explicitly_absent',
            native_selected_reference_available=native_reference is not None,reference_stage=reference_stage,state_stage=state['stage'],official_mapping_verified=False))


def attach_pricing_storage(model,data):
    """Attach new quarter equations, returning single-count injection/cost ports."""
    require(tuple(model.T)==tuple(range(8)),'storage pricing requires exactly eight quarters')
    require(not hasattr(model,'pricing_storage'),'duplicate pricing storage attachment')
    raw=data['raw'];batteries=raw['batteries'];units=raw['pumped_units'];plants=raw['pumped_plants'];resources={**batteries,**units};boundary=data['boundary']
    model.pricing_storage=s=pyo.Block();s.R=pyo.Set(initialize=tuple(resources),ordered=True);s.BAT=pyo.Set(initialize=tuple(batteries),ordered=True);s.U=pyo.Set(initialize=tuple(units),ordered=True);s.PS=pyo.Set(initialize=tuple(plants),ordered=True);s.BOUNDARY=pyo.RangeSet(0,8)
    s.net=pyo.Var(s.R,model.T,bounds=lambda _,u,t:tuple(data['signed_bounds'][u][t]))
    s.charge=pyo.Expression(s.R,model.T,rule=lambda _,u,t:-s.net[u,t] if data['modes'][u] in {'charge','pump'} else 0.)
    s.discharge=pyo.Expression(s.R,model.T,rule=lambda _,u,t:s.net[u,t] if data['modes'][u] in {'discharge','generate'} else 0.)
    s.K=pyo.Set(dimen=4,ordered=True,initialize=[(u,t,direction,k) for u in resources for t in model.T for direction in ('charge','discharge') for k in range(len(resources[u]['rows'][t][direction+'_bids']))])
    s.segment=pyo.Var(s.K,domain=pyo.NonNegativeReals,bounds=lambda _,u,t,d,k:(0,resources[u]['rows'][t][d+'_bids'][k]['width_mw']))
    def segment_rule(_,u,t,d):
        expression=sum(s.segment[u,t,d,k] for k in range(len(resources[u]['rows'][t][d+'_bids'])))
        return expression+0*s.net[u,t]==(s.charge[u,t] if d=='charge' else s.discharge[u,t])
    s.D=pyo.Set(initialize=('charge','discharge'));s.c_segments=pyo.Constraint(s.R,model.T,s.D,rule=segment_rule)
    s.c_ramp_up=pyo.Constraint(s.R,model.T,rule=lambda _,u,t:s.net[u,t]-(boundary['initial_power'][u]['mw'] if t==0 else s.net[u,t-1])<=.25*resources[u]['rows'][t]['ramp_up_mw_h'])
    s.c_ramp_down=pyo.Constraint(s.R,model.T,rule=lambda _,u,t:(boundary['initial_power'][u]['mw'] if t==0 else s.net[u,t-1])-s.net[u,t]<=.25*resources[u]['rows'][t]['ramp_down_mw_h'])
    s.energy=pyo.Var(s.BAT,s.BOUNDARY,bounds=lambda _,u,k:(batteries[u]['energy_bounds'][k]['minimum_mwh'],batteries[u]['energy_bounds'][k]['maximum_mwh']))
    s.c_energy_initial=pyo.Constraint(s.BAT,rule=lambda _,u:s.energy[u,0]==boundary['batteries'][u]['initial']['energy_mwh'])
    s.c_energy=pyo.Constraint(s.BAT,model.T,rule=lambda _,u,t:s.energy[u,t+1]==s.energy[u,t]+.25*(batteries[u]['eta_charge']*s.charge[u,t]-s.discharge[u,t]/batteries[u]['eta_discharge']))
    s.c_energy_terminal=pyo.Constraint(s.BAT,rule=lambda _,u:(boundary['batteries'][u]['terminal']['lower_mwh'],s.energy[u,8],boundary['batteries'][u]['terminal']['upper_mwh']))
    start=c._time(data['boundary']['initial_power'][next(iter(resources))]['at'],'start')
    day_of={t:(start+timedelta(minutes=15*t)).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for t in model.T}
    s.CYCLE=pyo.Set(dimen=2,initialize=[(u,day) for u in batteries for day in data['cycle_days']])
    s.c_cycle=pyo.Constraint(s.CYCLE,rule=lambda _,u,day:boundary['batteries'][u]['cycle_budgets'][day]['used_internal_throughput_mwh']+sum(.25*(batteries[u]['eta_charge']*s.charge[u,t]+s.discharge[u,t]/batteries[u]['eta_discharge']) for t in model.T if day_of[t]==day)<=2*batteries[u]['rated_energy_mwh']*boundary['batteries'][u]['cycle_budgets'][day]['limit_cycles'])
    s.upper=pyo.Var(s.PS,s.BOUNDARY,bounds=lambda _,p,k:(plants[p]['upper_bounds'][k]['minimum_m3'],plants[p]['upper_bounds'][k]['maximum_m3']))
    s.lower=pyo.Var(s.PS,s.BOUNDARY,bounds=lambda _,p,k:(plants[p]['lower_bounds'][k]['minimum_m3'],plants[p]['lower_bounds'][k]['maximum_m3']))
    s.c_upper_initial=pyo.Constraint(s.PS,rule=lambda _,p:s.upper[p,0]==boundary['pumped_plants'][p]['initial']['upper_m3'])
    s.c_lower_initial=pyo.Constraint(s.PS,rule=lambda _,p:s.lower[p,0]==boundary['pumped_plants'][p]['initial']['lower_m3'])
    s.transfer=pyo.Expression(s.PS,model.T,rule=lambda _,p,t:.25*sum(units[u]['rows'][t]['pump_m3_per_mwh']*s.charge[u,t]-units[u]['rows'][t]['generate_m3_per_mwh']*s.discharge[u,t] for u in plants[p]['unit_ids']))
    s.c_upper=pyo.Constraint(s.PS,model.T,rule=lambda _,p,t:s.upper[p,t+1]==s.upper[p,t]+s.transfer[p,t])
    s.c_lower=pyo.Constraint(s.PS,model.T,rule=lambda _,p,t:s.lower[p,t+1]==s.lower[p,t]-s.transfer[p,t])
    s.c_upper_terminal=pyo.Constraint(s.PS,rule=lambda _,p:(boundary['pumped_plants'][p]['upper_terminal']['lower_m3'],s.upper[p,8],boundary['pumped_plants'][p]['upper_terminal']['upper_m3']))
    s.c_lower_terminal=pyo.Constraint(s.PS,rule=lambda _,p:(boundary['pumped_plants'][p]['lower_terminal']['lower_m3'],s.lower[p,8],boundary['pumped_plants'][p]['lower_terminal']['upper_m3']))
    fixed=data['fixed_slacks'];s.SIDE=pyo.Set(initialize=SIDES)
    s.fixed_sl=pyo.Param(s.PS,s.SIDE,model.T,initialize=lambda _,p,side,t:fixed[p][side][t],mutable=True)
    bands={}
    for p,d in plants.items():
        for t,r in enumerate(d['target_rows']):
            for side in ('upper','lower'):
                bands[p,t,side]=translated_reservoir_band(target_m3=r[side+'_target_m3'],width_m3=r[side+'_width_m3'],positive_slack_m3=fixed[p][side+'_plus'][t],negative_slack_m3=fixed[p][side+'_minus'][t],source='live native selected ED fixed SL' if data['source_provenance']['native_selected_reference_available'] else 'authored fixed research SL; not solved ED')
    # Fixed Params are actually referenced by both translated row bounds.
    s.c_upper_band=pyo.Constraint(s.PS,model.T,rule=lambda _,p,t:(plants[p]['target_rows'][t]['upper_target_m3']-plants[p]['target_rows'][t]['upper_width_m3']+s.fixed_sl[p,'upper_plus',t]-s.fixed_sl[p,'upper_minus',t],s.upper[p,t+1],plants[p]['target_rows'][t]['upper_target_m3']+plants[p]['target_rows'][t]['upper_width_m3']+s.fixed_sl[p,'upper_plus',t]-s.fixed_sl[p,'upper_minus',t]))
    s.c_lower_band=pyo.Constraint(s.PS,model.T,rule=lambda _,p,t:(plants[p]['target_rows'][t]['lower_target_m3']-plants[p]['target_rows'][t]['lower_width_m3']+s.fixed_sl[p,'lower_plus',t]-s.fixed_sl[p,'lower_minus',t],s.lower[p,t+1],plants[p]['target_rows'][t]['lower_target_m3']+plants[p]['target_rows'][t]['lower_width_m3']+s.fixed_sl[p,'lower_plus',t]-s.fixed_sl[p,'lower_minus',t]))
    s.energy_bid_cost=pyo.Expression(expr=sum(.25*(1 if direction=='discharge' else -1)*resources[u]['rows'][t][direction+'_bids'][k]['yuan_per_mwh']*s.segment[u,t,direction,k] for u,t,direction,k in s.K))
    s.injection=pyo.Expression(model.N,model.T,rule=lambda _,n,t:sum(s.net[u,t] for u,d in resources.items() if d['node']==n))
    s.absolute_baseline=pyo.Expression(model.T,rule=lambda _,t:sum(s.net[u,t] for u in resources))
    audit=dict(schema_version=raw['schema_version'],source_provenance=data['source_provenance'],resource_ids=list(resources),plant_ids=list(plants),owners=data['owners'],modes=data['modes'],
        signed_power_bounds=data['signed_bounds'],inventory_slacks='fixed_parameters_not_optimized',inventory_penalty_created=False,
        reserve_basis='absolute_max_equals_min_equals_current_signed_injection_zero_incremental',native_selected_reference_available=data['source_provenance']['native_selected_reference_available'],
        translated_bands=[dict(plant=p,quarter=t,reservoir=side,lower_m3=b['lower_m3'],upper_m3=b['upper_m3']) for (p,t,side),b in bands.items()],
        cycle_days=data['cycle_days'],state_transitions_supported=False,official_price=None)
    return dict(injection_mw={(n,t):s.injection[n,t] for n in model.N for t in model.T},energy_cost=s.energy_bid_cost,
        positive_capacity_mw={t:s.absolute_baseline[t] for t in model.T},negative_minimum_mw={t:s.absolute_baseline[t] for t in model.T},
        primary_mw={t:0. for t in model.T},accident_mw={t:0. for t in model.T},audit=audit)
