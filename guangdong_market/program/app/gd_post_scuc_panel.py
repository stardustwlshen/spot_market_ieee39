"""Scope-only UI until explicit planning capability is installed; no native entry."""
def render(run):
 import streamlit as st
 with st.expander('SCUC后调频阶段：受限接口范围预检（不启动）',expanded=False):
  st.warning('当前已安装后端只支持未发布目标小时个体award为明确零baseline的研究路径；不是默认50链已全通。五机饱和规划已另以主卡独立选项接入，仅4窗；本旧zero-only预检不等于新模式启动。不能擅自把seed改0。')
  st.checkbox('本zero-only诊断面板不启动，主卡新规划模式另行选择',value=False,disabled=True,key='post_scuc_unavailable')
  declarations=st.text_area('范围预检声明文件路径（每行一个；不构成来源或资格认证）','',key='post_scope_declarations')
  if st.button('仅预检当前run的受限范围（0求解）',key='post_scope_preflight'):
   try:
    import gd_live_control as ctl
    ctl.sources()
    from gd_post_scuc_runtime.controller import preflight
    result=preflight(run,[p.strip()for p in declarations.splitlines()if p.strip()]);st.json(result);st.info('仅scope条件满足，不代表声明资格/数学/安全已验，也未连接新运行启动。')
   except Exception as exc:st.error('当前不可选：'+str(exc))
