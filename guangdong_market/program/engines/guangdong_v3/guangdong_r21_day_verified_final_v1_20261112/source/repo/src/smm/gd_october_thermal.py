"""Externally confirmed thermal curves, not an SCUC or curve generator.

Official anchors: October 2026 §§2.2.2.2/.6/.7, 3.3.1/.1, 3.4.3.5/.9.
Only complete externally supplied five-minute records are consumed. Source
hashes establish byte identity, never dispatcher authenticity. See docs/70.
"""
from copy import deepcopy
from datetime import timedelta, timezone
from hashlib import sha256
import json

from .gd_energy_core import EnergyInputError
from .gd_october_roll import (_require, _keys, _text, _time, _number, _close,
                              _parse, FIVE, DT_H, SHANGHAI)

SCHEMA = 'gd.october.thermal_trajectories.v1'
RECORD_SCHEMA = 'gd.october.confirmed_thermal_curve.v1'
RESEARCH_SCHEMA = 'gd.october.research_thermal_trajectories.v1'
RESEARCH_RECORD_SCHEMA = 'gd.october.research_thermal_curve.v1'

def is_research_book(book):
    return type(book) is dict and book.get('schema_version') == RESEARCH_SCHEMA

def trajectory_available_at(record):
    return record['assumption']['available_at'] if record['schema_version'] == RESEARCH_RECORD_SCHEMA else record['confirmation']['available_at']

def initial_trajectory_kind(book):
    return 'research_assumption_trajectory' if is_research_book(book) else 'externally_confirmed_trajectory'

def require_trajectory_mode(book, mode):
    _require(not is_research_book(book) or mode == 'research', 'research thermal assumptions require research mode; no operator/official promotion')

PARAMETERS = ('min_mw', 'max_mw', 'min_up_h', 'min_down_h',
              'ramp_up_mw_per_h', 'ramp_down_mw_per_h',
              'curve_rise_mw_per_min', 'curve_fall_mw_per_min')


def _utc(t):
    return _time(t, 'thermal time').astimezone(timezone.utc)


def _grid(t):
    x = _utc(t).astimezone(SHANGHAI)
    _require(x.minute % 5 == 0 and x.second == x.microsecond == 0,
             'thermal trajectory requires exact five-minute boundaries')
    return _utc(t)


