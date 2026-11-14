"""Future-only AGC source projection, invoked BEFORE physical model construction."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
from dataclasses import is_dataclass,asdict
import sys,hashlib,json
B=Path(__file__).resolve().parent;ROOT=B.parent
V1=ROOT/'guangdong_short_hour_agc_receipt_v1_20261112'
sys.path.insert(0,str(V1))
from hour_receipt import read,sha,digest,need,stamp,validate

def project(receipt_path,case,base_agc,carry_path,seed,*,expected_reference_plan_sha256,expected_receipt_context_sha256,zone,hour_start):
 receipt_path=Path(receipt_path).resolve();carry_path=Path(carry_path).resolve();seed=Path(seed).resolve()
 receipt=read(receipt_path);validate(receipt);c=receipt['context'];r=receipt['result'];carry=read(carry_path)
 original=ROOT/'guangdong_agc_joint_safety_loop_v1_20261112/candidate_v3_causal_receipted/thermal_input.json'
 need(receipt['source_pins'].get(str(original.resolve()))==sha(original),'retained50 source mismatch')
 declared=deepcopy(read(original));declared['as_of']=c['as_of'];declared['hour_start']=c['hour_start']
 for u in declared['units']:u['stable_online']=True
 need(declared==receipt['input'],'retained50 quote/qualification drift')
 need(expected_reference_plan_sha256==c['reference_plan_sha256'] and expected_receipt_context_sha256==receipt['context_sha256'],'reference/context mismatch')
 need(zone==c['zone'] and hour_start==c['hour_start'],'zone/hour mismatch')
 for p,h in carry['source_pins'].items():need(sha(p)==h,'carry history drift '+p)
 for k,file in [('seed_case_sha256','case.json'),('seed_request_sha256','request.json')]:need(carry[k]==c[k]==sha(seed/file),'parent DA/seed mismatch '+k)
 contract=case['contract'];origin=stamp(contract['origin']);cut=stamp(contract['cutoff']);delivery=stamp(carry['boundary_at']);begin=stamp(contract['domain_start'])
 need(contract['origin']==carry['origin']==c['origin'],'origin mismatch')
 need(contract['cutoff']==carry['cutoff'],'cutoff mismatch')
 need(delivery==origin+timedelta(minutes=5*carry['delivery_tick']),'delivery tick mismatch')
 need(cut==origin+timedelta(minutes=5*carry['cutoff_tick']),'cutoff tick mismatch')
 need(carry['executed_end_exclusive']==carry['cutoff_tick'] and carry['issued_end_exclusive']==carry['delivery_tick'] and carry['delivery_tick']==carry['cutoff_tick']+3,'issued/executed boundary mismatch')
 need(stamp(c['as_of'])<=cut and stamp(receipt['publication']['at'])<=cut,'future AGC publication')
 need(stamp(receipt['publication']['at'])>=stamp(c['as_of']),'publication before calculation')
 need(begin>=delivery,'window includes locked issued/executed prefix')
 need(r['unmet_requirement_mw']<=1e-9 and r['price'] is not None,'scarce/unpriced receipt')
 need(all(set(case['reserve'][k].values())=={400} for k in ('positive_mw','contingency_mw')),'original400 required')
 thermal=case['energy_inputs']['thermal'];need(set(base_agc)==set(thermal) and set(r['awards_mw'])<=set(thermal),'thermal inventory mismatch')
 times=sorted(int(t) for t in next(iter(base_agc.values()))['up_mw']);need(times==list(range(len(times))),'noncontiguous time domain')
 stop=begin+timedelta(minutes=5*len(times));h0=stamp(c['hour_start']);h1=stamp(c['hour_end']);need(h1-h0==timedelta(hours=1),'not hourly receipt')
 ticks=[t for t in times if max(delivery,h0)<=begin+timedelta(minutes=5*t)<min(stop,h1)];need(ticks,'no future hour overlap')
 # Original producer has already reviewed full12 source-plan cells. Do not accept a partial-hour source.
 need(set(receipt['stable_plan_review'])==set(r['awards_mw']),'source plan inventory mismatch')
 source_begin=stamp(c['reference_domain_start']);expected_source_ticks=[i for i in range(48) if h0<=source_begin+timedelta(minutes=5*i)<h1]
 need(len(expected_source_ticks)==12 and all(z['local_ticks']==expected_source_ticks for z in receipt['stable_plan_review'].values()),'source plan target-hour coverage mismatch')
 need(all(len(z['local_ticks'])==12 and len(z['stable_flags'])==12 and all(abs(v-1)<1e-8 for v in z['stable_flags']) for z in receipt['stable_plan_review'].values()),'incomplete source hour')
 out=deepcopy(base_agc)
 for g,z in out.items():
  need(z['qualification']=='explicit_synthetic_qualified','no new qualification')
  need(sorted(int(t) for t in z['up_mw'])==times and sorted(int(t) for t in z['down_mw'])==times,'AGC time domain mismatch')
  q=r['awards_mw'].get(g,0)
  for t in ticks:
   need(thermal[g]['max_mw'][t]-thermal[g]['min_mw'][t]>=2*q,'award exceeds stable range')
   for side in ('up_mw','down_mw'):z[side][str(t) if str(t) in z[side] else t]=q
  z['source']='future target-hour cells replaced, not added; AGC context '+receipt['context_sha256']+'; other cells retain inherited assumptions'
  z['available_at']=receipt['publication']['at']
 normalized=deepcopy(case)
 if is_dataclass(normalized['energy_inputs']['horizon']):normalized['energy_inputs']['horizon']=asdict(normalized['energy_inputs']['horizon'])
 from smm.gd_october_job_codec import dumps
 binding=dict(schema='gd.short.agc.future_consumption.v2',receipt_sha256=sha(receipt_path),context_sha256=receipt['context_sha256'],reference_plan_sha256=c['reference_plan_sha256'],seed_case_sha256=carry['seed_case_sha256'],seed_request_sha256=carry['seed_request_sha256'],consumer_case_sha256=hashlib.sha256(dumps(normalized)).hexdigest(),consumer_carry_sha256=sha(carry_path),target_local_ticks=ticks,window_start=begin.isoformat(),window_end=stop.isoformat(),delivery=delivery.isoformat(),cutoff=cut.isoformat(),hour_start=c['hour_start'],hour_end=c['hour_end'],overlap_start=(begin+timedelta(minutes=5*ticks[0])).isoformat(),overlap_end=(begin+timedelta(minutes=5*(ticks[-1]+1))).isoformat(),locked_prefix_untouched=True,replacement_not_addition=True,old_SCED_retroactive_claim=False,requires_new_SCED_and_safety_review=True,source_pins={str(p):sha(p) for p in (receipt_path,carry_path,seed/'case.json',seed/'request.json',Path(__file__),V1/'hour_receipt.py',V1/'vendor/adapter.py',V1/'vendor/projection.py')})
 binding['source_pins'].update(receipt['source_pins']);binding['source_pins'].update(carry['source_pins'])
 return dict(agc=out,consumption_binding=binding,formal=False,safety_release=False,native_calls=0)
