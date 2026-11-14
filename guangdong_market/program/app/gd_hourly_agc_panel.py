import streamlit as st
from pathlib import Path
import gd_hourly_agc_backend as api

def render(run):
 with st.expander('小时调频竞价与未来构模（独立研究）',expanded=False):
  st.error('安全未放行。小时竞价回执不代表已调度、已被旧SCED消费或可正式发布。')
  st.caption('仅固定安装v6、已接受同源运行。50MW与五厂资格/报价/性能沿用明确合成研究输入；不开放AGC0、虚构储能/风光资格或改400MW。目前不支持修改报价；变更必须新竞价、SCUC、SCED及安全检查，不能读取旧结果代替。')
  st.caption('当前运行：'+str(run))
  identity=str(run)
  if st.session_state.get('ha_run')!=identity:
   for k in ('ha_receipt','ha_build','ha_error','ha_confirm','ha_build_confirm','ha_index','ha_output'):st.session_state.pop(k,None)
   st.session_state['ha_run']=identity
  source=int(st.number_input('已接受源窗口',0,95,1,key='ha_source'));hour=int(st.number_input('目标小时',0,23,1,key='ha_hour'))
  base=Path(run).expanduser().parent if str(run).strip()else Path.cwd()
  index=st.text_input('新小时回执目录',str(base/'agc_H1_new'),key='ha_index')
  request=(identity,source,hour,index)
  if st.session_state.get('ha_request')!=request:
   for k in ('ha_receipt','ha_build','ha_error'):st.session_state.pop(k,None)
   st.session_state['ha_request']=request
  confirmed=st.checkbox('确认合成发布时间、研究报价/资格/性能；仅0求解生成回执',value=False,key='ha_confirm')
  if st.button('生成同源小时竞价回执（0求解）',disabled=not confirmed,key='ha_register'):
   for k in ('ha_receipt','ha_build','ha_error'):st.session_state.pop(k,None)
   try:st.session_state['ha_receipt']=api.register(run,source,hour,index,confirmed=True)
   except Exception as e:st.session_state['ha_error']=str(e)
  if 'ha_error'in st.session_state:st.error(st.session_state['ha_error'])
  receipt=st.session_state.get('ha_receipt')
  if receipt:
   st.success(receipt['status']);st.json(receipt)
  window=int(st.number_input('未来消费者窗口',0,95,2,key='ha_window'));out=st.text_input('新独立构模目录',str(base/'agc_W2_new'),key='ha_output')
  build_identity=(request,window,out)
  if st.session_state.get('ha_build_identity')!=build_identity:
   st.session_state.pop('ha_build',None);st.session_state['ha_build_identity']=build_identity
  bc=st.checkbox('确认只构建新的freeSCUC输入，不求解、不覆盖原运行',value=False,key='ha_build_confirm')
  if st.button('独立构模（0求解）',disabled=not(bc and receipt),key='ha_build_button'):
   st.session_state.pop('ha_build',None);st.session_state.pop('ha_error',None)
   try:st.session_state['ha_build']=api.build(run,window,index,out,confirmed=True)
   except Exception as e:st.session_state.pop('ha_receipt',None);st.session_state['ha_error']=str(e);st.rerun()
  if 'ha_build'in st.session_state:st.warning('尚未求解。不能使用原SCED/价格作为这次构模结果。');st.json(st.session_state['ha_build'])
