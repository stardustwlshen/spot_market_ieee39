"""New R67 full-tail actual source adapter. Pure C pricing, never imports runtime or old permits."""
from pathlib import Path
import sys,json,hashlib,argparse
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_r62_same_source_da_rt_20261112'),str(R/'guangdong_r61_price_boundary_20261112')]
from pricing_math import build_local_price
from r62smm.gd_r15_da_rt_bridge import digest
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,sort_keys=True,separators=(',',':'),allow_nan=False)
class Source:
 def __init__(self,root):
  self.root=Path(root).resolve();self.c=read(self.root/'SOURCE_CONTRACT.json');self.identity=read(self.root/'SOURCE_IDENTITY.json')
  assert self.c['schema']=='gd.research.r62.same_new98_source_contract.v1'
  assert self.c['financial_price_available'] is False and self.c['physical_schedule_release']['raw_PRICE_verified']is True
  self.contract_digest=digest(self.c);self.identity_digest=digest(self.identity);assert digest(self.identity['pins'])==self.identity['sha256']
  self.pins={**self.c['source_bindings'],**self.identity['pins'],str(self.root/'SOURCE_CONTRACT.json'):sha(self.root/'SOURCE_CONTRACT.json'),str(self.root/'SOURCE_IDENTITY.json'):sha(self.root/'SOURCE_IDENTITY.json')}
  self.cache_binding=read(self.root/'CACHED_TEMPLATE_BINDING.json')
  assert self.cache_binding['cache_used']is True and self.cache_binding['forecast_frozen']is True
  self.pins.update(self.cache_binding['cache_pins'])
  self.pins[str(self.root/'CACHED_TEMPLATE_BINDING.json')]=sha(self.root/'CACHED_TEMPLATE_BINDING.json')
  self.boundary()
 def boundary(self):
  assert digest(self.c)==self.contract_digest and digest(self.identity)==self.identity_digest
  for p,h in self.pins.items():assert sha(p)==h,'new source changed '+p
 def window(self,w):
  assert digest(self.c)==self.contract_digest and digest(self.identity)==self.identity_digest
  for f in ('SOURCE_CONTRACT.json','SOURCE_IDENTITY.json'):assert sha(self.root/f)==self.pins[str(self.root/f)]
  d=self.root/f'W{w}';p=d/'prepared';a=read(d/'accepted/ACCEPTANCE.json');t=read(p/'TICKET.json');inp=read(p/'input.json')
  assert t['schema']=='gd.research.r67.live_prepared.v1' and a['schema']=='gd.research.r67.accepted_RT.v1'
  assert t['window']==a['window']==inp['window']['index']==w
  assert t['source_contract_sha256']==a['source_contract_sha256']==inp['contract_sha256']==self.contract_digest
  assert t['source_identity_sha256']==a['source_identity_sha256']==self.identity['sha256']
  assert a['KKT']['passed']is True and a['local_lookahead_cells_reintegrated']==24 and a['global_lookahead_cells_reintegrated']==309
  assert t['global_offset']==a['global_offset']==3*w and t['global_physical_cells']==309 and t['local_physical_cells']==24
  assert t['current_objective_cells']==[3*w,3*w+24] and t['tail_cost_zero']is True
  assert t['tail_LP_duals_are_not_independent_PRICE']is a['tail_LP_duals_are_not_independent_PRICE']is True
  assert a['local_RT_input_sha256']==digest(inp) and read(p/'local_RT_input.json')==inp and a['issued_cells']==3
  for f,h in t['artifact_hashes'].items():assert sha(p/f)==h
  for z in a['event_pins'].values():assert sha(z['path'])==z['sha256']
  q=read(d/'native/request.json');r=read(d/'native/response.json')
  assert {k:v for k,v in q.items()if k!='deadline'}==read(p/'template.json')
  assert q['r67_ticket_id']==t['ticket_id'] and q['r67_source_identity']==self.identity['sha256'] and q['r67_contract_sha256']==self.contract_digest
  assert r['status']=='optimal'and r['native_status']=='Optimal'and r['has_primal']and r['dual_valid']
  assert a['event_pins']['response']['sha256']==sha(d/'native/response.json')
  assert inp['da_cells']==self.c['physical_cells'][3*w:3*w+24] and inp['references']==self.c['references'][w:w+8]
  assert inp['midnight_targets']==self.c['midnight_targets']==dict(ES1=100.,ES2=200.,ES3=100.,ES4=300.,ES5=300.)
  for z in inp['renewable'].values():assert z['bid_proxy_yuan_mwh']==-50 and z['M2_dispatch_research']==z['M2_price_prime']==1000 and z['reserve_credit_mw']==z['AGC_credit_mw']==0
  for z in inp['reserve']:assert z==dict(positive_mw=400.,negative_mw=0.,primary_mw=0.,accident_mw=400.)
  pins=dict(self.pins)
  for f in [p/'TICKET.json',p/'input.json',p/'local_RT_input.json',p/'template.json',d/'accepted/ACCEPTANCE.json']:pins[str(f)]=sha(f)
  for z in a['event_pins'].values():pins[str(Path(z['path']).resolve())]=z['sha256']
  return inp,pins

def batch(actual,windows,output):
 assert windows and len(windows)==len(set(windows)) and all(0<=w<96 for w in windows)
 source=Source(actual)
 for w in windows:assert (source.root/f'W{w}/accepted/ACCEPTANCE.json').is_file(),'actual accepted input missing, not a pure fixture'
 output=Path(output);output.mkdir(parents=True,exist_ok=False);items=[]
 for w in windows:
  inp,pins=source.window(w);cq,cn,tr,q,n=build_local_price(inp)
  tr.update(window=w,source_kind='new_actual_R67_tail_same_R50v2_R53_minus50',source_pins=pins,global_offset=3*w,no_RT_state_change=True)
  p=output/f'W{w}';p.mkdir()
  for f,x in [('input.json',inp),('base_template.json',q),('base_names.json',n),('template.json',cq),('names.json',cn),('transformation.json',tr)]:write(p/f,x)
  code={str(f.resolve()):sha(f)for f in B.glob('*.py')}
  for f in ['provider.py','transform.py']:z=R/'guangdong_r61_price_boundary_20261112'/f;code[str(z)]=sha(z)
  meta=dict(schema='gd.research.r69.new_R62_PRICE_preparation.v1',window=w,native_calls=0,source_pins=pins,source_code_pins=code,artifacts={f.name:sha(f)for f in p.iterdir()},columns=len(cq['variables']),rows=len(cq['rows']),market_price=None,settlement_price=None,formal_publication_allowed=False,execution_authority=False)
  write(p/'PREPARATION.json',meta);items.append(dict(window=w,prepared=str(p.resolve()),preparation_sha256=sha(p/'PREPARATION.json')))
 source.boundary()
 plan=dict(schema='gd.research.r69.PRICE_only_C_batch.v1',items=items,native_calls=0,source_contract_sha256=source.contract_digest,source_identity_sha256=source.identity['sha256'],bid=-50,market_price=None,settlement_price=None,formal_publication_allowed=False)
 write(output/'BATCH_PLAN.json',plan);print('BATCH_PLAN',sha(output/'BATCH_PLAN.json'))
if __name__=='__main__':
 ap=argparse.ArgumentParser();[ap.add_argument('--'+k,required=True)for k in ('actual','windows','output')];a=ap.parse_args();batch(a.actual,[int(w)for w in a.windows.split(',')],a.output)
