from pathlib import Path
import json,hashlib,sys
# Isolated arithmetic CLI still uses the pinned application hash facade.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(x,m):
 if not x:raise ValueError(m)
def hour(prices,index=0):
 """Normal hour arithmetic in an isolated process, fixed accepted proofs reused."""
 import session
 BRANCH=prices.host.branch_id
 need(prices.host.window>=4*(index+1),'complete hour required')
 offset=0 if getattr(prices.host,'same_period_execution',False) else -1
 needed=set(range(4*index+offset,4*index+4+offset));need(needed.issubset(prices.accepted),'four accepted quarters required')
 accepted={}
 for w in sorted(needed):
  p,d,owner=prices.accepted[w]
  owner.verify();need(sha(p)==d,'accepted price changed before arithmetic');z=read(p)
  need(z['context']['run_id']==BRANCH and z['context']['day_quarter']==w-offset and z['KKT']['passed'] is True,'accepted quarter identity')
  for f,h in z['source_pins'].items():need(sha(f)==h,'accepted price dependency changed')
  accepted[str(Path(p).resolve())]=d
 manifest=prices.output/f'FOUR_PRICE_ARITHMETIC_INPUTS_H{index}.json'
 with manifest.open('x')as f:json.dump(dict(schema='gd.four_price.arithmetic_input.v1',accepted=accepted,logical_branch=BRANCH,scope='only arithmetic from four current owned accepted proofs; no new acceptance authority'),f,indent=2)
 oldcall=session.call
 def arithmetic_only(args,log):
  if Path(args[0]).resolve()!=(session.B/'math_hour.py').resolve():return oldcall(args,log)
  return oldcall([Path(__file__).resolve(),'--hour-arithmetic',str(session.B),str(manifest),sha(manifest),*args[1:]],log)
 session.call=arithmetic_only
 try:return prices.hour(index)
 finally:session.call=oldcall

def _arithmetic_cli(runtime,manifest,expected,args):
 """Separate-process arithmetic only: no session/host reconstruction or solve."""
 import sys
 runtime=Path(runtime).resolve();need(runtime==Path(__file__).resolve().parents[1]/'gd_live_market_runtime','wrong arithmetic module')
 need(sha(manifest)==expected,'arithmetic input transport changed');m=read(manifest);BRANCH=m['logical_branch']
 need(m['schema']=='gd.four_price.arithmetic_input.v1' and m['logical_branch']==BRANCH and len(m['accepted'])==4,'exact arithmetic input scope')
 sys.path.insert(0,str(runtime));import math_hour
 import program
 need(Path(program.__file__).resolve()==(runtime.parent.parent/'engines/guangdong_short_unified_v6/price/program.py').resolve(),'wrong isolated price reader')
 def bound_read(path):
  key=str(Path(path).resolve());need(key in m['accepted'] and sha(path)==m['accepted'][key],'not fixed arithmetic input')
  z=read(path)
  for f,h in z['source_pins'].items():need(sha(f)==h,'accepted evidence altered')
  need(z['KKT']['passed'] is True and z['context']['run_id']==BRANCH,'not previously accepted logical price')
  return dict(context=z['context'],raw_prices=z['raw_prices'],KKT=z['KKT'],read_only=True,native_calls=0,prior_accepted_math_reused_without_replay=True)
 program.read_result=bound_read
 need(len(args)==7,'normal hour arguments')
 return math_hour.compute(args[0],args[1],args[2:6],args[6])

if __name__=='__main__':
 import sys
 need(len(sys.argv)>1 and sys.argv[1]=='--hour-arithmetic','arithmetic helper only')
 print(json.dumps(_arithmetic_cli(sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5:])))
