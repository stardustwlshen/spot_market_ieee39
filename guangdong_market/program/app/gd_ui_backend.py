"""UI adapter: subprocess only; never imports an energy solver."""
from pathlib import Path
import sys,json,subprocess,hashlib,os,uuid
B=Path(__file__).resolve().parent
DEFAULT_CORE=Path(os.environ.get('GD_MARKET_ENTRY',str(B.parent/'engines/guangdong_v3/market.py')))
DEFAULT_RUNS=Path(os.environ.get('GD_RUNS_DIR',str(B.parent/'runs_guangdong')))
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def command(core,run,action,windows,settlement=False,scenario=None):
 if action not in ('run','resume') or type(windows)is not int or windows not in (4,96):raise ValueError('run/resume and 4/96 only')
 core=Path(core).resolve();run=Path(run).resolve()
 if not core.is_file():raise ValueError('user_v2 entry missing')
 if action=='run' and run.exists():raise ValueError('new run requires nonexistent directory')
 if action=='resume' and not(run/'RUN.json').is_file():raise ValueError('resume requires existing RUN.json')
 cmd=[sys.executable,'-B',str(core),'energy',action,'--run',str(run),'--rt-windows',str(windows)]
 if scenario:
  if action=='resume':raise ValueError('resume禁止更换场景，只使用run已冻结输入')
  from gd_ui_scenarios import validate
  z=validate(core,scenario)
  for role,flag in [('bundle','--bundle'),('pricing_spec','--pricing-spec'),('rt_assets','--rt-assets')]:cmd.extend([flag,str(Path(z['directory'])/z['entries'][role])])
 if settlement:cmd+=['--simulation-settlement']
 return cmd
def validate_resume_sources(core,run):
 """Pure early UX guard; core remains final authority for all stage identities."""
 base=Path(core).resolve().parent;record=read(Path(run)/'RUN.json');pins=record.get('source_pins')
 if not isinstance(pins,dict) or not pins:raise ValueError('RUN.source_pins 缺失：仅可只读查看，不能恢复')
 for name,h in pins.items():
  p=Path(name)
  if not p.is_absolute() or not p.resolve().is_relative_to(base):raise ValueError('旧来源或迁移来源 run 仅可只读：source_pins 不属于当前引擎目录；请使用原来源环境或新建 run')
  if not p.is_file() or sha(p)!=h:raise ValueError('当前引擎来源文件缺失/哈希已变：run 仅可只读；不能跳过核心来源验证')
 return dict(source_files=len(pins),source_root=str(base),source_pins_verified=True)
def list_run_directories(parent):
 parent=Path(parent).expanduser()
 if not parent.is_dir():return []
 out=[]
 for p in sorted(parent.iterdir(),key=lambda z:z.name):
  if p.is_dir() and (p/'RUN.json').is_file():
   try:r=read(p/'RUN.json');out.append(dict(directory=str(p.resolve()),run_id=r.get('run_id'),recorded_status=r.get('status'),scope='只读索引；非完成/恢复许可'))
   except (ValueError,OSError):out.append(dict(directory=str(p.resolve()),recorded_status='不可读取；不推断状态'))
 return out
def preflight(core,run,action,windows,settlement=False,scenario=None):
 cmd=command(core,run,action,windows,settlement,scenario)
 if action=='resume':validate_resume_sources(core,run)
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
 if p.returncode:raise ValueError(p.stderr[-4000:]or p.stdout[-4000:])
 from gd_ui_scenarios import validate
 binding=validate(core,scenario) if scenario else None
 return dict(scenario_binding=binding,command=cmd,core_sha256=sha(core),run=str(Path(run).resolve()),dryrun_output=p.stdout,execute_native=False)
def status(core,run,ac_plan='',ac_result=''):
 from gd_live_results import supported, status as live_status
 if supported(run):return live_status(run)
 cmd=[sys.executable,'-B',str(Path(core).resolve()),'status','--run',str(Path(run).resolve())]
 if bool(ac_plan)!=bool(ac_result):raise ValueError('AC plan/result must both be supplied')
 if ac_plan:cmd+=['--ac-plan',ac_plan,'--ac-result',ac_result]
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
 if p.returncode:raise ValueError(p.stderr[-4000:]or p.stdout[-4000:])
 d=json.loads(p.stdout)
 if d.get('schema')!='gd.delivery.user_status.v1':raise ValueError('unsupported status schema')
 return d
