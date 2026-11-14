import streamlit as st
import gd_ui_agc_prepare_backend as api
from pathlib import Path

def render(parent_run=''):
 st.subheader('调频独立候选准备：真实程序入口')
 st.info('此入口只运行 context / example（可选）/ prepare，不求解、不运行 AC、不提交投标。实际 native/AC 后台执行尚未接通，禁止将准备成功当安全通过。')
 values={}
 engine=Path(__file__).resolve().parents[1]/'engines/guangdong_agc_v2'
 defaults={'cli':str(engine/'cli.py'),'deps':str(engine/'dependencies_v3'),'parent':parent_run}
 for key,label in [('cli','AGC 程序 cli.py'),('deps','冻结依赖目录'),('parent','已验收父 run'),('hour','目标小时（含 +08:00）'),('zone','明确 zone'),('asof','清算截止（含 +08:00，最迟 T−30）'),('output','全新独立输出目录')]:values[key]=st.text_input(label,value=defaults.get(key,''),key='agcp_'+key)
 choice=st.radio('研究报价来源',['指定 JSON','显式合成研究样例'],key='agcp_mode');values['mode']='provided'if choice=='指定 JSON'else'synthetic'
 values['thermal']=st.text_input('指定研究报价 JSON（样例模式可空）',key='agcp_thermal')
 synthetic_confirmed=st.checkbox('我明确选择合成研究报价/厂属/性能假设，不是正式投标或真实新资格',key='agcp_synthetic_confirm',disabled=values['mode']!='synthetic')
 if st.button('调频候选命令预检（0 求解）'):
  try:st.session_state['agcp_preflight']=api.preflight(**values);st.json(st.session_state['agcp_preflight'])
  except Exception as e:st.session_state.pop('agcp_preflight',None);st.error(str(e))
 if st.button('准备独立调频候选（0 求解 / 0 AC）',disabled='agcp_preflight'not in st.session_state):
  try:
   expected=st.session_state['agcp_preflight'];current=api.preflight(**values)
   if current!=expected:raise ValueError('界面输入已变化，请重新预检')
   result=api.prepare(expected,synthetic_confirmed=synthetic_confirmed);st.session_state['agcp_result']=result;st.session_state.pop('agcp_preflight',None);st.rerun()
  except Exception as e:st.error(str(e))
 if 'agcp_result'in st.session_state:
  r=st.session_state['agcp_result'];st.json(r);st.warning('候选状态：'+r['status']+'；仍需独立 SCED 和 AC 证据。')
  st.caption('以下是尚未执行的 native CLI 指令。只能在明确同意实际求解后，于新输出目录运行一次；未知结果先检查，不自动重试。')
  commands=r.get('CLI_instructions_not_executed',{'native':r['native_cli_not_executed']})
  for label,command in commands.items():st.caption(label);st.code(command,language='bash')
  st.caption('将 snapshot 路径放入下方只读“调频闭环快照 JSON”查看候选；最终 AC/安全核验结果也在该只读区读取。')
