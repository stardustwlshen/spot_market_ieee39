"""Pure local AGC research arithmetic; explicit simulated publication and downstream binding."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import sys,json,hashlib,importlib.util
B=Path(__file__).resolve().parent;ROOT=B.parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
digest=lambda z:hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2,allow_nan=False)
def stamp(x):
 d=datetime.fromisoformat(x);need(d.tzinfo is not None,'timezone required');return d
def r73(data):
 sys.path.insert(0,str(B/'vendor'));spec=importlib.util.spec_from_file_location('_fixed_r73_hour_adapter',B/'vendor/adapter.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m.run(data)
def binding(prepared,actual,asof,hour):
 prepared=Path(prepared).resolve();actual=Path(actual).resolve();carry=read(prepared/'CARRY.json');summary=read(prepared/'SUMMARY.json');cut=stamp(asof);start=stamp(hour)
 need(cut<=start-timedelta(minutes=30),'T-30 missed');need(stamp(carry['cutoff'])<cut,'same/future-cutoff plan cannot be presumed available');need(stamp(summary['domain_start'])<=cut,'plan delivery anchor after AGC deadline')
 return dict(schema='gd.short.agc.context.v1',origin=carry['origin'],seed_case_sha256=carry['seed_case_sha256'],seed_request_sha256=carry['seed_request_sha256'],reference_plan_sha256=sha(actual/'ACCEPTED_PAIR.json'),reference_prepared_sha256=sha(prepared/'SUMMARY.json'),reference_domain_start=summary['domain_start'],reference_cutoff=carry['cutoff'],as_of=asof,hour_start=hour,hour_end=(start+timedelta(hours=1)).isoformat(),zone='explicit_single_research_zone',publication_basis='new explicit counterfactual plan publication by delivery anchor; not actual operator publication',plan_available_at=summary['domain_start'])
def produce(prepared,actual,original_input,out,*,asof,hour):
 prepared=Path(prepared).resolve();actual=Path(actual).resolve();out=Path(out).resolve();need(not out.exists(),'fresh output only');ctx=binding(prepared,actual,asof,hour)
 # Fixed, known local producer code; no receipt-specified module execution.
 src=ROOT/'guangdong_rt_short_rolling_v2_20261112';sys.path.insert(0,str(src));spec=importlib.util.spec_from_file_location('_trusted_short_review',src/'rolling.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);names,response=m.checked_pair(prepared,actual);case=m.loads((prepared/'derived_case.json').read_bytes());values=dict(zip(names['variables'],response['primal']))
 data=deepcopy(read(original_input));old_hour=data['hour_start'];old_asof=data['as_of'];data['hour_start']=hour;data['as_of']=asof;start=stamp(case['contract']['domain_start']);ticks=[t for t in range(48)if stamp(hour)<=start+timedelta(minutes=5*t)<stamp(ctx['hour_end'])];need(len(ticks)==12,'plan does not cover whole target hour');stable={}
 for u in data['units']:
  g=u['resource_id'];need(g in case['energy_inputs']['thermal']and u['kind']=='thermal','original thermal only');need(u['zone']==ctx['zone'],'unit zone mismatch');flags=[values[f'stable[{g},{t}]']for t in ticks];need(all(abs(v-1)<=1e-8 for v in flags),'reference plan not stably online all hour');need(all(abs(case['energy_inputs']['thermal'][g]['max_mw'][t]-u['capacity_excluding_outage_mw'])<=1e-8 for t in ticks),'original outage-adjusted capacity mismatch');stable[g]=dict(local_ticks=ticks,stable_flags=flags,baseline_mean_mw=[values[f'power[{g},{t}]']for t in ticks]);u['stable_online']=True
 result=r73(data);pins={str(p.resolve()):sha(p)for p in [Path(original_input),B/'hour_receipt.py',B/'vendor/adapter.py',B/'vendor/projection.py',src/'rolling.py',prepared/'CARRY.json',prepared/'SUMMARY.json',prepared/'derived_case.json',prepared/'names.json',prepared/'template.json',actual/'ACCEPTED_PAIR.json',*[actual/s/f for s in('SCUC','SCED')for f in('request.json.gz','response.json.gz','JOB.json','COMPLETED.json')]]}
 pins.update(read(prepared/'SUMMARY.json')['source_pins'])
 receipt=dict(schema='gd.short.local_AGC.receipt.v1',context=ctx,context_sha256=digest(ctx),input=data,input_sha256=digest(data),result=result,result_sha256=digest(result),stable_plan_review=stable,source_pins=pins,input_adaptation=dict(original_hour=old_hour,original_as_of=old_asof,changed_only=['hour_start','as_of','stable_online independently checked but same true'],numeric_parameters_and_qualification_unchanged=True),publication=dict(at=asof,basis='counterfactual research publication, no real registration/dispatch claim'),original400_unchanged=True,new_BESS_VRE_qualification=False,formal=False,safety_release=False,already_consumed_by_old_SCED=False,native_calls=0,AC_calls=0)
 out.mkdir();write(out/'AGC_RECEIPT.json',receipt);write(out/'INPUT.json',data);write(out/'CLEARING_RESULT.json',result);return receipt

def validate(receipt):
 need(receipt['schema']=='gd.short.local_AGC.receipt.v1','wrong receipt type');need(receipt['context_sha256']==digest(receipt['context'])and receipt['input_sha256']==digest(receipt['input'])and receipt['result_sha256']==digest(receipt['result']),'receipt changed')
 for p,h in receipt['source_pins'].items():need(sha(p)==h,'source bytes changed '+p)
 ctx=receipt['context'];need(receipt['input']['as_of']==ctx['as_of']and receipt['input']['hour_start']==ctx['hour_start'],'input clock mismatch');need(stamp(ctx['plan_available_at'])<=stamp(ctx['as_of'])<=stamp(ctx['hour_start'])-timedelta(minutes=30),'causal deadline');need(digest(r73(receipt['input']))==receipt['result_sha256'],'arithmetic differs');return receipt

def consumer_payload(receipt,case,base_agc,consumer_context):
 validate(receipt);c=receipt['context'];r=receipt['result'];need(r['unmet_requirement_mw']<=1e-9 and r['price']is not None,'scarce/unpriced cannot become schedule')
 for k in('origin','seed_case_sha256','seed_request_sha256','reference_plan_sha256','zone'):need(consumer_context.get(k)==c[k],'consumer source mismatch '+k)
 need(consumer_context.get('hour_start')==c['hour_start']and consumer_context.get('receipt_context_sha256')==receipt['context_sha256'],'consumer hour/context mismatch');need(case['contract']['origin']==c['origin'],'case origin mismatch');need(stamp(case['contract']['cutoff'])>=stamp(receipt['publication']['at']),'receipt unavailable at consumer cutoff')
 need(all(set(case['reserve'][k].values())=={400}for k in('positive_mw','contingency_mw')),'400MW original reserve required');thermal=case['energy_inputs']['thermal'];need(set(base_agc)==set(thermal)and set(r['awards_mw'])<=set(thermal),'resource inventory')
 origin=stamp(case['contract']['domain_start']);times=next(iter(base_agc.values()))['up_mw'];ticks=[int(t)for t in times if stamp(c['hour_start'])<=origin+timedelta(minutes=5*int(t))<stamp(c['hour_end'])];need(len(ticks)==12,'full target hour must be covered')
 out=deepcopy(base_agc)
 for g,z in out.items():
  need(z['qualification']=='explicit_synthetic_qualified','no new qualification');q=r['awards_mw'].get(g,0)
  for t in ticks:
   need(thermal[g]['max_mw'][t]-thermal[g]['min_mw'][t]>=2*q,'AGC exceeds stable range')
   for side in('up_mw','down_mw'):z[side][str(t)if str(t)in z[side]else t]=q
  z['source']='target hour replaced (not added) from local research AGC receipt '+receipt['context_sha256']+'; other hours remain inherited assumptions';z['available_at']=receipt['publication']['at']
 return dict(agc=out,consumption_binding=dict(context_sha256=receipt['context_sha256'],reference_plan_sha256=c['reference_plan_sha256'],target_local_ticks=ticks,requires_new_SCED_and_safety_review=True,old_SCED_retroactive_claim=False),formal=False,safety_release=False,native_calls=0)
