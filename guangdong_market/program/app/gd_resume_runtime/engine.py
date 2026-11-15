"""Current Pipeline only: new owner/attempt, immutable historical stage sources.
No arbitrary object deserialization, retry, parameter change or security release.
"""
from pathlib import Path
from copy import deepcopy
import json,hashlib,uuid,re,sys
APP=Path(__file__).resolve().parents[1]
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(v,m):
 if not v:raise ValueError(m)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2)
PARAMS=('research_event_simulation','counterfactual_continue','ac_once','event_price_profile','stop_request','windows','nextday_selection','nextday_explicit','reference_revision','post_scuc_planning','post_declarations','post_scuc_day','candidate_loss_once','finite_h1','finite_hourly','pricing_clock_path','pricing_delta','rt_clock_mode')
def pins_for(folder):
 return {str(p.resolve()):sha(p) for p in Path(folder).rglob('*') if p.is_file() and p.suffix in ('.json','.gz')}
def verify(pins):
 for p,d in pins.items():need(sha(p)==d,'接续来源发生变化：'+p)
class HistoricalPrice:
 def __init__(self,h,a,pins):self.h=h;self.identity=(id(h),id(h.session),h.branch_id);self.a=a;self.pins=pins
 def verify(self):
  need(self.identity==(id(self.h),id(self.h.session),self.h.branch_id),'历史价格宿主改变');self.h.session.verify_sources();verify(self.pins);return read(self.a)
def prior_price(x,q,old,source_path=None):
 a=(Path(source_path)if source_path else old/'pipeline/price'/f'q{q}/ACCEPTED.json').resolve();z=read(a);ctx=z['context'];h=x.host
 if z.get('schema')=='gd.PRICE.reaccepted_preserved_raw.v1':
  from .recovery_overrides import validate_price
  validate_price(a,old)
 need(ctx['run_id']==h.branch_id and ctx['short_run']==str(h.source_branch) and ctx['day_quarter']==q,'历史价格来源不匹配')
 need(z['KKT']['passed'] is True,'缺少已完成价格证明');verify(z['source_pins']);pins={**z['source_pins'],str(a):sha(a)}
 if getattr(h,'same_period_execution',False) or q:
  _,actual=h.accepted_source(q if getattr(h,'same_period_execution',False) else q-1);response=read(Path(actual)/'SCED/COMPLETED.json')['response']['raw_sha256']
 else:response=read(h.session.run/'seed/WARMSTART_SOURCE.json')['response_sha256']
 need(ctx['SCED_response_sha256']==response,'历史价格与物理来源不匹配')
 x.prices.accepted[q if getattr(h,'same_period_execution',False) else q-1]=(a,sha(a),HistoricalPrice(h,a,pins))
 view=x.prices.output/f'q{q}'
 if not view.exists():view.symlink_to(a.parent,target_is_directory=True)
