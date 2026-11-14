"""R18 96-window source-bound session. Pure templates never mint live tickets.

Every live projection is authenticated against this run's issued RT commands
and fully reintegrated, cutoff-available receipts. R17 remains a pure source
contract only. No native calls are made by any function in this module.
"""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from weakref import WeakKeyDictionary
from . import gd_r17_midnight_contract as source_contract
from . import gd_r15_da_rt_bridge as old
from .gd_r15_da_rt_bridge import require, close, digest, stamp, finite, DT, SCOPES, integrate_cell
from .gd_r16_hour_session import project_issued_prefix, _forecast

SCHEMA='gd.research.r18.day_input.v1'
WINDOW_POLICY=dict(schema='gd.research.r18.full_day_window_policy.v1',indices=list(range(96)),
    cells_per_window=24,issued_cells=3,cutoff_lead_minutes=15,stride_minutes=15,
    source_contract_policy_sha256=digest(source_contract.POLICY),
    storage_terminal='original D96 interior boundary plus rolling two-hour physical SCED endpoint',
    actual_initial_state='same live R18 accepted receipts plus already issued RT commands at cutoff',
    forecast='frozen exogenous priors, no future actual',tail='archived physical SCED research reference, not published awards',
    freeze_thermal_states=True,freeze_sourced_battery_modes=True,
    positive_reserve_mw=400.,accident_reserve_mw=400.,wind_solar_unqualified_storage_reserve_mw=0.,
    final_drain='D+1 00:00 after W95 start, exactly 288 D-day executed cells; tail21 remain unissued',
    official_price_publication='verified D+1 or necessary D+2, not synthetic cutoff',
    stop_policy='first failure permanent; no retry or repair',native_release_available=False)
_SESSIONS=WeakKeyDictionary();_TICKETS=WeakKeyDictionary()
class DaySession:__slots__=('__weakref__',)
class PreparedWindow:__slots__=('__weakref__',)


def window_specs(plan,policy=None):
    require((WINDOW_POLICY if policy is None else policy)==WINDOW_POLICY,'exact R18 day policy required')
    return [source_contract.window_spec(plan,i) for i in range(96)]


def contract_sources(plan,permit):
    source_contract.validate_contract(plan,permit)
    e=source_contract._CONTRACTS[permit]
    return e['source'],e['data']


def _state_seal(s):
    return digest({k:s[k] for k in ('id','plan','specs','receipt','commands','clock','accepted_windows','next_window')})


def _seal_session(session):
    s=_SESSIONS[session];s['seal']=_state_seal(s)


def _session(session):
    require(type(session) is DaySession and session in _SESSIONS,'fresh typed R18 day session required')
    s=_SESSIONS[session]
    require(s['seal']==_state_seal(s),'live session state mutated outside transaction')
    source_contract.validate_contract(s['plan'],s['permit'])
    return s


def initialize_day(plan,permit,*,policy=None):
    source_contract.validate_contract(plan,permit,recheck_files=True)
    specs=window_specs(plan,policy);prep=plan['initial_preparation'];seed=prep['seed_state']
    require(seed['at']==specs[0]['cutoff'],'fresh initial seed cutoff required')
    sid='R18-RT-'+uuid4().hex
    receipt=dict(id=sid+'-SEED',at=seed['at'],available_at=seed['at'],state=deepcopy(seed),
        is_measurement=False,kind='new_synthetic_seed_from_reverified_archived_preparation',
        publication_transaction_id=digest(plan))
    commands=[dict(id=sid+'-PREP-'+str(j),issued_at=seed['at'],available_at=seed['at'],
        cell=deepcopy(c),kind='new_synthetic_preparation_command',source_bindings_sha256=digest(plan['preparation_source_bindings'])) for j,c in enumerate(prep['cells'])]
    p=project_issued_prefix(receipt,commands,cutoff=specs[0]['cutoff'],start=specs[0]['start'],technical=plan['technical'])
    require(digest(p['state'])==digest(prep['midnight_state']),'preparation projection mismatch')
    session=DaySession();_SESSIONS[session]=dict(id=sid,plan=deepcopy(plan),permit=permit,specs=specs,
        receipt=receipt,commands=commands,clock=specs[0]['cutoff'],accepted_windows=[],next_window=0,prepared=None,run=None)
    _seal_session(session);return session


