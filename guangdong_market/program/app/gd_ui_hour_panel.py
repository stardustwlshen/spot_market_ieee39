import streamlit as st
from pathlib import Path
from decimal import Decimal
import gd_ui_recommit_prepare as api

def load_hour(path):
 z=api.read(path)
 if z.get('schema')!='gd.first_hour.counterfactual.Decimal.v2' or z.get('executed_cells')!=12 or z.get('quarter_prices')!=4:raise ValueError('不是完整首小时合同')
 if z.get('formal_bill') is not None or z.get('payable') is not False or z.get('safety_release') is not False:raise ValueError('研究状态不符，拒绝显示为正式账单')
 for p,h in z['source_pins'].items():
  if api.sha(p)!=h:raise ValueError('结果来源变化：'+p)
 quarters=z['input_binding']['quarter_sources']
 if [q['day_quarter'] for q in quarters]!=[0,1,2,3]:raise ValueError('四季度顺序不全')
 for q in quarters:
  if z['source_pins'].get(q['path'])!=q['sha256']:raise ValueError('季度绑定与来源清单不符')
 if sum((Decimal(r['energy_amount_CNY']) for r in z['rows']),Decimal(0))!=Decimal(z['total_energy_component_CNY']):raise ValueError('金额汇总不符')
 return z

def render(parent_run='',engine=None):
 st.subheader('逐窗实时重新组合：独立研究入口')
 st.warning('与上方日前固定组合的一键链不同。首15分钟从所选日前结果假设已下发；非表计或正式调度。原400 MW备用不变，AGC/AC尚未放行。')
 st.caption('当前为48小时研究边界及统一事件启停费用，并非完整冷/温/热三态；首小时热机没有新启停，储能模式变化尚在待执行段。')
 engine=engine or Path(__file__).resolve().parents[1]/'engines/guangdong_rt_recommit_v1/program.py'
 cli=st.text_input('实时组合程序',str(engine),key='rth_cli');parent=st.text_input('来源日前运行',parent_run,key='rth_parent');out=st.text_input('全新实时准备目录',key='rth_output')
 confirmed=st.checkbox('明确采用独立反事实研究初态，允许纯构模（约200 MB），不求解',key='rth_confirm')
 if st.button('实时组合预检（0 求解）'):
  try:st.session_state['rth_preflight']=api.preflight(cli,parent,out);st.success('输入预检通过；完整场景兼容性由实际 seed/build 继续核验')
  except Exception as e:st.session_state.pop('rth_preflight',None);st.error(str(e))
 if st.button('准备首窗模型（0 求解）',disabled=not confirmed or 'rth_preflight' not in st.session_state):
  try:
   p=st.session_state['rth_preflight']
   if p!=api.preflight(cli,parent,out):raise ValueError('界面输入变化，请重新预检')
   st.session_state['rth_result']=api.prepare(p,confirmed=True);st.session_state.pop('rth_preflight',None)
  except Exception as e:st.session_state.pop('rth_preflight',None);st.error(str(e))
 if 'rth_result' in st.session_state:
  r=st.session_state['rth_result'];st.success(r['status']);st.caption('下列命令尚未执行。后台监督与安全取消未接通；未知结果先检查，不自动重试。');st.code(r['command_not_executed'],language='bash')
 existing=st.text_input('已有实时运行目录（只读）',key='rth_existing')
 if st.button('检查已有实时运行（不重算）'):
  try:st.dataframe(api.inspect_run(existing)['windows'],hide_index=True)
  except Exception as e:st.error(str(e))
 st.subheader('完整首小时模拟结果')
 path=st.text_input('首小时结果 JSON',key='rth_hour')
 if st.button('读取首小时价格与模拟核算'):
  try:
   z=load_hour(path);st.metric('已模拟执行 / 已下发', '12 / 15 个5分钟格');st.caption('前3格是日前seed，后9格来自新实时组合；另3格待执行。四季度为seed、W0、W1、W2；W3价格属于下一小时。')
   st.warning('研究能量收入分项，不是利润或付款账单；合成参与、空合同假设，AC未安全。')
   st.metric('模型列示账户电能量分项净合计（研究）（元）',z['total_energy_component_CNY'])
   prices=[{'节点':n,'日前小时价（元/MWh）':v['value'],'实时小时价（元/MWh）':z['hour_prices']['RT'][n]['value']} for n,v in z['hour_prices']['DA'].items()]
   st.dataframe(prices,hide_index=True)
   with st.expander('四季度独立实时价格（不含下一小时W3）'):
    quarter_rows=[]
    for source in z['input_binding']['quarter_sources']:
     record=api.read(source['path'])
     for node,value in record['raw_prices'].items():quarter_rows.append({'季度':source['day_quarter'],'节点':node,'原始研究价（元/MWh）':value})
    st.dataframe(quarter_rows,hide_index=True)
   rows=[{'资源':r['resource'],'方向':r['direction'],'日前电量（MWh）':r['QDA_MWh'],'模拟执行电量（MWh）':r['simulated_executed_MWh'],'电能量分项金额（研究，元）':r['energy_amount_CNY']} for r in z['rows']];st.dataframe(rows,hide_index=True)
   with st.expander('研究假设与核验边界'):st.json(z['assumptions']);st.caption('此处校验来源hash与金额汇总，不替代模型全量KKT复核。其余23小时未填充。')
  except Exception as e:st.error(str(e))
