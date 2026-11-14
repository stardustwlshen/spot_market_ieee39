"""Data-only execution views for plain and dual-hash compressed research runs."""
from pathlib import Path
from datetime import datetime,timedelta
import json
from domain import read,write,sha,need,digest
from evidence_io import load,read_bytes

def boundary_ok(req,receipt,hour):
 need(type(hour)is int and 0<=hour<24,'hour index0..23')
 end=12*(hour+1);n=receipt['executed_end_tick'];issued=receipt['issued_end_tick']
 need(type(n)is int and type(issued)is int and n>=end and issued>=n,'target hour not fully executed; issued is not executed')
 need(req['state']['executed_end_tick']==n and req['state']['issued_end_tick']==issued,'state execution boundary mismatch')
 need(req['run_id']==receipt['run_id'] and req['state']['receipt_sha256']==digest(receipt),'bound receipt/run')
 need(receipt['prefix_values_sha256']==digest(req['state']['fixed_values']),'bound execution values')
 need(datetime.fromisoformat(receipt['at'])==datetime.fromisoformat(req['origin'])+timedelta(minutes=5*n),'execution clock')
 need(receipt['safety_release']is False and receipt['AGC_AC_clearance']=='not_verified','explicit counterfactual only')
 return end

def materialize(run,da,hour,output,execution_window=None):
 run=Path(run).resolve();da=Path(da).resolve();out=Path(output).resolve();need(not out.exists(),'fresh execution view only');allpins={};compressed=not (run/'seed/request.json').exists();target=4*(hour+1)
 if compressed:
  cp=run/'CONTEXT.json';c=read(cp);need(c['schema'] in ['gd.rt.compressed.context.v2','gd.rt.compressed.context.v3'],'known compressed context')
  from compressed_contracts import producer
  producer(c,c['code_pins'])
  allpins[str(cp)]=sha(cp)
  for g in ['code_pins','legacy_pins']:
   for f,h in c[g].items():need(sha(f)==h,'context source changed');allpins[f]=h
  refs=c['refs'];seed=load(refs['request']);rawcase=read_bytes(refs['case']);rawagc=read_bytes(refs['agc']);rawpolicy=read_bytes(refs['policy'])
  for x in refs.values():read_bytes(x);allpins[x['path']]=x['stored_sha256']
  parent=Path(c['history_run']) if c.get('history_run') else None
  dp=read(parent/'seed/SOURCE_PINS.json') if parent else {f:h for f,h in c['legacy_pins'].items() if Path(f).name in ['ARTIFACTS.json','ACCEPTANCE.json']}
  if parent:allpins[str(parent/'seed/SOURCE_PINS.json')]=sha(parent/'seed/SOURCE_PINS.json')
  choices=sorted((int(p.parent.name[1:]),p) for p in (run/'handoff').glob('W*/HANDOFF.json') if p.parent.name[1:].isdigit())
  chosen=next(((w,p) for w,p in choices if w>=target and (execution_window is None or w==execution_window)),None)
  if chosen:
   w,hp=chosen;h=read(hp);need(h['context_sha256']==sha(cp),'handoff context');req=load(h['request']);receipt=load(h['receipt']);allpins[str(hp)]=sha(hp)
   for x in [h['request'],h['receipt']]:allpins[x['path']]=x['stored_sha256']
   ap=run/f'W{w-1}/ACCEPTED_PAIR.json';need(sha(ap)==h['acceptance_sha256']==receipt['accepted_pair_sha256'],'accepted execution chain')
  else:
   need(execution_window is None or execution_window==c['start_window'],'requested execution boundary absent');req=seed;receipt=load(refs['receipt']);need(req['window']>=target,'target boundary unavailable')
   matches=[Path(f) for f,h in c['legacy_pins'].items() if Path(f).name=='ACCEPTED_PAIR.json' and h==receipt.get('accepted_pair_sha256')];need(len(matches)==1,'unique imported acceptance required');ap=matches[0]
  from evidence import pair_inputs
  a,prev,x=pair_inputs(ap)
  from compressed_contracts import immutable_handoff
  immutable_handoff(prev,req,receipt,x)
  need(a['run_id']==req['run_id'] and a['window']+1==req['window'],'accepted execution window')
  need(receipt['previous_receipt_sha256']==prev['state']['receipt_sha256'],'previous receipt chain')
  for name,v in req['state']['fixed_values'].items():need(name in x and x[name]==v,'execution values differ from accepted raw SCED')
  for name,v in prev['state']['fixed_values'].items():need(abs(req['state']['fixed_values'][name]-v)<=1e-7,'past execution changed')
  allpins[str(ap)]=sha(ap)
 else:
  seed=read(run/'seed/request.json');dp=read(run/'seed/SOURCE_PINS.json');rawcase=(run/'seed/case.json').read_bytes();rawagc=(run/'seed/agc.json').read_bytes();rawpolicy=(run/'seed/policy.json').read_bytes()
  w=execution_window if execution_window is not None else target;hp=run/f'handoff/W{w}/HANDOFF.json';h=read(hp);req=read(hp.parent/'request.json');receipt=read(hp.parent/'RECEIPT.json')
  for f,v in h['pins'].items():need(sha(f)==v,'plain handoff changed');allpins[f]=v
  for p in [hp,hp.parent/'request.json',hp.parent/'RECEIPT.json',run/'seed/request.json',run/'seed/SOURCE_PINS.json']:allpins[str(p)]=sha(p)
 boundary_ok(req,receipt,hour)
 for f,v in dp.items():need(sha(f)==v,'parent DA source changed');allpins[f]=v
 need(dp.get(str(da/'ARTIFACTS.json'))==sha(da/'ARTIFACTS.json'),'wrong parent DA artifacts')
 import hashlib
 need(hashlib.sha256(rawcase).hexdigest()==seed['case_sha256']==req['case_sha256'],'same raw case')
 out.mkdir(parents=True);(out/'seed').mkdir();handoff=out/f'handoff/W{target}';handoff.mkdir(parents=True)
 for name,data in [('case',rawcase),('agc',rawagc),('policy',rawpolicy)]: (out/'seed'/f'{name}.json').write_bytes(data)
 write(out/'seed/request.json',seed);write(out/'seed/SOURCE_PINS.json',dp);write(handoff/'request.json',req);write(handoff/'RECEIPT.json',receipt);write(handoff/'HANDOFF.json',dict(schema='gd.accounting.explicit_execution_view.v1',pins=allpins,original_receipt_unchanged=True,not_new_dispatch_receipt=True))
 write(out/'VIEW.json',dict(schema='gd.accounting.execution_view.v1',source_run=str(run),compressed=compressed,hour_index=hour,executed_end_tick=receipt['executed_end_tick'],issued_end_tick=receipt['issued_end_tick'],target_cells=list(range(12*hour,12*(hour+1))),source_pins=allpins,native_calls=0,formal_bill=None,safety_release=False))
 return out
