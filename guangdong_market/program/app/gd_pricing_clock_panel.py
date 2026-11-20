"""Explicit research input availability; no clock inferred from filesystem timestamps."""
from pathlib import Path
import json,uuid
DEFAULT_STORE=Path(__file__).resolve().parent.parent/'runs_clock_declarations'
STATEMENT='各轮基础模拟输入假设在原调度截止时刻已可用；绑定参考更晚可用时仅作相应时刻的事后研究读取。不是实测到达时间或正式发布时间。'
def create(statement,store=None):
 from gd_live_market_runtime.information_clock import research_policy
 if not statement.strip():raise ValueError('请填写研究假设说明')
 z=research_policy(statement);root=Path(store or DEFAULT_STORE);root.mkdir(parents=True,exist_ok=True)
 p=root/(uuid.uuid4().hex+'.json')
 with p.open('x',encoding='utf-8') as f:json.dump(z,f,ensure_ascii=False,indent=2)
 return str(p.resolve())
def render(identity):
 import streamlit as st
 if st.session_state.get('clock_source_identity')!=identity:
  st.session_state['clock_source_identity']=identity;st.session_state.pop('clock_path',None);st.session_state['clock_assumption']=False
 yes=st.checkbox('明确采用输入可用时间研究假设（不是实际到达记录）',value=False,key='clock_assumption')
 if not yes:
  st.session_state.pop('clock_path',None)
  st.caption('未声明输入时标：可浏览历史和查看计划；新价格求解会拒绝未知时标。')
  return None
 st.caption(STATEMENT)
 if not st.session_state.get('clock_path'):
  try:st.session_state['clock_path']=create(STATEMENT)
  except OSError as exc:
   st.error(f'无法保存输入时标声明：{DEFAULT_STORE}。原因：{exc}。日前结果仍保留，暂不可启动实时。请在可写的安装目录使用程序，或修复目录的写入权限后重试。')
   return None
 return st.session_state['clock_path']
