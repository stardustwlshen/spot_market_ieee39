"""Normal reviewed resume API. Durable evidence is not a live permission token."""
from pathlib import Path
import json,hashlib,uuid,subprocess,sys,os,re
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def acquire(source):
 need(os.name=='posix','当前平台暂不支持安全持久接续锁；原正常入口仍可使用，不自动重启')
 import fcntl
 p=Path(source)/'PROCESS.guard';f=p.open('a+')
 try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:f.close();raise ValueError('仍有计算进程持有运行锁；不能重复接续')
 return f

def plan(source,*,prior_process_ended=False,expected=None,target_plan=None,checkpoint_config=None,recovery_manifest=None,solver_policy_manifest=None):
 from gd_live_control import sources
 from gd_resume_inventory import scan
 from gd_resume_runtime.solver_policy_compat import load as policy_load,permits
 policy=policy_load(solver_policy_manifest)if solver_policy_manifest else None
 sources();source=Path(source).resolve();inventory=scan(source,expected=expected);p=read(source/'PLAN.json');errors=list(inventory['errors'])
 if os.name!='posix':errors.append(dict(stage='platform',error='此平台暂不支持已验证的持久接续锁；未宣称Windows接续可用'))
 prior_native_fenced=False
 if p.get('post_scuc_day') and not(source/'pipeline/POST_D1_REFERENCE/ACCEPTED_REFERENCE.json').exists():
  try:
   from gd_resume_runtime.reference_abandonment import validate_abandoned_reference
   validate_abandoned_reference(source);prior_native_fenced=True
  except (OSError,ValueError,KeyError,TypeError):pass
 if not prior_process_ended and not(source/'EXIT.json').is_file() and not prior_native_fenced:errors.append(dict(stage='process',error='旧执行未记录退出且无明确未启动取消栅栏；不能凭缺心跳重启'))
 if inventory['unknown_native_occurrences']:errors.append(dict(stage='native',error='原生启动结果未知，必须先核明，不可重启'))
 if p.get('research_event_simulation')or p.get('stop_request')or p.get('candidate_loss_once'):errors.append(dict(stage='scope',error='当前接续暂不支持启停变更、未来交互声明或损耗纠正；保留原失败'))
 if p.get('reference_revision'):errors.append(dict(stage='reference',error='当前接续支持post-SCUC日计划参考，不支持旧版独立参考修订模式'))
 if p.get('ac_once'):errors.append(dict(stage='AC',error='已有AC调用的私有安全凭证不可由JSON恢复；需独立安全处理，不能自动重跑AC'))
 if list((source/'pipeline').glob('**/FUTURE_CAP/STATE.json')):errors.append(dict(stage='interactive',error='未来交互声明不可自动接续'))
 # No input/model migration. Viewer/launcher additions alone may differ.
 oldpins=p['source_pins']
 allow={'gd_live_control.py','gd_live_worker.py','gd_live_panel.py','gd_unified_page.py','gd_settlement_panel.py','gd_price_quality.py','gd_post_scuc_runtime/consumers.py','LIVE_MARKET_MANIFEST.json'}
 roots=sorted({Path(f).parent for f in oldpins if Path(f).name=='gd_live_control.py'},key=lambda p:len(p.parts),reverse=True)
 need(bool(roots),'无法识别历史程序根，禁止跳过源码校验')
 for f,d in oldpins.items():
  path=Path(f);matched=next((r for r in roots if path.is_relative_to(r)),None)
  if matched is not None:
   rel=path.relative_to(matched).as_posix();current=B/rel
   if not current.is_file():errors.append(dict(stage='source',error='计算来源文件缺失：'+rel))
   elif rel not in allow and not rel.startswith(('gd_resume','RESUME_CHAIN_REGISTRATION')) and sha(current)!=d and not permits(policy,'app/'+rel,d,sha(current)):errors.append(dict(stage='source',error='计算源码版本不同：'+rel))
 context_binding=None;d1_binding=None
 if target_plan is None:
  context=read(Path(p['run'])/'SHORT_CONTEXT.json')
  if any('/engines/'in f and not Path(f).is_relative_to(B.parent) for f in context['code_pins']):errors.append(dict(stage='context',error='跨安装接续需先准备同数据上下文，并提供当前已核对同模式计划；不得改旧来源路径'))
 if target_plan is not None:
  from gd_resume_runtime.context_equivalence import compare,d1
  from gd_resume_runtime.engine import PARAMS
  from gd_live_control import plan as normal_plan
  for k in PARAMS:
   if k!='nextday_selection':need(p.get(k)==target_plan.get(k),'同模式接续不能改变参数：'+k)
  need(p['da']==target_plan['da'],'日前来源改变')
  params={('event_simulation'if k=='research_event_simulation'else k):target_plan[k]for k in PARAMS if k in target_plan}
  need(normal_plan(target_plan['da'],target_plan['run'],Path(target_plan['output']).name,**params)==target_plan,'新上下文计划需正常复核')
  context_binding=compare(p['run'],target_plan['run'],solver_policy=policy)
  if p.get('nextday_selection'):d1_binding=d1(p['nextday_selection'],target_plan['nextday_selection'],p['run'],target_plan['run'])
 for r in inventory['windows']:
  if r['SCUC']['state']=='completed' and not r.get('math_accepted') and r['SCED']['state']not in ('absent','completed'):errors.append(dict(stage='W'+str(r['window']),error='已有原生结果尚无完整数学接受记录；先补只读验收，不重新求解'))
 if p.get('post_scuc_day') and inventory['windows'][94].get('receipt_selected') and not(source/'pipeline/POST_D1_REFERENCE/ACCEPTED_REFERENCE.json').is_file():
  try:
   from gd_resume_runtime.reference_abandonment import validate_abandoned_reference
   validate_abandoned_reference(source)
  except (OSError,ValueError,KeyError,TypeError)as exc:errors.append(dict(stage='H24_reference',error='H24历史事实参考接续需明确未启动取消栅栏：'+str(exc)))
 for r in inventory['prices']:
  if r['state']=='completed'and not r.get('math_accepted'):errors.append(dict(stage='q'+str(r['quarter']),error='定价原生已完成但未接受；先补原结果验收，禁止重新求解'))
 from gd_resume_chain import registered
 chain=registered(source)
 if chain:
  inventory['confirmed_completed_native_calls']=chain['completed_native'];inventory['remaining_native_upper_bound']=max(0,inventory['maximum_authorized_native_calls']-chain['completed_native'])
  errors.append(dict(stage='completed_chain',error='已登记显式接续已完成原12次；不能再补旧目录显示的3次。首小时到全天需新的明确扩域计划及模型等价证明'))
 pins={str(source/'PLAN.json'):sha(source/'PLAN.json'),**oldpins,**inventory['native_evidence']}
 for f in (source/'pipeline/dispatch').glob('W*/*.json'):pins[str(f)]=sha(f)
 for f in (source/'pipeline/dispatch').glob('W*/prepared/*.json'):pins[str(f)]=sha(f)
 for f in (source/'pipeline/dispatch').glob('W*/actual/ACCEPTED_PAIR.json'):pins[str(f)]=sha(f)
 for f in (source/'pipeline/price').glob('q*/ACCEPTED.json'):pins[str(f)]=sha(f)
 from gd_resume_runtime.engine import pins_for
 pins.update(pins_for(source/'pipeline'));pins.update({str(f):sha(f)for f in source.glob('OWNED_W*/HISTORICAL_PAIR_LINEAGE.json')})
 for folder in (source/'pipeline/dispatch').glob('W*'):
  if folder.is_dir():pins.update(pins_for(folder.resolve()))
 if context_binding:pins.update(context_binding['source_pins']);pins.update(target_plan['source_pins'])
 if d1_binding:pins.update(d1_binding['source_pins'])
 if policy:pins.update(policy['source_pins'])
 from gd_resume_runtime.recovery_branch import identify
 branch_index=identify(source,pins)
 recovery=None
 if recovery_manifest is not None:
  from gd_resume_runtime.recovery_overrides import load
  recovery=load(recovery_manifest,source);pins.update(recovery['source_pins'])
 durable=None
 if checkpoint_config is not None:
  config_path=Path(checkpoint_config).resolve();cfg=read(config_path);ident=cfg.get('execution_attempt_id')
  need(isinstance(ident,str)and re.fullmatch(r'resume-[0-9a-f]{32}',ident),'检查点必须绑定明确的新执行代次')
  from gd_resume_runtime.hourly_durable import preflight_config
  cfg_hash=sha(config_path);cfg=preflight_config(source/'resume_attempts'/ident,config_path,cfg_hash,normal_runtime=True)
  durable=dict(config_path=str(config_path),config_sha256=cfg_hash,policy=cfg['policy'],cadence=cfg.get('cadence','hour'),timeout_s=1800)
  pins[str(config_path)]=cfg_hash;pins[str(Path(cfg['baseline_manifest']).resolve())]=cfg['baseline_manifest_sha256']
 else:ident='resume-'+uuid.uuid4().hex
 return dict(solver_policy_transition=policy,recovery_overrides=recovery,durable_checkpoint=durable,branch_recovery=branch_index,schema='gd.resume.review.v1',target_plan=target_plan,context_relocation=context_binding,D1_relocation=d1_binding,source_output=str(source),execution_attempt_id=ident,output=str(source/'resume_attempts'/ident),completed_native=inventory['confirmed_completed_native_calls'],remaining_native=inventory['remaining_native_upper_bound'],prior_process_ended=bool(prior_process_ended),prior_native_fenced=prior_native_fenced,prior_native_fence_meaning='old reference O_EXCL barrier; not an EXIT assertion'if prior_native_fenced else None,identity=inventory['identity'],source_pins=pins,inventory=inventory,completed_chain=chain,errors=errors,can_start=not errors,new_AC_budget=0,formal=False,safety_release=False)

