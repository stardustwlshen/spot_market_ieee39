"""R13 native, source-bound causal research RT, separate from October guards.

Twenty-four five-minute cells are optimized at T-15. Only the first three
are issued. Receipts are explicitly synthetic zero-tracking-error simulation,
never telemetry. Mutable public objects cannot replace live native identities.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from weakref import WeakKeyDictionary
import pyomo.environ as pyo
from .gd_energy_core import build_energy_core, assert_linear_energy_model, EnergyInputError
from .gd_horizon import EnergyHorizon, EnergyPeriod
from .gd_da_battery_candidate import (DT, SHANGHAI, digest, require, stamp,
    primal_check, _native_metrics, _native_solver_identity, _attach_batteries)

_BUILT=WeakKeyDictionary()
_ACCEPTED=WeakKeyDictionary()
_CHAINS=WeakKeyDictionary()
_FAILURES=WeakKeyDictionary()


@dataclass(eq=False)
class SimulatedChain:
    """Inspectable simulated history; every mutation is checked against a seal."""
    history: dict


def _plan_sources(plan,permit):
    # V4 explicit historical-witness capability. This does not restore any
    # native acceptance registry and cannot impersonate the old live permit.
    from .gd_r13_checkpoint_resume import ArchivedResearchPlanPermit,archived_plan_sources
    if type(permit)is ArchivedResearchPlanPermit:return archived_plan_sources(plan,permit)
    from .gd_r13_da_publication import validate_live_plan,live_plan_sources
    validate_live_plan(plan,permit)
    return live_plan_sources(plan,permit)


def _chain(chain):
    require(type(chain) is SimulatedChain and chain in _CHAINS,'live simulated chain required')
    require(digest(chain.history)==_CHAINS[chain]['seal'],'simulated history/receipt/command modified')
    return chain.history


def _seal_chain(chain):
    _CHAINS[chain]['seal']=digest(chain.history)


def _state_initial(bundle,report):
    p=report['problem']; initial=report['equilibrium']['thermal_power_mw']
    at=p['hypothesis']['preparation_boundary_at']
    thermal={g:dict(online=d['online'],phase='stable' if d['online'] else 'off',
        state_age_h=d['age_h'],phase_age_h=d['age_h'],power_mw=initial[g],
        curve_step=None,curve_length=None,residence_remaining_h=max(0.,(d['min_up_h'] if d['online'] else d['min_down_h'])-d['age_h'])) for g,d in p['thermal'].items()}
    batteries={u:dict(energy_mwh=r['initial_energy_mwh'],power_mw=r['initial_power_mw'],
        mode=r['initial_mode'],mode_age_h=r['initial_mode_age_h']-.25,hour_direction=0,
        calendar_hour=stamp(at).replace(minute=0,second=0,microsecond=0).isoformat(),
        daily_used_internal_mwh={stamp(at).date().isoformat():0.,**{date:q['used_internal_mwh'] for date,q in r['cycle_budget'].items()}},
        cumulative_internal_mwh=sum(q['used_internal_mwh'] for q in r['cycle_budget'].values())) for u,r in bundle['batteries'].items()}
    return dict(at=at,thermal=thermal,batteries=batteries,pumped_storage=deepcopy(bundle['contract']['storage']['pumped_storage']['inventory']))


def initialize_chain(plan,permit):
    """Source-bound23:45 receipt plus exactly3 already-issued preparation cells."""
    bundle,_=_plan_sources(plan,permit)
    report=bundle['prepared_boundary']
    require(report is not None and not report['historical_telemetry'],'independent synthetic preparation required')
    state=_state_initial(bundle,report); at=state['at']
    require(stamp(plan['published_at'])<=stamp(at),'DA publication later than seed receipt')
    cells=[]
    for row in report['cells']:
        cells.append(dict(start=row['at'],end=row['end'],thermal={g:dict(phase='stable' if d['online'] else 'off',stable=d['online'],connected=d['online'],mean_mw=row['power_mw'][g],end_mw=row['power_mw'][g],begin_mw=None,curve_step=None,curve_length=None) for g,d in report['problem']['thermal'].items()},
            batteries={u:dict(mode='idle',charge_mw=0.,discharge_mw=0.,power_mw=0.,selected_hour_direction_permission=0) for u in bundle['batteries']}))
    commands=[dict(id='R13-PREPARATION-'+str(i),kind='accepted_native_preparation',issued_at=at,available_at=at,cell=deepcopy(cell),native_source_sha256=digest(report)) for i,cell in enumerate(cells)]
    history=dict(schema='gd.research.r13.simulated_chain.v1',clock=at,
        provenance='explicit synthetic receipt and zero tracking error hypothesis; not actual observations',
        is_measurement=False,uses_future_actual=False,plan_sha256=digest(plan),
        receipts=[dict(id='R13-SEED',at=at,available_at=at,state=state,command_id=None,
            previous_state_sha256=None,kind='synthetic_preparation_equilibrium',is_measurement=False)],
        commands=commands,notices=[],accepted_windows=[],events=[])
    chain=SimulatedChain(history)
    _CHAINS[chain]=dict(seal=digest(history),bundle=deepcopy(bundle),plan=deepcopy(plan),permit=permit,day_source_sha256=digest(bundle))
    _issue_due_notices(chain,at)
    return chain


def _issue_due_notices(chain,to):
    h=_chain(chain); events=_CHAINS[chain]['plan'].get('events',[])
    ids={x['event_id'] for x in h['notices']}
    for i,e in enumerate(events):
        planned=e.get('notification_at')
        if planned is None: continue
        eid=e.get('id',e.get('event_id','event-'+str(i)))
        if eid not in ids and stamp(planned)<=stamp(to):
            require(stamp(planned)>=stamp(h['clock']),'advance notice cannot be retroactively issued')
            h['notices'].append(dict(event_id=eid,issued_at=planned,available_at=planned,
                event=deepcopy(e),kind='synthetic_issued_notice',is_actual_instruction=False))
    _seal_chain(chain)


def chain_report(chain):
    return deepcopy(_chain(chain))


def _thermal_next(old,cell,d):
    phase=cell['phase']; previous=old['phase']
    require(phase in ('stable','off','startup','shutdown'),'unknown thermal phase')
    start=phase=='startup' and cell.get('curve_step')==0
    stop=phase=='shutdown' and cell.get('curve_step')==0
    if start:
        require(previous=='off' and old['state_age_h']+1e-8>=d['min_down_h'],'startup predecessor/min-down')
    if stop:
        require(previous=='stable' and old['state_age_h']+1e-8>=d['min_up_h'],'shutdown predecessor/min-up')
    if phase in ('startup','shutdown'):
        require(abs(old['power_mw']-cell['begin_mw'])<=1e-6,'transition true endpoint continuity')
        if not (start or stop):
            require(previous==phase and old['curve_step']==cell['curve_step'],'unfinished curve phase/step carry')
        end_of_curve=cell['curve_step']+1==cell['curve_length']
        next_phase=('stable' if phase=='startup' else 'off') if end_of_curve else phase
        online=0 if end_of_curve and phase=='shutdown' else 1
        age=(DT if start else old['state_age_h']+DT)
        if end_of_curve and phase=='shutdown':age=0.
        phase_age=(DT if previous!=phase else old['phase_age_h']+DT)
        if end_of_curve:phase_age=0.
        step=None if end_of_curve else cell['curve_step']+1
        length=None if end_of_curve else cell['curve_length']
    else:
        require(previous==phase,'thermal phase changed without sourced transition')
        online=int(phase=='stable');age=old['state_age_h']+DT
        next_phase=phase;phase_age=old['phase_age_h']+DT;step=length=None
        require(abs(cell['end_mw']-old['power_mw'])<=max(d['ramp_up_mw_per_h'].values())*DT+1e-6 if cell['end_mw']>=old['power_mw'] else abs(cell['end_mw']-old['power_mw'])<=max(d['ramp_down_mw_per_h'].values())*DT+1e-6,'thermal signed endpoint ramp')
    minimum=d['min_up_h'] if online else d['min_down_h']
    return dict(online=online,phase=next_phase,state_age_h=age,phase_age_h=phase_age,
        power_mw=cell['end_mw'],curve_step=step,curve_length=length,residence_remaining_h=max(0.,minimum-age))


def integrate_cell(state,cell,bundle):
    """Independent pure executed-prefix arithmetic, no reset or clipping."""
    require(stamp(state['at'])==stamp(cell['start']) and stamp(cell['end'])-stamp(cell['start'])==timedelta(minutes=5),'state/command contiguous5minute interval required')
    require(set(state['thermal'])==set(cell['thermal'])==set(bundle['energy_inputs']['thermal']),'thermal inventory changed')
    require(set(state['batteries'])==set(cell['batteries'])==set(bundle['batteries']),'battery inventory changed')
    new=deepcopy(state);new['at']=cell['end']
    for g,d in bundle['energy_inputs']['thermal'].items():new['thermal'][g]=_thermal_next(state['thermal'][g],cell['thermal'][g],d)
    local=stamp(cell['start']).astimezone(SHANGHAI); hour=local.replace(minute=0,second=0,microsecond=0).isoformat(); date=local.date().isoformat()
    end_local=stamp(cell['end']).astimezone(SHANGHAI)
    for u,r in bundle['batteries'].items():
        old=state['batteries'][u];c=cell['batteries'][u];mode=c['mode'];eta=math.sqrt(r['roundtrip_efficiency'])
        charge,discharge=c['charge_mw'],c['discharge_mw'];power=discharge-charge
        require(mode in ('idle','charge','discharge') and min(charge,discharge)>=-1e-7,'battery mode/nonnegative magnitudes')
        require(charge<=r['charge_max_mw']+1e-7 and discharge<=r['discharge_max_mw']+1e-7,'battery rated power')
        require((abs(charge)+abs(discharge)<=1e-6 if mode=='idle' else abs(discharge)<=1e-6 if mode=='charge' else abs(charge)<=1e-6),'mode/power mismatch')
        require(abs(c['power_mw']-power)<=1e-7,'signed battery power mismatch')
        require(-r['ramp_down_mw_per_h']*DT-1e-7<=power-old['power_mw']<=r['ramp_up_mw_per_h']*DT+1e-7,'first/continued signed battery ramp')
        require(r['energy_min_mwh']-1e-7<=old['energy_mwh']<=r['energy_max_mwh']+1e-7,'initial battery energy outside hard bounds')
        energy=old['energy_mwh']+DT*(eta*charge-discharge/eta)
        require(r['energy_min_mwh']-1e-7<=energy<=r['energy_max_mwh']+1e-7,'executed battery energy outside hard bounds')
        q=old['hour_direction'] if old['calendar_hour']==hour else 0
        sign=-1 if mode=='charge' else 1 if mode=='discharge' else 0
        require(not sign or q in (0,sign),'same-hour direction reversal through idle')
        if sign:q=sign
        entered=mode!=old['mode']
        if entered:
            require(old['mode_age_h']+1e-8>=r['minimum_mode_h'][old['mode']],'inherited battery minimum mode')
            if mode!='idle':
                prep=r['preparation_h'][mode]
                require(prep==0 or (old['mode']=='idle' and old['mode_age_h']+1e-8>=prep),'battery idle preparation')
        used=deepcopy(old['daily_used_internal_mwh']);used.setdefault(date,0.)
        throughput=DT*(eta*charge+discharge/eta);used[date]+=throughput
        budget=r['cycle_budget'].get(date)
        require(budget is not None or (abs(used[date])<=1e-9 and abs(throughput)<=1e-9),'no declared operating-date cycle budget')
        if budget is not None:require(used[date]<=2*r['energy_capacity_mwh']*budget['limit_cycles']+1e-6,'daily battery throughput exhausted')
        end_hour=end_local.replace(minute=0,second=0,microsecond=0).isoformat()
        if end_hour!=hour:q=0
        if end_local.date().isoformat()!=date:used.setdefault(end_local.date().isoformat(),0.)
        new['batteries'][u]=dict(energy_mwh=energy,power_mw=power,mode=mode,
            mode_age_h=DT if entered else old['mode_age_h']+DT,hour_direction=q,
            calendar_hour=end_hour,daily_used_internal_mwh=used,
            cumulative_internal_mwh=old['cumulative_internal_mwh']+throughput)
    return new


def advance_simulated_receipts(chain,to):
    """Advance simulation only over previously issued, accepted5-minute commands."""
    h=_chain(chain); target=stamp(to); oldclock=stamp(h['clock'])
    require(target>=oldclock,'simulation clock cannot go backwards')
    bundle=_CHAINS[chain]['bundle']
    while stamp(h['receipts'][-1]['at'])<target:
        previous=h['receipts'][-1]; start=stamp(previous['at'])
        matches=[x for x in h['commands'] if stamp(x['cell']['start'])==start and stamp(x['available_at'])<=start]
        require(len(matches)==1,'missing/overlapping issued execution prefix')
        command=matches[0];require(stamp(command['cell']['end'])<=target,'receipts require exact5minute boundary')
        for g,c in command['cell']['thermal'].items():
            if c['phase']=='startup' and c.get('curve_step')==0:
                notices=[n for n in h['notices'] if n['event']['unit']==g and stamp(n['event']['effective_at'])==start and stamp(n['issued_at'])+timedelta(minutes=30)<=start]
                require(len(notices)==1,'startup execution lacks unique actually issued advance notice')
        state=integrate_cell(previous['state'],command['cell'],bundle)
        _issue_due_notices(chain,command['cell']['end']);h=_chain(chain)
        h['receipts'].append(dict(id='R13-RECEIPT-'+str(len(h['receipts'])),at=state['at'],available_at=state['at'],state=state,
            command_id=command['id'],command_sha256=digest(command),previous_state_sha256=digest(previous['state']),
            kind='synthetic_zero_tracking_error_receipt',is_measurement=False))
        h['clock']=state['at'];_seal_chain(chain)
    require(stamp(h['receipts'][-1]['at'])==target,'simulation receipt target mismatch')
    _issue_due_notices(chain,to);h=_chain(chain);h['clock']=target.isoformat();_seal_chain(chain)
    return deepcopy(h['receipts'][-1])


def project_boundary(chain,*,start,cutoff):
    h=_chain(chain); start,cutoff=stamp(start),stamp(cutoff)
    require(cutoff==start-timedelta(minutes=15),'R13 explicit T-15 cutoff')
    require(stamp(h['clock'])==cutoff,'solve must occur at current simulated cutoff; no backdating')
    receipts=[r for r in h['receipts'] if stamp(r['at'])<=cutoff and stamp(r['available_at'])<=cutoff]
    require(bool(receipts),'no cutoff-available receipt')
    receipt=max(receipts,key=lambda r:stamp(r['at']));state=deepcopy(receipt['state']);used=[]
    while stamp(state['at'])<start:
        commands=[c for c in h['commands'] if stamp(c['cell']['start'])==stamp(state['at']) and stamp(c['available_at'])<=cutoff and stamp(c['issued_at'])<=cutoff]
        require(len(commands)==1,'cutoff-valid issued commands do not cover projection')
        command=commands[0];require(stamp(command['cell']['end'])<=start,'command extends beyond projected boundary')
        state=integrate_cell(state,command['cell'],_CHAINS[chain]['bundle']);used.append(deepcopy(command))
    return dict(kind='planned_projection_under_synthetic_zero_tracking_hypothesis',is_measurement=False,
        cutoff=cutoff.isoformat(),receipt=deepcopy(receipt),commands=used,state=state,
        receipt_sha256=digest(receipt),command_sha256=[digest(c) for c in used])


def _slice_series(series,indices):
    return {t:deepcopy(series[i]) for t,i in enumerate(indices)}


def _forecast(bundle,start):
    """Fresh labeled vintage from original frozen prior only, available atcutoff."""
    from .gd_october_research_forecast import prior_value
    buses=bundle['energy_inputs']['buses'];cutoff=start-timedelta(minutes=15)
    values={f:{(b,t):0. for b in buses for t in range(24)} for f in ('load_mw','wind_mw','solar_mw','boundary_tie_mw')};sources={}
    for family in values:
        src=bundle['contract']['sources']['prior:'+family];prior=json.loads(src['artifact'])
        require(sha256(src['artifact']).hexdigest()==src['sha256'] and stamp(src['available_at'])<=cutoff,'prior hash/cutoff changed')
        require(not prior['uses_future_actual'],'future actual prior forbidden')
        knots=[dict(at=(start+timedelta(minutes=15*j)).isoformat(),values={r:prior_value(prior,r,start+timedelta(minutes=15*j)) for r in prior['resources']}) for j in range(9)]
        for t in range(24):
            j,k=divmod(t,3)
            for rid,r in prior['resources'].items():
                a,b=knots[j]['values'][rid],knots[j+1]['values'][rid]
                values[family][r['bus'],t]+=a+(b-a)*(k+.5)/3
        sources[family]=dict(schema='gd.research.r13.rt_prior_vintage.v1',kind='synthetic_research',uses_future_actual=False,
            is_measurement=False,id='R13-RT:'+start.isoformat()+':'+family,issued_at=cutoff.isoformat(),available_at=cutoff.isoformat(),
            prior_sha256=src['sha256'],prior_artifact=src['artifact'],knots=knots,
            method='frozen harmonic prior; exact linear15minute knots to5minute intervalmeans')
    return values,sources


def _thermal_schedule(bundle,plan,cells):
    from .gd_da_battery_candidate import compile_path
    selected=plan.get('selected_paths' if stamp(bundle['contract']['boundary_at'])<stamp(plan['day_start'])+timedelta(days=1) else 'tail_selected_paths')
    if selected is None:
        selected=plan.get('day_selected_paths')
    # R12 physical phase fixes suffice for stable/off; curve metadata is always
    # independently regenerated from the selected source event, never guessed.
    paths={}
    report_cells={r['start']:r for r in plan['physical_cells']}
    for g,d in bundle['energy_inputs']['thermal'].items():
        candidates=bundle['candidates'][g]
        if selected is None:
            matching=[]
            for spec in candidates:
                path=compile_path(d,spec,bundle['energy_inputs']['horizon'].ids,bundle['contract'])
                if all(path['cells'][i]['phase']==report_cells[(stamp(bundle['contract']['boundary_at'])+timedelta(minutes=5*i)).isoformat()]['thermal'][g]['phase'] for i in range(len(path['cells']))):matching.append((spec,path))
            require(len(matching)==1,'selected thermal path ambiguous/absent in published plan')
            spec,path=matching[0]
        else:
            spec=next(x for x in candidates if x['id']==selected[g]);path=compile_path(d,spec,bundle['energy_inputs']['horizon'].ids,bundle['contract'])
        paths[g]=(spec,path)
    result=[]
    for row in cells:
        i=int((stamp(row['start'])-stamp(bundle['contract']['boundary_at'])).total_seconds()/300);out={}
        for g,(spec,path) in paths.items():
            require(0<=i<len(path['cells']),'thermal source tail coverage absent')
            c=deepcopy(path['cells'][i]);c['begin_mw']=c['begin'];c['curve_step']=None;c['curve_length']=None
            for event in spec['events']:
                if event['at']<=i<event['at']+len(event['steps']):c['curve_step']=i-event['at'];c['curve_length']=len(event['steps'])
            require(c['phase']==row['thermal'][g]['phase'],'published/source thermal phase mismatch')
            out[g]=c
        result.append(out)
    return result


def build_rt_window(plan,permit,chain,start):
    bundle,tail=_plan_sources(plan,permit);h=_chain(chain); beginning=stamp(start)
    require(beginning.astimezone(SHANGHAI).minute%15==0 and beginning.second==beginning.microsecond==0,'RT quarter alignment')
    cutoff=beginning-timedelta(minutes=15)
    require(stamp(plan['published_at'])<=cutoff,'DA plan unavailable atRTcutoff')
    require(digest(plan)==h['plan_sha256'],'chain publication identity changed; explicit extension adoption required')
    projection=project_boundary(chain,start=beginning.isoformat(),cutoff=cutoff.isoformat())
    by_at={stamp(r['start']):r for r in plan['physical_cells']}
    require(all(beginning+timedelta(minutes=5*t) in by_at for t in range(24)),'published DA/tail missing complete24cell horizon')
    cells=[deepcopy(by_at[beginning+timedelta(minutes=5*t)]) for t in range(24)]
    values,vintages=_forecast(bundle,beginning)
    schedule_by_start={}
    midnight=stamp(bundle['contract']['day_start'])+timedelta(days=1)
    for source,rows in ((bundle,[r for r in cells if stamp(r['start'])<midnight]),(tail,[r for r in cells if stamp(r['start'])>=midnight])):
        if not rows:continue
        require(source is not None,'next-day tail must have accepted source')
        schedule_by_start.update({r['start']:s for r,s in zip(rows,_thermal_schedule(source,plan,rows))})
    schedule=[schedule_by_start[r['start']] for r in cells]
    energy=deepcopy(bundle['energy_inputs']);ts=tuple(range(24));indices=[]
    source_at=[]
    for row in cells:
        source=bundle if stamp(row['start'])<stamp(bundle['contract']['day_start'])+timedelta(days=1) else tail
        index=int((stamp(row['start'])-stamp(source['contract']['boundary_at'])).total_seconds()/300)
        source_at.append((source,index))
    energy['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,DT,DT,'delivery',t<3) for t in ts),'real_time_5min','R13 causal research24x5minute horizon; first3 execution candidates')
    for g,d in energy['thermal'].items():
        for key in ('base_bid_yuan_per_h','min_mw','max_mw','ramp_up_mw_per_h','ramp_down_mw_per_h','startup_output_limit_mw','shutdown_output_limit_mw','startup_yuan','shutdown_yuan','status','fixed_power_mw'):
            d[key]={t:deepcopy(s['energy_inputs']['thermal'][g][key][i]) for t,(s,i) in enumerate(source_at)}
        for k,seg in enumerate(d['segments']):seg['price_yuan_per_mwh']={t:s['energy_inputs']['thermal'][g]['segments'][k]['price_yuan_per_mwh'][i] for t,(s,i) in enumerate(source_at)}
        state=projection['state']['thermal'][g]
        d.update(initial_online=state['online'],initial_power_mw=state['power_mw'],initial_state_age_h=state['state_age_h'],initial_state_kind='research_assumption_trajectory' if state['phase'] in ('startup','shutdown') else 'stable')
    for lid,line in energy['lines'].items():
        for key in ('lower_mw','upper_mw'):line[key]={t:s['energy_inputs']['lines'][lid][key][i] for t,(s,i) in enumerate(source_at)}
    require(not energy['sections'],'R13 original no-section case required')
    energy.update(load_mw=values['load_mw'],renewable_mw={k:values['wind_mw'][k]+values['solar_mw'][k] for k in values['load_mw']},physical_tie_mw=values['boundary_tie_mw'],renewable_bid_yuan_per_mwh={(b,t):0. for b in energy['buses'] for t in ts},renewable_curtailment_penalty_yuan_per_mwh={(b,t):0. for b in energy['buses'] for t in ts})
    reserve=deepcopy(bundle['reserve'])
    for key in ('positive_mw','negative_mw','primary_mw','contingency_mw'):reserve[key]={t:s['reserve'][key][i] for t,(s,i) in enumerate(source_at)}
    batteries=deepcopy(bundle['batteries']);clock=dict(times={},dates={},hours={})
    for t,row in enumerate(cells):
        local=stamp(row['start']).astimezone(SHANGHAI);clock['times'][t]=local.isoformat();clock['dates'][t]=local.date().isoformat();clock['hours'][t]=local.replace(minute=0,second=0,microsecond=0).isoformat()
    for u,r in batteries.items():
        state=projection['state']['batteries'][u]
        r.update(initial_energy_mwh=state['energy_mwh'],initial_power_mw=state['power_mw'],initial_mode=state['mode'],initial_mode_age_h=state['mode_age_h'],initial_hour_direction=state['hour_direction'],terminal_energy_mwh=cells[-1]['batteries'][u]['energy_end_mwh'],terminal_at=cells[-1]['end'],_terminal_period=23,_eta=math.sqrt(r['roundtrip_efficiency']))
        r['availability']={t:s['batteries'][u]['availability'][i] for t,(s,i) in enumerate(source_at)}
        budgets={}
        for s,_ in source_at:
            for date,q in s['batteries'][u]['cycle_budget'].items():
                if date in budgets:require(budgets[date]['limit_cycles']==q['limit_cycles'],'cycle limit source conflict')
                budgets[date]=dict(limit_cycles=q['limit_cycles'],used_internal_mwh=state['daily_used_internal_mwh'].get(date,0.))
        r['cycle_budget']=budgets
        # All modes/readiness remain sourced and fixed. There is no repair or
        # rolling optimization of discrete choices. Current source minima=0.
        require(all(x==0 for x in r['minimum_mode_h'].values()),'nonzero battery residence needs explicit carry-out extension; do not silently truncate')
    inputs=dict(schema='gd.research.r13.causal_rt.input.v1',start=beginning.isoformat(),cutoff=cutoff.isoformat(),plan_sha256=digest(plan),projection=projection,
        energy_inputs=energy,reserve=reserve,batteries=batteries,clock=clock,thermal_schedule=schedule,da_cells=cells,vintages=vintages,exogenous_families=values,
        source_sha256={k:v['sha256'] for k,v in bundle['contract']['sources'].items()},
        scope=dict(synthetic_forecast=True,synthetic_receipts=True,uses_future_actual=False,official_market=False,execution_authority=False,
            terminal_convention='acceptedDA exact energy at2hwindowend plus source daymidnight; no hourlySOCequality',fixed_modes='acceptedDA exact5min modes and hourly direction',battery_incremental_reserve_mw=0.,pumped_storage_active=False))
    if type(chain)is SimulatedChain and 'archive_resume_provenance'in _CHAINS[chain]:
        proof=_CHAINS[chain]['archive_resume_provenance']
        inputs['scope']['publication_evidence']='verified historical V3 checkpoint; not live old native acceptance'
        inputs['scope']['checkpoint_evidence_manifest_sha256']=proof['evidence_manifest_sha256']
        inputs['scope']['historical_native_workers']=59
    m=_build_rt_physics(inputs)
    _BUILT[m]=dict(seal=_fingerprint(m,False),inputs=deepcopy(inputs),plan=plan,permit=permit,chain=chain,chain_seal=digest(h))
    return m


def _build_rt_physics(inputs):
    e=inputs['energy_inputs'];schedule=inputs['thermal_schedule'];ts=tuple(range(24));ds=e['thermal'];r=inputs['reserve']
    commitment={(g,t):schedule[t][g]['connected'] for g in ds for t in ts}
    trajectory=tuple(g for g,d in e['thermal'].items() if d['initial_state_kind']=='research_assumption_trajectory')
    m=build_energy_core(**e,mode='SCED',commitment=commitment,_trajectory_initial_units=trajectory)
    for name in ('c_commitment','c_transition_exclusive','c_segment_capacity','c_power_sum','c_power_min','c_power_max','c_ramp_up','c_ramp_down','c_min_up','c_min_down','c_initial_min_duration','c_status','c_max_starts','c_max_stops'):getattr(m,name).deactivate()
    for g in m.G:
        for t in ts:
            m.online[g,t].fix(schedule[t][g]['connected']);m.start[g,t].fix(schedule[t][g]['start']);m.stop[g,t].fix(schedule[t][g]['stop'])
    m.stable=pyo.Expression(m.G,m.T,rule=lambda _,g,t:schedule[t][g]['stable'])
    m.fixed_transition=pyo.Expression(m.G,m.T,rule=lambda _,g,t:schedule[t][g]['mean'])
    m.rt_min=pyo.Expression(m.G,m.T,rule=lambda _,g,t:ds[g]['min_mw'][t]*m.stable[g,t]+m.fixed_transition[g,t])
    m.rt_max=pyo.Expression(m.G,m.T,rule=lambda _,g,t:ds[g]['max_mw'][t]*m.stable[g,t]+m.fixed_transition[g,t])
    m.rt_segment=pyo.Constraint(m.GK,m.T,rule=lambda _,g,k,t:m.segment_power[g,k,t]<=m._energy_inputs['thermal'][g]['segments'][k]['width_mw']*m.stable[g,t])
    m.rt_power=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]==ds[g]['bid_base_mw']*m.stable[g,t]+sum(m.segment_power[g,k,t] for k in m._energy_inputs['thermal'][g]['segments'])+m.fixed_transition[g,t])
    m.rt_lower=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]>=m.rt_min[g,t]);m.rt_upper=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]<=m.rt_max[g,t])
    m.end_power=pyo.Expression(m.G,m.T,rule=lambda _,g,t:m.power[g,t]-m.fixed_transition[g,t]+schedule[t][g]['end'])
    # V4: keep a genuinely constant boundary equality symbolic without a
    # variable, bound relaxation, endpoint repair or constant-True sentinel.
    m.rt_symbolic_zero=pyo.Expression(expr=0.)
    m.rt_ramps=pyo.ConstraintList()
    for g,d in ds.items():
        for t in ts:
            previous=d['initial_power_mw'] if t==0 else m.end_power[g,t-1];cell=schedule[t][g]
            if cell['phase'] in ('startup','shutdown'):
                if t==0:
                    require(abs(previous-cell['begin'])<=1e-6,'first transition true endpoint mismatch')
                m.rt_ramps.add(previous+m.rt_symbolic_zero==cell['begin'])
            else:
                m.rt_ramps.add(m.power[g,t]-previous<=DT*d['ramp_up_mw_per_h'][t]);m.rt_ramps.add(previous-m.power[g,t]<=DT*d['ramp_down_mw_per_h'][t])
    _attach_batteries(m,inputs['batteries'],inputs['clock'],{})
    b=m.battery
    for u in b.BAT:
        for t,row in enumerate(inputs['da_cells']):
            mode=row['batteries'][u]['mode'];b.charge_mode[u,t].fix(int(mode=='charge'));b.discharge_mode[u,t].fix(int(mode=='discharge'))
            permission=row['batteries'][u]['selected_hour_direction_permission'];require(permission in (-1,1),'DA hourly permission missing')
            hour=inputs['clock']['hours'][t];old=b.hour_direction[u,hour]
            require(not old.fixed or abs(pyo.value(old)-int(permission==1))<1e-8,'DAhour permission conflicts with executedprefix history')
            old.fix(int(permission==1))
            end=stamp(row['end']).astimezone(SHANGHAI)
            if end.hour==end.minute==0:b.c_terminal.add(b.energy[u,t]==row['batteries'][u]['energy_end_mwh'])
    # R13 hard-network domain: fix the inherited nonnegative slacks tozero.
    for block in (m.line_over,m.line_under,m.section_over,m.section_under):
        for v in block.values():v.fix(0.)
    m.primary=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0);m.contingency=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.rt_reserve=pyo.ConstraintList()
    for t in ts:
        demand=sum(m.demand_mw[bus,t] for bus in m.B);fixed=sum(m.renewable_power[bus,t]+m.physical_tie[bus,t]+m.other_injection[bus,t] for bus in m.B)
        m.rt_reserve.add(sum(m.rt_max[g,t] for g in m.G)+fixed>=demand+r['positive_mw'][t]);m.rt_reserve.add(sum(m.rt_min[g,t] for g in m.G)+fixed<=demand-r['negative_mw'][t])
        for g,d in ds.items():
            for var in (m.primary,m.contingency):m.rt_reserve.add(var[g,t]<=m.rt_max[g,t]-m.power[g,t])
            m.rt_reserve.add(m.primary[g,t]<=r['primary_fraction'][g]*d['max_mw'][t]*m.stable[g,t]);m.rt_reserve.add(m.contingency[g,t]<=int(r['contingency_eligible'][g])*d['ramp_up_mw_per_h'][t]/6*m.stable[g,t])
        m.rt_reserve.add(sum(m.primary[g,t] for g in m.G)>=r['primary_mw'][t]);m.rt_reserve.add(sum(m.contingency[g,t] for g in m.G)>=r['contingency_mw'][t])
    m.rt_thermal_cost=pyo.Expression(expr=sum(DT*(d['base_bid_yuan_per_h'][t]*m.stable[g,t]+sum(s['price_yuan_per_mwh'][t]*m.segment_power[g,k,t] for k,s in m._energy_inputs['thermal'][g]['segments'].items())+schedule[t][g]['cost'])+d['startup_yuan'][t]*schedule[t][g]['start']+d['shutdown_yuan'][t]*schedule[t][g]['stop'] for g,d in ds.items() for t in ts))
    m.total_cost.set_value(m.rt_thermal_cost+m.renewable_bid_cost+m.renewable_curtailment_cost+m.network_penalty_cost+m.balance_penalty_cost+m.other_cost)
    for g in m.G:
        for t in ts:m.online_min[g,t].set_value(m.rt_min[g,t]);m.online_max[g,t].set_value(m.rt_max[g,t])
    m._r13_inputs=deepcopy(inputs);assert_linear_energy_model(m,require_lp=True)
    return m


def _fingerprint(m,values=True):
    from pyomo.repn import generate_standard_repn
    rows=[]
    for c in m.component_data_objects(pyo.Constraint,active=True):
        r=generate_standard_repn(c.body);rows.append((c.name,str(c.lower),str(c.upper),r.constant,str(r.nonlinear_expr),[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    r=generate_standard_repn(m.objective.expr);rows.append(('objective',r.constant,str(r.nonlinear_expr),[(v.name,a) for v,a in zip(r.linear_vars,r.linear_coefs)]))
    return digest((m._r13_inputs,m._energy_inputs,m._battery_inputs,m._battery_clock,rows,[(v.name,v.value if values or v.fixed else None,v.fixed,str(v.domain),v.lb,v.ub) for v in m.component_data_objects(pyo.Var)]))


def solve_rt_window(model,*,time_limit_s,threads=1):
    from .gd_solve import solve_dispatch
    from .highs_solver import HighsNoIncumbentError
    require(type(threads) is int and threads==1,'RT requires one HiGHS thread')
    require(model in _BUILT and _fingerprint(model,False)==_BUILT[model]['seal'],'unbuilt/modified RT model')
    built=_BUILT[model];_plan_sources(built['plan'],built['permit']);_chain(built['chain'])
    require(digest(built['chain'].history)==built['chain_seal'],'causal history changed after construction')
    _native_solver_identity(solve_dispatch)
    try:solution=solve_dispatch(model,stage='R13_CAUSAL_RT_SCED',time_limit_s=time_limit_s,threads=1,mip_rel_gap=.001)
    except HighsNoIncumbentError as exc:
        require(exc.failure_record['native_path_intact'],'native failure identity');_FAILURES[model]=deepcopy(exc.failure_record);raise
    _native_metrics(solution,model,lp=True);residual=primal_check(model)
    slack=max([0.]+[pyo.value(v) for block in (model.line_over,model.line_under,model.section_over,model.section_under) for v in block.values()])
    require(slack<=1e-6,'RT network overload refuses acceptance')
    solution.metrics.update(primal_max_violation=residual,network_slack_max_mw=slack,hard_network_feasible=True,physical_and_network_verified=True,
        synthetic_research=True,execution_authority=False)
    _ACCEPTED[model]=dict(seal=_fingerprint(model),metrics=deepcopy(solution.metrics),solution=solution)
    return solution


def validate_rt_solution(solution):
    m=solution.model;require(m in _ACCEPTED and _ACCEPTED[m]['solution'] is solution,'live accepted RT solution required')
    accepted=_ACCEPTED[m];require(_fingerprint(m)==accepted['seal'] and solution.metrics==accepted['metrics'],'accepted RT solution/input/metrics changed')
    return True


def pricing_source(solution):
    validate_rt_solution(solution)
    return deepcopy(solution.model._r13_inputs)


def accepted_rt_report(solution):
    validate_rt_solution(solution);m=solution.model;i=m._r13_inputs;state=deepcopy(i['projection']['state']);cells=[]
    bundle=deepcopy(_CHAINS[_BUILT[m]['chain']]['bundle'])
    for u,r in bundle['batteries'].items():r['cycle_budget']=deepcopy(i['batteries'][u]['cycle_budget'])
    for t,da in enumerate(i['da_cells']):
        row=dict(start=da['start'],end=da['end'],thermal={},batteries={})
        for g in m.G:
            c=i['thermal_schedule'][t][g];previous=i['energy_inputs']['thermal'][g]['initial_power_mw'] if t==0 else pyo.value(m.end_power[g,t-1])
            row['thermal'][g]=dict(phase=c['phase'],stable=c['stable'],connected=c['connected'],mean_mw=pyo.value(m.power[g,t]),end_mw=pyo.value(m.end_power[g,t]),begin_mw=c['begin'] if c['phase'] in ('startup','shutdown') else previous,curve_step=c['curve_step'],curve_length=c['curve_length'])
        for u in m.battery.BAT:
            r=i['batteries'][u];mode=da['batteries'][u]['mode'];b=m.battery
            row['batteries'][u]=dict(mode=mode,charge_mw=pyo.value(b.charge[u,t]),discharge_mw=pyo.value(b.discharge[u,t]),power_mw=pyo.value(b.power[u,t]),energy_begin_mwh=pyo.value(b.energy_begin[u,t]),energy_end_mwh=pyo.value(b.energy[u,t]),soc_end=pyo.value(b.energy[u,t])/r['energy_capacity_mwh'],selected_hour_direction_permission=da['batteries'][u]['selected_hour_direction_permission'],internal_throughput_mwh=pyo.value(b.internal_throughput[u,t]),incremental_reserve_mw=0.,agc_mw=0.)
        before=deepcopy(state);state=integrate_cell(state,row,bundle)
        for g in row['thermal']:row['thermal'][g]['endpoint_state']=deepcopy(state['thermal'][g])
        for u,c in row['batteries'].items():
            require(abs(state['batteries'][u]['energy_mwh']-c['energy_end_mwh'])<=1e-6,'native SOC differs from independent integration')
            c.update(mode_age_begin_h=before['batteries'][u]['mode_age_h'],mode_age_end_h=state['batteries'][u]['mode_age_h'],hour_direction=state['batteries'][u]['hour_direction'],daily_used_internal_mwh=deepcopy(state['batteries'][u]['daily_used_internal_mwh']))
        row['reserve']=dict(positive_requirement_mw=i['reserve']['positive_mw'][t],contingency_requirement_mw=i['reserve']['contingency_mw'][t],primary_mw=sum(pyo.value(m.primary[g,t]) for g in m.G),contingency_mw=sum(pyo.value(m.contingency[g,t]) for g in m.G),battery_signed_baseline_mw=sum(c['power_mw'] for c in row['batteries'].values()),battery_incremental_credit_mw=0.)
        row['exogenous']={family:{bus:series[bus,t] for bus in m.B} for family,series in i['exogenous_families'].items()}
        row['flow_mw']={line:pyo.value(m.flow[line,t]) for line in m.L};row['pumped_storage_power_mw']=0.
        thermal_cost=sum(DT*(d['base_bid_yuan_per_h'][t]*i['thermal_schedule'][t][g]['stable']+sum(seg['price_yuan_per_mwh'][t]*pyo.value(m.segment_power[g,k,t]) for k,seg in m._energy_inputs['thermal'][g]['segments'].items())+i['thermal_schedule'][t][g]['cost'])+d['startup_yuan'][t]*i['thermal_schedule'][t][g]['start']+d['shutdown_yuan'][t]*i['thermal_schedule'][t][g]['stop'] for g,d in i['energy_inputs']['thermal'].items())
        credit=sum(DT*i['batteries'][u]['charge_curve']['prices_yuan_per_mwh'][k]*pyo.value(m.battery.segment[u,d,k,t]) for u,d,k in m.battery.SEG if d=='charge')
        discharge_cost=sum(DT*i['batteries'][u]['discharge_curve']['prices_yuan_per_mwh'][k]*pyo.value(m.battery.segment[u,d,k,t]) for u,d,k in m.battery.SEG if d=='discharge')
        row['cost_components_yuan']=dict(thermal=thermal_cost,battery_discharge=discharge_cost,battery_charge_credit=credit,battery_net=discharge_cost-credit,total=thermal_cost+discharge_cost-credit)
        cells.append(row)
    costs=dict(thermal=pyo.value(m.rt_thermal_cost),battery_discharge=pyo.value(m.battery.discharge_cost),battery_charge_credit=pyo.value(m.battery.charge_credit),battery_net=pyo.value(m.battery.bid_cost),renewable_bid=pyo.value(m.renewable_bid_cost),renewable_curtailment=pyo.value(m.renewable_curtailment_cost),network_penalty=pyo.value(m.network_penalty_cost),balance_penalty=pyo.value(m.balance_penalty_cost),total=pyo.value(m.total_cost))
    require(abs(sum(c['cost_components_yuan']['total'] for c in cells)-costs['total'])<=1e-6+1e-10*abs(costs['total']),'RT original bid/cell cost decomposition')
    return dict(schema='gd.research.r13.accepted_rt_report.v1',start=i['start'],cutoff=i['cutoff'],input_sha256=digest(i),physical_model_sha256=_BUILT[m]['seal'],plan_sha256=i['plan_sha256'],projection=deepcopy(i['projection']),physical_cells=cells,lookahead_endpoint_state=state,
        metrics=deepcopy(solution.metrics),cost_components_yuan=costs,scope=deepcopy(i['scope']),committable_prefix_length=3,lookahead_execution=False,official_price=None,settlement_cash=None,execution_authority=False)


def issue_rt_prefix(solution,chain):
    validate_rt_solution(solution);h=_chain(chain);m=solution.model;built=_BUILT[m];i=m._r13_inputs
    require(built['chain'] is chain and digest(h)==built['chain_seal'],'prefix issuance requires unchanged originating causal chain')
    require(stamp(h['clock'])==stamp(i['cutoff']),'cannot backdate RT command issuance')
    require(not any(x['start']==i['start'] for x in h['accepted_windows']),'accepted window already issued')
    report=accepted_rt_report(solution);rh=digest(report)
    for j,cell in enumerate(report['physical_cells'][:3]):
        require(not any(stamp(c['cell']['start'])==stamp(cell['start']) for c in h['commands']),'overlapping issued RT command')
        h['commands'].append(dict(id='R13-RT-'+i['start']+'-'+str(j),kind='accepted_native_rt_prefix',issued_at=h['clock'],available_at=h['clock'],cell=deepcopy(cell),native_source_sha256=rh))
    h['accepted_windows'].append(dict(start=i['start'],cutoff=i['cutoff'],report_sha256=rh,input_sha256=report['input_sha256'],issued_cells=3,lookahead_cells=21,metrics=deepcopy(solution.metrics)))
    _seal_chain(chain)
    return dict(report=report,commands=deepcopy(h['commands'][-3:]))


def adopt_plan_extension(chain,plan,permit):
    """Prospective tail adoption cannot alter an already published delivery cell."""
    h=_chain(chain);old=_CHAINS[chain]['plan'];day,tail=_plan_sources(plan,permit)
    require(plan.get('predecessor_plan_sha256')==digest(old) and plan.get('supersedes_plan_id')==old.get('plan_id'),'tail extension publication lineage changed')
    require(stamp(plan['published_at'])==stamp(h['clock']),'tail adoption must occur at its actual simulated publication time')
    require(len(plan['physical_cells'])>len(old['physical_cells']) and plan['physical_cells'][:len(old['physical_cells'])]==old['physical_cells'],'tail extension changed previously published cells')
    require(stamp(plan['published_at'])<=stamp(h['clock']),'tail plan not available at adoption')
    require(digest(day)==_CHAINS[chain]['day_source_sha256'],'tail extension changed day source bundle')
    for u,r in _CHAINS[chain]['bundle']['batteries'].items():
        if tail is not None:
            for date,budget in tail['batteries'][u]['cycle_budget'].items():
                if date not in r['cycle_budget']:r['cycle_budget'][date]=dict(limit_cycles=budget['limit_cycles'],used_internal_mwh=0.)
    _CHAINS[chain]['plan']=deepcopy(plan);_CHAINS[chain]['permit']=permit;h['plan_sha256']=digest(plan)
    h['events'].append(dict(kind='prospective_accepted_tail_adoption',at=h['clock'],plan_sha256=digest(plan)))
    _seal_chain(chain)
