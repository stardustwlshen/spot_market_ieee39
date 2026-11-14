import streamlit as st
from pathlib import Path
import gd_da_only as da

def render(scenario):
 with st.expander('独立准备/求解日前（不运行旧RT）',expanded=True):
  st.caption('必须选择上方已另存场景。原DA数学SCUC→固定整数SCED→独立DA PRICE→来源审核；不是正式DA98发布。实际求解参数以本次生成计划为准。')
  run=st.text_input('独立日前新目录',str(Path(__file__).resolve().parents[1]/'runs/selected_DA'),key='da_only_run')
  identity=(scenario,run)
  if st.session_state.get('da_only_identity')!=identity:
   for k in ['da_only_plan','da_only_status','da_only_confirm']:st.session_state.pop(k,None)
   st.session_state['da_only_identity']=identity
  if st.button('日前来源/预算预检（0求解）',key='da_only_plan_button'):
   try:st.session_state['da_only_plan']=da.plan(scenario,run)
   except Exception as e:st.error(str(e))
  p=st.session_state.get('da_only_plan')
  if p:
   st.caption('本次计划：MIP gap '+format(p['mip_gap'],'.2%')+'；各阶段时限 '+str(p['seconds'])+' 秒；线程 '+str(p['threads']))
   with st.expander('计划明细'):st.json({k:v for k,v in p.items()if k not in ('source_pins','scenario')})
  if st.button('准备日前SCUC模型（0求解）',disabled=p is None,key='da_only_prepare'):
   try:st.session_state['da_only_status']=da.prepare(p)
   except Exception as e:st.error(str(e))
  yes=st.checkbox('明确执行该场景日前最多3次原生求解；不执行RT，不作正式发布',key='da_only_confirm')
  if st.button('独立日前求解一次',disabled=not yes,key='da_only_start'):
   try:st.session_state['da_only_status']=da.start(run,confirmed=True)
   except Exception as e:st.error(str(e))
  if st.button('读取独立日前状态（0求解）',key='da_only_read'):
   try:st.session_state['da_only_status']=da.status(run)
   except Exception as e:st.error(str(e))
  status=st.session_state.get('da_only_status')
  if status:
   st.json(status)
   from gd_da98_panel import render as render_candidate
   render_candidate(status)
   if status.get('accepted')and st.button('选此已接受DA进入下方统一实时入口',key='da_only_use'):
    from gd_da_binding import selection
    st.session_state['gd_selected_da']=selection(status)
    st.session_state['u_link_parent']=True;st.session_state['u_da']=status['run'];st.success('已绑定该日前结果。')
  st.caption('失败/未知保留阶段日志和原结果，不自动重启/回退；新目录不能用来掩盖尚未知的旧求解。')
