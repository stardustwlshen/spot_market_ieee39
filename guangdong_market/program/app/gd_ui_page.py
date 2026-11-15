"""Install as an extra page, with this directory on import path. No legacy scenario editor."""
import streamlit as st
import gd_ui_backend as api
from gd_ui_scenarios import render_editor
def render_charts(data):
 import pandas as pd
 import plotly.express as px
 tabs=st.tabs(['实际模拟执行出力','储能 SOC','风光可用/发电/弃电','独立15分钟研究价','小时模拟核算'])
 for tab,key,title,y,x in [(tabs[0],'executed_5min','热机实际模拟执行（5min均值）','P_mw','start'),(tabs[1],'storage_5min','储能时段末SOC（MWh）','SOC_MWh','end')]:
  with tab:
   rows=data.get(key,[])
   if rows:
    if any(not row.get(x) for row in rows):st.warning('缺少时段时间字段，暂不绘制该曲线，不能将期末状态当作期初状态。')
    else:st.plotly_chart(px.line(pd.DataFrame(rows).sort_values([x,'resource']),x=x,y=y,color='resource',title=title),width='stretch')
   else:st.info('暂无经执行回执确认的数据；已下发未执行不计入。')
 with tabs[2]:
  rows=data.get('VRE_5min',[])
  if rows:
   f=pd.DataFrame(rows).groupby('start',as_index=False)[['availability_mw','dispatched_mw','curtailed_mw']].sum(min_count=1)
   st.plotly_chart(px.line(f,x='start',y=['availability_mw','dispatched_mw','curtailed_mw'],title='VRE 实际模拟执行三量（MW）'),width='stretch')
  else:st.info('暂无已执行VRE数据。')
 with tabs[3]:
  rows=data.get('independent_PRICE_15min',[])
  if rows:
   f=pd.DataFrame(rows);p=f.pivot(index='node',columns='window',values='raw_research_LMP').reindex(columns=range(96))
   st.plotly_chart(px.imshow(p,aspect='auto',labels=dict(x='15分钟窗口',y='节点',color='原始研究价 元/MWh')),width='stretch')
   st.caption('空白是缺价，非零；原始对偶研究价不是正式结算价。')
   clocks={r['window']:r['information_time'] for r in rows if r.get('information_time',{}).get('status')=='retrospective_research'}
   for quarter,clock in sorted(clocks.items()):st.warning('季度 '+str(quarter)+'：'+clock['label']+'；不得用于正式结算或预出清。')
  else:st.info('暂无独立PRICE；不取RT dual替代。')
 with tabs[4]:
  rows=data.get('settlement_hour_rows',[])
  if rows:
   f=pd.DataFrame(rows);plot=f.copy();plot['研究金额元']=pd.to_numeric(plot['energy_amount_CNY']);plot['账户']=plot['resource']+':'+plot['direction']
   st.plotly_chart(px.line(plot,x='start',y='研究金额元',color='账户'),width='stretch')
   st.dataframe(f,width='stretch')
   from gd_economics_panel import render as render_economics
   render_economics(data)
  else:st.info('完整执行和价格/明确合成合同条件未齐，不展示伪造账单。')
def render_developer(navigation=None):
 st.set_page_config(page_title='广东研究市场',layout='wide')
 if navigation is not None:navigation()
 st.title('广东省内研究电能量链')
 st.warning('研究仿真，非正式市场/账单。上方4/96窗仍是旧日前固定组合，可提供所选日前来源；下方当前入口已修正日末SOC与剩余循环吞吐必要约束。首小时原12次求解仅经新约束兼容后沿用，不是v3新native或新全天完成。保守日前在线保护、近/远各24个五分钟联合优化均为明确研究解释。')
 st.caption('有功/DC线路约束市场仿真；未评估交流/无功。旧交流诊断仅历史元数据，不用于新范围放行。')
 st.caption('采用独立研究输入模板，400 MW 正备用及 400 MW 事故备用不变；求解参数以本次求解计划和回执为准。交易时区为 +08:00，后台日志 UTC；暂不支持安全取消，不强杀求解器。新场景/报价可在下方另存后运行；旧版 CSV 不兼容。')
 core=st.text_input('新引擎 market.py',str(api.DEFAULT_CORE));run=st.text_input('独立 run 目录',str(api.DEFAULT_RUNS/'new_research_run'))
 action=st.radio('操作',['run','resume'],horizontal=True);windows=st.selectbox('窗口数',[4,96]);settle=st.checkbox('明确采用合成参与和空合同假设做小时模拟核算（仅96窗）',disabled=windows!=96)
 scenario=render_editor(core,disabled=action=='resume')
 from gd_da_only_panel import render as render_da_only
 render_da_only(scenario)
 st.info('旧固定日前组合仅提供历史结果，不再启动或恢复旧求解。')
 st.subheader('只读状态与结果')
 render_saved_results(core,run)

 with st.expander('修正循环尾约束的研究开发入口（统一实时/定价/核算）',expanded=True):
  from gd_unified_page import render as render_unified
  render_unified(parent_run="")

 with st.expander('旧短域入口说明（只读历史）'):
  st.warning('旧短域未包含新增的日末SOC/剩余循环吞吐必要可达约束；不再从主页启动旧RT/PRICE流程。旧源码和原始结果保留，不能将旧上下文改签为当前v3。原首小时兼容沿用报告在新入口单独展示。')

 with st.expander('旧短域已执行出力、SOC与价格图表（只读历史）'):
  from gd_ui_short_results_panel import render as render_short_results
  render_short_results(parent_run=run)

 with st.expander('历史48小时/三态研究分支（只读说明）'):
  st.caption('原源码、首窗和失败证据均保留；本主页不再提供旧域RT、三态或PRICE2.2的启动按钮，也不把旧域结果混成修正短域结果。下方保留安全与定价反馈的只读检查。')

 with st.expander('历史首小时报告（只读，不启动旧模型）'):
  old_hour=st.text_input('历史首小时报告JSON',key='historical_hour_json')
  if st.button('读取旧小时报告（0求解）',key='historical_hour_read'):
   try:
    from gd_ui_hour_panel import load_hour
    old_report=load_hour(old_hour);st.json(old_report);st.caption('历史报告来源hash/汇总检查，不是当前模型的新数学验收或正式账单。')
   except Exception as e:st.error(str(e))

 with st.expander('准备新调频研究候选（真实 CLI，0 求解）'):
  from gd_ui_agc_prepare_panel import render as render_agc_prepare
  render_agc_prepare(parent_run=run)

 with st.expander('历史实时 / 调频失败与安全反馈证据（只读）'):
  from gd_ui_rt_agc_panel import render as render_rt_agc
  render_rt_agc()

 from gd_ui_price_feedback_panel import render as render_price_feedback
 render_price_feedback()

 from gd_ui_recovery import render as render_recovery
 render_recovery(core,run)
 with st.expander('已有广东 run 目录（只读索引，不自动选择或启动）'):
  rows=api.list_run_directories(api.DEFAULT_RUNS)
  if rows:st.dataframe(rows,width='stretch')
  else:st.caption('尚未发现 RUN.json；可在上方手动填写已知 run 路径只读查看。')



