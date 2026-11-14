"""Research-only absolute 5-minute thermal state carrier. No optimizer or publication.
Inputs are caller-verified accepted/issued cell evidence, not a substitute for it.
"""
from copy import deepcopy
from datetime import datetime,timedelta
import math

def need(ok,msg):
    if not ok: raise ValueError(msg)
def integer(x): return type(x) is int
def classify(ticks,p):
    h,w=p['hot_upper_ticks'],p['warm_upper_ticks']
    need(integer(ticks) and ticks>=0 and integer(h) and integer(w) and 0<h<w,'classification ticks')
    need(p['classification_boundary_policy']=='hot_lt_H_warm_lt_W','unsupported classification policy')
    return 'hot' if ticks<h else 'warm' if ticks<w else 'cold'
def eq(a,b): return math.isfinite(a) and math.isfinite(b) and abs(a-b)<=1e-7

def advance(initial,parameters,events,cells,*,cutoff_tick,executed_cells,origin):
    """Cells are contiguous: executed prefix then already-issued nonexecuted lock.
    Returns BOTH cutoff/executed state and predicted issued-tail state. No collapsing
    these clocks, no notice creation, no future plan promoted to a commitment.
    Initial and output tick denote a boundary, while cell.tick denotes interval start.
    """
    s=deepcopy(initial);events=deepcopy(events);p=parameters
    o=datetime.fromisoformat(origin)
    need(o.utcoffset()==timedelta(hours=8),'explicit Guangdong absolute clock')
    need(p['basis']=='research' and p['source'] and p['parameter_sha256'],'explicit research declaration')
    need(p['min_up_basis']=='startup_begin' and p['min_down_basis']=='shutdown_end','unsupported minimum-time basis')
    need(all(integer(p[k]) and p[k]>=0 for k in ('min_up_ticks','min_down_ticks')),'minimum durations')
    need(integer(executed_cells) and 0<=executed_cells<=len(cells),'executed count')
    need(integer(s['tick']) and cutoff_tick==s['tick']+executed_cells,'one absolute execution clock')
    need(set(p['classes'])=={'hot','warm','cold'},'explicit three classes')
    for k,c in p['classes'].items():
        need(c['rows'] and c['parameter_sha256'] and integer(c['notice_ticks']) and c['notice_ticks']>=0,'class declaration')
    need(p['shutdown']['rows'] and p['shutdown']['parameter_sha256'],'shutdown declaration')
    need(s['phase'] in ('stable','off','startup','shutdown'),'initial phase')
    if s['phase']=='off':need(eq(s['end_power_mw'],0),'off boundary power')
    need(eq(p['shutdown']['rows'][-1]['end'],0),'shutdown must finish disconnected at zero power')
    need(integer(s['phase_since_tick']) and s['phase_since_tick']<=s['tick'],'phase clock')
    need(integer(s['connection_anchor_tick']) and s['connection_anchor_tick']<=s['tick'],'connection clock')
    need(all(integer(s[k]) and s[k]<=s['tick'] for k in ('min_up_anchor_tick','min_down_anchor_tick')),'minimum duration anchors')
    need(s['offline_ticks']==(s['tick']-s['last_disconnect_tick'] if s['phase']=='off' else 0),'initial offline age')
    # Only explicit, previously issued receipts can authorize simulated event replay.
    for eid,e in events.items():
        need(e['event_id']==eid and e['unit']==s['unit'] and e['phase'] in ('startup','shutdown'),'event identity')
        need(e['receipt_hash'] and e['source'] and e['issued'] is True,'unknown/unissued event receipt')
        need(integer(e['begin_tick']) and integer(e['issued_tick']) and e['issued_tick']<=cutoff_tick,'future notice cannot be invented')
        need(e['issued_tick']<=e['begin_tick'],'instruction after event start')
        k=e.get('kind');c=p['classes'][k] if e['phase']=='startup' else p['shutdown']
        need(e['curve_parameter_sha256']==c['parameter_sha256'] and e['rows']==c['rows'],'exact immutable selected curve')
        need(e['rows'],'empty trajectory')
        if e['phase']=='startup':need(e['issued_tick']+c['notice_ticks']<=e['begin_tick'],'notice lead unmet')
        for a,b in zip(e['rows'],e['rows'][1:]):need(eq(a['end'],b['begin']),'discontinuous curve')
    # Even obligations outside this replay must not silently overlap. This is a
    # structural consistency check, not proof of future ramp/network feasibility.
    ordered=sorted(events.values(),key=lambda e:(e['begin_tick'],e['event_id']))
    for previous,following in zip(ordered,ordered[1:]):
        need(previous['begin_tick']+len(previous['rows'])<=following['begin_tick'],
             'overlapping issued event trajectories')
    def annotate(state):
        state['phase_age_ticks']=state['tick']-state['phase_since_tick']
        state['connected']=int(state['phase']!='off')
        state['connection_state_age_ticks']=state['tick']-state['connection_anchor_tick']
        state['offline_ticks']=state['tick']-state['last_disconnect_tick'] if state['phase']=='off' else 0
        state['residual_up_ticks']=max(0,state['min_up_anchor_tick']+p['min_up_ticks']-state['tick'])
        state['residual_down_ticks']=max(0,state['min_down_anchor_tick']+p['min_down_ticks']-state['tick'])
        if state['phase'] in ('startup','shutdown'):
            e=events[state['event_id']];j=state['tick']-e['begin_tick']
            need(0<=j<len(e['rows']) and e['phase']==state['phase'] and state['phase_since_tick']==e['begin_tick'],'active event chronology')
            if state['phase']=='startup':need(state['connection_anchor_tick']==e['begin_tick'] and state['min_up_anchor_tick']==e['begin_tick'],'active startup anchors')
            need(eq(state['end_power_mw'],e['rows'][j]['begin']),'active boundary endpoint')
            state['event_elapsed_ticks']=j;state['remaining_curve']=deepcopy(e['rows'][j:])
            state['startup_kind']=e.get('kind')
            if state['phase']=='shutdown':state['residual_down_ticks']=len(e['rows'])-j+p['min_down_ticks']
        else:
            need(state['event_id'] is None,'inactive phase with event')
            state['event_elapsed_ticks']=0;state['remaining_curve']=[];state['startup_kind']=None
        return state
    annotate(s)
    if s['phase']=='startup':
        e=events[s['event_id']];need(e['kind']==classify(e['begin_tick']-s['last_disconnect_tick'],p),'inherited startup category cannot change')
    execution=deepcopy(s) if executed_cells==0 else None
    started=[]
    for i,cell in enumerate(cells):
        t=s['tick'];need(cell['tick']==t,'missing or duplicated cell')
        need(cell['evidence_hash'] and cell['source_kind']=='counterfactual_no_deviation','accepted simulation cell identity')
        if i>=executed_cells:need(cell['issued_receipt_hash'] and cell['issued_tick']<=cutoff_tick,'unissued future cell')
        starts=[e for e in events.values() if e['begin_tick']==t]
        need(len(starts)<=1,'simultaneous events')
        if starts:
            e=starts[0]
            if s['phase'] in ('startup','shutdown'):
                need(s['event_id']==e['event_id'] and e['begin_tick']==t,'cannot replace active trajectory')
            else:
                if e['phase']=='startup':
                    need(s['phase']=='off' and s['residual_down_ticks']==0,'startup while connected/down obligation')
                    need(e['kind']==classify(t-s['last_disconnect_tick'],p),'startup kind fixed at actual begin')
                    s['connection_anchor_tick']=t;s['min_up_anchor_tick']=t
                else:need(s['phase']=='stable' and s['residual_up_ticks']==0,'shutdown while off/up obligation')
                need(eq(s['end_power_mw'],e['rows'][0]['begin']),'event begins at actual endpoint')
                s.update(phase=e['phase'],phase_since_tick=t,event_id=e['event_id'])
            started.append(e['event_id'])
        expected_start=int(bool(starts) and starts[0]['phase']=='startup');expected_stop=int(bool(starts) and starts[0]['phase']=='shutdown')
        need(cell['start']==expected_start and cell['stop']==expected_stop,'event missing or invented by cells')
        need(cell['connected']==int(s['phase']!='off'),'cell connectivity')
        if s['phase'] in ('startup','shutdown'):
            e=events[s['event_id']];j=t-e['begin_tick'];r=e['rows'][j]
            need(all(eq(cell[a+'_mw'],r[a]) for a in ('begin','mean','end')),'active curve power/energy mismatch')
        else:
            need(eq(cell['begin_mw'],s['end_power_mw']),'stable boundary jump')
            if s['phase']=='off':need(all(eq(cell[a+'_mw'],0) for a in ('begin','mean','end')),'off power')
        s['tick']=t+1;s['end_power_mw']=cell['end_mw']
        if s['phase'] in ('startup','shutdown') and s['tick']==e['begin_tick']+len(e['rows']):
            phase='stable' if e['phase']=='startup' else 'off'
            s.update(phase=phase,phase_since_tick=s['tick'],event_id=None)
            if phase=='off':s.update(last_disconnect_tick=s['tick'],connection_anchor_tick=s['tick'],min_down_anchor_tick=s['tick'])
        annotate(s)
        if i+1==executed_cells:execution=deepcopy(s)
    return dict(schema='gd.research.thermal.exact_carry.v1',executed_boundary=execution,predicted_issued_boundary=s,
        events=events,future_issued_obligations={k:v for k,v in events.items() if v['begin_tick']>=s['tick']},started_events=started,
        executed_at=(o+timedelta(minutes=5*execution['tick'])).isoformat(),predicted_boundary_at=(o+timedelta(minutes=5*s['tick'])).isoformat(),
        notice_receipts_created=0,native_calls=0,formal=False,actual_telemetry_claimed=False,source_identity_verified_here=False,
        future_obligations_feasibility='not_certified_requires_model_validation',
        stable_cell_physics_verified_here=False,
        protection_policy_changed=False,safety_release=False)


def to_short_boundary(result,*,predicted,available_at,source):
    """Explicit adapter to existing free-SCUC boundary fields, NOT an installer.
    Caller must additionally bind selected typed curve to the model's curves and
    preserve DA protection/permissions, reserve, ramp, min time and all source checks.
    """
    s=result['predicted_issued_boundary' if predicted else 'executed_boundary']
    need(source and datetime.fromisoformat(available_at).utcoffset()==timedelta(hours=8),'adapter provenance')
    b={k:deepcopy(s[k]) for k in ('phase','end_power_mw','residual_up_ticks','residual_down_ticks','event_id','event_elapsed_ticks','remaining_curve')}
    b.update(source=source,available_at=available_at,min_up_basis='startup_begin',min_down_basis='shutdown_end')
    return dict(thermal={s['unit']:b},thermal_age_h={s['unit']:s['connection_state_age_ticks']/12},
        age_basis='connected since startup begin; off since actual shutdown completion',
        typed_state=deepcopy(s),events=deepcopy(result['events']),predicted_issued_prefix=predicted,
        notice_receipts_created=0,formal=False,safety_release=False)
