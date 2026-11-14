"""R12 source-bound IEEE39 battery-energy-only DA research adapter.

R11 is unchanged. Preparation is pure and never starts a solver. Every menu,
clock, physical assumption and exact source bid is reproducibly reconstructed.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import csv
import io
import json
from itertools import product

from .gd_da_candidate import compile_path, digest, research_horizon
from .gd_da_ieee39 import STATIC_SHA256, _prepare as _prepare_r11, raw
from .gd_october_ieee39_scenario import _load_static, STATIC_FILES

SCHEMA = 'gd.research.da_candidate_battery.v1'
PREPARED = 'prepared_D00_research'
BOUNDARIES = ('original_D00_research', 'synthetic_lead_in_research', PREPARED)
ASSUMPTIONS = {
    'base_zero_delay': dict(ramp_fraction_per_5min=1., preparation_h=0., available=True),
    'delayed_30min': dict(ramp_fraction_per_5min=1., preparation_h=.5, available=True),
    'restrictive_ramp_10pct': dict(ramp_fraction_per_5min=.1, preparation_h=0., available=True),
    'unavailable': dict(ramp_fraction_per_5min=1., preparation_h=0., available=False),
}
# Published before any experiment, not fitted to a solver result.
THERMAL_MENU = dict(shutdown_clock_hours=(6, 8, 10, 12, 18, 23),
    restart_clock_hours=(12, 15, 18), originally_off_start_clock_hours=(6, 12, 18),
    originally_off_boundary_start_delay_minutes=30,
    max_starts=1, max_stops=1, coal_policy='original_stable_only',
    selection='all chronological combinations satisfying declared minimum residence and complete curves; never filter for dispatch feasibility')


def _bind(c, key, artifact, kind, available):
    c['sources'][key] = dict(artifact=artifact, sha256=sha256(artifact).hexdigest(),
        kind=kind, available_at=available)


def _static_from_bytes(sr):
    import yaml
    if set(sr) != set(STATIC_FILES):
        raise ValueError('static inventory mismatch')
    return dict(base=yaml.safe_load(sr['config/base.yaml']),
        modes=yaml.safe_load(sr['config/modes.yaml']), units=yaml.safe_load(sr['config/units.yaml']),
        buses=list(csv.DictReader(io.StringIO(sr['data/case39/buses.csv'].decode()))),
        branches=list(csv.DictReader(io.StringIO(sr['data/case39/branches.csv'].decode()))),
        bids=json.loads(sr['resolved_bids.json']))


def prepare_ieee39_battery_da(root, *, boundary_kind='original_D00_research',
        day_start='2026-11-11T00:00:00+08:00', seed=20261111,
        generated_at_utc=None, battery_assumption_id='base_zero_delay', prepared_boundary=None):
    sr, static = _load_static(root)
    return _prepare(sr, static, boundary_kind, day_start, seed,
        generated_at_utc or datetime.now(timezone.utc).isoformat(),
        battery_assumption_id, prepared_boundary)


def _prepare(sr, static, kind, day_start, seed, generated, assumption_id, prepared):
    if kind not in BOUNDARIES or assumption_id not in ASSUMPTIONS:
        raise ValueError('unrecognized predeclared battery scenario')
    if (kind == PREPARED) != (prepared is not None):
        raise ValueError('prepared hypothesis requires its own certified equilibrium report only')
    b = _prepare_r11(sr, static,
        'original_D00_research' if kind == PREPARED else kind, day_start, seed, generated)
    c = b['contract']; c['schema'] = SCHEMA; c['boundary_kind'] = kind
    c['experiment_id'] = f'R12-IEEE39-{kind}-{day_start[:10]}-{seed}-{assumption_id}'
    day = datetime.fromisoformat(day_start); boundary = datetime.fromisoformat(c['boundary_at'])
    declared = (day - timedelta(days=1)).isoformat(); ts = b['energy_inputs']['horizon'].ids
    if prepared is not None:
        from .gd_da_prepared_boundary import validate_boundary_report
        validate_boundary_report(prepared)
        # This check is independent of the report's own certificate validator.
        hypothesis = prepared['problem']['hypothesis']
        if hypothesis['boundary_kind'] != PREPARED or hypothesis['delivery_boundary_at'] != boundary.isoformat() or prepared['output_at'] != boundary.isoformat():
            raise ValueError('prepared boundary identity/time mismatch')
        if prepared['problem']['seed'] != seed:
            raise ValueError('prepared boundary prior seed mismatch')
        if set(prepared['thermal']) != set(b['energy_inputs']['thermal']):
            raise ValueError('prepared thermal inventory mismatch')
        for g, state in prepared['thermal'].items():
            if set(state) != {'initial_online', 'initial_power_mw', 'initial_state_age_h', 'initial_state_kind', 'phase', 'residence_remaining_h'}:
                raise ValueError('prepared thermal state fields')
            b['energy_inputs']['thermal'][g].update({k:deepcopy(state[k]) for k in ('initial_online','initial_power_mw','initial_state_age_h','initial_state_kind')})
        # The certificate is data, not historical telemetry or a DA acceptance.
        from .gd_october_job_codec import dumps
        _bind(c, 'prepared_boundary_certificate', dumps(prepared), 'research_boundary_assumption', declared)
    b['prepared_boundary'] = deepcopy(prepared)
    b['battery_assumption_id'] = assumption_id
    _declare_menus(b, declared)
    assumption = dict(schema='gd.research.battery_technical_hypothesis.v1', id=assumption_id,
        **ASSUMPTIONS[assumption_id], cycle_limit_per_operating_day=1.,
        already_used_internal_mwh=0., initial_mode='idle', initial_power_mw=0.,
        initial_mode_age_h=24., initial_hour_direction=0,
        minimum_mode_h=dict(idle=0.,charge=0.,discharge=0.),
        physical_readiness_certified=False, technical_data_source='explicit unverified research hypothesis; original source omits ramp, readiness and cycle count',
        reserve_policy='zero incremental up/down/primary/contingency/AGC; actual signed energy baseline only',
        unresolved_submission='original self_schedule=None is not a certified submitted 96-point plan',
        day_end_target='unchanged original source SOC0.5 at delivery next midnight')
    _bind(c, 'battery_hypothesis', raw(assumption), 'research_storage_assumption', declared)
    pumped = dict(policy='off_fixed_inventory_zero_credit', inventory=deepcopy(b['storage_inventory']['pumped']))
    date_keys = sorted({(boundary + timedelta(minutes=5*t)).date().isoformat() for t in ts})
    batteries = {}
    for x in static['units']['storage']:
        g=x['id']; bid=static['bids']['units'][g]; cap=float(x['ecap']); power=float(x['pmax'])
        if bid['mode']!='price_quantity' or bid['self_schedule'] is not None:
            raise ValueError('unexpected original battery declaration')
        if (bid['soc_min'],bid['soc_max'],bid['soc_end']) != (x['soc_min'],x['soc_max'],x['soc_end_bid']):
            raise ValueError('configuration/bid SOC conflict')
        def curve(direction):
            segments=bid[direction+'_segments']; ends=[float(segments[0]['p_start'])]
            prices=[]
            for s in segments:
                if float(s['p_start']) != ends[-1] or float(s['p_end']) <= ends[-1]:
                    raise ValueError('source battery segment continuity')
                ends.append(float(s['p_end'])); prices.append(float(s['price']))
            if ends[0] != 0. or ends[-1] != power:
                raise ValueError('source battery segment capacity')
            if any((prices[j+1]-prices[j])*(1 if direction=='dis' else -1)<0 for j in range(len(prices)-1)):
                raise ValueError('source signed-axis monotonicity')
            return dict(breakpoints_mw=tuple(ends), prices_yuan_per_mwh=tuple(prices))
        batteries[g]=dict(bus=str(x['bus']),energy_capacity_mwh=cap,
            roundtrip_efficiency=float(x['eta_c'])*float(x['eta_d']),
            initial_energy_mwh=cap*float(x['soc0']), energy_min_mwh=cap*float(x['soc_min']),
            energy_max_mwh=cap*float(x['soc_max']), terminal_energy_mwh=cap*float(x['soc_end_bid']),
            terminal_at=(day+timedelta(days=1)).isoformat(),charge_max_mw=power,discharge_max_mw=power,
            initial_power_mw=0.,initial_mode='idle',initial_mode_age_h=24.,initial_hour_direction=0,
            ramp_up_mw_per_h=power*12*assumption['ramp_fraction_per_5min'],
            ramp_down_mw_per_h=power*12*assumption['ramp_fraction_per_5min'],
            preparation_h=dict(charge=assumption['preparation_h'],discharge=assumption['preparation_h']),
            minimum_mode_h=dict(idle=0.,charge=0.,discharge=0.),
            availability={t:assumption['available'] for t in ts},charge_curve=curve('ch'),discharge_curve=curve('dis'),
            cycle_budget={d:dict(limit_cycles=1.,used_internal_mwh=0.) for d in date_keys},
            notice_available_at=c['planned_publication_at'])
    if tuple(batteries) != ('ES1','ES2','ES3','ES4','ES5'):
        raise ValueError('exact five original batteries required')
    c['storage']=dict(policy='battery_energy_only_zero_incremental_reserve',
        inventory_sha256=sha256(raw(b['storage_inventory'])).hexdigest(),
        battery_parameters_source_id='battery_parameters',pumped_storage=pumped)
    _bind(c,'battery_parameters',raw(dict(batteries=batteries,pumped_storage=pumped)),
        'research_storage_assumption',declared)
    boundary_doc=dict(kind=kind,at=boundary.isoformat(),thermal={g:{k:d[k] for k in
        ('initial_online','initial_power_mw','initial_state_age_h','initial_state_kind')} for g,d in b['energy_inputs']['thermal'].items()},
        storage=b['storage_inventory'], provenance=('independent source-bound hypothetical equilibrium; not original telemetry or reachable prehistory' if prepared is not None else
        'unchanged original numerical D00 assumption' if kind=='original_D00_research' else 'unchanged separate synthetic21:00 assumption; not shifted D00 telemetry'))
    _bind(c,'boundary',raw(boundary_doc),'research_boundary_assumption',declared)
    b['batteries']=batteries
    b['qualifications']['storage']='five original batteries energy only; actual signed baseline; zero incremental reserves/AGC; original PS off inventory'
    b['qualifications']['submission']='research only: original self_schedule=None; no certified96-point storage declaration'
    b['qualifications']['prepared_boundary_evidence']=('portable independent optimality and finite feasibility only; native execution authenticated=False; live permit required for prepared model construction' if prepared is not None else 'original or separate synthetic numerical initial assumption; no authenticated telemetry')
    from .gd_da_battery_candidate import validate_contract
    validate_contract(c)
    return b


def _declare_menus(b, available):
    c=b['contract']; day=datetime.fromisoformat(c['day_start']); boundary=datetime.fromisoformat(c['boundary_at'])
    ts=b['energy_inputs']['horizon'].ids; count=len(ts)
    doc=json.loads(c['sources']['curves']['artifact'])
    doc['menu']=THERMAL_MENU
    _bind(c,'curves',raw(doc),'research_curve_assumption',available)
    b['menu_declaration']=deepcopy(THERMAL_MENU)
    by_id={x['id']:x for x in b['static']['units']['thermal']}
    menus={}
    for g,d in b['energy_inputs']['thermal'].items():
        x=by_id[g]; minimum=float(x['pmin']); bid=b['static']['bids']['units'][g]
        d['max_starts']=1; d['max_stops']=1
        menus[g]=[dict(id='stable' if d['initial_online'] else 'off',events=())]
        if x['type']=='coal': continue
        def event(kind,at):
            duration=doc['durations_minutes'][g][kind]; n=duration//5; steps=[]
            for j in range(n):
                a,z=minimum*j/n,minimum*(j+1)/n
                if kind=='shutdown': a,z=minimum-a,minimum-z
                mean=(a+z)/2
                steps.append((a,mean,z,float(x['noload'])+float(bid['segments'][0]['price'])*mean))
            return dict(kind=kind,at=int((at-boundary).total_seconds()/300),steps=tuple(steps),
                notice_h=.5 if kind=='startup' else 0.,
                notification_at=(at-timedelta(minutes=30)).isoformat() if kind=='startup' else None,
                source_id='curves',curve_rate_mw_per_min=minimum/duration)
        def append(identifier,events):
            spec=dict(id=identifier,events=tuple(events))
            # Only chronological/residence/complete-curve checks are permitted.
            compile_path(d,spec,ts,c)
            menus[g].append(spec)
        if d['initial_online']:
            for q in THERMAL_MENU['shutdown_clock_hours']:
                stop=day+timedelta(hours=q); f=event('shutdown',stop)
                if d['initial_state_age_h']+(stop-boundary).total_seconds()/3600 < d['min_up_h'] or f['at']+len(f['steps'])>count: continue
                append(f'stop-{f["at"]}',(f,))
                for r in THERMAL_MENU['restart_clock_hours']:
                    start=day+timedelta(hours=r); e=event('startup',start)
                    off=stop+timedelta(minutes=5*len(f['steps']))
                    if start-off < timedelta(hours=max(d['min_down_h'],.5)) or e['at']+len(e['steps'])>count: continue
                    append(f'stop-{f["at"]}-restart-{e["at"]}',(f,e))
        else:
            starts=[boundary+timedelta(minutes=30)]+[day+timedelta(hours=q) for q in THERMAL_MENU['originally_off_start_clock_hours']]
            for start in starts:
                e=event('startup',start)
                if d['initial_state_age_h']+(start-boundary).total_seconds()/3600<d['min_down_h']: continue
                append(f'start-{e["at"]}-stay',(e,))
                for q in THERMAL_MENU['shutdown_clock_hours']:
                    stop=day+timedelta(hours=q); f=event('shutdown',stop)
                    if stop-start<timedelta(hours=d['min_up_h']) or f['at']<e['at']+len(e['steps']) or f['at']+len(f['steps'])>count: continue
                    append(f'start-{e["at"]}-stop-{f["at"]}',(e,f))
    b['candidates']=menus


def validate_ieee39_battery_da(bundle):
    c=bundle['contract']
    from .gd_da_battery_candidate import validate_contract
    validate_contract(c)
    sr={k[7:]:v['artifact'] for k,v in c['sources'].items() if k.startswith('static:')}
    expected=_prepare(sr,_static_from_bytes(sr),c['boundary_kind'],c['day_start'],bundle['seed'],
        c['generated_at_utc'],bundle['battery_assumption_id'],bundle['prepared_boundary'])
    if digest(bundle)!=digest(expected):
        raise ValueError('R12 adapter reconstruction mismatch: source, menu, vintage, assumption, boundary, bid or inventory')
    return True


def build_ieee39_battery_da(bundle, *, boundary_permit=None):
    validate_ieee39_battery_da(bundle)
    from .gd_da_battery_candidate import build_candidate_scuc
    return build_candidate_scuc(**{k:bundle[k] for k in ('contract','candidates','reserve','energy_inputs','batteries')}, boundary_permit=boundary_permit)


def portable_bundle(bundle):
    p=deepcopy(bundle)
    p['energy_inputs']['horizon']=dict(physical_count=len(bundle['energy_inputs']['horizon'].ids),factory='smm.gd_da_candidate.research_horizon')
    return p


def load_prepared_ieee39_battery_da(artifact):
    from .gd_october_job_codec import loads
    b=loads(artifact); descriptor=b['energy_inputs']['horizon']
    if set(descriptor)!={'physical_count','factory'} or descriptor['factory']!='smm.gd_da_candidate.research_horizon':
        raise ValueError('unsupported horizon factory')
    b['energy_inputs']['horizon']=research_horizon(descriptor['physical_count'])
    validate_ieee39_battery_da(b)
    return b


def necessary_battery_envelopes(bundle):
    """Optimistic simultaneous-row and energy floors, never dispatch acceptance."""
    validate_ieee39_battery_da(bundle)
    c=bundle['contract']; e=bundle['energy_inputs']; ts=e['horizon'].ids
    boundary=datetime.fromisoformat(c['boundary_at']); day=datetime.fromisoformat(c['day_start'])
    paths={g:[compile_path(d,p,ts,c) for p in bundle['candidates'][g]] for g,d in e['thermal'].items()}
    rows=[]
    for i,t in enumerate(ts):
        at=boundary+timedelta(minutes=5*i)
        net=sum(e['load_mw'][b,t]-e['renewable_mw'][b,t]-e['physical_tie_mw'][b,t] for b in e['buses'])
        lo=hi=contingency=first_reach=0.
        thermal_options=[]
        for g,d in e['thermal'].items():
            cells=[p['cells'][i] for p in paths[g]]
            thermal_options.append(set((x['stable']*d['min_mw'][t]+x['mean'], x['stable']*min(d['ramp_up_mw_per_h'][t]/6,d['max_mw'][t]-d['min_mw'][t])*int(bundle['reserve']['contingency_eligible'][g])) for x in cells))
            lo+=min(x['stable']*d['min_mw'][t]+x['mean'] for x in cells)
            hi+=max(x['stable']*d['max_mw'][t]+x['mean'] for x in cells)
            contingency+=max(x['stable']*min(d['ramp_up_mw_per_h'][t]/6,d['max_mw'][t]-d['min_mw'][t])*int(bundle['reserve']['contingency_eligible'][g]) for x in cells)
            if i==0: first_reach+=max(min(d['max_mw'][t],d['initial_power_mw']+d['ramp_up_mw_per_h'][t]/12) if x['stable'] else x['mean'] for x in cells)
        reserve_valid_minima=[sum(x[0] for x in combo) for combo in product(*thermal_options) if sum(x[1] for x in combo)+1e-9>=bundle['reserve']['contingency_mw'][t]]
        conditioned_min=min(reserve_valid_minima) if reserve_valid_minima else None
        charge=discharge=first_discharge=0.
        for d in bundle['batteries'].values():
            ready=datetime.fromisoformat(d['notice_available_at'])+timedelta(hours=d['preparation_h']['discharge'])<=at
            available=d['availability'][t] and ready
            charge+=d['charge_max_mw']*available; discharge+=d['discharge_max_mw']*available
            first_discharge+=min(d['discharge_max_mw'],d['initial_power_mw']+d['ramp_up_mw_per_h']/12,
                (d['initial_energy_mwh']-d['energy_min_mwh'])*(d['roundtrip_efficiency']**.5)*12)*available
        rows.append(dict(t=t,at=at.isoformat(),delivery=day<=at<day+timedelta(days=1),net_load_mw=net,
            thermal_min_optimistic_mw=lo,thermal_max_optimistic_mw=hi,
            reserve_conditioned_thermal_minimum_mw=conditioned_min,
            reserve_conditioned_minimum_charge_mw=max(0.,conditioned_min-net+bundle['reserve']['negative_mw'][t]) if conditioned_min is not None else None,
            minimum_charge_external_mw=max(0.,lo-net+bundle['reserve']['negative_mw'][t]),
            minimum_discharge_external_mw=max(0.,net+bundle['reserve']['positive_mw'][t]-hi),
            battery_charge_available_optimistic_mw=charge,battery_discharge_available_optimistic_mw=discharge,
            negative_margin_with_battery_mw=net-bundle['reserve']['negative_mw'][t]-lo+charge,
            positive_margin_with_battery_mw=hi+discharge-net-bundle['reserve']['positive_mw'][t],
            eligible_thermal_ten_minute_mw=contingency,contingency_margin_mw=contingency-bundle['reserve']['contingency_mw'][t],
            first_boundary_energy_margin_mw=first_reach+first_discharge-net if i==0 else None,
            battery_incremental_reserve_credit_mw=0.))
    delivery=[r for r in rows if r['delivery']]
    ch=sum(r['minimum_charge_external_mw']/12 for r in delivery)
    dis=sum(r['minimum_discharge_external_mw']/12 for r in delivery)
    eta=.95; replacement=max(ch,dis/(eta*eta))
    return dict(kind='necessary_optimistic_envelopes_not_solve',rows=rows,
        rejected_cells={k:sum(r[k]<-1e-7 for r in rows if r[k] is not None) for k in
            ('negative_margin_with_battery_mw','positive_margin_with_battery_mw','contingency_margin_mw','first_boundary_energy_margin_mw')},
        delivery_energy_floors=dict(minimum_external_charge_mwh=ch,minimum_external_discharge_mwh=dis,
            terminal_preserving_minimum_external_charge_mwh=replacement,
            terminal_preserving_minimum_external_discharge_mwh=max(dis,ch*eta*eta),
            minimum_internal_throughput_mwh=2*eta*replacement,
            reserve_conditioned_absorption_floor_on_nonrejected_cells_mwh=sum(r['reserve_conditioned_minimum_charge_mw']/12 for r in delivery if r['reserve_conditioned_minimum_charge_mw'] is not None),
            reserve_conditioned_unavailable_cells=sum(r['reserve_conditioned_minimum_charge_mw'] is None for r in delivery),
            interpretation='discharge floor is actual signed baseline demanded by zero-incremental-reserve capacity row; not reserve activation energy',
            terminal_replacement_applies_directly_to_midnight_start=(boundary==day)),
        ignored_constraints=['network/location','joint chronological path compatibility','future SOC/ramp coupling','hour direction','cycle budgets','readiness transitions after first use','terminal jointly with all periods'],
        qualifications='battery energy only; thermal supplies all reserve', feasibility_proven=False,
        accepted_DA_plan=False,execution_authority=False,full_market_completed=False)
