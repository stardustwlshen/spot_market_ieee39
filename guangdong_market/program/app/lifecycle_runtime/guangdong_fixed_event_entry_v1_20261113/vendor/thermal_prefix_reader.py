"""Counterfactual no-deviation execution-prefix carry; no solve or automatic notice issuance."""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json
from thermal_tail_reader import derive


def derive_prefix(case,incoming_boundary,names,primal,executed_cells=1):
    """Requires caller-accepted SCED primal. Does not prove dispatch/telemetry or source identity.
    incoming_boundary is BOUNDARY wrapper {thermal:...,issued_notices:...}, or thermal mapping.
    returned boundary_states can be passed to build_free_scuc after input P/status updates.
    """
    assert type(executed_cells)is int and executed_cells>0
    n=len(case['energy_inputs']['horizon']['periods']);assert executed_cells<=n
    incoming=deepcopy(incoming_boundary if 'thermal'in incoming_boundary else {'thermal':incoming_boundary})
    short=deepcopy(case);short['energy_inputs']['horizon']['periods']=short['energy_inputs']['horizon']['periods'][:executed_cells]
    tail=derive(short,incoming,names,primal)
    digest=hashlib.sha256(json.dumps(dict(names=names,primal=primal),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    start=datetime.fromisoformat(case['contract']['domain_start']);origin=datetime.fromisoformat(case['contract']['origin']);offset=int((start-origin).total_seconds()/900)
    cutoff=case['contract']['cutoff'];vals=dict(zip(names['variables'],primal));states={};notices={};future={}
    for g,z in tail['thermal'].items():
        old=incoming['thermal'][g];pending=z['pending_curves'];event_id=None;elapsed=0;remaining=[]
        if pending:
            q=pending[0];remaining=q['remaining_curve']
            if q['inherited']:
                event_id=old['event_id'];elapsed=old['event_elapsed_ticks']+executed_cells
            else:
                elapsed=executed_cells-q['local_begin'];event_id=f"simulated:{digest[:20]}:{g}:{q['phase']}:{offset+q['local_begin']}"
        states[g]=dict(phase=z['phase_at_end_boundary'],source='counterfactual no-deviation SCED prefix; caller must independently accept primal; not telemetry; vector_sha256='+digest,available_at=cutoff,end_power_mw=z['end_power_mw'],residual_up_ticks=z['residual_min_up_ticks'],residual_down_ticks=z['residual_until_next_start_ticks'],min_up_basis=old['min_up_basis'],min_down_basis=old['min_down_basis'],event_id=event_id,event_elapsed_ticks=elapsed,remaining_curve=deepcopy(remaining))
        notices[g]=dict(preserved_issued_notices=deepcopy(incoming.get('issued_notices',{}).get(g,[])),executed_startup_requirements=[deepcopy(e) for e in z['events_in_domain_or_inherited'] if e['phase']=='startup'],receipts_created=0)
        plans=[]
        for t in range(executed_cells,n):
            for phase,stem in [('startup','start'),('shutdown','stop')]:
                x=vals[f'{stem}[{g},{t}]'];assert abs(x-round(x))<=1e-6 and round(x)in(0,1)
                if round(x):
                    q=dict(phase=phase,target_tick=offset+t,executed=False,issued=False,receipt=None)
                    if phase=='startup':q['required_notice_tick']=offset+t-round(case['curves'][g]['notice_h']*4)
                    plans.append(q)
        future[g]=plans
    return dict(schema='gd.short.thermal.prefix.counterfactual.v1',boundary_states=states,boundary_at=tail['boundary_at'],executed_cells=executed_cells,notice_requirements=notices,future_unexecuted_plan=future,primal_sha256=digest,source_kind='counterfactual_no_deviation',native_calls=0,formal=False,full_primal_verified_here=False,actual_execution_claimed=False,notice_issuance_authorized=False)
