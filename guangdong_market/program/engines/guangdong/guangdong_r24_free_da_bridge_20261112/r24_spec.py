"""Read-only legacy data facts -> NEW research spec; never inherit acceptance."""
from pathlib import Path
from hashlib import sha256
import json
import numpy as np
from r24_bridge import require,digest,case_digest,stamp

def from_core95(case,lmp_path,sced_path):
    paths=[Path(lmp_path),Path(sced_path)];raw=[p.read_bytes()for p in paths];lmp,sced=[json.loads(x)for x in raw]
    e=case['energy_inputs'];nodes=list(e['buses']);lines=e['lines'];require(not e.get('sections'),'section crosscheck not implemented')
    require(set(lmp['nodes'])==set(nodes)and set(lmp['network'])==set(lines)==set(sced['network']),'same original network inventory')
    # Independently reconstruct transfer factors from original R23 angles network.
    ix={n:i for i,n in enumerate(nodes)};A=np.zeros((len(lines),len(nodes)));bs=[]
    for row,(name,z)in enumerate(lines.items()):A[row,ix[z['from_bus']]]=1;A[row,ix[z['to_bus']]]=-1;bs.append(z['susceptance_mw_per_rad'])
    M=A.T@np.diag(bs)@A;slack='39';keep=[ix[n]for n in nodes if n!=slack];theta=np.zeros((len(nodes),len(nodes)));theta[np.ix_(keep,keep)]=np.linalg.inv(M[np.ix_(keep,keep)]);ptdf=np.diag(bs)@A@theta
    network={};err=0.
    for row,(name,z)in enumerate(lines.items()):
        old=lmp['network'][name];require(old==sced['network'][name],'SCED/LMP network facts disagree')
        for n in nodes:err=max(err,abs(old['ptdf'][n]-float(ptdf[row,ix[n]])))
        for field in ('lower_mw','upper_mw'):
            oldphys=lmp['physical_lift']['network'][name][field]
            require(all(abs(z[field][j]-oldphys[j])<=1e-9 for j in range(576)),'physical line bounds differ')
        network[name]={k:old[k]for k in ('kind','ptdf','penalty_yuan_mwh')};network[name]['penalty_source']='inherited numeric research parameter; original core95 label: '+old['penalty_source']
    require(err<1e-8,'PTDF incompatible with original angles network')
    reps=[]
    for q,old in enumerate(lmp['grid'][96:]):
        ids=lmp['physical_lift']['market_to_physical'][96+q];require(ids==list(range(ids[0],ids[0]+3))and ids[0]==case['contract']['representatives'][q]['physical_index'],'same representative mapping required')
        require(stamp(old['start'])==stamp(case['contract']['day_start'])+__import__('datetime').timedelta(minutes=5*ids[0]),'representative timestamp mismatch')
        reps.append(dict(kind=old['kind'],physical_index=ids[0],objective_weight_h=old['objective_weight_h'],mapping_source='core95 numeric research mapping; new R24 identity'))
    spec=dict(mode='research',source='R24 newly bound core95 numeric research facts; NOT legacy certification',available_at=case['contract']['cutoff'],availability_semantics='simulated predeclared research input, not actual historical publication',delta=lmp['delta'],delta_source=lmp['profile']['delta_source'],representatives=reps,thermal_price_eligible={g:True for g in e['thermal']},eligibility_source=lmp['profile']['eligibility_source']+'; stable phase only, no official qualification asserted',network=network,rule_source=lmp['profile']['rule_source'],terminal_source='R23 original D96 target; new explicit research mapping, not official declaration',source_fact_pins={str(p):sha256(x).hexdigest()for p,x in zip(paths,raw)},source_field_hashes={k:digest(lmp[k])for k in ('network','grid','profile','physical_lift')},new_case_sha256=case_digest(case),PTDF_crosscheck_max_abs=err,legacy_acceptance_reused=False,continuous48h_cost_NOT_DA98_objective=True,formal_strict_allowed=False)
    return spec

if __name__=='__main__':
    import argparse
    from smm.gd_october_job_codec import loads
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--core',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    case=loads(Path(a.case).read_bytes());core=Path(a.core)/'ieee39_native_v4b/reports';s=from_core95(case,core/'lmp/input.json',core/'sced/input.json');Path(a.output).write_text(json.dumps(s,indent=2,sort_keys=True)+'\n');print(json.dumps({'spec_sha256':digest(s),'PTDF_max_abs':s['PTDF_crosscheck_max_abs'],'native_calls':0}))
