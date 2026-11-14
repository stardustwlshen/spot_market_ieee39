"""Data-only research AGC display, never acceptance, recovery or dispatch authority."""
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def receipt_digest(value):
 # Same finite JSON payload encoding as phase_runtime.digest. This checks
 # internal consistency only, not the authority to mint an acceptance.
 def pack(v):
  if isinstance(v,dict):return {'map':sorted([[pack(k),pack(x)]for k,x in v.items()],key=lambda z:repr(z[0]))}
  if isinstance(v,(list,tuple)):return [pack(x)for x in v]
  return v
 return hashlib.sha256(json.dumps(pack(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def checked_row(export,accepted,*,branch,window,available_at):
 receipt=export['receipt'];need(export['schema']=='gd.agc.accepted_research_display.v1','durable research receipt schema only')
 need(export['branch_id']==branch==receipt['branch_id'],'receipt belongs to another branch')
 need(export['formal']is False and export['publication_allowed']is False and export['live_restore_allowed']is False,'display cannot grant authority')
 hour=export['hour_index'];need(type(hour)is int and 1<=hour<=24 and window==4*hour-2,'wrong hour/window association')
 digest=receipt_digest(receipt);need(export['receipt_digest']==digest,'receipt payload changed')
 proof=accepted['post_SCUC_phase'];need(proof['receipt']==digest and proof==export['phase_proof'],'receipt is not this accepted phase')
 need(accepted.get('status')=='accepted_research_pair'and accepted.get('safety_release')is False,'not accepted research pair')
 need(receipt['SCUC_source']==accepted['SCUC_response_sha256'],'receipt SCUC source differs')
 need(receipt['source_kind']=='native_internal_stage','not a planning receipt')
 allocator=receipt['allocator'];need(allocator['formal_publication_allowed']is False and allocator['dispatch_authorized']is False,'no formal publication or dispatch')
 start=datetime.fromisoformat(receipt['hour_start']);cut=start-timedelta(minutes=30)
 need(receipt['published_at']==cut.isoformat() and receipt['internal_completed_at']==cut.isoformat(),'unexpected internal data clock')
 need(datetime.fromisoformat(available_at)==cut+timedelta(minutes=15),'accepted source availability mismatch')
 return dict(hour=hour,hour_start=receipt['hour_start'],awards_MW=allocator['awards_mw'],research_price=allocator['price'],price_source=allocator.get('price_source'),data_as_of=cut.isoformat(),accepted_available_at=available_at,source_receipt_digest=digest,kind='accepted_internal_research_receipt_NOT_market_publication',planning=False,receipt=export)

def scan(output):
 """Called after ordinary ctl.status source gates. On its own, only checks
 data associations. Does not revalidate raw mathematics or trust old code pins.
 """
 out=Path(output).resolve();plan=read(out/'PLAN.json');need(plan['output']==str(out),'output binding changed')
 result=read(out/'pipeline/dispatch/RESULT.json');branch=result['branch_id'];rows=[];pending=[];pins={str(out/'PLAN.json'):sha(out/'PLAN.json'),str(out/'pipeline/dispatch/RESULT.json'):sha(out/'pipeline/dispatch/RESULT.json')}
 completed={r['window']:r for r in result['results']};origin=datetime.fromisoformat(read(Path(plan['run'])/'seed/request.json')['origin'])
 for f in sorted((out/'pipeline/dispatch').glob('W*/H*_RESEARCH_AGC_RECEIPT.json')):
  w=int(f.parent.name[1:]);a=f.parent/'actual/ACCEPTED_PAIR.json'
  if w not in completed or not a.exists():pending.append(dict(window=w,path=str(f),status='not_yet_accepted_and_handed_off'));continue
  need(completed[w]['branch_id']==branch,'window belongs to another branch')
  available=(origin+timedelta(minutes=15*(w+1))).isoformat();row=checked_row(read(f),read(a),branch=branch,window=w,available_at=available);
  selection=read(f).get('selection')
  if selection:
   need(selection.get('handoff_complete') is True,'selection missing handoff')
   handoff=completed[w]['handoff'];need(selection['carry_path']==handoff['carry_path'] and selection['carry_sha256']==handoff['carry_sha256'],'selected receipt differs from completed handoff')
   row['consumption_status']='selected_after_handoff_for_subsequent_research_NOT_actual_consumption'
  else:row['consumption_status']='legacy_export_without_new_commit_proof'
  rows.append(row)
  for p in (f,a):pins[str(p)]=sha(p)
 candidates=[]
 for f in sorted((out/'pipeline/dispatch').glob('W*/H*_AGC_MATH_CANDIDATE.json')):
  z=read(f);w=int(f.parent.name[1:]);need(z.get('schema')=='gd.agc.math_candidate.v1' and z.get('branch_id')==branch and z.get('consumable') is False,'invalid candidate evidence')
  selected=any(r['hour']==z['hour_index'] and r.get('consumption_status','').startswith('selected_after_handoff') for r in rows)
  candidates.append(dict(window=w,hour=z['hour_index'],path=str(f),status='selected_after_handoff' if selected else 'math_candidate_NOT_committed_NOT_consumable',safety_release=False));pins[str(f)]=sha(f)
 need(len({r['hour']for r in rows})==len(rows),'duplicate hour receipt')
 return dict(schema='gd.agc.readonly.results.v1',branch_id=branch,rows=rows,candidates=candidates,pending=pending,missing_hours=[i for i in range(1,25)if i not in {r['hour']for r in rows}],source_pins=pins,math_revalidated_here=False,publication_allowed=False,live_restore_allowed=False,native_calls=0)

def load_snapshot(root,expected_index_sha):
 """Explicitly registered frozen display snapshot, independent of current
 executable versions. Hashes provide content integrity, not live authority.
 """
 root=Path(root).resolve();need(sha(root/'INDEX.json')==expected_index_sha,'registered AGC snapshot index changed');index=read(root/'INDEX.json');need(index['schema']=='gd.agc.frozen.display.v1','wrong snapshot schema')
 tables={}
 for name,h in index['files'].items():
  p=(root/name).resolve();need(p.is_relative_to(root)and sha(p)==h,'AGC display content changed');tables[name]=read(p)
 need(index['publication_allowed']is False and index['live_restore_allowed']is False,'snapshot cannot grant authority')
 return index,tables

def render(data):
 import streamlit as st
 st.caption('已接受内部研究调频结果，不是规划预留或实际市场发布。数据时点与接受后可用时点分别显示；缺小时不补造。此读取不重验原始数学，不恢复live权限。')
 if data.get('candidates'):
  st.caption('数学候选单独留证；未提交候选不进入后续调频消费。已选研究回执也不是实际执行或安全放行。');st.dataframe([{'窗口':r['window'],'小时':r['hour'],'状态':'已完成交接并选定研究回执' if r['status']=='selected_after_handoff' else '数学候选：未提交、不可消费','安全放行':'否'} for r in data['candidates']],hide_index=True)
  with st.expander('技术详情：候选记录与来源',expanded=False):st.json(data['candidates'])
 states={'selected_after_handoff_for_subsequent_research_NOT_actual_consumption':'已选定供后续研究使用，不代表实际执行','legacy_export_without_new_commit_proof':'旧版本回执，未提供新提交证明'}
 summary=[{'小时':r['hour'],'开始时间':r['hour_start'],'分配容量（MW）':r['awards_MW'],'研究价格':r['research_price'],'数据时点':r['data_as_of'],'接受后可用时点':r['accepted_available_at'],'消费状态':states.get(r.get('consumption_status'),'旧版本记录，未提供新提交状态')} for r in data['rows']]
 st.dataframe(summary,hide_index=True)
 for row in data['rows']:
  with st.expander('H'+str(row['hour'])+' 完整研究声明/分配/价格/来源'):
   st.json(row['receipt'])
 if data.get('pending'):
  st.info('部分回执尚未完成接受和状态衔接，不列为已接受结果')
  with st.expander('技术详情：待处理回执',expanded=False):st.json(data['pending'])

def registered_history(output):
 """Only release-registered content may bypass old executable-version checks,
 and only for explicit display. Existing live status/action gates stay intact.
 """
 import gd_live_control as ctl
 ctl.sources() # verifies current viewer and registration, NOT old result math
 app=Path(__file__).resolve().parent;file=app/'AGC_HISTORY_REGISTRATION.json'
 need(file.exists(),'this historical output has no registered read-only snapshot')
 registrations=read(file);key=str(Path(output).resolve());need(key in registrations,'output not registered as fixed historical display')
 reg=registrations[key];rel=Path(reg['bundle']);need(not rel.is_absolute(),'history snapshot must be app-relative');root=(app/rel).resolve();need(root.is_relative_to(app.parent),'history snapshot escapes app')
 index,tables=load_snapshot(root,reg['index_sha256']);need(index['original_output']==key,'history output association changed');status=tables['STATUS.json']
 need(status['agc_results']['branch_id']==index['branch_id'],'snapshot branch changed')
 status.update(verification_scope='registered fixed data snapshot integrity only; old executable/math not revalidated, no live status/recovery or action authority',status='registered_readonly_history',publication_allowed=False,dispatch_authorized=False,durable_restart_supported=False)
 return status
