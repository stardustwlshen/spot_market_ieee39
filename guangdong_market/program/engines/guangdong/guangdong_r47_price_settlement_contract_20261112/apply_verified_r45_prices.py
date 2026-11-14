"""Pure, hash-bound application to already independently audited R45 raw prices.
No repeated LP audit, optimizer, official publication, settlement or source edits.
"""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from dataclasses import asdict,is_dataclass
import hashlib,json
from r47_price import PriceLimitSpec,price_candidate,hourly_candidate
O=Path(__file__).resolve().parent;ROOT=O.parent
N=ROOT/'guangdong_r45_actual_noon_20261112/native_four_v1';A=ROOT/'guangdong_r45_independent_review_20261112'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_bytes())
def enc(x):
 if isinstance(x,Decimal):return str(x)
 if isinstance(x,datetime):return x.isoformat()
 if is_dataclass(x):return asdict(x)
 raise TypeError(type(x).__name__)
def run():
 assert sha(A/'FINAL_REVIEW.json')=='d9d6cce8f8992ef5c6b133deb70fa7bfe6d5a77a7823e213c6c5e750b4bf8a89'
 final=read(A/'FINAL_REVIEW.json');raw_review=read(A/'RAW_KKT_REVIEW.json');workflow=read(A/'PHYSICS_WORKFLOW_REVIEW.json')
 assert sha(A/'RAW_KKT_REVIEW.json')==final['proof_files']['RAW_KKT_REVIEW.json']and sha(A/'PHYSICS_WORKFLOW_REVIEW.json')==final['proof_files']['PHYSICS_WORKFLOW_REVIEW.json']
 assert sha(N/'post_verification.json')==workflow['native_files']['post_verification.json']
 post=read(N/'post_verification.json');records=read(N/'result.json')['price_records']
 assert post['financial_price_available']is False and post['market_price']is None and post['settlement_price']is None
 sz=timezone(timedelta(hours=8));spec=PriceLimitSpec('GD','both','CNY/MWh',Decimal('-50'),Decimal('1800'),datetime(2025,11,1,tzinfo=sz),None,'https://drc.gd.gov.cn/gkmlpt/content/4/4775/post_4775501.html','粤发改价格〔2025〕263号','official_published',datetime(2026,11,12,tzinfo=timezone.utc),True,'not_found',False)
 quarters=[];by_node={};pins={}
 for rec,q in zip(records,post['executed_quarters']):
  i=q['window']['index'];assert rec['status']=='success'and rec['window']==q['window']and q['price_substitution_applied']is False
  reviewed=next(s for s in raw_review['stages']if s['window']==i and s['stage']=='PRICE');p=N/f'window{i}/price';assert reviewed['occurrence_id']==rec['occurrence']['id']
  for name in ('input.json','request.json','names.json','response.json','acceptance.json'):assert sha(p/name)==reviewed['hashes'][name]
  pins[str(i)]=dict(occurrence_id=reviewed['occurrence_id'],source_event_files=reviewed['hashes'])
  assert q['raw_price_status']=='available_raw_only'and q['raw_price_scope']=='delivered_first_quarter_only_not_seven_lookaheads'
  for node,P in sorted(q['raw_unclipped_PRICE'].items()):
   assert P==rec['prices'][node][0]
   candidate=price_candidate(spec=spec,status='success',run_id=rec['run_id'],node=node,start=datetime.fromisoformat(q['window']['start']),end=datetime.fromisoformat(q['window']['delivery_end']),stage='RT',raw_price=Decimal(str(P)),evidence_id=reviewed['occurrence_id'],evidence_sha256=reviewed['hashes']['response.json'])
   candidate['window']=i;quarters.append(candidate);by_node.setdefault(node,[]).append(candidate)
 hours=[hourly_candidate(v)for _,v in sorted(by_node.items())]
 assert len(quarters)==156 and len(hours)==39 and all(x['raw_price']==-1000 and x['bounded_candidate_price']==-50 and x['official_market_price']is None and x['settlement_price']is None for x in quarters)
 assert all(x['bounded_hourly_candidate_price']==-50 and x['official_market_price']is None and x['settlement_price']is None for x in hours)
 out=dict(schema='gd.research.r47.verified_R45_price_candidates.v1',basis='published_policy_basis_not_verified_current_operator_parameters',new_optimizer_calls=0,old_KKT_repeated=False,raw_source_unchanged=True,source_review_sha256=sha(A/'FINAL_REVIEW.json'),source_ledger_sha256=sha(N/'post_verification.json'),source_events=pins,policy=spec,quarters=quarters,hours=hours,official_market_price=None,settlement_price=None,formal_bill=None,payable=False,scope='15min public-baseline bound before hourly arithmetic mean; no replacement of R45/R44 artifacts or publication flags')
 (O/'R45_PRICE_CANDIDATES.json').write_text(json.dumps(out,default=enc,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(dict(quarter_candidates=156,hour_node_candidates=39,raw_each=-1000,bounded_each=-50,hour_each=-50,financial_or_official=False,new_optimizer_calls=0,sha256=sha(O/'R45_PRICE_CANDIDATES.json'))))
if __name__=='__main__':run()
