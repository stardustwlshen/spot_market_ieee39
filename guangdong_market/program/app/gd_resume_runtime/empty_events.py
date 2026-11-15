"""Narrow recovery scope: simulation enabled, but no historical/future events.
This does not restore event authority. Runtime still proves the empty event ledger
against each exact accepted carry before any new solve.
"""
from pathlib import Path
import json,gzip
from content_hash import sha

def validate(source):
 source=Path(source);pins={}
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 def read(p):
  p=Path(p);b=p.read_bytes();pins[str(p.resolve())]=sha(p);return json.loads(gzip.decompress(b)if p.suffix=='.gz'else b)
 plan=read(source/'PLAN.json')
 need(plan.get('rt_clock_mode')=='provincial_15min_execution'and not plan.get('stop_request'),'empty-event recovery requires same-period/no stop instruction')
 count=0
 for w in range(plan.get('windows',4)):
  folder=source/'pipeline/dispatch'/f'W{w}'
  if not(folder/'RESULT.json').exists():break
  need(read(folder/'RESULT.json')['window']==w,'noncontiguous completed windows')
  for rel in ('prepared/CARRY.json','EXACT_CARRY.json'):
   c=read(folder/rel);need(c.get('rt_clock_mode')=='provincial_15min_execution','mixed event recovery clock')
   need(all(z['phase']in ('off','offline','stable')and not z.get('remaining_curve')for z in c['thermal'].values()),'active thermal event cannot be reconstructed as empty')
  availability=read(folder/'prepared/NOTICE_AVAILABILITY.json')
  need(availability.get('scheduled_research_commitments')==[],'future notice commitments require explicit event-history restoration')
  n=read(folder/'prepared/names.json')['variables'];r=read(folder/'actual/SCED/response.json.gz')['primal']
  need(len(n)==len(r)and len(n)==len(set(n)),'invalid historical raw dimension')
  event_columns=[v for k,v in zip(n,r)if k.startswith(('start[','stop[','shutdown['))]
  need(event_columns and all(abs(v)<=1e-8 for v in event_columns),'historical/future startup or shutdown decisions require event-state restoration')
  count+=1
 need(count>0,'no complete event-free history')
 return dict(schema='gd.resume.empty_event_scope.v1',completed_windows=count,mode='provincial_15min_execution',source_pins=pins,no_prior_or_future_thermal_events=True,runtime_empty_ledger_proof_still_required=True)
