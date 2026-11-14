from pathlib import Path
import streamlit as st
import gd_ui_rt_execute as api

def render(parent_run=""):
 st.subheader('实时组合真实求解（单窗口）')
 st.warning('独立研究链，不是上方的日前固定组合入口；未通过调频/交流安全不得解释为正式调度。当前默认仍统一事件费用和 48 小时自由尾段，属于研究扩展，不能称为官方 24×5 分钟及近/远段实现；三态也是另一个研究分支。')
 default=Path(__file__).resolve().parents[1]/'engines/guangdong_rt_recommit_v32/program.py'
 entry=st.text_input('通用实时程序',str(default),key='rte_entry')
 with st.expander('从所选已接受日前结果准备新实时研究模型（不求解）'):
  parent=st.text_input('来源日前运行',parent_run,key='rte_da');new=st.text_input('全新实时输出目录',key='rte_new')
  seed_ok=st.checkbox('明确采用首15分钟已下发的反事实初态，允许纯构模',key='rte_seed_consent')
  if st.button('准备新实时模型（0 求解）',disabled=not seed_ok,key='rte_seed'):
   try:
    z=api.prepare(entry,parent,new,confirmed=True);st.session_state['rte_proof']=z;st.success('已准备首窗；下方填写其目录和窗口0后预检')
   except Exception as e:st.error(str(e))
 run=st.text_input('已准备的实时运行目录',key='rte_run');window=int(st.number_input('本次窗口编号（0–95）',min_value=0,max_value=95,value=0,step=1,key='rte_window'))
 if st.button('构建此窗口（仅纯构模）',key='rte_build'):
  try:st.session_state['rte_proof']=api.build_next(entry,run,window);st.success('模型已准备，尚未求解')
  except Exception as e:st.session_state.pop('rte_proof',None);st.error(str(e))
 if st.button('预检/读取实时状态（不求解）',key='rte_preflight'):
  try:
   proof=api.preflight(entry,run,window);st.session_state['rte_proof']=proof
   st.info('已接受，重复点击不会重算' if proof['core']['status']=='accepted' else '预检通过，可执行未完成阶段')
   with st.expander('阶段状态与待执行项'):st.json(proof['core'])
  except Exception as e:st.session_state.pop('rte_proof',None);st.error(str(e))
 consent=st.checkbox('明确允许此窗口研究求解（最多60+300+120秒，HiGHS gap 0.1%），不是调度指令',key='rte_consent')
 if st.button('启动/接续此窗口',disabled=not consent or 'rte_proof'not in st.session_state,key='rte_launch'):
  try:
   p=st.session_state['rte_proof']
   if (p['entry'],p['run'],p['window'])!=(str(Path(entry).resolve()),str(Path(run).resolve()),window):raise ValueError('输入变化，请重新预检')
   z=api.launch(p,confirmed=True);st.session_state['rte_launch_result']=z;st.session_state.pop('rte_proof',None);st.success(z['status'])
  except Exception as e:st.error(str(e))
 if 'rte_launch_result'in st.session_state:
  with st.expander('本次后台记录'):st.json(st.session_state['rte_launch_result'])
 st.caption('上述秒数是各原生求解阶段预算，构模和完整校验另计。刷新或重新预检查看完成结果；不靠PID存在推断成功。状态未知时禁止重试，本页没有强杀或安全取消按钮。')
 simulation=st.checkbox('明确仅生成无偏差反事实研究执行回执，不代表计量/AGC/AC放行',key='rte_simulate')
 if st.button('严格接受后生成下一窗口研究边界',disabled=not simulation,key='rte_handoff'):
  try:st.json(api.handoff(entry,run,window,confirmed=True));st.success('研究边界已保存，安全放行仍为否')
  except Exception as e:st.error(str(e))
