"""Portable IEEE39 research inputs and real October SCED construction, no solve.

Reads only named static inventory/network/config/bid files. Historical profiles,
old dispatch/actual results and source_inputs are intentionally never opened.
All new dynamic values are disclosed counterfactual hypotheses, not a forecast
reconstruction, operational effective plan, or historical performance evaluation.
"""
from copy import deepcopy
import csv
from datetime import datetime,timedelta
from hashlib import sha256
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .gd_october_forecast_inputs import (canonical_bytes,source_ref,build_forecast_series,
    rolling_day_requirements,assess_multiday_forecast_inputs)
from .gd_october_research_forecast import make_prior_snapshot
from .gd_october_research_thermal import ASSUMPTION_SCHEMA,POINT_SCHEMA
from .gd_october_thermal import (RESEARCH_SCHEMA,RESEARCH_RECORD_SCHEMA,validate_book,
    advance_thermal,_active,initial_trajectory_kind,FIVE)
from .gd_october_sced import PERIODS,RULE_VERSION,RESERVE_SHARING
from .gd_october_storage import SCHEMA as STORAGE_SCHEMA,UNITS,SL_SIDES

SOURCE='research://ieee39/r9-explicit-hypotheses-not-operator-approval'
STATIC_FILES=('config/modes.yaml','config/base.yaml','config/units.yaml','data/case39/buses.csv','data/case39/branches.csv','resolved_bids.json')


def _s(value):return {t:deepcopy(value) for t in PERIODS}
def _source_record(value):return dict(value,sources={k:SOURCE for k in value})
def _stamp(start,minutes=0):return (start+timedelta(minutes=minutes)).isoformat()


def _load_static(root):
    root=Path(root);root=root/'inputs/ieee39_v6' if (root/'inputs/ieee39_v6').is_dir() else root
    raw={name:(root/name).read_bytes() for name in STATIC_FILES}
    data=dict(base=yaml.safe_load(raw['config/base.yaml']),modes=yaml.safe_load(raw['config/modes.yaml']),units=yaml.safe_load(raw['config/units.yaml']),
        buses=list(csv.DictReader(io.StringIO(raw['data/case39/buses.csv'].decode()))),
        branches=list(csv.DictReader(io.StringIO(raw['data/case39/branches.csv'].decode()))),
        bids=json.loads(raw['resolved_bids.json']))
    reserve=data['modes']['MKT']['reserve']
    if reserve['up_requirement_mode']!='fixed' or reserve['fixed_up_mw']!=400. or reserve['down_enabled'] is not False:
        raise ValueError('original fixed 400 MW upward requirement and disabled downward demand required')
    if len(data['buses'])!=39 or len(data['branches'])!=46:raise ValueError('original IEEE39/46 inventory required')
    return raw,data


def _ledger(start,seed,raw):
    return dict(schema_version=ASSUMPTION_SCHEMA,source_kind='research_assumption',seed=seed,
        scenario='New exogenous IEEE39 fixed-plan research experiment. Not a replay or historical hindsight score.',
        day_start=start.isoformat(),operator_confirmation=None,official_market_accepted=False,
        static_sources={k:sha256(v).hexdigest() for k,v in raw.items()},
        dynamic_source='No old profiles, actual observations, source_inputs or dispatch results are read.',
        forecast=dict(method='synthetic_exogenous_harmonic_prior_v1',
            phase='uint32(first8hex(SHA256(str(seed)+":"+resource)))/2^32 * 2*pi',
            load='base_bus_MW * original_load_scale * (0.91+0.09*cos(2*pi*(h-18)/24)+0.025*sin(4*pi*h/24+phase)+0.0002*h)',
            wind='capacity*(0.38+0.10*sin(2*pi*h/18+phase)+0.035*cos(2*pi*h/7+phase))',
            solar='capacity*0.72*max(0,sin(pi*(hour_of_day-6)/12)), only 06:00..18:00',
            tie='original_pmax*(0.70+0.05*cos(2*pi*h/24+phase)+0.0001*h)',
            interpolation='15-minute instantaneous knots, exact piecewise-linear integral for each five-minute mean',
            tail='formula generated explicitly through next day 01:45, never last-row repetition',
            issue_clock='virtual scenario publication at prior day 23:30; immutable exogenous prior declared prior day'),
        network=dict(susceptance='100 MVA / original x_pu',limits='raw rate_mw * base.line_capacity_multiplier * base.line_scale.get(id,1), exactly once',
            losses='excluded; lossless DC',security_contingencies='not implemented'),
        thermal=dict(events='CCGT starts 06:00 for 60 min, stops 22:00 for 30 min; GT starts 09:00 and 16:00 for 30 min, stops 12:00 and 20:00 for 15 min.',
            class_choice='hot explicitly assumed, not inferred from offline age; notification_at is a predeclared hypothetical future notice, not an issued command',gas='single equivalent gas unit hypothesis, not verified multi-shaft equipment',
            curves='linear 15-minute endpoint hypotheses 0 to Pmin or reverse; fixed injection, no flexible reserve or AGC during transition',
            costs='Transition: noload_yuan_per_h + first energy segment price * transition mean MW. Stable: energy-bid integral only, no separate noload. Both exclude startup/shutdown event fees, compensation and all-in profit',
            residence='shutdown_curve_start_conservative; original ut/dt preserved',
            initial='original static init states are declared pre-day hypothetical seeds, not telemetry'),
        schedule='New explicitly authored counterfactual schedule, not historical optimized DA schedule; binary states are never interpolated.',
        storage=dict(modes='all original batteries idle and original pumped subunits off for this declared scenario; resources not removed',
            final='explicit per-window targets equal original initial inventory; no claim of economically optimal schedule',
            battery_ramp='nameplate pmax per five minutes, research assumption',cycle_limit=1,
            reservoir='original stored volume unit 10^4 m3; energy coefficients kWh/m3 -> m3/MWh=1000/e; fixed explicit target bands 0',
            reserve='unverified batteries/pumped/renewables excluded; no invented eligibility',
            bids='capacity-weighted average of existing piecewise storage offers; approximation is irrelevant in idle/off plan but not exact active dispatch bidding'),
        reserves=dict(original_source='config/modes.yaml MKT.reserve.fixed_up_mw=400.0; down_enabled=false; all other legacy policies inactive',positive_mw=400,negative_mw=0,primary_mw=0,contingency_mw=400,
            interpretation='400 MW original upward requirement preserved as positive and contingency stress-test requirements; October equivalence is a hypothesis, not official service mapping',
            contingency_policy='hard; no lowered requirement or hidden slack',thermal_qualification='stable online thermal assumed eligible; 6% coal, 4% gas primary capacity coefficients',agc='explicit zero award assumption, no 5 MW default'),
        limitations=['not SCUC','no solved DA/RT optimum','no official reserve/AGC auction','not official prices or settlement',
            'window count constraints not all-day unit start-count certification','single-equivalent gas units','no AC/N-1 security iteration',
            'counterfactual state blueprint is not actual executed history','storage idle/off plan restricts optimization intentionally'])


