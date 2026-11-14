import streamlit as st
import pandas as pd
import plotly.express as px
from gd_ui_short_results_backend import read_results

def display(z):
 st.success(f"只读验真：{z['executed_cells']} 格模拟已执行至 {z['execution_end']}；另有 {z['issued_cells']-z['executed_cells']} 格下发未执行至 {z['issued_end']}。")
 st.caption('无偏差反事实跟踪，不是实测。只显示回执覆盖段，不把其余4小时预测当已执行。SOC为每个五分钟段末值；金额未自动生成。')
 tabs=st.tabs(['模拟已执行出力','模拟已执行SOC','模拟已执行风光','下发未执行（不核算）','独立季度研究价'])
 executed=lambda rows:[r for r in rows if r['state']=='模拟已执行']
 with tabs[0]:
  rows=[dict(r,power_MW=r['mean_MW'])for r in executed(z['thermal'])]+[dict(r,power_MW=r['net_MW'])for r in executed(z['battery'])]
  st.plotly_chart(px.line(pd.DataFrame(rows),x='start',y='power_MW',color='resource',markers=True,title='热机均值 / 储能净出力（放电为正）'),width='stretch')
  st.dataframe(rows,hide_index=True)
 with tabs[1]:st.plotly_chart(px.line(pd.DataFrame(executed(z['battery'])),x='end',y='end_SOC_MWh',color='resource',markers=True),width='stretch');st.dataframe(executed(z['battery']),hide_index=True)
 with tabs[2]:
  f=pd.DataFrame(executed(z['VRE'])).groupby('start',as_index=False)[['available_MW','dispatched_MW','curtailed_MW']].sum();st.plotly_chart(px.line(f,x='start',y=['available_MW','dispatched_MW','curtailed_MW'],markers=True),width='stretch');st.dataframe(f,hide_index=True)
 with tabs[3]:
  for label,key in [('热机','thermal'),('储能预测SOC和出力','battery'),('风光','VRE')]:st.caption(label);st.dataframe([r for r in z[key]if r['state']=='下发未执行'],hide_index=True)
 with tabs[4]:
  if z['price']:
   p=z['price'];st.caption(f"{p['context']['start']} 至 {p['context']['end']}；零网损DC研究价，尚未交流安全发布。")
   f=pd.DataFrame([{'node':k,'CNY_per_MWh':v}for k,v in p['raw_prices'].items()]);st.plotly_chart(px.bar(f,x='node',y='CNY_per_MWh'),width='stretch');st.dataframe(f,hide_index=True)
  else:st.info('未提供同窗独立价格；不使用调度SCED对偶补价。')
 with st.expander('段能量平衡与CSV导出'):
  st.dataframe(z['balance'],hide_index=True)
  for key in ('thermal','battery','VRE','balance'):st.download_button('下载 '+key+' CSV',pd.DataFrame(z[key]).to_csv(index=False).encode('utf-8-sig'),'short_'+key+'.csv','text/csv',key='sview_csv_'+key)

def render(parent_run=''):
 st.warning('仅已安装CLI1同源结果；CLI2–5全天分支和旧48小时链不混用。验真失败时清空旧图，不把上次结果冒充本次。')
 da=st.text_input('图表来源日前',parent_run,key='sview_da');run=st.text_input('图表短域运行',key='sview_run');w=int(st.number_input('图表末窗',min_value=0,max_value=94,value=0,key='sview_window'));price=st.text_input('同窗独立PRICE接受JSON（可留空）',key='sview_price')
 identity=(da,run,w,price)
 if st.session_state.get('sview_identity')!=identity:st.session_state.pop('sview_result',None)
 if st.button('验真并刷新短域图表（0求解）',key='sview_read'):
  st.session_state.pop('sview_result',None)
  try:st.session_state['sview_result']=read_results(*identity);st.session_state['sview_identity']=identity
  except Exception as e:st.error(str(e))
 if 'sview_result'in st.session_state:display(st.session_state['sview_result'])