def transfer_completed(x,w,folder):
 """Completed non-due-hour states; unchanged code+inputs and accepted raw identities.
 Historical proofs remain historical, not a claim of new optimization/KKT.
 """
 from v6_host import e
 h=x.host;folder=Path(folder).resolve();p=folder/'prepared';a=folder/'actual';cp=folder/'EXACT_CARRY.json'
 proof=read(a/'ACCEPTED_PAIR.json');result=read(folder/'RESULT.json');carry=read(cp);incoming=read(p/'CARRY.json')
 expected_mode='provincial_15min_execution'
 need(getattr(h,'same_period_execution',False),'province resume requires same-period execution')
 for c in (incoming,carry,read(h.cp)):need(c.get('rt_clock_mode','legacy_issued_prefix')==expected_mode,'历史检查点时钟模式不匹配，禁止混接')
 if expected_mode=='provincial_15min_execution':
  need(incoming['delivery_tick']==w and carry['delivery_tick']==(w+1) and result['executed_end_exclusive']==(w+1) and result['issued_end_exclusive']==(w+1),'历史执行边界不是首个15分钟结束状态')
 need(proof['status']=='accepted_research_pair' and result['branch_id']==h.branch_id,'缺少同逻辑分支已接受结果')
 need(proof['prepared_sha256']==sha(p/'SUMMARY.json'),'数学输入绑定改变')
 for stage in ('SCUC','SCED'):need(proof[stage+'_response_sha256']==sha(a/stage/'response.json.gz'),'原始解身份改变')
 need(result['handoff']['carry_sha256']==sha(cp),'历史交接边界改变')
 need(Path(result['handoff']['carry_path']).resolve()==cp,'历史交接路径不是同一文件')
 # Normalize only the in-memory view after strict resolved identity, not old records.
 result=deepcopy(result);result['handoff']['carry_path']=str(cp)
 for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick','pumped_storage','pumped_storage_declaration_path','pumped_storage_declaration_sha256'):need(incoming.get(k)==read(h.cp).get(k),'历史状态链不连续：'+k)
 if incoming.get('pumped_storage'):
  decl=Path(incoming['pumped_storage_declaration_path']);need(sha(decl)==incoming['pumped_storage_declaration_sha256'],'抽蓄声明变化')
  from gd_provincial15_model.pumped_base import derive_pumped_carry
  ps_case=h.session.program.engine.loads((p/'derived_case.json').read_bytes())
  ps=derive_pumped_carry(ps_case,e.read(a/'SCED/response.json.gz'),read(p/'names.json'))
  need(ps==carry.get('pumped_storage'),'历史抽蓄状态不是首个实际执行格的库容、功率与机组状态')
 local=pins_for(folder);verify(carry['source_pins']);local.update(carry['source_pins'])
 ledger=deepcopy(h.hooks.ledgers[h.lid]);ledger.pop('ledger_sha256');need(not ledger['receipts'] and not ledger['pending'],'暂不支持带事件指令的历史接续');# Event publication is timestamped at model availability, not executed end.
 from datetime import datetime
 summary=read(p/'SUMMARY.json');publication=(datetime.fromisoformat(summary['domain_start'])-datetime.fromisoformat(carry['origin'])).total_seconds()/900
 need(publication==int(publication)==w and incoming['cutoff_tick']==w and carry['cutoff_tick']==w+1,'历史公告时标与执行时标不一致')
 need(ledger['cutoff_tick']<=publication,'事件账本时钟倒退')
 ledger['cutoff_tick']=int(publication);ledger['ledger_sha256']=e.digest(ledger);need(ledger['ledger_sha256']==carry['event_ledger_sha256'],'历史事件账本不匹配')
 h.hooks.ledgers[ledger['ledger_sha256']]=ledger;h.lid=ledger['ledger_sha256'];h.accepted[w]=(p,a,local);h.hooks.carries[str(cp)]=sha(cp);h.cp=cp;h.window=w+1;h.results.append(result)
 # Immutable source ledger with actual historical values. No read_result/KKT replay.
 n=read(p/'names.json');r=e.read(a/'SCED/response.json.gz')
 if proof.get('post_SCUC_phase'):
  from .owned_pair import historical_phase_names
  phase_names=historical_phase_names(folder,local);need(phase_names['variables']==n['variables'],'历史阶段变量顺序不同')
 need(len(n['variables'])==len(r['primal']),'历史原解长度与变量序列不同，禁止zip截断')
 shifted={};first=w
 for k,v in zip(n['variables'],r['primal']):
  m=re.search(r'(\d+)\]$',k)
  if m:k=k[:m.start(1)]+str(first+int(m[1]))+']'
  shifted[k]=v
 delta={k:v for k,v in shifted.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<first+1}
 report=dict(window=w,prepared=str(p),actual=str(a),SCED_response_sha256=read(a/'SCED/COMPLETED.json')['response']['raw_sha256'],historical_accepted_math_source=True,strict_pair_replayed=False,carry_registered=True,**{k:proof[k]for k in ('SCED_response_kind','dual_certificate_sha256','native_dual_passed')})
 z=dict(prepared=str(p),actual=str(a),window_values=shifted,prefix_delta=delta,out=str(cp),carry=carry,pins=local,report=report)
 x.prices.source._records.append(dict(data=z,digest=e.digest(z)))
