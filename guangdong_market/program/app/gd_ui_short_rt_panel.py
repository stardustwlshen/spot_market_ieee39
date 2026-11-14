from pathlib import Path
import streamlit as st
import gd_ui_short_rt_backend as api

def render(parent_run=""):
 st.subheader('短时域实时组合（单窗，独立研究）')
 st.warning('近/远各24个五分钟的联合4小时研究解释，非已认证官方联接；统一启停曲线，当前仅stable边界。未接通active/非零新通知与末日扩展时明确拒绝，不把新SOC重置到日前。无AGC/AC正式放行。')
 st.caption('准备、真实HiGHS、固定新整数SCED和下一边界为本入口同一链。旧48小时研究入口和旧PRICE2.2不能与此处票据混用。')
 default=Path(__file__).resolve().parents[1]/'engines/guangdong_short_rt_v1/guangdong_rt_short_user_cli_v1_20261112/program.py'
 entry=st.text_input('通用实时程序',str(default),key='srt_entry')
 with st.expander('从所选已接受日前结果准备新实时研究模型（不求解）'):
  parent=st.text_input('来源日前运行',parent_run,key='srt_da');new=st.text_input('全新实时输出目录',key='srt_new')
  seed_ok=st.checkbox('明确采用首15分钟已下发的反事实初态，允许纯构模',key='srt_seed_consent')
  if st.button('准备新实时模型（0 求解）',disabled=not seed_ok,key='srt_seed'):
   try:
    z=api.prepare(entry,parent,new,confirmed=True);st.session_state['srt_proof']=z;st.success('已准备首窗；下方填写其目录和窗口0后预检')
   except Exception as e:st.error(str(e))
 run=st.text_input('已准备的实时运行目录',key='srt_run');window=int(st.number_input('本次窗口编号（0–94；末日尚未接通）',min_value=0,max_value=94,value=0,step=1,key='srt_window'))
 if st.button('构建此窗口（仅纯构模）',key='srt_build'):
  try:st.session_state['srt_proof']=api.build_next(entry,run,window);st.success('模型已准备，尚未求解')
  except Exception as e:st.session_state.pop('srt_proof',None);st.error(str(e))
 if st.button('预检/读取实时状态（不求解）',key='srt_preflight'):
  try:
   proof=api.preflight(entry,run,window);st.session_state['srt_proof']=proof
   st.info('已接受，重复点击不会重算' if proof['core']['status']=='accepted' else '预检通过，可执行未完成阶段')
   with st.expander('阶段状态与待执行项'):st.json(proof['core'])
  except Exception as e:st.session_state.pop('srt_proof',None);st.error(str(e))
 consent=st.checkbox('明确允许此窗口研究求解（最多300+120秒，HiGHS gap 0.1%），不是调度指令',key='srt_consent')
 if st.button('启动/接续此窗口',disabled=not consent or 'srt_proof'not in st.session_state,key='srt_launch'):
  try:
   p=st.session_state['srt_proof']
   if (p['entry'],p['run'],p['window'])!=(str(Path(entry).resolve()),str(Path(run).resolve()),window):raise ValueError('输入变化，请重新预检')
   z=api.launch(p,confirmed=True);st.session_state['srt_launch_result']=z;st.session_state.pop('srt_proof',None);st.success(z['status'])
  except Exception as e:st.error(str(e))
 if 'srt_launch_result'in st.session_state:
  with st.expander('本次后台记录'):st.json(st.session_state['srt_launch_result'])
 st.caption('上述秒数是各原生求解阶段预算，构模和完整校验另计。刷新或重新预检查看完成结果；不靠PID存在推断成功。状态未知时禁止重试，本页没有强杀或安全取消按钮。')
 simulation=st.checkbox('明确仅生成无偏差反事实研究执行回执，不代表计量/AGC/AC放行',key='srt_simulate')
 if st.button('严格接受后生成下一窗口研究边界',disabled=not simulation,key='srt_handoff'):
  try:st.json(api.handoff(entry,run,window,confirmed=True));st.success('研究边界已保存，安全放行仍为否')
  except Exception as e:st.error(str(e))
