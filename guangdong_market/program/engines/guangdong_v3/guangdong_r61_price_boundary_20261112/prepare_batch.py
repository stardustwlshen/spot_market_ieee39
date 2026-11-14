"""Pure batch materialization. All requested actual sources must already exist."""
from prepare import *

def batch(suffix,windows,output):
 suffix=Path(suffix);output=Path(output);assert len(windows)==len(set(windows)) and all(46<=w<=95 for w in windows)
 sources=[]
 for w in windows:
  if w==46:
   p=R/'guangdong_r60_free_rolling_endpoint_20261112/W46_prepared_v1';n=R/'guangdong_r60_actual_counterfactual_20261112/W46_native_v1';a=R/'guangdong_r60_actual_counterfactual_20261112/W46_accepted_v1'
  else:p=suffix/f'W{w}/prepared';n=suffix/f'W{w}/native';a=suffix/f'W{w}/accepted'
  for f in [p/'input.json',p/'TICKET.json',n/'response.json',a/'ACCEPTANCE.json']:assert f.is_file(),'required actual source missing: '+str(f)
  sources.append((w,p,n,a))
 output.mkdir(parents=True,exist_ok=False);items=[]
 for w,p,n,a in sources:
  d=output/f'W{w}';meta=prepare(p,n,a,d)
  items.append(dict(window=w,prepared=str(d.resolve()),preparation_sha256=sha(d/'PREPARATION.json')))
 plan=dict(schema='gd.research.r61.PRICE_only_C_batch.v1',items=items,native_calls=0,max_native_calls=len(items),research_only=True,market_price=None,settlement_price=None,formal_publication_allowed=False,RT_backfill=False)
 write(output/'BATCH_PLAN.json',plan);print('BATCH_PLAN',sha(output/'BATCH_PLAN.json'))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--suffix',required=True);ap.add_argument('--windows',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();batch(a.suffix,[int(x)for x in a.windows.split(',')],a.output)
