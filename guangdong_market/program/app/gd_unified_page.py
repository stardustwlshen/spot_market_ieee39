"""Candidate UI, not installed in the user's main repository."""
from pathlib import Path
import streamlit as st
import gd_unified_backend as api

def render(parent_run="",standalone=False,simple=False):
 if standalone:st.set_page_config(page_title='统一短域研究入口（候选）',layout='wide')
 st.title('省内研究运行 · 当前工作候选')
 st.info('修正循环尾约束、SOC极小边界规范与SCED数值策略的研究入口，非最终合规版本。历史全天证据独立于此版本的新运行，不得改写来源。')
 from gd_agc_safety_panel import render as render_agc_safety
 render_agc_safety()
 if not simple:
  from gd_agc_failure_panel import render as render_agc_failure
  render_agc_failure()
  from gd_unified_adopted import render as render_adopted
  render_adopted()
 st.warning('候选版，不是广东正式运行系统。保守日前保护 conservative_DA_protection_research 会保留本场景热机在线；启停需独立研究许可，初始active事件导入仍不支持。近/远各24个5分钟联合优化是研究解释。原双400 MW与资格不变。')
 st.caption('显式假设原48小时日前计划及次日储能目标已发布；不是实际申报。无偏差回执不是计量/调频/交流安全放行；求解参数见实际计划与回执。')
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
 from gd_live_panel import render as render_live
 render_live(da,run)
 if simple:return
 from gd_batch_panel import render as render_batch
 render_batch(da,run)
 from gd_hourly_agc_panel import render as render_hourly_agc
 render_hourly_agc(run)
 w=int(st.number_input('实时窗口',min_value=0,max_value=95,value=0,step=1,key='u_window'))
 identity=(da,base,name,published,run,w)
 if st.session_state.get('u_identity')!=identity:
  for k in ['u_proof','u_rt_result','u_price_result']:st.session_state.pop(k,None)
  for k in ['u_assume','u_native','u_simulate']:st.session_state[k]=False
  st.session_state['u_identity']=identity
 consent=st.checkbox('我明确采用上述保守保护、合成次日声明和首15分钟已下发研究初态',key='u_assume')
 if st.button('1 准备新研究（0求解）',disabled=not consent,key='u_prepare'):
  st.session_state.pop('u_proof',None)
  try:st.session_state['u_proof']=api.prepare(da,base,name,published,confirmed=True);st.success('已准备窗口0，尚未求解')
  except Exception as e:st.error(str(e))
 if st.button('2 构建/预检当前窗口（0求解）',key='u_check'):
  st.session_state.pop('u_proof',None)
  try:st.session_state['u_proof']=api.build(da,run,w);st.json(st.session_state['u_proof']['core'])
  except Exception as e:st.error(str(e))
 native=st.checkbox('明确启动此窗SCUC及固定新整数SCED，预算以本次计划为准；不是调度指令',key='u_native')
 if st.button('3 启动当前窗口一次',disabled=not native or 'u_proof'not in st.session_state,key='u_launch'):
  try:st.session_state['u_rt_result']=api.launch('rt',da,run,w,confirmed=True,proof=st.session_state['u_proof']);st.session_state.pop('u_proof',None)
  except Exception as e:st.error(str(e))
 if st.button('刷新并严格读取RT（0求解）',key='u_rt_read'):
  try:st.session_state['u_proof']=api.rt_proof(da,run,w);st.session_state['u_rt_result']=st.session_state['u_proof']['core']
  except Exception as e:st.session_state.pop('u_proof',None);st.session_state.pop('u_rt_result',None);st.error(str(e))
 if 'u_rt_result'in st.session_state:st.json(st.session_state['u_rt_result'])
 sim=st.checkbox('明确按无偏差跟踪生成研究交接，不代表实测或安全认证',key='u_simulate')
 if st.button('4 严格接受后交接',disabled=not sim,key='u_handoff'):
  try:st.json(api.handoff(da,run,w,confirmed=True))
  except Exception as e:st.error(str(e))
 with st.expander('5 独立定价与结果（目录自动派生）',expanded=True):
  pw=int(st.number_input('价格窗口：−1为所选日前同源首季度seed',min_value=-1,max_value=94,value=0,key='u_price_window'))
  domain=st.selectbox('显式参考域',['thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current','legacy_all_current_comparison','legacy_DA_storage_comparison'],key='u_domain')
  st.warning('推荐研究profile：thermal DA区间+DA上一完整15分钟首锚；首锚解释未经官方认证，storage仍current、δ仍研究。thermalDA/current锚比较在q27有已知不可行冲突；旧current仅比较，绝不失败回退。')
  priceidentity=(da,run,pw,domain)
  if st.session_state.get('u_price_identity')!=priceidentity:st.session_state.pop('u_price_result',None);st.session_state['u_price_parameters']=False;st.session_state['u_price_native']=False;st.session_state['u_price_identity']=priceidentity
  pc=st.checkbox('采用命名研究参数：δ=.1、VRE价−50、负荷/事故/网络/弃电罚1000/200/1000/1000及原case声明；网络1000来自区域默认表，其他适用性仍待核查',key='u_price_parameters')
  if st.button('构建独立PRICE（0求解）',disabled=not pc,key='u_price_prepare'):
   try:st.json(api.prepare_price(da,run,pw,domain,confirmed=True))
   except Exception as e:st.error(str(e))
  pn=st.checkbox('明确启动此季度一次HiGHS PRICE，预算见本次计划',key='u_price_native')
  if st.button('启动PRICE一次',disabled=not pn,key='u_price_launch'):
   try:api.ensure_price_policy(run,pw,domain);st.json(api.launch('price',da,run,pw,confirmed=True))
   except Exception as e:st.error(str(e))
  if st.button('PRICE刷新/严格接受/读取',key='u_price_read'):
   st.session_state.pop('u_price_result',None)
   try:api.bound(da,run);api.ensure_price_policy(run,pw,domain);st.session_state['u_price_result']=api.accept_price(run,pw)
   except Exception as e:st.error(str(e))
  if 'u_price_result'in st.session_state:
   z=st.session_state['u_price_result']
   if 'raw_prices'in z:
    st.caption('同源独立DC研究价，未交流安全发布。');st.dataframe([{'node':k,'CNY/MWh':v}for k,v in z['raw_prices'].items()],hide_index=True)
   else:st.json(z)
 with st.expander('6 完整小时核算（必须12格执行+4季度）'):
  h=int(st.number_input('小时',min_value=0,max_value=23,value=0,key='u_hour'))
  st.caption('首小时自动取seed、W0、W1、W2；W3交接后才有12格执行。缺项拒绝，价格不补零；合成参与/空合同，不是正式账单或利润。')
  if st.button('核算/重验小时（0求解）',key='u_hour_read'):
   try:st.json(api.hour(da,run,h))
   except Exception as e:st.error(str(e))
 st.caption('默认0求解。重复已完成步骤只读取；已有失败/未知求解记录不能自动重试。页面关闭不代表计算停止，不提供强杀/安全取消。')
if __name__=='__main__':render()