def _thermal_book(start,units,ledger,run_id):
    raw_ledger=canonical_bytes(ledger);available=start-timedelta(minutes=30)
    events={'CCGT':[(360,'startup',60),(1320,'shutdown',30)],
            'GT':[(540,'startup',30),(720,'shutdown',15),(960,'startup',30),(1200,'shutdown',15)]}
    records=[]
    for unit in units['thermal']:
        g=unit['id'];previous=start-timedelta(hours=float(unit['init']['hours']))
        for index,(minute,kind,duration) in enumerate(events.get(g,[])):
            at=start+timedelta(minutes=minute);pmin=float(unit['pmin']);n=duration//15
            levels=[pmin*j/n for j in range(n+1)]
            if kind=='shutdown':levels.reverse()
            points=dict(schema_version=POINT_SCHEMA,source_kind='research_assumption',unit_id=g,kind=kind,
                points=[dict(at=_stamp(at,15*j),mw=v) for j,v in enumerate(levels)])
            raw_points=canonical_bytes(points);steps=[]
            for j in range(duration//5):
                p=pmin*j/(duration/5);q=pmin*(j+1)/(duration/5)
                if kind=='shutdown':p,q=pmin-p,pmin-q
                steps.append(dict(start=_stamp(at,j*5),end=_stamp(at,(j+1)*5),start_mw=p,end_mw=q,mean_mw=(p+q)/2,
                    cost_yuan_per_h=float(unit['noload'])+float(ledger['thermal_energy_price'][g])*(p+q)/2))
            p=dict(min_mw=pmin,max_mw=float(unit['pmax']),min_up_h=float(unit['ut']),min_down_h=float(unit['dt']),
                ramp_up_mw_per_h=float(unit['ramp'])*60,ramp_down_mw_per_h=float(unit['ramp'])*60,
                curve_rise_mw_per_min=pmin/(60 if g=='CCGT' else 30),curve_fall_mw_per_min=pmin/(30 if g=='CCGT' else 15))
            record=dict(schema_version=RESEARCH_RECORD_SCHEMA,run_id=run_id,event_id=f'{g}-{kind}-{index}',unit_id=g,kind=kind,
                startup_class='hot' if kind=='startup' else None,technology='gas',applicability_source=SOURCE+'; single-equivalent gas hypothesis',
                effective_plan_id='research-explicit-plan',source=SOURCE,
                typical_curve=dict(curve_id=f'{g}-{kind}-{index}-research',artifact_utf8=raw_points.decode(),sha256=sha256(raw_points).hexdigest(),native_interval_minutes=15),
                conversion=dict(method='research_piecewise_linear_points_exact_means_v1',source=SOURCE,uses_future_actual=False),
                assumption=dict(kind='research_assumption',authored_at=available.isoformat(),available_at=available.isoformat(),
                    ledger_utf8=raw_ledger.decode(),ledger_sha256=sha256(raw_ledger).hexdigest(),gas_applicability_hypothesis='Single-equivalent gas-unit research model; no operational confirmation'),
                parameters=p,parameter_sources={k:SOURCE for k in p},preceding_state_started_at=previous.isoformat(),
                notification_at=max(available,previous).isoformat() if kind=='startup' else None,notice_required_h=.5 if kind=='startup' else 0.,
                reserve_policy='fixed_injection_no_flexible_reserve_or_agc',cost_basis=ledger['thermal']['costs'],
                units=dict(power='MW',stable_ramp='MW/h',curve_ramp='MW/min',residence_and_notice='h',fixed_cost='yuan/h'),
                stable_rate_origin=dict(unit='MW/min',up=float(unit['ramp']),down=float(unit['ramp']),source=SOURCE+'; original unit ramp'),
                minimum_residence_policy='shutdown_curve_start_conservative',operating_policy_source=SOURCE,steps=steps)
            raw=canonical_bytes(record);records.append(dict(artifact=raw,sha256=sha256(raw).hexdigest()))
            previous=at if kind=='startup' else at+timedelta(minutes=duration)
    book=dict(schema_version=RESEARCH_SCHEMA,run_id=run_id,source=SOURCE,records=tuple(records))
    validate_book(book,cutoff=start-timedelta(minutes=15));return book


def create_scenario(root,day_start='2026-11-11T00:00:00+08:00',seed=20261111):
    """Read the six static inputs; return portable typed/byte research bundle."""
    start=datetime.fromisoformat(day_start) if isinstance(day_start,str) else day_start
    if start.tzinfo is None or start.utcoffset() is None:raise ValueError('explicit timezone-aware day_start required')
    start=start.astimezone(ZoneInfo('Asia/Shanghai'))
    if start.hour or start.minute or start.second or start.microsecond:raise ValueError('Shanghai midnight required')
    if type(seed) is not int:raise ValueError('explicit integer seed required')
    raw,data=_load_static(root);u=data['units'];ledger=_ledger(start,seed,raw)
    ledger['thermal_energy_price']={x['id']:data['bids']['units'][x['id']]['segments'][0]['price'] for x in u['thermal']}
    run_id=f'IEEE39-RESEARCH-{start.date().isoformat()}-{seed}'
    artifacts={'static:'+k:v for k,v in raw.items()};static_refs=[source_ref(k,v) for k,v in artifacts.items()]
    resources={'load_mw':{f'load:{x["bus"]}':dict(bus=x['bus'],scale_mw=float(x['load_mw'])*float(data['base']['load_scale'])) for x in data['buses']},
        'wind_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='wind'},
        'solar_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='pv'},
        'boundary_tie_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['pmax'])) for x in u['tie']}}
    inventories={}
    for f,r in resources.items():
        result=make_prior_snapshot(family=f,resources=r,day_start=start,seed=seed,static_refs=static_refs,static_artifacts=artifacts)
        artifacts.update(result['source_artifacts']);inventories[f]=dict(resource_ids=list(r),snapshot_refs=[result['snapshot_ref']])
    book=_thermal_book(start,u,ledger,run_id)
    bundle=dict(schema_version='gd.october.ieee39_research_scenario.v1',day_start=start.isoformat(),seed=seed,run_id=run_id,
        static=data,static_artifacts=raw,assumption_ledger=canonical_bytes(ledger),assumption_sha256=sha256(canonical_bytes(ledger)).hexdigest(),
        source_artifacts=artifacts,resources=resources,inventories=inventories,thermal_book=book,
        requirements=rolling_day_requirements(start))
    # Explicit discrete five-minute plan and separate boundary rows including
    # pre-day seeds and next-day tail. These are hypotheses, not observations.
    bundle['blueprint']=_blueprint(bundle)
    bundle['terminal_boundaries']=[dict(window_index=i,at=_stamp(start,15*i+120),
        battery_energy_mwh={x['id']:float(x['ecap'])*float(x['soc0']) for x in u['storage']},
        pumped_m3={x['id']:dict(upper=float(x['vup0'])*10000,lower=(float(x['v_total'])-float(x['vup0']))*10000) for x in u['pumped']},
        source_kind='research_assumption',source=SOURCE+'; explicitly declared per-window idle/off inventory target') for i in range(96)]
    bundle['coverage']=assess_multiday_forecast_inputs(day_start=start,inventories=inventories,source_artifacts=artifacts)
    from .gd_october_job_codec import dumps
    bundle['scenario_content_sha256']=sha256(dumps(bundle)).hexdigest()
    return bundle


def _blueprint(bundle):
    start=datetime.fromisoformat(bundle['day_start']);units=bundle['static']['units'];records=validate_book(bundle['thermal_book'])
    state={g['id']:dict(online=int(g['init']['online']),power_mw=float(g['init']['p0']),state_age_h=float(g['init']['hours'])-.25) for g in units['thermal']}
    rows=[];boundaries={};at=start-timedelta(minutes=15)
    # Original initial thermal powers are intentionally retained for coal.
    # Stable gas after startup stays at Pmin, allowing exact shutdown entry.
    for i in range(312):
        boundaries[at.isoformat()]=deepcopy(state);powers={};online={}
        for g in units['thermal']:
            uid=g['id'];match=_active(records,uid,at)
            power=match[2]['mean_mw'] if match else state[uid]['power_mw']
            connected=1 if match else state[uid]['online']
            state[uid]=advance_thermal(state[uid],unit=uid,power=power,online=connected,records=records,start=at,end=at+FIVE)
            powers[uid]=power;online[uid]=connected
        rows.append(dict(start=at.isoformat(),end=(at+FIVE).isoformat(),thermal_power_mw=powers,thermal_online=online,
            battery_modes={x['id']:'idle' for x in units['storage']},pumped_modes={f'{x["id"]}_{j+1}':'off' for x in units['pumped'] for j in range(x['n_units'])},
            source_kind='research_assumption',source=SOURCE))
        at+=FIVE
    boundaries[at.isoformat()]=deepcopy(state)
    return dict(rows=rows,boundaries=boundaries,pre_day_seed_rows=deepcopy(rows[:3]),
        first_delivery_boundary={g['id']:dict(online=int(g['init']['online']),power_mw=float(g['init']['p0']),state_age_h=float(g['init']['hours'])) for g in units['thermal']},
        first_delivery_boundary_source='config/units.yaml original init is separately declared as the hypothetical first delivery state, not copied from a simulated receipt',
        first_delivery_boundary_kind='independent_hypothetical_initial_not_execution_or_telemetry',
        source=SOURCE,operator_confirmation=None,actual_execution=None)


def _storage(bundle,start):
    units=bundle['static']['units'];bids=bundle['static']['bids']['units'];cut=start-timedelta(minutes=15);day0=datetime.fromisoformat(bundle['day_start'])
    age=24+(start-day0).total_seconds()/3600
    def initial(mode):return dict(as_of=start.isoformat(),available_at=cut.isoformat(),mode=mode,net_power_mw=0.,mode_elapsed_h=age,kind='causal_plan_projection',source=SOURCE+'; hypothetical idle/off projection')
    def plan(mode,battery=False):return dict(preceding_mode=mode,modes=_s(mode),published_at=_stamp(day0,-30),source=SOURCE,
        transition_authorizations={},minimum_mode_h={m:0. for m in (('charge','discharge','idle') if battery else ('generate','pump','off'))})
    def weighted(rows):return sum((r['p_end']-r['p_start'])*r['price'] for r in rows)/sum(r['p_end']-r['p_start'] for r in rows)
    batteries={}
    days=tuple(dict.fromkeys((start+timedelta(minutes=5*(t-1))).date().isoformat() for t in PERIODS))
    for u in units['storage']:
        e=float(u['ecap']);initial_e=e*float(u['soc0']);power=float(u['pmax']);bid=bids[u['id']]
        batteries[u['id']]=_source_record(dict(bus=str(u['bus']),energy_capacity_mwh=e,roundtrip_efficiency=float(u['eta_c'])*float(u['eta_d']),
            initial_energy_mwh=initial_e,final_energy_mwh=initial_e,final_energy_at=(start+timedelta(hours=2)).isoformat(),
            soc_conversion=dict(input_unit='percent',initial=100*float(u['soc0']),final=100*float(u['soc0']),source=SOURCE),
            energy_min_mwh=_s(e*float(u['soc_min'])),energy_max_mwh=_s(e*float(u['soc_max'])),
            charge_min_mw=_s(0.),charge_max_mw=_s(power),discharge_min_mw=_s(0.),discharge_max_mw=_s(power),
            charge_bid_yuan_per_mwh=_s(weighted(bid['ch_segments'])),discharge_bid_yuan_per_mwh=_s(weighted(bid['dis_segments'])),
            cycle_limit=1.,cycle_throughput_used_mwh=0.,cumulative_throughput_used_mwh=0.,cycle_budget_id=days[0],cycle_budget_scope='operating_day_Asia_Shanghai',
            cycle_budgets={day:dict(limit_cycles=1.,used_throughput_mwh=0.,source=SOURCE) for day in days},
            actual_initial=initial('idle'),effective_plan=plan('idle',True),reserve_eligibility='excluded_unverified',
            agc_award=dict(status='explicitly_no_award',up_mw=_s(0.),down_mw=_s(0.),source=SOURCE),
            ramp_up_mw_per_h=_s(power*12),ramp_down_mw_per_h=_s(power*12),
            boundary_interpretation='Explicit hypothetical per-window target at named end boundary, not a daily reset'))
    pumped={}
    for p in units['pumped']:
        bid=bids[p['id']];us={}
        for j in range(p['n_units']):
            us[f'{p["id"]}_{j+1}']=_source_record(dict(bus=str(p['bus']),pump_power_mw=_s(float(p['unit_pump'])),generation_min_mw=_s(float(p['unit_gen_min'])),generation_max_mw=_s(float(p['unit_gen_max'])),
                pump_m3_per_mwh=_s(1000/float(p['e_pump'])),generation_m3_per_mwh=_s(1000/float(p['e_gen'])),
                pump_bid_yuan_per_mwh=_s(weighted(bid['pump_segments'])),generation_bid_yuan_per_mwh=_s(weighted(bid['gen_segments'])),
                actual_initial=initial('off'),effective_plan=plan('off'),agc_up_mw=_s(0.),agc_down_mw=_s(0.),agc_boundary_interpretation=SOURCE,
                operating_reserve_eligible=_s(False),primary_eligible=_s(False),primary_fraction=_s(0.),
                ramp_up_mw_per_h=_s(float(p['unit_gen_max'])*12),ramp_down_mw_per_h=_s(float(p['unit_gen_max'])*12),
                standby_timing_model='preparation_after_minimum_off',standby_available_mw=_s(0.),standby_preparation_h=_s(0.),contingency_response_h=_s(1/6)))
        upper=float(p['vup0'])*10000;lower=(float(p['v_total'])-float(p['vup0']))*10000
        pumped[p['id']]=_source_record(dict(units=us,upper_initial_m3=upper,lower_initial_m3=lower,
            upper_min_m3=_s(float(p['vup_min'])*10000),upper_max_m3=_s(float(p['vup_max'])*10000),lower_min_m3=_s(float(p['vdn_min'])*10000),lower_max_m3=_s(float(p['vdn_max'])*10000),
            da_upper_target_m3=_s(upper),da_lower_target_m3=_s(lower),upper_band_m3=_s(0.),lower_band_m3=_s(0.),
            penalty_profile=dict(status='specified',unit='yuan/(m3*h)',time_index='per_5min_end',values={s:_s(2.) for s in SL_SIDES},source=SOURCE,interpretation_source='Explicit synthetic reservoir penalty 2 yuan/(m3*h), not inferred official MP'),
            contingency_eligible=_s(False),contingency_limit_mw=_s(0.),state_source=dict(as_of=start.isoformat(),available_at=cut.isoformat(),kind='causal_plan_projection',source=SOURCE),slack_time_index='per_5min_end'))
    return dict(schema_version=STORAGE_SCHEMA,run_id=bundle['run_id'],window_start=start.isoformat(),information_cutoff=cut.isoformat(),batteries=batteries,pumped=pumped,units=deepcopy(UNITS),source=SOURCE)


def _model_inputs(bundle,start,forecast):
    static=bundle['static'];units=static['units'];buses=tuple(x['bus'] for x in static['buses']);initial=bundle['blueprint']['boundaries'][start.isoformat()]
    schedule={x['start']:x for x in bundle['blueprint']['rows']};records=validate_book(bundle['thermal_book']);thermal={};commitment={};reserve_units={};agc={}
    for u in units['thermal']:
        g=u['id'];bid=static['bids']['units'][g];pmin=float(u['pmin']);segments=[];base_cost=0.
        for i,s in enumerate(bid['segments']):
            base_cost+=max(0,min(pmin,float(s['p_end']))-float(s['p_start']))*float(s['price'])
            width=float(s['p_end'])-max(pmin,float(s['p_start']))
            if width>0:segments.append(dict(key=f'segment-{i}',width_mw=width,price_yuan_per_mwh=_s(float(s['price']))))
        row=initial[g]
        thermal[g]=dict(bus=str(u['bus']),bid_base_mw=pmin,base_bid_yuan_per_h=_s(base_cost),segments=tuple(segments),min_mw=_s(pmin),max_mw=_s(float(u['pmax'])),
            ramp_up_mw_per_h=_s(float(u['ramp'])*60),ramp_down_mw_per_h=_s(float(u['ramp'])*60),startup_output_limit_mw=_s(pmin),shutdown_output_limit_mw=_s(pmin),
            startup_yuan=_s(float(bid['startup_cost_yuan'])),shutdown_yuan=_s(0.),min_up_h=float(u['ut']),min_down_h=float(u['dt']),
            initial_online=row['online'],initial_power_mw=row['power_mw'],initial_state_age_h=row['state_age_h'],
            initial_state_kind=initial_trajectory_kind(bundle['thermal_book']) if 'trajectory' in row else 'stable',
            status=_s('free'),fixed_power_mw=_s(None),max_starts=2 if g=='GT' else 1,max_stops=2 if g=='GT' else 1,
            transition_model='instant_with_output_limits',terminal_policy='carry_out',source=SOURCE+'; static inventory/nameplate retained')
        eligible={}
        for t in PERIODS:
            at=start+(t-1)*FIVE;commitment[g,t]=schedule[at.isoformat()]['thermal_online'][g]
            eligible[t]=int(bool(commitment[g,t]) and _active(records,g,at) is None)
        reserve_units[g]=dict(primary_eligible=eligible,contingency_eligible=deepcopy(eligible),primary_fraction=_s(.06 if u['type']=='coal' else .04),
            contingency_class='nonfixed',ramp_10min_mw=_s(float(u['ramp'])*10),sources={k:SOURCE for k in ('primary_eligible','primary_fraction','contingency_eligible','contingency_class','ramp_10min_mw')})
        agc[g]=dict(up_mw=_s(0.),down_mw=_s(0.),basis='explicit_no_reservation',source=SOURCE)
    lines={}
    for l in static['branches']:
        rate=float(l['rate_mw'])*float(static['base']['line_capacity_multiplier'])*float(static['base'].get('line_scale',{}).get(l['branch_id'],1))
        lines[l['branch_id']]=dict(from_bus=l['from_bus'],to_bus=l['to_bus'],susceptance_mw_per_rad=100/float(l['x_pu']),lower_mw=_s(-rate),upper_mw=_s(rate),source=SOURCE+'; original rate multiplied once')
    load={t:sum(r['value'] for r in forecast['load_mw']['rows'] if r['delivery_start']==_stamp(start,(t-1)*5)) for t in PERIODS}
    names=('positive_mw','negative_mw','primary_mw','contingency_mw','positive_network_deduction_mw','contingency_deduction_mw','negative_demand_mw')
    requirements={k:_s(float(static['modes']['MKT']['reserve']['fixed_up_mw']) if k in ('positive_mw','contingency_mw') else 0.) for k in names};requirements['negative_demand_mw']=load
    reserves=dict(rule_version=RULE_VERSION,requirements=requirements,thermal=reserve_units,load_side={},
        sources={k:SOURCE for k in names+('sharing_policy','negative_demand_basis','capacity_qualification_basis')},sharing_policy=RESERVE_SHARING,
        contingency_slack=dict(policy='hard',penalty_yuan_per_mw_h=None,source=SOURCE),
        renewable_capacity={b:dict(participation='excluded_unverified',positive_mw=_s(0.),negative_mw=_s(0.),source=SOURCE,qualification_source=SOURCE) for b in buses})
    return dict(buses=buses,thermal=thermal,lines=lines,sections={},reference_buses=('39',),commitment=commitment,reserves=reserves,
        agc=dict(thermal=agc,boundary_interpretation_source=SOURCE),storage_spec=_storage(bundle,start),
        thermal_trajectories=dict(book=deepcopy(bundle['thermal_book']),window_start=start.isoformat(),information_cutoff=_stamp(start,-15)),
        implementation_sources={k:SOURCE for k in ('effective_commitment','scope_exclusions','objective_units','network_translation','dispatch_penalties','renewable_fixed_boundary')},
        balance_policy='hard',shortfall_cap_policy='unbounded_slack',renewable_mode='fixed_exogenous',renewable_dispatch_profile=None,
        renewable_bid_yuan_per_mwh={(b,t):0. for b in buses for t in PERIODS},renewable_curtailment_penalty_yuan_per_mwh={(b,t):0. for b in buses for t in PERIODS},
        penalties_yuan_per_mwh=dict(line=1000.,section=1000.,balance_shortfall=10000.,balance_surplus=10000.),
        source_notes={k:SOURCE for k in ('thermal','transition','network','penalties','exogenous','boundary_basis','balance_policy','shortfall_cap_policy','renewable_policy')})


def window_inputs(bundle,index):
    """Source-bound real bridge inputs. No solve or fictional issued receipts."""
    from .gd_october_rt_contract import SCHEMA_VERSION,PROFILE,MAPPING_SCHEMA,content_sha256,RULE_VERSION as CONTRACT_RULE
    from .gd_october_input_bridge import MAPPING_SCHEMA as PHYSICAL_MAPPING,PHYSICAL_SOURCES
    validate_scenario(bundle)
    if type(index) is not int or not 0<=index<96:raise ValueError('window index must be 0..95')
    start=datetime.fromisoformat(bundle['day_start'])+timedelta(minutes=15*index);cut=_stamp(start,-15)
    artifacts=dict(bundle['source_artifacts']);artifacts['assumptions']=bundle['assumption_ledger']
    known_raw=canonical_bytes(dict(kind='research_assumption',source=SOURCE,plan='research-explicit-plan',
        description='Known hypothetical schedule identity, not solved or executed dispatch',ledger_sha256=bundle['assumption_sha256']))
    artifacts['hypothetical-plan']=known_raw;identity=dict(run_id=bundle['run_id'],window_id=f'window-{index:03d}',selected_dispatch_id='research-explicit-plan',selected_dispatch_sha256=sha256(known_raw).hexdigest())
    refs=[];specs=[];forecasts={};sced_refs={}
    for stage in ('sced','lmp'):
        for family,inventory in bundle['inventories'].items():
            result=build_forecast_series(snapshot_refs=inventory['snapshot_refs'],source_artifacts=artifacts,identity=identity,family=family,
                resource_ids=inventory['resource_ids'],delivery_start=start,information_cutoff=start-timedelta(minutes=15),stage=stage)
            artifacts.update(result['source_artifacts']);sid=stage+':'+family;artifacts[sid]=result['series_document'];ref=source_ref(sid,artifacts[sid]);refs.append(ref)
            specs.append(dict(stage=stage,family=family,unit='MW',resource_ids=inventory['resource_ids'],absence_source=None))
            if stage=='sced':forecasts[family]=result['series'];sced_refs[family]=ref
    mapping=dict(schema_version=MAPPING_SCHEMA,identity=identity,delivery_start=start.isoformat(),source_uri=SOURCE,published_at=cut,available_at=cut,
        selected_dispatch_available_at=_stamp(datetime.fromisoformat(bundle['day_start']),-30),state_mapping='Explicit hypothetical fixed plan, no official mapping',power_mapping='Research temporal correspondence only; no price mapping',certified=False,
        inventory_boundary_mapping=dict(operating_day_initial='Explicit original static inventory hypothetical seed',rolling_initial='Hypothetical blueprint, not executed state',window_terminal='Explicit idle/off inventory target for this window',source=SOURCE),
        rows=[dict(lmp_delivery_start=_stamp(start,15*i),sced_delivery_starts=[_stamp(start,15*i+5*j) for j in range(3)]) for i in range(8)])
    artifacts['time-map']=canonical_bytes(mapping)
    contract=dict(schema_version=SCHEMA_VERSION,profile=PROFILE,rule_version=CONTRACT_RULE,timezone='Asia/Shanghai',identity=identity,delivery_start=start.isoformat(),active_sced_start_index=0,
        timing_declaration=dict(basis='external_engineering_declaration',source=SOURCE,description='Research T-15 snapshot/24x5 clock, no market authenticity claim',interval_convention='left_closed_right_open'),
        information_cutoff=dict(sced=cut,lmp=_stamp(start,-10)),required_series=specs,series_sources=refs,selected_dispatch_source=source_ref('hypothetical-plan',known_raw),mapping_source=source_ref('time-map',artifacts['time-map']),content_sha256='')
    contract['content_sha256']=content_sha256(contract)
    core=_model_inputs(bundle,start,forecasts);rows=[];occupied=set()
    for f,resources in bundle['resources'].items():
        for rid,r in resources.items():
            rows.append(dict(family=f,source_resource_id=rid,model_resource_id=f+':'+rid,bus=r['bus'],series_source=sced_refs[f],source=SOURCE,
                minimum_mw=0.,maximum_mw=r['scale_mw']*(1.2 if f=='load_mw' else 1.),bounds_source=SOURCE))
            occupied.add((f,r['bus']))
    resource_map=dict(schema_version=PHYSICAL_MAPPING,identity=identity,delivery_start=start.isoformat(),delivery_end=_stamp(start,120),
        provenance=dict(source=SOURCE,published_at=cut,available_at=cut),quantity_semantics='balance_scope_load_separate_modelled_injections_positive_tie_import',
        load_accounting=dict(balance_load_scope='Original IEEE39 nodal load scaled once, new exogenous shape; not provincial gross load',local_generation_accounting='All modeled renewables and thermal remain separate injections',
            local_generation_source=SOURCE,negative_reserve_demand_basis='Same non-netted model nodal load sum, explicit research choice',negative_reserve_demand_source=SOURCE,modelled_injections_already_deducted=False),
        aggregation_policy='sum_mw_at_declared_bus_no_scaling',rows=rows,
        absent_bus_families=[dict(family=f,bus=b,source=SOURCE+'; explicit original inventory absence') for f in bundle['resources'] for b in core['buses'] if (f,b) not in occupied])
    artifacts['physical-map']=canonical_bytes(resource_map)
    physical=dict(identity=identity,period_intervals=[dict(period=t,delivery_start=_stamp(start,5*(t-1)),delivery_end=_stamp(start,5*t)) for t in PERIODS],
        sources={k:dict(source=SOURCE,published_at=cut,available_at=cut) for k in PHYSICAL_SOURCES},state_schedule=dict(method='explicit_5min_effective_schedule',is_measurement=False,source=SOURCE+'; hypothetical scenario schedule'),
        initial_state=dict(at=start.isoformat(),kind='synthetic_research_state',source=SOURCE),terminal_state=dict(at=_stamp(start,120),kind='explicit_window_terminal_boundary',source=SOURCE),model_inputs=core)
    return dict(contract_document=canonical_bytes(contract),source_artifacts=artifacts,expected_identity=identity,expected_delivery_start=start.isoformat(),asof=start.isoformat(),mode='research',physical_inputs=physical,resource_mapping_ref=source_ref('physical-map',artifacts['physical-map']))


def build_window(bundle,index):
    from .gd_october_input_bridge import build_october_model_from_contract
    return build_october_model_from_contract(**window_inputs(bundle,index))


def export_scenario(bundle,output_dir,build_indices=(0,23,24,25,35,36,47,48,63,64,79,80,87,88,95)):
    """Write deterministic original bytes, all 96 input coverage, and build audit."""
    from .gd_october_job_codec import dumps
    import pyomo.environ as pyo
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=False);files={}
    def write(name,raw):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);files[name]=dict(sha256=sha256(raw).hexdigest(),bytes=len(raw))
    write('scenario.codec.json',dumps(bundle));write('assumptions.json',bundle['assumption_ledger']);write('coverage.json',canonical_bytes(bundle['coverage']))
    write('explicit_plan_and_pre_day_seeds.json',canonical_bytes(bundle['blueprint']))
    write('explicit_terminal_boundaries.json',canonical_bytes(bundle['terminal_boundaries']))
    for n,(sid,raw) in enumerate(sorted(bundle['source_artifacts'].items())):write(f'sources/{n:03d}.bin',raw)
    builds=[]
    for index in build_indices:
        m=build_window(bundle,index)
        builds.append(dict(index=index,buses=len(m.B),branches=len(m.L),thermal=len(m.G),periods=len(m.T),
            variables=sum(1 for _ in m.component_data_objects(pyo.Var)),constraints=sum(1 for _ in m.component_data_objects(pyo.Constraint,active=True)),
            mathematical_sha256=m._gd_october_signature,thermal_curve_cells=len(m.c_thermal_curve),
            research_thermal_assumptions=m._gd_october_scope['research_thermal_assumptions'],
            constant_positive_reserve_screen=constant_positive_reserve_screen(m),
            solver_called=False,numerical_feasibility_verified=False))
    report=dict(schema_version='gd.october.ieee39_research_build.v1',status='research_inputs_and_unsolved_models_built',
        forecast_coverage_complete=bundle['coverage']['forecast_coverage_complete'],checked_window_family_pairs=len(bundle['coverage']['windows']),
        window_count=96,first_delivery=bundle['day_start'],last_forecast_end=bundle['blueprint']['rows'][-1]['end'],
        inventory=dict(buses=39,branches=46,thermal=5,wind=2,solar=3,batteries=5,pumped_plants=1,pumped_units=2,physical_tie=1),
        assumption_sha256=bundle['assumption_sha256'],builds=builds,operator_confirmation=None,operational_source_verified=False,
        solver_called=False,whole_day_feasibility_verified=False,native_day_ready=False,official_market_accepted=False,official_price=None,settlement=None,cash=None,
        remaining_gaps=json.loads(bundle['assumption_ledger'])['limitations'],source_files=files)
    write('build_report.json',canonical_bytes(report));write('manifest.json',canonical_bytes(files));return report