def validate_window_input(inp):
    require(inp['schema']==SCHEMA and inp['policy']==WINDOW_POLICY and inp['scopes']==SCOPES,'R18 schema/policy/scope mismatch')
    w=inp['window'];i=w['index'];require(type(i) is int and 0<=i<96,'D-day window index required')
    require(w==source_contract.window_spec(inp,i),'window/cutoff/midnight mapping changed')
    start,cutoff=stamp(w['start']),stamp(w['cutoff']);projection=inp['projection']
    require(projection['state']['at']==w['start'] and projection['start']==w['start'] and projection['cutoff']==w['cutoff'],'projection endpoint mismatch')
    require(projection['is_measurement'] is False and projection['future_actual_used'] is False,'real or future telemetry not available')
    require(type(inp['construction_only']) is bool,'explicit construction marker required')
    if not inp['construction_only']:
        require(projection.get('origin')=='same_live_R18_cutoff_receipt_and_issued_RT_prefix'
                and projection.get('session_id') and projection.get('run_id')
                and projection.get('live_provenance_sha256'),'live provenance required; R17 template/projector is not authentication')
    require(inp['midnight_targets']==source_contract.TARGETS,'original D96 targets changed')
    require(len(inp['da_cells'])==24 and len(inp['references'])==8,'complete source horizon required')
    require(stamp(inp['publication_at'])<=cutoff,'future publication')
    for j,c in enumerate(inp['da_cells']):
        require(stamp(c['start'])==start+timedelta(minutes=5*j) and stamp(c['end'])==start+timedelta(minutes=5*(j+1)),'physical gap/overlap')
        for r,z in c['thermal'].items():
            if z['phase'] in ('startup','shutdown'):
                effective=stamp(c['start'])-timedelta(minutes=5*z['curve_step'])
                events=[e for e in inp['events'] if e['unit']==r and e['kind']==z['phase'] and stamp(e['effective_at'])==effective]
                require(len(events)==1,'transition event source missing')
                ev=events[0];require(stamp(ev['available_at'])<=cutoff,'event unavailable at cutoff')
                if z['phase']=='startup':require(stamp(ev['notification_at'])<=effective-timedelta(minutes=30),'startup advance notice required')
    for q,ref in enumerate(inp['references']):
        g=i+q;require(ref['chronological_q']==g and ref['physical_indices']==list(range(3*g,3*g+3)),'chronological tail cannot use DA representative row')
        require(ref['start']==inp['da_cells'][3*q]['start'] and ref['end']==inp['da_cells'][3*q+2]['end'],'reference time mismatch')
        for z in ref['resources'].values():
            require((g<96 and z['source_market_index']==g and z['reference_kind']=='original_publication96_research_DA_award')
                    or (g>=96 and z['source_market_index'] is None and z['published_DA_award'] is None and z['reference_kind']==source_contract.TAIL_KIND),'tail publication mislabel')
    require(set(inp['resources'])==set(inp['technical']['thermal'])|set(inp['technical']['batteries']),'complete resource inventory required')
    for r,u in inp['resources'].items():
        require(len(u['rows'])==24,'physical rows missing')
        if u['kind']=='battery':
            state=projection['state']['batteries'][r]
            require(u['energy_min_mwh']-1e-6<=state['energy_mwh']<=u['energy_max_mwh']+1e-6,'initial SOC outside bounds')
            close(u['terminal_energy_mwh'],inp['da_cells'][-1]['batteries'][r]['energy_end_mwh'],'rolling target changed')
            require(all(finite(x)>=0 for x in state['daily_used_internal_mwh'].values()),'negative dated usage')
            close(sum(state['daily_used_internal_mwh'].values()),state['cumulative_internal_mwh'],'dated usage reset or duplicate')
            for date in {c['start'][:10] for c in inp['da_cells']}:
                require(date in u['cycle_budget'] and state['daily_used_internal_mwh'].get(date,0)<=u['cycle_budget'][date]['limit_internal_mwh']+1e-6,'dated throughput exceeds source budget')
            for row in u['rows']:
                require(not row['reserve_eligible'] and all(row[k+'_cap_mw']==0 for k in ('positive','negative','primary','accident')),'unqualified battery reserve')
    require(len(inp['reserve'])==24 and all(z==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.) for z in inp['reserve']),'original reserve stress changed')
    for v in inp['vintages'].values():
        require(stamp(v['source_available_at'])<=stamp(v['issued_at'])<=cutoff and stamp(v['available_at'])<=cutoff and stamp(v['information_end'])<=cutoff and not v['uses_future_actual'] and not v['is_measurement'],'future forecast vintage')
        require(v['coverage_start']==w['start'] and v['coverage_end']==w['horizon_end'],'forecast horizon missing')
    for family in ('load_mw','fixed_injection_mw'):
        require(set(inp[family])==set(inp['nodes']) and all(len(v)==24 for v in inp[family].values()),'network input shape')
    source_contract.derive_price_quarters(inp)
    return True


