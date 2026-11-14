"""Five-thermal research adapter. No optimizer/AC execution in this CLI."""
import argparse, json, sys, hashlib, shutil, ast, subprocess
from pathlib import Path
CORE='guangdong_agc_joint_safety_loop_v1_20261112'
def read(p): return json.loads(Path(p).read_bytes())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 with Path(p).open('x') as f: json.dump(x,f,indent=2,allow_nan=False)
def load(root):
 root=Path(root).resolve()
 manifest=root/'DEPENDENCIES.json'
 if manifest.exists():
  for p,h in read(manifest)['files'].items():
   if sha(root/p)!=h: raise ValueError('dependency changed: '+p)
 sys.path.insert(0,str(root/CORE));import loop,proof
 if loop.ROOT!=root: raise ValueError('dependency namespace already loaded from another root; use separate process')
 return loop,proof

def bundle(root,out):
 root=Path(root).resolve();out=Path(out).resolve()
 if out.exists(): raise ValueError('fresh dependency output required')
 paths=[CORE+'/'+p for p in ('loop.py','proof.py','launch_candidate.py','review_and_ac.py')]
 paths += ['guangdong_r73_agc_parameter_interface_20261112/'+p for p in ('adapter.py','projection.py','example.synthetic.json')]
 paths += ['guangdong_r77_agc_safety_gate_20261112/gate.py','guangdong_pipeline_20261112/da_math/scoped_kkt.py','guangdong_r30_ac_security_20261112/IEEE39_RESEARCH_INPUT.json','guangdong_r30_ac_security_20261112/ac_security.py','guangdong_fresh_run_ac_bridge_20261112/bridge.py','guangdong_r53_same_source_da_chain_20261112/dual_precision_v3/highs_worker_unscaled_dual_1e9.py']
 prefix='guangdong_after_reset_r23_compressed_20261112/repo/src/smm/'
 todo=['gd_october_job_codec'];seen=set()
 while todo:
  mod=todo.pop()
  if mod in seen: continue
  seen.add(mod);p=prefix+mod+'.py';tree=ast.parse((root/p).read_text());paths.append(p)
  for n in ast.walk(tree):
   if isinstance(n,ast.ImportFrom) and n.level==1:
    deps=[n.module.split('.')[0]] if n.module else [a.name for a in n.names]
    for dep in deps:
     if (root/(prefix+dep+'.py')).exists():todo.append(dep)
 # Empty package avoids side effects, but original package is copied when present.
 if (root/(prefix+'__init__.py')).exists():paths.append(prefix+'__init__.py')
 out.mkdir(parents=True)
 for p in sorted(set(paths)):
  dst=out/p;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/p,dst)
 write(out/'DEPENDENCIES.json',dict(schema='gd.agc.adapter.dependencies.v1',files={p:sha(out/p) for p in sorted(set(paths))},read_only_sources=True,no_native_calls=True))
 return dict(files=len(set(paths)),dependency_root=str(out))

def prepare(L,P,context,thermal,out):
 c=read(context);d=read(thermal);L.verify_context(c);controller=L.Loop(c);row=controller.clear(d)
 out=Path(out);out.mkdir(parents=True,exist_ok=False)
 for name,z in [('CONTEXT.json',c),('thermal_input.json',d),('ROUND.json',row),('LOOP_PENDING.json',controller.snapshot())]:write(out/name,z)
 if controller.status!='awaiting_SCED_AC_evidence':return dict(status=controller.status,native_allowed=False)
 q,n=P.expected_model(row['physical_request']);old={k:q.pop(k) for k in list(q) if k.startswith('r67_')};q['agc_joint_binding']=row['binding'];q['execution_authority']=False
 for name,z in [('PHYSICAL_REQUEST.json',row['physical_request']),('template.json',q),('names.json',n),('PARENT_METADATA_PROVENANCE_ONLY.json',old)]:write(out/name,z)
 local=L.read(L.resolve(c['parent_refs']['parent_local_input']));offset=old['r67_global_offset'];cells=sorted({m['global_cell'] for m in row['physical_request']['margin_rows']});selected=max(cells,key=lambda t:(sum(v[t-offset] for v in local['load_mw'].values()),-t))
 write(out/'READY.json',dict(AC_selected_cell=selected,AC_selection_rule='maximum frozen forecast gross load in candidate hour; earliest tie',schema='gd.agc.adapter.prepared.v1',status='pure_prepared_no_new_native',artifacts={p.name:L.file_ref(p) for p in out.iterdir() if p.is_file()},columns=len(q['variables']),rows=len(q['rows']),profile=q['options'],execution_authority=False,formal_submission=False))
 return dict(status=controller.status,columns=len(q['variables']),rows=len(q['rows']),native_calls=0)

