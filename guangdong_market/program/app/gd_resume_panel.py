"""Reviewed normal resume controls; no hidden solve or implicit scope extension."""
def render(run,name):
 import streamlit as st
 import gd_live_control as live
 import gd_resume_control as ctl
 with st.expander('中断后接续：保留已完成求解，建立新执行代次',expanded=False):
  st.caption('当前入口仅恢复同场景、同模式的既有计划，不自动从首小时扩展到全天。旧失败、旧原生结果及旧执行代次保持不变；结果未知的原生启动不能重试。')
  source=st.text_input('原运行或上一接续代次目录',str(live.location(run,name)),key='resume_source')
  render_saved_attempts(source)
  ended=st.checkbox('我已确认旧计算进程结束；不是仅因日志暂未更新',key='resume_prior_ended')
  use_target=st.checkbox('采用当前已核对同模式计划的新安装上下文（不改变原计划）',key='resume_new_context')
  if st.button('核对可复用结果和剩余预算（不求解）',key='resume_review'):
   st.session_state.pop('resume_reviewed',None);st.session_state['resume_authorized']=False
   try:st.session_state['resume_reviewed']=ctl.plan(source,prior_process_ended=ended,target_plan=st.session_state.get('live_plan')if use_target else None)
   except Exception as exc:st.error(str(exc))
  target=st.session_state.get('live_plan')
  if target and target.get('windows')==96 and target.get('finite_hourly'):
   with st.expander('独立研究扩域：复用已完成首小时，继续全天',expanded=False):
    st.caption('不是原计划原样恢复。必须采用新的全天日历和D1研究来源；前4窗/4价格完整数学等价及新H1交接提交通过后，才执行剩余277次预算。')
    comparison=st.text_input('独立模型比较清单路径',key='resume_extension_comparison')
    if st.button('核对首小时至全天的新扩域计划（不求解）',key='resume_extension_review',disabled=not comparison):
     st.session_state.pop('resume_reviewed',None);st.session_state['resume_authorized']=False
     try:st.session_state['resume_reviewed']=ctl.extension_plan(source,target,comparison,prior_process_ended=ended)
     except Exception as exc:st.error(str(exc))
  p=st.session_state.get('resume_reviewed')
  if p:
   st.write('原生已完成：'+str(p['completed_native'])+'次；本次最多新增：'+str(p['remaining_native'])+'次；交流潮流预算：0次')
   st.write('新执行代次：'+p['execution_attempt_id'])
   if p['errors']:st.error('当前不能接续');st.dataframe([{'步骤':r['stage'],'原因':r['error']}for r in p['errors']],hide_index=True)
   with st.expander('技术详情：来源、阶段及预算清单',expanded=False):st.json(p)
   yes=st.checkbox('授权此新执行代次仅执行上述缺失步骤和剩余求解预算；不作安全发布',key='resume_authorized')
   if st.button('启动已核对接续',disabled=not(p['can_start'] and yes and p['source_output']==source),key='resume_start'):
    try:st.session_state['resume_started']=ctl.start(p,confirmed=True);st.session_state.pop('resume_reviewed',None);st.success('新接续代次已启动；没有重启旧进程。')
    except Exception as exc:st.error(str(exc))
  started=st.session_state.get('resume_started')
  if started and st.button('读取接续进度（不求解）',key='resume_status'):
   try:
    z=ctl.status(started['output']);st.write('执行代次：'+z['execution_attempt_id']);st.write('阶段：'+z['checkpoint'].get('stage','尚未形成检查点'))
    from gd_resume_progress import render as show_progress
    show_progress(z)
    from gd_price_quality import render as show_quality
    for q in z.get('quarters',[]):
     st.write('15分钟价格时段 '+str(q['quarter']));st.dataframe([{'节点':n,'研究价格（元/MWh）':v}for n,v in q['raw_prices'].items()],hide_index=True);show_quality(q.get('price_quality'))
    if z.get('hours'):st.write('已形成研究核算小时数：'+str(len(z['hours'])))
    with st.expander('技术详情：检查点与退出记录',expanded=False):st.json(z)
   except Exception as exc:st.error(str(exc))

def saved_attempts(source):
 """Read only children of the explicitly selected source; no global discovery."""
 from pathlib import Path
 import json,hashlib
 source=Path(source).resolve();plan=source/'PLAN.json'
 if not plan.is_file():raise ValueError('所选原运行缺少计划')
 json.loads(plan.read_bytes());expected=hashlib.sha256(plan.read_bytes()).hexdigest()
 root=source/'resume_attempts';valid=[];errors=[]
 if not root.exists():return valid,errors
 if root.is_symlink():raise ValueError('拒绝外部代次目录链接')
 for child in sorted(root.iterdir(),key=lambda p:p.name):
  try:
   if child.is_symlink() or not child.is_dir():raise ValueError('不是独立代次目录')
   f=child/'RESUME_PLAN.json'
   if f.is_symlink():raise ValueError('计划不能为外部链接')
   p=json.loads(f.read_bytes())
   if p.get('schema')!='gd.resume.review.v1' or p.get('execution_attempt_id')!=child.name or Path(p.get('source_output','')).resolve()!=source or Path(p.get('output','')).resolve()!=child.resolve():raise ValueError('代次身份或所属运行不符')
   if p.get('source_pins',{}).get(str(plan))!=expected:raise ValueError('原运行计划绑定不符')
   if any(type(p.get(k))is not int or p[k]<0 for k in ('completed_native','remaining_native')):raise ValueError('求解预算缺失或无效')
   valid.append((child,p))
  except Exception as exc:errors.append(child.name+'：'+str(exc))
 return valid,errors

