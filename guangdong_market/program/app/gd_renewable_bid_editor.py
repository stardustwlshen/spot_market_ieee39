"""Compact, explicit node aggregate renewable bid editor."""
def render_renewable_bid_editor(source,source_token):
 import streamlit as st
 from gd_provincial_inputs import renewable_parameters
 parameters=renewable_parameters(source)
 state='renewable_bids_'+source_token
 if state not in st.session_state:st.session_state[state]={}
 with st.expander('风光电能量报价'):
  st.caption('按节点风光合计报价，同节点风电和光伏共用一个全天单价；暂不支持逐电站或分段报价。未保存的节点沿用场景原报价。')
  if not parameters:st.info('当前场景没有可报价的新能源节点。');return {}
  bus=st.selectbox('新能源所在节点',list(parameters),key='renew_bus_'+source_token)
  base=parameters[bus]
  if not base['constant']:st.info('原场景为分时报价；保存后该节点改为全天统一报价。')
  with st.form('renew_bid_'+source_token+'_'+bus):
   bid=st.number_input('电能量报价（元/MWh）',min_value=-50.,value=float(st.session_state[state].get(bus,base['bid'])),step=1.,key='renew_price_'+source_token+'_'+bus)
   if st.form_submit_button('保存新能源报价'):
    import math
    if not math.isfinite(bid):st.error('报价必须为有限数值，未保存。')
    else:st.session_state[state][bus]=float(bid);st.success('已保存，重新计算日前和实时后生效。')
  if st.session_state[state]:st.dataframe([{'节点':b,'全天报价（元/MWh）':v} for b,v in st.session_state[state].items()],hide_index=True)
 return dict(st.session_state[state])