def assemble_window_input(c,permit,index,projection,*,transaction_id,construction_only=False):
    """Pure assembly. No DA state is read or substituted for the projection."""
    source_contract.validate_contract(c,permit,recheck_files=True)
    e=source_contract._CONTRACTS[permit];data=e['data'];spec=source_contract.window_spec(c,index);first=3*index
    families,vintages=_forecast(e['source'],spec)
    for v in vintages.values():v.update(coverage_start=spec['start'],coverage_end=spec['horizon_end'])
    cells=deepcopy(c['physical_cells'][first:first+24]);refs=deepcopy(c['references'][index:index+8])
    require(len(cells)==24 and len(refs)==8,'missing chronological physical/reference tail')
    resources={}
    for r,u in data['resources'].items():
        resources[r]={k:deepcopy(v) for k,v in u.items() if k!='rows'}
        resources[r]['rows']=deepcopy(data['physical_lift']['resources'][r]['rows'][first:first+24])
        if u['kind']=='battery':
            resources[r].update(terminal_energy_mwh=cells[-1]['batteries'][r]['energy_end_mwh'],cycle_budget=deepcopy(data['physical_lift']['resources'][r]['cycle_budget']))
    inp=dict(schema=SCHEMA,transaction_id=transaction_id,publication_transaction_id=digest(c),publication_sha256=digest(c),contract_sha256=digest(c),window=spec,day_start=c['day_start'],publication_at=c['publication_at'],
        policy=deepcopy(WINDOW_POLICY),scopes=deepcopy(old.SCOPES),projection=projection,da_cells=cells,references=refs,
        technical=deepcopy(c['technical']),vintages=vintages,nodes=deepcopy(data['nodes']),resources=resources,
        reserve=deepcopy(data['physical_lift']['reserve'][first:first+24]),load_mw=families['load_mw'],
        fixed_injection_mw={n:[sum(families[f][n][j] for f in ('wind_mw','solar_mw','boundary_tie_mw')) for j in range(24)] for n in data['nodes']},
        network={l:dict(ptdf=deepcopy(z['ptdf']),lower_mw=deepcopy(data['physical_lift']['network'][l]['lower_mw'][first:first+24]),upper_mw=deepcopy(data['physical_lift']['network'][l]['upper_mw'][first:first+24])) for l,z in data['network'].items()},
        midnight_targets=deepcopy(c['midnight_targets']),events=deepcopy(c['events']),construction_only=construction_only,feasibility_established=False,
        future_RT_states_known=False,native_release_available=False)
    validate_window_input(inp)
    return inp


def pure_template_input(c,permit,index):
    """Archived DA arithmetic fixture for shapes/tests. Never live acceptance."""
    template,_=source_contract.template_window(c,permit,index)
    return assemble_window_input(c,permit,index,deepcopy(template['projection']),
        transaction_id='R18-PURE-SHAPE-W'+str(index),construction_only=True)


def prepare_window(session,index=0):
    s=_session(session);require(type(index) is int and 0<=index<96 and index==s['next_window'],'next live day window required')
    require(s['prepared'] is None,'window already prepared; no replay')
    require(s['run'] is not None,'unbound pure session cannot mint native window ticket')
    from . import gd_r18_priced_native_adapter as api
    provenance=api.authenticate_projection(s['run'],session)
    spec=s['specs'][index];require(s['clock']==spec['cutoff'],'cannot backdate or use final drain in earlier cutoff')
    projection=project_issued_prefix(s['receipt'],s['commands'],cutoff=spec['cutoff'],start=spec['start'],technical=s['plan']['technical'])
    projection.update(origin='same_live_R18_cutoff_receipt_and_issued_RT_prefix',session_id=s['id'],run_id=provenance['run_id'],live_provenance_sha256=digest(provenance))
    inp=assemble_window_input(s['plan'],s['permit'],index,projection,transaction_id=s['id']+'-W'+str(index))
    ticket=PreparedWindow();_TICKETS[ticket]=dict(session=session,input=deepcopy(inp),input_sha256=digest(inp),model=None,consumed=False)
    s['prepared']=ticket;return inp,ticket


def _ticket(inp,ticket):
    require(type(ticket) is PreparedWindow and ticket in _TICKETS,'fresh typed R18 prepared ticket required')
    t=_TICKETS[ticket];s=_session(t['session'])
    require(s['prepared'] is ticket and not t['consumed'] and digest(inp)==t['input_sha256'],'stale/consumed/mutated R18 ticket')
    require(inp['construction_only'] is False,'template cannot be accepted')
    source_contract.validate_contract(s['plan'],s['permit'],recheck_files=True)
    validate_window_input(inp);return t


def build_rt_model(inp,ticket):
    t=_ticket(inp,ticket);require(t['model'] is None,'one RT model per transaction')
    from .gd_r18_day_models import build_rt_template
    model,audit=build_rt_template(inp);t['model']=model;t['model_sha256']=model_fingerprint(model,'RT');return model,audit


def build_price_template(inp):
    from .gd_r18_day_models import build_price_template as build
    return build(inp)


def derive_price_quarters(inp):return source_contract.derive_price_quarters(inp)


def model_fingerprint(model,stage):
    from .highs_solver import serialize_model
    require(stage in ('RT','PRICE'),'typed model stage required')
    options=dict(threads=1,time_limit=120. if stage=='RT' else 60.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False)
    payload,vs,cs=serialize_model(model,options)
    return digest(dict(payload=payload,names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])))
