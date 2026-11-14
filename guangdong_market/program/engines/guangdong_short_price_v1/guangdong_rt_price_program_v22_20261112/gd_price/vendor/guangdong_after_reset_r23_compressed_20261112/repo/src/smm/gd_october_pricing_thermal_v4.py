"""Source-derived startup-only IEEE39 research pricing, never execution authority.

No solver is invoked. Raw bid/date identity, complete trajectory records and
precommitted research eligibility remain distinct sources of evidence.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

from .gd_october_pricing_inputs import require, num, boolean
from .gd_october_thermal import (validate_book, validate_model_context, _active,
    advance_thermal, _utc, FIVE)

BASE_FIELDS = {'minimum_mw','maximum_mw','agc_up_mw','agc_down_mw','pricing_eligible',
    'ramp_up_mw_h','ramp_down_mw_h','bids','positive_eligible','negative_eligible',
    'primary_eligible','primary_fraction','accident_eligible','accident_ramp_10min_mw'}
EXTRA_FIELDS = {'phase','event_id','curve_record_sha256','source_step_indices',
    'source_bid_base_mw','source_base_cost_yuan_per_h','fixed_power_mw',
    'transition_cost_yuan_per_h','eligibility_source_ref'}
STATE_FIELDS = {'start','end','online','transition','phase','event_id',
    'curve_record_sha256','source_step_index','start_mw','end_mw'}


def _same(a, b, label):
    require(math.isfinite(float(a)) and math.isfinite(float(b)) and
            abs(float(a)-float(b)) <= 1e-7, label+' mismatch')


def _constant(values, indices, label):
    vs = [values[i] for i in indices]
    require(len(set(vs)) == 1, label+': intra-quarter change unsupported')
    return vs[0]


def _integral(segments, power):
    return sum(max(0., min(power, s['p_end'])-s['p_start'])*s['price'] for s in segments)


def _sources(bridge):
    from .gd_october_job_codec import loads
    from .gd_october_ieee39_scenario import validate_scenario
    import yaml
    raw = bridge['source_artifacts']['r10-capacity-base-scenario']
    base = loads(raw); validate_scenario(base)
    static = base['static_artifacts']; root = Path(__file__).resolve().parents[3]/'inputs/ieee39_v6'
    for name in ('resolved_bids.json','config/units.yaml'):
        require((root/name).read_bytes() == static[name], 'original static source replacement: '+name)
    bids = json.loads(static['resolved_bids.json']); units = yaml.safe_load(static['config/units.yaml'])
    require(bids == base['static']['bids'] and units == base['static']['units'], 'decoded static source mismatch')
    return base, bids, {r['id']:r for r in units['thermal']}, dict(
        source_id='r10-capacity-base-scenario', source_sha256=sha256(raw).hexdigest(),
        bid_sha256=sha256(static['resolved_bids.json']).hexdigest(),
        units_sha256=sha256(static['config/units.yaml']).hexdigest())


def derive_thermal_pricing_rows(bridge, model, steps, eligibility):
    """Return thermal, 24-step power, phase states, and audit from live ED steps.

    eligibility is a precommitted source declaration with thermal, source_ref,
    published_at, available_at, source and reason. Issuer checks its byte binding.
    """
    import pyomo.environ as pyo
    core = bridge['physical_inputs']['model_inputs']; thermal = core['thermal']
    base, bids, units, source_ref = _sources(bridge)
    require(set(thermal) == set(units), 'complete original thermal inventory required')
    context = core['thermal_trajectories']; start = _utc(context['window_start'])
    require(context['book'] == base['thermal_book'], 'complete original thermal book must be retained')
    records = validate_book(context['book'], cutoff=context['information_cutoff'], run_id=base['run_id'])
    require(len(records) == 6, 'all six original thermal records required')
    validate_model_context(context, thermal, core['commitment'])
    preflight = steps is None
    if preflight:
        steps = [dict(delivery_start=(start+j*FIVE).isoformat(), delivery_end=(start+(j+1)*FIVE).isoformat(),
                      states=dict(thermal={g:core['commitment'][g,j+1] for g in thermal})) for j in range(24)]
    require(len(steps) == 24, 'exact live 24-step source required')
    require(type(eligibility) is dict and set(eligibility['thermal']) == set(thermal), 'complete research price declaration required')
    require(_utc(eligibility['published_at']) <= _utc(eligibility['available_at']) <= _utc(context['information_cutoff']),
            'research eligibility unavailable at source cutoff')
    require(bool(eligibility['source']) and bool(eligibility['reason']), 'eligibility source and reason required')
    require(set(eligibility.get('fixed_restrictions',{})) == set(thermal), 'complete fixed-restriction declaration required')
    require(eligibility.get('permission_basis') == 'authored_research_hypothesis_not_operator_qualification', 'explicit research eligibility basis required')
    eref = eligibility['source_ref']
    require(type(eref) is dict and set(eref) == {'source_id','source_sha256'} and
            len(eref['source_sha256']) == 64, 'eligibility original source reference required')
    for j, step in enumerate(steps):
        require(_utc(step['delivery_start']) == start+j*FIVE and
                _utc(step['delivery_end']) == start+(j+1)*FIVE, 'live source step order/time mismatch')
    result, power, states, audit_units = {}, {}, {}, {}
    original_cost = mean_cost = constant = 0.
    for g, d in thermal.items():
        u = units[g]; raw = bids['units'][g]['segments']; b = float(u['pmin']); maximum = float(u['pmax'])
        require(len(raw) == 6 and len(d['segments']) == 5, 'original base plus five increments required')
        previous = 0.; prices = []
        for s in raw:
            for k in ('p_start','p_end','price'): num(s[k], 'original bid '+k)
            _same(s['p_start'], previous, 'continuous original bid'); require(s['p_end'] > previous, 'positive original bid width required')
            previous = s['p_end']; prices.append(s['price'])
        require(prices == sorted(prices), 'nondecreasing original bids required')
        _same(raw[0]['p_end'], b, 'source base endpoint'); _same(previous, maximum, 'source bid rated endpoint')
        base_cost = _integral(raw,b); _same(d['bid_base_mw'],b,'source base power')
        for t in range(1,25):
            _same(d['base_bid_yuan_per_h'][t],base_cost,'source base cost')
            for key, value in [('min_mw',b),('max_mw',maximum),('ramp_up_mw_per_h',float(u['ramp'])*60),('ramp_down_mw_per_h',float(u['ramp'])*60)]:
                _same(d[key][t],value,'raw physical '+key)
            for key in ('up_mw','down_mw'): _same(core['agc']['thermal'][g][key][t],0,'unsupported thermal AGC')
            require(d['fixed_power_mw'][t] is None,'separate fixed thermal schedule unsupported')
        for seg, original in zip(d['segments'],raw[1:]):
            width = original['p_end']-original['p_start']; _same(seg['width_mw'],width,'original increment width')
            require(width >= .01*(maximum-b), 'original incremental width below one percent')
            for t in range(1,25): _same(seg['price_yuan_per_mwh'][t],original['price'],'original increment price')
        _same(b+sum(s['width_mw'] for s in d['segments']),maximum,'base plus increment coverage')
        declarations = eligibility['thermal'][g]
        require(type(declarations) is list and len(declarations)==8, 'eight explicit research eligibility values required')
        for v in declarations: boolean(v,'research eligibility')
        restrictions=eligibility['fixed_restrictions'][g]
        require(type(restrictions) is list and len(restrictions)==8,'eight fixed restrictions required')
        for v in restrictions: boolean(v,'fixed restriction')
        require(not any(a and b for a,b in zip(declarations,restrictions)),'restricted unit cannot be price eligible')
        initial_match = _active(records,g,start)
        require(initial_match is None or initial_match[1] == 0,'window starting inside a curve unsupported')
        boundary = dict(online=d['initial_online'],power_mw=d['initial_power_mw'],state_age_h=d['initial_state_age_h'])
        rows = []; strows = []; prows = []; matches = []
        for j, step in enumerate(steps):
            t=j+1; at=start+j*FIVE; match=_active(records,g,at); matches.append(match)
            on=step['states']['thermal'][g]; mw=(match[2]['mean_mw'] if match else None) if preflight else step['powers_mw']['thermal'][g]
            require(type(on) is int and on in (0,1) and on == core['commitment'][g,t], 'live thermal commitment mismatch')
            if not preflight: num(mw,'live thermal power',0)
            phase=match[0]['kind'] if match else ('stable' if on else 'off')
            require(phase != 'shutdown','shutdown pricing not implemented')
            if match:
                record, cursor, s = match
                _same(mw,s['mean_mw'],'live thermal trajectory mean')
                _same(s['cost_yuan_per_h'],float(u['noload'])+raw[0]['price']*mw,'source trajectory rate')
                lo=hi=mw; original_cost+=s['cost_yuan_per_h']/12
            else:
                lo=b if on else 0.; hi=maximum if on else 0.
                if not preflight: original_cost+=_integral(raw,mw)/12 if on else 0.
            for name, value in [('online_min',lo),('online_max',hi),('effective_min',lo),('effective_max',hi)]:
                _same(pyo.value(getattr(model,name)[g,t]),value,'rebuilt '+name)
            before=boundary['power_mw']
            if not preflight: boundary=advance_thermal(boundary,unit=g,power=mw,online=on,records=records,start=at,end=at+FIVE)
            prows.append(dict(start=step['delivery_start'],end=step['delivery_end'],mw=mw))
            strows.append(dict(start=step['delivery_start'],end=step['delivery_end'],online=bool(on),transition=bool(match),phase=phase,
                event_id=match[0]['event_id'] if match else None,curve_record_sha256=match[0]['_sha256'] if match else None,
                source_step_index=match[1] if match else None,start_mw=match[2]['start_mw'] if match else (None if preflight else before),end_mw=match[2]['end_mw'] if match else (None if preflight else boundary['power_mw'])))
        for q in range(8):
            js=list(range(q*3,q*3+3)); ts=[j+1 for j in js]; ss=[strows[j] for j in js]; mm=[matches[j] for j in js]
            require(len({s['phase'] for s in ss})==1,'mixed-phase quarter rejected')
            phase=ss[0]['phase']; active=phase=='startup'; stable=phase=='stable'; on=phase!='off'
            require(not declarations[q] or stable,'off/activity price declaration must be false')
            pref=(None if stable else (sum(m[2]['mean_mw'] for m in mm)/3 if active else 0.)) if preflight else sum(prows[j]['mw'] for j in js)/3
            if active:
                require(len({s['event_id'] for s in ss})==1 and len({s['curve_record_sha256'] for s in ss})==1,'same-event quarter required')
                require([m[1] for m in mm]==list(range(mm[0][1],mm[0][1]+3)),'consecutive trajectory cursor required')
            rate=sum(m[2]['cost_yuan_per_h'] for m in mm)/3 if active else 0.
            lo=b if stable else pref; hi=maximum if stable else pref
            rr=core['reserves']['thermal'][g]
            pe=_constant(rr['primary_eligible'],ts,'primary eligibility'); ae=_constant(rr['contingency_eligible'],ts,'accident eligibility')
            if not stable: require(not pe and not ae,'nonstable flexible service forbidden')
            row=dict(minimum_mw=lo,maximum_mw=hi,agc_up_mw=0.,agc_down_mw=0.,pricing_eligible=declarations[q],
                ramp_up_mw_h=_constant(d['ramp_up_mw_per_h'],ts,'ramp up'),ramp_down_mw_h=_constant(d['ramp_down_mw_per_h'],ts,'ramp down'),
                bids=[dict(width_mw=s['width_mw'],yuan_per_mwh=_constant(s['price_yuan_per_mwh'],ts,'bid price')) for s in d['segments']],
                positive_eligible=on,negative_eligible=on,primary_eligible=bool(pe) and stable,
                primary_fraction=_constant(rr['primary_fraction'],ts,'primary fraction'),accident_eligible=bool(ae) and stable,
                accident_ramp_10min_mw=_constant(rr['ramp_10min_mw'],ts,'accident ramp'),phase=phase,
                event_id=ss[0]['event_id'],curve_record_sha256=ss[0]['curve_record_sha256'],
                source_step_indices=[s['source_step_index'] for s in ss] if active else None,
                source_bid_base_mw=b,source_base_cost_yuan_per_h=base_cost,fixed_power_mw=None if stable else pref,
                transition_cost_yuan_per_h=rate,eligibility_source_ref=deepcopy(eref))
            rows.append(row); constant+=.25*(base_cost if stable else rate)
            if not preflight: mean_cost+=.25*(_integral(raw,pref) if stable else rate)
        result[g]=dict(node=d['bus'],initial_power_mw=d['initial_power_mw'],rows=rows,
            original_bid_source=deepcopy(source_ref),original_curve_ref=dict(source_id='retained_original_thermal_book',record_sha256=[r['_sha256'] for r in records if r['unit_id']==g]))
        power[g]=prows; states[g]=strows
        audit_units[g]=dict(source_bid_base_mw=b,source_base_cost_yuan_per_h=base_cost,phases=[r['phase'] for r in rows],initial=deepcopy(dict(online=d['initial_online'],power_mw=d['initial_power_mw'],state_age_h=d['initial_state_age_h'])))
    audit=dict(active_event_ids=sorted({s['event_id'] for rows in states.values() for s in rows if s['event_id'] is not None}),source_ref=source_ref,book_record_sha256=[r['_sha256'] for r in records],thermal=audit_units,
        source_bid_date=bids.get('date'),source_bid_created=bids.get('created'),delivery_day=start.date().isoformat(),
        source_bid_operational_validity_verified=False,eligibility_source_ref=deepcopy(eref),eligibility_authenticity_verified=False,
        objective_constant_yuan=constant,original_ed_cost=None if preflight else original_cost,price_model_cost_at_ed_mean=None if preflight else mean_cost,
        aggregation_cost_gap=None if preflight else original_cost-mean_cost,pricing_optimum_cost=None,
        pricing_ramp_basis='quarter_mean_research_not_exact_5min_projection',pricing_dispatch_executable=False)
    return result,None if preflight else power,states,audit


def validate_thermal_pricing_v4(spec, states, reference, selected, initial=None):
    """Validate V4 row/state algebra; source equality is enforced by live binding.

    `reference` contains already validated eight-quarter means. `selected` is
    filled with connected-state Booleans. The state envelope retains initial.
    """
    thermal=spec['thermal']; sr=states['rows']; initial=states['initial'] if initial is None else initial
    require(set(sr)==set(reference)==set(initial)==set(thermal),'V4 thermal/state coverage mismatch')
    for g,d in thermal.items():
        require(set(d)=={'node','initial_power_mw','rows','original_bid_source','original_curve_ref'},'V4 thermal unit fields')
        require(d['node'] in spec['nodes'],'V4 thermal node unknown'); num(d['initial_power_mw'],'initial power',0)
        boolean(initial[g],'initial online'); require(len(d['rows'])==8 and len(sr[g])==24,'V4 exact thermal grids required')
        if not initial[g]: _same(d['initial_power_mw'],0,'offline initial power')
        selected[g]=[]
        for j,s in enumerate(sr[g]):
            require(set(s)==STATE_FIELDS,'V4 state fields'); boolean(s['online'],'online'); boolean(s['transition'],'transition')
            require(_utc(s['end'])-_utc(s['start'])==FIVE,'V4 five-minute state duration')
            if j: require(_utc(sr[g][j-1]['end'])==_utc(s['start']),'V4 state continuity')
            require(s['phase'] in {'off','startup','stable'},'unsupported V4 phase')
            require(s['online']==(s['phase']!='off') and s['transition']==(s['phase']=='startup'),'V4 phase Boolean mismatch')
            if s['transition']:
                require(type(s['event_id']) is str and bool(s['event_id']) and type(s['curve_record_sha256']) is str and len(s['curve_record_sha256'])==64 and type(s['source_step_index']) is int and s['source_step_index']>=0,'V4 active event/hash/cursor required')
            else: require(all(s[k] is None for k in ('event_id','curve_record_sha256','source_step_index')),'V4 inactive event fields must be null')
            for k in ('start_mw','end_mw'): num(s[k],k,0)
            if j: _same(s['start_mw'],sr[g][j-1]['end_mw'],'V4 source endpoint continuity')
            else: _same(s['start_mw'],d['initial_power_mw'],'V4 source initial endpoint')
        for q,r in enumerate(d['rows']):
            require(set(r)==BASE_FIELDS|EXTRA_FIELDS,'V4 thermal row fields')
            ss=sr[g][q*3:q*3+3]; phase=r['phase']; stable=phase=='stable'; active=phase=='startup'; on=phase!='off'
            require(len({s['phase'] for s in ss})==1 and ss[0]['phase']==phase,'V4 mixed or forged phase')
            selected[g].append(on)
            for key in ('pricing_eligible','positive_eligible','negative_eligible','primary_eligible','accident_eligible'): boolean(r[key],key)
            require(r['positive_eligible']==r['negative_eligible']==on,'V4 absolute capacity participation mismatch')
            for key in ('minimum_mw','maximum_mw','agc_up_mw','agc_down_mw','ramp_up_mw_h','ramp_down_mw_h','primary_fraction','accident_ramp_10min_mw','source_bid_base_mw','source_base_cost_yuan_per_h','transition_cost_yuan_per_h'): num(r[key],key,0)
            require(r['primary_fraction']<=1 and r['agc_up_mw']==r['agc_down_mw']==0,'V4 unsupported AGC/fraction')
            require(type(r['bids']) is list and len(r['bids'])==5,'V4 original five increments required')
            widths=[]; prices=[]
            for b in r['bids']:
                require(set(b)=={'width_mw','yuan_per_mwh'},'V4 bid fields')
                widths.append(num(b['width_mw'],'bid width',0)); prices.append(num(b['yuan_per_mwh'],'bid price'))
            require(min(widths)>0 and prices==sorted(prices),'V4 bid width/convexity')
            require(min(widths)>=.01*sum(widths)-1e-7,'V4 minimum increment width')
            require(r['minimum_mw']-1e-7<=reference[g][q]<=r['maximum_mw']+1e-7,'V4 reference outside physical bounds')
            if stable:
                require(r['fixed_power_mw'] is None and r['transition_cost_yuan_per_h']==0,'stable fixed/transition fields')
                _same(r['minimum_mw'],r['source_bid_base_mw'],'stable Pmin'); _same(r['source_bid_base_mw']+sum(widths),r['maximum_mw'],'base plus increments')
            else:
                require(not any(r[k] for k in ('pricing_eligible','primary_eligible','accident_eligible')),'nonstable price/flexible service forbidden')
                for k in ('minimum_mw','maximum_mw','fixed_power_mw'): _same(r[k],reference[g][q],'fixed nonstable power')
                if not active:
                    _same(reference[g][q],0,'off power'); _same(r['transition_cost_yuan_per_h'],0,'off cost')
            if active:
                require(len({s['event_id'] for s in ss})==len({s['curve_record_sha256'] for s in ss})==1,'V4 same event/hash required')
                indices=[s['source_step_index'] for s in ss]
                require(indices==list(range(indices[0],indices[0]+3)) and r['source_step_indices']==indices,'V4 consecutive cursor mismatch')
                require(r['event_id']==ss[0]['event_id'] and r['curve_record_sha256']==ss[0]['curve_record_sha256'],'V4 row event/hash mismatch')
            else: require(all(r[k] is None for k in ('event_id','curve_record_sha256','source_step_indices')),'V4 inactive row event fields must be null')
    return selected
