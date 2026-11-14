"""New prior-generated bounded DA research inputs; never reads historical profiles.

Two distinct boundaries, immutable static inputs, no solver calls. The finite
menu is specified by a clock grid, never searched or repaired for feasibility.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from .gd_october_ieee39_scenario import _load_static
from .gd_october_research_forecast import prior_value, METHOD, PRIOR_SCHEMA
from .gd_da_candidate import research_horizon, build_candidate_scuc, digest, validate_contract

SOURCE = 'research://ieee39/r11/new-prior-da-finite-menu'
STATIC_SHA256 = {'config/modes.yaml': '7b8b885474816e7eb2a39903a6f49147b2f0ec34d6f134d134450e2bac27bf3a', 'config/base.yaml': '05e3c6cef9c2af2e64858db8deb181693a93059e8ab70fa9b50c2744ac50a62b', 'config/units.yaml': '4045e99ef2a0f30a43e062410f5d2acd7dff42fd70910b9abac73aae2fa978c6', 'data/case39/buses.csv': 'cf865656bcf1417fe3c470c0529f96ec2be18601f7ef0a6b4a6d084a105e6b60', 'data/case39/branches.csv': '3770230cf8a851cdfc694a049d524fade7e5703d4a313357e74b1afc9302a471', 'resolved_bids.json': '85ae59a2607f2e2d9c8f9fefb54c11e85720e9f17afe29fdaf45c365eec5d0b7'}


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def prepare_ieee39_da(root, *, boundary_kind='original_D00_research', day_start='2026-11-11T00:00:00+08:00', seed=20261111, generated_at_utc=None):
    """Prepare, not solve. Boundary choice is a named experiment, not a repair."""
    static_raw, static = _load_static(root)
    return _prepare(static_raw, static, boundary_kind, day_start, seed,
        generated_at_utc or datetime.now(timezone.utc).isoformat())


def _prepare(static_raw, static, boundary_kind, day_start, seed, generated):
    if {k:sha256(v).hexdigest() for k,v in static_raw.items()} != STATIC_SHA256:
        raise ValueError('retained original static source bytes changed')
    day = datetime.fromisoformat(day_start)
    if day.utcoffset() != timedelta(hours=8) or any((day.hour, day.minute, day.second, day.microsecond)):
        raise ValueError('explicit Shanghai midnight required')
    if type(seed) is not int: raise ValueError('integer seed required')
    if boundary_kind not in ('original_D00_research', 'synthetic_lead_in_research'): raise ValueError('boundary kind')
    boundary = day if boundary_kind == 'original_D00_research' else day-timedelta(hours=3)
    declared, issue, cutoff, publication = (day-timedelta(hours=h) for h in (24,4.5,4,3.5))
    count = 288 + int((day-boundary).total_seconds()/300)
    h = research_horizon(count); ts = h.ids
    series = lambda v: {t:v for t in ts}
    sources = {}
    def bind(key, artifact, kind, available):
        sources[key] = dict(artifact=artifact, sha256=sha256(artifact).hexdigest(), kind=kind, available_at=available.isoformat())
    for k,v in static_raw.items(): bind('static:'+k,v,'retained_static',declared)
    u=static['units']; buses=tuple(str(b['bus']) for b in static['buses'])
    resources = {'load_mw':{f'load:{b["bus"]}':dict(bus=str(b['bus']),scale_mw=float(b['load_mw'])*float(static['base']['load_scale'])) for b in static['buses']},
        'wind_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='wind'},
        'solar_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['cap'])) for x in u['renewable'] if x['type']=='pv'},
        'boundary_tie_mw':{x['id']:dict(bus=str(x['bus']),scale_mw=float(x['pmax'])) for x in u['tie']}}
    values={}; snapshots={}
    for family, rs in resources.items():
        prior=dict(schema_version=PRIOR_SCHEMA,source_kind='research_assumption',method=METHOD,seed=seed,epoch=day.isoformat(),family=family,resources=rs,published_at=declared.isoformat(),available_at=declared.isoformat(),information_end=declared.isoformat(),uses_future_actual=False,original_refs={k:sha256(v).hexdigest() for k,v in static_raw.items()})
        bind('prior:'+family,raw(prior),'synthetic_exogenous_prior',declared)
        knots=[dict(at=(boundary+timedelta(minutes=15*j)).isoformat(),values={r:prior_value(prior,r,boundary+timedelta(minutes=15*j)) for r in rs}) for j in range(count//3+1)]
        vintage=dict(schema='gd.research.da_prior_vintage.v1',id='R11-NEW-DA:'+family,issued_at=issue.isoformat(),available_at=issue.isoformat(),prior_sha256=sources['prior:'+family]['sha256'],method='new_prior_generated_15min_knots_exact_linear_5min_means',uses_future_actual=False,knots=knots)
        bind('vintage:'+family,raw(vintage),'synthetic_da_vintage',issue); snapshots[family]=vintage
        values[family]={(b,t):0. for b in buses for t in ts}
        for t in ts:
            j,k=divmod(t,3)
            for r,p in rs.items():
                a,b=knots[j]['values'][r],knots[j+1]['values'][r]
                values[family][p['bus'],t]+=a+(b-a)*(k+.5)/3
    inventory=dict(batteries={x['id']:dict(mode='idle',initial_energy_mwh=float(x['ecap'])*float(x['soc0']),final_energy_mwh=float(x['ecap'])*float(x['soc0']),power_mw=0.,reserve_credit_mw=0.,parameters=x) for x in u['storage']},pumped={x['id']:dict(modes={f'{x["id"]}_{j+1}':'off' for j in range(x['n_units'])},upper_m3=float(x['vup0'])*10000,lower_m3=(float(x['v_total'])-float(x['vup0']))*10000,net_power_mw=0.,reserve_credit_mw=0.,parameters=x) for x in u['pumped']})
    boundary_doc=dict(kind=boundary_kind,at=boundary.isoformat(),thermal={x['id']:x['init'] for x in u['thermal']},storage=inventory,provenance='original D00 numerical initial assumptions' if boundary==day else 'Separately authored synthetic 21:00 boundary borrows static numerical init values; not original D00 telemetry or a shifted historical record')
    bind('boundary',raw(boundary_doc),'research_boundary_assumption',declared)
    curve_doc=dict(kind='explicit_linear_research_curve',durations_minutes={'CCGT':dict(startup=60,shutdown=30),'GT':dict(startup=30,shutdown=15)},startup_class='hot research label; uniform original resolved fee, not inferred thermal age',notice_h=.5,cost='original noload plus first energy bid times interval mean',menu='off; start at boundary+30min, D06,D12,D18; each stay-on or stop at D06,D12,D18,D23 after min-up; no restarts; no hindsight',tail='complete curves, carry residual residence')
    bind('curves',raw(curve_doc),'research_curve_assumption',declared)
    contract=dict(schema='gd.research.da_candidate.v1',experiment_id=f'R11-IEEE39-{boundary_kind}-{day.date()}-{seed}',boundary_kind=boundary_kind,boundary_at=boundary.isoformat(),day_start=day.isoformat(),cutoff=cutoff.isoformat(),planned_publication_at=publication.isoformat(),generated_at_utc=generated,sources=sources,storage=dict(policy='all_idle_off_fixed_inventory_zero_credit',inventory_sha256=sha256(raw(inventory)).hexdigest()),cost_policy='stable_bid_plus_transition_hourly_plus_uniform_event',terminal_policy='complete_curves_carry_residence')
    thermal={}; candidates={}
    for x in u['thermal']:
        g=x['id']; bid=static['bids']['units'][g]; pmin=float(x['pmin']); segments=[]; base=0.
        for i,s in enumerate(bid['segments']):
            base+=max(0,min(pmin,float(s['p_end']))-float(s['p_start']))*float(s['price'])
            width=float(s['p_end'])-max(pmin,float(s['p_start']))
            if width>0: segments.append(dict(key=str(i),width_mw=width,price_yuan_per_mwh=series(float(s['price']))))
        thermal[g]=dict(bus=str(x['bus']),bid_base_mw=pmin,base_bid_yuan_per_h=series(base),segments=tuple(segments),min_mw=series(pmin),max_mw=series(float(x['pmax'])),ramp_up_mw_per_h=series(float(x['ramp'])*60),ramp_down_mw_per_h=series(float(x['ramp'])*60),startup_output_limit_mw=series(pmin),shutdown_output_limit_mw=series(pmin),startup_yuan=series(float(bid['startup_cost_yuan'])),shutdown_yuan=series(0.),min_up_h=float(x['ut']),min_down_h=float(x['dt']),initial_online=int(x['init']['online']),initial_power_mw=float(x['init']['p0']),initial_state_age_h=float(x['init']['hours']),initial_state_kind='stable',status=series('free'),fixed_power_mw=series(None),max_starts=1,max_stops=1,transition_model='instant_with_output_limits',terminal_policy='carry_out',source=SOURCE)
        candidates[g]=[dict(id='stable' if x['init']['online'] else 'off',events=())]
        if x['type']=='coal': continue
        def event(kind, at):
            duration=curve_doc['durations_minutes'][g][kind]; n=duration//5; steps=[]
            for j in range(n):
                a,b=pmin*j/n,pmin*(j+1)/n
                if kind=='shutdown': a,b=pmin-a,pmin-b
                mean=(a+b)/2; steps.append((a,mean,b,float(x['noload'])+float(bid['segments'][0]['price'])*mean))
            return dict(kind=kind,at=int((at-boundary).total_seconds()/300),steps=tuple(steps),notice_h=.5 if kind=='startup' else 0.,notification_at=(at-timedelta(minutes=30)).isoformat() if kind=='startup' else None,source_id='curves',curve_rate_mw_per_min=pmin/duration)
        for start in [boundary+timedelta(minutes=30)]+[day+timedelta(hours=q) for q in (6,12,18)]:
            e=event('startup',start); candidates[g].append(dict(id=f'start-{e["at"]}-stay',events=(e,)))
            for q in (6,12,18,23):
                stop=day+timedelta(hours=q)
                if stop-start < timedelta(hours=float(x['ut'])): continue
                f=event('shutdown',stop)
                if f['at']>=e['at']+len(e['steps']) and f['at']+len(f['steps'])<=count:
                    candidates[g].append(dict(id=f'start-{e["at"]}-stop-{f["at"]}',events=(e,f)))
    lines={}
    for l in static['branches']:
        rate=float(l['rate_mw'])*float(static['base']['line_capacity_multiplier'])*float(static['base'].get('line_scale',{}).get(l['branch_id'],1))
        lines[l['branch_id']]=dict(from_bus=str(l['from_bus']),to_bus=str(l['to_bus']),susceptance_mw_per_rad=100/float(l['x_pu']),lower_mw=series(-rate),upper_mw=series(rate),source=SOURCE)
    zero={(b,t):0. for b in buses for t in ts}
    energy=dict(horizon=h,buses=buses,thermal=thermal,load_mw=values['load_mw'],renewable_mw={k:values['wind_mw'][k]+values['solar_mw'][k] for k in zero},physical_tie_mw=values['boundary_tie_mw'],lines=lines,sections={},reference_buses=('39',),penalties_yuan_per_mwh=dict(line=1000.,section=1000.,balance_shortfall=10000.,balance_surplus=10000.),source_notes={k:SOURCE for k in ('thermal','transition','network','penalties','exogenous','boundary_basis','balance_policy','shortfall_cap_policy','renewable_policy')},balance_policy='hard',shortfall_cap_policy='unbounded_slack',renewable_mode='fixed_exogenous',renewable_bid_yuan_per_mwh=zero,renewable_curtailment_penalty_yuan_per_mwh=zero)
    reserve=dict(positive_mw=series(400.),negative_mw=series(0.),primary_mw=series(0.),contingency_mw=series(400.),primary_fraction={x['id']:.06 if x['type']=='coal' else .04 for x in u['thermal']},contingency_eligible={x['id']:True for x in u['thermal']})
    result=dict(contract=contract,candidates=candidates,reserve=reserve,energy_inputs=energy,storage_inventory=inventory,qualifications=dict(thermal='stable-only contingency eligible; coal6% gas4% primary; shared headroom; synthetic qualification',regulation='all zero awards; no auction or performance claim',renewables='fixed F, zero flexible credit',storage='idle/off zero credit',requirements='positive original fixed400; additional contingency400 stress; down_enabled=false -> negative0; primary0 explicit scenario assumption'),static=static,seed=seed)
    validate_contract(contract)
    return result


def validate_ieee39_da(bundle):
    """Reconstruct every forecast/path/parameter from byte-bound static sources."""
    import csv, io, yaml
    c=bundle['contract']; validate_contract(c)
    sr={k[7:]:v['artifact'] for k,v in c['sources'].items() if k.startswith('static:')}
    from .gd_october_ieee39_scenario import STATIC_FILES
    if set(sr)!=set(STATIC_FILES): raise ValueError('static inventory mismatch')
    static=dict(base=yaml.safe_load(sr['config/base.yaml']),modes=yaml.safe_load(sr['config/modes.yaml']),units=yaml.safe_load(sr['config/units.yaml']),buses=list(csv.DictReader(io.StringIO(sr['data/case39/buses.csv'].decode()))),branches=list(csv.DictReader(io.StringIO(sr['data/case39/branches.csv'].decode()))),bids=json.loads(sr['resolved_bids.json']))
    expected=_prepare(sr,static,c['boundary_kind'],c['day_start'],bundle['seed'],c['generated_at_utc'])
    if digest(bundle)!=digest(expected): raise ValueError('DA adapter reconstruction mismatch: changed source, vintage, menu, inventory or inputs')
    return True


def build_ieee39_da(bundle):
    validate_ieee39_da(bundle)
    return build_candidate_scuc(**{k:bundle[k] for k in ('contract','candidates','reserve','energy_inputs')})


def necessary_envelopes(bundle):
    """Optimistic necessary conditions only; passing never proves feasibility."""
    validate_ieee39_da(bundle)
    from .gd_da_candidate import compile_path
    e=bundle['energy_inputs']; ts=e['horizon'].ids; rows=[]
    paths={g:[compile_path(d,p,ts,bundle['contract']) for p in bundle['candidates'][g]] for g,d in e['thermal'].items()}
    for i,t in enumerate(ts):
        net=sum(e['load_mw'][b,t]-e['renewable_mw'][b,t]-e['physical_tie_mw'][b,t] for b in e['buses'])
        minimum=maximum=contingency=reach=0.
        for g,d in e['thermal'].items():
            cells=[p['cells'][i] for p in paths[g]]
            minimum+=min(c['stable']*d['min_mw'][t]+c['mean'] for c in cells)
            maximum+=max(c['stable']*d['max_mw'][t]+c['mean'] for c in cells)
            contingency+=max(c['stable']*min(d['ramp_up_mw_per_h'][t]/6,d['max_mw'][t]-d['min_mw'][t])*int(bundle['reserve']['contingency_eligible'][g]) for c in cells)
            if i==0: reach+=max(min(d['max_mw'][t],d['initial_power_mw']+d['ramp_up_mw_per_h'][t]/12) if c['stable'] else c['mean'] for c in cells)
        rows.append(dict(t=t,at=(datetime.fromisoformat(bundle['contract']['boundary_at'])+timedelta(minutes=5*i)).isoformat(),net_load_mw=net,min_output_mw=minimum,max_output_mw=maximum,negative_margin_mw=net-bundle['reserve']['negative_mw'][t]-minimum,positive_margin_mw=maximum-net-bundle['reserve']['positive_mw'][t],eligible_ten_minute_mw=contingency,contingency_margin_mw=contingency-bundle['reserve']['contingency_mw'][t],first_boundary_reachable_mw=reach if i==0 else None,first_boundary_energy_margin_mw=reach-net if i==0 else None))
    return dict(kind='necessary_optimistic_envelopes_not_solve',rows=rows,rejected_cells={k:sum(r[k]<-1e-7 for r in rows if r[k] is not None) for k in ('negative_margin_mw','positive_margin_mw','contingency_margin_mw','first_boundary_energy_margin_mw')},feasibility_proven=False)


def load_prepared_ieee39_da(artifact):
    """Load the example's portable bytes, reconstruct horizon and verify all inputs."""
    from .gd_october_job_codec import loads
    bundle=loads(artifact)
    descriptor=bundle['energy_inputs']['horizon']
    if set(descriptor)!={'physical_count','factory'} or descriptor['factory']!='smm.gd_da_candidate.research_horizon':
        raise ValueError('unsupported horizon factory')
    bundle['energy_inputs']['horizon']=research_horizon(descriptor['physical_count'])
    validate_ieee39_da(bundle)
    return bundle
