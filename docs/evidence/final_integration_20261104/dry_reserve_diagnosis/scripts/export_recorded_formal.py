"""Export exact FORMAL values only from stage and explicitly dispatch-labelled evidence."""
from pathlib import Path
import argparse,json
ap=argparse.ArgumentParser();ap.add_argument('--trial',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
stage=json.loads((a.trial/'da/stage_diagnostics.json').read_text())['da']['energy_sced'];aux=json.loads((a.trial/'da/auxiliary_pricing_evidence.json').read_text())['da'];known={}
def put(name,index,value):
 index=tuple(index) if isinstance(index,list) else index;key=name,index
 if key in known:assert abs(known[key]-value)<1e-8,key
 known[key]=value
for row in stage['integer_values']:put(row['variable'],row['index'],row['value'])
for row in stage['units']:
 uid,t,typ=row['unit'],row['period'],row['type']
 if typ in ['coal','gas_ccgt','gas_gt']:put('P',[uid,t],row['P_mw'])
 elif typ in ['wind','pv']:put('Pw',[uid,t],row['P_mw'])
 elif typ=='hydro':put('Ph',[uid,t],row['P_mw'])
 elif typ=='storage':
  for name,col in [('Pd','P_dis_mw'),('Pc','P_ch_mw'),('E','E_mwh')]:put(name,[uid,t],row[col])
 elif typ=='pumped':
  for name,col in [('Pg','P_gen_mw'),('Pp','P_pump_mw'),('Vu','V_up_1e4m3')]:put(name,[uid,t],row[col])
for group in ['state_differences','reserve_differences','slack_differences']:
 for row in aux[group]:put(row['variable'],row['index'],row['dispatch'])
for row in aux['hydro_spill_fixation']:put('Qspill',[row['station'],row['relative_period']],row['dispatch_spill_m3s'])
a.out.parent.mkdir(parents=True,exist_ok=True)
with a.out.open('x') as stream:json.dump([dict(variable=v,index=list(i) if isinstance(i,tuple) else i,value=x) for (v,i),x in known.items()],stream)
print(json.dumps({'recorded_formal_variables':len(known),'source':'energy_sced and dispatch-labelled reference quantities only','out':str(a.out)}))
