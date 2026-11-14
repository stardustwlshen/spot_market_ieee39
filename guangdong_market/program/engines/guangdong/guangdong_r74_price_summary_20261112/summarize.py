"""Price evidence assembly only. No repeated KKT or native optimization."""
from pathlib import Path
import json,hashlib,sys,argparse,math
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from dataclasses import asdict,is_dataclass
B=Path(__file__).resolve().parent;R=B.parent;RULE=R/'guangdong_r47_price_settlement_contract_20261112'
sys.path.insert(0,str(RULE))
from r47_price import PriceLimitSpec,price_candidate,hourly_candidate
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def enc(x):
 if isinstance(x,D):return str(x)
 if isinstance(x,datetime):return x.isoformat()
 if is_dataclass(x):return asdict(x)
 raise TypeError(type(x).__name__)
def spec():
 assert sha(RULE/'FINAL_MANIFEST.json')=='0a86468da8d15b33b76170c716f5e341808016d62e91dfc2bd510345ef227b73'
 fm=read(RULE/'FINAL_MANIFEST.json')
 for f in ('r47_price.py','baseline_registry.json'):assert sha(RULE/f)==fm['files'][f]['sha256']
 r=read(RULE/'baseline_registry.json');assert r['dynamic_current_verified']is False
 return PriceLimitSpec('GD','both','CNY/MWh',D(str(r['clearing_floor_CNY_MWh'])),D(str(r['clearing_cap_CNY_MWh'])),datetime.fromisoformat(r['effective_from']+'T00:00:00+08:00'),None,r['source'],'粤发改价格〔2025〕263号','official_published',datetime(2026,11,12,tzinfo=timezone.utc),True,'not_found',False)
def summarize(batches,output):
 policy=spec();windows={};sources=[];contract=None;upstream=None;nodes=None
 for plan_path,native_root in batches:
  plan_path=Path(plan_path);native=Path(native_root);plan=read(plan_path);assert plan['schema']=='gd.research.r74.PRICE_only_C_batch.v1'
  rp=native/'review_v1/BATCH_REVIEW.json';review=read(rp);bylabel={x.get('label','W'+str(x.get('window'))):x for x in review['results']};assert len(bylabel)==len(review['results'])
  sources.append(dict(plan_path=str(plan_path.resolve()),plan_sha256=sha(plan_path),review_path=str(rp.resolve()),review_sha256=sha(rp)))
  for item in plan['items']:
   w=item['window'];assert w not in windows and 0<=w<96;p=Path(item['prepared']);assert sha(p/'PREPARATION.json')==item['preparation_sha256']
   meta=read(p/'PREPARATION.json');assert sha(p/'input.json')==meta['artifacts']['input.json'];inp=read(p/'input.json');assert inp['window']['index']==w
   c=inp['contract_sha256'];run=inp['projection']['run_id'];ns=sorted(inp['nodes'])
   if contract is None:contract,upstream,nodes=c,run,ns
   assert (c,run,ns)==(contract,upstream,nodes) and len(nodes)==39
   rec=bylabel['W'+str(w)];raw=native/('W'+str(w));values=None
   if rec['status']=='verified_research_raw_price':
    for f,h in rec['raw_hashes'].items():assert sha(raw/f)==h
    request=read(raw/'request.json');assert {k:v for k,v in request.items()if k!='deadline'}==read(p/'template.json')
    assert sha(p/'template.json')==meta['artifacts']['template.json']
    values=rec['first_quarter_research_nodal_price_yuan_mwh'];assert set(values)==set(nodes) and all(math.isfinite(x)for x in values.values())
   windows[w]=dict(window=w,start=inp['window']['start'],end=inp['window']['delivery_end'],review_status=rec['status'],raw_prices=values,raw_native_path=str(raw.resolve()),raw_evidence_sha256=rec.get('raw_hashes',{}).get('response.json'),input_sha256=sha(p/'input.json'),preparation_sha256=sha(p/'PREPARATION.json'),review_sha256=sha(rp))
 assert set(windows)==set(range(96)),'full96 evidence required; no inferred future prices'
 day=datetime.fromisoformat(windows[0]['start']);rows=[];hours=[];runid='R74-quarter-independent-PRICE:'+upstream
 for w in range(96):
  z=windows[w];start=datetime.fromisoformat(z['start']);end=datetime.fromisoformat(z['end']);assert start==day+timedelta(minutes=15*w) and end==start+timedelta(minutes=15)
  for node in nodes:
   if z['raw_prices'] is None:
    row=dict(window=w,node=node,start=start,end=end,run_id=runid,stage='RT',status='pending',raw_price=None,bounded_candidate_price=None,official_market_price=None,settlement_price=None,review_status=z['review_status'])
   else:
    row=price_candidate(spec=policy,status='success',run_id=runid,node=node,start=start,end=end,stage='RT',raw_price=D(str(z['raw_prices'][node])),evidence_id=z['raw_native_path'],evidence_sha256=z['raw_evidence_sha256']);row['window']=w
   rows.append(row)
 for h in range(24):
  for node in nodes:
   qs=[x for x in rows if x['node']==node and h*4<=x['window']<h*4+4]
   if any(x['status']=='pending'for x in qs):hour=dict(node=node,start=day+timedelta(hours=h),end=day+timedelta(hours=h+1),bounded_hourly_candidate_price=None,official_market_price=None,settlement_price=None,status='pending')
   else:hour=hourly_candidate(qs)
   hour['hour']=h;hours.append(hour)
 out=dict(schema='gd.research.r74.full96_price_summary.v1',source_contract_sha256=contract,upstream_RT_run=upstream,source_batches=sources,windows=[windows[w]for w in range(96)],quarter_node_count=len(rows),hour_node_count=len(hours),quarters=rows,hours=hours,policy=policy,new_optimizer_calls=0,KKT_repeated=False,full_raw_price_available=all(z['raw_prices'] is not None for z in windows.values()),official_market_price=None,settlement_price=None,formal_bill=None,payable=False,basis='R74_8quarter_PRICE_research_actual_previous_power_C_SOC_and_datedU_with_published_baseline_clip_before_hour_mean_NOT_official',pricing_math='8quarter_power_ramp_rate_MW_per_h_times_0.25_no5min_powerlift',prior_R69_infeasible_records_unchanged=True)
 p=Path(output);p.mkdir(parents=True,exist_ok=False);(p/'PRICE_SUMMARY.json').write_text(json.dumps(out,default=enc,ensure_ascii=False,indent=2));print(sha(p/'PRICE_SUMMARY.json'));return out
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--batch',nargs=2,action='append',required=True,metavar=('PLAN','NATIVE_ROOT'));ap.add_argument('--output',required=True);a=ap.parse_args();summarize(a.batch,a.output)
