import sys,json
from pathlib import Path
sys.dont_write_bytecode=True
from r45smm import gd_r45_source_contract as s,gd_r45_day_session as rt,gd_r45_day_models as models,gd_r45_rt_pricing as price,highs_solver as hs
from r45smm.gd_r15_da_rt_bridge import digest
root=Path(__file__).resolve().parent;c,p=s.load_contract(root/'SOURCE_CONFIG.json',policy=s.POLICY)
session=rt.initialize_day(c,p);assert rt._session(session)['next_window']==44
inp=rt.pure_template_input(c,p,44);cells=__import__('copy').deepcopy(inp['da_cells']);e=s._CONTRACTS[p]
# Explicit zero-reserve shape fixture, NOT actual selected RT/feasibility evidence.
for cell in cells:cell['reserve_awards_mw']={g:{k:0. for k in ('positive','negative','primary','accident')} for g in cell['thermal']}
binding=dict(input_sha256=digest(inp),solution_cells_sha256=digest(cells),objective_yuan=0.)
d=price.normalize_price_inputs(inp,cells,c,e['data'],e['source'],binding)
m,a=price.build_price_template(d);q,vs,cs=hs.serialize_model(m,{});names=dict(variables=[v.name for v in vs],constraints=[x.name for x in cs])
w=price.check_selected_reference_witness(d,q,names)
print(json.dumps(dict(native_calls=0,PRICE_witness_columns=len(vs),diagnostic_feasible=w['selected_RT_feasible'],max_violation=w['max_violation'],seed_cutoff=c['seed_proof']['seed_state']['at'],source_financial_price_available=c['financial_price_available'])))