class PureNativeBoundary(RuntimeError):pass

def persist_quarter_boundary(out,w,durable,durable_config,checkpoint):
 from .executed_quarter import write as write_executed_quarter
 from .hourly_durable import seal_quarter,wait_for_upload,pack
 stop=durable_config.get('stop_after_executed_window')
 need(stop is None or (type(stop)is int and 0<=stop<96),'invalid bounded recovery window')
 state=out/f'EXECUTED_QUARTER_W{w}.json'
 write_executed_quarter(out,w,state)
 checkpoint('窗口'+str(w)+'已校验，准备15分钟完整检查点')
 directory=seal_quarter(out,w,durable['config_path'],durable['config_sha256'],executed_state_path=state)
 checkpoint('窗口'+str(w)+'本机检查点完成，等待外部保存')
 if durable_config['policy']=='cloud_required':
  pack(directory);wait_for_upload(directory,timeout_s=durable['timeout_s'])
 else:pack(directory)
 checkpoint('窗口'+str(w)+('外部保存已确认'if durable_config['policy']=='cloud_required'else'仅本机保存，可靠外部进度不增加'))
 return stop==w

def execute(review,out,*,execute_native=False,prepare_only=False):
 need(execute_native is True or prepare_only is True,'接续需单独授权剩余预算');verify(review['source_pins']);old=Path(review['source_output']);original_plan=read(old/'PLAN.json');extension=review.get('kind')=='explicit_4_to_96_extension';plan=review.get('target_plan')or original_plan;chain=review.get('completed_chain')if extension else None;out=Path(out)
 recovery=review.get('recovery_overrides')
 if recovery:
  from .recovery_overrides import load
  need(load(recovery['manifest_path'],old)==recovery,'恢复覆盖来源改变')
 sys.path[:0]=[str(APP),str(APP/'gd_live_market_runtime')]
 from session import Pipeline,require_space
 from gd_live_control import sources
 persisted=deepcopy(plan);persisted.update(solver_policy_transition=review.get('solver_policy_transition'),recovery_overrides=recovery,durable_checkpoint=review.get('durable_checkpoint'),output=str(out),logical_source_branch=original_plan.get('logical_source_branch',str(old/'pipeline/dispatch')),source_pins={**plan['source_pins'],**sources()},resume_parent=str(old),execution_attempt_id=review['execution_attempt_id']);write(out/'PLAN.json',persisted)
 params={k:plan[k] for k in PARAMS if k in plan};params.setdefault('rt_clock_mode','provincial_15min_execution');x=Pipeline(plan['run'],out/'pipeline',research=True,**params)
 from .recovery_branch import identify,record_index
 branch=identify(old,review['source_pins']);record_index(out,branch,review['execution_attempt_id'])
 h=x.host;h.branch_id=branch['branch_id'];h.source_branch=Path(original_plan.get('logical_source_branch',str(old/'pipeline/dispatch')));x.execution_attempt_id=review['execution_attempt_id'];h.execution_attempt_id=x.execution_attempt_id
 from source_ledger import LedgerSource
 x.prices.source=LedgerSource(h,windows=plan['windows'])
 # Pipeline constructs a fresh temporary branch before recovery adopts the old
 # logical branch. Bind a NEW declaration after that adoption; retain both the
 # old accepted declaration and the unused initial declaration byte-for-byte.
 from gd_live_market_runtime.pricing_delta import declare as declare_delta,policy as delta_policy
 need(plan.get('pricing_delta_policy')==delta_policy(plan.get('pricing_delta',.1)), 'resume delta policy differs')
 initial_delta=x.prices.delta_path
 delta=declare_delta(x.prices.pricing_delta,branch_id=h.branch_id,base_run=h.session.run,selected_DA=h.session.context['selected_DA'],plan_path=x.output/'PLAN.json')
 delta.update(selection_source='same reviewed delta rebound to verified recovered logical branch; not a parameter change',unused_initial_declaration_path=str(initial_delta),unused_initial_declaration_sha256=sha(initial_delta),source_reviewed_plan_path=str(old/'PLAN.json'),source_reviewed_plan_sha256=sha(old/'PLAN.json'))
 x.prices.delta_path=x.prices.output/'RESUMED_DELTA_DECLARATION.json'
 write(x.prices.delta_path,delta);x.prices.delta_sha256=sha(x.prices.delta_path)

 from lifecycle_runtime.gd_sced_precision_runtime.bridge import install
 h.sced_precision_policy=install(h.session.program);h.session.registry['source_pins'].update(h.sced_precision_policy.pins)
 from lifecycle_runtime.worker_proof_memo import install_for_pipeline
 install_for_pipeline(x)
 h.session.registry['source_pins'].update(review['source_pins'])
 from .historical_stage import install as historical_stage_install
 historical_stage_install(h,review['source_pins'])
 # Each actual RT native stage also rechecks space at the worker boundary.
 original_native=h.session.program.engine.worker.native
 def guarded_native(*args,**kwargs):
  need(not prepare_only,'纯准备不得调用原生求解器');require_space(out);return original_native(*args,**kwargs)
 h.session.program.engine.worker.native=guarded_native
 durable=review.get('durable_checkpoint');durable_config=None
 if durable:
  from .hourly_durable import preflight_config
  durable_config=preflight_config(out,durable['config_path'],durable['config_sha256'])
  need(durable_config['policy']==durable['policy'],'检查点策略不一致')
 stage_index={};new_calls=0;reserved=0
 quarter_mode=bool(durable_config and durable_config.get('cadence')=='quarter')
 def reserve(n,label):
  nonlocal reserved
  require_space(out);need(reserved+n<=review['remaining_native'],'超出明确剩余预算')
  if prepare_only:
   write(out/'PURE_NEXT_NATIVE_BOUNDARY.json',dict(stage=label,next_calls=n,native_calls=0,execution_attempt_id=x.execution_attempt_id));raise PureNativeBoundary('纯准备到达首次新原生边界：'+label)
  write(out/f'NATIVE_INTENT_{reserved}.json',dict(stage=label,calls=n,previous_reserved=reserved,execution_attempt_id=x.execution_attempt_id));reserved+=n
 def checkpoint(label):
  z=dict(stage=label,execution_attempt_id=x.execution_attempt_id,logical_branch=h.branch_id,historical_native_calls=review['completed_native'],new_native_calls=new_calls,remaining_native_budget=review['remaining_native']-new_calls,stages=stage_index,safety_release=False)
  if quarter_mode:
   from .hourly_durable import progress
   z['persistence_progress']=dict(progress(out),computed_window=h.window-1,validated_window=h.window-1,meaning='zero-based windows; external_saved is distinct from local/accepted, not dispatch safety')
  target=out/'CHECKPOINT.json';tmp=out/'CHECKPOINT.next.json';tmp.write_text(json.dumps(z,indent=2));tmp.replace(target)
 same_period=bool(getattr(h,'same_period_execution',False))
 need(not same_period or not(extension or recovery),'same-period recovery does not accept legacy extension/override manifests')
 oldq=Path(chain['price_paths']['0'])if chain else old/'pipeline/price/q0/ACCEPTED.json'
 if not same_period:
  if oldq.exists():prior_price(x,0,old,oldq)
  else:reserve(1,'q0');x.prices.prepare(-1);require_space(out);x.prices.execute(-1,execute_native=True);new_calls+=1
  if extension:
   from .extension import validate_proof,compare_price
   validate_proof(review);compare_price(x,0,oldq,out/'PREFIX_PRICE_EQUIVALENCE')
  checkpoint('初始价格就绪')
 for w in range(plan['windows']):
  folder=Path(chain['window_paths'][str(w)])if chain and str(w)in chain['window_paths']else old/'pipeline/dispatch'/f'W{w}';a=folder/'actual';haspair=(a/'ACCEPTED_PAIR.json').is_file()
  if haspair and not extension and (folder/'EXACT_CARRY.json').exists() and (folder/'RESULT.json').exists():
   transfer_completed(x,w,folder)
   if x.post_controller is not None and w%4==2:
    from .historical_hour import import_selected
    import_selected(x,w,folder,source_pins=review['source_pins'],out=out)
   stage_index[str(w)]=dict(kind='historical_completed_window_no_math_replay',prepared=str(folder/'prepared'),actual=str(a))
  elif haspair and (x.post_controller is not None and w%4==2 and (w+2)//4 in x.post_controller.declarations):
   from .owned_pair import attach
   owned=out/f'OWNED_W{w}';attach(x,owned,folder,source_pins=review['source_pins'],allow_new_binding=extension);stage_index[str(w)]=dict(kind='historical_pair_new_gate_handoff_selection',prepared=str(owned/'prepared'),actual=str(owned/'actual'));x.prices.source.accept(w)
  elif haspair and extension and w==3:
   from .owned_pair import ordinary
   ordinary(x,folder,source_pins=review['source_pins']);stage_index[str(w)]=dict(kind='extension_full_W3_model_rebuilt_equal_old_raw',prepared=str(h.accepted[w][0]),actual=str(h.accepted[w][1]));x.prices.source.accept(w)
  elif haspair and (folder/'EXACT_CARRY.json').exists() and (folder/'RESULT.json').exists():
   transfer_completed(x,w,folder);stage_index[str(w)]=dict(kind='historical_completed_window',prepared=str(folder/'prepared'),actual=str(a))
  elif haspair:
   from .owned_pair import ordinary
   ordinary(x,folder,source_pins=review['source_pins']);stage_index[str(w)]=dict(kind='historical_pair_new_ordinary_handoff',prepared=str(h.accepted[w][0]),actual=str(h.accepted[w][1]));x.prices.source.accept(w)
  elif (a/'SCUC/COMPLETED.json').is_file() and (not(a/'SCED').exists() or (a/'SCED/COMPLETED.json').is_file()):
   from .partial_pair import finish
   used=finish(x,folder,source_pins=review['source_pins'],before_native=reserve);new_calls+=used;stage_index[str(w)]=dict(kind='historical_SCUC_new_SCED',prepared=str(h.accepted[w][0]),actual=str(h.accepted[w][1]));x.after_pair(w)
  else:
   need(not a.exists(),'存在未处理原生阶段，禁止重复启动')
   if prepare_only:h.prepare()
   reserve(2,'W'+str(w))
   if not prepare_only:h.prepare()
   require_space(out);h.run(execute_native=True);new_calls+=2;stage_index[str(w)]=dict(kind='new_native_pair',prepared=str(h.accepted[w][0]),actual=str(h.accepted[w][1]));x.after_pair(w)
  visible=h.output/f'W{w}'
  chosen=Path(h.accepted[w][0]).parent
  if not visible.exists():visible.symlink_to(chosen,target_is_directory=True)
  if same_period:
   price=old/'pipeline/price'/f'q{w}/ACCEPTED.json'
   if price.exists():prior_price(x,w,old,price)
   else:
    if prepare_only:x.prices.prepare(w)
    reserve(1,'q'+str(w))
    if not prepare_only:x.prices.prepare(w)
    require_space(out);x.prices.execute(w,execute_native=True);new_calls+=1
  checkpoint('窗口'+str(w)+'状态已接续')
  if w%4==3:
   from .hour_export import hour
   historical_hour=Path(recovery['hours'][str(w//4)])if recovery and str(w//4)in recovery['hours']else old/'pipeline/price'/f'HOUR{w//4}.json'
   if not extension and historical_hour.is_file():
    from .recovery_branch import pinned_value
    need(pinned_value(review['source_pins'],historical_hour)==sha(historical_hour),'历史小时核算未绑定');hz=read(historical_hour);verify(hz['source_pins'])
    need(hz['run_id']==h.branch_id and hz['branch']==str(h.source_branch) and hz['input_binding']['hour']==w//4 and hz['payable']is False and hz['safety_release']is False,'历史小时范围不匹配')
    for qindex in range(w-3,w+1):
     ap,ah,owner=x.prices.accepted[qindex if same_period else qindex-1];owner.verify()
     from .recovery_branch import pinned_value
     need(pinned_value(hz['source_pins'],ap)==ah,'历史核算与已拥有价格来源不同')
    (x.prices.output/historical_hour.name).symlink_to(historical_hour)
    for stem in ('EXECUTION_H','SETTLEMENT_H','FOUR_PRICE_ARITHMETIC_INPUTS_H'):
     oldfile=historical_hour.parent/f'{stem}{w//4}.json'
     if oldfile.is_file():need(pinned_value(review['source_pins'],oldfile)==sha(oldfile),'小时附属来源未绑定');(x.prices.output/oldfile.name).symlink_to(oldfile)
   else:
    hour(x.prices,w//4)
    if durable and not quarter_mode:
     from .hourly_durable import seal_hour,wait_for_upload,pack
     checkpoint('小时'+str(w//4)+'完成，等待持久检查点')
     directory=seal_hour(out,w//4,durable['config_path'],durable['config_sha256'])
     if durable_config['policy']=='cloud_required':wait_for_upload(directory,timeout_s=durable['timeout_s'])
     else:pack(directory)
     checkpoint('小时'+str(w//4)+('已云端持久保存'if durable_config['policy']=='cloud_required'else'仅本地检查点，未云备份'))
  if quarter_mode and not haspair:
   # Stable accepted handoff, with q_w already accepted; q_(w+1) is future,
   # and no D1/next-pair/next-price native may start before the cloud receipt.
   if persist_quarter_boundary(out,w,durable,durable_config,checkpoint):
    return dict(status='bounded_recovery_checkpoint_reached',execution_attempt_id=x.execution_attempt_id,new_native_calls=new_calls,historical_native_calls=review['completed_native'],window=w,executed_end_exclusive=(w+1),next_price_not_started=w+1,formal_bill=None,safety_release=False)
  if extension and w==3:
   write(out/'EXTENSION_PREFIX_GATE.json',dict(passed=True,historical_native_calls=12,new_native_calls=0,mode='finite_h1_to_finite_hourly96',source_map=chain['window_paths'],new_H1_selection=True,W3_new_full_model_equal=True,remaining_native_budget=277,safety_release=False))
  if w==94 and plan.get('post_scuc_day'):
   from .reference_import_scope import reference_import_scope
   with reference_import_scope(APP,expose=True):
    from .reference import select_or_execute
    if not(old/'pipeline/POST_D1_REFERENCE/ACCEPTED_REFERENCE.json').exists():reserve(1,'D1参考')
    overlay,used=select_or_execute(x,old,out/'pipeline/POST_D1_REFERENCE');x.prices.reference_overlay=overlay;new_calls+=used
  if not same_period and w<plan['windows']-1:
   price=Path(chain['price_paths'][str(w+1)])if chain and str(w+1)in chain['price_paths']else old/'pipeline/price'/f'q{w+1}/ACCEPTED.json'
   if recovery and str(w+1)in recovery['prices']:price=Path(recovery['prices'][str(w+1)])
   if price.exists():prior_price(x,w+1,old,price)
   else:
    from .reference_import_scope import reference_import_scope
    if prepare_only:
     with reference_import_scope(APP):x.prices.prepare(w)
    reserve(1,'q'+str(w+1))
    if not prepare_only:
     with reference_import_scope(APP):x.prices.prepare(w)
    require_space(out);x.prices.execute(w,execute_native=True);new_calls+=1
  if extension and w<3:
   from .extension import compare_price
   compare_price(x,w+1,price,out/'PREFIX_PRICE_EQUIVALENCE')
  checkpoint('窗口'+str(w)+'价格已接续')
 need(new_calls<=review['remaining_native'],'剩余预算超限')
 return dict(status='explicit_resume_complete_research',execution_attempt_id=x.execution_attempt_id,new_native_calls=new_calls,historical_native_calls=review['completed_native'],window=h.window,formal_bill=None,safety_release=False)
