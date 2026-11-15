"""Candidate UI, not installed in the user's main repository."""
from pathlib import Path
import streamlit as st
import gd_unified_backend as api

def render(parent_run="",standalone=False,simple=False):
 from gd_provincial_page import render as provincial_render
 return provincial_render(parent_run=parent_run,standalone=standalone)
 if standalone:st.set_page_config(page_title='统一短域研究入口（候选）',layout='wide')
 st.title('省内研究运行 · 当前工作候选')
 st.info('修正循环尾约束、SOC极小边界规范与SCED数值策略的研究入口，非最终合规版本。历史全天证据独立于此版本的新运行，不得改写来源。')
 st.caption('有功/DC线路约束市场仿真；未评估交流/无功。能量、储能、备用与AGC门禁保持。')
 if not simple:
  from gd_agc_failure_panel import render as render_agc_failure
  render_agc_failure()
  from gd_unified_adopted import render as render_adopted
  render_adopted()
 st.warning('候选版，不是广东正式运行系统。保守日前保护 conservative_DA_protection_research 会保留本场景热机在线；启停需独立研究许可，初始active事件导入仍不支持。近/远各24个5分钟联合优化是研究解释。原双400 MW与资格不变。')
 st.caption('显式假设原48小时日前计划及次日储能目标已发布；不是实际申报。无偏差回执不是实际计量或调频指令；求解参数见实际计划与回执。')
 with st.expander('选择已有日前运行 / 存储位置'):
  da_root=st.text_input('已有日前运行集合（仅列表）','',key='u_da_root')
  choices=['手动填写']
  if da_root.strip():
   p=Path(da_root).expanduser()
   if p.is_dir():choices += [str(f.parent.resolve())for f in sorted(p.glob('*/ARTIFACTS.json'))][:200]
  choice=st.selectbox('选择已有运行',choices,key='u_da_choice')
  base=st.text_input('新研究输出位置',str(Path(__file__).resolve().parents[1]/'runs'),key='u_base')
 linked=st.checkbox('使用上方日前运行目录',value=True,key='u_link_parent')if parent_run else False
 if linked:st.session_state['u_da']=parent_run
 if not linked and choice!='手动填写'and st.session_state.get('u_last_choice')!=choice:st.session_state['u_da']=choice
 st.session_state['u_last_choice']=choice
 da=st.text_input('已接受日前来源',choice if choice!='手动填写'else'',key='u_da',disabled=linked)
 if not simple:
  from gd_thermal_day_panel import render as render_thermal_day
  render_thermal_day(da)
  from gd_partial_day_panel import render as render_partial_day
  render_partial_day(da)
  from gd_corrected_day_panel import render as render_corrected_day
  import json
  registry=Path(__file__).with_name('CORRECTED_DAY.json')
  reg=json.loads(registry.read_text())if registry.is_file()else{}
  with st.expander('旧current参考研究日：仅版本比较',expanded=False):
   if reg:st.caption('本历史结果的原始日前来源（可在上方选择，不会自动替换编辑场景）：'+reg['original_selected_DA'])
   render_corrected_day(selected_da=da,registered_bundle=(Path(__file__).parent/reg['bundle']).resolve()if reg else None,expected_manifest_sha256=reg.get('manifest_sha256'),historical_identity=reg.get('DA_identity'))
 name=st.text_input('研究运行名','新短域研究',key='u_name')
 published=st.text_input('明确假设的发布时间/可用时刻（+08:00）','2026-11-10T20:30:00+08:00',key='u_published')
 use=st.text_input('已有实时运行目录（可留空，自动使用运行名）','',key='u_existing')
 try:run=str(Path(use).expanduser().resolve())if use.strip()else str(api.layout(base,name)['run'])
 except ValueError as e:run='';st.error(str(e))
 st.caption('当前实时目录：'+run)
 consent=st.checkbox('明确采用合成次日声明和首15分钟已下发研究初态；仅准备新任务，非旧运行恢复',key='dc_base_assumption')
 if st.button('准备当前DC新任务（0求解）',disabled=not consent or bool(use.strip()),key='dc_base_prepare'):
  try:
   prepared=api.prepare(da,base,name,published,confirmed=True);st.success('新初态已准备；可在下方查看预算并启动连续滚动。');st.json(prepared)
  except Exception as exc:st.error(str(exc))
 from gd_live_panel import render as render_live
 render_live(da,run)
 if simple:return
 st.caption('旧批次入口已停用，保留历史证据；新任务统一使用上方DC连续滚动。')
 from gd_hourly_agc_panel import render as render_hourly_agc
 render_hourly_agc(run)
 st.caption('历史逐窗启动/反事实交接已停用。旧文件仍可在主结果页读取；新计算统一使用上方DC连续滚动入口。')
if __name__=='__main__':render()