def reconcile_known_sced_failure(core,run,lock):
 """Unlock only a fully pinned, independently accepted SCED recovery; never unknown work."""
 root=Path(run).resolve();lock=Path(lock);old=read(lock);attempt=Path(old['attempt'])
 if attempt.parent!=lock.parent or read(attempt/'EXIT.json').get('returncode')!=1:raise ValueError('不是已知退出的原UI失败，禁止恢复锁')
 if '--run' not in old['command'] or Path(old['command'][old['command'].index('--run')+1]).resolve()!=root:raise ValueError('原UI运行身份不符')
 validate_resume_sources(core,root)
 if status(core,root).get('blocking_unknown_attempt')is not False:raise ValueError('仍有未知native结果，禁止解锁')
 revision=root/'SCED_RECOVERY_REVISION.json';rev=read(revision);runrecord=read(root/'RUN.json');acc=read(root/'da_sced/ACCEPTANCE.json')
 if rev.get('schema')!='gd.explicit.sced_algorithm_recovery.v1' or rev['run_id']!=runrecord['run_id'] or rev['input_identity']!=runrecord['input_identity'] or rev['base_source_identity']!=runrecord['source_identity']:raise ValueError('恢复revision与本run不一致')
 if rev.get('math_changed')is not False or rev.get('KKT_threshold_changed')is not False or rev.get('integer_commitment_changed')is not False or rev['strict_review'].get('passed')is not True:raise ValueError('恢复必须保留矩阵/整数/门禁且完整KKT通过')
 for group in ('source_pins','candidate_pins'):
  for p,h in rev[group].items():
   if sha(p)!=h:raise ValueError('恢复证据或源码已变')
 for p,h in rev['base_evidence'].items():
  q=(root/p).resolve()
  if not q.is_relative_to(root)or sha(q)!=h:raise ValueError('原始失败/SCUC证据已变')
 if acc.get('status')!='accepted' or acc['run_id']!=runrecord['run_id'] or acc.get('recovery_source_identity')!=rev['recovery_source_identity']:raise ValueError('缺少明确恢复SCED接受收据')
 for ref in acc['artifacts'].values():
  q=(root/ref['path']).resolve()
  if not q.is_relative_to(root)or sha(q)!=ref['sha256']:raise ValueError('SCED接受证据改变')
 if acc['artifacts']['SCED_recovery_revision']['sha256']!=sha(revision):raise ValueError('SCED revision未绑定')
 price=read(root/'da_price/PREPARED.json');inp=read(root/'da_price/input.json');receipt=inp['source_receipt']
 if price['run_id']!=runrecord['run_id'] or receipt['SCED_request_sha256']!=acc['artifacts']['request']['sha256'] or receipt['SCED_response_sha256']!=acc['artifacts']['response']['sha256']:raise ValueError('后续PRICE未绑定实际恢复SCED，不能启动')
 for ref in price['artifacts'].values():
  q=(root/ref['path']).resolve()
  if not q.is_relative_to(root)or sha(q)!=ref['sha256']:raise ValueError('PRICE恢复输入改变')
 preserved=lock.with_name('RECOVERED_FAILED_ATTEMPT_'+attempt.name+'.json')
 if preserved.exists():raise ValueError('恢复锁已有记录，需检查而非覆盖')
 lock.rename(preserved)
 proof=lock.with_name('SCED_RECOVERY_UNLOCK_'+attempt.name+'.json')
 with proof.open('x')as f:json.dump(dict(original_lock_path=str(preserved),original_lock_sha256=sha(preserved),failed_exit_sha256=sha(attempt/'EXIT.json'),SCED_recovery_revision_sha256=sha(revision),SCED_acceptance_sha256=sha(root/'da_sced/ACCEPTANCE.json'),PRICE_preparation_sha256=sha(root/'da_price/PREPARED.json'),native_calls=0,unknown_outcome_ignored=False),f)
 return proof
def launch(core,run,action,windows,settlement,proof,confirmed=False,scenario=None):
 raise ValueError('旧固定日前组合入口已停用，仅保留历史只读；请使用日前及统一实时入口。')

