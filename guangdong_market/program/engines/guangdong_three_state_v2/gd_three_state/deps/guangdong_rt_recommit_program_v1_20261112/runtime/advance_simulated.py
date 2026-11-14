"""Explicit no-deviation research clock advance, not operational execution evidence."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import json,argparse,math
from acceptance import load_verified,read,sha,write
from contracts import validate,need,stamp,digest

def advance(request_path,acceptance_path,output):
 r=validate(read(request_path));acc,values=load_verified(acceptance_path,r);out=Path(output);need(not out.exists(),'fresh handoff output')
 cutoff=r['cutoff_tick']+3;delivery=r['delivery_tick']+3;at=(stamp(r['origin'])+timedelta(minutes=5*cutoff)).isoformat();fixed={}
 for name,value in values.items():
  if name.startswith('rt_notice['):continue
  if name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if stamp(hour)<stamp(r['origin'])+timedelta(minutes=5*delivery):fixed[name]=value
  elif '['in name:
   token=(name.rsplit(',',1)[-1]if ','in name else name.split('[',1)[1]).rstrip(']')
   if token.isdigit()and int(token)<delivery:fixed[name]=value
 notices={}
 for g in r['DA_online']:
  notices[g]={}
  for t in range(cutoff):
   name=f'rt_notice[{g},{t}]';need(name in values and abs(values[name]-round(values[name]))<=1e-8,'issued notice integer');notices[g][str(t)]=int(round(values[name]))
 # Earlier sealed values cannot drift. New values are assumed followed exactly only in this study.
 for name,value in r['state']['fixed_values'].items():need(name in fixed and abs(fixed[name]-value)<=1e-7,'previous issued prefix drift')
 receipt=dict(schema='gd.research.no_deviation_execution_receipt.v1',run_id=r['run_id'],at=at,executed_end_tick=cutoff,issued_end_tick=delivery,accepted_SCED_response_sha256=acc['SCED_response_sha256'],acceptance_sha256=sha(acceptance_path),previous_receipt_sha256=r['state']['receipt_sha256'],all_due_notices_issued=True,tracking_deviation_mw=0,basis='counterfactual_research_execution_without_AGC_AC_clearance',safety_release=False,AGC_AC_clearance='not_verified',formal=False,explicit_assumption='advance simulation clock by15min; follow previous issued plan exactly; no telemetry/operational notification claimed',sealed_notice_values=notices,prefix_values_sha256=digest(fixed),raw_physical_values_unchanged=True)
 z=deepcopy(r);z.update(window=r['window']+1,cutoff_tick=cutoff,delivery_tick=delivery,cutoff=at);z['state'].update(executed_end_tick=cutoff,issued_end_tick=delivery,receipt_at=at,receipt_sha256=digest(receipt),completed_window=r['window'],fixed_values=fixed,notice_values=notices);validate(z);out.mkdir(parents=True);write(out/'RECEIPT.json',receipt);write(out/'request.json',z);write(out/'SOURCE_PINS.json',{str(Path(p).resolve()):sha(p)for p in (request_path,acceptance_path,Path(__file__))});return dict(request=str(out/'request.json'),receipt=str(out/'RECEIPT.json'),executed=cutoff,issued=delivery)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--acceptance',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(advance(a.request,a.acceptance,a.output)))
