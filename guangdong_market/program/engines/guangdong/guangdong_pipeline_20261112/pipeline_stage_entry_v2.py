"""Exactly one stage per fresh interpreter; native retries remain stage-owned."""
from pathlib import Path
import argparse,json

def main():
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True,choices=['DA','RT_prepare','RT','PRICE','settle']);p.add_argument('--run',required=True);p.add_argument('--result',required=True);p.add_argument('--rt-prepared');p.add_argument('--windows',type=int,default=4);p.add_argument('--execute-native',action='store_true')
 for f in ('bundle','pricing-spec','rt-assets'):p.add_argument('--'+f)
 a=p.parse_args();root=Path(a.run);prepared=Path(a.rt_prepared)if a.rt_prepared else root/'rt_prepared'
 if a.stage=='DA':
  if not(root/'RUN.json').exists():
   from da_pipeline import init
   if not all((a.bundle,a.pricing_spec,a.rt_assets)):raise ValueError('fresh DA requires explicit inputs')
   init(a.bundle,root,a.pricing_spec,a.rt_assets)
  if(root/'PRECISION_REVISION.json').exists()and(root/'da_sced/ACCEPTANCE.json').exists():from da_native_price_v4 import run_da
  else:from pipeline_da_checked import run_da
  out=run_da(root,a.execute_native)
 elif a.stage=='RT_prepare':
  from rt_pipeline import prepare_rt
  if(prepared/'PREPARATION.json').exists():out={'status':'prepared_exists_revalidated_on_open','path':str(prepared)}
  else:
   if prepared.exists():raise ValueError('partial RT preparation pending; never overwrite')
   out=prepare_rt(root,prepared)
 elif a.stage=='RT':
  from rt_pipeline import run_rt
  out=run_rt(root,prepared,root/'rt',windows=a.windows,execute_native=a.execute_native)
  if out.get('status')=='paused_at_accepted_boundary':raise ValueError('RT paused; preserve accepted boundary')
 elif a.stage=='PRICE':
  from pipeline_rt_price_native import run_prices
  out=run_prices(root,root/'rt',root/'rt_price',windows=range(a.windows),execute_native=a.execute_native)
 else:
  from pipeline_cli import settle
  out=settle(root,root/'rt_price/PRICE_MANIFEST.json')
 from rt_price_settlement import dumps_exact
 result=Path(a.result)
 with result.open('x')as f:f.write(dumps_exact(out))
if __name__=='__main__':main()
