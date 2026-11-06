"""Read-only storage siting stress screen; zero-slack LP, never executable dispatch.

Keeps the loaded latest bid's existing subjects unchanged. ES4/ES5 default offers
are added only to detached candidates. Source files are never rewritten.
"""
from pathlib import Path
import argparse, copy, hashlib, json, sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
import pandas as pd
import pyomo.environ as pyo
from smm.config import load_config, ROOT
from smm.bids import load_bids, default_bids, effective_bids
from smm.forecast import generate_profiles, apply_profile_overrides
from smm.case import build_da_case
from smm.network import build_network
from smm import scuc, regulation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    identities={str(p.relative_to(args.root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ('src/smm','config','data/case39','bids')
        for p in sorted((args.root/folder).rglob('*')) if p.is_file() and '__pycache__' not in str(p)}
    (args.output/'input_hashes.json').write_text(json.dumps(identities,indent=2))
    records=[]
    scenarios=[('wet_base','wet',1.,1.),('wet_high_re_low_load','wet',1.2,.9),('dry_low_re_high_load','dry',.8,1.1)]
    layouts=[('original',()),('pv_load_nodes_4_8',(4,8)),('original_corridors_19_25',(19,25)),('meshed_nodes_16_25',(16,25)),('pv_nodes_4_27',(4,27))]
    for scenario,season,re_scale,load_scale in scenarios:
        for layout,buses in layouts:
            start=time.perf_counter(); cfg=load_config(root=args.root,season=season)
            bids=load_bids(cfg)  # Validate the latest expanded or original namespace before detaching candidates.
            cfg.units['storage']=[s for s in cfg.units['storage'] if s['id'] not in ('ES4','ES5')]
            cfg.base['solver']['time_limit_s']=60
            for uid in ('ES4','ES5'):bids['units'].pop(uid,None)
            for uid,bus in zip(('ES4','ES5'),buses):
                cfg.units['storage'].append(dict(id=uid,bus=bus,pmax=150,ecap=600,eta_c=.95,eta_d=.95,soc_min=.1,soc_max=.9,soc0=.5,soc_end_bid=.5))
            default=default_bids(cfg)
            for uid in ('ES4','ES5'):
                if uid in default['units']:bids['units'][uid]=copy.deepcopy(default['units'][uid])
            prof=apply_profile_overrides(cfg,generate_profiles(cfg))
            for stage in ('actual','da','id','rt'):
                prof['load_'+stage]*=load_scale
                for r in cfg.units['renewable']:prof[r['id']+'_'+stage]=np.minimum(prof[r['id']+'_'+stage]*re_scale,r['cap'])
            case=build_da_case(cfg,build_network(cfg),prof,effective_bids(cfg,bids))
            m=scuc.build_model(case);regulation.prepare_energy(m)
            for name in ('shed','surplus','sl','sR_up','sR_dn'):
                for v in getattr(m,name).values():v.fix(0.)
            pyo.TransformationFactory('core.relax_integer_vars').apply_to(m)
            opt=scuc._solver(cfg,mip=False);result=opt.solve(m,load_solutions=False)
            rec=dict(scenario=scenario,layout=layout,buses=buses,season=season,renewable_multiplier=re_scale,load_multiplier=load_scale,
                storage_total_mw=sum(s['pmax'] for s in cfg.units['storage']),storage_total_mwh=sum(s['ecap'] for s in cfg.units['storage']),
                termination=str(result.solver.termination_condition),scope='integer_relaxed_96_period_DA_with_shed_surplus_line_reserve_slacks_fixed_zero;not_MIP_RT_or_N1_certificate')
            if rec['termination']=='optimal':
                opt.load_vars(); rates=case.net.branches.set_index('branch_id').rate_mw.to_dict()
                curtailed=lambda typ:sum(float(case.ren_fc[u][t])-pyo.value(m.Pw[u,t]) for u in m._md.ren if m._md.ren[u]['type']==typ for t in m.T)*cfg.dt
                rec.update(lp_objective=float(pyo.value(m.obj)),pv_curtailment_mwh=curtailed('pv'),wind_curtailment_mwh=curtailed('wind'),
                    max_line_loading=max(abs(pyo.value(m.flow[l,t]))/rates[l] for l in m.L for t in m.T),
                    new_charge_mwh=sum(pyo.value(m.Pc[s,t]) for s in ('ES4','ES5') if s in m.S for t in m.T)*cfg.dt,
                    new_discharge_mwh=sum(pyo.value(m.Pd[s,t]) for s in ('ES4','ES5') if s in m.S for t in m.T)*cfg.dt,
                    new_terminal_max_error_mwh=max((abs(pyo.value(m.E[s,cfg.T-1])-300.) for s in ('ES4','ES5') if s in m.S),default=0.))
            rec['elapsed_s']=time.perf_counter()-start;records.append(rec)
            (args.output/'screening.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));pd.DataFrame(records).to_csv(args.output/'screening.csv',index=False)
            print(json.dumps(rec,ensure_ascii=False),flush=True)
    after={name:hashlib.sha256((args.root/name).read_bytes()).hexdigest() for name in identities}
    assert after==identities,'Source inputs changed during screen'
    (args.output/'source_unchanged.json').write_text(json.dumps(dict(unchanged=True,files=len(identities))))

if __name__=='__main__':main()
