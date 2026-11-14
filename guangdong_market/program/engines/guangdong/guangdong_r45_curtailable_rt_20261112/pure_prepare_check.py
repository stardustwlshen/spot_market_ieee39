import sys,json
from pathlib import Path
sys.dont_write_bytecode=True
from r45smm import gd_r45_source_contract as s,gd_r45_day_session as rt,gd_r45_day_models as models
from r45smm import highs_solver as hs
root=Path(__file__).resolve().parent
c,p=s.load_contract(root/'SOURCE_CONFIG.json',policy=s.POLICY)
seed=c['seed_proof'];print('seed',seed['seed_state']['at'],len(seed['prefix_cells']),flush=True)
for i in (44,45,46,47):
    projection=s.template_window(c,p,i)[0]['projection']
    inp=rt.assemble_window_input(c,p,i,projection,transaction_id='PURE_CONSTRUCTION_ONLY',construction_only=True)
    for stage,fn in [('RT',models.build_rt_template),('PRICE',models.build_price_template)]:
        m,a=fn(inp);q,vs,cs=hs.serialize_model(m,{})
        print(i,stage,len(q['variables']),len(q['rows']),flush=True)
print('0 native, pure construction passed')