def validate_book(book, *, cutoff=None, run_id=None):
    """Return parsed copies; retain complete external bytes in the caller book."""
    _keys(book, ('schema_version', 'run_id', 'source', 'records'), 'thermal book')
    _require(book['schema_version'] in (SCHEMA, RESEARCH_SCHEMA), 'unsupported thermal book schema')
    research = is_research_book(book)
    _text(book['run_id'], 'thermal run'); _text(book['source'], 'thermal source')
    if run_id is not None:
        _require(book['run_id'] == run_id, 'thermal run mismatch')
    _require(type(book['records']) is tuple and bool(book['records']), 'explicit nonempty thermal record tuple required')
    parsed, identifiers, parameters = [], set(), {}
    for ref in book['records']:
        _keys(ref, ('artifact', 'sha256'), 'thermal record reference')
        raw = ref['artifact']
        _require(type(raw) is bytes and sha256(raw).hexdigest() == ref['sha256'], 'thermal record bytes/hash mismatch')
        d = _parse(raw)
        _keys(d, ('schema_version','run_id','event_id','unit_id','kind','startup_class',
                  'technology','applicability_source','effective_plan_id','source',
                  'typical_curve','conversion',('assumption' if research else 'confirmation'),'parameters','parameter_sources',
                  'preceding_state_started_at','notification_at','notice_required_h',
                  'reserve_policy','cost_basis','units','stable_rate_origin',
                  'minimum_residence_policy','operating_policy_source','steps'), 'thermal confirmed record')
        _require(d['schema_version'] == (RESEARCH_RECORD_SCHEMA if research else RECORD_SCHEMA) and d['run_id'] == book['run_id'], 'thermal record identity/schema mismatch')
        for k in ('event_id','unit_id','applicability_source','effective_plan_id','source','cost_basis'):
            _text(d[k], 'thermal ' + k)
        _require(d['event_id'] not in identifiers, 'duplicate thermal event')
        identifiers.add(d['event_id'])
        _require(d['technology'] in ('coal','gas'), 'unsupported thermal technology')
        # Gas is never inferred from the coal-startup clause; explicit separate
        # externally reviewed applicability must accompany every gas record.
        _require(d['kind'] in ('startup','shutdown'), 'unsupported thermal transition kind')
        _require((d['kind']=='startup' and d['startup_class'] in ('cold','warm','hot')) or
                 (d['kind']=='shutdown' and d['startup_class'] is None), 'explicit startup thermal class required')
        _require(d['reserve_policy'] == 'fixed_injection_no_flexible_reserve_or_agc', 'unsupported transition reserve policy')
        c = d['typical_curve']; _keys(c, ('curve_id','artifact_utf8','sha256','native_interval_minutes'), 'typical curve')
        _text(c['curve_id'],'typical curve ID'); _text(c['artifact_utf8'],'typical curve bytes')
        _require(c['native_interval_minutes'] == 15 and type(c['native_interval_minutes']) is int,
                 'original typical curve must explicitly identify fifteen-minute samples')
        _require(sha256(c['artifact_utf8'].encode()).hexdigest() == c['sha256'], 'typical curve bytes/hash mismatch')
        if research:
            from .gd_october_research_thermal import validate_research_declaration
            available = validate_research_declaration(d, cutoff=cutoff)
        else:
            # A retained research artifact cannot be promoted by changing the
            # outer schema or attaching made-up approval strings.
            from .gd_october_research_thermal import reject_research_promotion
            reject_research_promotion(d['typical_curve']['artifact_utf8'])
            c = d['conversion']; _keys(c, ('method','source','confirmed','uses_future_actual','gas_applicability_confirmed'), 'curve conversion')
            _require(c['method']=='externally_supplied_confirmed_5min' and c['confirmed'] is True and
                     c['uses_future_actual'] is False, 'thermal five-minute conversion must be explicit, confirmed and non-future-actual')
            _text(c['source'],'conversion source')
            _require(type(c['gas_applicability_confirmed']) is bool and
                     (d['technology']!='gas' or c['gas_applicability_confirmed']), 'gas requires separately confirmed resource applicability')
            c = d['confirmation']; _keys(c, ('status','published_at','available_at','approved_at','review_source','southern_confirmation_source'), 'thermal confirmation')
            _require(c['status']=='externally_confirmed', 'thermal trajectory not externally confirmed')
            for k in ('review_source','southern_confirmation_source'): _text(c[k], k)
            published, available, approved = (_utc(c[k]) for k in ('published_at','available_at','approved_at'))
            _require(published <= available and approved <= available, 'thermal confirmation publication/approval/availability order invalid')
            if cutoff is not None:
                _require(available <= _utc(cutoff), 'thermal trajectory unavailable at information cutoff')
        _require(d['units']==dict(power='MW',stable_ramp='MW/h',curve_ramp='MW/min',residence_and_notice='h',fixed_cost='yuan/h'),
                 'explicit thermal parameter units required')
        _require(d['minimum_residence_policy']=='shutdown_curve_start_conservative',
                 'unsupported minimum-residence interpretation')
        _text(d['operating_policy_source'],'minimum-residence policy source')
        p = d['parameters']; _keys(p, PARAMETERS, 'thermal resource conditions')
        _keys(d['parameter_sources'], PARAMETERS, 'thermal parameter sources')
        for k in PARAMETERS:
            _number(p[k], k, 0); _text(d['parameter_sources'][k], k + ' source')
        origin=d['stable_rate_origin']
        _keys(origin, ('unit','up','down','source'), 'stable rate origin')
        _text(origin['source'],'stable rate conversion source')
        _require(origin['unit'] in ('MW/min','MW/s','MW/h'),'unsupported stable rate source unit')
        factor={'MW/min':60.,'MW/s':3600.,'MW/h':1.}[origin['unit']]
        for direction in ('up','down'):
            _close(_number(origin[direction],'original stable rate',0)*factor,p['ramp_'+direction+'_mw_per_h'],
                   'declared stable rate unit conversion')
        _require(0 < p['min_mw'] <= p['max_mw'], 'thermal stable range invalid')
        if d['unit_id'] in parameters:
            _require(parameters[d['unit_id']]==p, 'time-varying thermal resource conditions unsupported')
        parameters[d['unit_id']] = p
        _require(type(d['steps']) is list and bool(d['steps']), 'complete thermal curve steps required')
        prior_end, prior_power = None, None
        for i, s in enumerate(d['steps']):
            _keys(s, ('start','end','mean_mw','start_mw','end_mw','cost_yuan_per_h'), 'thermal step')
            start, end = _grid(s['start']), _grid(s['end'])
            _require(end-start==FIVE and (prior_end is None or start==prior_end), 'thermal curve has gap/overlap or non-five-minute step')
            for k in ('mean_mw','start_mw','end_mw'): _number(s[k], k, 0)
            _number(s['cost_yuan_per_h'], 'explicit trajectory cost')
            _require(max(s['start_mw'],s['end_mw']) <= p['min_mw'] and
                     min(s['start_mw'],s['end_mw']) <= s['mean_mw'] <= max(s['start_mw'],s['end_mw']),
                     'thermal trajectory mean/endpoints outside confirmed subminimum band')
            if prior_power is not None: _close(s['start_mw'],prior_power,'thermal endpoint continuity')
            change = s['end_mw']-s['start_mw']
            _require((d['kind']=='startup' and change>=0) or (d['kind']=='shutdown' and change<=0), 'thermal curve reverses direction')
            _require(-5*p['curve_fall_mw_per_min']-1e-7 <= change <= 5*p['curve_rise_mw_per_min']+1e-7,
                     'thermal trajectory violates explicit transition rate')
            prior_end, prior_power = end,s['end_mw']
        first,last = d['steps'][0],d['steps'][-1]
        _close(first['start_mw'],0 if d['kind']=='startup' else p['min_mw'],'thermal curve initial endpoint')
        _close(last['end_mw'],p['min_mw'] if d['kind']=='startup' else 0,'thermal curve final endpoint')
        start = _utc(first['start']); previous = _utc(d['preceding_state_started_at'])
        residence = p['min_down_h'] if d['kind']=='startup' else p['min_up_h']
        _require(previous<=start and (start-previous).total_seconds()/3600+1e-10>=residence,
                 'thermal preceding minimum residence incomplete')
        lead = _number(d['notice_required_h'],'thermal notice duration',0)
        if d['kind']=='startup':
            notice = _utc(d['notification_at'])
            _require(previous<=notice and (research or notice<=available) and notice+timedelta(hours=lead)<=start,
                     'thermal startup notification/lead time invalid')
        else:
            _require(d['notification_at'] is None and lead==0, 'shutdown has no inferred startup notification')
        d['_sha256']=ref['sha256']; parsed.append(d)
    for unit in parameters:
        events = sorted((d for d in parsed if d['unit_id']==unit),key=lambda d:_utc(d['steps'][0]['start']))
        for a,b in zip(events,events[1:]):
            _require(_utc(a['steps'][-1]['end'])<=_utc(b['steps'][0]['start']) and a['kind']!=b['kind'],
                     'overlapping or inconsistent thermal event sequence')
            expected = _utc(a['steps'][0]['start']) if a['kind']=='startup' else _utc(a['steps'][-1]['end'])
            _require(_utc(b['preceding_state_started_at'])==expected,'thermal preceding state occurrence mismatch')
    return tuple(parsed)


