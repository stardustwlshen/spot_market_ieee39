"""Fixed v6 producer, arbitrary accepted run. No caller evidence assertions/native."""
from pathlib import Path
import json,sys,hashlib,copy,importlib.util
from datetime import timedelta
B=Path(__file__).resolve().parent;R=B.parents[1]
INSTALL=R/'engines/guangdong_short_unified_v6'
INPUT_SHA='e947dffb0ff3ca8f21bcdf0391142274c32b2a78c7cf9ad60c5b17fabbb4c409'
MANIFEST='c8b3e53419eb178545a87f3e4854d0631c5217df126cdde1360f411b8cbf18d7'
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def need(ok,msg):
 if not ok:raise ValueError(msg)
def installed():
 f=INSTALL/'FROZEN_MANIFEST.json';need(sha(f)==MANIFEST,'unsupported or changed fixed producer manifest')
 for name,h in read(f)['files'].items():
  p=(INSTALL/name).resolve();need(p.is_relative_to(INSTALL)and sha(p)==h,'fixed producer code drift')
 core=INSTALL/'rt/guangdong_rt_short_user_cli_v5_20261112';sys.path.insert(0,str(core));import program
 need(Path(program.__file__).resolve()==core/'program.py','other producer already imported; isolated host process required')
 return program
sys.path.insert(0,str(B))
from hourly_day import produce,digest,stamp
from batch_api import apply_available

