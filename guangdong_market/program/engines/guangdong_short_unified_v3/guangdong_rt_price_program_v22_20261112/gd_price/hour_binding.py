"""Fail-closed selected DA/RT/quarter identity contract, no solver."""
from pathlib import Path
from domain import read,sha,need,digest

def bind(run,da,manifest,*,hour_index=0):
 run=Path(run).resolve();da=Path(da).resolve();m=read(manifest);seed=read(run/'seed/request.json');pins=read(run/'seed/SOURCE_PINS.json');a=read(da/'ARTIFACTS.json')
 need(type(hour_index)is int and 0<=hour_index<24,'hour index0..23');need(m.get('hour_index',0)==hour_index,'manifest hour mismatch');need(m['run_id']==seed['run_id'],'selected RT run identity')
 need(m['da_artifacts_sha256']==sha(da/'ARTIFACTS.json') and pins.get(str(da/'ARTIFACTS.json'))==sha(da/'ARTIFACTS.json'),'wrong DA parent: exact seeded ARTIFACTS required')
 accept=da/a['stages']['da_sced']['acceptance']['path'];need(sha(accept)==seed['DA_acceptance_sha256']==a['stages']['da_sced']['acceptance']['sha256'],'wrong DA acceptance')
 case=da/a['inputs']['case']['path'];need(sha(case)==seed['case_sha256']==a['inputs']['case']['sha256']==sha(run/'seed/case.json'),'wrong DA/RT case')
 for k in ['agc','policy']:
  p=da/a['inputs'][k]['path'];need(sha(p)==a['inputs'][k]['sha256']==sha(run/f'seed/{k}.json'),'wrong DA/RT '+k)
 need(len(m['quarters'])==4,'exactly four quarter sources required');paths=[]
 for i,x in enumerate(m['quarters']):
  p=Path(x['path']).resolve();need(x['day_quarter']==hour_index*4+i and sha(p)==x['sha256'],'quarter path/hash/order mismatch');z=read(p);ctx=z.get('context')
  if ctx is None:
   candidates=[Path(f) for f in z['source_pins'] if Path(f).name=='CONTEXT.json'];need(len(candidates)==1,'unique original context');ctx=read(candidates[0])
  need(ctx['run_id']==seed['run_id'] and ctx['day_quarter']==hour_index*4+i,'quarter from another run/time')
  cp=ctx['source_pins'];need(cp.get(str(da/'ARTIFACTS.json'))==sha(da/'ARTIFACTS.json'),'quarter DA parent mismatch')
  need(any(Path(f).name=='case.json' and h==seed['case_sha256'] for f,h in cp.items()),'quarter case mismatch')
  for f,h in cp.items():need(sha(f)==h,'quarter context source changed')
  paths.append(p)
 return paths,dict(run_id=seed['run_id'],DA_run_id=a['run_id'],DA_input_identity=a['input_identity'],DA_artifacts_sha256=sha(da/'ARTIFACTS.json'),case_sha256=seed['case_sha256'],quarter_sources=m['quarters'],quarter_sources_sha256=digest(m['quarters']),manifest_sha256=sha(manifest))
