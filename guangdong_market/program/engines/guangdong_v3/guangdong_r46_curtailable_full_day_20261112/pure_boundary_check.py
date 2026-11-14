"""One source load; necessary midnight/D96/tail constructions, zero native."""
import json,sys
from pathlib import Path
sys.dont_write_bytecode=True
from r46smm import gd_r46_source_contract as source,gd_r46_day_session as session,gd_r46_day_models as models,highs_solver as hs
from r46smm.gd_r15_da_rt_bridge import digest
root=Path(__file__).resolve().parent;c,p=source.load_contract(root/'SOURCE_CONFIG.json',policy=source.POLICY)
s=session.initialize_day(c,p);live=session._session(s)
assert live['next_window']==0 and c['seed_proof']['replayed_DA_prefix_cells']==0
assert c['initial_preparation']['midnight_state']['at']==c['day_start']
result=dict(native_calls=0,start_index=0,window_count=96,seed_state_basis=c['seed_proof']['state_basis'],seed_cutoff=live['clock'],physical_cells=len(c['physical_cells']),references=len(c['references']),financial_price_available=False,checks=[])
for i,k in [(0,None),(88,24),(95,3)]:
 inp=session.pure_template_input(c,p,i);assert inp['window']['midnight_local_energy_index']==k
 dates={x['start'][:10] for x in inp['da_cells']}
 for b,u in inp['resources'].items():
  if u['kind']=='battery':assert dates<=set(u['cycle_budget'])
 for stage,fn in [('RT',models.build_rt_template),('PRICE',models.build_price_template)]:
  m,a=fn(inp);q,vs,cs=hs.serialize_model(m,{})
  result['checks'].append(dict(window=i,stage=stage,D96_local_index=k,columns=len(vs),rows=len(cs),input_sha256=digest(inp),dates=sorted(dates)))
print(json.dumps(result,sort_keys=True))