def register_receipt(run,source_window,hour,index):
 p=installed();run=Path(run).resolve();r,c=p.context(run);need(type(source_window)is int and 0<=source_window<=95,'source window0..95');proof=p.preflight(r,source_window);need(proof['status']=='accepted','source must be accepted, not merely prepared')
 s=r/f'W{source_window}';n,response=p.engine.checked_pair(s/'prepared',s/'actual');case=p.engine.loads((s/'prepared/derived_case.json').read_bytes());carry=read(s/'prepared/CARRY.json');summary=read(s/'prepared/SUMMARY.json');v=dict(zip(n['variables'],response['primal']));seed=read(r/'seed/request.json');start=stamp(seed['origin'])+timedelta(hours=hour);need(type(hour)is int and 0<=hour<24,'hour0..23')
 # Bind every authenticated raw receipt and prepared source, never accept caller hashes.
 pins={str(f):sha(f)for f in [INSTALL/'FROZEN_MANIFEST.json',r/'SHORT_CONTEXT.json',r/'seed/case.json',s/'PREPARED.json',s/'prepared/CARRY.json',s/'prepared/SUMMARY.json',s/'prepared/names.json',s/'prepared/derived_case.json',s/'actual/ACCEPTED_PAIR.json',*[s/'actual'/stage/f for stage in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]}
 plan=dict(schema='gd.agc.verified_plan.v1',branch_id=c['run_id'],seed_case_sha256=carry['seed_case_sha256'],cutoff=carry['cutoff'],domain_start=summary['domain_start'],available_at=summary['domain_start'],availability_basis='explicit synthetic publication at delivery anchor, not operational time',cells=48,zone='explicit_single_research_zone',SCED_response_sha256=read(s/'actual/SCED/COMPLETED.json')['response']['raw_sha256'],audit=dict(SCUC_accepted=True,SCED_full_KKT=True),thermal={g:dict(stable=[v[f'stable[{g},{t}]']for t in range(48)],power=[v[f'power[{g},{t}]']for t in range(48)],minimum=[d['min_mw'][t]for t in range(48)],maximum=[d['max_mw'][t]for t in range(48)])for g,d in case['energy_inputs']['thermal'].items()},source_pins=pins,formal=False,safety_release=False)
 need(sha(B/'RESEARCH_INPUT.json')==INPUT_SHA,'fixed research offer/qualification drift')
 data=read(B/'RESEARCH_INPUT.json');data.update(hour_start=start.isoformat(),as_of=(start-timedelta(minutes=30)).isoformat());receipt=produce(plan,data,expected_plan_sha256=digest(plan),branch_id=c['run_id'],seed_case_sha256=carry['seed_case_sha256'])
 index=Path(index).resolve();need(not index.exists(),'host index exists; no overwrite');index.mkdir(parents=True);write(index/'RECEIPT.json',receipt);write(index/'INDEX.json',dict(run=str(r),run_id=c['run_id'],source_window=source_window,hour=hour,source_pins=pins,receipt_sha256=sha(index/'RECEIPT.json'),manifest_sha256=MANIFEST,input_sha256=INPUT_SHA,native_calls=0,formal=False,safety_release=False));return index

def build_with_receipt(run,window,index,out):
 p=installed();r,c=p.context(run);idx=read(Path(index)/'INDEX.json');need(idx['manifest_sha256']==MANIFEST and idx['run']==str(r)and idx['run_id']==c['run_id'],'wrong registered run/producer')
 for f,h in idx['source_pins'].items():need(sha(f)==h,'registered source changed')
 # Reauthenticate acceptance on consumption. Index is not a substitute for source proof.
 need(p.preflight(r,idx['source_window'])['status']=='accepted','source no longer accepted');receipt=read(Path(index)/'RECEIPT.json');need(sha(Path(index)/'RECEIPT.json')==idx['receipt_sha256'],'host receipt changed')
 import tempfile
 with tempfile.TemporaryDirectory(prefix='gd-agc-host-proof-')as tmp:
  fresh=register_receipt(r,idx['source_window'],idx['hour'],Path(tmp)/'proof');need(read(fresh/'RECEIPT.json')==receipt,'receipt differs from reauthenticated source replay')
 plan=receipt['verified_plan'];need(plan['branch_id']==c['run_id']and plan['seed_case_sha256']==sha(r/'seed/case.json'),'wrong receipt branch/seed')
 # Recreate from fixed offer and authenticated source binding; cannot accept user edit.
 need(receipt['input']==dict(read(B/'RESEARCH_INPUT.json'),hour_start=receipt['context']['hour_start'],as_of=receipt['context']['as_of']),'edited input requires new clearing and solve lifecycle')
 need(plan['source_pins']==idx['source_pins']and plan['SCED_response_sha256']==read(r/f"W{idx['source_window']}/actual/SCED/COMPLETED.json")['response']['raw_sha256'],'plan source mismatch')
 cp=p.current_carry(r,c,window);carry=read(cp);out=Path(out).resolve();need(not out.exists(),'new output required')
 import thermal_physical
 original=thermal_physical.build_free_scuc;bindings=[]
 def hook(**kw):
  ct=kw['contract'];begin=ct['domain_start'];need(stamp(receipt['context']['as_of'])<=stamp(carry['cutoff']),'receipt unavailable at cutoff');need(stamp(begin)==stamp(carry['cutoff'])+timedelta(minutes=15),'issued prefix cannot be edited')
  e=kw['energy_inputs'];thermal={g:dict(maximum=[d['max_mw'][t]for t in range(48)],minimum=[d['min_mw'][t]for t in range(48)])for g,d in e['thermal'].items()}
  consumer=dict(branch_id=c['run_id'],seed_case_sha256=carry['seed_case_sha256'],cutoff=carry['cutoff'],delivery=begin,domain_start=begin,cells=48,thermal=thermal,zone=plan['zone'],positive_reserve_mw=[kw['reserve']['positive_mw'][t]for t in range(48)],accident_reserve_mw=[kw['reserve']['contingency_mw'][t]for t in range(48)])
  base=json.loads(json.dumps(kw['agc_awards']));z=apply_available([receipt],consumer,base,expected_receipt_digests=[digest(receipt)],expected_consumer_digest=digest(consumer));need(z['selected_hours'],'no future overlap');need(z['agc']==base,'changed awards require new enabled lifecycle; cannot reuse old day');kw['agc_awards']={g:{**a,**{side:{int(t):v for t,v in a[side].items()}for side in ('up_mw','down_mw')}}for g,a in z['agc'].items()};bindings.append(dict(consumer_sha256=digest(consumer),same_original_awards=z['agc']==base,applied=z['applied_receipts'],requires_new_SCUC_SCED_security=True));return original(**kw)
 thermal_physical.build_free_scuc=hook
 try:z=p.engine.build(r/'seed',cp,out)
 finally:thermal_physical.build_free_scuc=original
 need(len(bindings)==1,'exactly one freeSCUC interception');write(out/'HOST_AGC.json',dict(index_sha256=sha(Path(index)/'INDEX.json'),bindings=bindings,native_calls=0,safety_release=False,formal=False));return z

if __name__=='__main__':
 import argparse
 q=argparse.ArgumentParser();q.add_argument('action',choices=['register','build']);q.add_argument('--run',required=True);q.add_argument('--source-window',type=int);q.add_argument('--hour',type=int);q.add_argument('--window',type=int);q.add_argument('--index',required=True);q.add_argument('--output');a=q.parse_args();z=register_receipt(a.run,a.source_window,a.hour,a.index)if a.action=='register'else build_with_receipt(a.run,a.window,a.index,a.output);print(json.dumps(str(z)if isinstance(z,Path)else z))
