"""Independent two-hour pricing mathematics. Never consumes RT solution duals."""
from pathlib import Path
import sys
R=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(R/'guangdong_r62_same_source_da_rt_20261112'),str(R/'guangdong_r61_price_boundary_20261112')]
from r62smm import gd_r46_day_models as models,highs_solver as hs
from transform import transform

def build_local_price(inp):
 assert len(inp['da_cells'])==24 and len(inp['references'])==8 and len(inp['nodes'])==39
 assert inp['window']['physical_cells']==24
 for z in inp['renewable'].values():
  assert len(z['availability_mw'])==24 and z['bid_proxy_yuan_mwh']==-50
  assert z['reserve_credit_mw']==z['AGC_credit_mw']==0
 for z in inp['reserve']:assert z==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.)
 m,a=models.build_price_template(inp)
 q,vs,cs=hs.serialize_model(m,dict(threads=1,time_limit=60.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False))
 q.pop('deadline',None);q.update(dual=True,preparation_elapsed_s=0.)
 n=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]);out,names,trace=transform(q,n,inp)
 trace.update(base_model_audit=a,independent_PRICE_model=True,RT_tail_dual_used=False,RT_tail_optimization_not_repeated=True,PRICE_network_resolution='8 quarters, not 24 electrical executions',market_price=None,settlement_price=None,formal_publication_allowed=False)
 return out,names,trace,q,n