def cursor_for(record, next_step):
    return dict(event_id=record['event_id'],event_sha256=record['_sha256'],next_step=next_step,kind=record['kind'])


def validate_cursor(cursor):
    _keys(cursor, ('event_id','event_sha256','next_step','kind'), 'thermal cursor')
    _text(cursor['event_id'],'thermal cursor event')
    h=cursor['event_sha256']
    _require(type(h) is str and len(h)==64 and all(c in '0123456789abcdef' for c in h),'thermal cursor hash required')
    _require(type(cursor['next_step']) is int and cursor['next_step']>0 and cursor['kind'] in ('startup','shutdown'), 'invalid thermal cursor')


def _active(records,unit,start):
    found=[(d,i,s) for d in records if d['unit_id']==unit for i,s in enumerate(d['steps']) if _utc(s['start'])==_utc(start)]
    _require(len(found)<=1,'ambiguous thermal event step')
    return found[0] if found else None


def advance_thermal(row, *, unit, power, online, records, start, end):
    """Check actual previous boundary, then advance only this supplied step."""
    _require(_utc(end)-_utc(start)==FIVE, 'thermal advance must be exactly five minutes')
    _keys(row, {'online','power_mw','state_age_h'} | ({'trajectory'} if 'trajectory' in row else set()), 'thermal initial boundary')
    _require(type(online) is int and online in (0,1) and type(row['online']) is int and row['online'] in (0,1),
             'thermal connected states must be integer binary')
    for name in ('power_mw','state_age_h'): _number(row[name], 'thermal initial '+name, 0)
    _number(power,'thermal interval power',0)
    if 'trajectory' in row: validate_cursor(row['trajectory'])
    applicable=[d for d in records if d['unit_id']==unit]
    if not applicable:
        _require('trajectory' not in row and online==row['online'], 'thermal transition lacks confirmed resource curve')
        return dict(online=online,power_mw=power,state_age_h=row['state_age_h']+DT_H)
    p=applicable[0]['parameters']; match=_active(records,unit,start)
    if match:
        d,i,s=match
        _require(_utc(s['end'])==_utc(end) and online==1, 'thermal curve step/state mismatch')
        _close(power,s['mean_mw'],'thermal fixed trajectory power')
        _close(row['power_mw'],s['start_mw'],'thermal actual initial endpoint')
        if i:
            _require(row.get('trajectory')==cursor_for(d,i),'thermal inherited trajectory cursor mismatch')
            _require(row['online']==1,'thermal inherited curve must be connected')
        else:
            _require('trajectory' not in row and row['online']==int(d['kind']=='shutdown'), 'thermal initial state/curve mismatch')
        state_since = _utc(d['steps'][0]['start']) if d['kind']=='startup' else _utc(d['preceding_state_started_at'])
        expected_age = (_utc(start)-state_since).total_seconds()/3600 if i or d['kind']=='shutdown' else (_utc(start)-_utc(d['preceding_state_started_at'])).total_seconds()/3600
        _close(row['state_age_h'],expected_age,'thermal initial state age/occurrence')
        finished = i+1==len(d['steps'])
        result=dict(online=0 if finished and d['kind']=='shutdown' else 1,
                    power_mw=s['end_mw'],state_age_h=0. if finished and d['kind']=='shutdown' else (_utc(end)-state_since).total_seconds()/3600)
        if not finished: result['trajectory']=cursor_for(d,i+1)
        return result
    _require('trajectory' not in row and row['online']==online, 'missing continuation or unapproved thermal transition')
    if online:
        _require(p['min_mw']<=row['power_mw']<=p['max_mw'] and p['min_mw']<=power<=p['max_mw'], 'thermal stable power outside resource bounds')
        _require(-p['ramp_down_mw_per_h']*DT_H-1e-7<=power-row['power_mw']<=p['ramp_up_mw_per_h']*DT_H+1e-7, 'thermal stable step exceeds resource ramp')
    else:
        _require(power==row['power_mw']==0,'offline thermal power must be zero')
    return dict(online=online,power_mw=power,state_age_h=row['state_age_h']+DT_H)


