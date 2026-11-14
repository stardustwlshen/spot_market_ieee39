import streamlit as st
from pathlib import Path
import gd_ui_three_state_backend as api

def render(parent_run='',engine=None):
 st.subheader('实时模型独立研究选择')
 model=st.radio('研究模型',['统一事件（保留当前入口）','三态首窗研究（独立分支）'],key='tsui_model')
 if model.startswith('统一'):
  st.caption('继续使用原统一事件入口；这里不会替换当前运行、旧票据或已选场景。');return
 st.warning('当前三态仍是48小时研究扩展，并非正式实时短时域，正在修正。仅支持首窗至W1边界；原400 MW双备用不变，无AGC/AC安全放行，不是正式调度。')
 st.caption('热/温/冷参数必须明确指定；退化研究沿用相同原曲线/费用/通知，不是实际设备三态数据。初始3格仅假设已下发，不是实测。')
 engine=engine or Path(__file__).resolve().parents[1]/'engines/guangdong_three_state_v2'
 eng=st.text_input('三态安装目录',str(engine),key='tsui_engine');parent=st.text_input('三态来源日前运行',parent_run,key='tsui_parent');out=st.text_input('全新三态纯准备目录',key='tsui_output')
 choice=st.selectbox('三态参数来源',['请选择','用户参数JSON','明确退化研究假设'],key='tsui_parameters_mode');params=st.text_input('三态参数JSON（选择用户文件时必填）',key='tsui_parameters')
 confirmed=st.checkbox('确认建立独立研究分支及所选参数来源；纯构模不会求解',key='tsui_prepare_confirm')
 if st.button('三态纯seed / build / preflight（0求解）',disabled=not confirmed or choice=='请选择'):
  try:
   z=api.prepare(eng,parent,out,'file'if choice=='用户参数JSON'else'degenerate',params,True);st.session_state['tsui_result']=z
  except Exception as e:st.error(str(e))
 if 'tsui_result'in st.session_state:
  st.success(st.session_state['tsui_result']['status']);st.caption(st.session_state['tsui_result']['run'])
 run=st.text_input('已有三态run（检查或独立求解）',key='tsui_existing')
 if st.button('只读检查三态状态'):
  try:st.json(api.inspect(eng,run))
  except Exception as e:st.error(str(e))
 with st.expander('实际求解（独立确认，不自动启动）'):
  st.warning('最多一次SCUC 300秒＋首次SCED 120秒；未知结果锁定、同一UI票据不重复。没有安全取消，不强杀求解器。')
  native=st.checkbox('明确启动实际HiGHS研究求解，不是安全调度/付款',key='tsui_native_confirm')
  if st.button('启动三态首窗隔离求解',disabled=not native):
   try:st.json(api.launch(eng,run,native))
   except Exception as e:st.error(str(e))
