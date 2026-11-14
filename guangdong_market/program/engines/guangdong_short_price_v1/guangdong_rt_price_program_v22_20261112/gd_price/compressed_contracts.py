"""Known installed producer profiles, never executing code supplied by receipts."""
from pathlib import Path
from domain import need,read,sha,digest
from handoff_validator import handoff_immutables
from datetime import datetime,timedelta
B=Path(__file__).resolve().parent

def producer(context,pins):
 registry=read(B/'REVIEWERS.json')['compressed_profiles'];hits=[h for f,h in pins.items()if Path(f).name=='program.py'and h in registry];need(len(hits)==1,'unknown compressed producer profile');profile=registry[hits[0]]
 names={Path(f).name:h for f,h in pins.items()};need(len(names)==len(pins) and names==profile['files'],'producer full source inventory changed')
 for f,h in pins.items():need(sha(f)==h,'producer source bytes changed')
 need(context['code_pins']==pins,'acceptance producer differs from run context')
 if profile['adapter_version']==32:need(context['schema']=='gd.rt.compressed.context.v3'and context['adapter_version']==32,'v32 context required')
 else:need(context['schema']=='gd.rt.compressed.context.v2','v2 context required')
 return profile['adapter_version']

def immutable_handoff(prev,req,receipt,values):
 handoff_immutables(prev,req);need(req['state']['completed_window']==prev['window'],'completed window mismatch')
 need(req['cutoff_tick']==prev['cutoff_tick']+3==receipt['executed_end_tick'] and req['delivery_tick']==prev['delivery_tick']+3==receipt['issued_end_tick'],'handoff clock progression')
 need(req['cutoff']==req['state']['receipt_at']==receipt['at'],'handoff timestamps')
 need(receipt['previous_receipt_sha256']==prev['state']['receipt_sha256'] and req['state']['receipt_sha256']==digest(receipt),'receipt chain')
 expected={}
 for name,v in values.items():
  if name.startswith('rt_notice[')or name.startswith('battery.power['):continue # power is a derived adapter convenience, not a model column
  if name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if datetime.fromisoformat(hour)<datetime.fromisoformat(req['origin'])+timedelta(minutes=5*req['delivery_tick']):expected[name]=v
  elif '['in name:
   t=(name.rsplit(',',1)[-1]if ','in name else name.split('[',1)[1]).rstrip(']')
   if t.isdigit()and int(t)<req['delivery_tick']:expected[name]=v
 need(expected==req['state']['fixed_values'],'incomplete or changed raw solution prefix')
 need(receipt['sealed_notice_values']==req['state']['notice_values'],'notice receipt mismatch')
 for g in req['DA_online']:
  need(set(req['state']['notice_values'][g])==set(map(str,range(req['cutoff_tick']))),'notice inventory')
  for t,v in req['state']['notice_values'][g].items():need(abs(values[f'rt_notice[{g},{t}]']-v)<=1e-8,'notice raw integer mismatch')
 for k,v in prev['state']['fixed_values'].items():need(abs(req['state']['fixed_values'][k]-v)<=1e-7,'past prefix drift')
