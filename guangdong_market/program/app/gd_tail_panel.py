def render(da):
 import streamlit as st
 import gd_tail_control as ctl
 with st.expander('独立：所选已验DA最后48格研究边界（非默认50MW实时链）'):
  st.warning('仅使用该DA完整已有的48格及原AGC/曲线。初态按原DA无偏差推导，不是实测；过去终值须已满足，不捏造未来终值。不代表正式DA98、实际调度或安全通过。')
  name=st.text_input('独立末48格新会话名','tail48_research',key='tail_name');title=st.text_input('独立研究声明名称',key='tail_declaration');identity=(da,name,title)
  if st.session_state.get('tail_identity')!=identity:
   st.session_state['tail_identity']=identity;st.session_state.pop('tail_plan',None);st.session_state.pop('tail_status',None);st.session_state['tail_confirm']=False
  if st.button('核验所选DA来源并查看3调用计划（0求解）',key='tail_plan_button',disabled=not title.strip()):
   st.session_state['tail_confirm']=False;st.session_state.pop('tail_plan',None)
   try:st.session_state['tail_plan']=ctl.plan(da,name,title)
   except Exception as e:st.error(str(e))
  p=st.session_state.get('tail_plan')
  if p:st.json({k:v for k,v in p.items()if k!='source_pins'})
  yes=st.checkbox('独立确认以上已验来源/原停机曲线研究许可/无偏差边界；授权最多1SCUC+1simplexSCED+1完整研究profile PRICE，无自动回退、无实际调度授权',key='tail_confirm')
  if st.button('单次启动独立末48格研究（最多3调用）',key='tail_start',disabled=not(p and yes)):
   try:st.session_state['tail_status']=ctl.start(p,confirmed=True);st.session_state.pop('tail_plan',None)
   except Exception as e:st.error(str(e))
  if st.button('读取独立末48格结果（0求解）',key='tail_read'):
   try:st.session_state['tail_status']=ctl.status(ctl.location(da,name))
   except Exception as e:st.error(str(e))
  if 'tail_status'in st.session_state:st.json(st.session_state['tail_status'])
  st.caption('PRICE是末48格中最后24格的前瞻研究价格，不冒充默认当前季度/实际执行核算。失败、未知、重复名称不重启；窗口关闭不停止后台。未运行AC，无安全发布。')