def validate_scenario(bundle):
    """Mutable convenience dictionaries cannot silently diverge from source bytes."""
    from .gd_october_job_codec import dumps
    content={k:v for k,v in bundle.items() if k!='scenario_content_sha256'}
    if sha256(dumps(content)).hexdigest()!=bundle.get('scenario_content_sha256'):
        raise ValueError('research scenario content changed from declared source-bound bundle')
    raw=bundle['static_artifacts'];data=bundle['static'];ledger=json.loads(bundle['assumption_ledger'])
    if sha256(bundle['assumption_ledger']).hexdigest()!=bundle['assumption_sha256']:
        raise ValueError('assumption ledger hash mismatch')
    if set(raw)!=set(STATIC_FILES) or ledger['static_sources']!={k:sha256(v).hexdigest() for k,v in raw.items()}:
        raise ValueError('static source hash/inventory mismatch')
    expected=dict(base=yaml.safe_load(raw['config/base.yaml']),modes=yaml.safe_load(raw['config/modes.yaml']),units=yaml.safe_load(raw['config/units.yaml']),
        buses=list(csv.DictReader(io.StringIO(raw['data/case39/buses.csv'].decode()))),
        branches=list(csv.DictReader(io.StringIO(raw['data/case39/branches.csv'].decode()))),bids=json.loads(raw['resolved_bids.json']))
    if expected!=data:raise ValueError('static numeric data differs from original source bytes')
    for d in validate_book(bundle['thermal_book']):
        if d['assumption']['ledger_sha256']!=bundle['assumption_sha256']:raise ValueError('thermal assumptions differ from scenario ledger')
    return True


