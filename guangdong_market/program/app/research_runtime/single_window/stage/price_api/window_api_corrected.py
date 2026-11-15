"""One explicitly authorized research planning window, no actual handoff."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json,hashlib,time
from stage_api import prepare_registered_stage
from predicted_registry import register_predicted_stage,verify_predicted_stage,require_actual_consumer
from restore_source import restore_same_origin
from far_prepare import prepare_predicted_stage
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def continue_window(*,program_root,host,near_prepared,near_actual,research_agc_input,output,mode='prepare',execute_far=False,loss_overlay_factory=None):
 if mode not in ('prepare','research_native')or(mode=='research_native' and execute_far is not True):raise ValueError('explicit research native permission required')
 root=Path(program_root).resolve();out=Path(output).resolve();out.mkdir(exist_ok=False);h=host;h.session.verify_sources();before=(deepcopy(h.hooks.carries),deepcopy(h.hooks.ledgers),h.window,str(h.cp),h.lid)
 c=read(h.cp);stage=dict(block='near',count=24,start_tick=c['delivery_tick'],near_start_tick=c['delivery_tick'],anchor_ticks=3,initial_kind='issued_prefix_projected_boundary',executed_end_exclusive=c['executed_end_exclusive'],issued_end_exclusive=c['issued_end_exclusive'],carry_sha256=sha(h.cp))
 import thermal_physical
 original=thermal_physical.build_free_scuc;bindings=[];timing=[]
 def agc_hook(**kw):
  from agc_near import apply_near
  _,binding=apply_near(h.agc,kw,c);bindings.append(binding);return original(**kw)
 t=time.perf_counter();near_key=str(Path(near_prepared).resolve())
 if near_key in h.hooks.prepared:
  if h.hooks.session is not h.session:raise ValueError('same live session required')
  # This is not historical adoption. The next register_predicted_stage call
  # still verifies the real RuntimeHooks owner and exports/checks the raw pair.
  from registered_near import check_registered_near
  overlay=None if loss_overlay_factory is None else loss_overlay_factory()
  restored=check_registered_near(record=h.hooks.prepared[near_key],prepared=near_prepared,actual=near_actual,carry=h.cp,ledger_id=h.lid,run_id=h.session.registry['run_id'],stage=stage,expected_loss_profile=None if overlay is None else overlay.profile)
  timing.append(dict(stage='near_registered_source_reuse_no_rebuild',duration_s=time.perf_counter()-t))
 else:
  thermal_physical.build_free_scuc=agc_hook
  try:prepare_registered_stage(program_root=root,seed=h.session.registry['seed'],carry=h.cp,stage=stage,agc_source={'kind':'registered_host'},output=out/'near_rebuilt',hooks=h.hooks,ledger_id=h.lid,loss_overlay=None if loss_overlay_factory is None else loss_overlay_factory())
  finally:thermal_physical.build_free_scuc=original
  timing.append(dict(stage='near_source_exact_rebuild_and_AGC',duration_s=time.perf_counter()-t))
  restored=restore_same_origin(h.hooks,out/'near_rebuilt',near_prepared,near_actual)
 save(out/'NEAR_SOURCE.json',restored)
 prediction=register_predicted_stage(program_root=root,hooks=h.hooks,prepared=near_prepared,actual=near_actual,receipts=list(h.hooks.ledgers[h.lid]['receipts'].values()),output=out/'PREDICTED.json');pred=verify_predicted_stage(h.hooks,prediction['path'])
 try:require_actual_consumer(pred)
 except ValueError:pass
 else:raise AssertionError('prediction can execute')
 from gd_post_scuc_runtime.phase_runtime import clear,fixed,need
 from gd_post_scuc_runtime.planner import FinitePlan
 from gd_post_scuc_runtime.participation import allocate
 start=datetime.fromisoformat(pred['projected']['boundary_at']);fullhour=start.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1);shared=start.replace(minute=0,second=0,microsecond=0)
 data=read(research_agc_input);data.update(hour_start=fullhour.isoformat(),as_of=c['cutoff']);captured=[];plans=[]
 def planning_hook(**kw):
  p=FinitePlan(data,kw,clear,issued_end=(datetime.fromisoformat(c['origin'])+timedelta(minutes=5*c['issued_end_exclusive'])).isoformat());need(p.saturated,'only explicit saturated capacity path supported');plans.append(p);captured.append(deepcopy(kw));return p.build(original,kw)
 thermal_physical.build_free_scuc=planning_hook;t=time.perf_counter()
 try:far=prepare_predicted_stage(program_root=root,hooks=h.hooks,predicted=prediction['path'],output=out/'far',loss_overlay=None if loss_overlay_factory is None else loss_overlay_factory())
 finally:thermal_physical.build_free_scuc=original
 timing.append(dict(stage='far_build_AGC_planning',duration_s=time.perf_counter()-t));need(len(captured)==1,'single stage build');case=captured[0];p=out/'far/prepared';q=read(p/'template.json');n=read(p/'names.json');worker=h.session.program.engine.worker;actual=out/'far/actual';actual.mkdir();native_count=0
 def native(kind,model,worker_path):
  nonlocal native_count
  need(native_count<2,'far budget');native_count+=1;row=dict(stage=kind,started_at=datetime.now(timezone.utc).isoformat(),limit_s=3600,mip_gap=.01 if kind=='SCUC'else None);t=time.perf_counter()
  try:return worker.native(actual,kind,model,worker_path,3600.)
  finally:
   row.update(ended_at=datetime.now(timezone.utc).isoformat(),duration_s=time.perf_counter()-t);timing.append(row);(out/'TIMING_CURRENT.json').write_text(json.dumps(timing,indent=2))
 save(out/'INTENT.json',dict(mode=mode,max_far_native=2 if execute_far else 0,near_optimizations_reused=2,near_prepared=str(near_prepared),near_actual=str(near_actual),near_source=restored,prediction=prediction,stage=far['stage'],execution=False,publication=False,settlement=False))
 if mode=='prepare':return dict(status='prepared',far=far,native_calls=0)
 try:
  uq,u=native('SCUC',q,worker.V/'scuc_worker.py');agc_audit_started=time.perf_counter();ua=worker.checks(uq,u,n,True);save(out/'SCUC_AUDIT.json',ua);values=dict(zip(n['variables'],u['primal']));ticks=[t for t in range(24)if fullhour<=start+timedelta(minutes=5*t)<fullhour+timedelta(hours=1)];need(len(ticks)==12,'complete far hour')
  allocation,participation=allocate(data,case,values,ticks,clear);plans[0].check_result(values,allocation)
  nearbundle,_=h.session.export_generated_pair(Path(near_prepared),Path(near_actual),h.hooks.prepared[str(Path(near_prepared).resolve())]);from v6_host import e
  nc=e.unpack(nearbundle['case']);nv=dict(zip(nearbundle['names']['variables'],nearbundle['SCUC_response']['primal']));ns=datetime.fromisoformat(nc['contract']['domain_start']);nt=[t for t in range(24)if shared<=ns+timedelta(minutes=5*t)<shared+timedelta(hours=1)];ft=[t for t in range(24)if shared<=start+timedelta(minutes=5*t)<shared+timedelta(hours=1)];need(len(nt)+len(ft)==12,'shared complete hour');combined=deepcopy(case);seq=[('near',t)for t in nt]+[('far',t)for t in ft];joint={}
  for g,d in combined['energy_inputs']['thermal'].items():
   for k in ('min_mw','max_mw'):d[k]={i:(nc['energy_inputs']['thermal'][g][k][t]if side=='near'else case['energy_inputs']['thermal'][g][k][t])for i,(side,t)in enumerate(seq)}
   for i,(side,t)in enumerate(seq):joint[f'stable[{g},{i}]']=(nv if side=='near'else values)[f'stable[{g},{t}]']
  sd=deepcopy(data);sd['hour_start']=shared.isoformat();sa,sp=allocate(sd,combined,joint,list(range(12)),clear);na=read(Path(near_prepared)/'agc.json')
  for g,a in case['agc_awards'].items():
   for side in ('up_mw','down_mw'):
    need(all(na[g][side][str(t)]==sa['awards_mw'].get(g,0)for t in nt),'shared AGC changes accepted near; reclear required, not authorized')
    need(all(a[side][t]==sa['awards_mw'].get(g,0)for t in ft),'shared far AGC differs')
    need(all(a[side][t]==allocation['awards_mw'].get(g,0)for t in ticks),'new capacity needs changed model; blocked')
  save(out/'AGC_CANDIDATES.json',dict(shared=dict(hour=shared.isoformat(),near_ticks=nt,far_ticks=ft,result=sa),full=dict(hour=fullhour.isoformat(),ticks=ticks,result=allocation,participation=participation),partial_tail='planning only, not award',source_near_SCuc=sha(Path(near_actual)/'SCUC/response.json.gz'),source_far_SCuc=sha(actual/'SCUC/response.json.gz'),published=False,cutoff=c['cutoff'],availability=None,research_logical_time=True));timing.append(dict(stage='SCUC_audit_and_joint_AGC',duration_s=time.perf_counter()-agc_audit_started))
  lp=fixed(q,u['primal']);lp['options'].update(time_limit=3600.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
  eq,ed=native('SCED',lp,worker.V/'lp_worker.py');t=time.perf_counter();ea=worker.checks(eq,ed,n,False);verification=e.verify_pair(uq,u,eq,ed,n)
  accepted=dict(schema='gd.predicted.far_stage.accepted.v1',stage=far['stage'],prediction=prediction,SCUC=ua,SCED=ea,verification=verification,pins={str(f):sha(f)for f in [p/'template.json',p/'names.json',p/'derived_case.json',*[f for f in (p/'LOSS_PROFILE.json',p/'LOSS_OVERLAY.json')if f.exists()],out/'AGC_CANDIDATES.json',*[actual/k/f for k in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]},native_calls=2,execution_allowed=False,publication=False,accounting=False)
  save(out/'FAR_ACCEPTED.json',accepted);timing.append(dict(stage='SCED_audit_and_pair_verification',duration_s=time.perf_counter()-t));result=dict(status='near_reused_far_pair_accepted',near_native_reused=2,new_native_calls=native_count,PRICE=False,far_accepted_sha256=sha(out/'FAR_ACCEPTED.json'))
 except Exception as ex:
  import traceback
  save(out/'FAILURE.json',dict(error=str(ex),traceback=traceback.format_exc(),native_calls=native_count));raise
 finally:
  need(before==(h.hooks.carries,h.hooks.ledgers,h.window,str(h.cp),h.lid),'actual domain advanced');save(out/'TIMINGS.json',timing)
 result.update(actual_domain_unchanged=True,publication=False,execution=False,settlement=False);save(out/'RESULT.json',result);return result
