"""Portable research hourly clearing and future-only replacement; no optimizer/publication."""
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
import hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
from adapter import run as clear

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(x,s):
 if not x:raise ValueError(s)
def stamp(s):
 d=datetime.fromisoformat(s);need(d.utcoffset()==timedelta(hours=8),'Guangdong +08 clock required');return d

def produce(plan,data,*,expected_plan_sha256,branch_id,seed_case_sha256):
 need(digest(plan)==expected_plan_sha256,'unexpected plan identity')
 need(plan['schema']=='gd.agc.verified_plan.v1' and plan['branch_id']==branch_id and plan['seed_case_sha256']==seed_case_sha256,'wrong branch/seed')
 need((plan.get('source_kind')=='DA_SCED' and plan['audit'].get('DA_SCED_accepted') and plan['audit'].get('full_physical') and plan['audit'].get('SCED_full_KKT')) or (plan.get('source_kind','RT')=='RT' and plan['audit'].get('SCUC_accepted') and plan['audit'].get('SCED_full_KKT')),'verified accepted typed plan required')
 start=stamp(data['hour_start']);end=start+timedelta(hours=1);cut=stamp(data['as_of']);begin=stamp(plan['domain_start'])
 need(cut<=start-timedelta(minutes=30),'T-30 deadline');need(stamp(plan['cutoff'])<cut and stamp(plan['available_at'])<=cut,'same/future cutoff plan not available')
 step=plan.get('step_minutes',5);need(step in (5,15),'unsupported AGC plan grid')
 ticks=[t for t in range(plan['cells'])if start<=begin+timedelta(minutes=step*t)<end];need(len(ticks)==60//step,'whole target hour unavailable')
 x=deepcopy(data);reviews={}
 for u in x['units']:
  if u.get('participant_type')=='third_party' and u.get('submitted') is False:continue
  need(u['zone']==plan['zone'],'wrong unit zone');g=u['resource_id'];need(u.get('kind')=='thermal' and g in plan['thermal'],'unsupported resource; no qualification creation')
  d=plan['thermal'][g];need(u.get('qualified') is True and u.get('qualification_evidence'),'missing qualification')
  flags=[d['stable'][t] for t in ticks];need(all(abs(v-1)<1e-8 for v in flags),'not stable whole hour')
  need(all(abs(d['maximum'][t]-u['capacity_excluding_outage_mw'])<1e-8 for t in ticks),'declared outage capacity differs')
  need(u['stable_online'] is True,'explicit stable declaration required, not synthesized')
  reviews[g]={'ticks':ticks,'stable_flags':flags,'plan_mean_mw':[d['power'][t] for t in ticks]}
 result=clear(x)
 ctx={'branch_id':branch_id,'seed_case_sha256':seed_case_sha256,'plan_sha256':expected_plan_sha256,'as_of':data['as_of'],'hour_start':data['hour_start'],'hour_end':end.isoformat(),'zone':plan['zone'],'SCED_response_sha256':plan['SCED_response_sha256']}
 return {'schema':'gd.agc.hourly.research.v1','context':ctx,'context_sha256':digest(ctx),'input':x,'result':result,'stable_review':reviews,'verified_plan':plan,'formal':False,'safety_release':False,'official_publication':False,'publication':'explicit counterfactual replay at as_of; not actual dispatch','already_consumed_by_existing_SCED':False,'native_calls':0}

def validate(receipt):
 c=receipt['context'];need(receipt['context_sha256']==digest(c),'context drift')
 fresh=produce(receipt['verified_plan'],receipt['input'],expected_plan_sha256=c['plan_sha256'],branch_id=c['branch_id'],seed_case_sha256=c['seed_case_sha256'])
 need(fresh==receipt,'receipt or arithmetic changed');return fresh

def project(receipt,consumer,base_agc,*,already_applied=()):
 validate(receipt);c=receipt['context'];r=receipt['result']
 consumer_identity=digest(consumer)
 need(all(isinstance(x,dict) and 'consumer_identity' in x for x in already_applied),'typed consumer-scoped application ledger required')
 need(not any(x['consumer_identity']==consumer_identity and x['hour_start']==c['hour_start'] and x['branch_id']==c['branch_id'] for x in already_applied),'duplicate receipt/hour in same consumer; explicit reallocation unsupported')
 need(consumer['branch_id']==c['branch_id'] and consumer['seed_case_sha256']==c['seed_case_sha256'],'wrong consumer branch/seed')
 cut=stamp(consumer['cutoff']);delivery=stamp(consumer['delivery']);begin=stamp(consumer['domain_start'])
 mode=consumer.get('rt_clock_mode','legacy_issued_prefix')
 need(mode in ('provincial_15min_execution','legacy_issued_prefix'),'unknown consumer RT clock mode')
 lead=0 if mode=='provincial_15min_execution' else 15
 need(cut>=stamp(c['as_of']) and delivery==cut+timedelta(minutes=lead) and begin>=delivery,'future publication or locked prefix')
 need(type(consumer['cells'])is int and consumer['cells']>0,'positive cell count');need(len(consumer['positive_reserve_mw'])==len(consumer['accident_reserve_mw'])==consumer['cells'],'complete reserve timeline')
 need(all(x==400 for x in consumer['positive_reserve_mw'])and all(x==400 for x in consumer['accident_reserve_mw']),'original400 required')
 need(r['unmet_requirement_mw']<=1e-9 and r['price']is not None,'unmet or unpriced award')
 need(consumer['zone']==c['zone'],'wrong zone');ticks=[t for t in range(consumer['cells'])if stamp(c['hour_start'])<=begin+timedelta(minutes=15*t if mode=='provincial_15min_execution' else 5*t)<stamp(c['hour_end'])];need(ticks,'no future overlap')
 need(set(base_agc)==set(consumer['thermal']) and set(r['awards_mw'])<=set(base_agc),'resource inventory')
 out=deepcopy(base_agc)
 for g,a in out.items():
  need(a['qualification']=='explicit_synthetic_qualified' or (a['qualification']=='no_award' and r['awards_mw'].get(g,0)==0 and all(v==0 for side in ('up_mw','down_mw')for v in a[side].values())),'unqualified consumer cannot receive AGC');need(all(set(a[side])=={str(t)for t in range(consumer['cells'])}for side in ('up_mw','down_mw')),'complete AGC timeline')
  v=r['awards_mw'].get(g,0)
  for t in ticks:
   need(consumer['thermal'][g]['maximum'][t]-consumer['thermal'][g]['minimum'][t]>=2*v,'award beyond stable range')
   for side in ('up_mw','down_mw'):a[side][str(t)]=v
 return {'agc':out,'applied_receipts':[*already_applied,{'context_sha256':receipt['context_sha256'],'hour_start':c['hour_start'],'branch_id':c['branch_id'],'consumer_domain_start':consumer['domain_start'],'consumer_identity':consumer_identity}],'target_ticks':ticks,'replacement_not_addition':True,'locked_prefix_untouched':True,'requires_new_SCED_and_safety_review':True,'already_consumed_by_existing_SCED':False,'formal':False,'safety_release':False,'native_calls':0}

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('action',choices=['produce','project']);p.add_argument('input');p.add_argument('output');a=p.parse_args();x=json.loads(Path(a.input).read_text());z=produce(**x)if a.action=='produce'else project(**x)
 with Path(a.output).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
