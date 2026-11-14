"""Data-only thermal tail obligations. No model construction or solver invocation.
Not an acceptance verifier, terminal network certificate, nor automatic handoff.
"""
import math, json, hashlib
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path


def derive(case, boundary, names, primal):
    ns=names['variables']; assert len(ns)==len(primal) and len(set(ns))==len(ns)
    assert all(isinstance(v,(int,float)) and math.isfinite(v) for v in primal)
    v=dict(zip(ns,primal)); ds=case['energy_inputs']['thermal']; curves=case['curves']
    n=len(case['energy_inputs']['horizon']['periods']); b=boundary['thermal']; assert set(b)==set(ds)
    clock=case['contract']; origin=datetime.fromisoformat(clock['origin']); begin=datetime.fromisoformat(clock['domain_start']); cutoff=datetime.fromisoformat(clock['cutoff'])
    assert all(x.utcoffset() is not None for x in (origin,begin,cutoff))
    offset=(begin-origin).total_seconds()/300; assert offset==int(offset); offset=int(offset)
    def binary(stem,g,t):
        x=v[f'{stem}[{g},{t}]']; assert abs(x-round(x))<=1e-6 and round(x) in (0,1)
        return int(round(x))
    out={}
    for g,z in b.items():
        starts=[t for t in range(n) if binary('start',g,t)]
        stops=[t for t in range(n) if binary('stop',g,t)]
        events=[]
        if z['remaining_curve']:
            events.append(dict(event_id=z['event_id'],phase=z['phase'],local_begin=-z['event_elapsed_ticks'],remaining_at_origin=deepcopy(z['remaining_curve']),inherited=True,notice_status='must_retain_original_receipt_not_reconstructed'))
        for kind,ticks in [('startup',starts),('shutdown',stops)]:
            for t in ticks:
                e=dict(event_id=None,phase=kind,local_begin=t,inherited=False,remaining_at_origin=deepcopy(curves[g][kind]))
                if kind=='startup':
                    lead=curves[g]['notice_h']*12; assert abs(lead-round(lead))<=1e-6
                    nt=offset+t-round(lead); e.update(notice_tick=nt,notice_at=(origin+timedelta(minutes=5*nt)).isoformat(),notice_status='requires_bound_issued_receipt' if origin+timedelta(minutes=5*nt)<cutoff else 'decision_not_proof_of_issued_notice')
                events.append(e)
        pending=[]; mean=end=0.
        for e in events:
            rows=e['remaining_at_origin']; first=0 if e['inherited'] else e['local_begin']; j=n-1-first
            if 0<=j<len(rows):mean+=rows[j]['mean'];end+=rows[j]['end']
            consumed=n-first
            if consumed<len(rows):
                q=deepcopy(e);q.pop('remaining_at_origin');q['remaining_curve']=deepcopy(rows[consumed:]);pending.append(q)
        assert len(pending)<=1,'overlapping pending trajectories'
        up=max([max(0,z['residual_up_ticks']-n)]+[max(0,t+math.ceil(ds[g]['min_up_h']*12)-n) for t in starts])
        down=max([max(0,z['residual_down_ticks']-n)]+[max(0,t+len(curves[g]['shutdown'])+math.ceil(ds[g]['min_down_h']*12)-n) for t in stops]+([max(0,len(z['remaining_curve'])+math.ceil(ds[g]['min_down_h']*12)-n)] if z['phase']=='shutdown' else []))
        if pending:phase=pending[0]['phase']
        else:
            completed=[(0 if e['inherited'] else e['local_begin'])+len(e['remaining_at_origin']) for e in events]
            latest=max(zip(completed,events),key=lambda x:x[0])[1] if events else None
            phase=('stable' if latest['phase']=='startup' else 'off') if latest else z['phase']
        out[g]=dict(phase_at_end_boundary=phase,end_power_mw=v[f'power[{g},{n-1}]']-mean+end,pending_curves=pending,residual_min_up_ticks=up,residual_until_next_start_ticks=down,min_up_basis=z['min_up_basis'],min_down_basis=z['min_down_basis'],events_in_domain_or_inherited=events,preserved_issued_notice_ledger=deepcopy(boundary.get('issued_notices',{}).get(g,[])))
    return dict(schema='gd.short.thermal.tail.obligations.v1',boundary_at=(begin+timedelta(minutes=5*n)).isoformat(),thermal=out,native_calls=0,formal=False,full_primal_verified_here=False,future_network_feasibility_proven=False,automatic_handoff_allowed=False)


def from_files(case_path,boundary_path,names_path,primal_path):
    # Caller supplies the existing data codec on PYTHONPATH; no executable model load.
    from smm.gd_october_job_codec import loads
    paths=list(map(Path,(case_path,boundary_path,names_path,primal_path)))
    c=loads(paths[0].read_bytes());b,ns,sol=[json.loads(p.read_bytes()) for p in paths[1:]]
    out=derive(c,b,ns,sol['primal']);out['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths};return out

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--case',required=True);ap.add_argument('--boundary',required=True);ap.add_argument('--names',required=True);ap.add_argument('--primal',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    result=from_files(a.case,a.boundary,a.names,a.primal)
    with open(a.out,'x') as f:json.dump(result,f,indent=2,allow_nan=False)