def start(review,*,confirmed=False):
 need(confirmed is True,'必须明确授权所示剩余预算');need(review.get('can_start') is True,'接续计划被阻断')
 # Fresh inventory and identity check immediately before locking/launch.
 fresh=extension_plan(review['source_output'],review['target_plan'],review['comparison_manifest'],prior_process_ended=review['prior_process_ended'])if review.get('kind')=='explicit_4_to_96_extension' else plan(review['source_output'],prior_process_ended=review['prior_process_ended'],expected=review['identity'],target_plan=review.get('target_plan'),checkpoint_config=(review.get('durable_checkpoint')or{}).get('config_path'),recovery_manifest=(review.get('recovery_overrides')or{}).get('manifest_path'),solver_policy_manifest=(review.get('solver_policy_transition')or{}).get('path'))
 need(fresh['can_start'] and fresh['source_pins']==review['source_pins'] and fresh['remaining_native']==review['remaining_native'],'复核后来源或预算改变')
 need(fresh.get('prior_native_fenced')==review.get('prior_native_fenced'),'prior native fence status changed')
 need(fresh.get('solver_policy_transition')==review.get('solver_policy_transition'),'solver-policy transition changed')
 need(fresh.get('recovery_overrides')==review.get('recovery_overrides'),'新恢复来源改变')
 need(fresh.get('durable_checkpoint')==review.get('durable_checkpoint'),'持久检查点配置或策略改变')
 source=Path(review['source_output']);lock=acquire(source)
 try:
  out=Path(review['output']);need(out.parent==source/'resume_attempts'and out.name==review['execution_attempt_id'],'接续目标不匹配');out.mkdir(parents=True,exist_ok=False)
  with(out/'RESUME_PLAN.json').open('x')as f:json.dump(review,f,indent=2)
  with(out/'stdout.log').open('ab')as log:
   proc=subprocess.Popen([sys.executable,'-B',str(B/'gd_resume_worker.py'),str(out)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(lock.fileno(),),env={**os.environ,'GD_RESUME_LOCK_FD':str(lock.fileno()),'PYTHONDONTWRITEBYTECODE':'1'})
  with(out/'PID.json').open('x')as f:json.dump(dict(pid=proc.pid,execution_attempt_id=review['execution_attempt_id']),f)
 finally:lock.close()
 return dict(status='new_execution_attempt_started',output=str(out),remaining_native_budget=review['remaining_native'],old_completed_native=review['completed_native'],publication_allowed=False)

def status(output):
 out=Path(output);p=read(out/'RESUME_PLAN.json');z=read(out/'CHECKPOINT.json')if(out/'CHECKPOINT.json').exists()else{}
 from gd_price_quality import from_accepted
 prices=[];display_errors=[]
 for f in sorted((out/'pipeline/price').glob('q*/ACCEPTED.json'),key=lambda f:int(f.parent.name[1:])):
  a=read(f)
  try:quality=from_accepted(f)
  except (OSError,ValueError,KeyError,TypeError)as exc:
   from gd_price_quality import unknown
   quality=unknown('质量展示来源未能核验：'+str(exc));display_errors.append(dict(path=str(f),error=str(exc)))
  prices.append(dict(quarter=a['context']['day_quarter'],raw_prices=a['raw_prices'],price_quality=quality))
 hours=[read(f)for f in sorted((out/'pipeline/price').glob('HOUR*.json'),key=lambda f:int(f.stem[4:]))]
 from gd_resume_runtime.hourly_durable import progress
 return dict(persistence_progress=progress(out),display_errors=display_errors,execution_attempt_id=p['execution_attempt_id'],source_output=p['source_output'],checkpoint=z,quarters=prices,hours=hours,exit=read(out/'EXIT.json')if(out/'EXIT.json').exists()else None,original_results_unchanged=True,publication_allowed=False)

def extension_plan(source,target_plan,comparison_manifest,*,prior_process_ended=False):
 """Separately reviewed 4→96 research extension; pure owned-prefix gate precedes 277."""
 from gd_resume_chain import registered
 from gd_resume_runtime.engine import verify,PARAMS
 from gd_live_control import sources
 sources();need(os.name=='posix','此平台暂不支持安全持久接续');source=Path(source).resolve();chain=registered(source);need(chain is not None and chain['completed_native']==12,'需要已登记的旧12次完整来源链')
 old=read(source/'PLAN.json');need(prior_process_ended or(source/'EXIT.json').exists(),'须先确认原执行结束')
 need(old['windows']==4 and old.get('finite_h1') and target_plan['windows']==96 and target_plan.get('finite_hourly') and target_plan.get('post_scuc_day'),'仅明确首小时至逐小时全天扩域')
 need(not target_plan.get('finite_h1') and not target_plan.get('post_scuc_planning') and not target_plan.get('reference_revision'),'扩域模式互斥')
 need(target_plan['maximum_native_calls']==289 and target_plan['AC_actual_calls']==0,'全天预算必须289且AC0')
 for k in ('da','counterfactual_continue','research_event_simulation','event_price_profile','candidate_loss_once','stop_request','SCED_solver','mip_gap','threads'):
  need(old.get(k)==target_plan.get(k),'扩域不得顺带改变：'+k)
 need(target_plan.get('nextday_explicit') is True and target_plan.get('nextday_selection'),'必须显式D1来源')
 verify(target_plan['source_pins'])
 from gd_live_control import plan as normal_plan
 control_params={('event_simulation'if k=='research_event_simulation'else k):target_plan[k]for k in PARAMS if k in target_plan}
 fresh=normal_plan(target_plan['da'],target_plan['run'],Path(target_plan['output']).name,**control_params)
 need(fresh==target_plan,'新的全天计划必须通过当前正常入口完整复核')
 mf=Path(comparison_manifest).resolve();proof=read(mf);verify(proof['source_pins'])
 need(proof.get('mathematical_native_calls')==0 and proof.get('not_authorization') is True and proof.get('not_live_resume_acceptance') is True,'只读比较证明不能是伪造live权限')
 from gd_resume_runtime.context_equivalence import compare,d1
 context_binding=compare(old['run'],target_plan['run']);d1_binding=d1(proof['D1_selection'],target_plan['nextday_selection'],old['run'],target_plan['run'])
 ident='extension-'+uuid.uuid4().hex
 pins={**context_binding['source_pins'],**d1_binding['source_pins'],**chain['source_pins'],**target_plan['source_pins'],**proof['source_pins'],str(mf):sha(mf),str(source/'PLAN.json'):sha(source/'PLAN.json')}
 return dict(schema='gd.resume.review.v1',kind='explicit_4_to_96_extension',context_relocation=context_binding,D1_relocation=d1_binding,source_output=str(source),execution_attempt_id=ident,output=str(source/'resume_attempts'/ident),completed_native=12,remaining_native=277,new_AC_budget=0,target_plan=target_plan,completed_chain=chain,comparison_manifest=str(mf),comparison_sha256=sha(mf),source_pins=pins,prior_process_ended=bool(prior_process_ended),can_start=True,errors=[],owned_full_prefix_gate_before_native=True,old_mode='finite_h1_first_hour',new_mode='finite_hourly96',formal=False,safety_release=False)
