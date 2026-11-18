"""Explicit node aggregate renewable bids with unsaved-change protection."""
def render_renewable_bid_editor(source,source_token):
 import streamlit as st
 from gd_provincial_inputs import renewable_parameters
 from gd_optional_drafts import capture,discard,dirty,stage
 parameters=renewable_parameters(source)
 state='renewable_bids_'+source_token
 drafts='renewable_drafts_'+source_token
 if state not in st.session_state:st.session_state[state]={}
 st.session_state['optional_dirty_renewable']=[]
 with st.expander('风光电能量报价'):
  st.caption('同节点风光共用全天单价。修改后保存，重新计算生效。')
  if not parameters:st.caption('当前场景没有可报价的新能源节点。');return {}
  bus=st.selectbox('新能源所在节点',list(parameters),key='renew_bus_'+source_token)
  base=parameters[bus]
  if not base['constant']:st.caption('原场景为分时报价；保存后改为全天统一报价。')
  accepted={'bid':st.session_state[state].get(bus,base['bid'])}
  current=st.session_state.get(drafts,{}).get(bus,accepted)
  fields={'bid':'renew_price_'+source_token+'_'+bus}
  st.number_input('电能量报价（元/MWh）',min_value=-50.,value=float(current['bid']),step=1.,key=fields['bid'],on_change=capture,args=(drafts,bus,fields))
  if st.button('保存新能源报价',key='save_renewable_'+source_token+'_'+bus):
   import math
   bid=stage(drafts,bus,fields)['bid']
   if not math.isfinite(bid):st.error('报价必须为有限数值，未保存。')
   else:
    st.session_state[state][bus]=float(bid)
    st.session_state.setdefault(drafts,{}).pop(bus,None)
    st.success('已保存，重新计算后生效。')
  if dirty(st.session_state.get(drafts,{}).get(bus),accepted):
   st.button('撤销本节点的未保存报价',key='discard_renewable_'+source_token+'_'+bus,on_click=discard,args=(drafts,bus,fields,accepted))
  if st.session_state[state]:st.dataframe([{'节点':b,'全天报价（元/MWh）':v} for b,v in st.session_state[state].items()],hide_index=True)
 for bus,draft in st.session_state.get(drafts,{}).items():
  if bus in parameters and dirty(draft,{'bid':st.session_state[state].get(bus,parameters[bus]['bid'])}):
   st.session_state['optional_dirty_renewable'].append('新能源节点 '+bus)
 return dict(st.session_state[state])