def validate_model_context(spec, thermal, commitment):
    _keys(spec, ('book','window_start','information_cutoff'), 'thermal model context')
    start,cutoff = _grid(spec['window_start']),_utc(spec['information_cutoff'])
    _require(start-cutoff==timedelta(minutes=15),'thermal model requires T-minus-15 cutoff')
    records=validate_book(spec['book'],cutoff=cutoff)
    _require({d['unit_id'] for d in records}<=set(thermal),'unknown thermal curve resource')
    cells={}; inherited=set()
    for unit,raw in thermal.items():
        ds=[d for d in records if d['unit_id']==unit]
        if not ds:
            _require(all(commitment[unit,t]==raw['initial_online'] for t in range(1,25)), 'thermal transition lacks confirmed curve')
            continue
        p=ds[0]['parameters']
        for key in ('min_up_h','min_down_h'):_close(raw[key],p[key],'model thermal '+key)
        for key in ('min_mw','max_mw','ramp_up_mw_per_h','ramp_down_mw_per_h'):
            _require(set(raw[key])==set(range(1,25)),'thermal conditions require all periods')
            for v in raw[key].values():_close(v,p[key],'model thermal '+key)
        row=dict(online=raw['initial_online'],power_mw=raw['initial_power_mw'],state_age_h=raw['initial_state_age_h'])
        initial_match=_active(records,unit,start)
        if initial_match is None:
            _require((row['online']==1 and p['min_mw']<=row['power_mw']<=p['max_mw']) or
                     (row['online']==0 and row['power_mw']==0), 'thermal model initial stable power outside resource conditions')
        if initial_match and initial_match[1]>0:
            d,i,_=initial_match;row['trajectory']=cursor_for(d,i);inherited.add(unit)
            _require(raw['initial_state_kind']==initial_trajectory_kind(spec['book']),'inherited thermal initial kind required')
        else:
            _require(raw['initial_state_kind']=='stable','unexpected thermal initial trajectory kind')
        for t in range(1,25):
            at=start+(t-1)*FIVE; match=_active(records,unit,at)
            if match:
                d,i,s=match
                # At a future curve entrance, dispatch must reach the confirmed
                # start endpoint. No assumed earlier optimized power is used.
                if t>1 and not _active(records,unit,at-FIVE):row['power_mw']=s['start_mw']
                row=advance_thermal(row,unit=unit,power=s['mean_mw'],online=commitment[unit,t],records=records,start=at,end=at+FIVE)
                cells[unit,t]=(d,i,s)
                _require(raw['fixed_power_mw'][t] is None,'trajectory fixed power must come only from curve record')
            else:
                _require('trajectory' not in row and commitment[unit,t]==row['online'], 'missing thermal continuation or instant commitment transition')
                row['state_age_h']+=DT_H
                if row['online']:row['power_mw']=max(p['min_mw'],row['power_mw'])
    return dict(records=records,cells=cells,inherited=inherited,start=start)


