from pathlib import Path
import gd_batch_control as ctl
def render(da,run):
 import streamlit as st
 with st.expander('批次运行：一次授权4窗/96窗，可恢复研究编排',expanded=False):
  st.caption('先准备一个新研究运行；此编排只调用同一已安装v6引擎。默认0求解，现只验证Linux文件锁。当前AGC安全未放行不因此改变。')
  windows=st.selectbox('批次窗口范围',[4,96],key='batch_windows');budget=int(st.number_input('本次后台墙钟预算（秒）',min_value=60,max_value=172800,value=7200,step=60,key='batch_budget'));name=st.text_input('批次名（同一目录不可重复创建）','batch_first_hour',key='batch_name')
  st.write(f'范围 W0–W{windows-1}；完全新算最多 {windows*3} 次底层原生求解，已有严格回执复用。原生时限合计 {windows*480} 秒，不是预计完成时间；预算到达只阻止后续阶段，不强杀当前求解。')
  policy=st.selectbox('本批PRICE固定profile',list(ctl.engine.POLICIES),key='batch_policy')
  st.caption('推荐profile首锚为研究解释；已有价格必须同profile，只读取原证据，不覆盖、不混用或失败回退。')
  key=(da,run,windows,budget,name,policy)
  if st.session_state.get('batch_plan_key')!=key:st.session_state['batch_plan_key']=key;st.session_state.pop('batch_reviewed_plan',None);st.session_state['batch_authorize']=False
  try:batch=ctl.location(run,name)
  except Exception as e:st.error(str(e));return
  st.caption('批次目录：'+str(batch))
  if st.button('生成并查看完整批次计划（0求解）',key='batch_plan'):
   try:st.session_state['batch_reviewed_plan']=ctl.plan(da,run,windows,budget,policy);st.session_state['batch_authorize']=False
   except Exception as e:st.error(str(e))
  p=st.session_state.get('batch_reviewed_plan')
  if p:st.json(p)
  consent=st.checkbox('我已阅读完整计划、参数和研究假设，一次授权本批所有求解与无偏差研究交接',key='batch_authorize')
  if st.button('一次授权并后台启动',disabled=not(p and consent),key='batch_start'):
   try:st.json(ctl.start(p,batch,confirmed=True))
   except Exception as e:st.error(str(e))
  if st.button('刷新批次进度（0求解）',key='batch_refresh'):
   try:st.json(ctl.status(batch))
   except Exception as e:st.error(str(e))
  if st.button('请求阶段间停止（不强杀当前求解）',key='batch_stop'):
   try:st.json(ctl.stop(batch))
   except Exception as e:st.error(str(e))
  cont=st.checkbox('明确继续原授权计划，并重开同等墙钟预算；失败/未知不重试',key='batch_continue_authorize')
  if st.button('继续原批次',disabled=not cont,key='batch_resume'):
   try:st.json(ctl.resume(batch,confirmed=True))
   except Exception as e:st.error(str(e))
  st.caption('页面关闭不停止后台。停止后须确认后台已到阶段边界才能继续；未知进程或失败结果保留并阻断，不能双击重新求解。')
