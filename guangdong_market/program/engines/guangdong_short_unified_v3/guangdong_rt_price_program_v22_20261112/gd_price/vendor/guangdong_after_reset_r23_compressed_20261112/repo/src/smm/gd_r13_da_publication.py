"""R13 live-only research publication and independently accepted tail.

No dictionary, serialized report, digest or caller choice grants live authority.
The simulated prior-day publication never claims actual backdated commands.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from weakref import WeakKeyDictionary
import json

from .gd_da_candidate import digest, require, stamp
from .gd_da_ieee39 import raw
from .gd_da_battery_candidate import (accepted_da_report, build_candidate_scuc,
    local_diagnostic_horizon, compile_path)
from .gd_da_prepared_boundary import validate_live_boundary
from .gd_r13_source_recipe import source_cells, TAIL_POLICY

_LIVE = WeakKeyDictionary()
_TAIL = WeakKeyDictionary()


class _PlanPermit:
    __slots__=('__weakref__',)


def _bind(c,key,value,kind,available):
    artifact=raw(value)
    c['sources'][key]=dict(artifact=artifact,sha256=sha256(artifact).hexdigest(),kind=kind,available_at=available)


def prepare_tail(root, day_bundle, day_report, *, generated_at_utc=None):
    """Pure tail recipe; this alone is not accepted and cannot be published."""
    b=deepcopy(day_bundle);c=b['contract'];day=stamp(c['day_start']);begin=day+timedelta(days=1);n=21;ts=tuple(range(n))
    require(day_report['physical_cells'][-1]['end']==begin.isoformat(),'exact accepted midnight source required')
    require(day_report['boundary_kind']=='prepared_D00_research','tail requires prepared DA research source')
    from .gd_da_battery_ieee39 import validate_ieee39_battery_da
    validate_ieee39_battery_da(day_bundle)
    ex=source_cells(static_raw={k[7:]:v['artifact'] for k,v in c['sources'].items() if k.startswith('static:')},day_start=c['day_start'],seed=b['seed'],count=309)
    c.update(schema='gd.research.da_candidate_battery.diagnostic.v1',experiment_id='R13-source-bound-tail-'+day.date().isoformat(),
        boundary_kind='local_diagnostic_research',boundary_at=begin.isoformat(),cutoff=c['planned_publication_at'],generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat())
    e=b['energy_inputs'];e['horizon']=local_diagnostic_horizon(n)
    old_ids=tuple(day_bundle['energy_inputs']['horizon'].ids)
    def series(v):return {t:deepcopy(v) for t in ts}
    for g,d in e['thermal'].items():
        for k,v in list(d.items()):
            if isinstance(v,dict) and set(v)==set(old_ids):d[k]=series(v[old_ids[-1]])
        for seg in d['segments']:
            seg['price_yuan_per_mwh']=series(seg['price_yuan_per_mwh'][old_ids[-1]])
        q=day_report['terminal'][g]
        d.update(initial_online=q['online'],initial_power_mw=q['power_mw'],initial_state_age_h=q['age_h'],initial_state_kind='stable',max_starts=1,max_stops=0)
    for d in e['lines'].values():
        for key in ('lower_mw','upper_mw'):d[key]=series(d[key][old_ids[-1]])
    for key,family in (('load_mw','load_mw'),('physical_tie_mw','boundary_tie_mw')):
        e[key]={(bus,t):ex['cells'][288+t]['values'][family][bus] for bus in e['buses'] for t in ts}
    e['renewable_mw']={(bus,t):sum(ex['cells'][288+t]['values'][f][bus] for f in ('wind_mw','solar_mw')) for bus in e['buses'] for t in ts}
    for key in ('renewable_bid_yuan_per_mwh','renewable_curtailment_penalty_yuan_per_mwh'):e[key]={(bus,t):0. for bus in e['buses'] for t in ts}
    for key in ('positive_mw','negative_mw','primary_mw','contingency_mw'):b['reserve'][key]=series(b['reserve'][key][old_ids[-1]])
    curves=json.loads(c['sources']['curves']['artifact']);byid={x['id']:x for x in b['static']['units']['thermal']};menu={}
    for g,d in e['thermal'].items():
        menu[g]=[dict(id='carry-stable' if d['initial_online'] else 'carry-off',events=())]
        if d['initial_online'] or byid[g]['type']=='coal':continue
        for minute in TAIL_POLICY['gt_restart_minutes']:
            at=minute//5;duration=curves['durations_minutes'][g]['startup'];steps=[];minimum=d['min_mw'][0];count=duration//5
            if at+count>n or d['initial_state_age_h']+minute/60+1e-9<d['min_down_h']:continue
            event_time=begin+timedelta(minutes=minute);notice=event_time-timedelta(minutes=30)
            if notice < begin-timedelta(hours=d['initial_state_age_h']):continue
            for j in range(count):
                a,z=minimum*j/count,minimum*(j+1)/count;mean=(a+z)/2
                steps.append((a,mean,z,float(byid[g]['noload'])+float(b['static']['bids']['units'][g]['segments'][0]['price'])*mean))
            event=dict(kind='startup',at=at,steps=tuple(steps),notice_h=.5,notification_at=notice.isoformat(),source_id='curves',curve_rate_mw_per_min=minimum/duration)
            spec=dict(id=f'restart-{minute}min',events=(event,));compile_path(d,spec,ts,c);menu[g].append(spec)
    b['candidates']=menu;b['menu_declaration']=deepcopy(TAIL_POLICY)
    last=day_report['physical_cells'][-1]
    for u,r in b['batteries'].items():
        q=last['batteries'][u]
        r.update(initial_energy_mwh=q['energy_end_mwh'],initial_power_mw=q['power_mw'],initial_mode=q['mode'],initial_mode_age_h=q['mode_age_end_h'],initial_hour_direction=0,
            terminal_at=(begin+timedelta(minutes=105)).isoformat(),availability=series(True),
            cycle_budget={**{d:dict(limit_cycles=1.,used_internal_mwh=v) for d,v in day_report['battery_terminal'][u]['daily_used_internal_mwh'].items()},begin.date().isoformat():dict(limit_cycles=1.,used_internal_mwh=0.)})
    _bind(c,'battery_parameters',dict(batteries=b['batteries'],pumped_storage=c['storage']['pumped_storage']),'research_storage_assumption',c['cutoff'])
    _bind(c,'r13_tail_policy',TAIL_POLICY,'research_curve_assumption',(day-timedelta(days=1)).isoformat())
    _bind(c,'r13_tail_prior',dict(priors=ex['priors'],vintages=ex['vintages']),'synthetic_da_vintage',(day-timedelta(hours=4.5)).isoformat())
    _bind(c,'r13_tail_predecessor',dict(report=day_report,reason='derived accepted research plan terminal; not actual midnight telemetry'),'research_boundary_assumption',c['cutoff'])
    b['r13_tail_meta']=dict(schema='gd.research.r13.tail_inputs.v1',policy=deepcopy(TAIL_POLICY),predecessor_report_sha256=digest(day_report),source_cells=ex['cells'][288:],
        source_availability_is_simulated=True,actual_generated_at_utc=c['generated_at_utc'],official_DA98=False)
    return b


def validate_tail_recipe(day_bundle, day_report, tail_bundle):
    expected=prepare_tail(None,day_bundle,day_report,generated_at_utc=tail_bundle['contract']['generated_at_utc'])
    require(digest(expected)==digest(tail_bundle),'tail source recipe or policy changed')
    return True


def build_tail(day_solution, day_bundle, tail_bundle):
    report=accepted_da_report(day_solution)
    require(digest(report)==tail_bundle['r13_tail_meta']['predecessor_report_sha256'],'live accepted DA predecessor mismatch')
    require(digest(day_solution.model._da_inputs)==digest({k:day_bundle[k] for k in ('contract','candidates','reserve','energy_inputs','batteries')}),'live DA input/source mismatch')
    validate_tail_recipe(day_bundle,report,tail_bundle)
    m=build_candidate_scuc(**{k:tail_bundle[k] for k in ('contract','candidates','reserve','energy_inputs','batteries')})
    _TAIL[m]=(day_solution,deepcopy(tail_bundle),digest(tail_bundle))
    return m


def _check_tail_solution(solution,tail_bundle,day_solution):
    report=accepted_da_report(solution)
    require(report['metrics']['hard_network_feasible'] and report['metrics']['physical_and_network_verified'],'tail hard physical/network acceptance required')
    require(digest(solution.model._da_inputs)==digest({k:tail_bundle[k] for k in ('contract','candidates','reserve','energy_inputs','batteries')}),'tail live input mismatch')
    # Fixed SCED is a clone. Find its genuine source model registered at build.
    matches=[m for m,v in _TAIL.items() if v[0] is day_solution and v[2]==digest(tail_bundle)]
    require(len(matches)==1,'tail lacks live accepted predecessor binding')
    # Ask the frozen live-SCUC API to revalidate the exact registered model.
    # A separate generic same-input solve is not this predecessor occurrence.
    from .gd_da_battery_candidate import build_fixed_selection_sced
    import pyomo.environ as pyo
    expected=build_fixed_selection_sced(matches[0])
    require(digest(expected._da_scuc_metrics)==digest(solution.metrics['original_scuc_metrics']),'tail SCED is not derived from the registered accepted SCUC occurrence')
    for variable in matches[0].component_data_objects(pyo.Var,descend_into=True):
        if variable.is_integer():
            fixed=solution.model.find_component(variable.name)
            require(fixed is not None and fixed.fixed and pyo.value(fixed)==round(pyo.value(variable)),'tail fixed selection differs from exact registered source')
    require(tail_bundle['r13_tail_meta']['predecessor_report_sha256']==digest(accepted_da_report(day_solution)),'DA changed after tail build')
    require(len(report['physical_cells'])==21,'complete 105-minute tail required')
    return report


def _decorate(report,bundle):
    cells=deepcopy(report['physical_cells']);ts=tuple(bundle['energy_inputs']['horizon'].ids);begin=stamp(bundle['contract']['boundary_at']);events=[]
    for g,d in bundle['energy_inputs']['thermal'].items():
        spec=next(x for x in bundle['candidates'][g] if x['id']==report['selected_paths'][g]);path=compile_path(d,spec,ts,bundle['contract']);age=d['initial_state_age_h'];online=d['initial_online']
        transition_steps={ev['at']+j:(j,len(ev['steps'])) for ev in spec['events'] for j in range(len(ev['steps']))}
        for ev in spec['events']:
            events.append(dict(unit=g,kind=ev['kind'],effective_at=(begin+timedelta(minutes=5*ev['at'])).isoformat(),notification_at=ev['notification_at'],
                available_at=bundle['contract']['planned_publication_at'],curve_steps=deepcopy(ev['steps']),source_id=ev['source_id'],research_schedule=True,actual_command_history=False))
        for i,row in enumerate(cells):
            p=path['cells'][i];x=row['thermal'][g];before=age
            x.update(curve_step=None if i not in transition_steps else transition_steps[i][0],curve_length=None if i not in transition_steps else transition_steps[i][1])
            if p['start']:age=0.;online=1
            x.update(begin_mw=p['begin'] if p['phase'] in ('startup','shutdown') else (d['initial_power_mw'] if i==0 else cells[i-1]['thermal'][g]['end_mw']),
                transition_begin_mw=p['begin'],transition_mean_mw=p['mean'],transition_end_mw=p['end'],cost_yuan_per_h=p['cost'],start=p['start'],stop=p['stop'],age_begin_h=age)
            age+=1/12
            if p['phase']=='shutdown' and abs(p['end'])<1e-9:online=0;age=0.
            x.update(age_end_h=age,residence_remaining_h=max(0.,(d['min_up_h'] if online else d['min_down_h'])-age))
    return cells,events


def _preparation(bundle):
    r=bundle['prepared_boundary'];p=r['problem'];h=p['hypothesis'];begin=h['preparation_boundary_at'];bats={}
    for u,x in r['storage_inventory']['batteries'].items():
        bats[u]=dict(energy_mwh=x['energy_mwh'],power_mw=0.,mode='idle',mode_age_h=bundle['batteries'][u]['initial_mode_age_h']-.25,hour_direction=0,selected_hour_direction_permission=None,daily_used_internal_mwh={stamp(begin).date().isoformat():0.})
    thermal={g:dict(phase='stable',online=1,power_mw=r['equilibrium']['thermal_power_mw'][g],age_h=h['age_h'][g],residence_remaining_h=0.) for g in r['thermal']}
    cells=[]
    for i,row in enumerate(r['cells']):
        cells.append(dict(start=row['at'],end=row['end'],thermal={g:dict(phase='stable',stable=1,connected=1,mean_mw=v,end_mw=v,begin_mw=thermal[g]['power_mw'] if i==0 else r['cells'][i-1]['power_mw'][g],age_begin_h=h['age_h'][g]+i/12,age_end_h=h['age_h'][g]+(i+1)/12,start=0,stop=0) for g,v in row['power_mw'].items()},
            batteries={u:dict(mode='idle',power_mw=0.,charge_mw=0.,discharge_mw=0.,energy_begin_mwh=x['energy_mwh'],energy_end_mwh=x['energy_mwh'],mode_age_begin_h=x['mode_age_h']+i/12,mode_age_end_h=x['mode_age_h']+(i+1)/12,hour_direction=0,selected_hour_direction_permission=None,internal_throughput_mwh=0.,daily_used_internal_mwh=0.,cycle_date=stamp(row['at']).date().isoformat()) for u,x in bats.items()}))
    return dict(receipt_at=begin,issued_at=begin,receipt_available_at=begin,source_publication_at=bundle['contract']['planned_publication_at'],
        initial_state=dict(at=begin,thermal=thermal,batteries=bats),cells=cells,end_state=deepcopy(r['thermal']),
        raw_boundary_report=deepcopy(r),synthetic_zero_tracking_hypothesis=True,historical_telemetry=False,actual_command_history=False)


def publish_research_plan(day_solution, day_bundle, tail_solution=None, tail_bundle=None, *, boundary_permit):
    require(tail_solution is None and tail_bundle is None,'tail publication requires explicit prospective extend_research_plan protocol')
    report=accepted_da_report(day_solution);validate_live_boundary(day_bundle['prepared_boundary'],boundary_permit)
    require(report['metrics']['hard_network_feasible'] and report['metrics']['physical_and_network_verified'],'day hard physical/network acceptance required')
    require(digest(day_solution.model._da_inputs)==digest({k:day_bundle[k] for k in ('contract','candidates','reserve','energy_inputs','batteries')}),'publication must bind exact live source bundle')
    require((tail_solution is None)==(tail_bundle is None),'tail solution and source required together')
    cells,events=_decorate(report,day_bundle);tail_report=None
    if tail_solution is not None:
        tail_report=_check_tail_solution(tail_solution,tail_bundle,day_solution);extra,ev=_decorate(tail_report,tail_bundle)
        require(cells[-1]['end']==extra[0]['start'],'tail gap');cells+=extra;events+=ev
    c=day_bundle['contract'];plan=dict(schema='gd.research.r13.issued_plan.v1',day_start=c['day_start'],cutoff=c['cutoff'],day_cutoff=c['cutoff'],
        published_at=c['planned_publication_at'],publication_at=c['planned_publication_at'],day_publication_at=c['planned_publication_at'],version_available_at=c['planned_publication_at'],publication_kind='initial_day',supersedes_plan_id=None,predecessor_plan_sha256=None,actual_created_at_utc=datetime.now(timezone.utc).isoformat(),
        publication_clock_is_simulated=True,historical_publication_claim=False,physical_cells=cells,events=events,selected_paths=deepcopy(report['selected_paths']),tail_selected_paths=None if tail_report is None else deepcopy(tail_report['selected_paths']),
        initial_preparation=_preparation(day_bundle),source_sha256=deepcopy(report['source_sha256']),day_report_sha256=digest(report),
        tail_report_sha256=None if tail_report is None else digest(tail_report),has_accepted_tail=tail_report is not None,
        coverage_end=cells[-1]['end'],accepted_research_plan=True,official_DA98=False,execution_authority=False,
        original_all_gas_off_reachable=False,actual_command_history=False)
    plan['plan_id']='R13-plan-'+digest(plan)
    tail_source_model=None if tail_solution is None else next(m for m,v in _TAIL.items() if v[0] is day_solution and v[2]==digest(tail_bundle))
    permit=_PlanPermit();_LIVE[permit]=dict(tail_source_model=tail_source_model,plan=deepcopy(plan),day_solution=day_solution,day_bundle=deepcopy(day_bundle),tail_solution=tail_solution,tail_bundle=deepcopy(tail_bundle),boundary_permit=boundary_permit,predecessor_permit=None)
    return plan,permit


def _extension_artifact(predecessor_plan, tail_report, tail_bundle, extension_publication_at):
    """Pure prospective serialization. This never mints a live plan permit."""
    previous=deepcopy(predecessor_plan);at=stamp(extension_publication_at);day=stamp(previous['day_start'])
    require(previous['publication_kind']=='initial_day' and not previous['has_accepted_tail'],'extension requires original tail-free day publication')
    require(len(previous['physical_cells'])==288,'unchanged complete day prefix required')
    require(at>=day+timedelta(minutes=45),'tail extension cannot predate the four-window pause at D00:45')
    require(at>=stamp(previous['version_available_at']),'plan version availability regressed')
    extra,events=_decorate(tail_report,tail_bundle)
    require(len(extra)==21 and extra[0]['start']==previous['coverage_end'],'complete contiguous tail required')
    require(at<=stamp(extra[0]['start']),'tail must be published prospectively')
    for event in events:
        event['available_at']=at.isoformat()
        deadline=event['notification_at'] or event['effective_at']
        require(at<=stamp(deadline),'tail selection became available after required notice or event')
    for row in extra:
        row['plan_version_available_at']=at.isoformat()
        for battery in row['batteries'].values():
            battery['notice_available_at']=at.isoformat()
            require(at+timedelta(hours=battery['preparation_h'])<=stamp(row['start']),'tail battery preparation unavailable by action')
    result=deepcopy(previous);result.pop('plan_id')
    result.update(published_at=at.isoformat(),publication_at=at.isoformat(),version_available_at=at.isoformat(),
        day_publication_at=previous['day_publication_at'],publication_kind='prospective_tail_extension',
        supersedes_plan_id=previous['plan_id'],predecessor_plan_sha256=digest(previous),
        extension_publication_at=at.isoformat(),extension_decision_cutoff=at.isoformat(),cutoff=at.isoformat(),day_cutoff=previous['day_cutoff'],
        actual_created_at_utc=datetime.now(timezone.utc).isoformat(),physical_cells=previous['physical_cells']+extra,
        events=previous['events']+events,tail_selected_paths=deepcopy(tail_report['selected_paths']),
        tail_report_sha256=digest(tail_report),has_accepted_tail=True,coverage_end=extra[-1]['end'])
    require(result['physical_cells'][:288]==previous['physical_cells'],'extension changed published day cells')
    result['plan_id']='R13-plan-'+digest(result)
    return result


def extend_research_plan(predecessor_plan, predecessor_permit, tail_solution, tail_bundle, *, extension_publication_at):
    """Publish a new live version at the explicit current virtual pause clock.

    The RT adopter separately verifies this exact predecessor and its own live
    clock. A prospective tail is never added to an earlier version in place.
    """
    validate_live_plan(predecessor_plan,predecessor_permit)
    previous=_LIVE[predecessor_permit]
    report=_check_tail_solution(tail_solution,tail_bundle,previous['day_solution'])
    plan=_extension_artifact(predecessor_plan,report,tail_bundle,extension_publication_at)
    source=next(m for m,v in _TAIL.items() if v[0] is previous['day_solution'] and v[2]==digest(tail_bundle))
    permit=_PlanPermit();_LIVE[permit]=dict(plan=deepcopy(plan),tail_source_model=source,day_solution=previous['day_solution'],
        day_bundle=deepcopy(previous['day_bundle']),tail_solution=tail_solution,tail_bundle=deepcopy(tail_bundle),
        boundary_permit=previous['boundary_permit'],predecessor_permit=predecessor_permit)
    return plan,permit


def validate_plan_availability(plan,cutoff):
    require(stamp(plan['published_at'])==stamp(plan['publication_at'])==stamp(plan['version_available_at']),'version publication clocks differ')
    require(stamp(plan['version_available_at'])<=stamp(cutoff),'plan version unavailable at requested cutoff')
    require(stamp(plan['day_publication_at'])<=stamp(plan['version_available_at']),'day publication later than version')
    return True


def validate_live_plan(plan,permit,*,cutoff=None):
    require(permit in _LIVE,'live internally accepted research publication required')
    r=_LIVE[permit];require(digest(plan)==digest(r['plan']),'published plan changed')
    if cutoff is not None:validate_plan_availability(plan,cutoff)
    if r['predecessor_permit'] is not None:
        previous=_LIVE.get(r['predecessor_permit'])
        require(previous is not None,'extension predecessor live identity unavailable')
        require(plan['supersedes_plan_id']==previous['plan']['plan_id'] and plan['predecessor_plan_sha256']==digest(previous['plan']),'extension predecessor identity changed')
        require(plan['physical_cells'][:288]==previous['plan']['physical_cells'],'extension day prefix changed')
    require(digest(accepted_da_report(r['day_solution']))==plan['day_report_sha256'],'accepted DA changed after publication')
    validate_live_boundary(r['day_bundle']['prepared_boundary'],r['boundary_permit'])
    if r['tail_solution'] is not None:require(digest(_check_tail_solution(r['tail_solution'],r['tail_bundle'],r['day_solution']))==plan['tail_report_sha256'],'accepted tail changed')
    return True


def live_plan_sources(plan,permit):
    validate_live_plan(plan,permit);r=_LIVE[permit]
    return deepcopy(r['day_bundle']),deepcopy(r['tail_bundle'])


def get_live_plan(plan,permit):
    validate_live_plan(plan,permit);r=_LIVE[permit]
    return dict(plan=deepcopy(plan),bundle=deepcopy(r['day_bundle']),tail_bundle=deepcopy(r['tail_bundle']),report=accepted_da_report(r['day_solution']),
        physical_cells=deepcopy(plan['physical_cells']),publication_at=plan['published_at'],initial_preparation=deepcopy(plan['initial_preparation']))