def attach_thermal_model(model, context):
    """Replace only confirmed trajectory rows; retain all stable constraints."""
    import pyomo.environ as pyo
    cells=context['cells']; thermal=model._energy_inputs['thermal']
    model.c_thermal_curve=pyo.ConstraintList(); model.c_thermal_curve_entry=pyo.ConstraintList()
    for (g,t),(d,i,s) in cells.items():
        model.c_power_sum[g,t].deactivate();model.c_ramp_up[g,t].deactivate();model.c_ramp_down[g,t].deactivate()
        for k in thermal[g]['segments']:model.segment_power[g,k,t].fix(0.)
        model.online_min[g,t].set_value(s['mean_mw']);model.online_max[g,t].set_value(s['mean_mw'])
        model.c_thermal_curve.add(model.power[g,t]==s['mean_mw'])
        if t==1:
            _close(thermal[g]['initial_power_mw'],s['start_mw'],'model thermal first curve endpoint')
        elif (g,t-1) not in cells:
            model.c_thermal_curve_entry.add(model.power[g,t-1]==s['start_mw'])
    for g in model.G:
        for t in model.T:
            if (g,t) not in cells and (g,t-1) in cells:
                end=cells[g,t-1][2]['end_mw'];d=thermal[g]
                model.c_ramp_up[g,t].set_value(model.power[g,t]-end<=d['ramp_up_mw_per_h'][t]*DT_H)
                model.c_ramp_down[g,t].set_value(end-model.power[g,t]<=d['ramp_down_mw_per_h'][t]*DT_H)
    model.thermal_bid_cost.set_value(sum(DT_H*(cells[g,t][2]['cost_yuan_per_h'] if (g,t) in cells else
        d['base_bid_yuan_per_h'][t]*model.online[g,t]+sum(s['price_yuan_per_mwh'][t]*model.segment_power[g,k,t]
        for k,s in d['segments'].items())) for g,d in thermal.items() for t in model.T))