def render_saved_attempts(source):
 import streamlit as st
 import gd_resume_control as ctl
 with st.expander('查看已保存接续代次（只读）',expanded=False):
  st.caption('仅查看所选原运行。刷新页面后可重新选择；不会启动或重试求解，也不修改旧中断记录。')
  try:items,errors=saved_attempts(source)
  except Exception as exc:st.error(str(exc));return
  for error in errors:st.warning('拒绝读取：'+error)
  choices=['请选择已保存代次']+[p.name for p,_ in items]
  choice=st.selectbox('已保存执行代次',choices,key='resume_saved_choice_'+str(source))
  if choice==choices[0]:return
  path,p=next((d,p)for d,p in items if d.name==choice)
  cache_key='resume_saved_result_'+str(path)
  import hashlib,json
  plan_hash=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()
  if st.button('查看/刷新所选代次状态与结果（不求解）',key='resume_saved_read_'+str(path)):
   try:
    # Recheck identity on every click; no process/PID inference or resume action.
    current,_=saved_attempts(source)
    if not any(d==path and q==p for d,q in current):raise ValueError('所选代次计划已改变')
    z=ctl.status(path)
    if z['execution_attempt_id']!=choice or z['source_output']!=p['source_output']:raise ValueError('状态返回的代次身份不符')
    st.session_state[cache_key]={'plan_hash':plan_hash,'result':z}
   except Exception as exc:st.session_state.pop(cache_key,None);st.error(str(exc))

  cached=st.session_state.get(cache_key)
  if cached and cached['plan_hash']==plan_hash:
   z=cached['result']
   from gd_resume_progress import render as show_progress
   show_progress(z)
   ex=z.get('exit');cp=z.get('checkpoint')or{}
   state='已有检查点，运行状态待确认' if not ex else ('完成'if ex.get('status')=='explicit_resume_complete_research'else '已停止或阻断')
   st.write('执行代次：'+choice+'；状态：'+state)
   st.write('历史已完成：'+str(p['completed_native'])+'次；本代次新增预算上限：'+str(p['remaining_native'])+'次')
   used=cp.get('new_native_calls')
   if type(used)is int:st.write('本代次已记录新增：'+str(used)+'次；预算余量：'+str(max(0,p['remaining_native']-used))+'次（不代表已获重试许可）')
   else:st.caption('当前新增完成量未在检查点明确提供，不按零处理。')
   st.caption('仅研究结果，未作正式结算或安全发布；无退出记录不等于进程仍存活。')
   st.write('已保存价格15分钟价格时段：'+str(len(z.get('quarters',[])))+'；已形成研究核算小时：'+str(len(z.get('hours',[]))))
   with st.expander('技术详情：检查点与结果',expanded=False):st.json(z)
   st.caption('以下为上次明确读取的同代次快照；选择15分钟价格时段/小时不会启动或重试。需要新进度请点击刷新。')
   quarters=z.get('quarters',[])
   if quarters:
    qindex=st.selectbox('查看研究价格15分钟价格时段',list(range(len(quarters))),format_func=lambda i:'15分钟价格时段 '+str(quarters[i]['quarter']),key='resume_saved_quarter_'+str(path))
    q=quarters[qindex]
    st.dataframe([{'节点':node,'独立研究价格（元/MWh）':value}for node,value in q['raw_prices'].items()],hide_index=True)
    from gd_price_quality import render as render_quality
    render_quality(q.get('price_quality'))
   else:st.info('尚无已接受独立价格；缺价不补零。')
   hours=z.get('hours',[])
   if hours:
    index=st.selectbox('查看已完成研究小时',list(range(len(hours))),format_func=lambda i:'小时 '+str(hours[i].get('input_binding',{}).get('hour',i)),key='resume_saved_hour_'+str(path))
    hour=hours[index]
    if hour.get('formal_bill')is not None or hour.get('safety_release')is not False or hour.get('payable')is not False:
     st.error('所选小时缺少明确研究限制，拒绝作为研究核算展示。')
    else:
     from gd_settlement_panel import render_energy_hour
     render_energy_hour(hour)
   else:st.info('尚无完整小时核算；需完整执行量及四个15分钟时段独立价。')