def build_native_window(bundle,index):
    """Construct source-bound native-input wrapper only; never call a solver."""
    from .gd_october_native_inputs import build_october_native_model
    from .gd_october_run_identity import KnownDispatchReference
    k=window_inputs(bundle,index);identity=k['expected_identity'];raw=k['source_artifacts']['hypothetical-plan'];at=_stamp(datetime.fromisoformat(bundle['day_start']),-30)
    known=KnownDispatchReference(role='effective_baseline_dispatch',run_id=identity['run_id'],window_id=identity['window_id'],dispatch_id=identity['selected_dispatch_id'],
        source_id='hypothetical-plan',source_sha256=sha256(raw).hexdigest(),artifact=raw,source=SOURCE+'; explicit hypothetical baseline, not operational effective dispatch',published_at=at,available_at=at)
    return build_october_native_model(known_dispatch_reference=known,solve_intent=dict(purpose='unsolved_research_input_applicability',
        research_assumptions_sha256=bundle['assumption_sha256'],operator_authority=None,requested_relative_gap=.001),**k)


def constant_positive_reserve_screen(model):
    """A violated fixed-capacity inequality is a structural contradiction, not a solve.

    Applies only if the expression has no free variables. Passing it says
    nothing about energy, lines, ramps or joint feasibility.
    """
    import pyomo.environ as pyo
    from pyomo.core.expr.visitor import identify_variables
    margins=[];violations=[]
    for t in model.T:
        row=model.c_positive_reserve[t]
        if any(not v.fixed for v in identify_variables(row.body)):
            return dict(status='not_constant',feasibility_verified=False)
        margin=float(pyo.value(row.body)-pyo.value(row.lower)) if row.lower is not None else float(pyo.value(row.upper)-pyo.value(row.body))
        margins.append(margin)
        if margin < -1e-7:violations.append(dict(period=int(t),margin_mw=margin))
    return dict(status='structural_infeasibility_witness' if violations else 'this_necessary_condition_only_passes',
        minimum_margin_mw=min(margins),violations=violations,feasibility_verified=False,
        explanation='Fixed online capacity + declared qualified contributions must cover non-netted load plus unchanged 400 MW; no positive-reserve slack exists.')
