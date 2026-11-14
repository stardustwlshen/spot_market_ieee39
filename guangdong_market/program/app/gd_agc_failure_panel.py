from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read_view():
 r=json.loads((B/'AGC_FAILURE.json').read_text());p=(B/r['bundle']).resolve();m=p/'MANIFEST.json'
 if sha(m)!=r['manifest_sha256']:raise ValueError('工作流证据清单改变')
 for n,h in json.loads(m.read_text())['files'].items():
  f=(p/n).resolve()
  if not f.is_relative_to(p)or sha(f)!=h:raise ValueError('工作流证据改变')
 v=json.loads((p/'ACTUAL_UI_VIEW_FINAL.json').read_text())
 if v['status']!='BLOCKED_SYSTEM_SAFETY'or any(v[k]for k in ['publication_allowed','dispatch_authorized','count_as_safety_pass'])or v['unit_removals']:raise ValueError('不支持的安全工作流状态')
 return v
def render():
 import streamlit as st
 with st.expander('调频失败工作流：当前原案例只读',expanded=False):
  try:v=read_view()
  except Exception as e:st.error('安全仍未放行：'+str(e));return
  st.error('BLOCKED_SYSTEM_SAFETY：系统级安全阻断，不能宣称已完成通用安全重新出清。')
  st.write({'原奖项MW':v['original_awards_mw'],'待选方案':v['pending_candidate'],'逐机移除':v['unit_removals']})
  st.caption('原继承合成50MW，不是新H12正式调频出清。当前没有操作外部JSON、发布或自动调用资源的入口。')
  st.write('缺失资料：');st.json(v['missing_inputs'])
  st.write('下一步：');st.json(v['next_actions'])
  st.caption('§59触发和认证候补清单尚不明，保持blocked；奖项、400MW与资格不变，任何研究检查声明也不等于物理认证。')
