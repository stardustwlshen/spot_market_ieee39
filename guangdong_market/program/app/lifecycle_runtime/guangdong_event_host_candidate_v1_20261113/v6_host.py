"""Fixed installed v6, arbitrary authenticated accepted run. No user hash assertions.
Normal event builder + exact handoff, never an implicit solver or dispatch permit.
"""
from pathlib import Path
from copy import deepcopy
import sys,json,hashlib,importlib,math
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_fixed_event_entry_v1_20261113'))
import event_path as e
INSTALL=R.parent.parent/'engines/guangdong_short_unified_v6'
MANIFEST='c8b3e53419eb178545a87f3e4854d0631c5217df126cdde1360f411b8cbf18d7'
read=e.read
from content_hash import sha
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def installed():
 sys.path.insert(0,str(R));from portable_gate import verify;verify()
 m=INSTALL/'FROZEN_MANIFEST.json';e.need(sha(m)==MANIFEST,'unsupported producer version/manifest')
 for name,h in read(m)['files'].items():
  p=(INSTALL/name).resolve();e.need(p.is_relative_to(INSTALL)and sha(p)==h,'installed source drift')
 core=INSTALL/'rt/guangdong_rt_short_user_cli_v5_20261112';sys.path.insert(0,str(core));import program
 e.need(Path(program.__file__).resolve()==core/'program.py','isolated event host process required');return e.dual_adapter().install(program)

def accepted_bundle(run,w):
 p=installed();r,c=p.context(run);e.need(type(w)is int and 0<=w<96,'window0..95');e.need(p.preflight(r,w)['status']=='accepted','actual accepted pair required')
 src=r/f'W{w}';n,response=p.engine.checked_pair(src/'prepared',src/'actual');case=p.engine.loads((src/'prepared/derived_case.json').read_bytes());incoming=read(src/'prepared/CARRY.json');summary=read(src/'prepared/SUMMARY.json')
 e.need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'original400')
 q=read(src/'actual/SCUC/request.json.gz');u=read(src/'actual/SCUC/response.json.gz');eq=read(src/'actual/SCED/request.json.gz');proof=e.verify_pair(q,u,eq,response,n);e.dual_adapter().validate_record(src/'actual',proof)
 b=dict(schema='gd.fixed.host_bundle.v1',mode='fixed_curve_research',run_id=c['run_id'],case_identity=sha(r/'seed/case.json'),available_at=summary['domain_start'],case=e.pack(case),boundary=e.pack(read(src/'prepared/BOUNDARY.json')),incoming_carry=e.pack(incoming),SCUC_request=q,SCUC_response=u,SCED_request=eq,SCED_response=response,names=n,host_source_selection={'fixed_manifest':MANIFEST,'run':str(r),'window':w,'prepared_sha256':sha(src/'PREPARED.json'),'accepted_sha256':sha(src/'actual/ACCEPTED_PAIR.json')})
 return p,r,c,b,e.digest(b)

def build_and_replay(run,source_window,window,out,*,research_fixed_curve_events=False):
 """Real arbitrary-run hookup; old raw adoption only when new constraints compatible.
 Future genuinely changed matrices must use RuntimeHooks + real new pair acceptance.
 """
 e.need(research_fixed_curve_events is True,'explicit research event switch');p,r,c,source,sh=accepted_bundle(run,source_window)
 e.need(source_window<window,'prior accepted plan only');cp=p.current_carry(r,c,window);incoming=read(cp);cut=incoming['cutoff_tick']
 ledger=e.publish(source,None,expected_bundle_sha256=sh,cutoff_tick=cut,simulate=True)
 # Consumer raw pair is authenticated, not granted acceptance by ledger/index flags.
 _,_,_,consumer,ch=accepted_bundle(run,window);case=e.unpack(consumer['case'])
 e.need(consumer['run_id']==ledger['run_id']and consumer['case_identity']==ledger['case_identity'],'run/case mismatch')
 out=Path(out).resolve();e.need(not out.exists(),'new output required');out.mkdir(parents=True)
 import thermal_physical
 original=thermal_physical.build_free_scuc;rows=[]
 from dataclasses import asdict,is_dataclass
 def hook(**kw):
  m=original(**kw);host_case=deepcopy(kw);h=host_case['energy_inputs']['horizon']
  if is_dataclass(h):host_case['energy_inputs']['horizon']=asdict(h)
  rows.append(e.apply_ledger_to_free_model(m,host_case,ledger,host_case_identity=consumer['case_identity'],run_id=c['run_id'],expected_ledger_sha256=ledger['ledger_sha256']));return m
 thermal_physical.build_free_scuc=hook
 try:p.engine.build(r/'seed',cp,out/'prepared')
 finally:thermal_physical.build_free_scuc=original
 e.need(len(rows)==1,'exactly one full builder hook')
 q=read(out/'prepared/template.json');n=read(out/'prepared/names.json');oldq=consumer['SCUC_request'];oldn=consumer['names'];newby=dict(zip(n['constraints'],q['rows']));oldby=dict(zip(oldn['constraints'],oldq['rows']))
 e.need(n['variables']==oldn['variables']and q['objective']==oldq['objective'],'original columns/objective');e.need([{k:v for k,v in x.items()if k!='start'}for x in q['variables']]==[{k:v for k,v in x.items()if k!='start'}for x in oldq['variables']],'original bounds/kinds')
 e.need(all(newby[k]==v for k,v in oldby.items()),'original constraint changed');x=consumer['SCED_response']['primal'];added=[v for k,v in newby.items()if k not in oldby];err=max([0.]+[abs(math.fsum(a*x[j]for j,a in row['terms'])-row['lb'])for row in added]);e.need(err<1e-7,'new restrictions require new native solve; cannot adopt')
 # Exact new carrier, NOT producer.engine.carry, handles this accepted prefix.
 new=e.carry_candidate(consumer,ledger,expected_bundle_sha256=ch,expected_ledger_sha256=ledger['ledger_sha256']);reference=read(r/f'handoff/W{window+1}/CARRY.json')
 for k in ('thermal_age_h','storage','cutoff','boundary_at','executed_end_exclusive','issued_end_exclusive'):e.need(new[k]==reference[k],'exact carry compatibility '+k)
 e.need(new['boundary_states']==reference['thermal'],'thermal exact carry compatibility')
 write(out/'LEDGER.json',ledger);write(out/'EXACT_CARRY.json',new)
 result=dict(status='arbitrary_registered_v6_run_full_builder_and_exact_handoff_passed',run_id=c['run_id'],source_window=source_window,consumer_window=window,installed_manifest=MANIFEST,source_bundle_sha256=sh,consumer_bundle_sha256=ch,added_rows=len(added),added_residual=err,old_constraints_AGC_400_storage_unchanged=True,new_native_calls=0,old_stable_only_handoff_used=False,source_preflight_may_replay_legacy_registered_history=True,adopted_raw_compatibility_not_new_solve=True,formal=False,safety_release=False);write(out/'RESULT.json',result);return result
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--source-window',type=int,required=True);p.add_argument('--window',type=int,required=True);p.add_argument('--output',required=True);p.add_argument('--research-fixed-curve-events',action='store_true');a=p.parse_args();print(json.dumps(build_and_replay(a.run,a.source_window,a.window,a.output,research_fixed_curve_events=a.research_fixed_curve_events)))
