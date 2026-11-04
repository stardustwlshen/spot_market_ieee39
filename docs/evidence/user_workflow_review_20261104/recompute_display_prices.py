"""Read-only independent price-weight verification of archived DA and executed RT."""
from pathlib import Path
import json, hashlib, argparse
import numpy as np
import pandas as pd
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--evidence',type=Path,default=Path(__file__).parent)
parser.add_argument('--generator-prefix',type=Path,default=None)
args=parser.parse_args()
ROOT=args.evidence
names=['final_c1f829a_baseline_full96','final_c1f829a_dry_nominal_full96','pv2152_dry_lowre_highload_full96','pv2152_wet_highre_lowload_full96']
prefix=args.generator_prefix
out=[]
for directory in [*(ROOT/n for n in names),*([prefix] if prefix else [])]:
    files=[directory/'summary.json',directory/'da/summary.json',directory/'da_nodal_prices.csv',directory/'rt_prices.csv']
    hashes={str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    s=json.loads(files[0].read_text()); ds=json.loads(files[1].read_text())
    da=pd.read_csv(files[2]); rt=pd.read_csv(files[3]); periods=set(rt.period)
    def means(frame):
        assert not frame[['period','bus']].duplicated().any()
        assert np.isfinite(frame[['load_mw','lmp']]).all().all()
        assert (frame.load_mw>=0).all()
        assert all(set(g.bus)==set(range(1,40)) for _,g in frame.groupby('period'))
        e=frame.assign(cost=frame.load_mw*frame.lmp).groupby('period')[['load_mw','cost']].sum()
        assert (e.load_mw>0).all()
        return e.cost/e.load_mw, e.cost.sum()/e.load_mw.sum()
    da_curve, da_all=means(da); _,da_selected=means(da[da.period.isin(periods)])
    rt_curve,rt_mean=means(rt)
    spread=float((rt_curve-da_curve.reindex(rt_curve.index)).mean())
    values={'da_all':(float(da_all),ds['lmp_load_weighted_avg']), 'da_executed_range':(float(da_selected),s['spread']['da_lw_avg']), 'rt_executed_range':(float(rt_mean),s['spread']['rt_lw_avg']), 'period_spread_arithmetic_mean':(spread,s['spread']['lw_spread_mean'])}
    errors={k:abs(a-b) for k,(a,b) in values.items()}
    # Archived CSV is rounded to four decimals; no solver tolerance is changed.
    assert max(errors.values())<0.0002,(directory,errors)
    assert hashes=={str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    out.append(dict(run=directory.name,scope=s.get('run_scope',s.get('completion_kind')),periods=len(periods),values=values,max_serialization_error=max(errors.values()),stage_mean_difference=float(rt_mean-da_selected),spread_not_stage_mean_difference=bool(abs(spread-(rt_mean-da_selected))>1e-5),files_unchanged=True,hashes=hashes))
print(json.dumps(out,ensure_ascii=False,indent=2))