def review(L,P,candidate,native,proof):
 c=Path(candidate);n=Path(native).resolve();ready=read(c/'READY.json')
 for ref in ready['artifacts'].values():L.resolve(ref)
 ev=read(proof);req=read(c/'PHYSICAL_REQUEST.json')
 for key,file in [('request','request.json'),('response','response.json')]:
  L.need(L.resolve(ev[key]).resolve()==n/file,'different native attempt '+key)
 L.need(sha(c/'names.json')==ev['names']['sha256'],'candidate names identity')
 # Rebuild round from its original context and declarations; not a caller-safe flag.
 row=read(c/'ROUND.json');context=read(c/'CONTEXT.json') if (c/'CONTEXT.json').exists() else read(c/'LOOP_PENDING.json')['context']
 ctl=L.Loop(context);fresh=ctl.clear(read(c/'thermal_input.json'));L.need(fresh==row,'candidate inputs/round changed')
 ctl.apply_verified_failure(ev)
 return ctl.snapshot()

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--dependency-root','--engine-root',dest='dependency_root',required=True,help='explicit frozen dependency tree; not a parent run');s=ap.add_subparsers(dest='command',required=True)
 p=s.add_parser('bundle-deps');p.add_argument('--output',required=True)
 p=s.add_parser('context');p.add_argument('--parent-run',required=True);p.add_argument('--hour',required=True);p.add_argument('--zone',required=True);p.add_argument('--as-of',required=True);p.add_argument('--output',required=True);p.add_argument('--parent-window',type=int)
 p=s.add_parser('example');p.add_argument('--context',required=True);p.add_argument('--output',required=True)
 p=s.add_parser('prepare');p.add_argument('--context',required=True);p.add_argument('--thermal-input',required=True);p.add_argument('--output',required=True)
 p=s.add_parser('ac-check');p.add_argument('--candidate',required=True);p.add_argument('--native',required=True);p.add_argument('--output',required=True);p.add_argument('--execute-ac',action='store_true')
 p=s.add_parser('native');p.add_argument('--candidate',required=True);p.add_argument('--output',required=True);p.add_argument('--execute-native',action='store_true')
 p=s.add_parser('review');p.add_argument('--candidate',required=True);p.add_argument('--native',required=True);p.add_argument('--ac-proof',required=True);p.add_argument('--output')
 a=ap.parse_args()
 if a.command=='bundle-deps':z=bundle(a.dependency_root,a.output)
 else:
  L,P=load(a.dependency_root)
  if a.command=='context':z=L.context_from_run(a.parent_run,a.hour,a.zone,a.as_of,parent_window=a.parent_window);write(a.output,z)
  elif a.command=='example':
   c=read(a.context);L.verify_context(c);d=read(L.ROOT/'guangdong_r73_agc_parameter_interface_20261112/example.synthetic.json');d['hour_start']=c['hour_start'];d['as_of']=c['as_of'];seed=d['units'][0].copy();d['units']=[]
   sys.path.insert(0,str(L.ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'));from smm.gd_october_job_codec import loads
   case=loads(L.resolve(c['parent_refs']['case']).read_bytes());idx=int((L.stamp(c['hour_start'])-L.stamp(case['contract']['day_start'])).total_seconds()/300)
   for uid in sorted(L.THERM):
    u=seed.copy();u.update(resource_id=uid,plant_id='SYNTHETIC_SEPARATE_PLANT_'+uid,zone=c['zone'],available_at=c['as_of'],qualified=True,qualification_evidence='Synthetic parent research qualification only; not actual registration',capacity_excluding_outage_mw=case['energy_inputs']['thermal'][uid]['max_mw'][idx],declared_ratio=.05,k=2.,k1=1.,bid=6.,stable_online=True);d['units'].append(u)
   for p in d['parameters'].values():p.update(available_at=c['as_of'],effective_from=c['hour_start'],effective_to=c['hour_end'],source='Explicit independent synthetic example; not submitted bid or official declaration',source_grade='research_assumption')
   for k,v in dict(distribution_zone_demand_mw=50.,provincial_procurement_target_mw=50.,local_reserved_floor_mw=0.,full_control_area_kmax=2.,bid_floor=1.).items():d['parameters'][k]['value']=v
   d['adapter_declaration']=dict(synthetic_only=True,formal_submission=False,plant_membership='separate synthetic plants, not actual ownership',bid_performance='bid6, k2, k1=1 are synthetic, not measured');write(a.output,d);z=dict(status='synthetic_example_only',formal_submission=False)
  elif a.command=='ac-check':
   command=[sys.executable,'-B',str(Path(a.dependency_root)/CORE/'review_and_ac.py'),'--candidate',a.candidate,'--native',a.native,'--output',a.output]
   if a.execute_ac:command.append('--execute-ac')
   subprocess.run(command,check=True);z=dict(status='AC_checked' if a.execute_ac else 'AC_input_prepared',AC_calls=1 if a.execute_ac else 0)
  elif a.command=='native':
   if not a.execute_native:z=dict(status='dry_run',native_calls=0,requires='--execute-native')
   else:
    subprocess.run([sys.executable,'-B',str(Path(a.dependency_root)/CORE/'launch_candidate.py'),'--candidate',a.candidate,'--output',a.output,'--execute-native'],check=True);z=dict(status='attempt_recorded',path=a.output)
  elif a.command=='prepare':z=prepare(L,P,a.context,a.thermal_input,a.output)
  else:
   z=review(L,P,a.candidate,a.native,a.ac_proof)
   if a.output:write(a.output,z)
 print(json.dumps(z,allow_nan=False))
if __name__=='__main__':main()