def execution_boundary(root,report_path=None):
 from datetime import datetime,timedelta
 root=Path(root);rt=root/'rt'
 candidates=[rt/'RUN_RESULT.json'] if (rt/'RUN_RESULT.json').exists() else sorted(rt.glob('PARTIAL_W*.json'),key=lambda p:int(p.stem.split('W')[-1]),reverse=True)
 if report_path is not None:candidates=[Path(report_path)]
 if not candidates:return dict(verified=False,executed=0,issued=None,receipt_at=None)
 p=candidates[0];outer=read(p);rep=outer['report'];source=read(rt/'SOURCE_CONTRACT.json');identity=read(rt/'SOURCE_IDENTITY.json');art=read(root/'ARTIFACTS.json')
 if outer['DA_source_identity']!=art['source_identity'] or outer['RT_source_identity_sha256']!=identity['sha256'] or rep['branch_id']!=read(root/'RUN.json')['run_id']+'-RT':raise ValueError('execution receipt source mismatch')
 n=rep['accepted_RT_windows'];executed=rep['executed_RT_cells'];issued=rep['issued_RT_cells']
 if type(n)is not int or not 1<=n<=96 or issued!=3*n or not 0<=executed<=issued:raise ValueError('execution counts')
 if rep['same_controller_full96_complete']:
  if n!=96 or executed!=288 or read(rt/'D96_DRAIN.json')['report']!=rep:raise ValueError('full execution drain missing')
 else:
  folder=rt/f'W{n-1}';complete=read(folder/'accepted/COMPLETE.json');fp=folder/'accepted/STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json'
  if complete['DA_receipt_sha256']!=sha(root/'ARTIFACTS.json') or complete['files']['accepted/STATE_FACTS_NOT_RESTORABLE_CAPABILITY.json']!=sha(fp):raise ValueError('partial receipt hash')
  facts=read(fp)
  if facts['report']!=rep or facts['receipt']['at']!=rep['receipt_at']:raise ValueError('partial receipt mismatch')
 if datetime.fromisoformat(rep['receipt_at'])!=datetime.fromisoformat(source['day_start'])+timedelta(minutes=5*executed):raise ValueError('execution clock/count mismatch')
 return dict(verified=True,executed=executed,issued=issued,receipt_at=rep['receipt_at'],report_sha256=sha(p))
def result_tables(run):
 """Only saved accepted first-three cells; no lookahead or inferred missing prices."""
 from gd_live_results import supported,load
 if supported(run):return load(run)
 root=Path(run).resolve();units=[];storage=[];vre=[];prices=[];unexecuted=[]
 boundary=execution_boundary(root)
 run_id=read(root/'RUN.json')['run_id']+'-RT'
 contract_sha=sha(root/'rt/SOURCE_CONTRACT.json') if(root/'rt/SOURCE_CONTRACT.json').exists() else None
 for w in range(96):
  a=root/f'rt/W{w}/accepted/ACCEPTANCE.json';c=root/f'rt/W{w}/accepted/issued_commands.json'
  if not a.exists()or not c.exists():continue
  rec=read(a)
  complete=read(root/f'rt/W{w}/accepted/COMPLETE.json')
  if complete['files']['accepted/ACCEPTANCE.json']!=sha(a) or complete['files']['accepted/issued_commands.json']!=sha(c):raise ValueError('accepted issued hash mismatch')
  if rec.get('source_contract_sha256')!=contract_sha:raise ValueError('RT source contract changed')
  if rec.get('window')!=w or rec.get('KKT',{}).get('passed')is not True:raise ValueError('unaccepted RT data')
  for k,z in enumerate(read(c)):
   if z['global_cell_index']!=3*w+k or z['accepted_event']!=rec['event_id']:raise ValueError('issued source mismatch')
   cell=z['cell']
   if 3*w+k>=boundary['executed']:
    unexecuted.append(dict(global_cell_index=3*w+k,start=cell['start'],end=cell['end'],kind='issued_not_verified_executed',cell=cell));continue
   if cell['end']>boundary['receipt_at']:raise ValueError('cell beyond execution receipt')
   for g,v in cell['thermal'].items():units.append(dict(start=cell['start'],end=cell['end'],resource=g,P_mw=v['mean_mw'],kind='simulated_5min_execution'))
   for g,v in cell['batteries'].items():storage.append(dict(start=cell['start'],resource=g,charge_mw=v['charge_mw'],discharge_mw=v['discharge_mw'],SOC_MWh=v['energy_end_mwh']))
   for g,v in cell.get('renewable',{}).items():vre.append(dict(start=cell['start'],resource='VRE:'+g,**{key:v[key]for key in ('availability_mw','dispatched_mw','curtailed_mw')}))
  pp=root/f'rt_price/W{w}/PRICE_RECEIPT.json'
  if pp.exists():
   pr=read(pp)
   if pr.get('run_id')!=run_id or pr.get('source_contract_sha256')!=contract_sha:raise ValueError('PRICE run/source mismatch')
   rp=root/f'rt_price/W{w}/native/response.json'
   if not rp.exists() or sha(rp)!=pr['response_sha256']:raise ValueError('PRICE raw response changed')
   if pr.get('KKT',{}).get('passed')is not True or pr.get('RT_dual_used')is not False:raise ValueError('independent PRICE receipt required')
   for bus,value in pr['raw_prices'].items():prices.append(dict(window=w,node=bus,raw_research_LMP=value,formal_price=None))
 settlement=read(root/'settlement/RESULT.json')if boundary['executed']==288 and (root/'settlement/RESULT.json').exists()else None
 return dict(execution_receipt=boundary,issued_not_verified_executed=unexecuted,executed_5min=units,storage_5min=storage,VRE_5min=vre,independent_PRICE_15min=prices,settlement_hour_rows=settlement['rows']if settlement else [],formal_bill=None)