def render_saved_results(core,run,acp='',acr=''):
 identity=(str(core),str(run),acp,acr)
 if st.session_state.get('gd_results_identity')!=identity:
  st.session_state.pop('gd_results_snapshot',None)
  st.session_state['gd_results_identity']=identity
 if st.button('刷新结果',key='gd_refresh_results'):
  st.session_state.pop('gd_results_snapshot',None)
  try:
   status=api.status(core,run,acp,acr)
   data=api.result_tables(run)
   st.session_state['gd_results_snapshot']={'status':status,'data':data}
  except Exception as e:st.error(str(e))
 snapshot=st.session_state.get('gd_results_snapshot')
 if snapshot:
  scope=snapshot['status'].get('model_scope',snapshot['data'].get('model_scope'))
  if scope=='active_power_dc':st.caption('有功/DC线路约束市场仿真；未评估交流/无功。')
  else:st.caption('历史/未声明新范围的结果：保留原运行身份。旧AC状态仅历史元数据，不用于新DC范围放行。')
  if snapshot['data'].get('schema')=='gd.live.display_view.v1':
   from gd_resume_progress import period_end
   receipt=snapshot['data']['execution_receipt'];progress=snapshot['data'].get('progress_record',{})
   st.write('图表已显示 '+str(receipt.get('complete_hours',0))+' 个完整小时；不是当前求解总进度。')
   st.caption('读取时进度记录：已计算至 '+period_end(progress.get('computed_window',progress.get('computed')))+'；已校验至 '+period_end(progress.get('validated_window',progress.get('validated')))+'；可靠外存记录至 '+period_end(progress.get('external_saved'))+'。未满一小时的执行结果暂不在图表中；这不是新的恢复验证。')
  if snapshot['data'].get('display_errors'):
   st.warning('部分来源核验失败，相关时段未显示，不补零。')
   st.dataframe(snapshot['data']['display_errors'],hide_index=True)
  render_charts(snapshot['data'])
  with st.expander('查看运行状态与校验明细'):
   st.json(snapshot['status'])
   st.json(snapshot['data'].get('execution_receipt',{}))

def render(navigation=None):
 mode=st.sidebar.radio('界面',['市场工作台','开发与历史记录'],key='gd_view_mode')
 if mode=='开发与历史记录':
  render_developer(navigation)
  return
 st.set_page_config(page_title='广东市场模拟',layout='wide')
 st.title('广东市场模拟')
 st.warning('有功/DC线路约束市场仿真；未评估交流/无功。非实际调度方案，当前金额不代表完整利润。')
 from pathlib import Path
 core=str(api.DEFAULT_CORE)
 with st.expander('运行位置与已有结果'):
  core=st.text_input('引擎位置',core,key='simple_core')
  run=st.text_input('结果目录',str(api.DEFAULT_RUNS/'new_research_run'),key='simple_run')
 tabs=st.tabs(['场景与报价','计算与进度','结果'])
 with tabs[0]:
  scenario=render_editor(core)
  from gd_da_only_panel import render as render_da
  render_da(scenario)
 with tabs[1]:
  from gd_unified_page import render as render_unified
  selected=st.session_state.get('gd_selected_da')
  if selected:
   from gd_da_binding import verify_selection
   try:
    accepted=verify_selection(selected)
    render_unified(parent_run=accepted,simple=True)

   except Exception as exc:
    st.session_state.pop('gd_selected_da',None);st.error('日前来源验证失败：'+str(exc))
  else:st.info('先完成日前计算，并点击“选此已接受DA”；结果目录不会自动当作日前来源。')
  from gd_single_window_prepare import render as render_single_prepare
  render_single_prepare(selected)
 with tabs[2]:render_saved_results(core,run)

if __name__=='__main__':render()
